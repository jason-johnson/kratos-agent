"""Admin API for managing skills — CRUD operations backed by Blob Storage."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import get_admin_actor, require_admin_user
from app.constants import PRIMARY_USE_CASE
from app.models import (
    RuntimeConfigVersion,
    RuntimeConfigVersionList,
    SkillCreate,
    SkillFile,
    SkillFileList,
    SkillFileUpsert,
    SkillList,
    SkillResponse,
    SkillUpdate,
)
from app.services.runtime_config_service import RuntimeConfigService
from app.services.skill_registry import SkillMetadata, SkillRegistry

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin_user)])


def _get_registry(request: Request) -> SkillRegistry:
    """Resolve the ARE skill registry."""
    registries = getattr(request.app.state, "registries", {})
    registry = registries.get(PRIMARY_USE_CASE)
    if registry is None:
        # Fall back to the legacy single registry
        registry = getattr(request.app.state, "skill_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="ARE skill registry is unavailable")
    return registry


def _get_runtime_config_service(request: Request) -> RuntimeConfigService:
    service = getattr(request.app.state, "runtime_config_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Runtime configuration service is unavailable")
    return service


async def _record_mutation(request: Request, action: str) -> RuntimeConfigVersion:
    marker = await _get_runtime_config_service(request).record_version(
        PRIMARY_USE_CASE,
        action=action,
        actor=get_admin_actor(request),
    )
    return RuntimeConfigVersion(**marker)


def _count_skill_files(skill: SkillMetadata) -> int:
    """Count non-SKILL.md files in a skill's local directory."""
    if not skill.local_path:
        return 0
    skill_dir = Path(skill.local_path)
    if not skill_dir.exists():
        return 0
    return sum(1 for f in skill_dir.rglob("*") if f.is_file() and f.name != "SKILL.md")


def _to_response(skill: SkillMetadata) -> SkillResponse:
    return SkillResponse(
        name=skill.name,
        description=skill.description,
        enabled=skill.enabled,
        instructions=skill.instructions,
        toolName=skill.tool_name or skill.name.replace("-", "_"),
        fileCount=_count_skill_files(skill),
        source=skill.source,
    )


@router.get("", response_model=SkillList)
async def list_skills(request: Request) -> SkillList:
    """List all registered ARE skills."""
    registry = _get_registry(request)
    skills = sorted(registry.skills.values(), key=lambda s: s.name)
    return SkillList(skills=[_to_response(s) for s in skills])


@router.get("/versions", response_model=RuntimeConfigVersionList)
async def list_runtime_config_versions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
) -> RuntimeConfigVersionList:
    """List immutable skill/prompt/MCP configuration versions."""
    versions = await _get_runtime_config_service(request).list_versions(PRIMARY_USE_CASE, limit)
    return RuntimeConfigVersionList(versions=[RuntimeConfigVersion(**version) for version in versions])


@router.post("/versions/{version}/rollback", response_model=RuntimeConfigVersion)
async def rollback_runtime_config(
    version: str,
    request: Request,
) -> RuntimeConfigVersion:
    """Restore a prior version and publish the rollback as a new version."""
    try:
        marker = await _get_runtime_config_service(request).rollback(
            PRIMARY_USE_CASE,
            version,
            get_admin_actor(request),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Runtime config version '{version}' not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RuntimeConfigVersion(**marker)


@router.get("/{skill_name}", response_model=SkillResponse)
async def get_skill(
    skill_name: str,
    request: Request,
) -> SkillResponse:
    """Get a single skill by name."""
    registry = _get_registry(request)
    skill = registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    return _to_response(skill)


@router.post("", response_model=SkillResponse, status_code=201)
async def create_skill(
    body: SkillCreate,
    request: Request,
) -> SkillResponse:
    """Create a new skill definition."""
    registry = _get_registry(request)

    if registry.get_skill(body.name):
        raise HTTPException(status_code=409, detail=f"Skill '{body.name}' already exists")

    skill = SkillMetadata(
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        instructions=body.instructions,
        tool_name=body.name.replace("-", "_"),
    )
    await registry.add_skill(skill)
    await _record_mutation(request, f"skill.create:{body.name}")
    logger.info("Admin created skill: %s", body.name)
    return _to_response(skill)


@router.patch("/{skill_name}", response_model=SkillResponse)
async def update_skill(
    skill_name: str,
    body: SkillUpdate,
    request: Request,
) -> SkillResponse:
    """Update an existing skill (partial update)."""
    registry = _get_registry(request)

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    skill = await registry.update_skill(skill_name, updates)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    await _record_mutation(request, f"skill.update:{skill_name}")
    logger.info("Admin updated skill: %s fields=%s", skill_name, list(updates.keys()))
    return _to_response(skill)


@router.delete("/{skill_name}", status_code=204)
async def delete_skill(
    skill_name: str,
    request: Request,
) -> None:
    """Delete a skill definition."""
    registry = _get_registry(request)

    removed = await registry.remove_skill(skill_name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    await _record_mutation(request, f"skill.delete:{skill_name}")
    logger.info("Admin deleted skill: %s", skill_name)


# ─── Skill file management ────────────────────────────────────────────────────


def _validate_file_path(file_path: str) -> None:
    """Reject paths with directory traversal or absolute components."""
    if (
        not file_path
        or "\\" in file_path
        or file_path.startswith("/")
        or any(part in {"", ".", ".."} for part in file_path.split("/"))
    ):
        raise HTTPException(status_code=400, detail="Invalid file path")


@router.get("/{skill_name}/files", response_model=SkillFileList)
async def list_skill_files(
    skill_name: str,
    request: Request,
) -> SkillFileList:
    """List all non-SKILL.md files for a skill, including their content."""
    registry = _get_registry(request)
    skill = registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    raw_files = registry.list_skill_files(skill_name)
    result: list[SkillFile] = []
    skill_dir = Path(skill.local_path) if skill.local_path else None
    for entry in raw_files:
        content = ""
        if skill_dir:
            f = skill_dir / entry["path"]
            try:
                content = f.read_text(errors="replace")
            except Exception:
                content = ""
        result.append(SkillFile(path=entry["path"], name=entry["name"], content=content))
    return SkillFileList(files=result)


@router.put("/{skill_name}/files/{file_path:path}", status_code=204)
async def upsert_skill_file(
    skill_name: str,
    file_path: str,
    body: SkillFileUpsert,
    request: Request,
) -> None:
    """Upload or update a file within a skill folder."""
    _validate_file_path(file_path)
    registry = _get_registry(request)
    skill = registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    await registry.upsert_skill_file(skill_name, file_path, body.content.encode())
    await _record_mutation(request, f"skill.file.upsert:{skill_name}/{file_path}")
    logger.info("Admin upserted skill file: %s/%s", skill_name, file_path)


@router.delete("/{skill_name}/files/{file_path:path}", status_code=204)
async def delete_skill_file(
    skill_name: str,
    file_path: str,
    request: Request,
) -> None:
    """Delete a file from a skill folder."""
    _validate_file_path(file_path)
    registry = _get_registry(request)
    if not registry.get_skill(skill_name):
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    await registry.remove_skill_file(skill_name, file_path)
    await _record_mutation(request, f"skill.file.delete:{skill_name}/{file_path}")
    logger.info("Admin deleted skill file: %s/%s", skill_name, file_path)
