---
name: incident-updates
description: Draft and post incident updates with exact, single-use human approval and post-write verification
enabled: true
---

# Approved incident updates

Use this skill when the user asks to draft, add, post, send, assign, or change an
incident update in ServiceNow or Microsoft 365.

## Universal write gate

Every mutation follows this sequence:

1. **Read current state.** Fetch the target and relevant recent history.
2. **Present one exact proposal.** Include:
   - system and exact target;
   - every changed field;
   - final wording;
   - internal/public visibility or exact audience;
   - expected receipt and verification query.
3. **Confirm.** Call `ask_user` and wait.
4. **Invalidate on change.** Any edit to target, fields, wording, visibility, or
   audience requires a new confirmation.
5. **Execute one write.** Approval cannot authorize a batch.
6. **Report receipt.** Return the record/message ID and tool status.
7. **Verify.** Re-read the target. Report `verified`, `unverified`, or `failed`.

Never claim success from the proposed text or from a missing/ambiguous receipt.

## ServiceNow

Use read tools before writes:

- `servicenow_get_major_incident`
- `servicenow_list_incident_updates`

Allowed updates:

1. Call `servicenow_prepare_update` with one exact action.
2. Show the returned current state and pass its `approvalQuestion` to
   `ask_user` unchanged.
3. Only after an affirmative response, call `servicenow_execute_update` with
   the returned `approvalId`.
4. Report the persisted action receipt and its `verified`, `unverified`, or
   `failed` status.

Public notes require the exact caller-visible wording to be shown. State changes
and assignments must include a reason. Never call the execute tool without the
prepare/read and approval sequence.

## Microsoft 365

Microsoft 365 is read-only in v1. Draft the proposed communication in the
response, but do not create drafts, send mail, create events, or post to Teams.
Tell the incident commander that ServiceNow is the only mutable system currently
enabled.
