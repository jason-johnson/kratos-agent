import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "compare_toolbox_evals.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("compare_toolbox_evals", SCRIPT_PATH)
assert SPEC and SPEC.loader
compare = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = compare
SPEC.loader.exec_module(compare)


def _run(duration_ms: int, tool: str, passed: bool = True) -> dict:
    return {
        "status": "completed",
        "foundry": {"result_counts": {"passed": int(passed), "failed": int(not passed)}},
        "results": [
            {
                "status": "completed",
                "duration_ms": duration_ms,
                "tool_calls": [{"status": "completed", "skillName": tool}],
            }
        ],
    }


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
