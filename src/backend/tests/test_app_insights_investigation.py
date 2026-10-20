"""Tests for bounded Application Insights reliability investigations."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from azure.monitor.query import LogsQueryStatus
from pydantic import ValidationError

from app.config import Settings
from app.services.app_insights_investigation import (
    AppInsightsInvestigationParams,
    AppInsightsInvestigationService,
    build_investigation_queries,
)
from app.services.skill_registry import SkillRegistry
from app.services.skill_tools import TOOL_MAP


class FakeLogsClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def query_resource(self, **kwargs):
        self.calls.append(kwargs)
        table = kwargs["query"].splitlines()[0].strip()
        response = self.responses[table]
        if isinstance(response, BaseException):
            raise response
        return response

    def query_workspace(self, **kwargs):
        return self.query_resource(**kwargs)

    def close(self) -> None:
        return None


def _result(*rows: list[object]):
    table = SimpleNamespace(
        columns=[SimpleNamespace(name="timestamp"), SimpleNamespace(name="name")],
        rows=list(rows),
    )
    return SimpleNamespace(status=LogsQueryStatus.SUCCESS, tables=[table])


def test_parameters_enforce_query_bounds():
    with pytest.raises(ValidationError):
        AppInsightsInvestigationParams(
            service_name="checkout-api",
            suspected_cause="dependency timeout",
            lookback_hours=25,
        )

    with pytest.raises(ValidationError):
        AppInsightsInvestigationParams(
            service_name="checkout-api",
            suspected_cause="dependency timeout",
            max_rows_per_query=51,
        )


def test_queries_are_fixed_and_escape_user_values():
    params = AppInsightsInvestigationParams(
        service_name="checkout'api",
        suspected_cause="SQL timeout after release",
        lookback_hours=6,
        max_rows_per_query=7,
    )

    queries = build_investigation_queries(params)

    assert [query.table for query in queries] == [
        "requests",
        "dependencies",
        "exceptions",
        "traces",
        "customEvents",
    ]
    assert all("ago(6h)" in query.kql for query in queries)
    assert all("| take 7" in query.kql for query in queries)
    assert all("checkout''api" in query.kql for query in queries)
    assert "sql" in queries[3].kql
    assert "timeout" in queries[3].kql


@pytest.mark.asyncio
async def test_investigation_uses_only_configured_resource_and_returns_provenance():
    client = FakeLogsClient(
        {
            "requests": _result(["2026-10-15T08:00:00Z", "failed request"]),
            "dependencies": _result(["2026-10-15T08:01:00Z", "database"]),
            "exceptions": _result(),
            "traces": _result(["2026-10-15T08:02:00Z", "timeout"]),
            "customEvents": _result(["2026-10-15T07:55:00Z", "deployment"]),
        }
    )
    settings = Settings(
        application_insights_resource_id="/subscriptions/test/resourceGroups/test/providers/microsoft.insights/components/app"
    )
    service = AppInsightsInvestigationService(settings, client=client)

    result = await service.investigate(
        AppInsightsInvestigationParams(
            service_name="checkout-api",
            suspected_cause="database timeout after deployment",
            lookback_hours=4,
            max_rows_per_query=10,
        )
    )

    assert result["status"] == "partial"
    assert result["readOnly"] is True
    assert result["target"] == "configured-application-insights-resource"
    assert len(result["queries"]) == 5
    assert result["coverageGaps"]
    assert result["freshness"]["status"] == "stale"
    assert len(result["evidence"]) == 4
    assert {item["queryId"] for item in result["evidence"]} == {
        "AI-REQUESTS",
        "AI-DEPENDENCIES",
        "AI-TRACES",
        "AI-CHANGES",
    }
    assert all(
        call["resource_id"] == "/subscriptions/test/resourceGroups/test/providers/microsoft.insights/components/app"
        for call in client.calls
    )
    assert all(call["timespan"] == timedelta(hours=4) for call in client.calls)


@pytest.mark.asyncio
async def test_investigation_reports_partial_failures_without_success_fallback():
    client = FakeLogsClient(
        {
            "requests": _result(["2026-10-15T08:00:00Z", "failed request"]),
            "dependencies": RuntimeError("dependencies query denied"),
            "exceptions": RuntimeError("exceptions query denied"),
            "traces": RuntimeError("traces query denied"),
            "customEvents": RuntimeError("changes query denied"),
            "AzureActivity": RuntimeError("activity query denied"),
        }
    )
    service = AppInsightsInvestigationService(
        Settings(application_insights_workspace_id="workspace-test"),
        client=client,
    )

    result = await service.investigate(
        AppInsightsInvestigationParams(
            service_name="checkout-api",
            suspected_cause="database timeout",
        )
    )

    assert result["status"] == "partial"
    assert len(result["failedQueries"]) == 5
    assert result["queries"][1]["status"] == "failed"


def test_workspace_configuration_adds_allowlisted_azure_activity_query():
    params = AppInsightsInvestigationParams(
        service_name="checkout-api",
        suspected_cause="deployment regression",
        lookback_hours=2,
        max_rows_per_query=12,
    )

    query = build_investigation_queries(params, include_azure_activity=True)[-1]

    assert query.query_id == "AZURE-ACTIVITY"
    assert query.table == "AzureActivity"
    assert "CategoryValue =~ 'Administrative'" in query.kql
    assert "OperationNameValue endswith '/write'" in query.kql
    assert "checkout-api" in query.kql
    assert "| take 12" in query.kql
    assert "SubscriptionId" not in query.kql
    assert "Caller" not in query.kql


@pytest.mark.asyncio
async def test_workspace_investigation_correlates_azure_activity_without_coverage_gap():
    client = FakeLogsClient(
        {
            "requests": _result(),
            "dependencies": _result(),
            "exceptions": _result(),
            "traces": _result(),
            "customEvents": _result(),
            "AzureActivity": _result(["2026-10-20T11:00:00Z", "deployment write"]),
        }
    )
    service = AppInsightsInvestigationService(
        Settings(application_insights_workspace_id="workspace-test"),
        client=client,
    )

    result = await service.investigate(
        AppInsightsInvestigationParams(
            service_name="checkout-api",
            suspected_cause="deployment regression",
        )
    )

    assert result["status"] == "success"
    assert result["coverageGaps"] == []
    assert result["evidence"][0]["queryId"] == "AZURE-ACTIVITY"
    assert len(result["queries"]) == 6


@pytest.mark.asyncio
async def test_investigation_fails_explicitly_without_configured_target():
    service = AppInsightsInvestigationService(Settings(), client=FakeLogsClient({}))

    result = await service.investigate(
        AppInsightsInvestigationParams(
            service_name="checkout-api",
            suspected_cause="database timeout",
        )
    )

    assert result["status"] == "error"
    assert result["error"] == "Application Insights is not configured"


@pytest.mark.asyncio
async def test_are_use_case_resolves_to_registered_investigation_tool():
    repository_root = Path(__file__).resolve().parents[3]
    registry = SkillRegistry()

    await registry.load(
        "agent-reliability-engineer",
        local_root=str(repository_root / "use-cases"),
    )

    assert registry.system_prompt
    assert registry.get_enabled_tool_names() == {
        "app_insights_investigation",
        "file_sharing",
        "incident_command_brief",
        "incident_updates",
        "m365_incident_context",
    }
    assert set(registry.mcp_servers) == {"m365-graph"}
    assert "app_insights_investigation" in TOOL_MAP
    assert {
        "servicenow_get_major_incident",
        "servicenow_list_incident_updates",
        "servicenow_prepare_update",
        "servicenow_execute_update",
    }.issubset(TOOL_MAP)


def test_are_eval_scenarios_are_valid_and_use_investigation_skill():
    repository_root = Path(__file__).resolve().parents[3]
    scenario_dir = repository_root / "use-cases" / "agent-reliability-engineer" / "evals" / "scenarios"
    scenarios = [json.loads(path.read_text()) for path in sorted(scenario_dir.glob("*.json"))]

    assert len(scenarios) == 8
    assert all(scenario["input_message"] for scenario in scenarios)
    assert all(scenario["expected_behavior"] for scenario in scenarios)
    assert all(scenario["expected_tool_calls"] for scenario in scenarios)


def test_are_prompt_and_update_skill_define_exact_write_gate():
    repository_root = Path(__file__).resolve().parents[3]
    use_case_root = repository_root / "use-cases" / "agent-reliability-engineer"
    prompt = (use_case_root / "SYSTEM_PROMPT.md").read_text()
    update_skill = (use_case_root / "skills" / "incident-updates" / "SKILL.md").read_text()
    normalized_prompt = " ".join(prompt.split())

    for phrase in (
        "Present one exact proposed write",
        "Approval is valid only for that exact proposal",
        "Execute exactly one approved write",
        "Re-read the target and verify",
    ):
        assert phrase in normalized_prompt

    assert "Any edit to target, fields, wording, visibility, or" in update_skill
    assert "Microsoft 365 is read-only in v1" in update_skill
    assert "ServiceNow is the only mutable system currently" in update_skill
    assert "`verified`, `unverified`, or `failed`" in update_skill
