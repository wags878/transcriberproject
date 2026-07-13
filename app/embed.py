"""Speaker-embedding backend for voice enrollment / role labeling (Track B).

Wraps pyannote's pretrained speaker-embedding model
(`pyannote/wespeaker-voxceleb-resnet34-LM` by default) and exposes a small
`Embedder` protocol so the role-labeling logic in `app/roles.py` can be unit
tested with a fake embedder — no torch required in the test path.

Lives in ``app/`` (not ``ml/``) because it is a **service-side capability**:
``app/roles.py`` uses it at request time when ``ENABLE_ROLE_LABELS`` is on, so it
must ship in the service image. The offline ``ml/enroll`` tools import it from
here (enrollment runs in the service image, which already has torch/pyannote).

**Model choice:** pyannote's wespeaker embedding is used deliberately rather than
speechbrain ECAPA-TDNN. pyannote 3.1 already ships and caches this model as part
of the diarization pipeline, and it fits the existing ``torch==2.0.1`` pin, so no
new service dependency is added. speechbrain would reopen the dependency
fragility documented in ``docs/BLOCKERS.md`` B-003.

Heavy imports (torch / pyannote) are deferred to first use, mirroring
``app/diarize.py``, so importing this module stays cheap (numpy only).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

log = logging.getLogger("transcribe-svc.embed")

DEFAULT_EMBEDDING_MODEL = "pyannote/wespeaker-voxceleb-resnet34-LM"


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns a (region of) audio into a fixed-length vector."""

    def embed(
        self, audio_path: Path, start: float | None = None, end: float | None = None
    ) -> np.ndarray:
        ...


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity in [-1, 1]; 0.0 if either vector is degenerate."""
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def l2_normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    n = np.linalg.norm(v)
    return v if n == 0.0 else (v / n).astype(np.float32)


def average_embeddings(vectors: list[np.ndarray]) -> np.ndarray:
    """Mean of L2-normalized vectors, renormalized — a robust centroid for
    building an enrollment from several clips or several segments of a cluster."""
    if not vectors:
        raise ValueError("no vectors to average")
    stacked = np.vstack([l2_normalize(v) for v in vectors])
    return l2_normalize(stacked.mean(axis=0))


class PyannoteEmbedding:
    """Pretrained pyannote speaker embedding. Lazy-loads on first `embed`."""

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        hf_token: str | None = None,
        hf_home: str | None = None,
    ) -> None:
        self.model_name = model_name or DEFAULT_EMBEDDING_MODEL
        self.device = device or "cpu"
        self.hf_token = hf_token or None
        self.hf_home = hf_home
        self._inference: Any | None = None

    def load(self) -> None:
        if self._inference is not None:
            return
        if self.hf_home:
            os.environ.setdefault("HF_HOME", self.hf_home)
        log.info("Loading embedding model (name=%s, device=%s)",
                 self.model_name, self.device)
        import torch  # type: ignore
        from pyannote.audio import Inference, Model  # type: ignore

        model = Model.from_pretrained(self.model_name, use_auth_token=self.hf_token)
        # window="whole" → one vector per crop (or per file if no crop given).
        inference = Inference(model, window="whole")
        inference.to(torch.device(self.device))
        self._inference = inference
        log.info("Embedding model loaded.")

    def embed(
        self, audio_path: Path, start: float | None = None, end: float | None = None
    ) -> np.ndarray:
        if self._inference is None:
            self.load()
        assert self._inference is not None
        if start is None or end is None:
            emb = self._inference(str(audio_path))
        else:
            from pyannote.core import Segment  # type: ignore

            emb = self._inference.crop(str(audio_path), Segment(float(start), float(end)))
        return np.asarray(emb, dtype=np.float32).reshape(-1)
