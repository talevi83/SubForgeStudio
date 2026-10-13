from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from app.models.schemas import AudioStreamInfo


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
    whisper_model: str = "medium"
    source_language: Optional[str] = "auto"
    vad_filter: bool = True
    enable_translation: bool = False
    target_language: str = "Hebrew"
    gemini_model: str = "gemini-2.5-flash"
    gemini_api_key_override: Optional[str] = None
    context_notes: Optional[str] = None
    raw_segments: List[RawSegment] = field(default_factory=list)
    refined_segments: List[Dict[str, Any]] = field(default_factory=list)
    translated_segments: List[Dict[str, Any]] = field(default_factory=list)
    is_cancelled: bool = False
