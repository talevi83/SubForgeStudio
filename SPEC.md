# SubForge Studio — Master Technical Specification (`SPEC.md`)

## 1. Project Overview & Vision
**SubForge Studio** is a production-grade, modular local web application that ingests video files (movies, TV episodes), probes media streams, extracts and preprocesses audio, performs high-precision local speech-to-text transcription with word-level timing, refines subtitles to broadcast standards, optionally translates subtitles into target languages via the **Google Gemini API** (with first-class RTL/Hebrew support), and provides an interactive real-time Web UI for live monitoring, video-synced previewing, inline editing, and SRT/VTT exporting.

### Core Engineering Rules
1. **English-Only Code & Comments:** All code comments, docstrings, variable names, commit messages, and logs MUST be written strictly in **English**.
2. **Modular Pipeline & Provider Architecture:** Use a Pipeline + Strategy/Provider pattern. Transcription and Translation engines must implement abstract base interfaces (`BaseTranscriber`, `BaseTranslator`) so the pipeline remains clean, testable, and extensible.
3. **Non-Blocking Async Execution:** Heavy media and AI tasks (`ffmpeg`, `faster-whisper`, Gemini API batch translation) must never block the FastAPI async event loop. Execute blocking work in background worker threads/processes and stream live updates via WebSockets.
4. **Zero-Copy Local File Support:** Because video files are large (1GB–20GB+), the system must support both standard browser file uploads AND direct local filesystem path selection without copying the video file unnecessarily.
5. **Official GenAI SDK & Env Management:** Use `google-genai>=1.0.0` (`from google import genai`) for Gemini integration. Do NOT use the deprecated `google-generativeai` package. All sensitive keys and default parameters must be loaded from `backend/.env` via `pydantic-settings`.

---

## 2. Technology Stack

### Backend (`/backend`)
* **Runtime & Web Framework:** Python 3.11+, FastAPI, Uvicorn
* **Configuration:** `pydantic-settings` loading from `backend/.env` (with automatic creation of `.env.example`)
* **Database & Persistence:** SQLite with SQLModel (or SQLAlchemy + Pydantic v2) for persisting jobs, pipeline configurations, and subtitle segments
* **Media Processing:** `ffmpeg` & `ffprobe` (invoked via async subprocesses)
* **Speech-to-Text Engine:** `faster-whisper` (CTranslate2 backend) supporting `cuda` (`float16`) and `cpu` (`int8`) with automatic hardware detection and Silero VAD filtering
* **Translation Engine:** `google-genai` (`gemini-2.5-flash` structured batch translation) + `NoopTranslator` when translation is disabled
* **Real-Time Communication:** FastAPI WebSockets (`ws://`) for live progress, processing speed metrics, and real-time segment streaming
* **Testing:** `pytest`, `pytest-asyncio`, `httpx`

### Frontend (`/frontend`)
* **Framework:** React 18+, TypeScript, Vite
* **Styling & UI Components:** Tailwind CSS, Shadcn UI (Radix primitives), Lucide React icons
* **State Management:** Zustand (active job state, live WebSocket segment stream, and inline editor state)
* **Video & Sync:** HTML5 Video Player with HTTP Range streaming support + dynamic WebVTT blob track injection for instant on-video subtitle previewing

---

## 3. Repository Directory Structure

```text
subforge-studio/
├── SPEC.md
├── README.md
├── backend/
│   ├── .env                        # Local environment variables (gitignored)
│   ├── .env.example                # Template environment variables
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py                 # FastAPI app initialization, lifespan & CORS
│   │   ├── core/
│   │   │   ├── config.py           # Pydantic BaseSettings (.env loader)
│   │   │   ├── database.py         # SQLite engine & session management
│   │   │   ├── hardware.py         # GPU/VRAM (CUDA) & CPU detection utility
│   │   │   └── ws_manager.py       # WebSocket connection & broadcast manager
│   │   ├── models/
│   │   │   ├── db_models.py        # SQLModel tables: Job, SubtitleSegmentRecord
│   │   │   └── schemas.py          # Pydantic API request/response schemas
│   │   ├── pipeline/
│   │   │   ├── orchestrator.py     # Executes pipeline stages sequentially
│   │   │   ├── context.py          # Shared state object passed across stages
│   │   │   └── stages/
│   │   │       ├── base.py         # Abstract PipelineStage interface
│   │   │       ├── probe_stage.py  # FFprobe metadata & audio stream discovery
│   │   │       ├── audio_stage.py  # FFmpeg 16kHz mono WAV extraction
│   │   │       ├── transcribe_stage.py # Invokes FasterWhisperTranscriber
│   │   │       ├── refine_stage.py # Smart line splitting, CPS & gap smoothing
│   │   │       └── translate_stage.py  # Invokes GeminiTranslator (Optional)
│   │   ├── providers/
│   │   │   ├── transcription/
│   │   │   │   ├── base.py         # Abstract BaseTranscriber
│   │   │   │   └── faster_whisper.py   # Faster-Whisper implementation
│   │   │   └── translation/
│   │   │       ├── base.py         # Abstract BaseTranslator & TranslatableSegment
│   │   │       ├── noop.py         # Pass-through when translation is disabled
│   │   │       └── gemini_provider.py  # Context-aware batch translation via Gemini API
│   │   ├── utils/
│   │   │   ├── srt_formatter.py    # SRT/VTT serialization & timestamp formatting
│   │   │   └── rtl_fixer.py        # BiDi / Hebrew punctuation & RLM fixer
│   │   └── api/
│   │       ├── routes_media.py     # Probe local paths, upload files, stream video
│   │       ├── routes_jobs.py      # CRUD jobs, start/cancel pipeline, edit segments
│   │       ├── routes_export.py    # Download .srt / .vtt (original or translated)
│   │       └── routes_system.py    # Hardware status, env config check & API key status
│   └── tests/
│       ├── test_srt_formatter.py
│       ├── test_refine_stage.py
│       └── test_rtl_fixer.py
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── index.html
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── types/
        │   └── index.ts            # Shared TS interfaces matching backend schemas
        ├── store/
        │   └── useJobStore.ts      # Zustand store + WebSocket event handler
        ├── services/
        │   └── api.ts              # Typed REST client
        └── components/
            ├── layout/
            │   ├── Sidebar.tsx     # Job history, hardware badge, API key status badge
            │   └── Header.tsx
            ├── ingest/
            │   ├── MediaDropzone.tsx   # Drag & Drop + Local File Path input
            │   ├── StreamSelector.tsx  # Select specific audio track from MKV/MP4
            │   └── PipelineConfig.tsx  # Whisper model, source lang, Gemini translation toggle
            ├── monitor/
            │   ├── LivePipelineBar.tsx # Step-by-step stage progress, ETA & speed (x RT)
            │   └── LiveStreamFeed.tsx  # Auto-scrolling real-time segment feed
            └── studio/
                ├── VideoSyncPlayer.tsx # Video player with live subtitle overlay
                ├── SubtitleTable.tsx   # Virtualized inline editor (Original + Translated)
                ├── CpsBadge.tsx        # Reading speed indicator (Chars Per Second)
                └── ExportToolbar.tsx   # Format selector, RTL fixer toggle, Download CTA
```

---

## 4. Core Domain Models & Data Contracts

### 4.1 Job State Machine (`JobStatus`)
* `IDLE` — Created, awaiting start
* `PROBING` — Extracting video duration and audio tracks via `ffprobe`
* `EXTRACTING_AUDIO` — Converting selected audio stream to 16kHz mono WAV
* `TRANSCRIBING` — Running Speech-to-Text; streaming segments via WebSocket
* `REFINING` — Applying broadcast subtitle heuristics (CPL, CPS, timing gaps)
* `TRANSLATING` — Running Gemini context-aware batch translation (if enabled)
* `COMPLETED` — Ready for studio review and export
* `FAILED` — Error occurred (with detailed error message)
* `CANCELLED` — Terminated by user

### 4.2 Subtitle Segment Schema (`SubtitleSegment`)
```json
{
  "id": "uuid-or-int",
  "job_id": "string",
  "index": 1,
  "start_time": 12.340,
  "end_time": 15.120,
  "original_text": "We need to secure the perimeter\nbefore nightfall.",
  "translated_text": "\u200fאנחנו צריכים לאבטח את ההיקף\u200f\n\u200fלפני רדת החשיכה.\u200f",
  "confidence": 0.94,
  "cps": 17.6
}
```

### 4.3 WebSocket Event Payload (`ws://localhost:8000/ws/jobs/{job_id}`)
```json
{
  "event": "STAGE_PROGRESS | SEGMENT_CREATED | BATCH_TRANSLATED | JOB_COMPLETED | JOB_FAILED",
  "job_id": "string",
  "stage": "TRANSCRIBING",
  "progress_percent": 42.5,
  "speed_factor": 4.8,
  "eta_seconds": 115,
  "segment": null,
  "segments_batch": [],
  "message": "Optional status or error detail"
}
```

---

## 5. Deep-Dive Specifications & Reference Implementations

### 5.1 Environment Configuration (`backend/.env` & `backend/app/core/config.py`)
The backend MUST include a `backend/.env.example` file and load `backend/.env` automatically via `pydantic-settings`. If `.env` does not exist yet on first boot, the app should still start gracefully and report whether `GEMINI_API_KEY` is configured via `/api/system/info` (and allow optional runtime API key override from the UI).

#### `backend/.env.example`
```env
# Google Gemini API Configuration (Required for Subtitle Translation)
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
GEMINI_BATCH_SIZE=30

# Faster-Whisper Local Transcription Defaults
WHISPER_DEFAULT_MODEL=medium
WHISPER_DEVICE=auto
WHISPER_COMPUTE_TYPE=auto

# Subtitle Formatting Rules
MAX_CHARS_PER_LINE=42
MAX_CPS_WARNING=20.0

# Paths & Server Configuration
STORAGE_DIR=./storage
DATABASE_URL=sqlite:///./storage/subforge.db
HOST=0.0.0.0
PORT=8000
```

#### `backend/app/core/config.py`
```python
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    # Gemini API Settings
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_batch_size: int = 30

    # Whisper Settings
    whisper_default_model: str = "medium"
    whisper_device: str = "auto"          # "auto", "cuda", or "cpu"
    whisper_compute_type: str = "auto"    # "auto", "float16", or "int8"

    # Subtitle Rules
    max_chars_per_line: int = 42
    max_cps_warning: float = 20.0

    # Storage & Database
    storage_dir: Path = Path("./storage")
    database_url: str = "sqlite:///./storage/subforge.db"
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_gemini_configured(self) -> bool:
        """Return True if a valid non-placeholder Gemini API key is present."""
        return bool(
            self.gemini_api_key
            and self.gemini_api_key.strip()
            and self.gemini_api_key != "your_gemini_api_key_here"
        )


settings = Settings()
settings.storage_dir.mkdir(parents=True, exist_ok=True)
(settings.storage_dir / "uploads").mkdir(parents=True, exist_ok=True)
(settings.storage_dir / "audio").mkdir(parents=True, exist_ok=True)
(settings.storage_dir / "exports").mkdir(parents=True, exist_ok=True)
```

---

### 5.2 Audio Extraction Stage (`backend/app/pipeline/stages/audio_stage.py`)
Extract only the user-selected audio stream (`stream_index`) into a 16kHz mono PCM WAV file to minimize RAM/VRAM footprint before running Whisper.

```python
import asyncio


async def extract_audio_track(
    video_path: str,
    output_wav_path: str,
    audio_stream_index: int = 0,
) -> None:
    """Extract a specific audio stream converted to 16kHz mono WAV using ffmpeg."""
    command = [
        "ffmpeg",
        "-y",                               # Overwrite output if exists
        "-i", video_path,
        "-map", f"0:a:{audio_stream_index}", # Select specific audio stream
        "-vn",                              # Disable video stream
        "-acodec", "pcm_s16le",             # Raw 16-bit PCM
        "-ar", "16000",                     # Whisper optimal sample rate (16kHz)
        "-ac", "1",                         # Mono channel
        output_wav_path,
    ]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode(errors="ignore") if stderr else "Unknown FFmpeg error"
        raise RuntimeError(f"FFmpeg audio extraction failed: {error_msg}")
```

---

### 5.3 Smart Segmentation, Broadcast Rules & SRT Formatter (`backend/app/utils/srt_formatter.py`)
1. **Max Characters Per Line (CPL):** Default `42` characters.
2. **Max Lines Per Subtitle:** `2` lines.
3. **Balanced Line Splitting:** If a line is between 43 and 84 characters, split it into two visually balanced lines at word boundaries. If a raw Whisper segment exceeds 84 characters, use word-level timestamps (`word_timestamps=True`) in `refine_stage.py` to split it into separate timed subtitle blocks.
4. **Duration Constraints:** Minimum segment duration `0.8s`, maximum `7.0s`.
5. **Reading Speed (CPS):** Calculate `len(clean_text.replace("\n", "")) / duration`. Flag segments where `cps > 20` in the UI.

```python
import math
from typing import List
from app.utils.rtl_fixer import apply_rtl_fix, strip_directional_marks


def format_srt_timestamp(seconds: float) -> str:
    """Convert float seconds to SRT timecode: HH:MM:SS,mmm"""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - math.floor(seconds)) * 1000))
    if millis == 1000:
        secs += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_vtt_timestamp(seconds: float) -> str:
    """Convert float seconds to WebVTT timecode: HH:MM:SS.mmm"""
    return format_srt_timestamp(seconds).replace(",", ".")


def split_long_lines(text: str, max_chars_per_line: int = 42) -> str:
    """Split single-line text into maximum two balanced lines if it exceeds limit."""
    if "\n" in text:
        return text

    words = text.split()
    if len(text) <= max_chars_per_line or len(words) <= 1:
        return text

    mid = len(text) // 2
    best_split = min(
        range(1, len(words)),
        key=lambda i: abs(len(" ".join(words[:i])) - mid),
    )
    line1 = " ".join(words[:best_split])
    line2 = " ".join(words[best_split:])
    return f"{line1}\n{line2}"


def serialize_to_srt(
    segments: List[dict],
    use_translated: bool = False,
    apply_rtl: bool = True,
    max_chars_per_line: int = 42,
) -> str:
    """Serialize segment dictionaries into a standard .srt file string."""
    blocks = []
    for idx, seg in enumerate(segments, start=1):
        raw_text = (
            seg.get("translated_text") if use_translated and seg.get("translated_text")
            else seg.get("original_text", "")
        )
        clean_text = strip_directional_marks(raw_text.strip())
        formatted_text = split_long_lines(clean_text, max_chars_per_line=max_chars_per_line)

        if apply_rtl:
            formatted_text = apply_rtl_fix(formatted_text)

        start_tc = format_srt_timestamp(seg["start_time"])
        end_tc = format_srt_timestamp(seg["end_time"])
        blocks.append(f"{idx}\n{start_tc} --> {end_tc}\n{formatted_text}\n")

    return "\n".join(blocks)
```

---

### 5.4 RTL / Hebrew BiDi Fixer (`backend/app/utils/rtl_fixer.py`)
Use this exact reference implementation to prevent trailing punctuation (`?`, `!`, `.`, `,`) and leading dialogue hyphens (`- `) from jumping to the wrong side of Hebrew/Arabic lines in media players.

```python
import re

# Unicode directional control characters
RLM = "\u200f"  # Right-to-Left Mark
LRM = "\u200e"  # Left-to-Right Mark
RLE = "\u202b"  # Right-to-Left Embedding
LRE = "\u202a"  # Left-to-Right Embedding
PDF = "\u202c"  # Pop Directional Formatting

# Regex matching any Hebrew or Arabic character
RTL_CHAR_PATTERN = re.compile(r"[\u0590-\u05FF\u0600-\u06FF]")

# Regex to strip existing directional control characters for idempotency
CONTROL_CHARS_PATTERN = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def contains_rtl(text: str) -> bool:
    """Check whether the given string contains Hebrew or Arabic characters."""
    return bool(RTL_CHAR_PATTERN.search(text))


def strip_directional_marks(text: str) -> str:
    """Remove any existing Unicode BiDi control characters from the text."""
    return CONTROL_CHARS_PATTERN.sub("", text)


def fix_rtl_line(line: str, use_embedding: bool = False) -> str:
    """
    Fix BiDi punctuation placement for a single subtitle line containing RTL text.

    By sandwiching the line between RLM characters (start and end), neutral
    characters such as leading dialogue hyphens ('- ') and trailing punctuation
    ('?', '!', '.', ',') are forced into the RTL layout context across all
    standard media players (VLC, MPV, Plex, HTML5 WebVTT).
    """
    clean_line = strip_directional_marks(line).strip()
    if not clean_line:
        return ""

    if not contains_rtl(clean_line):
        return clean_line

    # Normalize dialogue hyphen spacing (e.g., "-Hello" -> "- Hello")
    if clean_line.startswith("-") and not clean_line.startswith("- "):
        clean_line = "- " + clean_line[1:].lstrip()

    if use_embedding:
        return f"{RLE}{RLM}{clean_line}{RLM}{PDF}"

    return f"{RLM}{clean_line}{RLM}"


def apply_rtl_fix(text: str, use_embedding: bool = False) -> str:
    """
    Apply BiDi RTL fixes line-by-line to a potentially multi-line subtitle block.
    Safe to call multiple times (idempotent).
    """
    if not text:
        return ""

    lines = text.split("\n")
    fixed_lines = [fix_rtl_line(line, use_embedding=use_embedding) for line in lines]
    return "\n".join(fixed_lines)
```

---

### 5.5 Translation Base Interface (`backend/app/providers/translation/base.py`)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Awaitable, List, Optional


@dataclass
class TranslatableSegment:
    """Lightweight data contract passed into translation providers."""
    index: int
    start_time: float
    end_time: float
    original_text: str
    translated_text: Optional[str] = None

    @property
    def duration(self) -> float:
        return round(max(0.1, self.end_time - self.start_time), 2)


# Callback type emitted after each batch completes for WebSocket streaming
BatchProgressCallback = Callable[[List[TranslatableSegment], float], Awaitable[None]]


class BaseTranslator(ABC):
    """Abstract interface for all subtitle translation providers."""

    @abstractmethod
    async def translate_segments(
        self,
        segments: List[TranslatableSegment],
        source_lang: str,
        target_lang: str,
        context_notes: str = "",
        apply_rtl: bool = True,
        on_batch_complete: Optional[BatchProgressCallback] = None,
    ) -> List[TranslatableSegment]:
        """
        Translate a full list of subtitle segments while strictly preserving
        indices and timestamps.
        """
        pass
```

---

### 5.6 Gemini Context-Aware Batch Translator (`backend/app/providers/translation/gemini_provider.py`)
Use this exact reference implementation. It features a bidirectional sliding window (`previous_context` + `lookahead_context`), duration-aware constraints, Pydantic structured output enforcement, automatic retries with exponential backoff, and RTL BiDi post-processing. It automatically falls back to `settings.gemini_api_key` and `settings.gemini_model` from `.env` if not explicitly passed.

```python
import asyncio
import json
import logging
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from app.core.config import settings
from app.providers.translation.base import (
    BaseTranslator,
    TranslatableSegment,
    BatchProgressCallback,
)
from app.utils.rtl_fixer import apply_rtl_fix

logger = logging.getLogger(__name__)


# =====================================================================
# Pydantic Schemas for Gemini Structured Output
# =====================================================================

class TranslatedItemSchema(BaseModel):
    index: int = Field(description="The exact index of the subtitle segment from the input.")
    translated_text: str = Field(
        description="The translated subtitle text. Use '\\n' if split into two lines (max 2 lines)."
    )


class TranslationBatchResponseSchema(BaseModel):
    translations: List[TranslatedItemSchema] = Field(
        description="List of translated subtitle segments matching the exact input indices."
    )


# =====================================================================
# Broadcast-Grade System Prompt Builder
# =====================================================================

def build_subtitle_system_prompt(
    source_lang: str,
    target_lang: str,
    context_notes: str = "",
    max_chars_per_line: int = 42,
) -> str:
    """Construct the system prompt engineered for cinema/broadcast subtitle translation."""
    extra_context = (
        f"\n### SHOW / MOVIE SPECIFIC CONTEXT:\n{context_notes.strip()}\n"
        if context_notes.strip()
        else ""
    )

    return f"""You are an elite, broadcast-grade audiovisual subtitle translator specializing in translating movies and TV series from {source_lang} to {target_lang}.

### CORE OBJECTIVE
Translate the `target_batch` subtitle segments into natural, idiomatic, spoken {target_lang} suitable for cinema and television (Netflix/HBO broadcast standards), while strictly maintaining a 1-to-1 mapping with the original segment `index`.
{extra_context}
### STRICT TECHNICAL RULES
1. **1-to-1 Index Mapping:**
   - You MUST return the exact same number of items as provided in `target_batch`.
   - Every returned item MUST have the exact `index` from `target_batch`.
   - NEVER merge two segments into one, and NEVER skip or invent an index.
   - Do NOT translate items from `previous_context` or `lookahead_context`—those are provided strictly for narrative continuity.

2. **Line Length & Formatting Constraints:**
   - Maximum **{max_chars_per_line} characters per line**.
   - Maximum **2 lines per subtitle segment** (separated by a single `\\n`).
   - If a translation exceeds {max_chars_per_line} characters, split it into two visually balanced lines at a natural linguistic boundary (e.g., after a comma, conjunction, or preposition).
   - Respect `duration_sec`: If a segment is very short (e.g., under 1.5s), keep the phrasing concise so the viewer can read it comfortably (aim for 18 characters per second or fewer).

3. **Split Sentences & Continuity:**
   - Sentences frequently span across two or more consecutive subtitle segments.
   - Inspect `previous_context` and `lookahead_context` to understand the full sentence before translating.
   - Ensure each segment contains the semantic portion spoken during that exact timeframe so the text stays synchronized with the actor's speech on screen.

4. **Dialogue & Dual Speakers:**
   - If the original segment contains two speakers (indicated by leading hyphens, e.g., `- Hello.\\n- Hi.`), you MUST preserve the two-line hyphen structure (`- ...\\n- ...`).

5. **Natural Spoken Hebrew & Linguistic Guidelines (When Target is Hebrew):**
   - **Avoid "Translationese" (תרגומית):** Do not translate word-for-word. Translate the intent, subtext, and natural Hebrew conversational flow (e.g., translate "Are you kidding me?" as "אתה צוחק עליי?" or "את עובדת עליי?", never literally).
   - **Gender & Plurality Resolution:** English "you" or gender-neutral verbs must be conjugated accurately into masculine/feminine and singular/plural based on speaker/listener clues in the surrounding context and show notes.
   - **Slang, Humor & Profanity:** Preserve the exact register, tone, and intensity of the original dialogue. Do not censor profanity; use authentic Hebrew equivalents that match the character's voice.
   - **Proper Nouns & Technical Terms:** Transliterate character names consistently across batches. Keep standard punctuation at the logical end of sentences (do NOT manually reverse punctuation; a downstream BiDi processor handles Unicode RTL marks).
"""


# =====================================================================
# Gemini Translator Implementation
# =====================================================================

class GeminiTranslator(BaseTranslator):
    """
    Context-aware batch subtitle translator powered by the Google GenAI SDK.
    Loads defaults from backend/.env via settings, supports sliding context windows,
    structured JSON outputs, automatic retries, and RTL BiDi post-processing.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        batch_size: Optional[int] = None,
        prev_context_size: int = 5,
        lookahead_size: int = 3,
        max_chars_per_line: Optional[int] = None,
        max_retries: int = 3,
    ):
        resolved_key = api_key or settings.gemini_api_key
        if not resolved_key or resolved_key == "your_gemini_api_key_here":
            raise ValueError(
                "GEMINI_API_KEY is missing. Set it in backend/.env or provide it in the UI."
            )

        self.client = genai.Client(api_key=resolved_key)
        self.model_name = model_name or settings.gemini_model
        self.batch_size = batch_size or settings.gemini_batch_size
        self.prev_context_size = prev_context_size
        self.lookahead_size = lookahead_size
        self.max_chars_per_line = max_chars_per_line or settings.max_chars_per_line
        self.max_retries = max_retries

    async def translate_segments(
        self,
        segments: List[TranslatableSegment],
        source_lang: str = "English",
        target_lang: str = "Hebrew",
        context_notes: str = "",
        apply_rtl: bool = True,
        on_batch_complete: Optional[BatchProgressCallback] = None,
    ) -> List[TranslatableSegment]:
        """Translate all segments in batches with sliding context."""
        if not segments:
            return []

        total_segments = len(segments)
        system_prompt = build_subtitle_system_prompt(
            source_lang=source_lang,
            target_lang=target_lang,
            context_notes=context_notes,
            max_chars_per_line=self.max_chars_per_line,
        )

        is_rtl_target = target_lang.lower() in {"hebrew", "he", "עברית", "arabic", "ar"}

        for start_idx in range(0, total_segments, self.batch_size):
            end_idx = min(start_idx + self.batch_size, total_segments)
            current_batch = segments[start_idx:end_idx]

            # Gather already-translated previous segments for continuity
            prev_start = max(0, start_idx - self.prev_context_size)
            prev_context = segments[prev_start:start_idx]

            # Gather upcoming untranslated segments for split-sentence awareness
            lookahead_end = min(total_segments, end_idx + self.lookahead_size)
            lookahead_context = segments[end_idx:lookahead_end]

            await self._translate_single_batch(
                target_batch=current_batch,
                prev_context=prev_context,
                lookahead_context=lookahead_context,
                system_prompt=system_prompt,
                apply_rtl=(apply_rtl and is_rtl_target),
            )

            # Calculate overall progress and notify WebSocket listener if provided
            progress_pct = round((end_idx / total_segments) * 100, 2)
            logger.info(
                f"Translated batch {start_idx + 1}-{end_idx}/{total_segments} ({progress_pct}%)"
            )

            if on_batch_complete:
                await on_batch_complete(current_batch, progress_pct)

        return segments

    async def _translate_single_batch(
        self,
        target_batch: List[TranslatableSegment],
        prev_context: List[TranslatableSegment],
        lookahead_context: List[TranslatableSegment],
        system_prompt: str,
        apply_rtl: bool,
    ) -> None:
        """Execute a single batch translation request with retry and index verification."""
        payload = {
            "previous_context": [
                {
                    "index": seg.index,
                    "original_text": seg.original_text,
                    "translated_text": seg.translated_text or "",
                }
                for seg in prev_context
            ],
            "target_batch": [
                {
                    "index": seg.index,
                    "duration_sec": seg.duration,
                    "original_text": seg.original_text,
                }
                for seg in target_batch
            ],
            "lookahead_context": [
                {
                    "index": seg.index,
                    "original_text": seg.original_text,
                }
                for seg in lookahead_context
            ],
        }

        user_prompt = json.dumps(payload, ensure_ascii=False, indent=2)

        for attempt in range(1, self.max_retries + 1):
            try:
                # Run synchronous SDK call in a worker thread to avoid blocking the event loop
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        response_schema=TranslationBatchResponseSchema,
                        temperature=0.2,  # Low temperature for faithful, consistent translation
                    ),
                )

                parsed_response = TranslationBatchResponseSchema.model_validate_json(
                    response.text
                )
                translation_map = {
                    item.index: item.translated_text
                    for item in parsed_response.translations
                }

                # Populate translated text back onto the segment objects
                missing_indices = []
                for seg in target_batch:
                    if seg.index in translation_map:
                        raw_translation = translation_map[seg.index].strip()
                        seg.translated_text = (
                            apply_rtl_fix(raw_translation)
                            if apply_rtl
                            else raw_translation
                        )
                    else:
                        missing_indices.append(seg.index)

                if missing_indices:
                    raise ValueError(
                        f"Gemini response omitted segment indices: {missing_indices}"
                    )

                return  # Batch succeeded

            except Exception as exc:
                logger.warning(
                    f"Batch translation attempt {attempt}/{self.max_retries} failed: {exc}"
                )
                if attempt == self.max_retries:
                    logger.error(
                        "Max retries reached for batch. Falling back to original text for missing segments."
                    )
                    for seg in target_batch:
                        if not seg.translated_text:
                            seg.translated_text = seg.original_text
                else:
                    # Exponential backoff before retry
                    await asyncio.sleep(1.5 * (2 ** (attempt - 1)))
```

---

### 5.7 Video Streaming Endpoint (`GET /api/media/stream/{job_id}`)
Support HTTP `Range` requests (`206 Partial Content`) so the frontend HTML5 `<video>` player can seek smoothly through large local video files without loading the entire file into memory.

---

## 6. UI/UX Specification & Studio Workspace Flows

### Visual Identity
* **Theme:** Sleek Cinema Dark Mode (`#09090b` background, `#18181b` surface cards, `#6366f1` indigo primary accents, emerald/amber/rose semantic status badges).
* **Typography:** `Inter` or `Plus Jakarta Sans` for UI controls, `JetBrains Mono` for timecodes (`00:01:24,450`), and `Heebo` / `Rubik` for clear RTL Hebrew subtitle editing.

### Screen 1: Media Ingest & Pipeline Setup
1. **Dual Input Card:**
   * **Tab A — Local File Path:** Direct text input with instant file-existence check and `ffprobe` metadata inspection (ideal for multi-gigabyte MKV/MP4 files already on disk).
   * **Tab B — Drag & Drop Upload:** Standard browser file dropzone with upload progress bar.
2. **Media Inspector Card (Appears immediately after probing):**
   * Displays video resolution, duration, container format, and a **Dropdown to select the exact Audio Track** (e.g., `Track #0: English (AAC 5.1)`, `Track #1: Japanese (FLAC 2.0)`).
3. **Pipeline Configuration:**
   * **Transcription Settings:** Whisper Model selector (`tiny`, `base`, `small`, `medium`, `large-v3`), Source Language (`Auto-detect` or explicit code), VAD Filter toggle.
   * **Gemini Translation Step (Optional Toggle):**
     * Enable/Disable toggle.
     * Target Language selector (`Hebrew` default, `English`, `Spanish`, `Russian`, etc.).
     * Gemini Model selector (`gemini-2.5-flash` default, `gemini-2.5-pro`).
     * Status badge indicating whether `GEMINI_API_KEY` is already loaded from `backend/.env` (with an optional input field to override the key for the current session if needed).
     * Optional **Show / Context Notes** textarea (e.g., *"Sci-fi thriller, main protagonist Sarah speaks in feminine singular"*).

### Screen 2: Live Execution & Studio Workspace
* **Top Pipeline Stepper:** Interactive 5-stage progress bar (`Probe` -> `Extract Audio` -> `Transcribe` -> `Refine` -> `Translate`) displaying live completion percentage, processing speed badge (e.g., `5.2x Real-Time`), ETA countdown, and active compute device badge (`CUDA FP16` or `CPU INT8`).
* **Left Pane (40% width) — Video Sync Player:**
  * Streams the source video with a live dynamic WebVTT subtitle overlay.
  * Includes a quick toggle to switch the video overlay between **Original**, **Translated (RTL)**, or **Dual Stacked** view.
* **Right Pane (60% width) — Interactive Subtitle Table:**
  * Columns: `#`, `Start / End Timecode` (with `+/- 100ms` nudge buttons), `Original Text` (editable auto-resizing textarea), `Translated Text` (with `dir="rtl"` when target is Hebrew/Arabic), and `CPS Badge` (green when `<= 18`, amber `18-20`, red `> 20`).
  * **Live Stream & Auto-Scroll:** During transcription and translation, rows populate in real time via WebSocket. Clicking any row seeks the video player to `start_time`.
  * **Search & Replace Bar:** Instant filter and bulk find-and-replace across original or translated columns.
* **Export Toolbar:**
  * One-click download for `.srt` or `.vtt` (Original or Translated).
  * Checkbox toggle: **Apply RTL BiDi Punctuation Fix (Hebrew/Arabic)** (enabled by default for RTL targets).

---

## 7. Automated Verification & Unit Tests (`backend/tests/test_rtl_fixer.py`)

Include and run these unit tests alongside `test_srt_formatter.py` to verify text formatting and BiDi safety:

```python
from app.utils.rtl_fixer import (
    RLM,
    contains_rtl,
    fix_rtl_line,
    apply_rtl_fix,
)


def test_contains_rtl_detects_hebrew():
    assert contains_rtl("שלום עולם") is True
    assert contains_rtl("Hello world!") is False


def test_fix_rtl_line_wraps_hebrew_with_rlm():
    line = "אתה צוחק עליי?"
    fixed = fix_rtl_line(line)
    assert fixed == f"{RLM}אתה צוחק עליי?{RLM}"


def test_fix_rtl_line_leaves_english_untouched():
    line = "Wait for me!"
    assert fix_rtl_line(line) == "Wait for me!"


def test_fix_rtl_line_normalizes_dialogue_hyphen():
    line = "-לא עכשיו!\n-למה?"
    fixed = apply_rtl_fix(line)
    expected = f"{RLM}- לא עכשיו!{RLM}\n{RLM}- למה?{RLM}"
    assert fixed == expected


def test_apply_rtl_fix_is_idempotent():
    line = "זה עובד מצוין..."
    once = apply_rtl_fix(line)
    twice = apply_rtl_fix(once)
    assert once == twice
```

---

## 8. Phased Implementation Plan for Antigravity

Execute this project in **4 sequential phases**. Verify each phase before moving to the next.

### Phase 1: Backend Foundation, `.env` Config, Utilities & Unit Tests
1. Set up `/backend` directory, `requirements.txt` (`fastapi`, `uvicorn[standard]`, `sqlmodel`, `faster-whisper`, `google-genai>=1.0.0`, `pydantic-settings`, `python-multipart`, `pytest`, `pytest-asyncio`, `httpx`), `backend/.env.example`, and `backend/.env`.
2. Implement `config.py` (Section 5.1), `database.py`, `hardware.py`, `rtl_fixer.py` (Section 5.4), `srt_formatter.py` (Section 5.3), and `refine_stage.py`.
3. Implement `probe_stage.py` (`ffprobe` JSON parser) and `audio_stage.py` (Section 5.2).
4. Create and run `pytest` suites (`test_rtl_fixer.py`, `test_srt_formatter.py`, `test_refine_stage.py`) and ensure 100% pass rate.

### Phase 2: AI Providers, Pipeline Orchestrator & API Routes
1. Implement `BaseTranscriber` and `FasterWhisperTranscriber` (`word_timestamps=True`, `vad_filter=True`) emitting live segment callbacks.
2. Implement `BaseTranslator` (Section 5.5), `NoopTranslator`, and `GeminiTranslator` (using the exact implementation in Section 5.6).
3. Implement `orchestrator.py` and `ws_manager.py` to run the pipeline asynchronously and broadcast real-time WebSocket events.
4. Build REST endpoints (`routes_media.py` with HTTP Range video streaming, `routes_jobs.py`, `routes_export.py`, and `routes_system.py` exposing hardware and `.env` Gemini key status).

### Phase 3: Frontend Setup & Media Ingest Experience
1. Initialize `/frontend` with Vite, React, TypeScript, Tailwind CSS, and Lucide icons.
2. Implement typed API service (`api.ts`), Zustand store (`useJobStore.ts`), and WebSocket synchronization hook.
3. Build `Sidebar` (showing hardware badge and `.env` Gemini API Key configured indicator), `Header`, `MediaDropzone` (Upload + Local Path tabs), `StreamSelector` (audio track picker), and `PipelineConfig`.

### Phase 4: Real-Time Studio Workspace, Video Sync & End-to-End Polish
1. Build `LivePipelineBar` and the split-pane Studio Workspace.
2. Implement `VideoSyncPlayer` with HTTP Range playback and dynamic WebVTT blob track updates so inline edits reflect on the video immediately.
3. Implement `SubtitleTable` with inline editing, RTL alignment, timestamp nudging, CPS warning badges, Search & Replace, and `ExportToolbar`.
4. Verify full end-to-end flow and generate a clean `README.md` documenting `.env` setup and startup commands.