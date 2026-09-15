from app.services.eval_service import (
    _build_task_adherence_response,
    _build_tool_definitions,
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
            "tool_call": {
                "id": "tool_1",
                "type": "function",
                "function": {
                    "name": "kratos-tools-web_search",
                    "arguments": {"query": "Foundry"},
                },
            },
            "result": "search result",
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
    assert response[0]["content"][0]["tool_call"]["function"]["name"] == "kratos-tools-web_search"
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