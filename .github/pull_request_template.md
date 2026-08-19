## What does this change?

<!-- A sentence or two. Link the issue it addresses, if there is one. -->

## Why?

<!-- The problem this solves. -->

## Checklist

- [ ] `pytest -q` passes locally
- [ ] Tests added or updated for anything testable without torch
- [ ] Docs updated if user-visible behaviour changed (`docs/API.md`, `.env.example`, `README.md`)
- [ ] New settings added to **both** `app/config.py` and `.env.example`
- [ ] Per-request CPU fallback still works for any GPU path I touched
- [ ] **No real audio, transcripts, or voice enrollments in this diff**
