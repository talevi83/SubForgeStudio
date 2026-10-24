# SubForge Studio — Technical Specification (`SPEC.md`)

> Architecture, technology stack and design decisions of SubForge Studio as it is built today.
> For setup, usage, every setting and the full endpoint list see [`README.md`](README.md); this document
> explains **how the system is put together and why**. It was rewritten from the original build
> specification (phases 1–4) to match the current code.

## 1. Overview

**SubForge Studio** is a local application that turns a video file into finished subtitles:

1. **Transcribes** the audio on the user's own machine (faster-whisper, word-level timestamps, GPU or CPU).
2. **Refines** Whisper's output into broadcast-style subtitles (sentence re-segmentation, line limits, reading speed).
3. **Translates** with Google Gemini (context-aware batches, glossary, first-class Hebrew/RTL support) — optional.
4. **Re-times a subtitle file the user already has** against the video's speech and on-screen text — an alternative to 1–3.
5. Offers an **editing studio** (synced player, inline edit, add/move/re-time lines, quality checks) and **SRT/VTT export**.

It runs three ways from the same code: as a **local web app** (browser), as a **desktop window** from source (`run.bat`), and
as a **standalone Windows program** (`SubForge Studio.exe`, PyInstaller). Nothing leaves the machine except the text
and frames explicitly sent to Gemini; the video itself is never uploaded anywhere.

### Core engineering rules

1. **English-only code, comments, docstrings, logs and commit messages.** (The UI itself is bilingual: English and Hebrew.)
2. **Pipeline + provider architecture.** Work is a sequence of stages; transcription and translation sit behind abstract
   interfaces (`BaseTranscriber`, `BaseTranslator`) so engines can be swapped or faked in tests.
3. **Never block the event loop.** `ffmpeg`, Whisper and Gemini calls run in subprocesses or worker threads
   (`asyncio.to_thread`); progress reaches the browser over a WebSocket.
4. **Zero-copy local files.** Videos are 1–20 GB: the user can point at a path on disk (nothing is copied), or upload through the
   browser (copy kept in `storage/uploads`, owned by the app and deleted with its project).
5. **Official Gemini SDK (`google-genai`) and typed configuration.** Defaults come from `backend/.env` via `pydantic-settings`;
   the Settings screen overrides them in `storage/settings.json`. API keys typed in the UI are never written to disk.
6. **Safe by default for a local server.** The API can read any path on the PC, so it binds to `127.0.0.1`, uses an explicit
   CORS allow-list and a trusted-host check (against DNS rebinding).
7. **Nothing is lost.** Results are saved as soon as they exist (after refinement and after every translated batch); failures
   are flagged on the line, never silently dropped; jobs interrupted by a restart are recovered.

---

## 2. Technology stack

| Layer | Technology | Role |
|---|---|---|
| Backend framework | **Python 3.11+ (3.12 in `run.bat`), FastAPI, Uvicorn** | REST API, WebSocket, static hosting of the built frontend |
| Persistence | **SQLModel / SQLAlchemy on SQLite** (`storage/subforge.db`) | Jobs and subtitle segments; additive column migration at startup |
| Configuration | **pydantic-settings** (`.env`) + `storage/settings.json` | First-run defaults vs. user choices made in Settings |
| Speech-to-text | **faster-whisper** (CTranslate2) + **Silero VAD** | Local transcription, word timestamps, speech maps, language detection |
| GPU runtime | **NVIDIA cuBLAS / cuDNN wheels** (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) | CUDA FP16 without a system CUDA install; CPU INT8 fallback |
| Media | **ffmpeg / ffprobe** (async subprocesses) | Probing, audio extraction and filtering, frame sampling (GPU-accelerated), subtitle-track extraction |
| AI | **Google Gemini** via `google-genai` | Glossary pass, translation, on-screen text (vision), sync line matching, speaker gender detection |
| Local AI & Optimization | **Ollama** (local LLM), **Pillow** (edge & dHash) | Local gender pre-tagging, frame deduplication & blank-frame pruning in Cost-Saver Mode |
| Frontend | **React 19, TypeScript 6, Vite 8, Tailwind CSS 4, Zustand 5, lucide-react** | Single-page studio; typed REST client; WebSocket listener inside the job store |
| Lint / build | **oxlint**, `tsc -b` + `vite build` | Frontend must pass both with zero warnings |
| Desktop shell | **pywebview** (Edge WebView2), **pystray + Pillow** (tray icon) | Own app window and tray menu (`packaging/launcher.py`) |
| Packaging | **PyInstaller** one-folder (`packaging/subforge.spec`, `build_exe.bat`) | Self-contained `SubForge Studio.exe` with Python, libraries, frontend, ffmpeg and CUDA libs |
| Tests | **pytest, pytest-asyncio, httpx** | ~260 backend tests; fake Whisper/Gemini plus real-ffmpeg end-to-end runs |

---

## 3. System architecture

```text
 ┌──────────────────────────── Browser tab  or  pywebview window (WebView2) ───────────────────────────┐
 │  React SPA:  Ingest ─ Live monitor ─ Studio (player + table) ─ Settings ─ Help & Q&A                 │
 │  Zustand stores (jobs, settings, ui)   typed REST client (services/api.ts)   WebSocket listener       │
 └───────────────▲──────────────────────────────────────┬───────────────────────────────────────────────┘
                 │ REST /api/*   (Range streaming for video)      │ WS /ws/jobs/{id}: progress, segments
 ┌───────────────┴──────────────────────────────────────▼───────────────────────────────────────────────┐
 │ FastAPI app  (CORS allow-list, TrustedHost, routers, serves frontend/dist)                           │
 │   routes: system · media · jobs · export · settings · glossary · storage                              │
 │                                    │                                                                  │
 │                       PipelineOrchestrator  (one asyncio task per job)                                │
 │        GPU semaphore · cancel flag · save-as-you-go · usage/cost tracking · per-job log               │
 │                                    │                                                                  │
 │   Stages:  Probe → Audio → Transcribe → [On-screen] → Refine → [Translate]     (mode "full")          │
 │            Probe → Audio → Transcribe → [On-screen] → Sync                      (mode "sync")         │
 │            Translate only                                                       (mode "translate")    │
 │                                    │                                                                  │
 │   Providers:  BaseTranscriber ── FasterWhisperTranscriber                                             │
 │               BaseTranslator  ── GeminiTranslator (Flex wrapper)  ·  NoopTranslator                   │
 └────────┬──────────────────────────┬────────────────────────────┬───────────────────────────┬─────────┘
          │ SQLite (SQLModel)        │ ffmpeg / ffprobe           │ faster-whisper (GPU/CPU)  │ Gemini API / Ollama
          ▼                          ▼                            ▼                           ▼
   storage/subforge.db        audio, frames, tracks         models in the HF cache        text + frames only
```

### 3.1 Backend layout (`backend/app/`)

| Package | Contents |
|---|---|
| `main.py` | App factory: lifespan (DB init, crash recovery, housekeeping), middleware, routers, WebSocket endpoint, static hosting of `SUBFORGE_FRONTEND_DIST` / `frontend/dist` |
| `api/` | One router per area (see §6): `routes_system`, `routes_media`, `routes_jobs`, `routes_export`, `routes_settings`, `routes_glossary`, `routes_storage` |
| `core/` | `config` (settings), `database` (engine + additive migration), `hardware` (GPU detection, CUDA DLL paths), `model_catalog` (Gemini models, prices, thinking options, cost estimates), `user_settings`, `glossary_profiles`, `ws_manager`, `job_logging`, `housekeeping`, `storage_cleanup`, `ollama_setup` (Ollama lifecycle/install), `compat` (PyAV/faster-whisper shim) |
| `models/` | `db_models` (tables, `JobStatus`, `SegmentType`), `schemas` (Pydantic request/response models) |
| `pipeline/` | `orchestrator`, `context` (state passed between stages), `stages/*`, `onscreen_prefilter` (edge & dHash frame filter), `gender_pretag` (local Ollama pre-tagger), `glossary_pruner` (batch glossary pruner), and the algorithms below |
| `providers/` | `transcription/` (`BaseTranscriber`, faster-whisper), `translation/` (`BaseTranslator`, Gemini, Noop), `gemini_calls` (Flex tier wrapper) |
| `utils/` | SRT/VTT formatter, RTL fixer, subtitle file parser, language helpers, text-break rules |

### 3.2 Pipeline stages (`pipeline/stages/`)

| Stage (`JobStatus`) | What it does |
|---|---|
| **Probe** (`PROBING`) | `ffprobe` the file: duration, audio streams (the user picks one), embedded subtitle tracks |
| **Audio** (`EXTRACTING_AUDIO`) | ffmpeg → mono 16 kHz WAV. Surround tracks are downmixed with emphasis on the centre (dialogue) channel; *Comms boost* adds a speech-band chain (band-pass, FFT denoise, compressor, `dynaudnorm`) |
| **Transcribe** (`TRANSCRIBING`) | faster-whisper with Silero VAD and word timestamps; batched GPU inference with fallback to sequential on out-of-memory; real mid-file cancellation; model stays loaded between jobs. Then: *start alignment* (`word_snap`), *missed-speech recovery* (`gap_recovery`), *foreign-speech detection* (`foreign_speech`). In Cost-Saver Mode, Whisper is unallocated from GPU memory immediately after completion to free VRAM |
| **On-screen text** (`SCANNING_ONSCREEN`, optional) | Uses an embedded forced/"signs" subtitle track if present; otherwise samples frames in the background while Whisper runs (prioritising speech gaps and scene cuts). Frame extraction is GPU-accelerated (`-hwaccel auto` and `-discard nokey`). In Cost-Saver Mode, frames are pre-filtered locally (Pillow edge detection + 64-bit dHash) before calling Gemini Vision. Also reads burned-in subtitles for foreign passages |
| **Refine** (`REFINING`) | Flatten words → remove repetition loops → re-group by sentence → split at clause boundaries (42 chars/line, 2 lines) → timing pass (no overlaps, ≥ 2-frame gaps, target reading speed) → merge on-screen cues into the timeline |
| **Translate** (`TRANSLATING`, optional) | Glossary pass over the whole transcript, then batched translation (batch size 60) with sliding context and strict 1-to-1 index mapping (structured output); per-line retry; failures flagged. Dynamic glossary pruning per batch. In Cost-Saver Mode, pre-tags ambiguous pronouns using local Ollama |
| **Sync** (`SYNCING`, sync jobs) | Re-times the user's subtitle file, see §4.4 |

A job moves through `IDLE/QUEUED → … → COMPLETED | FAILED | CANCELLED`. `QUEUED` is the wait for the GPU slot
(`MAX_CONCURRENT_TRANSCRIPTIONS`, default 1). On startup `recover_interrupted_jobs()` repairs jobs left mid-run by a crash:
jobs that already have subtitles become usable (`COMPLETED` with a warning, untranslated lines flagged), others `FAILED`.

### 3.3 Real-time protocol (`WS /ws/jobs/{job_id}`)

One JSON envelope: `{ event, job_id, stage?, progress_percent, speed_factor?, eta_seconds?, segment?, segments_batch?, message? }`.

| `event` | Meaning |
|---|---|
| `STAGE_PROGRESS` | Stage, percent, speed factor, ETA, optional status message |
| `SEGMENT_CREATED` | A transcribed segment, streamed while Whisper runs |
| `SEGMENTS_REFINED` | The final, editable subtitles (with database ids) replace the raw Whisper segments |
| `BATCH_TRANSLATED` | A translated batch; each batch is also saved to the database |
| `JOB_COMPLETED` / `JOB_FAILED` / `JOB_CANCELLED` | Terminal events |

The frontend store reconnects automatically and re-fetches the job on reconnect, so the database — not the socket — is the source of truth.

---

## 4. Key algorithms and design decisions

### 4.1 Transcription and timing quality
- **Hardware:** CUDA FP16 when an NVIDIA GPU is found, CPU INT8 otherwise (`WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE`). In the frozen build the CUDA wheels' `bin` folders are added to the DLL search path (`core/hardware.py`).
- **Hallucination control:** Silero VAD, `condition_on_previous_text=False`, repetition-loop removal, strict checks on recovered text.
- **Word start alignment (`word_snap`):** Whisper's timestamps ignore silence, so a word after a pause can be stamped early. Silero VAD finds the real speech onset; starts are only ever moved *later*, and only across silent gaps ≥ 0.4 s.
- **Missed-speech recovery (`gap_recovery`):** VAD misses shouted speech under gunfire/music; Whisper re-listens *without* VAD to uncovered stretches and keeps only lines that pass hallucination checks.
- **Foreign passages (`foreign_speech`):** per-chunk language detection (encoder only) finds speech in another language than the rest; the text comes from burned-in subtitles (frame reading) or a Whisper re-transcription in that language.
- **Re-check timing (`retime`, `speech_regions`):** timing-only correction of finished subtitles against a fresh speech map; text and on-screen cues are never touched; cached under `storage/regions`; the studio offers *Undo*.

### 4.2 Subtitle refinement and export
- Linguistic break rules live in `utils/text_rules.py`; formatting (balanced line splitting, SRT/VTT) in `utils/srt_formatter.py`.
- Limits are configurable: `MAX_CHARS_PER_LINE` 42, `TARGET_CPS` 17, `MAX_CPS_WARNING` 20, duration 1–7 s, minimum gap ≈ 2 frames.
- **RTL (Hebrew/Arabic):** text is stored clean; directional marks (`U+200F`) are added only at export so `? ! .` and dialogue dashes land on the right side in VLC/Plex/HTML5 players; SRT is written with a UTF-8 BOM.
- Segment types: `DIALOGUE`, `COMMS` (radio traffic), `ON_SCREEN` (titles/signs, bracketed), `FOREIGN` (other-language speech).
- **On-Screen / Dialogue Overlap & Adequate Reading Time:** On-screen chyrons/titles can display concurrently with bottom dialogue subtitles without clipping subsequent speech lines (`allow_overlap=True`). On-screen cues compute a minimum reading duration based on character count (`chars / 15 cps`). Manual segment insertion in the studio distinguishes dialogue from on-screen lines to prevent collision false-positives.
- **On-Screen Subtitle Positioning (`onscreen_position`):** Configurable in Settings and per-job Ingest (`top_center`, `middle_right`, `middle_left`, `top_right`, `top_left`). Serialized using standard SubStation Alpha tags `{\anX}` in SRT (supported by VLC, MPC-HC, MPV, PotPlayer) and positioning cue settings (`line:8%`, `line:50% position:90%`, etc.) in WebVTT, as well as live HTML5 video player overlay coordinates.

### 4.3 Translation (Gemini)
- **Glossary pass → batches:** characters (gender, name spelling), terms and tone feed every batch; **series glossary profiles** (`storage/glossaries/`) carry them across episodes.
- **Structured output** with strict index mapping; sliding `previous_context` / `lookahead_context`; missing lines retried individually.
- **Metric System Conversion (`convert_to_metric`):** When enabled, automatically converts non-metric imperial units (miles, feet, inches, yards, pounds/lbs, ounces, Fahrenheit, gallons, acres) into idiomatic metric equivalents (km, km/h, meters, cm, kg, Celsius, liters, dunams) with natural rounding for subtitle reading speed, while preserving figurative/idiomatic expressions ("give an inch", "walk a mile").
- **Per-step models:** *Translation*, *Glossary pass* and *On-screen text (vision)* each have their own model and thinking level. The catalog (`core/model_catalog.py`) knows prices (including dated promotions and long-context tiers), supported thinking modes and which models may do which task (vision needs image input); retired models are migrated automatically. Flash-Lite models are the budget choices for translation.
- **Flex tier** (`providers/gemini_calls.py`): requests at half price with a long timeout; on capacity errors (429/503) exponential backoff, then one Standard-tier request; after two consecutive "bad" Flex results the rest of the job uses Standard.
- **Cost:** every call records exact input/output/thinking tokens and dollars per step (`Job.usage_json`); `POST /api/settings/estimate` predicts cost for a video length before running.
- **Row-level tools:** re-translate selected rows with speaker/addressee gender and a free-text note; *Detect from video* asks Gemini to infer gender from frames; a deterministic **quality check** (`pipeline/qa.py`, no model call) flags untranslated Latin words, glossary mismatches and pronoun/verb gender errors.

### 4.4 Syncing an existing subtitle file (`sync_*`)
Input: video + the user's own SRT / WebVTT / ASS (`utils/subtitle_parser.py` reads UTF-8/16, Windows-1255 and -1252; formatting is stripped, top-positioned cues marked as probable signs). **Text is never changed — only timing.**

1. **Speech map:** Silero VAD over the audio plus Whisper word times.
2. **Reference timing from the video itself** when available: an embedded text subtitle track, else subtitles burned into the picture (`sync_burned.py` — local frame analysis of the bottom band with ffmpeg, no OCR; used only if it fits the speech).
3. **Global mapping** `t → scale·t + offset` by cross-correlating "a subtitle is showing" with "someone is speaking", trying the usual frame-rate ratios (25 ↔ 23.976/24 …) (`sync_align.py`).
4. **Per-cue offsets by dynamic programming (Viterbi)** with split penalties, so cuts (recaps, removed scenes) are found; an onset bonus pulls cues to speech starts.
5. **Text anchors** (`sync_text.py`): same-language files are matched to Whisper words locally (difflib); other-language files are matched by Gemini line-to-phrase batches (the *Translation* model, optional). Anchors are evidence, not orders — neighbours outvote a wrong one.
6. **Second alignment pass** with the anchors; low-confidence lines surface under *Only problems*; the job stores a report (`sync_report_json`: offset, frame rate, cuts, method per line).
7. The result is an ordinary project (edit, re-check timing, export). A file in another language becomes the translation, with the audio's text as the original.

### 4.5 Editing operations
All edits go through the REST API and renumber lines transactionally: single/bulk patch, **add a line** (`POST /segments`, overlap → `409`, timecode `HH:MM:SS,mmm` shared with the UI), **delete a line** (`DELETE /segments/{segment_id}` with renumbering), **move** (`/segments/move`, text moves and time slots stay, only the affected span is re-checked), **re-time**, delete project. In the studio, lines can be filtered by tag (`ON_SCREEN`, `COMMS`, `FOREIGN`, or any tagged cue) as well as by CPS/confidence problems.

### 4.6 Cost-Saver Mode & Local AI Offloading (`cost_saver_mode`)
- **Local frame pre-filtering (`onscreen_prefilter.py`):** Pillow-based edge detection and 64-bit difference hashing (`dHash`) filter out blank/textless frames and deduplicate consecutive identical title cards before calling Gemini Vision, pruning ~75–80% of Vision image tokens.
- **Whisper GPU VRAM unloading:** Faster-Whisper model is cleanly unallocated from GPU memory immediately after transcription completes, freeing VRAM for local LLMs or other GPU tasks on 8 GB cards.
- **Local gender pre-tagging (`gender_pretag.py`):** Auto-detects local Ollama (e.g. `qwen2.5:7b`) and pre-tags ambiguous pronoun lines (`you`, `I am`) with `[Speaker: M/F, To: M/F]` hints locally for Hebrew/Arabic translation, cutting cloud token overhead. Skips with zero delay if Ollama is absent.
- **Ollama setup & lifecycle management (`core/ollama_setup.py`):** Live status polling (`GET /api/settings/ollama/status`) and background one-click automated setup (`POST /api/settings/ollama/setup`) which downloads the installer on Windows, launches the daemon, and pulls `qwen2.5:7b`.
- **Dynamic glossary pruning (`glossary_pruner.py`):** Prunes full series or project glossaries down to only the characters and terms appearing in each 60-line translation batch, reducing input token overhead.
- **Live & post-run token breakdown:** Tracks input, output, thinking tokens per Gemini model, local Ollama queries handled, and frames/tokens saved locally.
- **Hardware-accelerated frame sampling:** FFmpeg sampling uses `-hwaccel auto` (NVDEC / D3D11VA) and `-discard nokey` on full video files in both OCR scanning and burned-in subtitle detection (`sync_burned.py`), reducing 36-min video sampling from 20+ min to ~29 seconds.

---

## 5. Data model

**`jobs`** — one row per project: `id`, `video_path`, `file_name`, `is_upload`, `status`, `current_stage`, `progress_percent`,
`speed_factor`, `eta_seconds`, `error_message`, `warning_message`, `video_duration`, `audio_streams_json`, `selected_audio_stream`,
`whisper_model`, `source_language`, `detected_language`, `vad_filter`, `comms_boost`, `high_speech_sensitivity`, `use_embedded_subtitles`,
`detect_onscreen_text`, `onscreen_sensitivity`, `onscreen_position`, `enable_translation`, `convert_to_metric`, `target_language`, `gemini_model`,
`context_notes`, `glossary_profile`, `glossary_json`, `usage_json`, and for sync jobs
`job_kind` (`transcribe` | `sync`), `sync_file_name`, `sync_source_json`, `sync_report_json`, plus timestamps.

**`subtitle_segments`** — `id`, `job_id` (FK, cascade delete), `index`, `start_time`, `end_time`, `original_text`, `translated_text`,
`translation_failed`, `segment_type`, `confidence`, `cps`.

**Migrations:** `core/database.py` keeps an additive `_ADDED_COLUMNS` map and `ALTER TABLE … ADD COLUMN`s missing columns at startup, so
existing databases keep working across versions (no migration tool, no destructive changes).

**On disk (`storage/`, git-ignored):** `subforge.db`, `settings.json`, `uploads/`, `audio/`, `frames/`, `exports/`, `regions/`, `glossaries/`, `logs/` (`server.log` rotating 3 × 5 MB, `jobs/<job>.log` per project).
Housekeeping at startup removes stale temporary audio and abandoned uploads (`UPLOAD_RETENTION_HOURS`); the Settings screen can free space by category and never touches videos the user selected by path.

---

## 6. API surface

Interactive documentation: `http://localhost:8000/docs`. Summary:

| Area | Endpoints |
|---|---|
| System | `GET /api/system/info`, `GET /api/system/log` |
| Media | `POST /api/media/probe-path`, `POST /api/media/upload`, `GET /api/media/upload/existing`, `POST /api/media/parse-subtitles`, `GET /api/media/stream/{job_id}` (HTTP Range) |
| Jobs | `POST/GET /api/jobs` (`sync_cues` makes a sync job), `GET/DELETE /api/jobs/{id}`, `POST …/cancel`, `POST …/translate` |
| Editing | `POST …/segments`, `DELETE …/segments/{id}`, `PATCH …/segments/{id}`, `PATCH …/segments`, `POST …/segments/move`, `POST …/retime` |
| Translation tools | `PUT …/glossary`, `POST …/detect-speakers`, `GET …/qa`, `GET /api/glossary-profiles` |
| Export | `GET …/export/srt`, `…/export/vtt`, `…/export/log` |
| Settings | `GET/PUT /api/settings`, `POST /api/settings/estimate`, `POST /api/settings/check`, `GET /api/settings/ollama/status`, `POST /api/settings/ollama/setup` |
| Storage | `GET /api/storage`, `POST /api/storage/clean` |
| Live | `WS /ws/jobs/{job_id}` |

---

## 7. Frontend

- **Structure:** `components/` → `ingest/` (media drop, stream selector, mode switch *Create subtitles / Sync my subtitles*, pipeline config, sync options), `monitor/` (live pipeline bar, stream feed, token & local AI savings monitor), `studio/` (video player, subtitle table/rows, export toolbar, glossary / row-translate / insert-line dialogs), `settings/` (AI models, cost-saver toggle & Ollama status/setup, storage cleanup), `layout/` (Sidebar with Studio, Settings, Help buttons; Header), `HelpSection` (standalone view with 4-step workflow guide and interactive Q&A accordion).
- **State:** Zustand — `useJobStore` (jobs, active job, segments, WebSocket, ingest mode, sync file), `useSettingsStore`, `useUiStore` (language, theme, view: `'studio' | 'settings' | 'help'`; persisted to `localStorage`, reflected on `<html>` for CSS and RTL layout).
- **Contracts:** `types/index.ts` mirrors the backend schemas; `services/api.ts` is the only REST client.
- **i18n:** `i18n/en.ts` + `i18n/he.ts`; message keys are typed, so a missing translation fails the TypeScript build. Hebrew switches the whole layout to RTL (`dir`), with explicit `dir` only around genuinely left-to-right fragments.
- **Player:** HTML5 video with Range seeking and a dynamic WebVTT blob track, so edits show on the video immediately. Shortcuts: `Space`, `←/→` (±2 s, `Shift` ±0.1 s), `[` `]` (set start/end to playhead), `N` (add line), `F` (fullscreen).
- **Dev/prod:** `npm run dev` on `:5173` proxies `/api` and `/ws` to `:8000`; `npm run build` produces `frontend/dist`, which FastAPI serves on the same origin.
- **Desktop bridge:** `utils/desktop.ts` talks to `window.pywebview.api` when present (fullscreen of the whole window).

---

## 8. Desktop shell and packaging (`packaging/`)

- **`launcher.py`** starts Uvicorn inside the same process (server thread), then shows the studio in a **pywebview window** (Edge WebView2) with a **tray icon** (`pystray`): open, open in browser, data folder, `.env`, server log, restart, quit. A loading page shows while the server starts; window size is remembered; closing asks first if a project is still running (quit / keep in background / cancel).
- **Fallbacks:** `--browser` (or `SUBFORGE_UI=browser`), or automatically when WebView2 is missing; `--no-tray` for a console server. A second launch asks the running copy to show its window (`POST /api/desktop/show`); if the running copy has no window it says so instead of opening a tab.
- **Data folder** (`SUBFORGE_HOME` → `data` next to the exe → `%LOCALAPPDATA%\SubForge Studio`) holds `.env`, `storage/`, `launcher.log` and the WebView profile, so replacing the program never touches user data. `run.bat` sets `SUBFORGE_HOME=backend`, keeping the developer layout.
- **Windowed-exe details:** stdout/stderr go to `launcher.log`; child consoles (ffmpeg, nvidia-smi) are hidden under the frozen exe and `pythonw`.
- **Build:** `build_exe.bat` installs backend + `packaging/requirements-build.txt`, builds the frontend, fetches a static ffmpeg (`get_ffmpeg.ps1`) and runs PyInstaller (`subforge.spec`) → `dist\SubForge Studio\` (2–3 GB, mostly CUDA libraries). `run.bat` runs the same launcher from source using `packaging/requirements-desktop.txt`.

---

## 9. Security and privacy
- Server binds to `127.0.0.1`; CORS allow-list (`cors_origins`); `TrustedHostMiddleware` (`ALLOWED_HOSTS`) blocks DNS rebinding. Changing `HOST` to `0.0.0.0` requires adding the LAN names to `ALLOWED_HOSTS` and exposes a file-reading API — documented as unsafe by default.
- API keys: `.env` (git-ignored) or typed in the UI for one run only; never persisted by the app. `.gitignore` also covers `*.exe`, `build/`, `dist/`, `storage/`, `data/` and `launcher.log`.
- Sent to Gemini: subtitle text (and glossary), sampled frames when on-screen detection/speaker detection is used, nothing else. Audio and video stay local.
- Housekeeping and cleanup only delete files inside `storage/` that the app created.

---

## 10. Testing and quality gates
- Backend: `pytest` in `backend/tests` (~260 tests). Whisper and Gemini are replaced by fakes; ffmpeg-based tests run end to end on generated clips (forced track, frame scan, whole pipeline, sync with synthetic delays/frame-rate/cuts). Areas: RTL fixer, SRT formatter, refine quality, Whisper provider (cancel, fallback), Gemini provider (retries, flags), settings/pricing/Flex, foreign speech, gap recovery, word snap, retime, row translate, glossary profiles, storage cleanup, sync, insert line, desktop launcher.
- Frontend: `npx tsc -b`, `npx oxlint src` (0 warnings) and `npm run build` must pass; UI flows are checked in a headless browser in both English and Hebrew.
- Windows-only behavior (tray, WebView2 window, CUDA loading) is verified manually on a Windows PC; the launcher's pure logic is unit-tested.

---

## 11. Extending the system
- **New transcription or translation engine:** implement `BaseTranscriber` / `BaseTranslator` and return the shared dataclasses (`RawSegment`, `TranslatableSegment`). Each stage builds its provider itself (`TranscribeStage`, `TranslateStage._make_translator`) and also accepts an injected one, which is how the tests use fakes.
- **New pipeline stage:** subclass `PipelineStage` (`pipeline/stages/base.py`: a `name` and `async execute(ctx, on_progress)`), add a `JobStatus` value if it deserves its own progress state, add it to `PipelineOrchestrator._stages()`, add the matching label in the frontend `LivePipelineBar` and both i18n files.
- **New Gemini model or price change:** edit `core/model_catalog.py` only (models, tasks, prices, thinking options, retired-model migration).
- **New DB column:** add it to the model and to `_ADDED_COLUMNS` in `core/database.py`.
- **New UI language:** add `i18n/<code>.ts` with the same typed keys and extend `Language` in `useUiStore`.
