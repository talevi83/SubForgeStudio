from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship


class JobStatus(str, Enum):
    IDLE = "IDLE"
    PROBING = "PROBING"
    EXTRACTING_AUDIO = "EXTRACTING_AUDIO"
    TRANSCRIBING = "TRANSCRIBING"
    REFINING = "REFINING"
    TRANSLATING = "TRANSLATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SubtitleSegmentRecord(SQLModel, table=True):
    """Database entity representing an individual subtitle segment block."""
    __tablename__ = "subtitle_segments"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(index=True, foreign_key="jobs.id")
    index: int = Field(index=True)
    start_time: float
    end_time: float
    original_text: str
    translated_text: Optional[str] = None
    confidence: Optional[float] = 1.0
    cps: Optional[float] = 0.0

    job: Optional["Job"] = Relationship(back_populates="segments")


class Job(SQLModel, table=True):
    """Database entity representing a video subtitling job."""
    __tablename__ = "jobs"

    id: str = Field(primary_key=True, index=True)
    video_path: str
    file_name: str
    file_size: Optional[int] = None
    status: JobStatus = Field(default=JobStatus.IDLE)
    current_stage: Optional[str] = None
    progress_percent: float = Field(default=0.0)
    speed_factor: Optional[float] = None
    eta_seconds: Optional[float] = None
    error_message: Optional[str] = None
    video_duration: Optional[float] = None
    audio_streams_json: Optional[str] = None  # JSON encoded list of discovered audio streams
    selected_audio_stream: int = Field(default=0)
    whisper_model: str = Field(default="medium")
    source_language: Optional[str] = Field(default="auto")
    vad_filter: bool = Field(default=True)
    enable_translation: bool = Field(default=False)
    target_language: str = Field(default="Hebrew")
    gemini_model: str = Field(default="gemini-2.5-flash")
    gemini_api_key_override: Optional[str] = None
    context_notes: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    segments: List[SubtitleSegmentRecord] = Relationship(
        back_populates="job",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "SubtitleSegmentRecord.index"}
    )
