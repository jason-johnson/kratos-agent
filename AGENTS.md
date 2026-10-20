# AGENTS.md — working agreements

## This repository is public

Never commit deployment endpoints, subscription/tenant/resource/principal/client
IDs, credentials, connection strings, private feed URLs, customer data, real
personal data, `.env` files, or browser/test artifacts. Synthetic fixtures only.

Infrastructure templates may define parameterized resources and public Azure
built-in role IDs, but must not contain environment values.

Before committing, scan added content:

```bash
git diff --cached -U0 | grep '^+' | grep -nEi \
  'azurestaticapps\.net|azurecontainerapps\.io|azure-api\.net|cognitiveservices\.azure\.com|search\.windows\.net|vault\.azure\.net|documents\.azure\.com|InstrumentationKey|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
```

Treat unexplained matches as blocking. Never commit private-feed credentials or
URLs; Python and npm restore must use feeds configured outside the repository.

## Supported product and architecture

There is one public Agent Reliability Engineer and one checked-in configuration:
`use-cases/agent-reliability-engineer`. Do not add persona discovery, selection,
import/export, caller-selected `useCase`, or alternate application runtimes.

| Path | Purpose |
|---|---|
| `src/backend` | FastAPI API and in-process GitHub Copilot SDK loop |
| `src/frontend` | Next.js static frontend |
| `src/obo-mcp-server` | Optional delegated Microsoft Graph MCP service |
| `use-cases/agent-reliability-engineer` | ARE prompt, skills, MCP config, evals |
| `mocks/packages/m365-graph-mcp-server` | Synthetic local Graph tools |
| `infra` | AKS and retained managed Azure services |
| `deploy/helm/kratos-agent` | Hardened application deployment |
| `deploy/aks` | Manual operator runbook and utilities |

AKS is the only supported application runtime. `azure.yaml` provisions
infrastructure only; Helm deployment, image publication, DNS, certificates, and
live acceptance are explicit operator actions. CI must never deploy.

Keep immutable image digests, Workload Identity, OIDC ingress authentication,
default-deny network policy, namespace-only Kubernetes inspection RBAC,
non-root/read-only containers, probes, HPA/PDB, and local accounts disabled.

## Reliability and authorization boundaries

- ServiceNow is the only external system that may mutate. Writes are fixed,
  exact, conversation-bound, expiring, single-use, idempotent, audited, and
  independently re-read for verification.
- Application Insights, Azure Activity, Kubernetes, and Microsoft Graph are
  read-only.
- Conversation APIs derive ownership from the authenticated principal and do
  not reveal another user's resources.
- Runtime prompt/skill changes are admin-only, versioned, audited, and refreshed
  across replicas.
- Preserve uncertainty, stale-data, partial-result, and coverage-gap reporting.
- Do not add generic HTTP, KQL, Graph, or Kubernetes execution surfaces.

## Validation

Run the smallest check covering a change. At phase or release boundaries run:

```bash
cd src/backend
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy app/ --ignore-missing-imports

cd ../frontend
npm run lint
npm run build

cd ../../mocks
npm ci --userconfig <private-npmrc>
npm run build

cd ..
az bicep build --file infra/main.bicep --stdout > /dev/null
helm lint deploy/helm/kratos-agent -f deploy/helm/kratos-agent/values-ci.yaml
helm template validation deploy/helm/kratos-agent --namespace kratos-agent \
  -f deploy/helm/kratos-agent/values-ci.yaml
```

Deployed smoke tests require an approved target and protected auth material:

```bash
cd .copilot/skills/e2e-smoke
KRATOS_URL=https://... KRATOS_AUTH_COOKIE=... ./run.sh
```

Never use `|| true` on a release gate. Do not hand-edit `.azure/`; query it
through `azd`. Do not commit rendered manifests, Helm private values, Kubernetes
Secrets, image/test artifacts, or captured production data.
