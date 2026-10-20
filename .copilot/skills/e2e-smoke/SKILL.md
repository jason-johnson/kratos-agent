# e2e-smoke — Playwright-driven live smoke tests for kratos-agent

A repo-local Playwright skill that exercises a deployed kratos-agent
environment end-to-end: frontend renders, backend health and readiness,
ARE scenarios load, chat round-trips, evals API, and traces API.

## When to use

- After a manual Helm install or upgrade to confirm AKS from a real client.
- Before opening a PR that touches the backend, frontend, chart, evals, or
  traces surface.
- After changing Foundry RBAC, to confirm the ARE can still reach the model.

## When NOT to use

- Local-only frontend dev work — `next dev` + targeted manual click is faster.
- Pure backend unit changes — prefer `uv run pytest` in `src/backend`.
- Static type / lint checks — those have their own runners.

## What it tests

| Spec | Surface | Asserts |
|------|---------|---------|
| `01-health.spec.ts` | API + ingress | `/health` 200, frontend HTML serves, runtime `config.json` uses the ingress origin |
| `02-scenarios.spec.ts` | `/api/evals/scenarios` | ARE returns at least one valid scenario |
| `03-chat.spec.ts` | `/api/agent/chat` SSE | Sends a tiny prompt, asserts a non-empty assistant response inside `CHAT_TIMEOUT_MS` |
| `04-evals.spec.ts` | `/api/evals/runs` | At least one run exists and its detail is reachable |
| `05-traces.spec.ts` | `/api/traces/operations` | ≥1 operation in lookback window; per-operation detail returns spans |
| `06-ui.spec.ts` | browser | Frontend loads without console errors (smoke gate) |
| `07-regression.spec.ts` | core API | Operational context, conversation CRUD, skills catalogue/detail, and system prompt |
| `08-ux.spec.ts` | browser, interactive | Chat, administration tabs, scenario generation modal, and traces refresh |

## Inputs (env vars)

| Var | Default | Purpose |
|-----|---------|---------|
| `KRATOS_URL` | none | AKS ingress origin used for frontend and backend |
| `KRATOS_FRONTEND_URL` | `KRATOS_URL` | Optional separate frontend origin |
| `KRATOS_BACKEND_URL` | `KRATOS_URL` | Optional separate backend origin |
| `KRATOS_AUTH_COOKIE` | unset | Protected auth-proxy Cookie header for API tests |
| `KRATOS_STORAGE_STATE` | unset | Protected Playwright storage-state path for browser tests |
| `PRIVATE_NPMRC` | none | Private-feed npmrc path, required only when dependencies are absent |
| `CHAT_TIMEOUT_MS` | `60000` | Chat round-trip ceiling |
| `TRACES_LOOKBACK_HOURS` | `6` | App Insights query window |
| `SKIP_BROWSER` | unset | Set to `1` to skip browser tests (CI / no-chromium hosts) |

**No deployment endpoints are hardcoded in this repo — it is public.** `run.sh`
requires an explicit target and fails fast instead of silently hitting a stale
environment.

## Run it

```bash
cd .copilot/skills/e2e-smoke
KRATOS_URL=https://... ./run.sh           # install (first run) + run all specs
./run.sh --grep "chat"                    # filter
KRATOS_BACKEND_URL=... ./run.sh           # override the azd-resolved target
SKIP_BROWSER=1 ./run.sh                   # API-only mode
```

The wrapper installs `npm` deps + Chromium on first run only (cached in
`node_modules/` + the playwright user dir). Results land in
`playwright-report/` and `test-results/` — both gitignored.

## Conventions

- TypeScript Playwright with `@playwright/test`.
- One spec = one concern; specs are independent (no shared fixture state) so
  Playwright's `fullyParallel: true` works.
- Assertions are content-aware: `/health` checks the actual JSON shape, not
  just status code. Chat assertion requires non-empty body, not just 200.
- Browser tests are gated on `SKIP_BROWSER` for CI on hosts without Chromium.
- Browser runs require an authenticated OIDC session. Use a protected
  Playwright storage state or complete login interactively; never commit it.

## Known limitations

- Foundry agent cold-start can spike `/api/agent/chat` above 60s on the
  very first call after 15 min idle. The spec already retries once with
  a 30 s warmup ping; bump `CHAT_TIMEOUT_MS` if your environment is colder.
- Traces lookback default is 2h to match the typical pilot session; the
  test gracefully skips assertions if zero operations exist in that
  window (so it doesn't false-fail before any real chat has happened).
