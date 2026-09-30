import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "compare_toolbox_learn_evals.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("compare_toolbox_learn_evals", SCRIPT_PATH)
assert SPEC and SPEC.loader
learn_compare = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = learn_compare
SPEC.loader.exec_module(learn_compare)


def _run(duration_ms: int, tools: list[str], *, scores_pass: bool = True) -> dict:
    return {
        "status": "completed",
        "foundry": {"result_counts": {"passed": int(scores_pass), "failed": int(not scores_pass)}},
        "results": [
            {
                "status": "completed",
                "duration_ms": duration_ms,
                "scores": {
                    "ToolCallAccuracy": {"tool_call_accuracy_passed": scores_pass},
                    "ToolCallSuccess": {"tool_call_success_passed": True},
                },
                "tool_calls": [
                    {"status": "completed", "skillName": tool, "output": "evidence"} for tool in tools
                ],
            }
        ],
    }


def test_accepts_complete_direct_and_toolbox_routes() -> None:
    direct = _run(10_000, sorted(learn_compare.DIRECT_TOOLS) + ["view"])
    toolbox = _run(11_000, sorted(learn_compare.TOOLBOX_TOOLS) + ["rg"])

    direct_metrics = learn_compare.summarize_learn_runs(
        "direct", [direct, direct], learn_compare.DIRECT_TOOLS, learn_compare.TOOLBOX_TOOLS
    )
    toolbox_metrics = learn_compare.summarize_learn_runs(
        "Toolbox", [toolbox, toolbox], learn_compare.TOOLBOX_TOOLS, learn_compare.DIRECT_TOOLS
    )

    assert direct_metrics.pass_rate == 1
    assert toolbox_metrics.pass_rate == 1
    assert learn_compare.threshold_failures(direct_metrics, toolbox_metrics, 1, 0.2) == []


def test_rejects_missing_fetch_opposite_route_and_failed_evaluator() -> None:
    run = _run(
        10_000,
        ["kratos-tools-microsoft_docs_search", "microsoft-learn-microsoft_docs_fetch"],
        scores_pass=False,
    )

    failures = learn_compare.learn_route_failures(
        run, learn_compare.TOOLBOX_TOOLS, learn_compare.DIRECT_TOOLS
    )

    assert any("did not invoke kratos-tools-microsoft_docs_fetch" in failure for failure in failures)
    assert any("unexpectedly invoked microsoft-learn-microsoft_docs_fetch" in failure for failure in failures)
    assert any("ToolCallAccuracy did not pass" in failure for failure in failures)


def test_rejects_toolbox_latency_regression() -> None:
    direct_run = _run(10_000, sorted(learn_compare.DIRECT_TOOLS))
    toolbox_run = _run(13_000, sorted(learn_compare.TOOLBOX_TOOLS))
    direct = learn_compare.summarize_learn_runs(
        "direct", [direct_run, direct_run], learn_compare.DIRECT_TOOLS, learn_compare.TOOLBOX_TOOLS
    )
    toolbox = learn_compare.summarize_learn_runs(
        "Toolbox", [toolbox_run, toolbox_run], learn_compare.TOOLBOX_TOOLS, learn_compare.DIRECT_TOOLS
    )

    assert any("p95" in failure for failure in learn_compare.threshold_failures(direct, toolbox, 1, 0.2))


def test_measure_drains_requested_sessions_before_recording(monkeypatch) -> None:
    evaluations = []

    def evaluate(use_case: str, scenario: str) -> dict:
        evaluations.append((use_case, scenario))
        return {"sequence": len(evaluations)}

    monkeypatch.setattr(learn_compare.compare, "_evaluate", evaluate)

    measured = learn_compare._measure("direct MCP", 2, 2, "generic")

    assert len(evaluations) == 4
    assert [run["sequence"] for run in measured] == [3, 4]
