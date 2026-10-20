"""Authorization tests for administrative configuration endpoints."""

import base64
import json
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.auth import get_authenticated_user_id, require_admin_user, require_authenticated_user
from app.config import Settings


def _request(roles: list[str] | None) -> MagicMock:
    request = MagicMock()
    if roles is None:
        request.headers = {}
    else:
        principal = {"userId": "operator", "userRoles": roles}
        encoded = base64.b64encode(json.dumps(principal).encode()).decode()
        request.headers = {"x-ms-client-principal": encoded}
    return request


@pytest.mark.asyncio
async def test_admin_auth_disabled_uses_local_admin_identity():
    request = _request(None)
    principal = await require_admin_user(request, Settings(admin_auth_enabled="false"))
    assert "admin" in principal["userRoles"]
    assert get_authenticated_user_id(request) == "default-user"


@pytest.mark.asyncio
async def test_admin_auth_requires_admin_role_when_enabled():
    with pytest.raises(HTTPException) as exc:
        await require_admin_user(
            _request(["authenticated"]),
            Settings(admin_auth_enabled="true"),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_auth_accepts_admin_role_case_insensitively():
    principal = await require_admin_user(
        _request(["authenticated", "Admin"]),
        Settings(admin_auth_enabled="true"),
    )
    assert principal["userId"] == "operator"


@pytest.mark.asyncio
async def test_authenticated_user_requires_stable_user_id():
    principal = base64.b64encode(json.dumps({"userRoles": ["authenticated"]}).encode()).decode()
    request = MagicMock()
    request.headers = {"x-ms-client-principal": principal}

    with pytest.raises(HTTPException) as exc:
        await require_authenticated_user(request, Settings(admin_auth_enabled="true"))

    assert exc.value.status_code == 401
