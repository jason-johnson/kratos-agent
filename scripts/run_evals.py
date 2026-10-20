#!/usr/bin/env python3
"""Run ARE evals against the running backend.

Usage:
    # Validation mode (in-process invoke + score, fast):
    python scripts/run_evals.py --mode validation

    # Foundry mode (cloud eval, slower):
    python scripts/run_evals.py --mode foundry

    # Run only specific scenarios:
    python scripts/run_evals.py --scenarios bounded-app-insights-investigation,kubernetes-read-only-inspection

Polls until the run completes and prints a summary table.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import httpx

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")
POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 60 * 30


def _headers() -> dict[str, str]:
    h = {"content-type": "application/json"}
    if ADMIN_TOKEN:
        h["authorization"] = f"Bearer {ADMIN_TOKEN}"
    return h


def start_run(mode: str, scenario_names: list[str] | None) -> dict[str, Any]:
    url = f"{BACKEND_URL}/api/evals/run"
    payload: dict[str, Any] = {"mode": mode}
    if scenario_names:
        payload["scenario_names"] = scenario_names
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, json=payload, headers=_headers())
    r.raise_for_status()
    return r.json()


def get_run(run_id: str) -> dict[str, Any]:
    url = f"{BACKEND_URL}/api/evals/runs/{run_id}"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers=_headers())
    r.raise_for_status()
    return r.json()


def poll(run_id: str) -> dict[str, Any]:
    deadline = time.time() + POLL_TIMEOUT_S
    last_status = ""
    while time.time() < deadline:
        run = get_run(run_id)
        status = run.get("status", "")
        if status != last_status:
            print(f"  status={status}")
            last_status = status
        if status in ("completed", "failed", "cancelled"):
            return run
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(f"Run {run_id} did not finish within {POLL_TIMEOUT_S}s")


def print_summary(run: dict[str, Any]) -> None:
    print()
    print(f"Run:      {run.get('run_id')}")
    print(f"Agent:    {run.get('use_case')}")
    print(f"Mode:     {run.get('mode')}")
    print(f"Status:   {run.get('status')}")

    foundry = run.get("foundry") or {}
    if foundry.get("report_url"):
        print(f"Foundry:  {foundry['report_url']}")

    results = run.get("results", []) or []
    if not results:
        print("(no per-scenario results)")
        return

    print()
    print(f"{'scenario':<40} {'status':<10} avg")
    print("-" * 65)
    for r in results:
        scores = r.get("scores") or {}
        numeric = [v for v in scores.values() if isinstance(v, (int, float))]
        avg = (sum(numeric) / len(numeric)) if numeric else None
        avg_str = f"{avg:.2f}" if avg is not None else "n/a"
        print(f"{r.get('scenario_name', ''):<40} {r.get('status', ''):<10} {avg_str}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["validation", "foundry"], default="validation")
    p.add_argument("--scenarios", help="Comma-separated scenario names. Default: all.")
    args = p.parse_args()

    scenario_names = [s.strip() for s in args.scenarios.split(",")] if args.scenarios else None

    print(f"→ Starting ARE {args.mode} run…")
    try:
        run = start_run(args.mode, scenario_names)
    except httpx.HTTPStatusError as e:
        print(f"✗ HTTP {e.response.status_code}: {e.response.text[:400]}", file=sys.stderr)
        return 1

    run_id = run.get("run_id")
    if not run_id:
        print(f"✗ No run_id in response: {run}", file=sys.stderr)
        return 1
    print(f"  run_id={run_id}")

    try:
        final = poll(run_id)
    except TimeoutError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    print_summary(final)
    return 0 if final.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
