from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

MODULE_PATH = Path(__file__).parents[1] / "obo.py"
SPEC = importlib.util.spec_from_file_location("obo_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
obo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(obo)


@pytest.fixture
def token_factory(monkeypatch):
    tenant_id = "synthetic-tenant"
    api_client_id = "synthetic-obo-api"
    caller_client_id = "synthetic-caller"
    issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJwksClient:
        def get_signing_key_from_jwt(self, token: str):
            assert token
            return SimpleNamespace(key=private_key.public_key())

    monkeypatch.setattr(obo, "TENANT_ID", tenant_id)
    monkeypatch.setattr(obo, "OBO_API_CLIENT_ID", api_client_id)
    monkeypatch.setattr(obo, "ALLOWED_CLIENT_APP_IDS", frozenset({caller_client_id}))
    monkeypatch.setattr(obo, "AUTH_DISABLED", False)
    monkeypatch.setattr(obo, "_ISSUER_V2", issuer)
    monkeypatch.setattr(obo, "_jwks_client", lambda: FakeJwksClient())

    def create(**overrides):
        claims = {
            "aud": f"api://{api_client_id}",
            "iss": issuer,
            "tid": tenant_id,
            "ver": "2.0",
            "scp": "access_as_user",
            "azp": caller_client_id,
            "exp": int(time.time()) + 300,
        }
        claims.update(overrides)
        return jwt.encode(claims, private_key, algorithm="RS256")

    return create


def test_accepts_valid_delegated_token(token_factory):
    claims = obo.validate_user_token(token_factory())

    assert claims["scp"] == "access_as_user"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"aud": "api://wrong-api"}, "token rejected"),
        ({"exp": 1}, "token rejected"),
        ({"tid": "wrong-tenant"}, "tenant"),
        ({"ver": "1.0"}, "token version"),
        ({"scp": "User.Read"}, "required scope"),
        ({"roles": ["Application.Read.All"]}, "application-only"),
        ({"azp": "unapproved-client"}, "allow-listed"),
    ],
)
def test_rejects_invalid_token_boundaries(token_factory, overrides, message):
    with pytest.raises(obo.TokenValidationError, match=message):
        obo.validate_user_token(token_factory(**overrides))


def test_rejects_missing_token():
    with pytest.raises(obo.TokenValidationError, match="missing bearer token"):
        obo.validate_user_token("")


def test_auth_bypass_is_refused_in_production(monkeypatch):
    monkeypatch.setattr(obo, "AUTH_DISABLED", True)
    monkeypatch.setattr(obo, "ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="not allowed"):
        obo.assert_auth_config_safe()
