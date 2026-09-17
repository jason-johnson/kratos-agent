import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "compare_toolbox_evals.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("compare_toolbox_evals", SCRIPT_PATH)
assert SPEC and SPEC.loader
compare = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = compare
SPEC.loader.exec_module(compare)


def _run(
    duration_ms: int,
    tool: str,
    passed: bool = True,
    *,
    response: str = "",
    tool_output: str = "",
) -> dict:
    return {
        "status": "completed",
        "foundry": {"result_counts": {"passed": int(passed), "failed": int(not passed)}},
        "results": [
            {
                "status": "completed",
                "duration_ms": duration_ms,
                "response": response,
                "scores": {
                    "TaskAdherence": {"task_adherence_passed": passed},
                    "Relevance": {"relevance_passed": True},
                },
                "tool_calls": [
                    {
                        "status": "completed",
                        "skillName": tool,
                        "output": tool_output,
                    }
                ],
            }
        ],
    }


def _grounded_response() -> tuple[str, str]:
    urls = [f"https://example.com/source-{index}" for index in range(1, 6)]
    response = "\n".join(
        f"{index}. **Trend {index}**: One concise description. [{index}]({url})"
        for index, url in enumerate(urls, start=1)
    )
    return response, "\n".join(urls)


def test_summarizes_runs_and_accepts_thresholds() -> None:
    local = compare.summarize_runs("local", [_run(10_000, "web_search"), _run(12_000, "web_search")])
    toolbox = compare.summarize_runs(
        "Toolbox",
        [_run(10_500, "kratos-tools-web_search"), _run(13_000, "kratos-tools-web_search")],
    )

    assert local.pass_rate == 1
    assert local.median_ms == 11_000
    assert local.p95_ms == 12_000
    assert compare.threshold_failures(local, toolbox, min_pass_rate=1, max_p95_regression=0.1) == []


def test_rejects_failed_or_slow_toolbox_runs() -> None:
    local = compare.summarize_runs("local", [_run(10_000, "web_search"), _run(10_000, "web_search")])
    toolbox = compare.summarize_runs(
        "Toolbox",
        [_run(11_000, "kratos-tools-web_search"), _run(13_000, "kratos-tools-web_search", passed=False)],
    )

    failures = compare.threshold_failures(local, toolbox, min_pass_rate=1, max_p95_regression=0.2)

    assert any("pass rate" in failure for failure in failures)
    assert any("p95" in failure for failure in failures)


def test_summarizes_invocation_errors_as_failed_runs() -> None:
    response, tool_output = _grounded_response()
    failed_run = {
        "status": "completed",
        "results": [{"status": "error", "duration_ms": 100, "error": "Hosted agent error: HTTP 409"}],
    }

    metrics = compare.summarize_runs(
        "local",
        [_run(10_000, "web_search", response=response, tool_output=tool_output), failed_run],
        "web_search",
    )

    assert metrics.runs == 2
    assert metrics.passed == 1
    assert metrics.median_ms == 10_000


def test_web_search_contract_ignores_task_adherence_when_evidence_is_valid() -> None:
    response, tool_output = _grounded_response()
    run = _run(
        10_000,
        "kratos-tools-web_search",
        passed=False,
        response=response,
        tool_output=tool_output,
    )

    assert compare.web_search_contract_failures(run, "kratos-tools-web_search") == []
    assert compare.web_search_run_passed(run, "kratos-tools-web_search")


def test_web_search_contract_rejects_unsupported_citation() -> None:
    response, tool_output = _grounded_response()
    response = response.replace("https://example.com/source-5", "https://unsupported.example/source")
    run = _run(10_000, "kratos-tools-web_search", response=response, tool_output=tool_output)

    failures = compare.web_search_contract_failures(run, "kratos-tools-web_search")

    assert any("not present in tool evidence" in failure for failure in failures)


def test_web_search_contract_rejects_non_concise_items() -> None:
    response, tool_output = _grounded_response()
    response = response.replace(
        "1. **Trend 1**: One concise description.",
        "1. **Trend 1**: First sentence. Second sentence.",
    )
    run = _run(10_000, "web_search", response=response, tool_output=tool_output)

    failures = compare.web_search_contract_failures(run, "web_search")

    assert any("one sentence" in failure for failure in failures)


def test_web_search_contract_rejects_preamble_and_multiline_items() -> None:
    response, tool_output = _grounded_response()
    response = "Here are the results:\n" + response.replace(
        "1. **Trend 1**: One concise description.",
        "1. **Trend 1**:\nOne concise description.",
    )
    run = _run(10_000, "web_search", response=response, tool_output=tool_output)

    failures = compare.web_search_contract_failures(run, "web_search")

    assert any("no preamble" in failure for failure in failures)
    assert any("one line" in failure for failure in failures)


def test_rejects_deployed_scenario_drift(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scenario_dir = tmp_path / "use-cases" / "generic" / "evals" / "scenarios"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "example.json").write_text(json.dumps({"name": "example", "input_message": "new"}))
    monkeypatch.setattr(compare, "__file__", str(tmp_path / "scripts" / "compare_toolbox_evals.py"))

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"scenarios": [{"name": "example", "input_message": "old"}]}

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def get(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(compare.run_evals.httpx, "Client", Client)

    with pytest.raises(RuntimeError, match="differs"):
        compare._verify_deployed_scenario("generic", "example")
