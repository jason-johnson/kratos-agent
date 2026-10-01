#!/usr/bin/env python3
"""Compare direct and Toolbox Microsoft Learn MCP routes with rollback."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import replace
from typing import Any

import compare_toolbox_evals as compare
import run_evals

SCENARIO = "microsoft-learn-mcp-authentication"
DIRECT_TOOLS = frozenset(
    {
        "microsoft-learn-microsoft_docs_search",
        "microsoft-learn-microsoft_docs_fetch",
    }
)
TOOLBOX_TOOLS = frozenset(
    {
        "kratos-tools-microsoft_docs_search",
        "kratos-tools-microsoft_docs_fetch",
    }
)


def learn_route_failures(
    run: dict[str, Any],
    required_tools: frozenset[str],
    forbidden_tools: frozenset[str],
) -> list[str]:
    results = run.get("results") or []
    if run.get("status") != "completed" or len(results) != 1 or results[0].get("status") != "completed":
        return ["run did not contain one completed scenario result"]

    observed = compare._tool_names(run)  # noqa: SLF001
    tool_counts: dict[str, int] = {}
    for call in results[0].get("tool_calls") or []:
        if call.get("status") == "completed" and call.get("skillName") != "skill":
            name = str(call.get("skillName"))
            tool_counts[name] = tool_counts.get(name, 0) + 1
    failures = [
        f"invoked {tool} {tool_counts.get(tool, 0)} times; expected exactly once"
        for tool in sorted(required_tools)
        if tool_counts.get(tool, 0) != 1
    ]
    failures.extend(f"unexpectedly invoked {tool}" for tool in sorted(forbidden_tools & observed))
    scores = results[0].get("scores") or {}
    if not scores:
        failures.append("run did not contain evaluator scores")
    else:
        failures.extend(
            f"{name} did not pass"
            for name, score in scores.items()
            if not compare._score_passed(score)  # noqa: SLF001
        )
    return failures


def summarize_learn_runs(
    label: str,
    runs: list[dict[str, Any]],
    required_tools: frozenset[str],
    forbidden_tools: frozenset[str],
) -> compare.RunMetrics:
    metrics = compare.summarize_runs(label, runs)
    passed = sum(not learn_route_failures(run, required_tools, forbidden_tools) for run in runs)
    return replace(metrics, passed=passed)


def threshold_failures(
    direct: compare.RunMetrics,
    toolbox: compare.RunMetrics,
    min_pass_rate: float,
    max_p95_regression: float,
) -> list[str]:
    failures: list[str] = []
    if direct.pass_rate < min_pass_rate:
        failures.append(f"direct MCP pass rate {direct.pass_rate:.0%} is below {min_pass_rate:.0%}")
    if toolbox.pass_rate < min_pass_rate:
        failures.append(f"Toolbox pass rate {toolbox.pass_rate:.0%} is below {min_pass_rate:.0%}")
    allowed_p95 = direct.p95_ms * (1 + max_p95_regression)
    if toolbox.p95_ms > allowed_p95:
        failures.append(
            f"Toolbox p95 {toolbox.p95_ms / 1000:.1f}s exceeds "
            f"direct MCP p95 {direct.p95_ms / 1000:.1f}s by more than {max_p95_regression:.0%}"
        )
    return failures


def _verify_deployment(environment: str, enabled: bool, version: str) -> None:
    enabled_value = str(enabled).lower()
    definition = json.loads(
        compare._command("azd", "ai", "agent", "show", "-e", environment, "--output", "json")  # noqa: SLF001
    )
    variables = definition.get("definition", {}).get("environment_variables", {})
    actual_enabled = str(variables.get("TOOLBOX_LEARN_ENABLED", "")).lower()
    actual_version = str(variables.get("TOOLBOX_LEARN_VERSION", ""))
    if definition.get("status") != "active" or actual_enabled != enabled_value or actual_version != version:
        raise RuntimeError(
            "Hosted Agent did not become active with the requested Microsoft Learn Toolbox configuration"
        )


def _deploy(environment: str, enabled: bool, version: str) -> None:
    enabled_value = str(enabled).lower()
    subprocess.run(["azd", "env", "set", "TOOLBOX_LEARN_ENABLED", enabled_value], check=True)  # noqa: S603
    subprocess.run(["azd", "env", "set", "TOOLBOX_LEARN_VERSION", version], check=True)  # noqa: S603
    subprocess.run(["azd", "deploy", "kratos-agent", "--no-prompt"], check=True)  # noqa: S603
    _verify_deployment(environment, enabled, version)


def _measure(label: str, count: int, drain_runs: int, use_case: str) -> list[dict[str, Any]]:
    for index in range(1, drain_runs + 1):
        print(f"Draining warm pool for {label} {index}/{drain_runs}...")
        compare._evaluate(use_case, SCENARIO)  # noqa: SLF001
    measured = []
    for index in range(1, count + 1):
        print(f"Measuring {label} {index}/{count}...")
        measured.append(compare._evaluate(use_case, SCENARIO))  # noqa: SLF001
    return measured


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, help="Selected azd environment; must match AZURE_ENV_NAME")
    parser.add_argument("--toolbox-version", required=True, help="Immutable numeric Toolbox version containing Learn MCP")
    parser.add_argument("--use-case", default="generic")
    parser.add_argument("--runs", type=int, default=5, choices=range(2, 21))
    parser.add_argument("--drain-runs", type=int, default=2, choices=range(1, 6))
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--max-p95-regression", type=float, default=0.20)
    parser.add_argument("--confirm-disruptive", action="store_true")
    args = parser.parse_args()

    if not args.confirm_disruptive:
        parser.error("--confirm-disruptive is required because this command redeploys the Hosted Agent twice")
    if not args.toolbox_version.isdigit() or int(args.toolbox_version) < 1:
        parser.error("--toolbox-version must be a positive numeric immutable version")
    if not 0 <= args.min_pass_rate <= 1:
        parser.error("--min-pass-rate must be between 0 and 1")
    if args.max_p95_regression < 0:
        parser.error("--max-p95-regression must be non-negative")
    if compare._azd_value("AZURE_ENV_NAME") != args.environment:  # noqa: SLF001
        parser.error("--environment must match the currently selected azd environment")
    if compare._azd_value("TOOLBOX_ENABLED").lower() != "true":  # noqa: SLF001
        parser.error("TOOLBOX_ENABLED must remain true so the promoted Web Search route is preserved")
    if compare._azd_value("TOOLBOX_LEARN_ENABLED").lower() != "false":  # noqa: SLF001
        parser.error("TOOLBOX_LEARN_ENABLED must be false before comparison")
    if compare._azd_value("TOOLBOX_LEARN_VERSION") != "0":  # noqa: SLF001
        parser.error("TOOLBOX_LEARN_VERSION must be 0 before comparison")

    backend_url = compare._azd_optional_value("AGENT_SERVICE_DIRECT_URL") or compare._azd_value(  # noqa: SLF001
        "AGENT_SERVICE_URL"
    )
    run_evals.BACKEND_URL = backend_url.rstrip("/")
    compare._verify_deployed_scenario(args.use_case, SCENARIO)  # noqa: SLF001
    _verify_deployment(args.environment, False, "0")

    direct_runs = _measure("direct MCP", args.runs, args.drain_runs, args.use_case)
    try:
        _deploy(args.environment, True, args.toolbox_version)
        toolbox_runs = _measure("Toolbox", args.runs, args.drain_runs, args.use_case)
    finally:
        print("Restoring direct Microsoft Learn MCP route...", file=sys.stderr)
        _deploy(args.environment, False, "0")

    direct = summarize_learn_runs("direct", direct_runs, DIRECT_TOOLS, TOOLBOX_TOOLS)
    toolbox = summarize_learn_runs("Toolbox", toolbox_runs, TOOLBOX_TOOLS, DIRECT_TOOLS)
    print()
    compare._print_metrics(direct)  # noqa: SLF001
    compare._print_metrics(toolbox)  # noqa: SLF001
    failures = threshold_failures(direct, toolbox, args.min_pass_rate, args.max_p95_regression)
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
