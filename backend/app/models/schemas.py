from datetime import datetime
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
from app.models.db_models import JobStatus


class AudioStreamInfo(BaseModel):
    index: int = Field(description="Stream index within audio streams (0-based)")
    codec_name: str
    channels: int = 2
    channel_layout: Optional[str] = None
    sample_rate: Optional[str] = None
    language: Optional[str] = "und"
    title: Optional[str] = None


class ProbePathRequest(BaseModel):
    file_path: str = Field(description="Direct absolute path on the local filesystem")


class ProbeResponse(BaseModel):
    file_path: str
    file_name: str
    file_size: int
    duration: float
    format_name: str
    video_streams: List[Dict[str, Any]] = []
    audio_streams: List[AudioStreamInfo] = []


class JobConfig(BaseModel):
    selected_audio_stream: int = 0
    whisper_model: str = "medium"
    source_language: Optional[str] = "auto"
    vad_filter: bool = True
    enable_translation: bool = False
    target_language: str = "Hebrew"
    gemini_model: str = "gemini-2.5-flash"
    gemini_api_key_override: Optional[str] = None
    context_notes: Optional[str] = None


class JobCreate(JobConfig):
    video_path: str
    file_name: Optional[str] = None


class SubtitleSegmentSchema(BaseModel):
    id: Optional[int] = None
    job_id: str
    index: int
    start_time: float
    end_time: float
    original_text: str
    translated_text: Optional[str] = None
    confidence: Optional[float] = 1.0
    cps: Optional[float] = 0.0


class SegmentUpdate(BaseModel):
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    original_text: Optional[str] = None
    translated_text: Optional[str] = None


class JobResponse(BaseModel):
    id: str
    video_path: str
    file_name: str
    file_size: Optional[int] = None
    status: JobStatus
    current_stage: Optional[str] = None
    progress_percent: float = 0.0
    speed_factor: Optional[float] = None
    eta_seconds: Optional[float] = None
    error_message: Optional[str] = None
    video_duration: Optional[float] = None
    audio_streams: List[AudioStreamInfo] = []
    selected_audio_stream: int = 0
    whisper_model: str = "medium"
    source_language: Optional[str] = "auto"
    vad_filter: bool = True
    enable_translation: bool = False
    target_language: str = "Hebrew"
    gemini_model: str = "gemini-2.5-flash"
    context_notes: Optional[str] = None
    segments: List[SubtitleSegmentSchema] = []
    created_at: datetime
    updated_at: datetime


class WebSocketEventPayload(BaseModel):
    event: str  # "STAGE_PROGRESS" | "SEGMENT_CREATED" | "BATCH_TRANSLATED" | "JOB_COMPLETED" | "JOB_FAILED"
    job_id: str
    stage: Optional[str] = None
    progress_percent: float = 0.0
    speed_factor: Optional[float] = None
    eta_seconds: Optional[float] = None
    segment: Optional[SubtitleSegmentSchema] = None
    segments_batch: Optional[List[SubtitleSegmentSchema]] = None
    message: Optional[str] = None


class SystemInfoResponse(BaseModel):
    is_gemini_configured: bool
    gemini_model: str
    whisper_default_model: str
    hardware: Dict[str, Any]
