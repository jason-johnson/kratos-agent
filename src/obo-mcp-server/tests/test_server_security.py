from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from starlette.requests import Request

SERVICE_DIR = Path(__file__).parents[1]


@pytest.fixture
def server(monkeypatch):
    class FakeFastMCP:
        def __init__(self, **kwargs):
            pass

        def tool(self):
            return lambda function: function

    fastmcp = ModuleType("mcp.server.fastmcp")
    fastmcp.Context = object
    fastmcp.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp)
    monkeypatch.syspath_prepend(str(SERVICE_DIR))

    spec = importlib.util.spec_from_file_location(
        "obo_server_under_test", SERVICE_DIR / "server.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def context_with_authorization(value: str | None):
    headers = [] if value is None else [(b"authorization", value.encode())]
    request = Request({"type": "http", "headers": headers})
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


def test_bearer_is_read_only_from_transport_header(server):
    assert (
        server._bearer_from_context(
            context_with_authorization("Bearer synthetic-value")
        )
        == "synthetic-value"
    )

    with pytest.raises(server.TokenValidationError, match="missing or malformed"):
        server._bearer_from_context(context_with_authorization(None))


def test_profile_photo_bytes_are_not_returned_to_model(server, monkeypatch, caplog):
    monkeypatch.setattr(
        server, "validate_user_token", lambda value: {"sub": "synthetic-user"}
    )
    monkeypatch.setattr(
        server,
        "fetch_my_profile",
        lambda value: {
            "displayName": "Synthetic User",
            "mail": "synthetic@example.invalid",
            "photoDataUri": "data:image/jpeg;base64,SYNTHETIC_BYTES",
        },
    )

    result = server.get_my_profile(context_with_authorization("Bearer synthetic-value"))

    assert result == {
        "displayName": "Synthetic User",
        "mail": "synthetic@example.invalid",
        "hasProfilePhoto": True,
    }
    assert "SYNTHETIC_BYTES" not in caplog.text


def test_validation_failure_does_not_expose_bearer(server, monkeypatch, caplog):
    opaque_value = "synthetic-value-that-must-not-escape"

    def reject(value):
        raise server.TokenValidationError(f"rejected Authorization: Bearer {value}")

    monkeypatch.setattr(server, "validate_user_token", reject)
    caplog.set_level(logging.WARNING, logger="obo-mcp-server")

    result = server.get_my_profile(context_with_authorization(f"Bearer {opaque_value}"))

    assert result["error"] == "unauthorized"
    assert opaque_value not in result["detail"]
    assert opaque_value not in caplog.text


def test_upstream_failure_is_opaque_and_does_not_log_bearer(
    server, monkeypatch, caplog
):
    opaque_value = "synthetic-value-that-must-not-escape"
    monkeypatch.setattr(
        server, "validate_user_token", lambda value: {"sub": "synthetic-user"}
    )

    def fail(value):
        raise RuntimeError(f"upstream rejected Authorization: Bearer {value}")

    monkeypatch.setattr(server, "fetch_my_profile", fail)
    caplog.set_level(logging.ERROR, logger="obo-mcp-server")

    result = server.get_my_profile(context_with_authorization(f"Bearer {opaque_value}"))

    assert result == {
        "error": "obo_failed",
        "detail": "Graph on-behalf-of request failed",
    }
    assert opaque_value not in caplog.text
