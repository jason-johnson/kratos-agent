"""Operational context endpoint for incident-command safety cues."""

from typing import Literal

from fastapi import APIRouter, Depends

from app.auth import require_authenticated_user
from app.config import get_settings
from app.models import OperationalContext

router = APIRouter(dependencies=[Depends(require_authenticated_user)])


EnvironmentClass = Literal["local", "non-production", "production", "unknown"]


def _environment_class(environment: str, is_local_mode: bool) -> EnvironmentClass:
    if is_local_mode:
        return "local"
    normalized = environment.strip().lower()
    if normalized in {"prod", "production"} or normalized.startswith("prod-"):
        return "production"
    if normalized in {"dev", "development", "test", "testing", "stage", "staging", "sandbox", "non-production"}:
        return "non-production"
    return "unknown"


def _evidence_target(resource_id: str, workspace_id: str) -> tuple[str, bool]:
    if resource_id.strip():
        name = resource_id.rstrip("/").rsplit("/", 1)[-1]
        return (f"Application Insights: {name}", True)
    if workspace_id.strip():
        return ("Configured Log Analytics workspace", True)
    return ("Not configured", False)


@router.get("", response_model=OperationalContext)
async def get_operational_context() -> OperationalContext:
    """Return non-secret runtime context and safety boundaries."""
    settings = get_settings()
    evidence_target, configured = _evidence_target(
        settings.application_insights_resource_id,
        settings.application_insights_workspace_id,
    )
    return OperationalContext(
        environment=settings.environment,
        environmentClass=_environment_class(settings.environment, settings.is_local_mode),
        modelDeployment=settings.foundry_model_deployment or "Not configured",
        evidenceTarget=evidence_target,
        evidenceTargetConfigured=configured,
    )
