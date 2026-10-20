# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read AGENTS.md first

[AGENTS.md](AGENTS.md) holds the binding working agreements: **this repo is public** (never commit
endpoints, subscription/tenant/object IDs, keys, customer data, or Playwright artifacts), plus
hard-won deployment gotchas. It is not duplicated here — read it before committing or deploying.

## Commands

```bash
# Backend (Python 3.11, uv)
cd src/backend
uv run pytest                          # all tests
uv run pytest tests/test_skill_registry.py::test_name -v   # a single test
uv run ruff check . && uv run ruff format --check .
uv run mypy app/ --ignore-missing-imports

# Frontend (Next.js 15, static export)
cd src/frontend
npm run lint && npm run build          # `build` == `export`; output lands in out/
npm run dev                            # localhost:3000

# Mocks (TypeScript stdio MCP servers)
cd mocks && npm install && npm run build

# Full local stack — no Azure needed (SQLite + Azurite + a Copilot token)
cp .env.local.example .env.local       # set COPILOT_GITHUB_TOKEN=ghu_...
./run-local.sh                         # or .\run-local.ps1

# Deployed environment, end to end (after any azd deploy) — 21 specs, ~55s
cd .copilot/skills/e2e-smoke && ./run.sh
SKIP_BROWSER=1 ./run.sh                # API-only, skips the Chromium download

# Evals / traces CLI
python scripts/run_evals.py --use-case agent-reliability-engineer --mode validation
python scripts/generate_evals.py --use-case agent-reliability-engineer --count 5 --save
python scripts/fetch_traces.py --conversation-id abc123
```

`azd` commands follow the **globally selected** environment. Confirm with
`azd env get-value AZURE_ENV_NAME`, or name the target per command (`azd deploy -e <env>`)
before deploying or destroying anything.

## Architecture

### In-process agent runtime

The FastAPI service owns the API and the Copilot SDK agent loop:

| Layer | Entry point | Runtime |
|---|---|---|
| **Agent/API** | [src/backend/app/main.py](src/backend/app/main.py) — FastAPI, port 8000 | Azure Container Apps |
| **Model host** | Microsoft Foundry model deployment | Azure AI Services |

`POST /api/agent/chat` invokes [copilot_agent.py](src/backend/app/services/copilot_agent.py)
in-process and streams SSE events back. The same service owns conversation persistence, file
serving, versioned skills administration, evals, and traces. The API managed identity calls the Foundry
model deployment directly.

**Session continuity:** the service stores the Copilot SDK session mapping in Cosmos (`sessions`,
partitioned by `conversationId`) so multi-turn state can resume across API replicas. Active turns
support explicit cancellation and SDK user-input requests.

### One ARE, N skills

The public product has one Agent Reliability Engineer configuration under
[use-cases/agent-reliability-engineer/](use-cases/agent-reliability-engineer/). Its
`SYSTEM_PROMPT.md`, `skills/*/SKILL.md`, `.mcp.json`, `apm.yml`, and `evals/` are fixed for every
conversation. `agent-reliability-engineer` remains an internal Blob/Cosmos key, not a caller-selectable
API field.

Skills load in priority order (see [skill_registry.py](src/backend/app/services/skill_registry.py)):
blob storage → local filesystem → APM packages, with local/blob winning name conflicts. Authorized
runtime changes go through `/api/admin/skills`, create immutable versions, and refresh replicas.

### Local mode

`LOCAL_MODE` auto-activates whenever `COSMOS_DB_ENDPOINT` is empty (`Settings.is_local_mode()`).
SQLite replaces Cosmos, Azurite replaces Blob, and a GitHub Copilot token replaces
Foundry/Managed Identity. Same code path in both worlds — don't branch on environment beyond
the existing `is_local_mode()` checks.

### Mocks

[mocks/packages/](mocks/packages/) contains synthetic development servers. The active backend image
installs only the ServiceNow and M365 Graph MCP packages retained by the ARE.

## Conventions

- **API JSON is camelCase** (Pydantic alias generator): `{message, conversationId}`.
  The exception is the on-disk eval scenario format, which is snake_case
  (`input_message`, `expected_behavior`, `expected_tool_calls`) so it stays readable in git.
- Python: `ruff` (lint + format, line length 120) and `mypy` are both gating in CI. Per-module
  mypy relaxations are documented in `pyproject.toml` — extend that list rather than sprinkling
  `# type: ignore`.
- Container images build in ACR (`remoteBuild: true`), never locally. Don't "simplify" this back.
- Every azd hook carries both a `posix:` and a `windows:` variant (`hooks/*.sh` + `hooks/*.ps1`),
  and `*.sh` is pinned to LF in `.gitattributes`. Both are load-bearing on Windows — see the
  hook rules in [AGENTS.md](AGENTS.md). Test either side with
  `azd hooks run <name> --platform windows|posix`.
- Optional services need a `condition:` in `azure.yaml` kept in sync with
  `infra/main.parameters.json` (see `obo-mcp-server` / `DEPLOY_OBO`).
- Packages that pin each other exactly (`pydantic`/`pydantic-core`, `react`/`react-dom`) must stay
  grouped in `.github/dependabot.yml`.
- Don't hand-edit anything under `.azure/`; prefer `azd env get-values` over reading those files.
