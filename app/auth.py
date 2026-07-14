from __future__ import annotations

import asyncio
import hmac
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import Header, HTTPException, status

from app.config import settings


@dataclass(frozen=True)
class AuthPrincipal:
    """Provider-neutral identity exposed to the rest of the application."""

    subject: str
    email: str | None = None
    scopes: frozenset[str] = frozenset()
    groups: tuple[str, ...] = ()
    method: str = "oidc"


def _unauthorized(detail: str = "Invalid token.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _mode() -> str:
    mode = settings.auth_mode.strip().lower()
    if mode not in {"static", "hybrid", "oidc"}:
        raise RuntimeError("AUTH_MODE must be static, hybrid, or oidc")
    return mode


def validate_auth_config() -> None:
    """Fail startup instead of discovering a broken auth boundary on request."""
    mode = _mode()
    if mode in {"static", "hybrid"} and not settings.api_token:
        raise RuntimeError(f"API_TOKEN is required for AUTH_MODE={mode}")
    if mode in {"hybrid", "oidc"} and (
        not settings.oidc_issuer or not settings.oidc_client_id
    ):
        raise RuntimeError(f"OIDC_ISSUER and OIDC_CLIENT_ID are required for AUTH_MODE={mode}")


class OIDCVerifier:
    """Small OIDC/JWKS verifier with bounded caching and key-rotation retry."""

    def __init__(self) -> None:
        self._issuer = ""
        self._jwks_uri = ""
        self._keys: dict[str, Any] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def reset(self) -> None:
        """Clear cached provider metadata; primarily useful for tests."""
        self._issuer = ""
        self._jwks_uri = ""
        self._keys = {}
        self._expires_at = 0.0

    async def _refresh(self, *, force: bool = False) -> None:
        issuer = settings.oidc_issuer.rstrip("/")
        if not issuer or not settings.oidc_client_id:
            raise RuntimeError("OIDC_ISSUER and OIDC_CLIENT_ID are required")
        if not force and self._issuer == issuer and time.monotonic() < self._expires_at:
            return
        async with self._lock:
            if not force and self._issuer == issuer and time.monotonic() < self._expires_at:
                return
            discovery_url = f"{issuer}/.well-known/openid-configuration"
            async with httpx.AsyncClient(timeout=5.0) as client:
                discovery_response = await client.get(discovery_url)
                discovery_response.raise_for_status()
                discovery = discovery_response.json()
                if discovery.get("issuer", "").rstrip("/") != issuer:
                    raise RuntimeError("OIDC discovery issuer does not match OIDC_ISSUER")
                jwks_uri = discovery.get("jwks_uri")
                if not isinstance(jwks_uri, str) or not jwks_uri:
                    raise RuntimeError("OIDC discovery document has no jwks_uri")
                jwks_response = await client.get(jwks_uri)
                jwks_response.raise_for_status()
                jwks = jwks_response.json()
            keys: dict[str, Any] = {}
            for value in jwks.get("keys", []):
                kid = value.get("kid")
                if kid and value.get("kty") == "RSA":
                    keys[kid] = jwt.PyJWK.from_dict(value).key
            if not keys:
                raise RuntimeError("OIDC provider returned no usable RSA signing keys")
            self._issuer = issuer
            self._jwks_uri = jwks_uri
            self._keys = keys
            self._expires_at = time.monotonic() + max(60, settings.oidc_jwks_cache_seconds)

    async def verify(self, token: str) -> AuthPrincipal:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise _unauthorized()
            await self._refresh()
            key = self._keys.get(header["kid"])
            if key is None:
                await self._refresh(force=True)
                key = self._keys.get(header["kid"])
            if key is None:
                raise _unauthorized()

            # Cognito access tokens use client_id instead of the normal aud
            # claim. Decode audience separately, then enforce one unambiguous
            # application match below.
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                issuer=settings.oidc_issuer.rstrip("/"),
                options={"verify_aud": False, "require": ["exp", "iat", "iss", "sub"]},
            )
            expected = settings.oidc_client_id
            audience = claims.get("aud")
            audience_matches = audience == expected or (
                isinstance(audience, list) and expected in audience
            )
            cognito_matches = (
                claims.get("client_id") == expected and claims.get("token_use") == "access"
            )
            if not (audience_matches or cognito_matches):
                raise _unauthorized()
            if claims.get("token_use") not in {None, "access"}:
                raise _unauthorized("An access token is required.")

            scope_value = claims.get("scope", "")
            # A generic JWT ID token normally has aud but no OAuth scope. When
            # the provider does not expose Cognito's explicit token_use claim,
            # require scope as the access-token discriminator.
            if audience_matches and not str(scope_value).strip():
                raise _unauthorized("An access token is required.")
            scopes = frozenset(str(scope_value).split())
            required = frozenset(settings.oidc_required_scopes.split())
            if not required.issubset(scopes):
                raise HTTPException(status_code=403, detail="Token is missing a required scope.")
            raw_groups = claims.get("cognito:groups", claims.get("groups", []))
            groups = tuple(raw_groups) if isinstance(raw_groups, list) else ()
            subject = str(claims["sub"]).strip()
            if not subject:
                raise _unauthorized()
            return AuthPrincipal(
                subject=subject,
                email=str(claims["email"]) if claims.get("email") else None,
                scopes=scopes,
                groups=groups,
            )
        except HTTPException:
            raise
        except (jwt.PyJWTError, httpx.HTTPError, RuntimeError, ValueError, TypeError) as exc:
            raise _unauthorized() from exc


oidc_verifier = OIDCVerifier()


async def bearer_auth(authorization: str | None = Header(default=None)) -> AuthPrincipal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("Missing or malformed Authorization header.")
    presented = authorization.split(" ", 1)[1].strip()
    mode = _mode()
    if mode in {"static", "hybrid"}:
        expected = settings.api_token
        if expected and hmac.compare_digest(presented, expected):
            return AuthPrincipal(subject="local-admin", method="static")
        if mode == "static":
            raise _unauthorized()
    return await oidc_verifier.verify(presented)
