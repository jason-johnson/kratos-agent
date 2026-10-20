---
name: incident-command-brief
description: Build or refresh a live incident command brief from authoritative records and cited technical evidence
enabled: true
---

# Live incident command brief

Use this skill when an incident commander asks for a brief, situation report,
timeline, status refresh, hypothesis board, decision log, action tracker, or
handoff.

## Evidence collection

1. Fetch the incident with `servicenow_get_major_incident`.
2. Fetch chronological notes with `servicenow_list_incident_updates`.
3. Use `app-insights-investigation` when a technical hypothesis, affected service,
   and bounded time window are available.
4. Use **m365-incident-context** only when responder availability or existing
   communications are relevant.
5. Treat every failed, partial, denied, empty, or stale result as a named gap.

ServiceNow is authoritative for declared incident state and ownership. Telemetry
is authoritative for its observations. Highlight disagreement; do not silently
choose one source.

## Required structure

```markdown
# Incident command brief — {incident ID}

**Last refreshed:** {timestamp or unknown}

## Declared state and impact
- Severity / state:
- Incident commander:
- Affected services:
- User / region impact:
- Started:

## Evidence-backed timeline
| Time | Observation or decision | Type | Source |
|---|---|---|---|

## Hypotheses
| Hypothesis | Assessment | Supporting evidence | Counter-evidence | Owner |
|---|---|---|---|---|

## Decisions
| Time | Decision | Rationale | Owner | Source |
|---|---|---|---|---|

## Actions
| Action | Owner | Status | Due | Verification signal |
|---|---|---|---|---|

## Unknowns, contradictions, and stale information
- ...

## Next command checkpoint
- ...
```

## Rules

- Cite ServiceNow record/note IDs and Application Insights evidence/query IDs.
- Label inferred timeline entries as inference.
- Never invent owners, decisions, action status, due times, or impact.
- Mark an action complete only when verification evidence is present.
- Do not mutate any system while building or refreshing the brief.
