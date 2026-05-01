# Deploy

Reproducible bring-up from a fresh Ubuntu 24.04 VM. The current host is the
target of record (see `HARDWARE.md`); this doc should remain runnable
end-to-end if the operator rebuilds the VM.

## Prerequisites

- Ubuntu 24.04 LTS VM (Proxmox guest in this case)
- Docker Engine 25+ with the `compose` plugin v2 (already present: v29.4.1 / v5.1.3)
- Tailscale 1.96+ (already present: 1.96.4) joined to the operator's tailnet
- Outbound internet from the VM during the first build (HuggingFace + PyPI). Tailscale Magic DNS is fine.

## One-time setup

```sh
cd /home/transcriber/Github/transcriberproject
cp .env.example .env

# Generate a strong API token and edit it into .env:
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# (set API_TOKEN= to that value)

# Optional: tag the VM in the Tailscale admin console as tag:transcribe-svc.
# Then tag your iPhone / laptop as tag:transcribe-client. Phase 4 adds the
# ACL JSON; for now any tailnet device can reach :8000.
```

## First build and start

```sh
docker compose up -d --build
docker compose logs -f transcribe-svc
```

The first model download happens on first request (lazy load) and writes to
the `models` named volume. Subsequent restarts reuse the cached models.

To eagerly load models at startup (slower boot, faster first request), set
`EAGER_LOAD=1` in `.env` before `up`.

## Smoke test

```sh
# Health
curl -fsS http://localhost:8000/v1/health
# expect: {"status":"ok","device":"cpu","compute_type":"int8","gpu":false}

# Transcribe (replace token + audio path)
TOKEN=$(grep ^API_TOKEN .env | cut -d= -f2-)
curl -fsS -H "Authorization: Bearer $TOKEN" \
     -F "audio=@tests/fixtures/short_two_speaker.wav" \
     -F "title=smoke-$(date +%Y%m%d-%H%M)" \
     http://localhost:8000/v1/transcribe \
| tee /tmp/resp.json

# Fetch transcript
ID=$(jq -r .id /tmp/resp.json)
curl -fsS -H "Authorization: Bearer $TOKEN" \
     "http://localhost:8000/v1/results/$ID/transcript.txt"
```

For the **Phase 1 acceptance perf baseline**, time a 5-minute fixture:

```sh
time curl -fsS -H "Authorization: Bearer $TOKEN" \
       -F "audio=@tests/fixtures/five_minute.wav" \
       -F "title=baseline" \
       http://localhost:8000/v1/transcribe > /dev/null
# Record wall-clock in docs/HARDWARE.md "Performance baseline" table.
```

## Restart / stop / update

```sh
docker compose restart transcribe-svc
docker compose stop
docker compose pull && docker compose up -d   # only after Phase 4 pins SHAs
```

## Local pytest (no docker)

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt fastapi[standard] pytest
pytest tests/
```

The unit tests stub out the WhisperX pipeline, so they don't require torch or a
GPU. They cover health, auth, and the route shapes.

## Reaching it from your iPhone / laptop

Until the Phase 3 PWA exists, use `curl`:

```sh
# From any device on the tailnet:
TOKEN=...
curl -H "Authorization: Bearer $TOKEN" \
     -F "audio=@~/Downloads/recording.m4a" \
     http://100.x.y.z:8000/v1/transcribe
```

(Replace `100.x.y.z` with the VM's tailnet IP from `tailscale status`. A
MagicDNS hostname can be set later — see `PROJECT_PLAN.md` §8 Q6.)
