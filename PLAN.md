# Kratos AKS Simplification Plan

Last updated: 2026-10-15

## Purpose

Turn Kratos from a broad multi-persona Azure reference architecture into a focused
Agent Reliability Engineer (ARE) application that runs on Azure Kubernetes Service
(AKS), while retaining the GitHub Copilot SDK and the skill-based extension model.

This is a living document. Decisions should be recorded here before implementation,
and progress should be updated as work lands.

## Confirmed goals

- Deploy and operate the application on AKS.
- Keep the GitHub Copilot SDK as the agent runtime.
- Keep reusable skills as the primary extension mechanism.
- Replace the persona catalog with one focused personality/use-case.
- Start from the current IT service desk use-case, but evolve it into an Agent
  Reliability Engineer rather than an L1 support bot.
- Reuse useful skills from other use-cases where they support reliability work.
- Reduce the number of services, Azure resources, features, and operational paths.

## Working terminology

The project currently calls a personality a **use-case**. A use-case consists of:

- `SYSTEM_PROMPT.md`: identity, behavior, workflow, and sample questions.
- `skills/*/SKILL.md`: task-specific instructions loaded by the Copilot SDK.
- `.mcp.json`: MCP servers and tools available to the use-case.
- `apm.yml`: optional APM package dependencies.

The product name is **Agent Reliability Engineer** and the single use-case
identifier is `agent-reliability-engineer`.

## Current-state findings

### Runtime

- The FastAPI backend owns the user-facing API, persistence, administration,
  evaluations, traces, and file serving.
- Chat is proxied from the backend to a separate Microsoft Foundry hosted-agent
  runtime.
- The hosted-agent runtime runs the Copilot SDK and loads a selected use-case.
- Session pinning and a warm-sandbox pool exist mainly because of the split Foundry
  hosted-agent architecture.
- Local development adds Azurite, a hosted-agent container, a backend container, an
  optional OBO MCP server, and the frontend.

### Persona and skills

- There are currently eleven use-cases and ninety-six checked-in skills.
- `it-service-desk` is the closest starting point. It has ServiceNow, Workday, and
  M365 MCP integrations plus queue, triage, policy, ticket-action, VIP, handover,
  and file-sharing skills.
- The current service-desk prompt is demo-specific: it hardcodes a fictional user,
  company, date, queue, IDs, and simulated data.
- Useful generic skills include code interpretation, data analysis, document
  summarization, email drafting, file sharing, RAG search, and web search.
- The current frontend and API support persona discovery, selection, filtering,
  import, and export. Those surfaces become unnecessary with one use-case.

### Infrastructure

- The existing Bicep provisions a VNet, Container Apps environment, backend
  Container App, Foundry resources, Foundry hosted agent, Static Web App, Cosmos
  DB, Blob Storage, Key Vault, ACR, Application Insights, Log Analytics, Bing
  Search, optional OBO infrastructure, and optional Azure health-model resources.
- AKS should replace the compute topology, but not every existing managed service
  needs an AKS-hosted equivalent.

## Recommended target architecture

This is the lean default to validate together.

### Application shape

1. **One use-case:** `agent-reliability-engineer`.
2. **One agent runtime:** run `CopilotAgent` in the FastAPI process instead of
   proxying chat to a separate Foundry hosted-agent service.
3. **One agent/API workload:** package the API, Copilot SDK runtime, selected
   skills, and required MCP clients/servers in one image initially.
4. **One web experience:** keep the current chat UI largely unchanged. Remove or
   hide only controls that have no meaning with one use-case, such as persona
   selection and filtering. Preserve existing chat, conversation, settings,
   skills, traces, and evaluation experiences until each is separately retired.
5. **Managed model endpoint:** continue using an Azure-hosted model with AKS
   Workload Identity; remove the Foundry hosted-agent Invocations dependency.
6. **AKS-hosted frontend:** containerize the existing static Next.js export and
   route the UI and API through the cluster ingress/Gateway.
7. **Blob-backed live skills:** retain Blob Storage and the existing skills admin
   experience so authorized users can update skills without rebuilding the image.
   Keep checked-in ARE skills as the seed and recovery baseline.
8. **Foundry external-agent registration:** register the AKS-hosted runtime for
   Foundry observability and evaluation, while AKS remains responsible for
   invocation, scaling, identity, networking, and availability.

This removes the backend-to-hosted-agent proxy, hosted-agent session mapping,
warm-pool maintenance, persona lazy loading, and multi-persona routing. Blob
seeding and synchronization remain, but only for the single ARE use-case.

### Initial AKS topology

- One namespace for the application.
- One Kubernetes service account using Azure Workload Identity.
- One frontend Deployment and ClusterIP Service.
- One agent/API Deployment and ClusterIP Service. The FastAPI process runs the
  Copilot SDK directly; this is not the current backend/hosted-agent split.
- One separately secured OBO MCP Deployment and ClusterIP Service for delegated
  Microsoft Graph access.
- One ingress or Gateway API route with TLS.
- ConfigMap for non-secret configuration.
- Key Vault CSI or workload-identity-based secret retrieval for secrets.
- Startup, readiness, and liveness probes.
- Resource requests/limits, a PodDisruptionBudget, and an HPA after load behavior
  is measured.
- NetworkPolicies restricting ingress and egress to required systems.
- OpenTelemetry export to the selected observability backend.

The first production design should avoid putting state on pod-local filesystems.

## Agent Reliability Engineer product definition

### Mission

Help engineers understand, mitigate, and prevent reliability incidents across
applications and platforms. The agent should gather evidence, correlate signals,
form testable hypotheses, recommend safe actions, and verify outcomes. It should
not behave as a generic support chatbot or claim certainty without evidence.

### Primary user

The first release is for an **incident commander coordinating investigations and
communications**. It should optimize the command function rather than act as an
autonomous operator:

- Maintain a shared, timestamped incident picture.
- Gather and normalize evidence from technical and incident-management systems.
- Track impact, hypotheses, decisions, owners, actions, and unresolved questions.
- Detect contradictions, missing evidence, stale updates, and unowned actions.
- Produce role-appropriate updates for responders, leadership, support, and
  affected users.
- Recommend investigation and mitigation steps, while leaving execution with the
  responsible human or specialist system in v1.

Deep cluster administration remains useful context, but is not the product's
primary workflow.

### Top three v1 workflows

1. **Investigate suspected causes** using bounded, read-only Application Insights
   logs and traces, with supporting evidence, counter-evidence, unknowns, and
   query provenance.
2. **Maintain a live evidence-backed incident command brief** containing impact,
   timeline, hypotheses, decisions, owners, actions, unresolved questions, and
   stale or contradictory information.
3. **Draft and post approved incident updates** to ServiceNow and the retained
   Microsoft 365 channels. The agent must show exact wording, audience,
   visibility, and target before each write.

These workflows define v1 scope and acceptance. Responder coordination, handover
PDFs, and post-incident reviews may support them but must not delay the three
release-defining workflows.

### Incident system of record

ServiceNow is the first source of truth for incident state, ownership, and
updates. The v1 agent should treat ServiceNow records as authoritative for the
declared incident status while still identifying discrepancies with technical
evidence.

The current mock ServiceNow integration is useful as a behavioral prototype, not
as a production connector. Its replacement must define:

- Authentication and user authorization.
- Major-incident record types and state model.
- Assignment groups, roles, owners, and action tracking.
- Work-note versus customer-visible communication rules.
- Read and write tool boundaries.
- Idempotency, concurrency, audit receipts, and failure behavior.

### First technical evidence source

Azure Monitor and Application Insights are the first technical evidence sources.
The v1 agent should use them to:

- Query alerts, metrics, logs, traces, failures, and dependencies for a bounded
  incident time range.
- Correlate symptoms across services and resources without inventing joins.
- Compare technical evidence with the ServiceNow incident record.
- Build and refresh an evidence-backed timeline.
- Link every important claim to its source query, resource, and timestamp.
- State query gaps, retention limits, permission failures, and stale data
  explicitly.

Access should use AKS Workload Identity and least-privilege Azure RBAC. Queries
must be bounded by allowed subscriptions, resource groups, workspaces,
applications, time windows, and result limits.

### Application observability backend

The ARE application exports its own logs, metrics, traces, model operations, and
tool activity through OpenTelemetry to **Azure Monitor, Application Insights, and
Log Analytics**. The trace exploration UI remains the product-facing diagnostic
surface.

The application telemetry workspace must be distinguishable from incident
evidence targets. The agent may query only explicitly allowlisted evidence
resources; it must not infer that its own telemetry resource is the incident
target.

### First implementation slice

The first end-to-end workflow is **investigating a suspected cause across
Application Insights logs and traces**.

The initial vertical slice should:

1. Accept an incident identifier or explicit service, time range, and suspected
   cause.
2. Retrieve the authoritative ServiceNow incident context when an incident ID is
   provided.
3. Resolve allowed Application Insights resources without accepting arbitrary
   resource IDs from the prompt.
4. Generate or select bounded, read-only KQL queries for failures, requests,
   dependencies, exceptions, traces, and relevant deployment/change markers.
5. Preserve query text, target resource, time range, and result metadata as
   evidence.
6. Correlate findings into a timestamped sequence.
7. Rank hypotheses and clearly separate supporting evidence, contradictory
   evidence, unknowns, and next checks.
8. Produce a concise command brief with citations to the underlying queries.
9. Make no ServiceNow, Azure, or AKS changes during this first slice.

Acceptance should include cases where the suspected cause is supported,
contradicted, untestable with available telemetry, or obscured by partial/query
failures.

### Core operating loop

1. **Observe:** retrieve alerts, service health, metrics, logs, traces, changes,
   incidents, dependencies, and runbooks.
2. **Scope:** identify affected services, users, regions, versions, and time range.
3. **Correlate:** build a timeline and connect symptoms with deployments,
   configuration changes, dependency failures, or capacity events.
4. **Hypothesize:** rank likely causes and state the evidence for and against each.
5. **Plan:** propose the lowest-risk mitigation and define expected signals.
6. **Approve:** require explicit human confirmation for mutations.
7. **Act:** execute only the approved, bounded operation.
8. **Verify:** query health signals again and compare them with the expected result.
9. **Communicate:** draft incident updates, handoffs, and stakeholder summaries.
10. **Learn:** produce follow-ups, post-incident findings, and runbook improvements.

### Safety model

- Read-only investigation is the default.
- V1 mutations are limited to explicitly approved ServiceNow updates. Azure
  Monitor, Application Insights, Azure resources, and AKS remain read-only.
- Every ServiceNow mutation must show the exact record, fields, visibility, and
  final wording before requesting approval.
- Approval is scoped to one presented action; it is not reusable.
- Production and non-production targets must be visually and verbally distinct.
- Destructive or broad technical actions are outside v1.
- Tool output is evidence. The agent must distinguish facts, inferences, and
  unknowns.
- Every executed action needs an audit record and a reported receipt.
- A mitigation is not complete until its result is verified.

### Proposed capability groups

#### Keep and reshape from IT service desk

- `ticket-triage` -> incident investigation and evidence-backed diagnosis.
- `queue-overview` -> active major-incident and coordination overview.
- `ticket-actions` -> approved ServiceNow incident updates, assignments, action
  tracking, and communications.
- `it-policy-reference` -> runbook, operational policy, and change-policy lookup.
- `handover-pack-pdf` -> incident handoff or operations handover.
- `file-sharing` -> downloadable reports and evidence bundles.
- `m365-graph` -> communications and on-call context, only if required.
- `workday` -> remove unless employee/org context is an actual requirement.
- `vip-watchlist` -> replace with service criticality and customer-impact
  prioritization.

#### Candidate skills to reuse from other use-cases

- `code-interpreter`: structured log parsing and bounded analysis.
- `data-analysis`: metrics, trends, error budgets, and capacity analysis.
- `document-summary`: runbooks, incident records, and change summaries.
- `rag-search`: internal operational knowledge retrieval.
- `web-search`: vendor status and current public documentation, if external
  internet access is permitted.
- `email-draft`: incident communications, if the UI needs it.

#### New reliability skills likely needed

- Incident status, action, decision, owner, and hypothesis tracking.
- Incident timeline construction from multiple sources.
- Impact and service-criticality assessment.
- Technical-to-stakeholder communication translation.
- Kubernetes workload and cluster inspection.
- Azure Monitor / Log Analytics querying.
- Application Insights metrics, logs, traces, and dependency analysis.
- Deployment and configuration change correlation.
- SLO, SLI, and error-budget analysis.
- Alert investigation and deduplication.
- Runbook discovery and execution.
- Safe Kubernetes remediation with approval and rollback.
- Incident update, handoff, and post-incident review generation.
- GitHub or Azure DevOps change lookup.
- On-call and incident-management integration.

Each capability should be a focused skill over a narrow, permissioned MCP tool
surface. The plan should not expose unrestricted shell, Kubernetes, or cloud
administration as a single general-purpose tool.

## Binding scope and deletion contract

Autopilot must follow this disposition. "Remove" means remove only after the
replacement phase passes its exit criteria; never delete a working path before
its replacement is verified.

### Keep and adapt

- Foundry model hosting and the model deployment used by the Copilot SDK.
- Foundry external-agent registration for observability and evaluation. This is
  a preview integration, so registration failure must not break AKS invocation;
  direct OpenTelemetry export remains authoritative.
- FastAPI API, SSE event contract, Copilot SDK loop, event translation, and file
  streaming.
- One `agent-reliability-engineer` prompt, registry, skill tree, and eval set.
- Blob-backed live skill administration plus a checked-in seed/recovery copy.
- Cosmos DB conversation and shared runtime persistence for v1.
- Azure Monitor, Application Insights, Log Analytics, OpenTelemetry, trace
  exploration, and evaluation interfaces.
- Azure AI Search for runbooks and operational knowledge.
- Microsoft Graph OBO as a separately secured AKS MCP service.
- ServiceNow integration, replacing mock-backed behavior with a production
  contract while retaining synthetic fixtures for tests.
- Downloadable evidence bundles, incident PDFs, and the Playwright dependency
  required to render them.
- Current frontend behavior except controls tied exclusively to multiple personas.
- ACR, Key Vault, the VNet/private networking foundation, and required managed
  identities/RBAC.
- Focused MCP support and synthetic fixtures required by retained ARE workflows.

### Remove after replacement verification

- Every use-case except `agent-reliability-engineer`, after retained skills and
  eval coverage have migrated.
- Persona selection/filtering, persona-specific conversation views, persona
  import/export, and their API routes, templates, tests, and image content.
- Foundry-managed hosted-agent deployment, hosted-agent source image and
  manifests, Invocations proxy, warm pool, gateway session pinning, and
  multi-use-case lazy loading/routing.
- Azure Container Apps workloads and Container Apps environment after equivalent
  AKS workloads pass smoke and rollback tests.
- Azure Static Web Apps after the AKS frontend route passes functional, TLS, and
  rollback tests.
- APM package installation and APM administration. Blob-backed ARE skill
  administration is the supported runtime mutation path.
- Bing Search and the general web-search skill; they are not required by the
  three v1 workflows.
- Azure health-model resources; Azure Monitor and Application Insights are the
  selected evidence and observability path.
- Workday, VIP-specific behavior, demo identities/dates/companies, unrelated
  report templates, and unrelated mock MCP packages.
- Unused Azure resources, role assignments, configuration, hooks, CI jobs,
  documentation, and dependencies made obsolete by the removals above.

### Defer; do not implement in v1

- Azure or AKS mutation/remediation tools.
- Autonomous ServiceNow writes or reusable/broad approvals.
- A persistence migration away from Cosmos DB.
- A second observability stack such as Prometheus/Grafana.
- Multi-persona support, imported personas, or arbitrary external skills.
- Employee/HR enrichment through Workday.
- General internet search.
- Post-incident review automation beyond artifacts naturally produced by the
  three v1 workflows.

## Autopilot execution contract

### Execution order

- Execute phases in numeric order. A later phase may be explored, but must not be
  implemented until every prerequisite phase exit criterion is met.
- Work in one coherent vertical slice at a time. Each slice must include code,
  tests/evals, configuration, and directly related documentation.
- Update this plan at the start and end of every slice, including validation
  results and unresolved blockers.
- Preserve the existing working path until its replacement passes the relevant
  contract and rollback checks.
- Follow the binding keep/remove/defer disposition above. Do not reinterpret
  scope based on convenience.

### Required validation before phase completion

- Run the smallest focused tests while iterating, then the complete affected
  backend or frontend validation suite before marking a phase complete.
- Run agent evals for behavior changes. New tools require happy-path,
  counter-evidence, empty/partial-data, denied-permission, and unsafe-request
  coverage where applicable.
- Run static validation for Kubernetes, Bicep, workflow, and hook changes using
  repository-standard tools.
- Verify secrets, endpoints, tenant/subscription/resource identifiers, and
  private package-feed URLs are absent from the diff.
- Confirm existing user-visible behavior intentionally retained by this plan
  still works.
- Record any unavailable live-environment validation as a blocker rather than
  treating a mock or unit test as equivalent.

### Mandatory stop conditions

Autopilot must stop and request a decision when:

- A change would expand v1 write authority beyond explicitly approved ServiceNow
  updates.
- A required integration contract, authentication method, target resource
  allowlist, or authorization model is unspecified.
- Validation requires production access, production mutation, provisioning,
  deployment, or resource destruction.
- A migration would lose or reinterpret existing Cosmos, Blob, or ServiceNow
  data.
- A security boundary would be weakened, broad cloud/Kubernetes permissions
  would be introduced, or secrets would need to be stored in code/configuration.
- The external-agent registration preview conflicts with AKS availability or
  invocation. AKS invocation and direct OpenTelemetry export take precedence.
- Existing unrelated worktree changes conflict with files needed for the slice.
- Tests expose a pre-existing failure that prevents proving the slice's outcome.
- The implementation would add a new managed service, runtime, package manager,
  deployment platform, or user-facing capability not confirmed in Phase 0.

### Cloud and deployment boundary

- Deployment remains manual. Autopilot may author and validate deployment
  artifacts, but must not run `azd deploy`, provision AKS/Azure resources, modify
  a live cluster, or destroy old resources.
- Live smoke tests may run only after a user identifies a non-production target
  and explicitly authorizes that validation.
- Phase 6 may remove obsolete source and templates after replacement validation,
  but live Azure resource deletion remains a separate manual operation.

### Completion and handoff format

At the end of each phase, record:

- completed scope and intentionally deferred scope;
- files and runtime surfaces changed;
- tests, evals, lint, type checks, build, and manifest validation performed;
- live checks still required;
- migration and rollback notes;
- the exact next ready phase or a blocking decision.

## Delivery phases

### Phase 0 - Product and architecture decisions (complete)

- [x] Record the initial goals and current-state findings.
- [x] Confirm the single use-case name and user-facing product name.
- [x] Confirm the first target users and their top three workflows.
- [x] Define read-only and mutating integrations for the first release.
- [x] Decide whether the first release needs the existing frontend.
- [x] Decide persistence and skill distribution.
- [x] Decide the observability backend.
- [x] Decide whether any existing persona import/export, OBO, or report feature
  stays.
- [x] Agree on an explicit deletion list.

Exit criteria: a short, approved v1 product boundary and target architecture.

### Phase 1 - Establish the single ARE use-case

- [x] Create the ARE system prompt without demo-specific identities or dates.
- [x] Select and copy only the retained skills.
- [x] Rewrite retained skills around reliability terminology and evidence.
- [x] Define the approval and verification contract for mutating skills.
- [x] Add initial ARE evaluation scenarios for normal, ambiguous, and unsafe tasks.
- [x] Add the bounded, read-only Application Insights suspected-cause
  investigation skill and Copilot tool.
- [x] Wire the configured Application Insights resource into the current hosted
  runtime and grant its identity the read-only Monitoring Reader role.
- [ ] Deploy to an explicitly identified non-production environment and run the
  eight ARE scenarios end to end.
- [x] Make the ARE use-case the only/default runtime configuration.

Exit criteria: the agent can execute the agreed read-only reliability workflows
locally using one use-case.

Phase 1 implementation checkpoints:

- `d1e0b4e` - bounded Application Insights investigation vertical slice.
- `342026d` - incident command brief, approval-gated updates, M365 context, and
  file-delivery skills and evals.
- `44317b8` - sole ARE runtime and frontend defaults.
- `c42b1dc` - hosted-runtime Application Insights target and least-privilege
  telemetry access.

Current validation:

- Backend: 105 tests passed; Ruff lint and format checks passed.
- Changed Python modules: mypy passed.
- Frontend: lint passed with pre-existing warnings; production build passed.
- Infrastructure: main and hosted-agent role Bicep templates compile.
- Hooks: all POSIX and Windows hook definitions pass the repository hook checker.

Phase 1 remains open:

- Live evals require an explicitly identified non-production environment.
  Provisioning, deployment, and live cloud mutation remain manual.
- The retained ServiceNow and M365 mock build cannot currently restore its local
  npm dependencies: npm 11 fails internally and public npm access is blocked.
  No dependency manifest or lockfile was changed.

### Phase 2 - Collapse the runtime

- [ ] Start `CopilotAgent` directly in FastAPI lifespan.
- [ ] Replace `FoundryAgentProxy` calls with in-process agent calls.
- [ ] Remove hosted-agent session mapping and warm-pool behavior.
- [ ] Preserve SSE, persistence, tool events, approval prompts, and cancellation.
- [ ] Simplify configuration and authentication for AKS Workload Identity.
- [ ] Make Blob-backed skill updates consistent across multiple API replicas.
- [ ] Define skill admin authorization, validation, versioning, rollback, and
  audit behavior.
- [ ] Reduce the image to only required MCP servers and system dependencies.
- [ ] Remove unused backend endpoints and services.

Exit criteria: one application process runs chat end to end without the Foundry
hosted-agent Invocations service.

### Phase 3 - Simplify the frontend and API

- [ ] Remove persona discovery and selection.
- [ ] Remove persona import/export and unused admin panels.
- [ ] Rename UI language from support/persona terminology to ARE terminology.
- [ ] Keep or redesign conversation history for one agent.
- [ ] Add clear environment, target, approval, execution, and verification UX.
- [ ] Remove API fields and routes made obsolete by the single-agent model.

Exit criteria: no user-visible or API-level multi-persona concepts remain.

### Phase 4 - Add production reliability integrations

- [ ] Replace the ServiceNow mock path with the production major-incident read
  contract.
- [ ] Implement exact-draft, explicitly approved ServiceNow updates with
  idempotency, audit receipts, and verification.
- [ ] Implement allowlisted Azure Monitor/Application Insights evidence queries
  and deployment/change correlation.
- [ ] Implement the live command brief model: impact, timeline, hypotheses,
  decisions, owners, actions, questions, and staleness.
- [ ] Implement delegated Microsoft Graph email, calendar, presence, and Teams
  workflows through the OBO MCP service.
- [ ] Add read-only Kubernetes inspection only where it supports the three v1
  workflows; do not add remediation tools.
- [ ] Enforce authorization, target scoping, approvals, audit, and verification.
- [ ] Add failure, timeout, partial-data, and stale-data handling.

Exit criteria: the v1 ARE workflows operate against real, permissioned systems.

### Phase 5 - Package for AKS

- [ ] Create a deterministic, pinned production image.
- [ ] Add Kubernetes manifests or a Helm chart.
- [ ] Configure Workload Identity and least-privilege Azure RBAC.
- [ ] Configure ingress/Gateway API and TLS.
- [ ] Configure secrets, ConfigMaps, probes, resources, PDB, and NetworkPolicies.
- [ ] Configure persistent managed services and backup/retention policies.
- [ ] Configure OpenTelemetry and operational dashboards/alerts.
- [ ] Add CI image build, scan, test, and manifest validation.
- [ ] Keep deployment manual until a deployment workflow is explicitly approved.

Exit criteria: the application is repeatably deployable to a non-production AKS
cluster and passes smoke, security, and resilience checks.

### Phase 6 - Delete obsolete architecture

- [ ] Remove unused use-cases, skills, mocks, and fixtures.
- [ ] Remove Foundry hosted-agent source and deployment configuration.
- [ ] Remove obsolete Bicep modules and azd hooks.
- [ ] Remove unused Azure resource dependencies.
- [ ] Rewrite architecture, local-development, and deployment documentation.
- [ ] Run repository-wide tests and confirm no obsolete terminology or routes remain.

Exit criteria: the repository contains one supported architecture and no dead
deployment path.

## Validation strategy

- Unit tests for skill loading, prompt selection, authorization, and approval state.
- Contract tests for every MCP integration.
- Agent evals for evidence use, uncertainty, safe refusal, approval, and verification.
- API integration tests for SSE event ordering and conversation persistence.
- AKS smoke tests for probes, ingress, identity, secrets, telemetry, and scaling.
- Failure injection for unavailable telemetry, denied permissions, MCP timeouts,
  pod restarts, and partial results.
- Security review of Kubernetes RBAC, workload identity, tool authorization,
  network egress, secret handling, and prompt/tool injection boundaries.

## Key design decisions

| ID | Decision | Status | Notes |
|---|---|---|---|
| D-001 | Run on AKS | Confirmed | User goal. |
| D-002 | Retain GitHub Copilot SDK | Confirmed | User goal. |
| D-003 | Retain skill-based extensibility | Confirmed | User goal. |
| D-004 | Reduce to one personality/use-case | Confirmed | User goal. |
| D-005 | Base the new agent on IT service desk | Confirmed | Evolve, do not preserve demo framing. |
| D-006 | Name the product Agent Reliability Engineer and the use-case `agent-reliability-engineer` | Confirmed | Use this vocabulary in code, UI, docs, and operations. |
| D-007 | Run Copilot SDK in the FastAPI process | Proposed | Largest runtime simplification. |
| D-008 | Keep Blob Storage and the skills admin UI | Confirmed | Checked-in skills seed/recover the single live skill set; runtime changes need authorization, versioning, audit, and replica consistency. |
| D-009 | Keep the current frontend largely unchanged in v1 | Confirmed | Remove or hide only controls invalidated by the single-use-case model unless separately approved. |
| D-010 | Keep Azure Cosmos DB for v1 persistence | Confirmed | Avoid coupling AKS/runtime work to a data-model migration; revisit after simplification. |
| D-011 | Serve frontend from AKS | Confirmed | Containerize the static export and remove Static Web Apps from the target architecture. |
| D-012 | Keep evaluation and trace exploration UIs | Confirmed | Treat them as product quality and diagnostic capabilities for agent behavior. |
| D-013 | Retain Microsoft Graph OBO | Confirmed | Support delegated email, calendar, presence, and Teams incident workflows through a separate secured AKS service. |
| D-014 | First production integrations | Confirmed | ServiceNow, Azure Monitor, and Application Insights. |
| D-015 | Mutating actions in v1 | Confirmed | Only explicitly approved ServiceNow updates; technical systems remain read-only. |
| D-016 | Primary v1 user is an incident commander | Confirmed | Prioritize coordination, evidence, decisions, ownership, timelines, and communications over direct remediation. |
| D-017 | ServiceNow is the incident system of record | Confirmed | Replace the mock connector with a production, permissioned major-incident contract. |
| D-018 | Azure Monitor and Application Insights are the first technical evidence sources | Confirmed | Use bounded, attributed queries through AKS Workload Identity. |
| D-019 | V1 writes are limited to approved ServiceNow updates | Confirmed | Azure, Application Insights, and AKS access remain read-only. |
| D-020 | Use separate frontend and agent/API Deployments and Services | Confirmed | Both live in one AKS namespace; the agent is in-process with the API. |
| D-021 | Keep downloadable files and PDF incident reports | Confirmed | Retain file streaming and Playwright, but remove non-incident report templates. |
| D-022 | Remove persona import and export | Confirmed | The product has one managed ARE use-case; remove exporter/importer APIs, UI, tests, templates, and image content. |
| D-023 | First vertical slice is suspected-cause investigation in Application Insights | Confirmed | Use bounded logs and traces to produce ranked, cited findings before adding write workflows. |
| D-024 | V1 workflows are investigation, live command brief, and approved incident updates | Confirmed | These three workflows define release scope and acceptance. |
| D-025 | Use Azure Monitor, Application Insights, and Log Analytics for application observability | Confirmed | Export via OpenTelemetry and retain trace exploration. |
| D-026 | Run the Copilot SDK in the AKS agent/API service and register it as a Foundry external agent | Confirmed | Foundry retains model hosting, observability, and evaluation; AKS owns invocation and runtime operations. |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Simplifying too early removes useful quality tooling | Decide eval and trace retention separately from persona/admin removal. |
| In-process SDK sessions conflict with multiple AKS replicas | Externalize required state or use a deliberate session-affinity design; test pod restart behavior. |
| Broad Kubernetes/cloud tools create excessive privilege | Expose narrow MCP operations and use separate read/write identities or authorization paths. |
| Agent proposes unsafe remediation | Require scoped approval, policy checks, rollback plans, and post-action verification. |
| Dynamic skills create configuration drift | Start with immutable image-baked skills and version them with releases. |
| Demo skills appear production-ready but depend on mocks | Replace each retained mock-backed integration with an explicit production contract. |
| Removing Foundry hosted agents changes model/session behavior | Add SSE, multi-turn, cancellation, and restart tests before deleting the old path. |
| One application image becomes large | Remove unused mocks, Playwright, APM, exporters, and build dependencies before optimizing topology. |

## Discussion agenda

Work through these in order:

1. Who is the ARE's primary user, and what are the top three jobs it must do?
2. Which systems contain the first release's source-of-truth signals?
3. Should v1 be read-only, or may it perform approved remediation?
4. Is the web UI required, and where should it run?
5. Which persistence and skill-distribution model is acceptable?
6. Which current quality/admin features justify their operating cost?
7. What is the minimum AKS production baseline for the first milestone?

## Progress log

### 2026-10-14

- Inspected the current runtime, use-case/skill model, service-desk prompt and
  skills, frontend persona selection, container topology, and Azure resources.
- Recorded the initial target architecture and reduction candidates.
- Selected an incident commander as the primary v1 user.
- Reframed v1 around incident coordination and communication rather than
  autonomous remediation.
- Selected ServiceNow as the incident system of record.
- Selected Azure Monitor and Application Insights as the first technical evidence
  sources.
- Limited v1 writes to explicitly approved ServiceNow updates; all technical
  integrations remain read-only.
- Confirmed that the current web UI remains largely unchanged.
- Confirmed that the frontend runs on AKS.
- Selected separate frontend and agent/API Deployments and Services in one
  namespace.
- Kept Azure Cosmos DB as the v1 production persistence backend.
- Kept Blob Storage and the existing skills admin UI for live skill updates.
- Kept both evaluation and trace exploration interfaces.
- Kept Microsoft Graph OBO for email, calendar, presence, and Teams workflows.
- Kept downloadable files and PDF incident handover reports.
- Removed persona import and export from the target product.
- Selected suspected-cause investigation across Application Insights logs and
  traces as the first implementation slice.
- Named the product Agent Reliability Engineer and the single use-case
  `agent-reliability-engineer`.
- Initial planning discussion complete; implementation has not started.

### 2026-10-15

- Added the `agent-reliability-engineer` use-case with an incident-command system
  prompt and no demo-specific identity, company, or date.
- Added `app-insights-investigation`, a fixed read-only Copilot tool that:
  - uses only the configured Application Insights resource or workspace;
  - accepts no arbitrary resource ID or KQL;
  - limits lookback to 1-24 hours and rows to 1-50 per query;
  - queries requests, dependencies, exceptions, traces, and change events;
  - returns query provenance, evidence IDs, and explicit partial failures.
- Added the suspected-cause investigation skill response contract.
- Added five ARE eval scenarios covering supporting evidence, contradictory
  evidence, empty telemetry, partial query failure, and rejected AKS writes.
- Added unit and integration tests for bounds, escaping, configured-resource
  enforcement, provenance, partial failures, skill registration, and eval files.
- Validation:
  - Backend tests: 98 passed.
  - Ruff lint and formatting: passed.
  - Changed-module mypy: passed.
  - Full mypy is currently blocked by an installed NumPy stub that uses Python
    3.12 syntax while the project config targets Python 3.11.
- The slice is additive: existing personas and deployment topology remain intact.
- Next: configure a non-production Application Insights target and execute the
  ARE eval scenarios end to end before collapsing the runtime.
- Confirmed the three v1 workflows: suspected-cause investigation, a live
  evidence-backed command brief, and approved incident updates.
- Selected Azure Monitor, Application Insights, and Log Analytics via
  OpenTelemetry for the ARE application's own observability.
- Confirmed that the Copilot SDK runs in the AKS agent/API service, Foundry keeps
  model hosting, and the AKS runtime is registered as an external agent for
  observability and evaluation.
- Finalized the binding keep/remove/defer contract.
- Added autonomous execution order, validation gates, mandatory stop conditions,
  manual cloud/deployment boundaries, and phase handoff requirements.
- Phase 0 complete.
