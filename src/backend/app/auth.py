"""Authentication dependencies for user and administrative endpoints.

Container Apps uses the platform-provided ``x-ms-client-principal`` header.
AKS uses an ingress authentication proxy and keeps the backend ClusterIP
unreachable except through that ingress path.

Auth enforcement is opt-in via the ADMIN_AUTH_ENABLED env var.  Set it to
"true" after configuring Easy Auth on your Container App or SWA.
"""

import base64
import json
import logging

from fastapi import Depends, HTTPException, Request

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _get_client_principal(request: Request) -> dict | None:
    """Decode the SWA / Easy Auth client-principal header if present."""
    header = request.headers.get("x-ms-client-principal")
    if not header:
        return None
    try:
        decoded = base64.b64decode(header)
        return json.loads(decoded)
    except Exception:
        logger.warning("Failed to decode x-ms-client-principal header")
        return None


def _get_trusted_proxy_principal(request: Request, settings: Settings) -> dict | None:
    if not settings.trusted_auth_proxy_enabled:
        return None
    user_id = request.headers.get("x-auth-request-user", "").strip()
    if not user_id:
        return None
    groups = {group.strip() for group in request.headers.get("x-auth-request-groups", "").split(",") if group.strip()}
    roles = ["authenticated"]
    if settings.trusted_auth_proxy_admin_group and settings.trusted_auth_proxy_admin_group in groups:
        roles.append("admin")
    return {
        "userId": user_id,
        "userDetails": request.headers.get("x-auth-request-email", "").strip() or user_id,
        "userRoles": roles,
    }


_settings_dep = Depends(get_settings)


async def require_authenticated_user(
    request: Request,
    settings: Settings = _settings_dep,
) -> dict:
    """FastAPI dependency that enforces authentication.

    Returns the decoded client-principal dict on success.
    When admin_auth_enabled is not "true", returns a stub identity
    (auth is opt-in until Easy Auth is configured).
    """
    if settings.admin_auth_enabled.lower() != "true":
        principal = {"userId": "default-user", "userRoles": ["authenticated", "admin"]}
        request.state.authenticated_principal = principal
        return principal

    principal = (
        _get_trusted_proxy_principal(request, settings)
        if settings.trusted_auth_proxy_enabled
        else _get_client_principal(request)
    )
    if not principal:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = principal.get("userId")
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(status_code=401, detail="Authenticated principal has no stable user ID")
    request.state.authenticated_principal = principal
    return principal


async def require_admin_user(
    request: Request,
    settings: Settings = _settings_dep,
) -> dict:
    """Require an authenticated principal carrying the ``admin`` app role."""
    principal = await require_authenticated_user(request, settings)
    roles = principal.get("userRoles") or principal.get("roles") or []
    if not isinstance(roles, list) or "admin" not in {str(role).lower() for role in roles}:
        raise HTTPException(status_code=403, detail="Administrator role required")
    request.state.admin_principal = principal
    return principal


def get_admin_actor(request: Request) -> str:
    """Return a stable, non-secret audit identity for the active administrator."""
    principal = getattr(request.state, "admin_principal", {})
    return str(principal.get("userDetails") or principal.get("userId") or "unknown")


def get_authenticated_user_id(request: Request) -> str:
    principal = getattr(request.state, "authenticated_principal", {})
    user_id = principal.get("userId") if isinstance(principal, dict) else None
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(status_code=401, detail="Authentication context is unavailable")
    return user_id
