# Foundry Adoption Plan

## Goal

Use more of Microsoft Foundry's managed agent capabilities without replacing Kratos's code-centric orchestration, dynamic personas, custom UI protocol, or existing deployment model.

The target architecture keeps Kratos as a Foundry Hosted Agent and incrementally moves suitable shared capabilities into a Foundry Toolbox. Foundry becomes the managed hosting, model, identity, observability, evaluation, and tool-governance plane. Kratos continues to own persona selection, Copilot SDK orchestration, interactive workflows, and the product-specific event contract.

## Decisions

1. Keep the existing Foundry Hosted Agent and Invocations protocol.
2. Introduce Foundry Toolbox as an additional MCP source consumed by the hosted container.
3. Start with the `generic` persona and low-risk tools.
4. Preserve current tools as a fallback during migration.
5. Require evaluation evidence before making Toolbox the default or expanding it to other personas.
6. Keep prompts and persona routing in source control for now.
7. Defer Graph OBO migration until Foundry identity passthrough is proven end to end.
8. Treat Responses protocol and specialist Foundry agents as later, independent decisions.

## Implementation Status

Last updated: 2026-09-17

- [x] Confirmed Toolbox and Hosted Agent support in Sweden Central.
- [x] Added declarative `kratos-tools` provisioning with managed Web Search.
- [x] Corrected the Bicep project endpoint output to use Azure's `AI Foundry API` endpoint.
- [x] Added disabled-by-default, environment-controlled Toolbox consumption.
- [x] Added renewable managed-identity authentication through the Copilot SDK MCP auth callback.
- [x] Scoped Toolbox consumption to the `generic` persona and removed duplicate local `web_search` exposure while enabled.
- [x] Deployed an active Hosted Agent and Toolbox in the `bankfdry` environment with Toolbox enabled.
- [x] Added focused tests for disabled behavior, persona scoping, and endpoint-scoped authentication.
- [x] Proved an end-to-end generic Web Search invocation through Toolbox; the deployed event stream recorded a successful `kratos-tools-web_search` call with sourced results.
- [x] Fixed the eval CLI scenario filter payload to match the backend `scenarios` contract.
- [x] Confirmed platform-managed Application Insights telemetry after full Hosted Agent recreation; the reserved connection string remains absent from the agent definition and must not be declared there.
- [x] Verified a successful `execute_tool kratos-tools-web_search` dependency span in Application Insights with Foundry agent, project, session, invocation, and GenAI tool dimensions. Authentication succeeded operationally, but no dedicated auth dimension is emitted.
- [x] Recreated the deleted `bankfdry` Hosted Agent instance identity by deleting and redeploying the hosted agent, then reapplied its declarative role assignments; a minimal model invocation returned `OK`.
- [x] Deployed and verified the evaluator adapter in `bankfdry`. A post-fix Toolbox run of `generic/web-search-ai-trends` completed in 25.7 seconds, invoked `kratos-tools-web_search`, passed the aggregate, and scored Coherence 5/5, Relevance 4/5, Intent Resolution 4/5, Tool Call Accuracy 5/5, and Task Adherence 1/1. Task Adherence now executes instead of failing schema validation, explicit evaluator pass flags take precedence over numeric-score thresholds, and failed/skipped evaluators can no longer produce a false passing run summary.
- [x] Completed the disabled baseline comparison for `generic/web-search-ai-trends` in `bankfdry` and restored Toolbox immediately afterward. The local `web_search` run completed in 30.9 seconds and scored Coherence 5/5, Relevance 5/5, Intent Resolution 4/5, Tool Call Accuracy 5/5, and Task Adherence 1/1, with an aggregate pass. The earlier Toolbox run completed in 31.6 seconds with the same criterion scores and an explicit Task Adherence pass; its stored aggregate failure was produced by the older threshold-based aggregation and is not a quality difference. Deployment-spanning warm-pool sessions can retain the previous agent version, so controlled comparisons must drain the two-session pool before measuring. `bankka` remained untouched.
- [x] Added a guarded multi-run comparison command that verifies the selected azd environment, drains the warm pool after each Hosted Agent deployment, enforces release thresholds, and restores Toolbox after failures.
- [x] Ran the guarded five-sample comparison in `bankfdry` and restored Toolbox on the active Hosted Agent version. Tool routing was correct in all ten measured runs. Local tools passed 3/5 runs with a 34.3-second median and 38.4-second p95; Toolbox passed 2/5 runs with a 29.6-second median and 37.2-second p95. Toolbox latency passed the 20% regression limit, but promotion failed the 100% quality threshold. The local failures were both Task Adherence failures. Toolbox failures included Task Adherence in three runs, with one also failing Tool Call Accuracy and another also failing Intent Resolution. Keep the local fallback and investigate evaluator or response variability before promoting Toolbox or starting Code Interpreter migration.
- [x] Separated evaluation evidence retention from telemetry truncation. Completed tool events now retain up to 12,000 characters for evaluators while OpenTelemetry attributes remain capped at 2,000 characters. The focused regression test and all 15 affected backend tests pass.
- [x] Repeated the five-sample comparison after the evidence fix. Local improved to 5/5 with a 34.0-second median and 35.2-second p95. Toolbox improved to 3/5 with a 30.7-second median but failed both the quality gate and latency gate with a 43.5-second p95. The Toolbox latency outlier passed all quality criteria, so latency and quality failures were independent.
- [x] Tightened the generic prompt to treat tool output as evidence, obey explicit response constraints, and cite only sources present in tool output. A five-sample rerun kept local at 5/5 and Toolbox at 3/5; Toolbox latency passed at 38.0-second p95 versus 40.1 seconds locally.
- [x] Aligned the committed web-search scenario input with its expected one-sentence, one-citation-per-item contract and reran five samples per mode. Local passed 5/5 with a 32.4-second median and 54.6-second p95. Toolbox passed 3/5 with a 28.6-second median and 30.0-second p95. Managed `web_search` returned 10,000-12,000 characters of evidence but exposed empty tool arguments; Task Adherence intermittently treated the call as uncorroborated or counted the tool's long report as assistant prose. This is evaluator instability around the managed tool transcript, not evidence truncation or a latency regression. Promotion remains blocked, the local fallback stays in place, and Code Interpreter work remains deferred.
- [x] Replaced Task Adherence as the Web Search release decision with a deterministic gate that requires the expected completed tool, exactly five one-line numbered items, one sentence and one evidence-backed citation per item, and passing non-Task quality evaluators. The comparison now also aborts before either Hosted Agent deployment when the blob-backed scenario differs from the committed scenario; this caught an older blob override that had made the first apparent 0/5 versus 0/5 strict-format run invalid.
- [x] Synchronized the committed scenario through the evaluation API and completed the first valid deterministic five-sample comparison. Local passed 5/5 with a 28.4-second median and 37.0-second p95. Toolbox passed 2/5 with a 35.9-second median and 118.5-second p95. One Toolbox failure cited five URLs absent from tool evidence; two contract-compliant runs failed Tool Call Accuracy after repeated managed-search attempts, and the latency outlier also made redundant `web_fetch` calls. This is a Toolbox-specific quality and latency regression. Keep the local fallback, do not promote Toolbox, and keep Code Interpreter deferred.
- [x] Added generic-persona Toolbox routing guidance that permits one managed search plus at most one evidence-driven retry and prohibits `web_fetch` unless the user requests a specific page. A five-sample Toolbox diagnostic then passed 4/5 with a 33.9-second median and 38.1-second p95; every completed sample used one or two managed searches with no `web_fetch` or failed tool calls. The sole failure substituted five URLs absent from tool evidence. This was not a valid promotion comparison because the paired local run passed only 1/5 and included two Hosted Agent HTTP 409 invocation failures.
- [x] Made the comparison report invocation errors as failed samples while computing latency from completed scenarios, instead of discarding the full comparison. Focused regression coverage verifies the partial-failure behavior.
- [x] Added Toolbox-only guidance to copy citation URLs exactly from managed-search evidence and ran a warm-pool drain plus five diagnostic samples on active Hosted Agent version 27. Toolbox passed 0/5 with a 23.2-second median and 37.9-second p95: one sample failed with Hosted Agent HTTP 409, three returned prose that violated the exact five-line response contract, and one still substituted all five citation URLs. All four completed samples used exactly one managed search with no `web_fetch` or failed tool calls. The experiment was rejected and the citation-specific sentences were removed; active version 28 restores the earlier routing-only guidance. Toolbox remains unpromoted, the local fallback stays available, and Code Interpreter remains deferred.
- [x] Replaced the generic Copilot SDK MCP handoff with an application-owned Streamable HTTP bridge for `kratos-tools-web_search`. The bridge sends the documented `search_query`, preserves resource text and citation metadata, deduplicates authoritative HTTP(S) sources, and explicitly forbids invented links when Toolbox returns no source URLs. All 100 backend tests and touched-file Ruff checks pass. Deployed Hosted Agent version 30 with Toolbox enabled in `bankfdry`; a targeted `generic/web-search-ai-trends` run completed in 28.6 seconds, invoked the bridge once, and used five URLs that were all present in tool evidence. It remained ineligible for promotion because an assistant preamble violated the exact five-line deterministic contract. After installing the locked Node dependencies through the configured Azure Artifacts feed, all 15 post-deploy API smoke tests passed in 31.3 seconds.
- [x] Ran the API-only deployed smoke suite against `bankfdry`; it completed successfully.
- [x] Restored backend dependencies through Azure Artifacts; all 83 backend tests pass and focused evaluator lint is clean. Whole-tree Ruff still reports three unrelated existing findings.
- [x] Verified the recreated Hosted Agent identity has only the declared ARM data-plane roles (`Cognitive Services OpenAI User`, `Cognitive Services User`, `Key Vault Secrets User`, and `Storage Blob Data Reader`) plus one Cosmos DB data-plane assignment; Toolbox execution succeeds with this set.

## Current State

Foundry currently provides:

- AI Services account, project, and model deployment.
- Managed Hosted Agent compute with a dedicated endpoint and agent identity.
- Per-session isolation and scale-to-zero behavior.
- Container versioning and lifecycle management.
- Application Insights integration and OpenTelemetry traces.

Kratos currently provides inside the hosted container:

- Persona selection and system prompts.
- Copilot SDK session orchestration.
- Built-in Python tools such as web search, Azure AI Search, code interpreter, CRM, and agent delegation.
- `SKILL.md` discovery and APM package management.
- MCP server discovery and per-user OBO token injection.
- Cosmos-backed application state and the custom SSE event contract.
- Evaluation scenario storage, invocation, scoring, and reporting.

Because the Hosted Agent is bring-your-own code, Foundry cannot introspect its internal Copilot SDK tools. The empty tool list in the Foundry UI is therefore expected. Tools moved into a Toolbox will become Foundry-managed and visible there.

## Target Architecture

```mermaid
flowchart LR
    UI[Kratos UI] --> API[Container App API]
    API --> HA[Foundry Hosted Agent]
    HA --> ORCH[Kratos Copilot SDK orchestration]
    ORCH --> TB[Foundry Toolbox MCP endpoint]
    ORCH --> LOCAL[Kratos local skills and tools]
    TB --> WEB[Managed web search]
    TB --> CODE[Managed code interpreter]
    TB --> SEARCH[Azure AI Search]
    TB --> MCP[Approved MCP and OpenAPI services]
    HA --> MODEL[Foundry model deployment]
    HA --> DATA[Cosmos, Blob, Key Vault]
    HA --> OBS[Foundry traces and evaluations]
```

Toolbox is additive at first. A tool is removed from the local registry only after its Toolbox replacement passes functional, identity, latency, cost, and evaluation checks.

## Success Criteria

- `azd up` can provision or configure the Toolbox integration without embedding environment-specific endpoints or credentials in the repository.
- Toolbox can be enabled per azd environment and disabled without rebuilding application behavior.
- The generic persona can discover and invoke the pilot Toolbox tools from the Foundry Hosted Agent.
- Existing generic web-search and computation scenarios do not regress.
- Tool calls and failures are observable in Foundry/Application Insights with stable tool names.
- Authentication uses managed identity or Foundry-managed connections; no secrets are added to manifests or images.
- Existing personas, APM skills, MCP integrations, local development, and OBO continue to work when Toolbox is disabled.
- A documented rollback returns traffic to current local tools without infrastructure teardown.

## Non-Goals

- Replacing Kratos with a prompt-only Foundry agent.
- Moving all personas or prompts into the Foundry portal.
- Replacing the frontend or backend proxy.
- Changing the Invocations protocol in the initial work.
- Migrating delegated Microsoft Graph access in the pilot.
- Moving every local file-manipulation or workflow skill into Toolbox.

## Phase 0: Baseline and Compatibility Spike

### Work

- Confirm Toolbox availability, API version, SDK support, and regional availability for the selected azd environment.
- Confirm Hosted Agent access to a Toolbox MCP endpoint using its dedicated agent identity.
- Record the exact Toolbox resource model and determine whether provisioning can be expressed in Bicep or requires a deployment hook/SDK call.
- Capture baseline results for:
  - `generic/web-search-ai-trends`
  - `generic/code-interpreter-math`
  - `generic/data-analysis-with-charts`
- Record response quality, expected tool selection, duration, cold-start impact, token usage where available, and failure behavior.
- Verify current tool-call events contain enough structure for Foundry process evaluators.

### Baseline Exit Criteria

- A supported provisioning and authentication path is identified.
- Baseline eval artifacts are available for comparison.
- No unsupported preview dependency is accepted without an explicit decision and rollback plan.

## Phase 1: Toolbox Foundation

### Infrastructure

- Add a Toolbox associated with the existing Foundry project.
- Surface only non-sensitive Toolbox identifiers/endpoints as Bicep or azd outputs.
- Grant the Hosted Agent identity the minimum roles required to discover and invoke Toolbox tools.
- Prefer Foundry connections and managed identity over keys.
- Keep all deployment-specific values in azd environment outputs or environment variables.

### Runtime

- Add an opt-in configuration such as `FOUNDRY_TOOLBOX_ENABLED=false` and a runtime-resolved Toolbox endpoint/identifier.
- Register Toolbox as one MCP server in the Copilot SDK session configuration.
- Scope the pilot to the `generic` persona.
- Keep existing local tools registered while validating connectivity, but avoid exposing duplicate tool names to the model in the same session.
- Normalize Toolbox events into the existing Kratos `tool_call` stream and OpenTelemetry attributes.
- Fail closed for authentication errors and fall back only when explicitly configured.

### Tests

- Configuration parsing and disabled-by-default behavior.
- Persona scoping.
- MCP configuration construction without secret logging.
- Tool-event normalization.
- Hosted smoke invocation with Toolbox enabled and disabled.

### Foundation Exit Criteria

- Toolbox is deployable repeatedly through the project workflow.
- Enabling it for `generic` does not affect other personas.
- Disabling it restores current behavior without code or infrastructure rollback.

## Phase 2: Pilot Managed Tools

### 2A. Web Search

Move the current web-search capability to Toolbox first.

Benefits:

- Removes the custom Responses API wrapper and endpoint transformation.
- Centralizes tool configuration and governance.
- Makes the tool visible and independently versionable in Foundry.

Acceptance criteria:

- Source citations remain available to the frontend.
- Tool naming remains compatible with existing scenarios or is migrated deliberately.
- Quality and success rate meet or exceed the baseline.
- Added latency and cost are measured and accepted.

### 2B. Code Interpreter

Evaluate Foundry-managed Code Interpreter against the current local Python subprocess.

Benefits:

- Stronger execution isolation and managed lifecycle.
- Better governance than arbitrary subprocess execution inside the Hosted Agent container.
- Potentially better file and execution observability.

Acceptance criteria:

- Numeric and data-analysis scenarios pass.
- Generated files can still be surfaced through the Kratos download flow.
- Required Python package behavior is documented.
- Execution limits, persistence, and cost are acceptable.

Do not remove the local interpreter until file transfer and package compatibility are proven.

## Phase 3: Foundry Evaluation Expansion

The repository already invokes the Hosted Agent and scores scenarios with `azure-ai-evaluation`. Extend this rather than replacing it.

### Evaluation Work

- Normalize captured messages, tool definitions, tool calls, and tool results to Foundry's current evaluation schema.
- Add or enable process evaluators where supported:
  - Tool Selection
  - Tool Input Accuracy
  - Tool Output Utilization
  - Tool Call Success
  - Task Navigation Efficiency
- Retain Task Adherence, Intent Resolution, Relevance, Coherence, and Tool Call Accuracy.
- Compare local-tool and Toolbox runs using the same committed scenarios.
- Link cloud evaluation reports and trace identifiers from Kratos run records.
- Define release thresholds for pilot scenarios and prevent a failed eval from being treated as a successful migration.

### Pilot Promotion Thresholds

- Measure at least five runs per mode after one unmeasured warm-pool drain run.
- Require a 100% aggregate and per-criterion pass rate in both modes.
- Require every local run to invoke `web_search` and every Toolbox run to invoke `kratos-tools-web_search`.
- Require Toolbox p95 scenario latency to be no more than 20% above the local p95.
- Do not remove the local fallback until per-run tool cost is observable. Once available, require Toolbox cost to be no more than 20% above local cost unless the increase is explicitly accepted.

Run the guarded comparison from `src/backend`:

```bash
uv run python ../../scripts/compare_toolbox_evals.py \
  --environment "$(azd env get-value AZURE_ENV_NAME)" \
  --runs 5 \
  --min-pass-rate 1.0 \
  --max-p95-regression 0.20 \
  --confirm-disruptive
```

The command performs two Hosted Agent redeployments and always attempts to restore Toolbox to enabled. It does not persist response content or deployment values.

The first five-sample promotion run did not pass the quality gate. Both modes used the expected tool on every measured run, and Toolbox p95 latency was 3.1% lower than local p95, but aggregate pass rates were 60% for local and 40% for Toolbox.

After retaining up to 12,000 characters of tool evidence, local reached 100% in three consecutive five-sample comparisons while Toolbox remained at 60%. The release decision now uses a deterministic citation-and-format contract plus non-Task response-quality evaluators because Task Adherence proved unreliable for the managed-tool transcript. The first valid deterministic comparison passed local 5/5 and Toolbox 2/5; narrowly scoped routing guidance later improved a Toolbox-only diagnostic to 4/5 and eliminated redundant searches, `web_fetch`, and failed tool calls. That result was not promotion-valid because the paired local measurement suffered platform failures.

Exact citation-copying guidance did not close the remaining gap. The subsequent version 27 Toolbox-only diagnostic passed 0/5: one HTTP 409 invocation error, three format-contract failures, and one response with five unsupported citation URLs. Although completed-run latency and tool-call efficiency were acceptable, response reliability was not. The citation-specific experiment was rolled back in version 28 to the routing-only prompt that previously produced 4/5. Keep Toolbox opt-in, retain the local fallback, and defer Code Interpreter until a fresh full comparison reaches 100% in both modes and satisfies the p95 threshold.

### Evaluation Exit Criteria

- Baseline and Toolbox variants can be compared from repeatable runs.
- Tool-level regressions are visible, not hidden inside final-response scores.
- The team has agreed thresholds for quality, reliability, latency, and cost.

## Phase 4: Shared MCP, OpenAPI, and Search Integrations

After the pilot succeeds, move stateless or agent-identity-based integrations that benefit from centralized reuse.

Priority order:

1. Non-user-specific remote MCP servers.
2. Stable internal APIs represented by OpenAPI.
3. Azure AI Search connections where indexes and authorization fit Toolbox's model.
4. Shared workflows suitable for versioned Foundry Skills.
5. A2A delegation to independently owned specialist agents.

For each candidate:

- Identify the owning team and authentication model.
- Decide whether it is global, persona-specific, or environment-specific.
- Preserve least privilege and persona allowlists.
- Run the relevant use-case eval suite before removing the old path.
- Document data residency and third-party data-flow implications.

Local `SKILL.md` workflows should remain in Kratos when they depend on the container filesystem, installed CLIs, custom interactive input, or tight coupling to the Copilot SDK session.

## Phase 5: Graph OBO Identity Spike

Do not migrate the current Graph OBO MCP path as part of the initial Toolbox work.

### Questions to Prove

- Can Toolbox perform OAuth identity passthrough for the signed-in user from this custom frontend and Invocations protocol?
- Is consent handled at the correct tenant and scope?
- Can a user token ever be exposed to an unintended Toolbox tool or MCP endpoint?
- How are token refresh, Conditional Access, audit logs, and revoked consent handled?
- Does the flow work for direct API use as well as future Teams/Microsoft 365 channels?

### Required Security Tests

- No-token and expired-token behavior.
- Wrong audience and wrong tenant rejection.
- Persona/tool allowlist enforcement.
- Confused-deputy attempts using a similarly named MCP server.
- User A cannot access User B's delegated context.
- Tokens never appear in logs, traces, eval artifacts, or tool output.

Only replace the custom OBO injection after these checks pass and the authorization boundary is at least as strong as the current implementation.

## Phase 6: Optional Responses Protocol

Consider adding Responses alongside Invocations after Toolbox is stable. Do not make this a prerequisite for Toolbox.

Potential benefits:

- Foundry-managed conversation history.
- Native conversation and tool-call records.
- Easier playground testing.
- Background execution and cancellation.
- Teams and Microsoft 365 publishing through supported channel bridges.

Costs and risks:

- Translation of the custom SSE event contract.
- Reconciliation with Cosmos conversation mappings.
- Interactive user-input and file-download behavior.
- Frontend and backend proxy changes.
- Migration and coexistence rules for existing conversations.

Proceed only with a separate design and measured product need.

## Phase 7: Optional Specialist Agents

Create separate Foundry agents only where a capability has independent ownership, lifecycle, permissions, or scaling needs. Connect them through A2A or Toolbox.

Do not create one Foundry agent per Kratos persona merely to mirror the current directory structure. Persona configuration should stay lightweight unless separation provides a concrete governance or operational benefit.

## Rollout Strategy

1. Development environment only, Toolbox disabled by default.
2. Enable for the generic persona and internal test users.
3. Run baseline and Toolbox evals repeatedly.
4. Enable one pilot tool at a time.
5. Observe traces, errors, latency, token usage, and cost.
6. Promote only after acceptance thresholds are met.
7. Expand to selected personas and integrations.
8. Remove local implementations only after a full release cycle with no required rollback.

## Rollback Strategy

- Disable Toolbox through the azd environment setting.
- Restore the current local tool allowlist for the affected persona.
- Keep Toolbox infrastructure in place during observation; disabling consumption must be sufficient.
- Preserve existing tool names and event shapes where practical so rollback does not require frontend changes.
- Never couple rollback to deletion of Foundry resources or connections.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Toolbox and Skills features change while in preview | Pin supported API/SDK versions, isolate integration code, and keep local fallbacks |
| Duplicate tools confuse model selection | Expose only one implementation of a logical tool per session |
| Persona receives excessive capability | Apply explicit persona allowlists or separate toolboxes |
| Managed tool output differs from current event schema | Normalize at the hosted-agent boundary and cover with contract tests |
| Added network hop increases latency or cost | Benchmark before/after and set acceptance thresholds |
| Tool authentication broadens privileges | Use dedicated agent identity, managed connections, and least-privilege RBAC |
| OBO passthrough weakens user isolation | Defer migration and require dedicated security testing |
| Portal configuration drifts from source control | Provision declaratively where supported and document unavoidable control-plane steps |
| Local development becomes dependent on Azure | Keep Toolbox optional and preserve local implementations |

## First Implementation Slice

The first pull request should contain only the foundation needed to prove one managed tool:

- [x] Document and validate the current Toolbox resource/API support in the target region.
- [x] Add declarative Toolbox provisioning through the Foundry azd extension.
- [x] Add azd outputs and hosted-agent environment configuration without hardcoded endpoints.
- [x] Add an opt-in Toolbox MCP entry for the `generic` persona.
- [x] Configure managed Web Search in Toolbox.
- [x] Preserve the current local web-search implementation behind the disabled/default path.
- [x] Add focused unit tests for configuration, persona scoping, authentication, and event normalization.
- [x] Run the existing `web-search-ai-trends` scenario in both modes and record the comparison.
- [x] Run backend tests/lint and the API-only deployed smoke suite.

Code Interpreter belongs in a follow-up pull request because file transfer and execution semantics require separate validation.

## Open Decisions Before the First Edit

- Which azd environment and region will host the pilot?
- Is the current Foundry Toolbox feature/API available there and suitable for production, or preview-only?
- Can Toolbox resources and tool connections be provisioned through Bicep at the required API version?
- What latency, quality, success-rate, and cost deltas are acceptable?
- Should the pilot use one project-wide Toolbox with persona filtering or a dedicated generic-persona Toolbox?

Default recommendation: begin with a dedicated pilot Toolbox in a non-production azd environment. This gives the clearest authorization and rollback boundary while the integration is being learned.
