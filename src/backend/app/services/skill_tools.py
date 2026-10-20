"""Custom Copilot tools retained by the Agent Reliability Engineer."""

from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from copilot.tools import define_tool
from opentelemetry import trace

from app.config import Settings
from app.services.action_approval import action_approval_store
from app.services.app_insights_investigation import (
    AppInsightsInvestigationParams,
    AppInsightsInvestigationService,
)
from app.services.incident_command_brief import (
    IncidentBriefGetParams,
    IncidentBriefUpdateParams,
    brief_document,
    with_staleness,
)
from app.services.kubernetes_inspection import (
    KubernetesEventsParams,
    KubernetesInspectionClient,
    KubernetesPodsParams,
    KubernetesWorkloadParams,
    classify_kubernetes_error,
    kubernetes_result,
)
from app.services.servicenow_incidents import (
    ServiceNowClient,
    ServiceNowExecuteUpdateParams,
    ServiceNowIncidentParams,
    ServiceNowPrepareUpdateParams,
    ServiceNowUpdatesParams,
    classify_upstream_error,
)

if TYPE_CHECKING:
    from app.services.cosmos_service import CosmosService

tracer = trace.get_tracer(__name__)

_ctx_use_case: ContextVar[str] = ContextVar("kratos_use_case", default="")
_ctx_conversation_id: ContextVar[str] = ContextVar("kratos_conversation_id", default="")
_ctx_eval_run_id: ContextVar[str] = ContextVar("kratos_eval_run_id", default="")
_tool_cosmos_service: "CosmosService | None" = None


def configure_tool_runtime(cosmos_service: "CosmosService") -> None:
    global _tool_cosmos_service
    _tool_cosmos_service = cosmos_service


def _set_kratos_attrs(span: trace.Span) -> None:
    """Stamp active ARE request context onto a tool span."""
    use_case = _ctx_use_case.get()
    if use_case:
        span.set_attribute("kratos.use_case", use_case)
    conversation_id = _ctx_conversation_id.get()
    if conversation_id:
        span.set_attribute("kratos.conversation_id", conversation_id)
    eval_run_id = _ctx_eval_run_id.get()
    if eval_run_id:
        span.set_attribute("kratos.eval_run_id", eval_run_id)


@define_tool(
    description=(
        "Investigate a suspected reliability cause using bounded, read-only queries "
        "against the configured Application Insights resource"
    )
)
async def app_insights_investigation(params: AppInsightsInvestigationParams) -> dict:
    with tracer.start_as_current_span("skill.app_insights_investigation") as span:
        _set_kratos_attrs(span)
        span.set_attribute("are.service_name", params.service_name)
        span.set_attribute("are.lookback_hours", params.lookback_hours)
        service = AppInsightsInvestigationService(Settings())
        try:
            return await service.investigate(params)
        finally:
            await service.close()


async def _servicenow_read(operation) -> dict:  # noqa: ANN001
    client = ServiceNowClient(Settings())
    try:
        return await operation(client)
    except Exception as exc:  # noqa: BLE001
        code, message = classify_upstream_error(exc)
        return {"status": "error", "code": code, "message": message, "readOnly": True}
    finally:
        await client.close()


@define_tool(description="Read one incident from the configured production ServiceNow instance by exact INC number")
async def servicenow_get_major_incident(params: ServiceNowIncidentParams) -> dict:
    async def read(client: ServiceNowClient) -> dict:
        incident = await client.get_incident(params.incident_number)
        if incident is None:
            return {
                "status": "not_found",
                "incidentNumber": params.incident_number,
                "readOnly": True,
            }
        return {
            "status": "success",
            "source": "configured-servicenow-instance",
            "readOnly": True,
            "incident": incident,
            "isMajorIncident": bool(incident.get("major_incident_state")),
            "retrievedAt": datetime.now(UTC).isoformat(),
        }

    with tracer.start_as_current_span("skill.servicenow_get_major_incident") as span:
        _set_kratos_attrs(span)
        span.set_attribute("are.incident_number", params.incident_number)
        return await _servicenow_read(read)


@define_tool(description="Read bounded internal and caller-visible updates for one configured ServiceNow incident")
async def servicenow_list_incident_updates(params: ServiceNowUpdatesParams) -> dict:
    async def read(client: ServiceNowClient) -> dict:
        incident = await client.get_incident(params.incident_number)
        if incident is None:
            return {"status": "not_found", "incidentNumber": params.incident_number, "readOnly": True}
        updates = await client.list_updates(str(incident["sys_id"]), params.limit)
        return {
            "status": "success",
            "source": "configured-servicenow-instance",
            "readOnly": True,
            "incidentNumber": params.incident_number,
            "updates": updates,
            "retrievedAt": datetime.now(UTC).isoformat(),
        }

    with tracer.start_as_current_span("skill.servicenow_list_incident_updates") as span:
        _set_kratos_attrs(span)
        span.set_attribute("are.incident_number", params.incident_number)
        return await _servicenow_read(read)


@define_tool(
    description=(
        "Prepare exactly one ServiceNow incident update after reading current state. "
        "Returns an approvalQuestion that must be passed unchanged to ask_user."
    )
)
async def servicenow_prepare_update(params: ServiceNowPrepareUpdateParams) -> dict:
    conversation_id = _ctx_conversation_id.get()
    if not conversation_id:
        return {"status": "error", "code": "missing_conversation", "message": "No active conversation context"}
    client = ServiceNowClient(Settings())
    try:
        incident = await client.get_incident(params.incident_number)
        if incident is None:
            return {"status": "not_found", "incidentNumber": params.incident_number}
        proposal = action_approval_store.prepare(
            conversation_id=conversation_id,
            system="servicenow",
            target=params.incident_number,
            action=params.action,
            payload={
                "incidentSysId": str(incident["sys_id"]),
                "fields": params.update_fields(),
                "reason": params.reason,
            },
            current_state=incident,
            display=params.proposal_display(),
        )
        return {
            "status": "prepared",
            "requiresApproval": True,
            "approvalId": proposal.approval_id,
            "approvalQuestion": proposal.approval_question,
            "expiresAt": proposal.expires_at.isoformat(),
            "currentState": incident,
            "instruction": "Call ask_user with approvalQuestion unchanged. Execute only after an affirmative answer.",
        }
    except Exception as exc:  # noqa: BLE001
        code, message = classify_upstream_error(exc)
        return {"status": "error", "code": code, "message": message}
    finally:
        await client.close()


def _verification_matches(proposal_payload: dict[str, Any], refreshed: dict[str, Any], updates: list[dict]) -> bool:
    fields = proposal_payload["fields"]
    for field, expected in fields.items():
        if field == "work_notes":
            return any(entry.get("element") == "work_notes" and entry.get("value") == expected for entry in updates)
        if field == "comments":
            return any(entry.get("element") == "comments" and entry.get("value") == expected for entry in updates)
        actual = refreshed.get(field)
        if isinstance(actual, dict):
            actual = actual.get("value")
        if str(actual) != expected:
            return False
    return True


@define_tool(
    description=(
        "Execute one previously prepared and explicitly approved ServiceNow update. "
        "The approval is single-use; the tool returns an audit receipt and independent re-read verification."
    )
)
async def servicenow_execute_update(params: ServiceNowExecuteUpdateParams) -> dict:
    conversation_id = _ctx_conversation_id.get()
    proposal = action_approval_store.consume(conversation_id, params.approval_id)
    if proposal is None:
        return {
            "status": "rejected",
            "code": "approval_missing_expired_or_used",
            "message": "No valid single-use approval exists for this action",
        }
    if _tool_cosmos_service is None:
        return {"status": "error", "code": "audit_unavailable", "message": "The shared action ledger is unavailable"}

    ledger_id = f"action:{proposal.approval_id}"
    started_at = datetime.now(UTC).isoformat()
    ledger = {
        "id": ledger_id,
        "kind": "servicenow-action",
        "status": "started",
        "conversationId": conversation_id,
        "target": proposal.target,
        "action": proposal.action,
        "fingerprint": proposal.fingerprint,
        "startedAt": started_at,
    }
    if not await _tool_cosmos_service.create_setting_once(ledger):
        existing = await _tool_cosmos_service.get_setting(ledger_id)
        return {
            "status": "duplicate",
            "code": "idempotency_replay_blocked",
            "receipt": existing,
        }

    client = ServiceNowClient(Settings())
    try:
        write_receipt = await client.update_incident(
            proposal.payload["incidentSysId"],
            proposal.payload["fields"],
            idempotency_key=proposal.approval_id,
        )
        refreshed = await client.get_incident(proposal.target)
        updates: list[dict] = []
        if refreshed is not None and any(field in proposal.payload["fields"] for field in ("work_notes", "comments")):
            updates = await client.list_updates(proposal.payload["incidentSysId"], 100)
        verified = refreshed is not None and _verification_matches(proposal.payload, refreshed, updates)
        completed_at = datetime.now(UTC).isoformat()
        receipt = {
            **ledger,
            "status": "verified" if verified else "unverified",
            "completedAt": completed_at,
            "upstreamReceipt": write_receipt,
            "verification": {
                "status": "verified" if verified else "unverified",
                "reReadAt": completed_at,
            },
        }
        await _tool_cosmos_service.upsert_setting(receipt)
        return receipt
    except Exception as exc:  # noqa: BLE001
        code, message = classify_upstream_error(exc)
        status = "unverified" if code in {"timeout", "unavailable"} else "failed"
        receipt = {
            **ledger,
            "status": status,
            "completedAt": datetime.now(UTC).isoformat(),
            "error": {"code": code, "message": message},
            "verification": {"status": status},
        }
        await _tool_cosmos_service.upsert_setting(receipt)
        return receipt
    finally:
        await client.close()


@define_tool(
    description=(
        "Load the latest persisted incident command brief with revision and per-source staleness. "
        "Returns revision 0 when no brief exists."
    )
)
async def incident_brief_get(params: IncidentBriefGetParams) -> dict:
    if _tool_cosmos_service is None:
        return {"status": "error", "code": "persistence_unavailable"}
    document = await _tool_cosmos_service.get_setting(f"incident-brief:{params.incident_number}")
    if document is None:
        return {
            "status": "not_found",
            "incidentNumber": params.incident_number,
            "revision": 0,
            "instruction": "Collect authoritative evidence before creating revision 1.",
        }
    return {"status": "success", **with_staleness(document)}


@define_tool(
    description=(
        "Create or replace a persisted incident command brief using optimistic revision control. "
        "Every timeline/decision claim requires source references and completed actions require verification evidence."
    )
)
async def incident_brief_update(params: IncidentBriefUpdateParams) -> dict:
    if _tool_cosmos_service is None:
        return {"status": "error", "code": "persistence_unavailable"}
    conversation_id = _ctx_conversation_id.get()
    next_revision = params.expected_revision + 1
    document = brief_document(
        params.brief,
        revision=next_revision,
        conversation_id=conversation_id,
    )
    if params.expected_revision == 0:
        saved = await _tool_cosmos_service.create_setting_once(document)
    else:
        saved = await _tool_cosmos_service.replace_setting_if_revision(document, params.expected_revision)
    if not saved:
        current = await _tool_cosmos_service.get_setting(document["id"])
        return {
            "status": "conflict",
            "code": "brief_revision_changed",
            "expectedRevision": params.expected_revision,
            "currentRevision": current.get("revision") if current else None,
            "instruction": "Reload the brief, merge against current evidence, and retry with its revision.",
        }
    return {"status": "saved", **with_staleness(document)}


async def _kubernetes_read(operation: str, reader) -> dict:  # noqa: ANN001
    client = KubernetesInspectionClient(Settings())
    try:
        return kubernetes_result(operation, await reader(client))
    except Exception as exc:  # noqa: BLE001
        code, message = classify_kubernetes_error(exc)
        return {
            "status": "error",
            "code": code,
            "message": message,
            "source": "in-cluster-kubernetes-api",
            "readOnly": True,
        }
    finally:
        await client.close()


@define_tool(description="Read rollout and readiness status for one exact allowlisted Kubernetes workload")
async def kubernetes_get_workload(params: KubernetesWorkloadParams) -> dict:
    with tracer.start_as_current_span("skill.kubernetes_get_workload") as span:
        _set_kratos_attrs(span)
        span.set_attribute("k8s.namespace.name", params.namespace)
        span.set_attribute("k8s.workload.name", params.name)
        return await _kubernetes_read("get_workload", lambda client: client.get_workload(params))


@define_tool(description="Read bounded pod health for one service in an allowlisted Kubernetes namespace")
async def kubernetes_list_service_pods(params: KubernetesPodsParams) -> dict:
    with tracer.start_as_current_span("skill.kubernetes_list_service_pods") as span:
        _set_kratos_attrs(span)
        span.set_attribute("k8s.namespace.name", params.namespace)
        span.set_attribute("service.name", params.service_name)
        return await _kubernetes_read("list_service_pods", lambda client: client.list_pods(params))


@define_tool(description="Read bounded Kubernetes events for one exact object in an allowlisted namespace")
async def kubernetes_list_object_events(params: KubernetesEventsParams) -> dict:
    with tracer.start_as_current_span("skill.kubernetes_list_object_events") as span:
        _set_kratos_attrs(span)
        span.set_attribute("k8s.namespace.name", params.namespace)
        span.set_attribute("k8s.object.name", params.object_name)
        return await _kubernetes_read("list_object_events", lambda client: client.list_events(params))


ALL_TOOLS = [
    app_insights_investigation,
    servicenow_get_major_incident,
    servicenow_list_incident_updates,
    servicenow_prepare_update,
    servicenow_execute_update,
    incident_brief_get,
    incident_brief_update,
    kubernetes_get_workload,
    kubernetes_list_service_pods,
    kubernetes_list_object_events,
]
TOOL_SKILL_MAP = {
    "servicenow_get_major_incident": "incident_updates",
    "servicenow_list_incident_updates": "incident_updates",
    "servicenow_prepare_update": "incident_updates",
    "servicenow_execute_update": "incident_updates",
    "incident_brief_get": "incident_command_brief",
    "incident_brief_update": "incident_command_brief",
    "kubernetes_get_workload": "kubernetes_inspection",
    "kubernetes_list_service_pods": "kubernetes_inspection",
    "kubernetes_list_object_events": "kubernetes_inspection",
}
TOOL_MAP = {tool.name: tool for tool in ALL_TOOLS}


def get_enabled_custom_tools(enabled_names: set[str]) -> list:
    return [tool for tool in ALL_TOOLS if tool.name in enabled_names or TOOL_SKILL_MAP.get(tool.name) in enabled_names]
