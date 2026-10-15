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

- `servicenow_get_ticket`
- `servicenow_list_work_notes`

Allowed writes after exact confirmation:

- `servicenow_add_work_note`
- `servicenow_update_ticket_state`
- `servicenow_assign_ticket`
- `servicenow_create_incident`

Public notes require the exact caller-visible wording to be shown. State changes
must include a reason. Verify with `servicenow_get_ticket` and, for notes,
`servicenow_list_work_notes`.

## Microsoft 365

Creating a draft is a write. Sending it is a separate write:

1. Show exact sender, recipients, subject, body, importance, and thread target.
2. Confirm before `m365_draft_message`.
3. Report the draft ID and verify it with `m365_get_message`.
4. Show the verified draft again and request a separate confirmation before
   `m365_send_message`.
5. Verify that the message is in Sent Items.

If a Teams/channel post tool is unavailable, say so. Do not substitute an email,
claim a post occurred, or invent a receipt.
