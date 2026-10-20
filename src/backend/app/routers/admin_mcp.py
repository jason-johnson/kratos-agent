"""Admin API for managing MCP server configs — stored as .mcp.json per use-case in blob."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_admin_actor, require_admin_user
from app.constants import PRIMARY_USE_CASE
from app.models import MCPConfigResponse, MCPConfigUpdate
from app.services.runtime_config_service import RuntimeConfigService

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin_user)])


@router.get("", response_model=MCPConfigResponse)
async def get_mcp_config(request: Request) -> MCPConfigResponse:
    """Return the current ARE MCP server configuration."""
    registries: dict = request.app.state.registries
    registry = registries.get(PRIMARY_USE_CASE)
    if registry is None:
        raise HTTPException(status_code=503, detail="ARE skill registry is unavailable")
    return MCPConfigResponse(
        servers=registry.mcp_servers,
        sources=getattr(registry, "mcp_sources", {}),
    )


@router.put("", response_model=MCPConfigResponse)
async def update_mcp_config(body: MCPConfigUpdate, request: Request) -> MCPConfigResponse:
    """Update the ARE MCP server configuration and clear active sessions."""
    registries: dict = request.app.state.registries
    registry = registries.get(PRIMARY_USE_CASE)
    if registry is None:
        raise HTTPException(status_code=503, detail="ARE skill registry is unavailable")

    await registry.update_mcp_servers(body.servers)
    runtime_config: RuntimeConfigService = request.app.state.runtime_config_service
    await runtime_config.record_version(
        PRIMARY_USE_CASE,
        action="mcp.update",
        actor=get_admin_actor(request),
    )

    logger.info("ARE MCP config updated: %d servers", len(body.servers))
    return MCPConfigResponse(
        servers=body.servers,
        sources=getattr(registry, "mcp_sources", {}),
    )
