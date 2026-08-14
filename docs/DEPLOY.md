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

# Keep AUTH_MODE=static for initial bring-up. Generate a strong API token:
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# (set API_TOKEN= to that value)

# Optional: tag the VM in the Tailscale admin console as tag:transcribe-svc.
# Then tag your iPhone / laptop as tag:transcribe-client. Phase 4 adds the
# ACL JSON; for now any tailnet device can reach :8000.
```

## Optional: enable the OIDC authentication bridge

The app can use Cognito without moving the API or GPU stack to AWS. Provision
the public PWA client first, then set `AUTH_MODE=hybrid`, `OIDC_ISSUER`, and
`OIDC_CLIENT_ID` in `.env` and rebuild/restart `transcribe-svc`. Hybrid mode
keeps the original API token as an emergency master key. After browser login,
refresh, and API access have been verified, change to `AUTH_MODE=oidc` to
disable that key. Exact Cognito callback URLs, scopes, and verification steps
are in `docs/AUTH.md`.

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

Either the **web client** (a browser) or `curl`:

```sh
# From any device on the tailnet:
TOKEN=...
curl -H "Authorization: Bearer $TOKEN" \
     -F "audio=@~/Downloads/recording.m4a" \
     http://transcribe-svc.<your-tailnet>.ts.net:8000/v1/transcribe
```

(Use the MagicDNS name `transcribe-svc.<tailnet>.ts.net`, or the tailnet IP from
`tailscale status`.)

### HTTPS (required for mic recording + PWA install on iPhone)

Browsers only allow the microphone in a **secure context** (HTTPS or localhost),
so recording over plain `http://…:8000` is blocked on the phone. Enable HTTPS once
with `tailscale serve` (issues a valid **Let's Encrypt** cert for the MagicDNS
name — no browser warning; the tailnet must have HTTPS Certificates enabled, which
it is if `tailscale status --json` shows a non-empty `CertDomains`):

```sh
docker compose -f docker-compose.gpu.yml exec tailscale tailscale serve --bg 8000
# → https://transcribe-svc.<tailnet>.ts.net/  proxies to  http://127.0.0.1:8000
```

Then browse **`https://transcribe-svc.<tailnet>.ts.net`** (no port) on the phone.
The config persists in the `tailscale_state` volume. Disable with
`tailscale serve --https=443 off`.

---

## Fallback: CPU-only / the old server

`main` runs on a CPU-only host unchanged, because every GPU feature is opt-in and
defaults to CPU. On the old Proxmox VM (or any box without an NVIDIA GPU), deploy
the **CPU compose** — not the GPU one:

```sh
docker compose up -d          # docker-compose.yml — CPU profile
```

This uses `ASR_BACKEND=whisperx` (in-process CPU WhisperX), `DIARIZE_BACKEND=local`
(in-process CPU pyannote), role labels off, `task=transcribe`. The `diarize-svc`
GPU sidecar is referenced **only** in `docker-compose.gpu.yml` and is never touched
by the CPU compose, so nothing forces CUDA. Carry over the same `.env`. Slower
(no GPU) but fully functional — the same path Phase 1/2 ran on that VM.

---

## Deploying to the Alienware (Windows 11 + WSL2 + Docker Desktop + RTX 5090)

The current home of record (transcribe-svc moved here from the Proxmox VM on
2026-07-12). Uses `docker-compose.gpu.yml` — **four** containers: a tailscale
sidecar (also terminating HTTPS via `tailscale serve`), **Speaches** doing ASR on
the GPU, a **`diarize-svc`** sidecar doing pyannote diarization on the GPU, and
transcribe-svc orchestrating on CPU. ASR and diarization each fall back to
in-process CPU per request if their sidecar is unavailable.

### Host prerequisites

1. **NVIDIA driver v566+** (Blackwell / WSL2 support). *Verified present:
   592.02.*
2. **Windows 11 22H2+**, fully patched. Virtualization enabled in BIOS.
3. **WSL2 + Ubuntu.** *Verified present.* If rebuilding, in an admin PowerShell:
   ```powershell
   wsl --install -d Ubuntu-22.04
   wsl --set-default-version 2
   ```
4. **Docker Desktop** with WSL2 backend + Ubuntu integration.
   *Verified present: engine 29.6.1, compose v5.2.0.* Settings → Resources →
   WSL Integration → enable for your Ubuntu distro.
5. **Power settings — the one that will bite you if you skip it:**
   - Settings → System → Power & battery → Screen and sleep:
     "Never" for both plugged-in options.
   - Advanced power → When I close the lid: "Do nothing" (battery + plugged in).
6. **Defender Firewall:** allow Docker Desktop on both private and public profiles.

### Verify GPU passthrough

From PowerShell or a WSL2 shell:

```bash
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi
```

Must show the RTX 5090. *Verified 2026-07-12: sees "NVIDIA GeForce RTX 5090
Laptop GPU, 24463 MiB".* If this fails, stop — nothing downstream will work.
Common causes: driver too old (need v566+), WSL2 kernel out of date
(`wsl --update`), or Docker Desktop's GPU support not enabled.

### Tailscale prep (one-time, in the admin console)

1. Confirm `tag:transcribe-svc` and `tag:transcribe-client` are in the ACL policy.
2. **Delete any stale `transcribe-svc` node — and do it BEFORE first bring-up.**
   MagicDNS names are claimed first-come *and are sticky*: if an offline node from
   a previous deployment holds `transcribe-svc`, the new container silently
   registers as `transcribe-svc-1` and **you cannot get the name back afterward**.
   Verified on 2026-08-14 — deleting the stale node, restarting the container, a
   full `down`/`up`, `tailscale set --hostname`, and an admin-console rename all
   leave it as `-1`. The name is assigned at registration and persisted in the
   `tailscale_state` volume; only wiping that volume re-registers, which demands a
   known-**reusable** `TS_AUTHKEY` (every container gates on tailscale's health, so
   a consumed single-use key strands the whole stack).

   This is a one-way door. Check the machine list first:
   ```sh
   # after bring-up, confirm you got the name you wanted:
   docker compose -f docker-compose.gpu.yml exec tailscale tailscale status | grep transcribe
   ```
   (Hit on 2026-08-14; the deployment now permanently answers to
   `transcribe-svc-1`. See `docs/STATUS.md`.)
3. Generate a **reusable, pre-authorized** auth key at
   [Settings → Keys](https://login.tailscale.com/admin/settings/keys):
   - "Reusable" checked
   - "Pre-approved" checked
   - Tag: `tag:transcribe-svc`
4. Save the key; you'll paste it into `.env` next.

### Deploy

`docker-compose.gpu.yml` sets `ASR_BACKEND=router` and
`ASR_HOSTS=http://127.0.0.1:8001,local-whisperx` on the transcribe-svc service,
so you don't need to set those in `.env`. You do need three secrets:

```bash
cp .env.example .env
# Edit .env — set:
#   API_TOKEN   (generate: python3 -c "import secrets; print(secrets.token_urlsafe(32))")
#   TS_AUTHKEY  (paste the reusable auth key from above)
#   HF_TOKEN    (HuggingFace token with pyannote/speaker-diarization-3.1 accepted)
#   DIARIZATION_MODEL=pyannote/speaker-diarization-3.1   (community-1 doesn't
#                     load in pyannote.audio 3.1.1 — see B-004)

### Pre-flight (run these BEFORE `up` — both failures are invisible at startup)

Both of these pass a container healthcheck and then fail *inside* the first
transcription, which is a much worse place to find them.

```bash
# 1. HF token + gated model access. A bad/absent token does NOT fail at boot:
#    pipeline.transcribe() runs ASR and diarization under asyncio.gather with no
#    return_exceptions, so a diarization load failure kills the whole request.
HF=$(awk -F= '/^HF_TOKEN=/{print $2}' .env)
for m in pyannote/speaker-diarization-3.1 \
         pyannote/segmentation-3.0 \
         pyannote/wespeaker-voxceleb-resnet34-LM; do
  curl -s -o /dev/null -w "$m -> %{http_code}\n" \
       -H "Authorization: Bearer $HF" "https://huggingface.co/api/models/$m"
done
# All three must be 200. A 403 = conditions page not accepted for that model.

# 2. The ASR model download is now AUTOMATIC (B-005 resolved) — the one-shot
#    `speaches-model-init` service fetches it on first `up` and no-ops after.
#    No manual pre-pull needed. Just confirm it succeeded afterward:
#      docker logs transcribe-svc-speaches-init
#      # "already cached, nothing to do."  or  "done, model is cached."
```

### Bring-up (order matters)

All three non-tailscale containers share the tailscale netns and gate on its
health, so **nothing** starts until Tailscale authenticates. Start it alone first
— a key/tag/ACL rejection is far easier to read with one container up than four.

```bash
docker compose -f docker-compose.gpu.yml up -d tailscale
docker compose -f docker-compose.gpu.yml logs tailscale   # want "Switching ipn state Starting -> Running"
docker compose -f docker-compose.gpu.yml exec tailscale tailscale status --json \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['Self']['DNSName'], d['Self']['Tags'])"
# Confirm the name is transcribe-svc.<tailnet>, NOT transcribe-svc-1 (stale node — see above).

docker compose -f docker-compose.gpu.yml up -d --build
docker compose -f docker-compose.gpu.yml logs -f speaches        # "Uvicorn running"
docker compose -f docker-compose.gpu.yml logs -f diarize-svc     # "Loaded diarization pipeline ... on cuda"
docker compose -f docker-compose.gpu.yml logs -f transcribe-svc  # "Pipeline loaded"
```

The transcribe-svc image builds its heavy CPU stack (torch / ctranslate2 /
pyannote) on first `--build`, and diarize-svc pulls torch cu128 — budget a while
for both. Neither needs any secret, so both can build while you're still fetching
keys. Measured 2026-08-14: `transcribe-svc:cpu` 14 GB, `transcribe-svc-diarize:cuda`
4.4 GB, `speaches:latest-cuda` 8.6 GB.

Once all three are healthy, from any tagged `transcribe-client` host:

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
     https://transcribe-svc.<your-tailnet>.ts.net/v1/health
# {"status":"ok","device":"cpu","compute_type":"int8","gpu":false}
```

That `device: cpu` refers to the **local WhisperX fallback** inside
transcribe-svc; Speaches does the actual GPU inference and reports separately
in the `asr_backend` field on each transcription response.

### Web client

`docker-compose.gpu.yml` publishes the service on the host loopback
(`127.0.0.1:8000`), so the installable **PWA** is at <http://localhost:8000> on
the Alienware itself. In static mode, paste the API token in Settings; in
hybrid/OIDC mode, use **Sign in**. Then drag in an audio file, record, or click
a committed sample (`/samples`).

Over the tailnet, use the **HTTPS** URL (enabled 2026-08-14):
**`https://transcribe-svc-1.example-tailnet.ts.net`** — no port. Mic recording and
"Add to Home Screen" require a secure context, which this provides via a real
Let's Encrypt cert. `localhost` is also a secure context, so recording works
there without HTTPS.

⚠️ **Switching a device from `http://…:8000` to the HTTPS URL is an origin
change.** Installed PWAs, stored API tokens, and service workers are all
per-origin, so each device must reopen the HTTPS URL, re-enter the token in
Settings, and re-run "Add to Home Screen". A browser holding the old service
worker needs one hard refresh (`Ctrl+Shift+R`); it self-updates after that. Enable
HTTPS **before** rolling the app out to devices to avoid making everyone redo it.

### First-transcribe smoke test

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
     -F "audio=@samples/friendly_conversation.mp3" \
     -F "title=alienware-smoke" \
     http://localhost:8000/v1/transcribe
```

Expected: HTTP 200 with `transcript_txt_url` and `transcript_json_url`.

**Then verify what actually served** — this is the step that catches a silent CPU
fallback, and it is not optional. A degraded stack still returns 200:

```bash
ID=$(... .id from the response ...)
curl -fsS -H "Authorization: Bearer $API_TOKEN" \
     "http://localhost:8000/v1/results/$ID/transcript.json" \
| python3 -c "import sys,json; d=json.load(sys.stdin); print(d['asr_backend']); print(d['diarize_device'])"
```

| Field | Good (GPU) | Degraded (silent CPU fallback) |
|---|---|---|
| `asr_backend` | `speaches@http://127.0.0.1:8001` | `local-whisperx` |
| `diarize_device` | `cuda` | `cpu-fallback` |

Warm throughput on the RTX 5090 is **~8.7–10.9× realtime**. If a job runs slower
than realtime you are on the CPU tier regardless of what the healthchecks say.

**`/v1/health` does not tell you this.** It reports
`{"device":"cpu","gpu":false}` even when everything is correctly on the GPU —
that field describes the *local WhisperX fallback tier* inside transcribe-svc, not
what served the request. Speaches and diarize-svc hold the GPU reservations and
report per-response in the fields above.

### Running the unit tests on the Alienware (no torch, no GPU)

The new Phase 3 modules (stitch, ASR router, Speaches client) and all route
tests run without the heavy inference stack. WSL's system Python has
`ensurepip` stripped and there's no passwordless sudo, so bootstrap pip into a
`--without-pip` venv:

```bash
# In WSL:
python3 -m venv --without-pip ~/venvs/tsl
curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
~/venvs/tsl/bin/python /tmp/get-pip.py
~/venvs/tsl/bin/pip install fastapi==0.115.5 python-multipart==0.0.18 \
    pydantic==2.10.3 pydantic-settings==2.7.0 httpx==0.27.2 \
    pytest pytest-asyncio==0.24.0 python-dateutil==2.9.0
cd /mnt/c/Users/<user>/Github/transcriberproject   # repo path as of 2026-08-14
~/venvs/tsl/bin/python -m pytest tests/ -q      # 39 passing at the time of writing; 85 after the OIDC bridge
```

For the full pipeline (torch) run tests inside the built image instead:
`docker compose -f docker-compose.gpu.yml run --rm transcribe-svc pytest tests/`.

### Troubleshooting

- **Speaches container OOMs on model download** — first run downloads ~3 GB of
  weights. If the WSL2 instance is memory-capped (`.wslconfig`), raise the cap
  and `wsl --shutdown`, then reopen.
- **Speaches health check keeps timing out** — model load takes 30–90 s on
  first startup. Increase `start_period` in `docker-compose.gpu.yml`.
- **Requests hang for minutes / jobs succeed but are 5–10× too slow** — the
  router silently fell back to local-whisperx on CPU. Confirm via
  `asr_backend` in the transcript `.json` (see the smoke test above), not via
  wall-clock. **Most common cause: Speaches is up but has no model.** Since B-005
  was fixed this is now *visible* — speaches reports **unhealthy** instead of
  healthy-but-useless. Check the init service first:
  ```bash
  docker compose -f docker-compose.gpu.yml ps        # is speaches unhealthy?
  docker logs transcribe-svc-speaches-init           # did the fetch fail?
  # re-run the one-shot fetch:
  docker compose -f docker-compose.gpu.yml up -d speaches-model-init
  ```
  `PRELOAD_MODELS` in the compose does nothing — it is retained only so it isn't
  re-added as a "fix". See `docs/BLOCKERS.md` **B-005**.
- **A job is slow but `asr_backend` says `speaches@…`** — that is not a fallback.
  Speaches evicts the model after `ttl=300` (5 min idle) and reloads on the next
  request. Measured 0.9× cold vs 10.3× warm on the same clip. Send one throwaway
  request to warm it before timing anything.
- **Everything is healthy but the tailnet URL 404s / cert is for the wrong name**
  — a stale offline node is holding the `transcribe-svc` MagicDNS name and this
  node registered as `transcribe-svc-1`. Confirm with
  `docker compose -f docker-compose.gpu.yml exec tailscale tailscale status | grep transcribe`,
  delete the stale node in the admin console, then
  `docker compose -f docker-compose.gpu.yml restart tailscale`.
- **Docs say it's deployed but nothing is running** — verify before believing:
  `docker compose -f docker-compose.gpu.yml ps` and `docker volume ls`. An empty
  result means a cold rebuild, not a restart (happened 2026-08-14).
- **Container fails to start with "not found" on entrypoint.sh** — a CRLF line
  ending crept in. `.gitattributes` forces LF; if you edited the file with a
  Windows tool, run `git add --renormalize . && git checkout -- docker/`.
- **Windows updates rebooted the machine** — Docker Desktop restarts and
  `restart: unless-stopped` brings the containers back. If not,
  `docker compose -f docker-compose.gpu.yml up -d` again.
