"""Narrow, read-only Kubernetes evidence retrieval for incident investigation."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from app.config import Settings

_K8S_NAME = r"^[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?$"


class KubernetesWorkloadParams(BaseModel):
    namespace: str = Field(pattern=_K8S_NAME)
    kind: Literal["deployment", "statefulset", "daemonset"]
    name: str = Field(pattern=_K8S_NAME)


class KubernetesPodsParams(BaseModel):
    namespace: str = Field(pattern=_K8S_NAME)
    service_name: str = Field(pattern=_K8S_NAME)
    limit: int = Field(default=50, ge=1, le=100)


class KubernetesEventsParams(BaseModel):
    namespace: str = Field(pattern=_K8S_NAME)
    object_name: str = Field(pattern=_K8S_NAME)
    limit: int = Field(default=50, ge=1, le=100)


class KubernetesConfigurationError(RuntimeError):
    pass


class KubernetesInspectionClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        host = settings.kubernetes_api_url.strip()
        if not host:
            service_host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
            service_port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
            host = f"https://{service_host}:{service_port}" if service_host else ""
        self._base_url = host.rstrip("/")
        self._token_file = Path(settings.kubernetes_token_file)
        self._namespace_file = Path(settings.kubernetes_namespace_file)
        self._configured_namespaces = {
            namespace.strip() for namespace in settings.kubernetes_allowed_namespaces.split(",") if namespace.strip()
        }
        verify: str | bool = settings.kubernetes_ca_file if Path(settings.kubernetes_ca_file).exists() else True
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.integration_timeout_seconds),
            verify=verify,
            follow_redirects=False,
        )
        self._owns_client = client is None

    def _allowed_namespaces(self) -> set[str]:
        allowed = set(self._configured_namespaces)
        if self._namespace_file.exists():
            namespace = self._namespace_file.read_text().strip()
            if namespace:
                allowed.add(namespace)
        return allowed

    def _authorize_namespace(self, namespace: str) -> None:
        allowed = self._allowed_namespaces()
        if not allowed:
            raise KubernetesConfigurationError(
                "No Kubernetes namespace is allowlisted; configure KUBERNETES_ALLOWED_NAMESPACES "
                "or mount the pod service-account namespace"
            )
        if namespace not in allowed:
            raise PermissionError(f"Kubernetes namespace {namespace!r} is not allowlisted")

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        if not self._base_url or not self._token_file.exists():
            raise KubernetesConfigurationError("Kubernetes in-cluster API credentials are not available")
        token = self._token_file.read_text().strip()
        if not token:
            raise KubernetesConfigurationError("Kubernetes service-account token is empty")
        response = await self._client.get(
            f"{self._base_url}{path}",
            params=params,
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Kubernetes API returned a non-object response")
        return payload

    async def get_workload(self, params: KubernetesWorkloadParams) -> dict[str, Any]:
        self._authorize_namespace(params.namespace)
        plural = {
            "deployment": "deployments",
            "statefulset": "statefulsets",
            "daemonset": "daemonsets",
        }[params.kind]
        payload = await self._get(f"/apis/apps/v1/namespaces/{quote(params.namespace)}/{plural}/{quote(params.name)}")
        metadata = payload.get("metadata") or {}
        spec = payload.get("spec") or {}
        status = payload.get("status") or {}
        return {
            "apiVersion": payload.get("apiVersion"),
            "kind": payload.get("kind"),
            "name": metadata.get("name"),
            "namespace": metadata.get("namespace"),
            "generation": metadata.get("generation"),
            "observedGeneration": status.get("observedGeneration"),
            "desiredReplicas": spec.get("replicas"),
            "replicas": status.get("replicas"),
            "readyReplicas": status.get("readyReplicas"),
            "availableReplicas": status.get("availableReplicas"),
            "updatedReplicas": status.get("updatedReplicas"),
            "unavailableReplicas": status.get("unavailableReplicas"),
            "conditions": [
                {
                    "type": condition.get("type"),
                    "status": condition.get("status"),
                    "reason": condition.get("reason"),
                    "message": str(condition.get("message") or "")[:500],
                    "lastTransitionTime": condition.get("lastTransitionTime"),
                }
                for condition in status.get("conditions") or []
            ],
        }

    async def list_pods(self, params: KubernetesPodsParams) -> list[dict[str, Any]]:
        self._authorize_namespace(params.namespace)
        items: dict[str, dict[str, Any]] = {}
        for label_key in ("app.kubernetes.io/name", "app"):
            payload = await self._get(
                f"/api/v1/namespaces/{quote(params.namespace)}/pods",
                params={"labelSelector": f"{label_key}={params.service_name}", "limit": str(params.limit)},
            )
            for item in payload.get("items") or []:
                metadata = item.get("metadata") or {}
                if metadata.get("name"):
                    items[str(metadata["name"])] = item
        return [_project_pod(item) for item in list(items.values())[: params.limit]]

    async def list_events(self, params: KubernetesEventsParams) -> list[dict[str, Any]]:
        self._authorize_namespace(params.namespace)
        payload = await self._get(
            f"/api/v1/namespaces/{quote(params.namespace)}/events",
            params={
                "fieldSelector": f"involvedObject.name={params.object_name}",
                "limit": str(params.limit),
            },
        )
        return [_project_event(item) for item in (payload.get("items") or [])[: params.limit]]

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _project_pod(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    status = item.get("status") or {}
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "creationTimestamp": metadata.get("creationTimestamp"),
        "phase": status.get("phase"),
        "reason": status.get("reason"),
        "message": str(status.get("message") or "")[:500],
        "startTime": status.get("startTime"),
        "podIPPresent": bool(status.get("podIP")),
        "containerStatuses": [
            {
                "name": container.get("name"),
                "ready": container.get("ready"),
                "restartCount": container.get("restartCount"),
                "started": container.get("started"),
                "state": _container_state(container.get("state") or {}),
                "lastState": _container_state(container.get("lastState") or {}),
            }
            for container in status.get("containerStatuses") or []
        ],
    }


def _container_state(state: dict[str, Any]) -> dict[str, Any]:
    for name in ("waiting", "running", "terminated"):
        value = state.get(name)
        if isinstance(value, dict):
            return {
                "type": name,
                "reason": value.get("reason"),
                "message": str(value.get("message") or "")[:500],
                "startedAt": value.get("startedAt"),
                "finishedAt": value.get("finishedAt"),
                "exitCode": value.get("exitCode"),
            }
    return {}


def _project_event(item: dict[str, Any]) -> dict[str, Any]:
    involved = item.get("involvedObject") or {}
    return {
        "name": (item.get("metadata") or {}).get("name"),
        "type": item.get("type"),
        "reason": item.get("reason"),
        "message": str(item.get("message") or "")[:500],
        "count": item.get("count"),
        "firstTimestamp": item.get("firstTimestamp"),
        "lastTimestamp": item.get("lastTimestamp"),
        "eventTime": item.get("eventTime"),
        "involvedObject": {
            "kind": involved.get("kind"),
            "name": involved.get("name"),
            "namespace": involved.get("namespace"),
        },
    }


def kubernetes_result(operation: str, data: Any) -> dict[str, Any]:
    return {
        "status": "success",
        "source": "in-cluster-kubernetes-api",
        "readOnly": True,
        "operation": operation,
        "retrievedAt": datetime.now(UTC).isoformat(),
        "data": data,
    }


def classify_kubernetes_error(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, KubernetesConfigurationError):
        return ("not_configured", str(exc))
    if isinstance(exc, PermissionError):
        return ("namespace_denied", str(exc))
    if isinstance(exc, httpx.TimeoutException):
        return ("timeout", "Kubernetes API did not respond before the configured timeout")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return ("permission_denied", f"Kubernetes API denied the read with HTTP {status}")
        if status == 404:
            return ("not_found", "The requested Kubernetes object was not found")
        return ("upstream_error", f"Kubernetes API returned HTTP {status}")
    if isinstance(exc, httpx.RequestError):
        return ("unavailable", "Kubernetes API could not be reached")
    return ("invalid_response", "Kubernetes API returned an invalid response")
