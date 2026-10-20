"""Secret-less On-Behalf-Of (OBO) exchange to Microsoft Graph.

This module is the security core of the OBO MCP server. It does two things:

1. ``validate_user_token`` — verifies the inbound Entra access token that the
   agent forwards in the ``Authorization`` header. The token MUST be issued for
   THIS server (``aud``), by the expected tenant (``iss``/``tid``), and carry the
   ``access_as_user`` delegated scope (``scp``). A token minted for any other
   audience is rejected — the agent cannot trick us into acting on a token that
   was meant for a different API.

2. ``get_my_profile`` — performs the OBO flow with **no client secret**. The
   server authenticates itself to Entra using a *federated* managed-identity
   assertion (FIC -> user-assigned MI), exchanges the user's token for a Graph
   token scoped to ``User.Read``, and calls ``GET /me`` as the signed-in user.

The managed identity never holds a secret; the federated identity credential on
the app registration trusts the MI's token-exchange assertion instead.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import threading
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from urllib.parse import quote

import httpx
import jwt
from azure.identity import ManagedIdentityCredential, OnBehalfOfCredential
from jwt import PyJWKClient

logger = logging.getLogger("obo-mcp-server.obo")

# ─── Configuration (from environment) ────────────────────────────────────────

TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
# The OBO server app registration's appId (GUID). Doubles as the OBO client_id
# and as the expected token audience (api://<appId> or the bare GUID for v2).
OBO_API_CLIENT_ID = os.environ.get("OBO_API_CLIENT_ID", "")
# Client id of the user-assigned managed identity bound to this container app.
# Used to fetch the token-exchange assertion for the federated credential.
UAMI_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
# LOCAL DEV ONLY: a client secret for the OBO server app. When set, the OBO
# exchange uses this secret instead of the managed-identity federated assertion
# (managed identity is unavailable outside Azure). Lets you test the real
# OBO -> Graph hop on a laptop. Never set this in a deployed environment — use
# the secret-less FIC path there.
OBO_CLIENT_SECRET = os.environ.get("OBO_CLIENT_SECRET", "")
# Downstream Graph delegated scope(s) to request via OBO for the core profile.
GRAPH_SCOPES = os.environ.get(
    "GRAPH_SCOPES",
    (
        "https://graph.microsoft.com/User.Read "
        "https://graph.microsoft.com/User.ReadBasic.All "
        "https://graph.microsoft.com/Presence.Read.All "
        "https://graph.microsoft.com/Calendars.Read "
        "https://graph.microsoft.com/Mail.Read "
        "https://graph.microsoft.com/Chat.Read"
    ),
).split()
# Fields fetched from Graph /me. Several of these (department, preferredLanguage,
# mobilePhone, businessPhones, givenName, surname, officeLocation, jobTitle) do
# NOT appear in the inbound access token, so a correct answer proves the data was
# fetched live from Graph via OBO — it cannot have been decoded from the JWT.
GRAPH_ME_SELECT = (
    "id,displayName,userPrincipalName,mail,jobTitle,officeLocation,"
    "department,preferredLanguage,mobilePhone,businessPhones,givenName,surname"
)
GRAPH_ME_URL = f"https://graph.microsoft.com/v1.0/me?$select={GRAPH_ME_SELECT}"
# Small (48x48) live profile photo — covered by User.Read (no extra consent).
# Returned as a data URI so the report can show the user's actual photo.
GRAPH_PHOTO_URL = "https://graph.microsoft.com/v1.0/me/photos/48x48/$value"
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
_USER_SELECTOR = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~@-]{1,254}$")

# The audience Entra expects when the MI mints its token-exchange assertion.
TOKEN_EXCHANGE_SCOPE = "api://AzureADTokenExchange/.default"  # noqa: S105

# Allow-list of client application ids (the SPA frontend, optionally VS Code)
# permitted to call this server. Enforced against the token's azp/appid claim so
# a different app in the tenant that happens to hold the access_as_user scope
# cannot act as the user through us (confused-deputy defence). Empty = allow any
# caller that already passed audience+scope+tenant checks.
ALLOWED_CLIENT_APP_IDS = frozenset(os.environ.get("ALLOWED_CLIENT_APP_IDS", "").replace(",", " ").split())

# Deployment environment. AUTH_DISABLED is only honoured when this is a dev/local
# value; anywhere else it is refused so it can never silently disable auth in prod.
ENVIRONMENT = os.environ.get("ENVIRONMENT", "production").lower()
_DEV_ENVIRONMENTS = {"development", "dev", "local"}

# Local-dev escape hatch ONLY: skip JWT validation so the MCP transport can be
# smoke-tested without a real Entra token. Refused outside a dev environment.
AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "false").lower() == "true"


def assert_auth_config_safe() -> None:
    """Fail fast if AUTH_DISABLED is set outside a development environment."""
    if AUTH_DISABLED and ENVIRONMENT not in _DEV_ENVIRONMENTS:
        raise RuntimeError(
            "AUTH_DISABLED=true is not allowed when ENVIRONMENT="
            f"{ENVIRONMENT!r}; it may only be used in a development environment."
        )


_ISSUER_V2 = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
_JWKS_URI = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"


class TokenValidationError(Exception):
    """Raised when the inbound user token is missing or invalid."""


def classify_graph_error(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, ValueError):
        return ("invalid_request", str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return ("permission_denied", f"Microsoft Graph denied the request with HTTP {status}")
        if status == 404:
            return ("not_found", "The requested Microsoft Graph record was not found")
        if status == 429:
            return ("rate_limited", "Microsoft Graph rate-limited the request")
        return ("graph_error", f"Microsoft Graph returned HTTP {status}")
    if isinstance(exc, httpx.RequestError):
        return ("graph_unavailable", "Microsoft Graph could not be reached")
    return ("obo_failed", "Graph on-behalf-of request failed")


# ─── 1. Inbound token validation ─────────────────────────────────────────────


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    # PyJWKClient caches signing keys internally and refreshes on rotation.
    return PyJWKClient(_JWKS_URI)


def _expected_audiences() -> set[str]:
    return {f"api://{OBO_API_CLIENT_ID}", OBO_API_CLIENT_ID}


def validate_user_token(token: str) -> dict[str, Any]:
    """Validate the inbound Entra access token and return its claims.

    Raises ``TokenValidationError`` on any problem. Checks signature, issuer,
    audience, expiry, and the ``access_as_user`` scope.
    """
    if not token:
        raise TokenValidationError("missing bearer token")

    if AUTH_DISABLED:
        assert_auth_config_safe()  # refuses if not a dev environment
        logger.warning("AUTH_DISABLED=true — skipping token validation (LOCAL DEV ONLY)")
        return jwt.decode(token, options={"verify_signature": False})

    if not (TENANT_ID and OBO_API_CLIENT_ID):
        raise TokenValidationError("server misconfigured: AZURE_TENANT_ID / OBO_API_CLIENT_ID unset")

    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=list(_expected_audiences()),
            issuer=_ISSUER_V2,
            options={"require": ["exp", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenValidationError(f"token rejected: {exc}") from exc

    # Tenant pin (defence in depth on top of issuer check).
    if claims.get("tid") and claims["tid"] != TENANT_ID:
        raise TokenValidationError("token tenant (tid) does not match this server's tenant")

    # v2-only: issuer pinning already implies this; make it explicit.
    if claims.get("ver") != "2.0":
        raise TokenValidationError("unsupported token version (expected v2.0)")

    # Reject app-only tokens outright: a delegated (user) token carries 'scp';
    # an application token carries 'roles' and no user context.
    if claims.get("roles"):
        raise TokenValidationError("application-only tokens are not accepted")

    # Require the delegated scope this API exposes.
    scopes = set((claims.get("scp") or "").split())
    if "access_as_user" not in scopes:
        raise TokenValidationError("token missing required scope 'access_as_user'")

    # Caller allow-list: ensure the token was issued to an approved client app
    # (the SPA frontend / VS Code), not just any app holding our scope.
    if ALLOWED_CLIENT_APP_IDS:
        caller = claims.get("azp") or claims.get("appid")
        if caller not in ALLOWED_CLIENT_APP_IDS:
            raise TokenValidationError("token caller application is not allow-listed")

    return claims


# ─── 2. Secret-less OBO -> Microsoft Graph ───────────────────────────────────

_mi_cred: ManagedIdentityCredential | None = None
_mi_lock = threading.Lock()


def _managed_identity() -> ManagedIdentityCredential:
    global _mi_cred
    if _mi_cred is None:
        with _mi_lock:
            if _mi_cred is None:
                # client_id selects the user-assigned MI bound to this container app.
                _mi_cred = (
                    ManagedIdentityCredential(client_id=UAMI_CLIENT_ID)
                    if UAMI_CLIENT_ID
                    else ManagedIdentityCredential()
                )
    return _mi_cred


def _mi_assertion() -> str:
    """Return a fresh managed-identity token-exchange assertion for the FIC.

    Called by ``OnBehalfOfCredential`` on every token request, so it always
    hands Entra a valid (non-expired) assertion. This is what replaces a client
    secret — the app reg's federated identity credential trusts this MI.
    """
    return _managed_identity().get_token(TOKEN_EXCHANGE_SCOPE).token


def _graph_token_for_user(user_token: str) -> str:
    """Exchange the user's token for a Graph token via OBO.

    Production: secret-less, using the managed-identity federated assertion.
    Local dev: if ``OBO_CLIENT_SECRET`` is set, use that client secret instead
    (managed identity is not available off-Azure).
    """
    if OBO_CLIENT_SECRET:
        logger.warning("Using OBO_CLIENT_SECRET for OBO exchange (LOCAL DEV ONLY)")
        credential = OnBehalfOfCredential(
            tenant_id=TENANT_ID,
            client_id=OBO_API_CLIENT_ID,
            client_secret=OBO_CLIENT_SECRET,
            user_assertion=user_token,
        )
    else:
        credential = OnBehalfOfCredential(
            tenant_id=TENANT_ID,
            client_id=OBO_API_CLIENT_ID,
            client_assertion_func=_mi_assertion,
            user_assertion=user_token,
        )
    return credential.get_token(*GRAPH_SCOPES).token


def _fetch_photo_data_uri(graph_token: str) -> str | None:
    """BEST-EFFORT: the signed-in user's live 48x48 profile photo as a data URI.
    Covered by ``User.Read``; accounts with no photo return 404 → ``None``.
    """
    try:
        resp = httpx.get(
            GRAPH_PHOTO_URL,
            headers={"Authorization": f"Bearer {graph_token}"},
            timeout=15.0,
        )
        if resp.status_code != 200 or not resp.content:
            return None
        ctype = resp.headers.get("content-type", "image/jpeg").split(";")[0]
        return f"data:{ctype};base64," + base64.b64encode(resp.content).decode("ascii")
    except Exception:  # noqa: BLE001
        logger.info("profile photo fetch skipped", exc_info=True)
        return None


def get_my_profile(user_token: str) -> dict[str, Any]:
    """Call Microsoft Graph ``/me`` on behalf of the signed-in user.

    ``user_token`` must already have passed ``validate_user_token``.

    The returned dict deliberately includes fields that are **not present in the
    inbound access token** — so a correct answer proves the call hit Graph live
    on the user's behalf and was not decoded from the JWT:

    * Profile fields a login token never carries: ``jobTitle`` / ``department`` /
      ``officeLocation`` / ``preferredLanguage`` / ``givenName`` / ``surname`` —
      the human-readable "this is really my directory record" proof.
    * ``graphRequestId`` — the ``request-id`` correlation GUID Graph generates for
      this exact HTTP response (technical, cross-referenceable proof).
    * ``fetchedAtUtc`` — server timestamp of the live call.
    * ``photoDataUri`` — the user's live profile photo (best-effort).
    """
    graph_token = _graph_token_for_user(user_token)
    resp = httpx.get(
        GRAPH_ME_URL,
        headers={"Authorization": f"Bearer {graph_token}"},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    # Graph echoes a per-response correlation id; this is dispositive proof of a
    # live round-trip (the model cannot fabricate a Graph-issued request-id).
    graph_request_id = resp.headers.get("request-id") or resp.headers.get("client-request-id")
    # Return a compact, non-sensitive projection of the profile.
    profile = {
        "displayName": data.get("displayName"),
        "userPrincipalName": data.get("userPrincipalName"),
        "mail": data.get("mail"),
        "jobTitle": data.get("jobTitle"),
        "officeLocation": data.get("officeLocation"),
        "department": data.get("department"),
        "preferredLanguage": data.get("preferredLanguage"),
        "mobilePhone": data.get("mobilePhone"),
        "businessPhones": data.get("businessPhones"),
        "givenName": data.get("givenName"),
        "surname": data.get("surname"),
        "id": data.get("id"),
        # ── Graph-only proof signals (NOT derivable from the access token) ──
        "graphRequestId": graph_request_id,
        "fetchedAtUtc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "microsoft-graph:/v1.0/me (delegated, on-behalf-of)",
    }

    # ── Live profile photo (best-effort; never breaks the core call) ──
    photo = _fetch_photo_data_uri(graph_token)
    if photo:
        profile["photoDataUri"] = photo
    return profile


def _graph_get(user_token: str, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
    graph_token = _graph_token_for_user(user_token)
    response = httpx.get(
        f"{GRAPH_BASE_URL}{path}",
        params=params,
        headers={"Authorization": "Bearer " + graph_token},
        timeout=15.0,
        follow_redirects=False,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Microsoft Graph returned a non-object response")
    payload.pop("@odata.context", None)
    payload.pop("@odata.nextLink", None)
    payload["graphRequestId"] = response.headers.get("request-id") or response.headers.get("client-request-id")
    payload["fetchedAtUtc"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return payload


def _validated_user_selector(selector: str) -> str:
    value = selector.strip()
    if not _USER_SELECTOR.fullmatch(value):
        raise ValueError("user selector must be an exact user ID or user principal name")
    return quote(value, safe="")


def get_user(user_token: str, selector: str) -> dict[str, Any]:
    encoded = _validated_user_selector(selector)
    payload = _graph_get(
        user_token,
        f"/users/{encoded}",
        params={
            "$select": "id,displayName,userPrincipalName,mail,jobTitle,department,officeLocation,accountEnabled",
        },
    )
    payload["source"] = "microsoft-graph:/v1.0/users/{id} (delegated, read-only)"
    return payload


def get_user_presence(user_token: str, selector: str) -> dict[str, Any]:
    encoded = _validated_user_selector(selector)
    payload = _graph_get(user_token, f"/users/{encoded}/presence")
    return {
        "id": payload.get("id"),
        "availability": payload.get("availability"),
        "activity": payload.get("activity"),
        "graphRequestId": payload.get("graphRequestId"),
        "fetchedAtUtc": payload.get("fetchedAtUtc"),
        "source": "microsoft-graph:/v1.0/users/{id}/presence (delegated, read-only)",
    }


def list_calendar_events(
    user_token: str,
    start_at: str,
    end_at: str,
    limit: int,
) -> dict[str, Any]:
    start = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("calendar window must use timezone-aware timestamps with end after start")
    if end - start > timedelta(days=7):
        raise ValueError("calendar window cannot exceed 7 days")
    payload = _graph_get(
        user_token,
        "/me/calendarView",
        params={
            "startDateTime": start_at,
            "endDateTime": end_at,
            "$top": str(min(max(limit, 1), 25)),
            "$orderby": "start/dateTime",
            "$select": "id,subject,organizer,attendees,start,end,location,isOnlineMeeting,showAs,lastModifiedDateTime",
        },
    )
    return {
        "events": payload.get("value", []),
        "graphRequestId": payload.get("graphRequestId"),
        "fetchedAtUtc": payload.get("fetchedAtUtc"),
        "source": "microsoft-graph:/v1.0/me/calendarView (delegated, read-only)",
    }


def search_mail(user_token: str, query: str, limit: int) -> dict[str, Any]:
    normalized = " ".join(query.strip().split())
    if not normalized or len(normalized) > 120 or any(character in normalized for character in {'"', "\r", "\n"}):
        raise ValueError("mail query must be 1-120 characters without quotes or newlines")
    payload = _graph_get(
        user_token,
        "/me/messages",
        params={
            "$search": f'"{normalized}"',
            "$top": str(min(max(limit, 1), 25)),
            "$select": (
                "id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,"
                "importance,bodyPreview,hasAttachments,isRead"
            ),
        },
    )
    return {
        "messages": payload.get("value", []),
        "graphRequestId": payload.get("graphRequestId"),
        "fetchedAtUtc": payload.get("fetchedAtUtc"),
        "source": "microsoft-graph:/v1.0/me/messages (delegated, read-only)",
    }


def get_message(user_token: str, message_id: str) -> dict[str, Any]:
    normalized = message_id.strip()
    if not normalized or len(normalized) > 512:
        raise ValueError("message_id must be a non-empty Graph message ID")
    payload = _graph_get(
        user_token,
        f"/me/messages/{quote(normalized, safe='')}",
        params={
            "$select": (
                "id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,"
                "importance,bodyPreview,body,hasAttachments,isRead"
            ),
        },
    )
    body = payload.get("body")
    if isinstance(body, dict) and isinstance(body.get("content"), str):
        body["content"] = body["content"][:4000]
    payload["source"] = "microsoft-graph:/v1.0/me/messages/{id} (delegated, read-only)"
    return payload


def get_mail_thread(user_token: str, conversation_id: str, limit: int) -> dict[str, Any]:
    normalized = conversation_id.strip()
    if not normalized or len(normalized) > 512 or any(character in normalized for character in {"\r", "\n"}):
        raise ValueError("conversation_id must be a non-empty Graph conversation ID")
    escaped = normalized.replace("'", "''")
    payload = _graph_get(
        user_token,
        "/me/messages",
        params={
            "$filter": f"conversationId eq '{escaped}'",
            "$top": str(min(max(limit, 1), 25)),
            "$orderby": "receivedDateTime",
            "$select": "id,conversationId,subject,from,toRecipients,receivedDateTime,importance,bodyPreview",
        },
    )
    return {
        "messages": payload.get("value", []),
        "graphRequestId": payload.get("graphRequestId"),
        "fetchedAtUtc": payload.get("fetchedAtUtc"),
        "source": "microsoft-graph:/v1.0/me/messages?conversationId (delegated, read-only)",
    }


def get_calendar_event(user_token: str, event_id: str) -> dict[str, Any]:
    normalized = event_id.strip()
    if not normalized or len(normalized) > 512:
        raise ValueError("event_id must be a non-empty Graph event ID")
    payload = _graph_get(
        user_token,
        f"/me/events/{quote(normalized, safe='')}",
        params={
            "$select": (
                "id,subject,organizer,attendees,start,end,location,isOnlineMeeting,"
                "onlineMeeting,showAs,lastModifiedDateTime,bodyPreview"
            ),
        },
    )
    payload["source"] = "microsoft-graph:/v1.0/me/events/{id} (delegated, read-only)"
    return payload


def list_chats(user_token: str, limit: int) -> dict[str, Any]:
    payload = _graph_get(
        user_token,
        "/me/chats",
        params={
            "$top": str(min(max(limit, 1), 25)),
            "$expand": "members",
            "$select": "id,topic,chatType,lastUpdatedDateTime,members",
        },
    )
    return {
        "chats": payload.get("value", []),
        "graphRequestId": payload.get("graphRequestId"),
        "fetchedAtUtc": payload.get("fetchedAtUtc"),
        "source": "microsoft-graph:/v1.0/me/chats (delegated, read-only)",
    }


def list_chat_messages(user_token: str, chat_id: str, limit: int) -> dict[str, Any]:
    normalized = chat_id.strip()
    if not normalized or len(normalized) > 512:
        raise ValueError("chat_id must be a non-empty Graph chat ID")
    payload = _graph_get(
        user_token,
        f"/chats/{quote(normalized, safe='')}/messages",
        params={
            "$top": str(min(max(limit, 1), 25)),
            "$select": "id,from,createdDateTime,lastModifiedDateTime,body,mentions",
        },
    )
    messages = payload.get("value", [])
    if isinstance(messages, list):
        for message in messages:
            body = message.get("body") if isinstance(message, dict) else None
            if isinstance(body, dict) and isinstance(body.get("content"), str):
                body["content"] = body["content"][:2000]
    return {
        "messages": messages,
        "graphRequestId": payload.get("graphRequestId"),
        "fetchedAtUtc": payload.get("fetchedAtUtc"),
        "source": "microsoft-graph:/v1.0/chats/{id}/messages (delegated, read-only)",
    }
