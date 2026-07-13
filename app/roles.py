"""Optional post-diarization role labeling (Track B, behind ENABLE_ROLE_LABELS).

After diarization + stitching, speakers are anonymous ``SPEAKER_00/01``. When
role labels are enabled, this module embeds each speaker cluster, cosine-matches
it against enrolled voiceprints (built offline by ``ml/enroll``), and relabels
matches — e.g. the enrolled therapist's cluster becomes ``Therapist``. In a
two-speaker session the other cluster is inferred as ``Client``.

The matching / inference / relabeling functions are **pure** (they take
precomputed vectors), so they unit-test without torch. Only
``compute_cluster_embeddings`` needs a real `Embedder`, which is injected — tests
pass a fake one. When the feature flag is off, nothing here runs and the /v1
output contract is unchanged.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from app.embed import Embedder, average_embeddings, cosine
from app.stitch import UNKNOWN_SPEAKER

log = logging.getLogger("transcribe-svc.roles")


def load_enrollments(enrollments_dir: Path) -> dict[str, np.ndarray]:
    """Load every ``<name>.npy`` voiceprint in a directory → {name: vector}."""
    result: dict[str, np.ndarray] = {}
    if not enrollments_dir.is_dir():
        return result
    for npy in sorted(enrollments_dir.glob("*.npy")):
        try:
            result[npy.stem] = np.load(npy)
        except Exception as e:  # pragma: no cover - corrupt file guard
            log.warning("Skipping unreadable enrollment %s: %s", npy, e)
    return result


def _distinct_labels(segments: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for seg in segments:
        spk = seg.get("speaker")
        if spk and spk != UNKNOWN_SPEAKER and spk not in seen:
            seen.append(spk)
    return seen


def match_clusters(
    cluster_vecs: dict[str, np.ndarray],
    enrollments: dict[str, np.ndarray],
    threshold: float,
) -> dict[str, str]:
    """Greedy one-to-one assignment of clusters to enrolled names by descending
    cosine similarity, keeping only pairs at or above ``threshold``.

    One-to-one: each cluster and each enrolled name is used at most once, so two
    clusters can't both become 'Therapist'."""
    scored: list[tuple[float, str, str]] = []
    for cluster, cv in cluster_vecs.items():
        for name, ev in enrollments.items():
            sim = cosine(cv, ev)
            if sim >= threshold:
                scored.append((sim, cluster, name))
    scored.sort(key=lambda t: t[0], reverse=True)

    mapping: dict[str, str] = {}
    used_names: set[str] = set()
    for _sim, cluster, name in scored:
        if cluster in mapping or name in used_names:
            continue
        mapping[cluster] = name
        used_names.add(name)
    return mapping


def infer_client_label(
    distinct_labels: list[str],
    mapping: dict[str, str],
    client_label: str,
) -> dict[str, str]:
    """In an exactly-two-speaker session where exactly one cluster matched an
    enrollment, label the other cluster ``client_label``. No-op otherwise."""
    if len(distinct_labels) != 2 or len(mapping) != 1:
        return mapping
    others = [lbl for lbl in distinct_labels if lbl not in mapping]
    if not others:
        return mapping
    updated = dict(mapping)
    updated[others[0]] = client_label
    return updated


def relabel_segments(
    segments: list[dict[str, Any]],
    mapping: dict[str, str],
) -> list[dict[str, Any]]:
    """Return new segments with ``speaker`` replaced per ``mapping`` (unmapped
    speakers untouched)."""
    out: list[dict[str, Any]] = []
    for seg in segments:
        new_seg = dict(seg)
        spk = new_seg.get("speaker")
        if spk in mapping:
            new_seg["speaker"] = mapping[spk]
        out.append(new_seg)
    return out


def compute_cluster_embeddings(
    audio_path: Path,
    segments: list[dict[str, Any]],
    embedder: Embedder,
    *,
    max_segments_per_cluster: int = 6,
    min_segment_seconds: float = 0.5,
) -> dict[str, np.ndarray]:
    """Embed each speaker cluster: average the embeddings of up to N of its
    longest segments (favoring segments long enough for a stable embedding)."""
    by_cluster: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    for seg in segments:
        spk = seg.get("speaker")
        if not spk or spk == UNKNOWN_SPEAKER:
            continue
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or 0.0)
        dur = end - start
        if dur > 0.0:
            by_cluster[spk].append((dur, start, end))

    cluster_vecs: dict[str, np.ndarray] = {}
    for spk, segs in by_cluster.items():
        segs.sort(key=lambda t: t[0], reverse=True)
        long_enough = [s for s in segs if s[0] >= min_segment_seconds]
        chosen = (long_enough or segs)[:max_segments_per_cluster]
        vectors: list[np.ndarray] = []
        for _dur, start, end in chosen:
            try:
                vectors.append(embedder.embed(audio_path, start, end))
            except Exception as e:  # pragma: no cover - per-segment robustness
                log.warning("embed failed for %s [%.2f-%.2f]: %s", spk, start, end, e)
        if vectors:
            cluster_vecs[spk] = average_embeddings(vectors)
    return cluster_vecs


def apply_role_labels(
    audio_path: Path,
    segments: list[dict[str, Any]],
    *,
    embedder: Embedder,
    enrollments: dict[str, np.ndarray],
    threshold: float,
    client_label: str = "Client",
    infer_client: bool = True,
) -> list[dict[str, Any]]:
    """Full pass: embed clusters → match enrollments → (optionally) infer Client
    → relabel. Returns segments unchanged if there are no enrollments or no
    cluster clears the threshold."""
    if not enrollments or not segments:
        return segments
    cluster_vecs = compute_cluster_embeddings(audio_path, segments, embedder)
    mapping = match_clusters(cluster_vecs, enrollments, threshold)
    if not mapping:
        return segments
    if infer_client:
        mapping = infer_client_label(_distinct_labels(segments), mapping, client_label)
    log.info("Role labels applied: %s", mapping)
    return relabel_segments(segments, mapping)


class RoleLabeler:
    """Lazy holder the pipeline uses when ENABLE_ROLE_LABELS is on.

    Builds the embedding backend and loads enrollments once (from settings), then
    relabels each request's segments. Constructing it is cheap — the pyannote
    model only loads on the first embed. Runs entirely off the request thread via
    the pipeline's ``asyncio.to_thread`` call.
    """

    def __init__(self) -> None:
        self._embedder: Embedder | None = None
        self._enrollments: dict[str, np.ndarray] | None = None

    def _ensure(self) -> None:
        if self._embedder is not None:
            return
        from app.config import settings
        from app.embed import PyannoteEmbedding

        self._embedder = PyannoteEmbedding(
            model_name=settings.embedding_model,
            device=settings.whisperx_device,
            hf_token=settings.hf_token or None,
            hf_home=settings.hf_home_str,
        )
        self._enrollments = load_enrollments(settings.enrollments_dir)
        log.info(
            "Role labeler ready: %d enrollment(s) from %s",
            len(self._enrollments), settings.enrollments_dir,
        )

    def label(
        self, audio_path: Path, segments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        from app.config import settings

        self._ensure()
        assert self._embedder is not None and self._enrollments is not None
        if not self._enrollments:
            return segments
        return apply_role_labels(
            audio_path, segments,
            embedder=self._embedder,
            enrollments=self._enrollments,
            threshold=settings.role_match_threshold,
            client_label=settings.client_label,
        )


role_labeler = RoleLabeler()
