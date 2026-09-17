#!/usr/bin/env python3
"""Compare local and Toolbox evals through controlled Hosted Agent deployments.

The command is intentionally disruptive and requires both an explicit azd
environment and ``--confirm-disruptive``. It starts with Toolbox enabled,
deploys the local fallback, drains the deployment-spanning warm pool, measures
the requested runs, restores Toolbox, drains again, and measures Toolbox.
Toolbox restoration is attempted in ``finally`` after any failure.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import run_evals


@dataclass(frozen=True)
class RunMetrics:
    label: str
    runs: int
    passed: int
    median_ms: float
    p95_ms: float
    tools: tuple[str, ...]

    @property
    def pass_rate(self) -> float:
        return self.passed / self.runs if self.runs else 0.0


def _command(*args: str) -> str:
    completed = subprocess.run(args, check=True, capture_output=True, text=True)  # noqa: S603
    return completed.stdout.strip()


def _azd_value(name: str) -> str:
    return _command("azd", "env", "get-value", name)


def _azd_optional_value(name: str) -> str:
    try:
        return _azd_value(name)
    except subprocess.CalledProcessError:
        return ""


def _verify_deployed_scenario(use_case: str, scenario: str) -> None:
    scenario_path = Path(__file__).parents[1] / "use-cases" / use_case / "evals" / "scenarios" / f"{scenario}.json"
    expected = json.loads(scenario_path.read_text(encoding="utf-8"))
    url = f"{run_evals.BACKEND_URL}/api/use-cases/{use_case}/evals/scenarios"
    with run_evals.httpx.Client(timeout=30.0) as client:
        response = client.get(url, headers=run_evals._headers())  # noqa: SLF001
    response.raise_for_status()
    deployed = next((item for item in response.json().get("scenarios", []) if item.get("name") == scenario), None)
    if deployed != expected:
        raise RuntimeError(
            f"Deployed scenario {use_case}/{scenario} differs from {scenario_path}; "
            "synchronize the blob-backed scenario before running the comparison"
        )


def _run_passed(run: dict[str, Any]) -> bool:
    counts = (run.get("foundry") or {}).get("result_counts") or {}
    return run.get("status") == "completed" and counts.get("failed") == 0 and counts.get("passed", 0) > 0


def _score_passed(score: dict[str, Any]) -> bool:
    if score.get("error"):
        return False
    pass_flags = [
        value
        for key, value in score.items()
        if (key == "passed" or key.endswith("_passed")) and isinstance(value, bool)
    ]
    return bool(pass_flags) and all(pass_flags)


def _completed_tool_events(run: dict[str, Any], expected_tool: str) -> list[dict[str, Any]]:
    return [
        event
        for result in run.get("results") or []
        for event in result.get("tool_calls") or []
        if event.get("status") == "completed" and event.get("skillName") == expected_tool
    ]


def _urls(text: str) -> list[str]:
    return [url.rstrip(".,;:") for url in re.findall(r"https?://[^\s)>\]]+", text)]


def web_search_contract_failures(run: dict[str, Any], expected_tool: str) -> list[str]:
    failures: list[str] = []
    results = run.get("results") or []
    if run.get("status") != "completed" or len(results) != 1 or results[0].get("status") != "completed":
        return ["run did not contain one completed scenario result"]

    tool_events = _completed_tool_events(run, expected_tool)
    if not tool_events:
        failures.append(f"did not invoke {expected_tool}")

    response = str(results[0].get("response") or "")
    if not response.lstrip().startswith("1. "):
        failures.append("response must start with the first numbered item and contain no preamble")
    matches = list(re.finditer(r"(?ms)^\s*(\d+)\.\s+(.+?)(?=^\s*\d+\.\s+|\Z)", response))
    item_numbers = [int(match.group(1)) for match in matches]
    if item_numbers != [1, 2, 3, 4, 5]:
        failures.append("response must contain exactly five consecutively numbered items")
        return failures

    evidence = "\n".join(str(event.get("output") or event.get("result") or "") for event in tool_events)
    evidence_urls = set(_urls(evidence))
    for number, match in enumerate(matches, start=1):
        item = match.group(2).strip()
        if "\n" in item:
            failures.append(f"item {number} must stay on one line")
        item_urls = _urls(item)
        if len(item_urls) != 1:
            failures.append(f"item {number} must contain exactly one source citation")
        for url in item_urls:
            if url not in evidence_urls:
                failures.append(f"item {number} citation is not present in tool evidence: {url}")

        prose = re.sub(r"https?://[^\s)>\]]+", "", item)
        prose = re.sub(r"\[[^\]]*\]\([^)]*\)", "", prose)
        if len(re.findall(r"[.!?](?=\s|$)", prose)) != 1:
            failures.append(f"item {number} must contain one sentence")
    return failures


def web_search_run_passed(run: dict[str, Any], expected_tool: str) -> bool:
    if web_search_contract_failures(run, expected_tool):
        return False
    scores = (run.get("results") or [{}])[0].get("scores") or {}
    quality_scores = [score for name, score in scores.items() if name != "TaskAdherence"]
    return bool(quality_scores) and all(_score_passed(score) for score in quality_scores)


def _tool_names(run: dict[str, Any]) -> set[str]:
    return {
        str(event.get("skillName"))
        for result in run.get("results") or []
        for event in result.get("tool_calls") or []
        if event.get("status") == "completed" and event.get("skillName") != "skill"
    }


def summarize_runs(label: str, runs: list[dict[str, Any]], expected_tool: str | None = None) -> RunMetrics:
    durations = [
        float(result.get("duration_ms", 0))
        for run in runs
        for result in run.get("results") or []
        if result.get("status") == "completed"
    ]
    if not runs or len(durations) != len(runs):
        raise ValueError(f"{label}: every run must contain one completed scenario result")
    ordered = sorted(durations)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    tools = sorted({tool for run in runs for tool in _tool_names(run)})
    return RunMetrics(
        label=label,
        runs=len(runs),
        passed=sum(
            web_search_run_passed(run, expected_tool) if expected_tool else _run_passed(run)
            for run in runs
        ),
        median_ms=statistics.median(durations),
        p95_ms=ordered[p95_index],
        tools=tuple(tools),
    )


def threshold_failures(
    local: RunMetrics,
    toolbox: RunMetrics,
    min_pass_rate: float,
    max_p95_regression: float,
) -> list[str]:
    failures = []
    if local.pass_rate < min_pass_rate:
        failures.append(f"local pass rate {local.pass_rate:.0%} is below {min_pass_rate:.0%}")
    if toolbox.pass_rate < min_pass_rate:
        failures.append(f"Toolbox pass rate {toolbox.pass_rate:.0%} is below {min_pass_rate:.0%}")
    allowed_p95 = local.p95_ms * (1 + max_p95_regression)
    if toolbox.p95_ms > allowed_p95:
        failures.append(
            f"Toolbox p95 {toolbox.p95_ms / 1000:.1f}s exceeds "
            f"local p95 {local.p95_ms / 1000:.1f}s by more than {max_p95_regression:.0%}"
        )
    if "web_search" not in local.tools:
        failures.append("local runs did not invoke web_search")
    if "kratos-tools-web_search" not in toolbox.tools:
        failures.append("Toolbox runs did not invoke kratos-tools-web_search")
    return failures


def _deploy(environment: str, enabled: bool) -> None:
    value = str(enabled).lower()
    subprocess.run(["azd", "env", "set", "TOOLBOX_ENABLED", value], check=True)  # noqa: S603
    subprocess.run(["azd", "deploy", "kratos-agent"], check=True)  # noqa: S603
    definition = json.loads(_command("azd", "ai", "agent", "show", "-e", environment, "--output", "json"))
    actual = str(definition.get("definition", {}).get("environment_variables", {}).get("TOOLBOX_ENABLED", ""))
    if definition.get("status") != "active" or actual != value:
        raise RuntimeError(f"Hosted Agent did not become active with TOOLBOX_ENABLED={value}")


def _evaluate(use_case: str, scenario: str) -> dict[str, Any]:
    started = run_evals.start_run(use_case, "foundry", [scenario])
    run_id = started.get("run_id")
    if not run_id:
        raise RuntimeError("Evaluation start response did not include run_id")
    print(f"  run_id={run_id}")
    return run_evals.poll(use_case, run_id)


def _measure(label: str, count: int, use_case: str, scenario: str) -> list[dict[str, Any]]:
    print(f"Draining warm pool for {label}...")
    _evaluate(use_case, scenario)
    measured = []
    for index in range(1, count + 1):
        print(f"Measuring {label} {index}/{count}...")
        measured.append(_evaluate(use_case, scenario))
    return measured


def _print_metrics(metrics: RunMetrics) -> None:
    print(
        f"{metrics.label:<8} pass={metrics.passed}/{metrics.runs} "
        f"median={metrics.median_ms / 1000:.1f}s p95={metrics.p95_ms / 1000:.1f}s "
        f"tools={','.join(metrics.tools) or 'none'}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, help="Selected azd environment; must match AZURE_ENV_NAME")
    parser.add_argument("--use-case", default="generic")
    parser.add_argument("--scenario", default="web-search-ai-trends")
    parser.add_argument("--runs", type=int, default=5, choices=range(2, 21))
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--max-p95-regression", type=float, default=0.20)
    parser.add_argument("--confirm-disruptive", action="store_true")
    args = parser.parse_args()

    if not args.confirm_disruptive:
        parser.error("--confirm-disruptive is required because this command redeploys the Hosted Agent twice")
    if not 0 <= args.min_pass_rate <= 1:
        parser.error("--min-pass-rate must be between 0 and 1")
    if args.max_p95_regression < 0:
        parser.error("--max-p95-regression must be non-negative")
    if _azd_value("AZURE_ENV_NAME") != args.environment:
        parser.error("--environment must match the currently selected azd environment")
    if _azd_value("TOOLBOX_ENABLED").lower() != "true":
        parser.error("TOOLBOX_ENABLED must be true before comparison so restoration has a known target")

    backend_url = _azd_optional_value("AGENT_SERVICE_DIRECT_URL") or _azd_value("AGENT_SERVICE_URL")
    run_evals.BACKEND_URL = backend_url.rstrip("/")
    _verify_deployed_scenario(args.use_case, args.scenario)

    restored = False
    try:
        _deploy(args.environment, False)
        local_runs = _measure("local", args.runs, args.use_case, args.scenario)
        _deploy(args.environment, True)
        restored = True
        toolbox_runs = _measure("Toolbox", args.runs, args.use_case, args.scenario)
    finally:
        if not restored:
            print("Restoring Toolbox after interrupted comparison...", file=sys.stderr)
            _deploy(args.environment, True)

    use_contract_gate = args.scenario == "web-search-ai-trends"
    local = summarize_runs("local", local_runs, "web_search" if use_contract_gate else None)
    toolbox = summarize_runs(
        "Toolbox",
        toolbox_runs,
        "kratos-tools-web_search" if use_contract_gate else None,
    )
    print()
    _print_metrics(local)
    _print_metrics(toolbox)
    failures = threshold_failures(local, toolbox, args.min_pass_rate, args.max_p95_regression)
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
