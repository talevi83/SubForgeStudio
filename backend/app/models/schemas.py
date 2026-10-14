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


class SubtitleStreamInfo(BaseModel):
    index: int = Field(description="Stream index within subtitle streams (0-based, for -map 0:s:N)")
    codec_name: str
    language: Optional[str] = "und"
    title: Optional[str] = None
    forced: bool = False
    default: bool = False
    text_based: bool = True  # False for image subtitles (PGS/VobSub) which can't be parsed as text

    @property
    def looks_forced(self) -> bool:
        title = (self.title or "").lower()
        return self.forced or "forced" in title or "signs" in title


class ProbePathRequest(BaseModel):
    file_path: str = Field(description="Direct absolute path on the local filesystem")


class ProbeResponse(BaseModel):
    file_path: str
    file_name: str
    file_size: int
    duration: float
    format_name: str
    is_upload: bool = False
    video_streams: List[Dict[str, Any]] = []
    audio_streams: List[AudioStreamInfo] = []
    subtitle_streams: List[SubtitleStreamInfo] = []


class JobConfig(BaseModel):
    selected_audio_stream: int = 0
    whisper_model: str = "medium"
    source_language: Optional[str] = "auto"
    vad_filter: bool = True
    comms_boost: bool = False
    detect_onscreen_text: bool = False
    enable_translation: bool = False
    target_language: str = "Hebrew"
    # None = use the translation model chosen on the Settings screen
    gemini_model: Optional[str] = None
    # Used for this run only; never written to the database.
    gemini_api_key_override: Optional[str] = None
    context_notes: Optional[str] = None
    glossary_profile: Optional[str] = None


class JobCreate(JobConfig):
    video_path: str
    file_name: Optional[str] = None


class RetranslateRequest(BaseModel):
    only_failed: bool = False
    target_language: Optional[str] = None
    gemini_model: Optional[str] = None
    context_notes: Optional[str] = None
    gemini_api_key_override: Optional[str] = None
    rebuild_glossary: bool = False
    # Translate only these rows (segment ids) - used with a hint about who is speaking to whom
    segment_ids: Optional[List[int]] = None
    speaker_gender: Optional[str] = Field(default=None, description="male, female or group")
    addressee_gender: Optional[str] = Field(default=None, description="male, female or group")
    hint: Optional[str] = Field(default=None, description="Free-text note for the selected rows only")
    remember_hint: bool = Field(default=False, description="Also keep the hint in the job's context notes")
    thinking: Optional[str] = Field(default=None, description="Thinking level for this run (default, off, minimal, low, medium, high)")


class GlossaryCharacterEdit(BaseModel):
    name: str
    target_name: str = ""
    gender: str = "unknown"
    notes: str = ""


class GlossaryTermEdit(BaseModel):
    source: str
    translation: str = ""
    notes: str = ""


class GlossaryUpdate(BaseModel):
    summary: str = ""
    characters: List[GlossaryCharacterEdit] = []
    terms: List[GlossaryTermEdit] = []
    register_notes: str = ""
    # Name of a series profile to save this glossary to and use from now on; "" detaches the job
    profile: Optional[str] = None


class SubtitleSegmentSchema(BaseModel):
    id: Optional[int] = None
    job_id: str
    index: int
    start_time: float
    end_time: float
    original_text: str
    translated_text: Optional[str] = None
    translation_failed: bool = False
    segment_type: str = "DIALOGUE"
    confidence: Optional[float] = 1.0
    cps: Optional[float] = 0.0


class SegmentUpdate(BaseModel):
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    original_text: Optional[str] = None
    translated_text: Optional[str] = None


class BulkSegmentUpdate(SegmentUpdate):
    id: int


class RetimeRequest(BaseModel):
    """Optional window (seconds); omitted = the whole video."""
    start: Optional[float] = None
    end: Optional[float] = None


class MoveSegmentRequest(BaseModel):
    segment_id: int
    to_position: int = Field(description="0-based position in time order to drop the row at")


class SegmentsChangeResponse(BaseModel):
    changed: int = 0
    retimed: bool = True
    message: Optional[str] = None
    segments: List["SubtitleSegmentSchema"] = []


class DetectSpeakersRequest(BaseModel):
    segment_ids: List[int]
    gemini_model: Optional[str] = None
    thinking: Optional[str] = None
    gemini_api_key_override: Optional[str] = None


class DetectSpeakersResponse(BaseModel):
    speaker_gender: Optional[str] = None
    addressee_gender: Optional[str] = None
    explanation: str = ""


class QAIssueSchema(BaseModel):
    segment_id: int
    index: int
    kind: str
    message: str


class JobResponse(BaseModel):
    id: str
    video_path: str
    file_name: str
    file_size: Optional[int] = None
    is_upload: bool = False
    status: JobStatus
    current_stage: Optional[str] = None
    progress_percent: float = 0.0
    speed_factor: Optional[float] = None
    eta_seconds: Optional[float] = None
    error_message: Optional[str] = None
    warning_message: Optional[str] = None
    video_duration: Optional[float] = None
    audio_streams: List[AudioStreamInfo] = []
    selected_audio_stream: int = 0
    whisper_model: str = "medium"
    source_language: Optional[str] = "auto"
    detected_language: Optional[str] = None
    vad_filter: bool = True
    comms_boost: bool = False
    detect_onscreen_text: bool = False
    enable_translation: bool = False
    target_language: str = "Hebrew"
    gemini_model: str = "gemini-3.8-flash"
    context_notes: Optional[str] = None
    glossary_profile: Optional[str] = None
    glossary: Optional[Dict[str, Any]] = None
    usage: Optional[Dict[str, Any]] = None
    segment_count: int = 0
    failed_translation_count: int = 0
    segments: List[SubtitleSegmentSchema] = []
    created_at: datetime
    updated_at: datetime


class WebSocketEventPayload(BaseModel):
    # "STAGE_PROGRESS" | "SEGMENT_CREATED" | "SEGMENTS_REFINED" | "BATCH_TRANSLATED"
    # | "JOB_COMPLETED" | "JOB_FAILED" | "JOB_CANCELLED"
    event: str
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
    gemini_models: List[str] = []
    whisper_default_model: str
    whisper_models: List[str] = []
    max_cps_warning: float = 20.0
    hardware: Dict[str, Any]
