#!/bin/sh
set -eu

# Make sure data subdirs exist (they should from the image, but volumes can wipe them).
mkdir -p "${DATA_DIR:-/data}/models/hf" \
         "${DATA_DIR:-/data}/uploads" \
         "${DATA_DIR:-/data}/outputs"

# Refuse to start without an API token. Fail fast and loud rather than booting
# an unauthenticated service.
if [ -z "${API_TOKEN:-}" ] || [ "${API_TOKEN}" = "replace-me-with-a-strong-token" ]; then
    echo "ERROR: API_TOKEN is unset or still the placeholder. Set a real token in .env." >&2
    exit 1
fi

exec "$@"
