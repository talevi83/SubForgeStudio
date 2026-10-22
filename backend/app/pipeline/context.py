import threading
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from app.models.schemas import AudioStreamInfo, SubtitleStreamInfo


class PipelineCancelled(Exception):
    """Raised inside a stage when the user cancelled the job."""


@dataclass
class TranscribedWord:
    word: str
    start: float
    end: float
    probability: float = 1.0


@dataclass
class RawSegment:
    index: int
    start_time: float
    end_time: float
    text: str
    confidence: float = 1.0
    words: List[TranscribedWord] = field(default_factory=list)


@dataclass
class PipelineContext:
    job_id: str
    video_path: str
    file_name: str
    audio_wav_path: Optional[str] = None
    selected_audio_stream: int = 0
    duration: float = 0.0
    video_metadata: Dict[str, Any] = field(default_factory=dict)
    audio_streams: List[AudioStreamInfo] = field(default_factory=list)
    subtitle_streams: List[SubtitleStreamInfo] = field(default_factory=list)
    whisper_model: str = "medium"
    source_language: Optional[str] = "auto"
    detected_language: Optional[str] = None
    vad_filter: bool = True
    comms_boost: bool = False
    use_embedded_subtitles: bool = True
    detect_onscreen_text: bool = False
    enable_translation: bool = False
    target_language: str = "Hebrew"
    gemini_model: str = "gemini-3.8-flash"
    gemini_api_key_override: Optional[str] = None
    context_notes: Optional[str] = None
    glossary: Optional[Dict[str, Any]] = None
    glossary_profile: Optional[str] = None  # shared series glossary (see core/glossary_profiles.py)
    rebuild_glossary: bool = False
    only_failed: bool = False  # translation-only runs: retry just the failed segments
    only_indices: Optional[List[int]] = None  # translation-only runs: translate just these segment indices
    gemini_thinking: Optional[str] = None  # per-run override of the translation thinking level
    translation_hint: Optional[str] = None  # note that applies only to the rows being re-translated
    raw_segments: List[RawSegment] = field(default_factory=list)
    # Embedded full dialogue subtitle track extracted from video container (skips Whisper transcription)
    embedded_dialogue_segments: List[Dict[str, Any]] = field(default_factory=list)
    embedded_subtitle_stream: Optional[SubtitleStreamInfo] = None
    # ON_SCREEN segments from forced tracks / frame OCR, merged into the timeline by RefineStage
    onscreen_segments: List[Dict[str, Any]] = field(default_factory=list)
    # Passages spoken in another language (or speech Whisper produced nothing for):
    # [{"start", "end", "language", "probability", "reason"}]
    foreign_spans: List[Dict[str, Any]] = field(default_factory=list)
    # Fallback text for those passages: Whisper re-run in the passage's own language
    foreign_audio_segments: List[RawSegment] = field(default_factory=list)
    # Preferred text for those passages: the show's burned-in (or forced-track) subtitles
    foreign_ocr_segments: List[Dict[str, Any]] = field(default_factory=list)
    # Background ffmpeg frame sampling started early so it overlaps with transcription
    frame_task: Optional[Any] = None
    # Exact Gemini token usage / cost of this run, per stage
    usage: Any = None
    # Flex tier bookkeeping shared by every Gemini call of this run (see providers/gemini_calls.py)
    flex_state: Any = None
    # Non-fatal notes shown to the user when the job completes
    warnings: List[str] = field(default_factory=list)
    refined_segments: List[Dict[str, Any]] = field(default_factory=list)
    translated_segments: List[Dict[str, Any]] = field(default_factory=list)
    translation_warning: Optional[str] = None
    # "Sync existing subtitles" jobs: the user's cues ([{start, end, text, position, kind}]),
    # their language, whether Gemini may match lines to the speech, and the result summary
    sync_cues: Optional[List[Dict[str, Any]]] = None
    sync_file_name: Optional[str] = None
    sync_language: Optional[str] = None
    sync_use_gemini: bool = True
    sync_report: Optional[Dict[str, Any]] = None
    # Background burned-in subtitle detection (started right after audio extraction)
    burned_task: Optional[Any] = None
    # Thread-safe flag so worker threads (Whisper) can observe cancellation
    cancel_flag: threading.Event = field(default_factory=threading.Event)

    @property
    def is_cancelled(self) -> bool:
        return self.cancel_flag.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancel_flag.is_set():
            raise PipelineCancelled()

    @property
    def effective_source_language(self) -> Optional[str]:
        if self.source_language and self.source_language.lower() not in ("auto", "none"):
            return self.source_language
        return self.detected_language
