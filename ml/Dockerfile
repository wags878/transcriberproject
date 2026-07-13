# Offline ML harness image (Track A: synthetic generation + eval).
#
# Reproducible environment so generation + scoring need no host Python/ffmpeg.
# This image NEVER runs in the serving path — it drives the service purely over
# its HTTP API. Build context is the repo root so it can see ml/.
#
#   docker compose -f ml/docker-compose.ml.yml run --rm ml-eval
#
# ffmpeg here is Debian 12's 5.x, used only for concat/anullsrc (no PyAV), so
# the version constraints that pin the service image do not apply.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

# ffmpeg + ffprobe for clip assembly; ca-certificates for edge-tts HTTPS.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deps first for layer caching.
COPY ml/requirements-ml.txt ./ml/requirements-ml.txt
RUN pip install --upgrade pip \
 && pip install -r ml/requirements-ml.txt

# Code baked in so the image runs standalone; a bind mount can override it for
# live iteration (see docker-compose.ml.yml).
COPY ml/ ./ml/

CMD ["python", "-m", "ml.eval.run_baseline"]
