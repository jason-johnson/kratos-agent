---
name: m365-incident-context
description: Read responder availability and existing Microsoft 365 incident communications without mutating them
enabled: true
---

# Microsoft 365 incident context

Use this skill to understand responder identity, presence, out-of-office state,
calendar constraints, relevant mail, and existing Teams/chat context.

## Read routing

- Resolve a responder: `m365_get_user`
- Presence and out-of-office: `m365_get_user_presence`
- Relevant mail: `m365_search_messages`, then `m365_get_message` or
  `m365_get_thread`
- Calendar context: `m365_list_events`, then `m365_get_event`
- Existing chat context: `m365_list_chats`, then `m365_search_chat_messages`

## Rules

- Use the signed-in user's delegated permissions in production.
- Minimize personal data. Return only context relevant to incident coordination.
- Cite message, event, chat, and user IDs from tool results.
- Treat denied access as a permission boundary, not as evidence that data does
  not exist.
- This skill is read-only. Use **incident-updates** for any draft, send, event
  creation, or channel post.
