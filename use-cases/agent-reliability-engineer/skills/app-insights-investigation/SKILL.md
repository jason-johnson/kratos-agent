---
name: app-insights-investigation
description: Test a suspected reliability cause using bounded, read-only Application Insights logs and traces
enabled: true
---

# Application Insights suspected-cause investigation

Use this skill when the user asks whether a suspected cause explains an incident,
failure, latency increase, dependency problem, or behavior change.

## Required inputs

Before calling `app_insights_investigation`, identify:

- `service_name`: the affected Application Insights cloud role.
- `suspected_cause`: the specific hypothesis to test.
- `lookback_hours`: an explicit 1-24 hour window. Ask when it cannot be inferred.
- `max_rows_per_query`: normally 20; increase only when the investigation needs it.

The tool selects the configured Application Insights resource. Never request,
accept, or invent a subscription ID, resource ID, workspace ID, or arbitrary KQL.

## Workflow

1. Restate the service, time window, and hypothesis.
2. Call `app_insights_investigation`.
3. Inspect every query receipt. A failed or partial query is an evidence gap, not
   an empty result.
4. Correlate requests, dependencies, exceptions, traces, and change events by
   timestamps and operation IDs when present.
5. Assess the hypothesis as:
   - **Supported**
   - **Contradicted**
   - **Mixed**
   - **Untestable with available telemetry**
6. Cite `evidenceId` and `queryId` for each material observation.
7. Separate observed facts from inference. Temporal proximity does not establish
   causation.
8. Recommend the smallest read-only next checks that would reduce uncertainty.

## Required response

```markdown
# Suspected-cause investigation

## Scope
- Service:
- Window:
- Hypothesis:
- Query status:

## Observed evidence
- ... (AI-REQUESTS-R1, AI-REQUESTS)

## Hypothesis assessment
**Supported | Contradicted | Mixed | Untestable**

## Contradictory evidence
- ...

## Unknowns and telemetry gaps
- ...

## Next checks
1. ...

## Incident command brief
...
```

Never modify ServiceNow, Azure, or AKS during this workflow.

