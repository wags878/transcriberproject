from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    device: str
    compute_type: str
    gpu: bool


class TranscribeResponse(BaseModel):
    id: str
    transcript_txt_url: str
    transcript_json_url: str
    speakers_detected: int
    duration_seconds: float
    language: str            # detected source language
    task: str = "transcribe"
    output_language: str = ""  # language of the transcript text ('en' for translate)


class StorageResponse(BaseModel):
    uploads_mb: float = Field(ge=0)
    outputs_mb: float = Field(ge=0)
    models_mb: float = Field(ge=0)


class RelabelRequest(BaseModel):
    """Manual speaker relabel: one final speaker label per segment, in order."""
    speakers: list[str] = Field(..., min_length=0)
