# CLAUDE.md

Read [AGENTS.md](AGENTS.md) first. It contains the binding public-repository,
single-ARE, safety, validation, and deployment agreements.

## Commands

```bash
# Backend
cd src/backend
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy app/ --ignore-missing-imports

# Frontend
cd src/frontend
npm run lint
npm run build

# Retained synthetic Microsoft 365 Graph MCP server
cd mocks
npm ci --userconfig <private-npmrc>
npm run build

# AKS packaging
helm lint deploy/helm/kratos-agent -f deploy/helm/kratos-agent/values-ci.yaml
helm template validation deploy/helm/kratos-agent --namespace kratos-agent \
  -f deploy/helm/kratos-agent/values-ci.yaml
az bicep build --file infra/main.bicep --stdout > /dev/null

# Deployed smoke tests
cd .copilot/skills/e2e-smoke
KRATOS_URL=https://... KRATOS_AUTH_COOKIE=... ./run.sh
```

Python and npm restoration must use the configured private feeds.

## Architecture

The FastAPI service owns the GitHub Copilot SDK loop and streams chat over SSE.
Microsoft Foundry hosts the model. Cosmos stores conversations, messages,
receipts, briefs, and runtime versions; Blob stores prompt and skill
configuration. The Next.js static frontend, FastAPI backend, optional Graph OBO
service, and OIDC auth proxy run on AKS through the Helm chart under
`deploy/helm/kratos-agent`.

There is one public Agent Reliability Engineer configuration:
`use-cases/agent-reliability-engineer`. The internal key is not a
caller-selectable API field.

Production boundaries:

- ServiceNow is the only mutable external system and every write requires an
  exact, expiring, single-use approval plus independent verification.
- Application Insights, Azure Activity, Kubernetes, and Microsoft Graph are
  read-only.
- Conversation-scoped routes enforce ownership from the authenticated
  principal.
- Kubernetes access is allowlisted, namespace-scoped, and GET-only.
- Runtime skill/prompt changes are admin-only, immutable, audited, and
  replica-refreshed.

`azure.yaml` provisions infrastructure only. Application deployment is a manual
Helm operation described in `deploy/aks/README.md`. Never add push-triggered
deployment.

## Conventions

- API JSON is camelCase; checked-in eval scenarios are snake_case.
- AKS images are digest-pinned, non-root, read-only, and use private-feed
  BuildKit secrets.
- Local mode uses SQLite, Azurite, a Copilot token, and the synthetic M365 mock.
- Do not edit `.azure/`; query selected environment values through `azd`.
- Keep exact-pin dependency pairs grouped in Dependabot.
