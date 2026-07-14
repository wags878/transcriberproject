# Authentication bridge

> **Codex implementation note — 2026-07-13.** This feature was implemented by
> Codex for Claude to continue driving. It deliberately uses OIDC standards,
> not a Cognito SDK, so identity and hosting remain separate decisions.

## What exists

The API supports three rollout modes through `AUTH_MODE`:

| Mode | Accepted credentials | Intended use |
|---|---|---|
| `static` | `API_TOKEN` | Backward-compatible default |
| `hybrid` | OIDC access token or `API_TOKEN` | Initial Cognito testing and rollback |
| `oidc` | OIDC access token only | After the new login is proven |

The API verifies RS256 signatures against cached provider JWKS, refreshes keys
when it encounters an unknown `kid`, and checks issuer, expiry, issued-at time,
application, token type, and optional required scopes. It supports standard
OIDC `aud` access tokens and Cognito's `client_id` + `token_use=access` profile.
ID tokens are not accepted as API credentials.

The PWA uses Authorization Code + PKCE with no browser client secret. Access and
refresh tokens live in `sessionStorage`, so closing the browser session clears
them. The older static token is migrated out of `localStorage` once and is also
kept session-only. Sign out currently clears the local app session; the
identity-provider SSO session may still allow an immediate sign-in.

## Cognito development setup

1. Create a Cognito user pool in the intended AWS region.
2. Create/configure a user-pool domain so managed login has authorization and
   token endpoints.
3. Create an **app client without a client secret**. This is a public PWA.
4. Enable the authorization-code grant and PKCE. Do not enable the implicit
   grant for this app.
5. Add exact callback URLs, including the trailing slash:
   - `http://localhost:8000/`
   - `https://transcribe-svc.<your-tailnet>.ts.net/`
6. Add the same URLs as allowed sign-out URLs if Cognito requests them.
7. Enable the `openid`, `profile`, and `email` scopes.
8. Create the first operator user or enable the desired federated provider.

Then set:

```dotenv
AUTH_MODE=hybrid
API_TOKEN=<keep-the-current-emergency-token>
OIDC_ISSUER=https://cognito-idp.<region>.amazonaws.com/<user-pool-id>
OIDC_CLIENT_ID=<public-app-client-id>
OIDC_BROWSER_SCOPES=openid profile email
OIDC_REQUIRED_SCOPES=
```

Rebuild/restart only `transcribe-svc`; Cognito does not need inbound access to
the Alienware. The browser follows the redirects, and the API makes outbound
requests to discovery/JWKS endpoints.

## Rollout checklist

1. Back up the current `.env` and leave Tailscale access controls in place.
2. Deploy in `hybrid` mode.
3. Verify local static-token API access still works.
4. From the tailnet HTTPS PWA, sign in and confirm Settings shows the user.
5. Transcribe a synthetic sample, fetch both result formats, relabel a speaker,
   reload history, and wait long enough to exercise token refresh.
6. Test sign out and sign in again on both desktop and iPhone.
7. Rotate the emergency `API_TOKEN` after testing.
8. Change to `AUTH_MODE=oidc` only when rollback through hybrid mode is no
   longer needed.

Do not set `OIDC_REQUIRED_SCOPES` until the provider has a matching API resource
server/custom scope. Once created, a value such as `transcriptions:write` makes
the API return `403` when a valid token lacks that scope.

## Provider portability

For another OIDC provider, change the issuer, public client ID, callback URLs,
and scopes. The rest of the application consumes only `AuthPrincipal` fields:
stable `subject`, optional `email`, scopes, groups, and authentication method.
Provider-specific claims stay confined to `app/auth.py`.

## Current boundary

Authentication now identifies users but **does not provide per-user storage
authorization**. Every authenticated human can currently access any transcript
whose ID they know, and the PWA history remains browser-local. Before any
third-party use, namespace jobs by `AuthPrincipal.subject`, enforce ownership on
every result/relabel route, add auditable administrative roles, and complete the
broader PHI/production hardening work.
