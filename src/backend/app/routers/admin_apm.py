"""Admin API for managing APM (Agent Package Manager) dependencies per use-case."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import get_admin_actor, require_admin_user
from app.constants import PRIMARY_USE_CASE
from app.services.apm_service import ApmCommandResult, ApmDependency, ApmError, ApmMcpServer, ApmService
from app.services.runtime_config_service import RuntimeConfigService
from app.services.skill_registry import SkillRegistry

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin_user)])


# ─── Request / response models ────────────────────────────────────────────────


class ApmDependencyOut(BaseModel):
    """Serialised view of an :class:`ApmDependency` for the admin UI."""

    name: str
    ref: str | None = None
    resolved: str | None = None
    source: str


class ApmMcpServerOut(BaseModel):
    """Serialised view of an APM-declared MCP server."""

    name: str
    transport: str
    command: str | None = None
    args: list[str] = []
    url: str | None = None
    env: dict[str, str] = {}
    registry: bool = False


class ApmStatusResponse(BaseModel):
    """Aggregate snapshot returned from ``GET /admin/use-cases/{uc}/apm``."""

    dependencies: list[ApmDependencyOut]
    materialised_skill_dirs: list[str]
    mcp_servers: list[ApmMcpServerOut] = []
    version: str


class ApmCommandResponse(BaseModel):
    """Result of a mutating APM command plus the refreshed dependency list."""

    success: bool
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float
    dependencies: list[ApmDependencyOut]


class InstallRequest(BaseModel):
    package: str
    ref: str | None = None
    dev: bool = False


class InstallMcpRequest(BaseModel):
    """Install an MCP server into ``apm.yml`` via ``apm mcp install``.

    For ``stdio`` transports supply ``command`` and optional ``args``.
    For ``http`` / ``sse`` transports supply ``url``.
    """

    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = []
    url: str | None = None
    env: dict[str, str] = {}


class UpdateRequest(BaseModel):
    package: str | None = None


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _get_apm_service(request: Request) -> ApmService:
    """Resolve the shared :class:`ApmService` from application state."""
    service: ApmService | None = getattr(request.app.state, "apm_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="APM service is not configured")
    return service


def _to_dep_out(dep: ApmDependency) -> ApmDependencyOut:
    return ApmDependencyOut(
        name=dep.name,
        ref=dep.ref,
        resolved=dep.resolved,
        source=dep.source,
    )


def _to_mcp_out(server: ApmMcpServer) -> ApmMcpServerOut:
    return ApmMcpServerOut(
        name=server.name,
        transport=server.transport,
        command=server.command,
        args=list(server.args),
        url=server.url,
        env=dict(server.env),
        registry=server.registry,
    )


def _apm_error_to_http(exc: ApmError) -> HTTPException:
    """Translate an :class:`ApmError` into the admin HTTP error convention."""
    message = str(exc)
    status = 404 if message.startswith("Use case") else 500
    detail: dict[str, object] = {"detail": message}
    if exc.result is not None:
        stderr_tail = exc.result.stderr[-2048:] if exc.result.stderr else ""
        detail["stderr"] = stderr_tail
        detail["returncode"] = exc.result.returncode
    return HTTPException(status_code=status, detail=detail)


async def _reload_use_case(request: Request, use_case: str) -> None:
    """Reload the registry for ``use_case`` and reset any cached agent sessions.

    Mirrors the admin-MCP pattern: after a successful mutation we refresh the
    in-memory :class:`SkillRegistry` so newly materialised APM skills show up
    immediately, then drop cached SDK sessions so the next chat picks them up.
    """
    registries: dict[str, SkillRegistry] = getattr(request.app.state, "registries", {})
    registry = registries.get(use_case)
    blob_service = getattr(request.app.state, "blob_skill_service", None)
    apm_service = getattr(request.app.state, "apm_service", None)

    if registry is not None:
        await registry.load(use_case, blob_service, apm_service=apm_service)

    copilot_agent = getattr(request.app.state, "copilot_agent", None)
    if copilot_agent is not None:
        await copilot_agent.reset_sessions_for_use_case(use_case)

    runtime_config: RuntimeConfigService = request.app.state.runtime_config_service
    await runtime_config.record_version(
        use_case,
        action="apm.update",
        actor=get_admin_actor(request),
        reset_sessions=False,
    )


async def _build_command_response(
    apm_service: ApmService,
    use_case: str,
    result: ApmCommandResult,
) -> ApmCommandResponse:
    """Combine a raw command result with the post-op dependency snapshot."""
    try:
        deps = await apm_service.list_dependencies(use_case)
    except ApmError:
        deps = []
    return ApmCommandResponse(
        success=result.success,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        dependencies=[_to_dep_out(d) for d in deps],
    )


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("", response_model=ApmStatusResponse)
async def get_apm_status(request: Request) -> ApmStatusResponse:
    """Return the ARE APM dependency snapshot and CLI version."""
    apm_service = _get_apm_service(request)
    try:
        deps = await apm_service.list_dependencies(PRIMARY_USE_CASE)
        skill_dirs = await apm_service.list_materialised_skill_dirs(PRIMARY_USE_CASE)
        mcp_servers = await apm_service.list_mcp_servers(PRIMARY_USE_CASE)
    except ApmError as exc:
        raise _apm_error_to_http(exc) from exc

    try:
        version = await apm_service.version()
    except ApmError as exc:
        version = f"unavailable: {exc}"

    return ApmStatusResponse(
        dependencies=[_to_dep_out(d) for d in deps],
        materialised_skill_dirs=[str(p) for p in skill_dirs],
        mcp_servers=[_to_mcp_out(s) for s in mcp_servers],
        version=version,
    )


@router.post("/install", response_model=ApmCommandResponse)
async def install_package(
    body: InstallRequest,
    request: Request,
) -> ApmCommandResponse:
    """Install an APM package for the ARE and refresh the registry."""
    apm_service = _get_apm_service(request)
    try:
        result = await apm_service.install(
            PRIMARY_USE_CASE,
            package=body.package,
            ref=body.ref,
            dev=body.dev,
        )
    except ApmError as exc:
        raise _apm_error_to_http(exc) from exc

    await _reload_use_case(request, PRIMARY_USE_CASE)
    logger.info("ARE APM install %s succeeded", body.package)
    return await _build_command_response(apm_service, PRIMARY_USE_CASE, result)


@router.delete("/mcp/{name}", response_model=ApmCommandResponse)
async def uninstall_mcp_server(
    name: str,
    request: Request,
) -> ApmCommandResponse:
    """Remove an MCP server from ``apm.yml`` and refresh the registry."""
    apm_service = _get_apm_service(request)
    try:
        result = await apm_service.uninstall_mcp_server(PRIMARY_USE_CASE, name)
    except ApmError as exc:
        raise _apm_error_to_http(exc) from exc

    await _reload_use_case(request, PRIMARY_USE_CASE)
    logger.info("ARE APM MCP uninstall '%s' succeeded", name)
    return await _build_command_response(apm_service, PRIMARY_USE_CASE, result)


@router.delete("/{package:path}", response_model=ApmCommandResponse)
async def uninstall_package(
    package: str,
    request: Request,
) -> ApmCommandResponse:
    """Uninstall an APM package (``owner/repo``) and refresh the registry."""
    apm_service = _get_apm_service(request)
    try:
        result = await apm_service.uninstall(PRIMARY_USE_CASE, package)
    except ApmError as exc:
        raise _apm_error_to_http(exc) from exc

    await _reload_use_case(request, PRIMARY_USE_CASE)
    logger.info("ARE APM uninstall %s succeeded", package)
    return await _build_command_response(apm_service, PRIMARY_USE_CASE, result)


@router.post("/sync", response_model=ApmCommandResponse)
async def sync_packages(request: Request) -> ApmCommandResponse:
    """Re-materialise declared APM dependencies and refresh the registry."""
    apm_service = _get_apm_service(request)
    try:
        result = await apm_service.sync(PRIMARY_USE_CASE)
    except ApmError as exc:
        raise _apm_error_to_http(exc) from exc

    await _reload_use_case(request, PRIMARY_USE_CASE)
    logger.info("ARE APM sync succeeded")
    return await _build_command_response(apm_service, PRIMARY_USE_CASE, result)


@router.post("/update", response_model=ApmCommandResponse)
async def update_packages(
    body: UpdateRequest,
    request: Request,
) -> ApmCommandResponse:
    """Update one or all APM packages for a use-case and refresh the registry."""
    apm_service = _get_apm_service(request)
    try:
        result = await apm_service.update(PRIMARY_USE_CASE, package=body.package)
    except ApmError as exc:
        raise _apm_error_to_http(exc) from exc

    await _reload_use_case(request, PRIMARY_USE_CASE)
    logger.info("ARE APM update %s succeeded", body.package or "<all>")
    return await _build_command_response(apm_service, PRIMARY_USE_CASE, result)


@router.post("/mcp", response_model=ApmCommandResponse)
async def install_mcp_server(
    body: InstallMcpRequest,
    request: Request,
) -> ApmCommandResponse:
    """Declare an MCP server in ``apm.yml`` and merge it into the registry."""
    apm_service = _get_apm_service(request)
    try:
        result = await apm_service.install_mcp_server(
            PRIMARY_USE_CASE,
            name=body.name,
            transport=body.transport,
            command=body.command,
            args=body.args,
            url=body.url,
            env=body.env or None,
        )
    except ApmError as exc:
        raise _apm_error_to_http(exc) from exc

    await _reload_use_case(request, PRIMARY_USE_CASE)
    logger.info("ARE APM MCP install '%s' succeeded", body.name)
    return await _build_command_response(apm_service, PRIMARY_USE_CASE, result)
