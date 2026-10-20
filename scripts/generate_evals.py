#!/usr/bin/env python3
"""Generate ARE eval scenarios via the running backend.

Usage:
    # Local backend (default http://localhost:8000):
    python scripts/generate_evals.py --count 5

    # Deployed backend:
    BACKEND_URL=https://kratos-be.example.com python scripts/generate_evals.py --count 10

    # Save generated scenarios into the repo (not just echo to stdout):
    python scripts/generate_evals.py --save

Requires the backend's ``EvalService`` to be reachable. Auth: set
ADMIN_TOKEN (passed as bearer) only if ``ADMIN_AUTH_ENABLED=true`` on the
backend; otherwise endpoints are open.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
USE_CASE = "agent-reliability-engineer"
USE_CASE_DIR = REPO_ROOT / "use-cases" / USE_CASE
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")


def _headers() -> dict[str, str]:
    h = {"content-type": "application/json"}
    if ADMIN_TOKEN:
        h["authorization"] = f"Bearer {ADMIN_TOKEN}"
    return h


def generate(count: int, instructions: str) -> list[dict[str, Any]]:
    url = f"{BACKEND_URL}/api/evals/scenarios/generate"
    payload = {"count": count, "instructions": instructions}
    with httpx.Client(timeout=300.0) as client:
        r = client.post(url, json=payload, headers=_headers())
    r.raise_for_status()
    return r.json().get("scenarios", [])


def save_scenarios(scenarios: list[dict[str, Any]]) -> None:
    target_dir = USE_CASE_DIR / "evals" / "scenarios"
    target_dir.mkdir(parents=True, exist_ok=True)
    for s in scenarios:
        name = s.get("name", "").strip()
        if not name:
            print(f"  ! skipping scenario without 'name': {s!r}", file=sys.stderr)
            continue
        path = target_dir / f"{name}.json"
        path.write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
        print(f"  ✔ saved {path.relative_to(REPO_ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=5, help="Number of scenarios (default 5)")
    parser.add_argument("--instructions", default="", help="Extra LLM hints")
    parser.add_argument("--save", action="store_true", help=f"Persist into use-cases/{USE_CASE}/evals/scenarios/")
    args = parser.parse_args()

    print(f"→ Generating {args.count} ARE scenarios…")
    try:
        scenarios = generate(args.count, args.instructions)
    except httpx.HTTPStatusError as e:
        print(f"  ✗ HTTP {e.response.status_code}: {e.response.text[:400]}", file=sys.stderr)
        return 1
    except httpx.HTTPError as e:
        print(f"  ✗ {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"  Got {len(scenarios)} scenarios.")
    if args.save:
        save_scenarios(scenarios)
    else:
        print(json.dumps(scenarios, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
