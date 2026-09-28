import json
from types import SimpleNamespace

import pytest

from app.models import FoundryEvalSummary, ScenarioResult
from app.services.eval_service import (
    _attach_cloud_evaluation,
    _build_cloud_metric_values,
    _build_evaluator_kwargs,
    _build_task_adherence_response,
    _build_tool_definitions,
    _evaluator_score_passed,
    _normalise_tool_calls,
    _publish_cloud_evaluation,
    _return_precomputed_metrics,
)


def test_normalises_completed_kratos_tool_event_for_evaluators() -> None:
    events = [
        {
            "skillName": "kratos-tools-web_search",
            "status": "started",
            "input": '{"query":"Foundry"}',
            "output": "",
        },
        {
            "skillName": "kratos-tools-web_search",
            "status": "completed",
            "input": '{"query":"Foundry"}',
            "output": "search result",
        },
        {"skillName": "skill", "status": "completed", "output": "loaded"},
    ]

    calls = _normalise_tool_calls(events)

    assert calls == [
        {
            "type": "tool_call",
            "name": "kratos-tools-web_search",
            "arguments": {"query": "Foundry"},
            "tool_call_id": "tool_1",
            "tool_result": "search result",
        }
    ]


def test_builds_task_adherence_conversation_with_tool_result() -> None:
    calls = _normalise_tool_calls(
        [
            {
                "skillName": "kratos-tools-web_search",
                "status": "completed",
                "input": "",
                "output": "search result",
            }
        ]
    )

    response = _build_task_adherence_response("final answer", calls)

    assert [message["role"] for message in response] == ["assistant", "tool", "assistant"]
    assert response[0]["content"][0] == {
        "type": "tool_call",
        "name": "kratos-tools-web_search",
        "arguments": {},
        "tool_call_id": "tool_0",
    }
    assert response[1]["content"][0]["tool_result"] == "search result"
    assert response[2]["content"][0]["text"] == "final answer"


def test_tool_definitions_include_required_id() -> None:
    calls = _normalise_tool_calls(
        [{"skillName": "kratos-tools-web_search", "status": "completed"}]
    )

    definitions = _build_tool_definitions(["web-search"], calls)

    assert definitions[0]["id"] == "mcp-tools-web-search"
    assert definitions[0]["name"] == "mcp-tools-web-search"
    assert definitions[1]["id"] == "kratos-tools-web_search"
    assert definitions[1]["name"] == "kratos-tools-web_search"


def test_builds_process_evaluator_inputs_from_normalised_conversation() -> None:
    calls = _normalise_tool_calls(
        [
            {
                "skillName": "kratos-tools-web_search",
                "status": "completed",
                "input": '{"query":"Foundry"}',
                "output": "search result",
            }
        ]
    )
    definitions = _build_tool_definitions(["web-search"], calls)

    success = _build_evaluator_kwargs("ToolCallSuccess", "question", "answer", calls, definitions)
    utilization = _build_evaluator_kwargs("ToolOutputUtilization", "question", "answer", calls, definitions)

    assert success is not None
    assert set(success) == {"response", "tool_definitions"}
    assert [message["role"] for message in success["response"]] == ["assistant", "tool", "assistant"]
    assert success["tool_definitions"] == definitions
    assert utilization is not None
    assert utilization["query"] == [
        {"role": "user", "content": [{"type": "text", "text": "question"}]}
    ]
    assert utilization["response"] == success["response"]
    assert utilization["tool_definitions"] == definitions


def test_skips_process_evaluators_when_no_tool_was_called() -> None:
    assert _build_evaluator_kwargs("ToolCallSuccess", "question", "answer", [], []) is None
    assert _build_evaluator_kwargs("ToolOutputUtilization", "question", "answer", [], []) is None
    assert _build_evaluator_kwargs("Relevance", "question", "answer", [], []) == {
        "query": "question",
        "response": "answer",
    }


def test_builds_non_sensitive_cloud_metrics() -> None:
    result = ScenarioResult(
        scenario="web-search",
        query="private query",
        response="private response",
        tool_calls=[{"output": "private tool output"}],
        duration_ms=1234,
        scores={
            "ToolCallSuccess": {"tool_call_success_result": "pass"},
            "Relevance": {"relevance_score": 2, "relevance_threshold": 3},
        },
    )

    assert _build_cloud_metric_values(result) == {
        "duration_ms": 1234.0,
        "invocation_passed": 1.0,
        "tool_call_success_passed": 1.0,
        "relevance_passed": 0.0,
        "scenario_passed": 0.0,
    }


def test_publishes_only_precomputed_metrics(monkeypatch) -> None:
    captured = {}

    def fake_evaluate(**kwargs):
        rows = [json.loads(line) for line in kwargs["data"].read_text(encoding="utf-8").splitlines()]
        captured.update(kwargs)
        captured["rows"] = rows
        return {"studio_url": "https://example.test/report", "oai_eval_run_ids": [{"eval_run_id": "cloud-run"}]}

    monkeypatch.setattr("azure.ai.evaluation.evaluate", fake_evaluate)
    result = ScenarioResult(
        scenario="web-search",
        query="private query",
        response="private response",
        tool_calls=[{"output": "private tool output"}],
        duration_ms=1234,
        scores={"ToolCallSuccess": {"tool_call_success_result": "pass"}},
    )

    published = _publish_cloud_evaluation("https://example.test/api/projects/test", "generic", "run-1", [result])

    assert published["studio_url"] == "https://example.test/report"
    assert captured["rows"] == [
        {
            "scenario": "web-search",
            "metrics": {
                "duration_ms": 1234.0,
                "invocation_passed": 1.0,
                "tool_call_success_passed": 1.0,
                "scenario_passed": 1.0,
            },
        }
    ]
    assert captured["evaluators"] == {"KratosScores": _return_precomputed_metrics}
    assert captured["azure_ai_project"] == "https://example.test/api/projects/test"


@pytest.mark.asyncio
async def test_attaches_cloud_report_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.eval_service._publish_cloud_evaluation",
        lambda *_args: {
            "studio_url": "https://example.test/report",
            "oai_eval_run_ids": [{"eval_group_id": "group", "eval_run_id": "cloud-run"}],
        },
    )
    settings = SimpleNamespace(eval_foundry_project_endpoint="project", foundry_project_endpoint="")
    summary = FoundryEvalSummary()

    await _attach_cloud_evaluation(settings, "generic", "run-1", [], summary)

    assert summary.report_url == "https://example.test/report"
    assert summary.cloud_eval_run_ids == [{"eval_group_id": "group", "eval_run_id": "cloud-run"}]
    assert summary.publication_error == ""


@pytest.mark.asyncio
async def test_cloud_publication_failure_is_sanitized(monkeypatch) -> None:
    def fail_with_sensitive_detail(*_args):
        raise RuntimeError("request failed for https://private.example.test")

    monkeypatch.setattr("app.services.eval_service._publish_cloud_evaluation", fail_with_sensitive_detail)
    settings = SimpleNamespace(eval_foundry_project_endpoint="project", foundry_project_endpoint="")
    summary = FoundryEvalSummary()

    await _attach_cloud_evaluation(settings, "generic", "run-1", [], summary)

    assert summary.report_url == ""
    assert summary.publication_error == "Cloud report publication failed (RuntimeError)"
    assert "private" not in summary.publication_error


@pytest.mark.asyncio
async def test_cloud_publication_is_skipped_without_project_endpoint(monkeypatch) -> None:
    def unexpected_publication(*_args):
        raise AssertionError("publication should be skipped")

    monkeypatch.setattr("app.services.eval_service._publish_cloud_evaluation", unexpected_publication)
    settings = SimpleNamespace(eval_foundry_project_endpoint="", foundry_project_endpoint="")
    summary = FoundryEvalSummary()

    await _attach_cloud_evaluation(settings, "generic", "run-1", [], summary)

    assert summary == FoundryEvalSummary()


def test_evaluator_score_does_not_treat_errors_or_skipped_thresholds_as_passes() -> None:
    assert not _evaluator_score_passed({"error": "invalid payload"})
    assert not _evaluator_score_passed(
        {
            "tool_call_accuracy_score": None,
            "tool_call_accuracy_result": "not_applicable",
            "tool_call_accuracy_status": "skipped",
            "tool_call_accuracy_threshold": 3,
        }
    )
    assert _evaluator_score_passed(
        {
            "task_adherence_score": 1.0,
            "task_adherence_passed": True,
            "task_adherence_result": "pass",
            "task_adherence_threshold": 3,
        }
    )
    assert not _evaluator_score_passed(
        {
            "task_adherence_score": 5.0,
            "task_adherence_passed": False,
            "task_adherence_result": "pass",
            "task_adherence_threshold": 3,
        }
    )
