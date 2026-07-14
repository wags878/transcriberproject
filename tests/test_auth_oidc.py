from __future__ import annotations

import asyncio
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.auth import bearer_auth, oidc_verifier
from app.config import settings


ISSUER = "https://identity.example.test"
CLIENT_ID = "transcribe-pwa"


@pytest.fixture
def oidc(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "oidc_required_scopes", "transcriptions:write")
    oidc_verifier.reset()
    oidc_verifier._issuer = ISSUER
    oidc_verifier._keys = {"test-key": private_key.public_key()}
    oidc_verifier._expires_at = time.monotonic() + 3600
    yield private_key
    oidc_verifier.reset()


def make_token(private_key, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "user-123",
        "aud": CLIENT_ID,
        "iat": now,
        "exp": now + 300,
        "scope": "openid transcriptions:write",
        "email": "person@example.test",
        "groups": ["operators"],
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


def authenticate(token: str):
    return asyncio.run(bearer_auth(f"Bearer {token}"))


def test_generic_oidc_access_token_returns_provider_neutral_principal(oidc) -> None:
    principal = authenticate(make_token(oidc))
    assert principal.subject == "user-123"
    assert principal.email == "person@example.test"
    assert principal.scopes == frozenset({"openid", "transcriptions:write"})
    assert principal.groups == ("operators",)
    assert principal.method == "oidc"


def test_cognito_access_token_client_id_profile_is_supported(oidc) -> None:
    token = make_token(oidc, aud=None, client_id=CLIENT_ID, token_use="access")
    assert authenticate(token).subject == "user-123"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "another-client"},
        {"iss": "https://wrong-issuer.example.test"},
        {"exp": int(time.time()) - 1},
        {"scope": "openid"},
        {"scope": ""},
        {"token_use": "id"},
    ],
)
def test_invalid_oidc_claims_are_rejected(oidc, overrides) -> None:
    with pytest.raises(HTTPException) as exc:
        authenticate(make_token(oidc, **overrides))
    assert exc.value.status_code in {401, 403}


def test_hybrid_mode_keeps_static_token_as_migration_fallback(oidc, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_mode", "hybrid")
    monkeypatch.setattr(settings, "api_token", "emergency-token")
    principal = authenticate("emergency-token")
    assert principal.subject == "local-admin"
    assert principal.method == "static"


def test_oidc_mode_disables_static_token(oidc, monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_token", "emergency-token")
    with pytest.raises(HTTPException) as exc:
        authenticate("emergency-token")
    assert exc.value.status_code == 401


def test_unknown_signing_key_forces_one_rotation_refresh(oidc, monkeypatch) -> None:
    rotated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        {
            "iss": ISSUER,
            "sub": "rotated-user",
            "aud": CLIENT_ID,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "scope": "transcriptions:write",
        },
        rotated_key,
        algorithm="RS256",
        headers={"kid": "rotated-key"},
    )
    refreshed = False

    async def refresh(*, force=False):
        nonlocal refreshed
        if not force:
            return
        refreshed = True
        oidc_verifier._keys["rotated-key"] = rotated_key.public_key()

    monkeypatch.setattr(oidc_verifier, "_refresh", refresh)
    assert authenticate(token).subject == "rotated-user"
    assert refreshed
