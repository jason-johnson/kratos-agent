"""Persisted, provenance-bound incident command brief model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

IncidentNumber = str


class BriefSourceSnapshot(BaseModel):
    source: Literal["ServiceNow", "ApplicationInsights", "AzureActivity", "MicrosoftGraph", "Kubernetes"]
    reference: str = Field(min_length=1, max_length=200)
    observed_at: datetime
    status: Literal["success", "partial", "failed"]
    stale_after_minutes: int = Field(default=15, ge=1, le=1440)


class BriefTimelineEntry(BaseModel):
    timestamp: datetime
    summary: str = Field(min_length=1, max_length=1000)
    kind: Literal["observation", "decision", "action", "communication", "inference"]
    source_refs: list[str] = Field(min_length=1, max_length=20)


class BriefHypothesis(BaseModel):
    hypothesis: str = Field(min_length=1, max_length=1000)
    assessment: Literal["supported", "contradicted", "mixed", "untestable"]
    confidence: Literal["low", "medium", "high"]
    supporting_refs: list[str] = Field(default_factory=list, max_length=20)
    counter_evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    owner: str = Field(default="unassigned", max_length=200)

    @model_validator(mode="after")
    def assessed_hypotheses_require_evidence(self) -> BriefHypothesis:
        if self.assessment in {"supported", "mixed"} and not self.supporting_refs:
            raise ValueError("supported or mixed hypotheses require supporting_refs")
        if self.assessment in {"contradicted", "mixed"} and not self.counter_evidence_refs:
            raise ValueError("contradicted or mixed hypotheses require counter_evidence_refs")
        return self


class BriefDecision(BaseModel):
    timestamp: datetime
    decision: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=2000)
    owner: str = Field(default="unassigned", max_length=200)
    source_refs: list[str] = Field(min_length=1, max_length=20)


class BriefAction(BaseModel):
    action: str = Field(min_length=1, max_length=1000)
    owner: str = Field(default="unassigned", max_length=200)
    status: Literal["proposed", "in_progress", "blocked", "completed"]
    due_at: datetime | None = None
    verification_signal: str = Field(min_length=1, max_length=1000)
    verification_evidence_refs: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def completed_actions_require_verification(self) -> BriefAction:
        if self.status == "completed" and not self.verification_evidence_refs:
            raise ValueError("completed actions require verification_evidence_refs")
        return self


class BriefQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    owner: str = Field(default="unassigned", max_length=200)
    status: Literal["open", "answered", "blocked"]
    source_refs: list[str] = Field(default_factory=list, max_length=20)


class IncidentCommandBriefContent(BaseModel):
    incident_number: IncidentNumber = Field(pattern=r"^INC[0-9]{4,12}$")
    declared_state: str = Field(default="unknown", max_length=200)
    severity: str = Field(default="unknown", max_length=100)
    commander: str = Field(default="unassigned", max_length=200)
    affected_services: list[str] = Field(default_factory=list, max_length=50)
    user_impact: str = Field(default="unknown", max_length=2000)
    region_impact: str = Field(default="unknown", max_length=1000)
    started_at: datetime | None = None
    timeline: list[BriefTimelineEntry] = Field(default_factory=list, max_length=100)
    hypotheses: list[BriefHypothesis] = Field(default_factory=list, max_length=25)
    decisions: list[BriefDecision] = Field(default_factory=list, max_length=50)
    actions: list[BriefAction] = Field(default_factory=list, max_length=50)
    questions: list[BriefQuestion] = Field(default_factory=list, max_length=50)
    source_snapshots: list[BriefSourceSnapshot] = Field(default_factory=list, max_length=50)
    contradictions: list[str] = Field(default_factory=list, max_length=50)
    telemetry_gaps: list[str] = Field(default_factory=list, max_length=50)
    next_checkpoint: str = Field(default="unknown", max_length=1000)


class IncidentBriefGetParams(BaseModel):
    incident_number: IncidentNumber = Field(pattern=r"^INC[0-9]{4,12}$")


class IncidentBriefUpdateParams(BaseModel):
    expected_revision: int = Field(ge=0)
    brief: IncidentCommandBriefContent


def brief_document(
    content: IncidentCommandBriefContent,
    *,
    revision: int,
    conversation_id: str,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": f"incident-brief:{content.incident_number}",
        "kind": "incident-command-brief",
        "revision": revision,
        "conversationId": conversation_id,
        "lastRefreshedAt": now.isoformat(),
        "brief": content.model_dump(mode="json"),
    }


def with_staleness(document: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    evaluated_at = now or datetime.now(UTC)
    source_status: list[dict[str, Any]] = []
    for snapshot in document.get("brief", {}).get("source_snapshots", []):
        observed_at = datetime.fromisoformat(str(snapshot["observed_at"]).replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        age_seconds = max(0, int((evaluated_at - observed_at.astimezone(UTC)).total_seconds()))
        threshold = int(snapshot["stale_after_minutes"]) * 60
        source_status.append(
            {
                "source": snapshot["source"],
                "reference": snapshot["reference"],
                "status": snapshot["status"],
                "ageSeconds": age_seconds,
                "stale": age_seconds > threshold,
            }
        )
    return {
        **document,
        "staleness": {
            "evaluatedAt": evaluated_at.isoformat(),
            "stale": any(item["stale"] or item["status"] != "success" for item in source_status),
            "sources": source_status,
        },
    }
