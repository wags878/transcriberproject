# GPU diarization sidecar. python + torch cu128 (self-contained CUDA runtime) —
# no CUDA base image needed; the host NVIDIA driver + nvidia-container-toolkit
# (Docker Desktop) provide device access via `deploy.resources` in compose.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/data/hf

# ffmpeg: decode arbitrary uploads to wav; libsndfile: pyannote audio IO;
# libgomp1: OpenMP runtime; curl: healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libsndfile1 libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip \
 && pip install -r requirements.txt

COPY app.py ./

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8002/health || exit 1

CMD ["uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8002", "--timeout-keep-alive", "300"]
