"""Persisted incident command brief contract tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from copilot.tools import ToolInvocation
from pydantic import ValidationError

from app.config import Settings
from app.services import skill_tools
from app.services.cosmos_service import CosmosService
from app.services.incident_command_brief import (
    BriefAction,
    BriefHypothesis,
    IncidentBriefGetParams,
    IncidentBriefUpdateParams,
    IncidentCommandBriefContent,
    with_staleness,
)


async def _invoke(tool, arguments: dict) -> dict:  # noqa: ANN001
    result = await tool.handler(ToolInvocation(arguments=arguments))
    assert result.result_type == "success", result.error
    return json.loads(result.text_result_for_llm)


def _brief(observed_at: datetime) -> IncidentCommandBriefContent:
    return IncidentCommandBriefContent.model_validate(
        {
            "incident_number": "INC7002",
            "declared_state": "In Progress",
            "severity": "P1",
            "commander": "unassigned",
            "affected_services": ["checkout-api"],
            "user_impact": "Checkout requests are intermittently failing.",
            "timeline": [
                {
                    "timestamp": observed_at,
                    "summary": "Failure rate increased.",
                    "kind": "observation",
                    "source_refs": ["AI-REQUESTS-R1"],
                }
            ],
            "hypotheses": [
                {
                    "hypothesis": "Database timeouts are contributing.",
                    "assessment": "supported",
                    "confidence": "medium",
                    "supporting_refs": ["AI-DEPENDENCIES-R1"],
                }
            ],
            "actions": [
                {
                    "action": "Inspect database saturation.",
                    "status": "in_progress",
                    "verification_signal": "Connection timeout rate returns to baseline.",
                }
            ],
            "questions": [
                {
                    "question": "Did a database configuration change occur?",
                    "status": "open",
                }
            ],
            "source_snapshots": [
                {
                    "source": "ApplicationInsights",
                    "reference": "AI-REQUESTS",
                    "observed_at": observed_at,
                    "status": "success",
                    "stale_after_minutes": 15,
                }
            ],
            "next_checkpoint": "Refresh in 15 minutes.",
        }
    )


def test_command_brief_rejects_unsupported_claims_of_completion():
    with pytest.raises(ValidationError, match="verification_evidence_refs"):
        BriefAction(
            action="Restart service",
            status="completed",
            verification_signal="Healthy requests",
        )

    with pytest.raises(ValidationError, match="supporting_refs"):
        BriefHypothesis(
            hypothesis="Deployment caused failures",
            assessment="supported",
            confidence="high",
        )


def test_command_brief_reports_per_source_staleness():
    now = datetime.now(UTC)
    document = {
        "brief": _brief(now - timedelta(minutes=16)).model_dump(mode="json"),
    }

    result = with_staleness(document, now)

    assert result["staleness"]["stale"] is True
    assert result["staleness"]["sources"][0]["stale"] is True
    assert result["staleness"]["sources"][0]["ageSeconds"] == 960


@pytest.mark.asyncio
async def test_command_brief_tools_persist_and_enforce_revision(tmp_path, monkeypatch):
    cosmos = CosmosService(Settings(local_mode=True, local_data_dir=str(tmp_path)))
    await cosmos.initialize()
    monkeypatch.setattr(skill_tools, "_tool_cosmos_service", cosmos)
    token = skill_tools._ctx_conversation_id.set("conversation-1")
    content = _brief(datetime.now(UTC))

    try:
        missing = await _invoke(
            skill_tools.incident_brief_get,
            IncidentBriefGetParams(incident_number="INC7002").model_dump(),
        )
        assert missing["status"] == "not_found"
        assert missing["revision"] == 0

        saved = await _invoke(
            skill_tools.incident_brief_update,
            IncidentBriefUpdateParams(expected_revision=0, brief=content).model_dump(mode="json"),
        )
        assert saved["status"] == "saved"
        assert saved["revision"] == 1

        conflict = await _invoke(
            skill_tools.incident_brief_update,
            IncidentBriefUpdateParams(expected_revision=0, brief=content).model_dump(mode="json"),
        )
        assert conflict["status"] == "conflict"
        assert conflict["currentRevision"] == 1

        refreshed = await _invoke(
            skill_tools.incident_brief_update,
            IncidentBriefUpdateParams(expected_revision=1, brief=content).model_dump(mode="json"),
        )
        assert refreshed["status"] == "saved"
        assert refreshed["revision"] == 2

        loaded = await _invoke(
            skill_tools.incident_brief_get,
            IncidentBriefGetParams(incident_number="INC7002").model_dump(),
        )
        assert loaded["status"] == "success"
        assert loaded["revision"] == 2
        assert loaded["brief"]["timeline"][0]["source_refs"] == ["AI-REQUESTS-R1"]
    finally:
        skill_tools._ctx_conversation_id.reset(token)
        await cosmos.close()


def test_command_brief_skill_enables_persistence_tools():
    names = {tool.name for tool in skill_tools.get_enabled_custom_tools({"incident_command_brief"})}
    assert names == {"incident_brief_get", "incident_brief_update"}
