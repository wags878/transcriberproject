# Contributing

Thanks for taking an interest. This is a small project maintained in spare time,
so please read the two rules below before anything else — they matter more here
than the usual style guidance.

## Two hard rules

1. **Never contribute real audio, real transcripts, or real voice enrollments.**
   Not in a commit, not in an issue, not in a test fixture, not in a bug report.
   This project processes patient↔provider conversations; a "helpful"
   reproduction clip can be a serious privacy incident. Use `samples/` or
   generate synthetic audio with `ml/synth/`.
2. **Report security problems privately**, not as a public issue. See
   [SECURITY.md](SECURITY.md).

## Getting set up

You do **not** need a GPU, or torch, or a 14 GB Docker image to work on most of
this codebase. `tests/conftest.py` stubs the inference pipeline, so the API
routes, ASR router, stitcher, OIDC bridge, and role-labelling logic all run
against a light dependency set.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest -q                          # 85 tests, ~3 seconds
```

**Python 3.11 or newer is required.** The code uses PEP 604 `X | None`
annotations that FastAPI and Pydantic evaluate at runtime, so 3.10 and below
fail at import, not at type-check time.

To exercise the real inference stack, run the suite inside the built image:

```bash
docker compose run --rm transcribe-svc pytest tests/
```

## Running the service locally

```bash
cp .env.example .env               # then set API_TOKEN
docker compose up -d               # CPU profile; no GPU needed
```

The web client is at <http://localhost:8000>. See [docs/DEPLOY.md](docs/DEPLOY.md)
for the GPU stack and [docs/API.md](docs/API.md) for the endpoint contract.

## Making a change

1. Fork, then branch off `main`.
2. Make the change. Match the surrounding code — this codebase uses type hints
   throughout, `from __future__ import annotations` at the top of each module,
   and comments that explain *why* rather than *what*.
3. **Add or update tests.** Anything that can be tested without torch should be.
4. Run `pytest -q` and make sure it is green.
5. If you changed behaviour a user can observe, update the relevant doc —
   `docs/API.md` for the contract, `.env.example` for a new setting, `README.md`
   if it changes the quick start.
6. Open a pull request describing what changed and why. Link any related issue.

CI runs the suite on Python 3.11 and 3.12 and validates that the compose files
parse. Both must pass.

## Things worth knowing about the architecture

- **Fallbacks are load-bearing.** Both GPU paths (`ASR_BACKEND=router` and
  `DIARIZE_BACKEND=remote`) fall back to in-process CPU per request, so a GPU or
  sidecar outage degrades speed but never fails a job. Please preserve that
  property.
- **The `/v1` contract is stable.** Additive changes are fine; breaking an
  existing field is not, without a clear discussion first.
- **New settings go in `app/config.py` *and* `.env.example`**, with a comment
  explaining the default. `.env.example` is the user-facing reference.
- **Dependency pins are deliberate.** `requirements.txt` documents why torch,
  numpy, and whisperx are pinned where they are; the constraints are real and
  interlocking. Read the comments before bumping anything.

## Reporting a bug

Open an issue with:

- what you expected and what happened,
- the compose profile (CPU or GPU) and `AUTH_MODE`,
- the relevant `docker compose logs` output, and
- the `asr_backend` / `diarize_device` fields from the transcript header if the
  problem is about speed or quality.

Redact anything identifying before you paste logs.

## Licensing of contributions

This project is licensed under [Apache License 2.0](LICENSE). By submitting a
contribution, you agree that your work is licensed under the same terms, per
section 5 of that license. There is no separate CLA to sign.
