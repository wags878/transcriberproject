"""ml/ — offline ML train→eval→serve tooling for transcribe-svc.

Everything here is an OFFLINE job. Nothing in this package is imported by the
running service (app/); the only coupling is over the HTTP API. See ml/README.md
for the honesty caveats on synthetic data.
"""
