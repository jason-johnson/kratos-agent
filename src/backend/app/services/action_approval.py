"""Exact, single-use approval state for mutating agent tools."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

_APPROVAL_MARKER = re.compile(r"\[approval:([0-9a-f-]{36})\]")
_APPROVED_ANSWERS = {"approve", "approved", "confirm", "confirmed", "yes", "proceed"}


@dataclass
class ActionProposal:
    approval_id: str
    conversation_id: str
    system: str
    target: str
    action: str
    payload: dict[str, Any]
    current_state: dict[str, Any]
    approval_question: str
    fingerprint: str
    expires_at: datetime
    approved: bool = False
    consumed: bool = False


class ActionApprovalStore:
    """Keeps an exact proposal in memory for the duration of an active turn."""

    def __init__(self) -> None:
        self._proposals: dict[str, ActionProposal] = {}

    def prepare(
        self,
        *,
        conversation_id: str,
        system: str,
        target: str,
        action: str,
        payload: dict[str, Any],
        current_state: dict[str, Any],
        display: str,
    ) -> ActionProposal:
        approval_id = str(uuid.uuid4())
        canonical = json.dumps(
            {
                "conversationId": conversation_id,
                "system": system,
                "target": target,
                "action": action,
                "payload": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        question = (
            f"[approval:{approval_id}] Approve this single ServiceNow action?\n\n"
            f"{display}\n\n"
            f"Proposal fingerprint: {fingerprint[:16]}\n"
            "Reply Approve to authorize exactly this action, or Deny."
        )
        proposal = ActionProposal(
            approval_id=approval_id,
            conversation_id=conversation_id,
            system=system,
            target=target,
            action=action,
            payload=payload,
            current_state=current_state,
            approval_question=question,
            fingerprint=fingerprint,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        self._proposals[approval_id] = proposal
        return proposal

    def record_response(self, conversation_id: str, question: str, answer: str) -> str | None:
        """Approve only an unchanged generated question answered affirmatively."""
        marker = _APPROVAL_MARKER.search(question)
        if not marker:
            return None
        proposal = self._proposals.get(marker.group(1))
        if (
            proposal is None
            or proposal.conversation_id != conversation_id
            or proposal.approval_question != question
            or proposal.expires_at <= datetime.now(UTC)
            or proposal.consumed
        ):
            return None
        if answer.strip().lower().rstrip(".!") not in _APPROVED_ANSWERS:
            return None
        proposal.approved = True
        return proposal.approval_id

    def consume(self, conversation_id: str, approval_id: str) -> ActionProposal | None:
        proposal = self._proposals.get(approval_id)
        if (
            proposal is None
            or proposal.conversation_id != conversation_id
            or not proposal.approved
            or proposal.consumed
            or proposal.expires_at <= datetime.now(UTC)
        ):
            return None
        proposal.consumed = True
        return proposal

    def clear_conversation(self, conversation_id: str) -> None:
        self._proposals = {
            approval_id: proposal
            for approval_id, proposal in self._proposals.items()
            if proposal.conversation_id != conversation_id
        }


action_approval_store = ActionApprovalStore()
