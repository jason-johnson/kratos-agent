"""Read-only, namespace-scoped Kubernetes inspection tests."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services import skill_tools
from app.services.kubernetes_inspection import (
    KubernetesEventsParams,
    KubernetesInspectionClient,
    KubernetesPodsParams,
    KubernetesWorkloadParams,
)


def _settings(tmp_path, namespaces: str = "payments") -> Settings:
    token = tmp_path / "token"
    token.write_text("synthetic-service-account-token")
    return Settings(
        kubernetes_api_url="https://kubernetes.default.svc",
        kubernetes_allowed_namespaces=namespaces,
        kubernetes_token_file=str(token),
        kubernetes_namespace_file=str(tmp_path / "namespace"),
        kubernetes_ca_file=str(tmp_path / "ca.crt"),
    )


@pytest.mark.asyncio
async def test_workload_projection_excludes_pod_template_and_secrets(tmp_path):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "checkout-api", "namespace": "payments", "generation": 4},
                "spec": {
                    "replicas": 3,
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "app",
                                    "env": [{"name": "SECRET", "value": "must-not-leak"}],
                                }
                            ]
                        }
                    },
                },
                "status": {
                    "observedGeneration": 4,
                    "replicas": 3,
                    "readyReplicas": 2,
                    "conditions": [
                        {
                            "type": "Available",
                            "status": "False",
                            "reason": "MinimumReplicasUnavailable",
                            "message": "One replica is unavailable.",
                        }
                    ],
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = KubernetesInspectionClient(_settings(tmp_path), client=http)

    result = await client.get_workload(
        KubernetesWorkloadParams(namespace="payments", kind="deployment", name="checkout-api")
    )

    assert result["readyReplicas"] == 2
    assert "template" not in result
    assert "must-not-leak" not in str(result)
    assert captured[0].method == "GET"
    assert captured[0].url.path == "/apis/apps/v1/namespaces/payments/deployments/checkout-api"
    assert captured[0].headers["authorization"].startswith("Bearer ")
    await http.aclose()


@pytest.mark.asyncio
async def test_namespace_allowlist_blocks_request_before_network(tmp_path):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: pytest.fail("network request should not occur"))
    )
    client = KubernetesInspectionClient(_settings(tmp_path), client=http)

    with pytest.raises(PermissionError, match="not allowlisted"):
        await client.list_events(KubernetesEventsParams(namespace="production", object_name="checkout-api"))

    await http.aclose()


@pytest.mark.asyncio
async def test_pod_lookup_uses_fixed_label_selectors_and_bounded_projection(tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "metadata": {"name": "checkout-api-1", "namespace": "payments"},
                        "spec": {"containers": [{"env": [{"value": "must-not-leak"}]}]},
                        "status": {
                            "phase": "Running",
                            "podIP": "10.0.0.2",
                            "containerStatuses": [
                                {
                                    "name": "app",
                                    "ready": False,
                                    "restartCount": 3,
                                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                                }
                            ],
                        },
                    }
                ]
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = KubernetesInspectionClient(_settings(tmp_path), client=http)

    result = await client.list_pods(KubernetesPodsParams(namespace="payments", service_name="checkout-api", limit=10))

    assert len(requests) == 2
    assert {request.url.params["labelSelector"] for request in requests} == {
        "app.kubernetes.io/name=checkout-api",
        "app=checkout-api",
    }
    assert result[0]["podIPPresent"] is True
    assert result[0]["containerStatuses"][0]["restartCount"] == 3
    assert "10.0.0.2" not in str(result)
    assert "must-not-leak" not in str(result)
    await http.aclose()


def test_kubernetes_inputs_reject_arbitrary_paths():
    with pytest.raises(ValidationError):
        KubernetesWorkloadParams(namespace="../secrets", kind="deployment", name="checkout-api")


def test_kubernetes_skill_enables_only_read_tools():
    names = {tool.name for tool in skill_tools.get_enabled_custom_tools({"kubernetes_inspection"})}

    assert names == {
        "kubernetes_get_workload",
        "kubernetes_list_service_pods",
        "kubernetes_list_object_events",
    }
    assert not any(action in name for name in names for action in ("delete", "patch", "restart", "scale", "exec"))
