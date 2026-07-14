#!/bin/sh
set -eu

# Make sure data subdirs exist (they should from the image, but volumes can wipe them).
mkdir -p "${DATA_DIR:-/data}/models/hf" \
         "${DATA_DIR:-/data}/uploads" \
         "${DATA_DIR:-/data}/outputs"

# Fail fast on an incomplete authentication boundary. OIDC-only deliberately
# does not require the legacy emergency token.
case "${AUTH_MODE:-static}" in
    static|hybrid)
        if [ -z "${API_TOKEN:-}" ] || [ "${API_TOKEN}" = "replace-me-with-a-strong-token" ]; then
            echo "ERROR: API_TOKEN is unset or still the placeholder for AUTH_MODE=${AUTH_MODE:-static}." >&2
            exit 1
        fi
        ;;
    oidc)
        if [ -z "${OIDC_ISSUER:-}" ] || [ -z "${OIDC_CLIENT_ID:-}" ]; then
            echo "ERROR: OIDC_ISSUER and OIDC_CLIENT_ID are required for AUTH_MODE=oidc." >&2
            exit 1
        fi
        ;;
    *)
        echo "ERROR: AUTH_MODE must be static, hybrid, or oidc." >&2
        exit 1
        ;;
esac

exec "$@"
