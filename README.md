# Agent Reliability Engineer

An evidence-driven incident-command agent built with the GitHub Copilot SDK.

The project is being reduced from a multi-persona accelerator into one focused
Agent Reliability Engineer (ARE). The ARE helps an incident commander:

1. investigate suspected causes with attributed technical evidence;
2. maintain an evidence-backed incident command brief; and
3. draft and post explicitly approved incident updates.

Technical systems are read-only. In v1, the only allowed mutations are
single-use, explicitly approved ServiceNow updates.

See [PLAN.md](PLAN.md) for the migration decisions, phase status, and manual AKS
acceptance gates.

## Current architecture

The application currently runs the API and agent loop in one FastAPI service.
The model remains hosted in Microsoft Foundry.

```text
Next.js UI
    |
    | HTTP + SSE
    v
FastAPI agent/API
    |-- GitHub Copilot SDK agent loop
    |-- ARE system prompt and skills
    |-- conversation, eval, trace, file, and runtime-config APIs
    |-- read-only Application Insights, Azure Activity, and Kubernetes inspection
    |-- guarded production ServiceNow client
    |-- persisted incident command briefs
    |
    +--> Microsoft Foundry model deployment
    +--> Cosmos DB
    +--> Blob Storage
    +--> Application Insights / Log Analytics
    +--> ServiceNow
    +--> delegated Microsoft Graph OBO MCP service
```

The currently deployed path remains Azure Container Apps plus Azure Static Web
Apps. The AKS images, Helm chart, Workload Identity/RBAC infrastructure,
ingress, policies, monitoring, and runbook are implemented but have not been
deployed. Live acceptance requires an explicitly approved non-production target.

## Product model

There is one public agent identity: **Agent Reliability Engineer**.

- The frontend has no persona selector.
- Chat, conversation, eval, trace, and administration APIs accept no
  caller-selected use case.
- `agent-reliability-engineer` remains an internal storage/configuration key.
- Runtime skill and prompt changes are versioned, audited, and admin-only.
- Persona import/export and hosted-agent packaging have been removed.

The active configuration is under
[`use-cases/agent-reliability-engineer/`](use-cases/agent-reliability-engineer/):

| Path | Purpose |
|---|---|
| `SYSTEM_PROMPT.md` | ARE role, evidence rules, approval boundaries, and response contract |
| `skills/` | Incident investigation, command-brief, and communication workflows |
| `.mcp.json` | Synthetic local Microsoft Graph tool configuration |
| `evals/` | ARE behavior and safety scenarios |

## Safety and operational UX

The UI displays the active environment and configured evidence target before
the user starts a conversation and throughout a chat.

Production is visually distinct. The UI also states:

- technical evidence access is read-only;
- writes are limited to approved ServiceNow updates;
- each approval applies only to the exact displayed action;
- execution completion is not presented as verification; and
- the agent must provide a receipt and report verification as verified,
  unverified, or failed.

Approval responses are sent to the active Copilot SDK user-input request. They
are not converted into reusable chat instructions.

All conversation-scoped APIs enforce the authenticated principal's ownership.
ServiceNow writes are conversation-bound, exact, expiring, and single-use.

## Repository layout

| Path | Purpose |
|---|---|
| [`src/backend/`](src/backend/) | FastAPI API and in-process Copilot SDK runtime |
| [`src/frontend/`](src/frontend/) | Next.js user interface |
| [`src/obo-mcp-server/`](src/obo-mcp-server/) | Optional Microsoft Graph On-Behalf-Of MCP service |
| [`use-cases/agent-reliability-engineer/`](use-cases/agent-reliability-engineer/) | ARE prompt, skills, MCP config, and evals |
| [`mocks/`](mocks/) | Synthetic development servers; ServiceNow is not on the active production path |
| [`infra/`](infra/) | Parameterized Azure infrastructure, including optional AKS |
| [`deploy/helm/kratos-agent/`](deploy/helm/kratos-agent/) | Hardened AKS Helm chart |
| [`deploy/aks/`](deploy/aks/) | Manual AKS build, deploy, acceptance, and rollback runbook |
| [`scripts/`](scripts/) | Eval and trace utilities |

## Local development

### Prerequisites

- Python 3.11+
- `uv`
- Node.js 20+
- Docker, for the local integrated stack
- a GitHub Copilot token

This project must restore Python and npm packages through the configured private
feeds. Configure and authenticate those feeds before running install or sync
commands. Do not add private feed URLs or credentials to the repository.

### Integrated local stack

```bash
cp .env.local.example .env.local
# Set COPILOT_GITHUB_TOKEN and any optional OBO values in .env.local.

./run-local.sh
# Windows: .\run-local.ps1
```

Local mode uses SQLite and Azurite. It does not require Azure Cosmos DB or Blob
Storage.

### Backend

```bash
cd src/backend
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
```

### Frontend

```bash
cd src/frontend
npm run lint
npm run build
npm run dev
```

### Retained MCP mocks

The local ARE configuration retains the synthetic Microsoft Graph package.
ServiceNow production behavior uses the fixed Python client; its mock remains
only as a repository fixture pending Phase 6 cleanup.

```bash
cd mocks/packages/m365-graph-mcp-server
npm run build
```

## Main API surfaces

| Surface | Purpose |
|---|---|
| `POST /api/agent/chat` | Stream an ARE turn over SSE |
| `POST /api/agent/user-input` | Resolve the active scoped SDK input/approval request |
| `POST /api/agent/cancel` | Cancel an active turn |
| `/api/conversations` | Conversation persistence |
| `GET /api/context` | Safe environment, target, and action-boundary context |
| `/api/admin/skills` | Versioned ARE runtime configuration |
| `/api/evals` | ARE scenarios and runs |
| `/api/traces` | Trace exploration |

API JSON uses camelCase. On-disk eval scenarios use snake_case.

## Deployment status

Deployment is manual. Do not attach deployment to push-based CI.

The current `azure.yaml` still deploys:

- the FastAPI agent/API to Azure Container Apps;
- the optional OBO MCP server to Azure Container Apps; and
- the static frontend to Azure Static Web Apps.

Container images use ACR remote builds. Do not switch them back to local builds.

The packaged target architecture uses AKS workloads in one namespace:

1. frontend; and
2. agent/API;
3. optional OBO service, isolated behind its own identity and policy boundary;
4. OIDC authentication proxy.

Provisioning is disabled by default (`DEPLOY_AKS=false`) and no CI job deploys.
Follow the [AKS runbook](deploy/aks/README.md) only with an explicitly approved
target and private values.

## Validation policy

Run the smallest check covering a change, then use the full matrix at phase
boundaries:

- backend tests, Ruff lint, and Ruff format;
- frontend lint and production build;
- retained MCP package builds;
- Bicep compilation and hook checks while Container Apps infrastructure exists;
- Docker Compose configuration;
- diff whitespace and public-repository sensitive-data scans.

Deployed smoke tests are run only after an explicitly approved deployment.

## Contributing

Read [AGENTS.md](AGENTS.md) before changing or deploying the project. This is a
public repository: never commit deployment endpoints, IDs, credentials,
customer data, private feed URLs, or captured browser/test artifacts.
