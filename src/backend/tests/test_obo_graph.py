"""Delegated Microsoft Graph read-contract tests for the OBO MCP server."""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

OBO_ROOT = Path(__file__).resolve().parents[2] / "obo-mcp-server"
sys.path.insert(0, str(OBO_ROOT))
obo = importlib.import_module("obo")


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = {"request-id": "graph-request-1"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://graph.microsoft.com")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

    def json(self) -> dict:
        return self._payload


def test_graph_request_is_pinned_to_graph_and_redacts_pagination(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(obo, "_graph_token_for_user", lambda _token: "downstream-token")

    def fake_get(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return _Response({"value": [{"id": "message-1"}], "@odata.nextLink": "sensitive-link"})

    monkeypatch.setattr(obo.httpx, "get", fake_get)

    result = obo._graph_get("user-token", "/me/messages", params={"$top": "1"})

    assert captured["url"] == "https://graph.microsoft.com/v1.0/me/messages"
    assert captured["headers"]["Authorization"] == "Bearer downstream-token"
    assert captured["follow_redirects"] is False
    assert "@odata.nextLink" not in result
    assert result["graphRequestId"] == "graph-request-1"


def test_calendar_window_is_bounded(monkeypatch):
    monkeypatch.setattr(obo, "_graph_get", lambda *_args, **_kwargs: {"value": []})
    start = datetime.now(UTC)

    with pytest.raises(ValueError, match="cannot exceed 7 days"):
        obo.list_calendar_events(
            "token",
            start.isoformat(),
            (start + timedelta(days=8)).isoformat(),
            20,
        )


def test_mail_search_rejects_odata_injection(monkeypatch):
    monkeypatch.setattr(obo, "_graph_get", lambda *_args, **_kwargs: {"value": []})

    with pytest.raises(ValueError, match="without quotes"):
        obo.search_mail("token", 'incident" OR from:anyone', 20)


def test_chat_message_content_is_bounded(monkeypatch):
    monkeypatch.setattr(
        obo,
        "_graph_get",
        lambda *_args, **_kwargs: {
            "value": [{"id": "message-1", "body": {"content": "x" * 3000}}],
            "graphRequestId": "request-1",
        },
    )

    result = obo.list_chat_messages("token", "chat-id", 20)

    assert len(result["messages"][0]["body"]["content"]) == 2000


def test_obo_server_exposes_only_read_only_incident_context_tools():
    source = (OBO_ROOT / "server.py").read_text()

    for tool in (
        "get_my_profile",
        "m365_get_user",
        "m365_get_user_presence",
        "m365_list_events",
        "m365_search_messages",
        "m365_get_message",
        "m365_get_thread",
        "m365_get_event",
        "m365_list_chats",
        "m365_search_chat_messages",
    ):
        assert f"def {tool}(" in source
    for mutation in ("m365_send", "m365_draft", "m365_create_event", "m365_post"):
        assert f"def {mutation}" not in source


def test_graph_scope_set_matches_read_only_tools():
    assert set(obo.GRAPH_SCOPES) == {
        "https://graph.microsoft.com/User.Read",
        "https://graph.microsoft.com/User.ReadBasic.All",
        "https://graph.microsoft.com/Presence.Read.All",
        "https://graph.microsoft.com/Calendars.Read",
        "https://graph.microsoft.com/Mail.Read",
        "https://graph.microsoft.com/Chat.Read",
    }


def test_graph_permission_denial_is_explicit():
    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me")
    response = httpx.Response(403, request=request)
    result = obo.classify_graph_error(httpx.HTTPStatusError("denied", request=request, response=response))

    assert result == ("permission_denied", "Microsoft Graph denied the request with HTTP 403")
