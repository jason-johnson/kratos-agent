"""Production ServiceNow major-incident read and update contract."""

from __future__ import annotations

import re
import time
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, model_validator

from app.config import Settings

_INCIDENT_NUMBER = re.compile(r"^INC[0-9]{4,12}$")
_INCIDENT_FIELDS = (
    "sys_id,number,short_description,description,state,priority,impact,urgency,"
    "major_incident_state,business_impact,caller_id,assigned_to,assignment_group,"
    "cmdb_ci,opened_at,updated_at,resolved_at,close_notes"
)
_STATE_VALUES = {
    "new": "1",
    "in_progress": "2",
    "on_hold": "3",
    "resolved": "6",
    "closed": "7",
    "canceled": "8",
}


class ServiceNowIncidentParams(BaseModel):
    incident_number: str = Field(
        pattern=r"^INC[0-9]{4,12}$",
        description="Exact ServiceNow incident number, for example INC7002.",
    )


class ServiceNowUpdatesParams(ServiceNowIncidentParams):
    limit: int = Field(default=50, ge=1, le=100)


class ServiceNowPrepareUpdateParams(ServiceNowIncidentParams):
    action: Literal["add_work_note", "add_public_comment", "update_state", "assign"]
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    state: Literal["new", "in_progress", "on_hold", "resolved", "closed", "canceled"] | None = None
    assigned_to: str | None = Field(default=None, min_length=1, max_length=64)
    assignment_group: str | None = Field(default=None, min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_action_payload(self) -> ServiceNowPrepareUpdateParams:
        if self.action in {"add_work_note", "add_public_comment"} and not self.text:
            raise ValueError("text is required for note and comment actions")
        if self.action == "update_state" and self.state is None:
            raise ValueError("state is required for update_state")
        if self.action == "assign" and not (self.assigned_to or self.assignment_group):
            raise ValueError("assigned_to or assignment_group is required for assign")
        return self

    def update_fields(self) -> dict[str, str]:
        if self.action == "add_work_note":
            return {"work_notes": self.text or ""}
        if self.action == "add_public_comment":
            return {"comments": self.text or ""}
        if self.action == "update_state":
            return {"state": _STATE_VALUES[self.state or "new"]}
        fields: dict[str, str] = {}
        if self.assigned_to:
            fields["assigned_to"] = self.assigned_to
        if self.assignment_group:
            fields["assignment_group"] = self.assignment_group
        return fields

    def proposal_display(self) -> str:
        visibility = "internal" if self.action == "add_work_note" else "caller-visible"
        lines = [
            "System: ServiceNow",
            f"Target: {self.incident_number}",
            f"Action: {self.action}",
            f"Reason: {self.reason}",
        ]
        if self.text:
            lines.extend((f"Visibility: {visibility}", "Exact wording:", self.text))
        if self.state:
            lines.append(f"New state: {self.state}")
        if self.assigned_to:
            lines.append(f"Assigned to sys_id: {self.assigned_to}")
        if self.assignment_group:
            lines.append(f"Assignment group sys_id: {self.assignment_group}")
        return "\n".join(lines)


class ServiceNowExecuteUpdateParams(BaseModel):
    approval_id: str = Field(
        pattern=r"^[0-9a-f-]{36}$",
        description="Single-use approval ID returned by servicenow_prepare_update.",
    )


class ServiceNowConfigurationError(RuntimeError):
    pass


class ServiceNowClient:
    """Calls a single configured ServiceNow instance using OAuth client credentials."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = settings.servicenow_instance_url.strip().rstrip("/")
        self._client_id = settings.servicenow_client_id.strip()
        self._client_secret = settings.servicenow_client_secret
        self._token_url = settings.servicenow_token_url.strip()
        self._timeout_seconds = settings.integration_timeout_seconds
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_seconds),
            follow_redirects=False,
        )
        self._owns_client = client is None
        self._access_token = ""
        self._access_token_expires_at = 0.0

    def _validate_configuration(self) -> None:
        if not (self._base_url and self._client_id and self._client_secret):
            raise ServiceNowConfigurationError(
                "ServiceNow is not configured; set SERVICENOW_INSTANCE_URL, "
                "SERVICENOW_CLIENT_ID, and SERVICENOW_CLIENT_SECRET"
            )
        parsed = urlparse(self._base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ServiceNowConfigurationError("SERVICENOW_INSTANCE_URL must be an HTTPS origin")

    async def _token(self) -> str:
        self._validate_configuration()
        if self._access_token and self._access_token_expires_at > time.monotonic() + 30:
            return self._access_token
        token_url = self._token_url or f"{self._base_url}/oauth_token.do"
        response = await self._client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ServiceNowConfigurationError("ServiceNow OAuth response did not include an access token")
        self._access_token = token
        self._access_token_expires_at = time.monotonic() + int(payload.get("expires_in") or 300)
        return token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        token = await self._token()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        response = await self._client.request(
            method,
            f"{self._base_url}{path}",
            params=params,
            json=json,
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("ServiceNow returned a non-object response")
        return payload

    async def get_incident(self, incident_number: str) -> dict[str, Any] | None:
        if not _INCIDENT_NUMBER.fullmatch(incident_number):
            raise ValueError("Invalid ServiceNow incident number")
        payload = await self._request(
            "GET",
            "/api/now/table/incident",
            params={
                "sysparm_query": f"number={incident_number}",
                "sysparm_fields": _INCIDENT_FIELDS,
                "sysparm_limit": "1",
                "sysparm_display_value": "false",
                "sysparm_exclude_reference_link": "true",
            },
        )
        result = payload.get("result")
        if not isinstance(result, list) or not result:
            return None
        incident = result[0]
        if not isinstance(incident, dict):
            raise ValueError("ServiceNow returned an invalid incident record")
        return incident

    async def list_updates(self, incident_sys_id: str, limit: int = 50) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/api/now/table/sys_journal_field",
            params={
                "sysparm_query": (
                    f"element_id={incident_sys_id}^name=incident^elementINwork_notes,comments^ORDERBYsys_created_on"
                ),
                "sysparm_fields": "sys_id,element,value,sys_created_on,sys_created_by",
                "sysparm_limit": str(min(max(limit, 1), 100)),
                "sysparm_display_value": "false",
                "sysparm_exclude_reference_link": "true",
            },
        )
        result = payload.get("result")
        if not isinstance(result, list):
            raise ValueError("ServiceNow returned an invalid journal response")
        return [entry for entry in result if isinstance(entry, dict)]

    async def update_incident(
        self,
        incident_sys_id: str,
        fields: dict[str, str],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = await self._request(
            "PATCH",
            f"/api/now/table/incident/{incident_sys_id}",
            params={
                "sysparm_fields": _INCIDENT_FIELDS,
                "sysparm_display_value": "false",
                "sysparm_exclude_reference_link": "true",
            },
            json=fields,
            idempotency_key=idempotency_key,
        )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("ServiceNow returned an invalid update receipt")
        return result

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def classify_upstream_error(exc: BaseException) -> tuple[str, str]:
    """Return a safe machine code and actionable message without response bodies."""
    if isinstance(exc, ServiceNowConfigurationError):
        return ("not_configured", str(exc))
    if isinstance(exc, httpx.TimeoutException):
        return ("timeout", "ServiceNow did not respond before the configured timeout")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return ("permission_denied", f"ServiceNow denied the request with HTTP {status}")
        if status == 404:
            return ("not_found", "The configured ServiceNow API endpoint was not found")
        if status == 429:
            return ("rate_limited", "ServiceNow rate-limited the request")
        return ("upstream_error", f"ServiceNow returned HTTP {status}")
    if isinstance(exc, httpx.RequestError):
        return ("unavailable", "ServiceNow could not be reached")
    return ("invalid_response", "ServiceNow returned an invalid response")
