import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "run_evals.py"
SPEC = importlib.util.spec_from_file_location("run_evals", SCRIPT_PATH)
assert SPEC and SPEC.loader
run_evals = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_evals)


def test_print_summary_renders_nested_evaluator_scores(capsys) -> None:
    run_evals.print_summary(
        {
            "run_id": "run-1",
            "use_case": "generic",
            "mode": "foundry",
            "status": "completed",
            "results": [
                {
                    "scenario": "web-search-ai-trends",
                    "status": "completed",
                    "duration_ms": 25685,
                    "scores": {
                        "TaskAdherence": {"task_adherence_score": 1.0},
                        "Relevance": {"relevance_score": 4.0},
                    },
                }
            ],
        }
    )

    output = capsys.readouterr().out
    assert "web-search-ai-trends" in output
    assert "25.7s" in output
    assert "TaskAdherence=1/1" in output
    assert "Relevance=4/5" in output
