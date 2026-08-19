# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub's
[private vulnerability reporting](https://github.com/wags878/transcriberproject/security/advisories/new)
(the **Security** tab → **Report a vulnerability**). That opens a channel visible
only to you and the maintainers.

Please include:

- what the issue is and why you believe it is a security problem,
- the affected file(s) or endpoint(s),
- steps to reproduce, and
- the deployment mode it applies to (`static`, `hybrid`, or `oidc` auth; CPU or
  GPU compose profile).

This is a small, volunteer-maintained project. Expect an initial acknowledgement
within about a week. Please give a reasonable window to ship a fix before
disclosing publicly.

**Never include real recordings, real transcripts, or real voice enrollments in a
report.** If a reproduction needs audio, use a clip from `samples/` or generate a
synthetic one with `ml/synth/`.

## Supported versions

This project has not cut a tagged release yet. Only the current `main` branch
receives security fixes.

## Threat model — read this before deploying

This service is designed for **private, single-operator deployment on a trusted
network**. It is not hardened for exposure to the public internet, and several
defaults assume a trusted perimeter.

**What the service protects:**

- All `/v1` endpoints except `/v1/health` and `/v1/auth/config` require a bearer
  token. In `static` mode that is a shared secret compared with
  `hmac.compare_digest`; in `oidc` mode it is an OIDC access token verified
  against the issuer's JWKS (signature, issuer, audience, expiry, and any
  configured required scopes). `hybrid` accepts either.
- Audio and transcripts are written to a container volume, never to a
  third-party service. No audio leaves the host unless you configure a remote
  ASR backend yourself.
- `RETAIN_DAYS` sweeps old uploads and outputs on container start.

**What it does not protect against, by design:**

- **No transport security of its own.** The app speaks plain HTTP. In the
  reference deployment, TLS is terminated by a Tailscale sidecar. If you expose
  it any other way, put it behind a TLS-terminating reverse proxy — otherwise
  the bearer token and the audio travel in clear text.
- **No multi-tenancy.** Every authenticated caller can read every job's output
  via `/v1/results/{job_id}`. There is no per-user isolation. Do not give the
  token to anyone who should not see all transcripts on the instance.
- **No rate limiting or upload quotas** beyond `MAX_UPLOAD_MB` and
  `MAX_CONCURRENT_JOBS`.
- **Data at rest is not encrypted** by the application. Use full-disk or volume
  encryption on the host.

## Handling sensitive data

This service is built to process **conversations between patients and
providers**. Treat every deployment as holding sensitive personal data.

- **Voice enrollments (`ENROLLMENTS_DIR`) are biometric identifiers.** They are
  gitignored for a reason. Never commit them, never sync them to cloud storage,
  and back them up only to encrypted media.
- **Never commit real audio or transcripts.** `.gitignore` blocks the common
  paths, but it cannot catch a file you place somewhere new.
- **`API_TOKEN` is a shared secret.** Generate it with
  `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` and rotate it
  freely — nothing persists a hash of it.
- **`HF_TOKEN` and `TS_AUTHKEY` are credentials** for HuggingFace and Tailscale
  respectively. They belong in `.env`, which is gitignored.

## Regulatory status

This project is **not** a medical device, and it is **not** a HIPAA-compliant
service. It has not been audited, certified, or validated against any healthcare
regulatory framework. If you deploy it in a context where HIPAA, GDPR, or a
comparable regime applies, meeting those obligations — including any Business
Associate Agreement, breach notification duty, and retention or consent
requirement — is entirely your responsibility as the operator.

Recording a conversation may require the consent of everyone involved, and the
rules vary by jurisdiction. Know your local law before you record anyone.
