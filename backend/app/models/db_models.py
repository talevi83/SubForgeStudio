from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship


class JobStatus(str, Enum):
    IDLE = "IDLE"
    QUEUED = "QUEUED"
    PROBING = "PROBING"
    EXTRACTING_AUDIO = "EXTRACTING_AUDIO"
    TRANSCRIBING = "TRANSCRIBING"
    SCANNING_ONSCREEN = "SCANNING_ONSCREEN"
    REFINING = "REFINING"
    TRANSLATING = "TRANSLATING"
    SYNCING = "SYNCING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SegmentType(str, Enum):
    DIALOGUE = "DIALOGUE"    # regular speech
    COMMS = "COMMS"          # radio / walkie-talkie transmissions
    ON_SCREEN = "ON_SCREEN"  # burned-in titles, signs, forced narratives
    FOREIGN = "FOREIGN"      # speech in another language than the rest (e.g. Russian in an English show)


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
    translation_failed: bool = Field(default=False)
    segment_type: str = Field(default=SegmentType.DIALOGUE.value)
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
    # True when video_path points at a copy inside storage/uploads that this app owns
    # (and may delete together with the job).
    is_upload: bool = Field(default=False)
    status: JobStatus = Field(default=JobStatus.IDLE)
    current_stage: Optional[str] = None
    progress_percent: float = Field(default=0.0)
    speed_factor: Optional[float] = None
    eta_seconds: Optional[float] = None
    error_message: Optional[str] = None
    # Non-fatal problems on a finished job (e.g. some segments failed to translate)
    warning_message: Optional[str] = None
    video_duration: Optional[float] = None
    audio_streams_json: Optional[str] = None  # JSON encoded list of discovered audio streams
    selected_audio_stream: int = Field(default=0)
    whisper_model: str = Field(default="medium")
    source_language: Optional[str] = Field(default="auto")
    detected_language: Optional[str] = None  # ISO code reported by Whisper
    vad_filter: bool = Field(default=True)
    # Radio/walkie-talkie friendly audio filtering and Whisper decoding
    comms_boost: bool = Field(default=False)
    # High speech sensitivity: relaxed VAD & decoding thresholds to catch whispers
    high_speech_sensitivity: bool = Field(default=False)
    # Extract embedded text-based full dialogue subtitles instead of running Whisper
    use_embedded_subtitles: bool = Field(default=True)
    # Forced subtitle tracks + Gemini Vision scan for burned-in narrative text
    detect_onscreen_text: bool = Field(default=False)
    # On-screen text sensitivity: 1=Strict, 2=Balanced, 3=Broad
    onscreen_sensitivity: int = Field(default=2)
    enable_translation: bool = Field(default=False)
    target_language: str = Field(default="Hebrew")
    gemini_model: str = Field(default="gemini-3.8-flash")
    # Deprecated: API keys are no longer persisted. Column kept so old databases still load.
    gemini_api_key_override: Optional[str] = None
    context_notes: Optional[str] = None
    glossary_profile: Optional[str] = None  # Series glossary profile this job shares with other episodes
    glossary_json: Optional[str] = None  # Character/terminology glossary produced before translating
    usage_json: Optional[str] = None  # Gemini tokens and cost per stage (accumulates across runs)
    # "transcribe" (subtitles made from the audio) or "sync" (the user's own subtitle file re-timed)
    job_kind: str = Field(default="transcribe")
    sync_file_name: Optional[str] = None
    sync_source_json: Optional[str] = None  # the uploaded cues (with their original times) and options
    sync_report_json: Optional[str] = None  # how the sync went (methods, offset, frame rate, cuts)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    segments: List[SubtitleSegmentRecord] = Relationship(
        back_populates="job",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "SubtitleSegmentRecord.index"}
    )

    @property
    def effective_source_language(self) -> Optional[str]:
        """The language actually spoken: explicit choice, else what Whisper detected."""
        if self.source_language and self.source_language.lower() not in ("auto", "none"):
            return self.source_language
        return self.detected_language
