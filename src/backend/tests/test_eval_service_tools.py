from app.services.eval_service import (
    _build_evaluator_kwargs,
    _build_task_adherence_response,
    _build_tool_definitions,
    _evaluator_score_passed,
    _normalise_tool_calls,
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
