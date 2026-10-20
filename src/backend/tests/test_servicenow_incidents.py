"""Production ServiceNow contract and approval lifecycle tests."""

from __future__ import annotations

import json

import httpx
import pytest
from copilot.tools import ToolInvocation

from app.config import Settings
from app.services import skill_tools
from app.services.action_approval import ActionApprovalStore, action_approval_store
from app.services.cosmos_service import CosmosService
from app.services.servicenow_incidents import (
    ServiceNowClient,
    ServiceNowConfigurationError,
    ServiceNowExecuteUpdateParams,
    ServiceNowIncidentParams,
    ServiceNowPrepareUpdateParams,
)


async def _invoke(tool, arguments: dict) -> dict:  # noqa: ANN001
    result = await tool.handler(ToolInvocation(arguments=arguments))
    assert result.result_type == "success", result.error
    return json.loads(result.text_result_for_llm)


@pytest.mark.asyncio
async def test_servicenow_read_contract_is_pinned_and_bounded():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/oauth_token.do":
            return httpx.Response(200, json={"access_token": "secret", "expires_in": 300})
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "sys_id": "sys-1",
                        "number": "INC7002",
                        "state": "2",
                        "major_incident_state": "accepted",
                    }
                ]
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ServiceNowClient(
        Settings(
            servicenow_instance_url="https://example.service-now.com",
            servicenow_client_id="client",
            servicenow_client_secret="synthetic-test-value",  # noqa: S106
        ),
        client=http,
    )

    incident = await client.get_incident("INC7002")

    assert incident and incident["number"] == "INC7002"
    read_request = requests[1]
    assert read_request.url.path == "/api/now/table/incident"
    assert read_request.url.params["sysparm_query"] == "number=INC7002"
    assert read_request.url.params["sysparm_limit"] == "1"
    assert read_request.headers["authorization"] == "Bearer secret"
    assert "secret" not in str(read_request.url)
    await http.aclose()


@pytest.mark.asyncio
async def test_servicenow_rejects_non_https_instance():
    client = ServiceNowClient(
        Settings(
            servicenow_instance_url="http://example.service-now.com",
            servicenow_client_id="client",
            servicenow_client_secret="synthetic-test-value",  # noqa: S106
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
    )

    with pytest.raises(ServiceNowConfigurationError, match="HTTPS origin"):
        await client.get_incident("INC7002")

    await client.close()


def test_action_approval_requires_exact_question_and_conversation():
    store = ActionApprovalStore()
    proposal = store.prepare(
        conversation_id="conversation-1",
        system="servicenow",
        target="INC7002",
        action="add_work_note",
        payload={"fields": {"work_notes": "Investigating."}},
        current_state={"state": "2"},
        display="Exact wording: Investigating.",
    )

    assert store.record_response("conversation-2", proposal.approval_question, "Approve") is None
    assert store.record_response("conversation-1", proposal.approval_question + " changed", "Approve") is None
    assert store.record_response("conversation-1", proposal.approval_question, "Deny") is None
    assert store.record_response("conversation-1", proposal.approval_question, "Approve") == proposal.approval_id
    assert store.consume("conversation-1", proposal.approval_id) is proposal
    assert store.consume("conversation-1", proposal.approval_id) is None


@pytest.mark.asyncio
async def test_action_ledger_create_is_atomic_in_local_mode(tmp_path):
    cosmos = CosmosService(Settings(local_mode=True, local_data_dir=str(tmp_path)))
    await cosmos.initialize()

    assert await cosmos.create_setting_once({"id": "action:1", "status": "started"}) is True
    assert await cosmos.create_setting_once({"id": "action:1", "status": "started"}) is False
    assert (await cosmos.get_setting("action:1"))["status"] == "started"

    await cosmos.close()


class _FakeServiceNowClient:
    updated_fields: dict[str, str] | None = None

    def __init__(self, _settings: Settings) -> None:
        pass

    async def get_incident(self, _number: str):
        incident = {
            "sys_id": "sys-1",
            "number": "INC7002",
            "state": "2",
            "major_incident_state": "accepted",
        }
        if self.updated_fields:
            incident.update(self.updated_fields)
        return incident

    async def list_updates(self, _sys_id: str, _limit: int):
        if not self.updated_fields:
            return []
        return [
            {
                "sys_id": "journal-1",
                "element": "work_notes",
                "value": self.updated_fields["work_notes"],
            }
        ]

    async def update_incident(self, _sys_id: str, fields: dict[str, str], *, idempotency_key: str):
        assert idempotency_key
        type(self).updated_fields = fields
        return {"sys_id": "sys-1", **fields}

    async def close(self) -> None:
        pass


class _FakeLedger:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    async def create_setting_once(self, document: dict) -> bool:
        if document["id"] in self.documents:
            return False
        self.documents[document["id"]] = document
        return True

    async def get_setting(self, setting_id: str):
        return self.documents.get(setting_id)

    async def upsert_setting(self, document: dict):
        self.documents[document["id"]] = document
        return document


@pytest.mark.asyncio
async def test_servicenow_tool_executes_only_exact_approved_proposal(monkeypatch):
    _FakeServiceNowClient.updated_fields = None
    ledger = _FakeLedger()
    monkeypatch.setattr(skill_tools, "ServiceNowClient", _FakeServiceNowClient)
    monkeypatch.setattr(skill_tools, "_tool_cosmos_service", ledger)
    token = skill_tools._ctx_conversation_id.set("conversation-1")
    action_approval_store.clear_conversation("conversation-1")

    try:
        prepared = await _invoke(
            skill_tools.servicenow_prepare_update,
            ServiceNowPrepareUpdateParams(
                incident_number="INC7002",
                action="add_work_note",
                text="Checkout errors are under investigation.",
                reason="Record the current investigation status.",
            ).model_dump(),
        )
        approval_id = prepared["approvalId"]

        rejected = await _invoke(
            skill_tools.servicenow_execute_update,
            ServiceNowExecuteUpdateParams(approval_id=approval_id).model_dump(),
        )
        assert rejected["status"] == "rejected"

        assert (
            action_approval_store.record_response(
                "conversation-1",
                prepared["approvalQuestion"],
                "Approve",
            )
            == approval_id
        )
        receipt = await _invoke(
            skill_tools.servicenow_execute_update,
            ServiceNowExecuteUpdateParams(approval_id=approval_id).model_dump(),
        )

        assert receipt["status"] == "verified"
        assert receipt["verification"]["status"] == "verified"
        assert _FakeServiceNowClient.updated_fields == {"work_notes": "Checkout errors are under investigation."}
        assert ledger.documents[f"action:{approval_id}"]["fingerprint"]
    finally:
        skill_tools._ctx_conversation_id.reset(token)
        action_approval_store.clear_conversation("conversation-1")


def test_servicenow_parameter_contract_rejects_arbitrary_targets():
    with pytest.raises(ValueError):
        ServiceNowIncidentParams(incident_number="problem.do?sys_id=anything")

    with pytest.raises(ValueError, match="text is required"):
        ServiceNowPrepareUpdateParams(
            incident_number="INC7002",
            action="add_public_comment",
            reason="Communicate status.",
        )


def test_incident_updates_skill_enables_all_servicenow_tools():
    names = {tool.name for tool in skill_tools.get_enabled_custom_tools({"incident_updates"})}

    assert names == {
        "servicenow_get_major_incident",
        "servicenow_list_incident_updates",
        "servicenow_prepare_update",
        "servicenow_execute_update",
    }
