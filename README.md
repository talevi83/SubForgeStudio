# SubForge Studio 🎬

**SubForge Studio** is a modular local web application for automated video subtitling, high-precision Speech-to-Text transcription with word-level timing, broadcast-grade subtitle refinement, and context-aware batch translation powered by Google Gemini API (with first-class RTL/Hebrew support).

---

## 🌟 Key Features

1. **Zero-Copy Local Ingest & Stream Discovery:**
   - Ingest multi-gigabyte files (MKV, MP4, AVI, WebM) directly from disk paths without duplicating files.
   - Discovers all video and audio tracks via `ffprobe` and lets you select the exact audio stream to transcribe.
   - Browser uploads show progress, are de-duplicated (same name + size is reused), and are deleted together with their project.

2. **Hardware-Accelerated Transcription:**
   - Powered by `faster-whisper` (CTranslate2) with automatic hardware detection (`CUDA FP16` on NVIDIA GPUs or `CPU INT8`).
   - Batched GPU inference (`WHISPER_BATCH_SIZE`) with automatic fallback to sequential decoding if VRAM runs out; `large-v3-turbo` available.
   - The model stays loaded between jobs; jobs queue for the GPU instead of loading two models at once.
   - Silero VAD, `condition_on_previous_text=False` and loop detection against hallucinated repetitions.
   - Surround (5.1/7.1) tracks are downmixed with emphasis on the dialogue centre channel.
   - Cancel really stops Whisper mid-file.

3. **Broadcast-Grade Subtitle Refinement:**
   - Re-segments Whisper's output by sentence: lines Whisper cut mid-sentence are re-joined, dangling one-word fragments are merged, and sentences from different Whisper segments are never glued together (avoids mixing speakers).
   - Long sentences are split at clause boundaries/pauses, max 42 characters per line and 2 lines per subtitle.
   - Timing pass: no overlaps, ≥2-frame gaps, display time extended toward a target reading speed (`TARGET_CPS`), short gaps closed to avoid flicker.
   - Reading speed (CPS) badges, plus an "Only problems" filter in the editor.

4. **Context-Aware Gemini Subtitle Translation:**
   - One glossary pass over the whole transcript (characters, gender, terminology) feeds every batch — better Hebrew gender agreement and consistent names.
   - Batch translation with sliding context (`previous_context` + `lookahead_context`) and strict 1-to-1 index mapping via structured output.
   - Missing lines are retried individually; anything that still fails is **flagged**, never silently left in English. "Retranslate failed" / "Retranslate all" (or a different target language) without re-transcribing.
   - Subtitles are saved as soon as they're refined and after every translated batch — nothing is lost if translation fails.

5. **Bidirectional RTL Punctuation Safety (Hebrew & Arabic):**
   - Text is stored clean; Unicode Right-to-Left Marks (`\u200f`) are added at export so `?`, `!` and dialogue hyphens land on the correct side in VLC, Plex and HTML5 players. SRT files are written with a UTF-8 BOM for player compatibility.

6. **📻 Tactical Comms & Radio Boost (optional, per job):**
   - Extracts audio through a speech-band filter chain (180Hz–3.8kHz, FFT denoise, compressor, `dynaudnorm`) so faint or distorted walkie-talkie traffic and whispers survive.
   - Whisper runs with a permissive VAD, relaxed `no_speech`/`log_prob` thresholds and a tactical vocabulary prompt (`initial_prompt` + `hotwords`).
   - Radio-procedure lines ("Copy that, over.", "Do you copy?", "…come in.") are tagged `COMMS` and translated with authentic IDF radio phrasing ("רות", "עבור").

7. **📺 On-Screen Text / Forced Narratives (optional, per job):**
   - Uses an embedded forced/"signs" text subtitle track when the file has one.
   - Otherwise samples a frame every 2s (in the background while Whisper runs), prioritises speech gaps and scene cuts, and asks Gemini Vision for narrative text only ("One week later...", "Damascus, Syria", HUD headers, plot-critical signs — not logos, credits or burned-in dialogue).
   - Repeated detections are merged into one `[bracketed]` `ON_SCREEN` subtitle and woven into the timeline (own entry in silence, or top line above simultaneous dialogue). Translated as Hebrew screen titles, with RTL marks applied at export. Lone short words (HUD/radio labels such as "EAGLE") are dropped, and a sign the camera returns to within 3 minutes is not shown again. Every kept title is listed in the job log.
   - **Speech in other languages** (e.g. Russian in an English episode): Whisper locks onto one language and drops or garbles such passages. After transcription, Silero VAD + Whisper language detection find passages that are another language (or speech Whisper produced no text for). Those windows are read at 2 frames/s from the bottom third of the picture, and the show's own burned-in subtitles (or a forced track) become `FOREIGN` subtitles with their exact on/off timing — scanning outward while a subtitle is still visible. Without burned-in subtitles, Whisper re-transcribes just that passage in its own language. Gemini translates both as normal dialogue.

8. **Interactive Studio Workspace:**
   - Split-screen workspace with synced HTML5 video player (HTTP Range seeking), seek bar and fullscreen that keeps the subtitle overlay.
   - Keyboard: `Space` play/pause, `←/→` ±2s (`Shift` ±0.1s), `[` / `]` set the subtitle start/end to the playhead, `F` fullscreen.
   - Inline editor with debounced auto-save, ±100ms nudges, and bulk Search & Replace.
   - Export to `.srt` or `.vtt`, named for media servers (e.g. `Movie.he.srt`).

---

## ⚙️ Settings, Models & Pricing

Open **Settings** (bottom of the sidebar):

- **Appearance** — interface language (Hebrew / English, with a full right-to-left layout in Hebrew) and dark / light theme. Saved per browser.
- **AI models** — pick the Gemini model and thinking level separately for each step that calls Gemini: *Translation*, *Glossary pass* and *On-screen text (vision)*, plus the frame detail used for vision. Saved to `storage/settings.json` and used by every new job and retranslation.
  - Each list shows only the models that can do that step (vision needs image input). For translation, **Gemini 3.5 Flash-Lite** and **Gemini 3.1 Flash-Lite** are the budget choices: about 58% and 29% of the Flash cost for a translated episode (about 29% and 15% once the Flash promotion ends in January 2027), with weaker Hebrew quality (gender agreement, idioms), so check the result. With Flex on top they cost half again. Models Google retired are migrated automatically in saved settings.
  - Thinking uses `thinking_level` (minimal / low / medium / high, as supported by each model). "Model default" sends nothing.
  - **Flex mode** (per step): sends requests with `service_tier="flex"` at 50% of the Standard price. Google answers Flex when it has spare capacity, so a request can take minutes; busy (429/503) requests are retried with backoff and then sent once on the Standard tier, and the job warns how many requests fell back. Translation runs batch by batch, so Flex can make long episodes much slower.
  - Gemini 3+ models keep their default temperature, as Google recommends.
  - **Test models** sends a tiny request to each chosen model (an image for the vision step, over Flex when Flex is on) to confirm your API key can use it. The API is `POST /api/settings/check`.
- **Missed-speech recovery** (on by default, `WHISPER_GAP_RECOVERY=false` to disable): the voice detector (Silero VAD) misses speech under gunfire, engines, music and radio filtering, so Whisper never hears it. After transcription, Whisper re-listens *without* VAD to every stretch no subtitle covers and keeps only lines that pass strict hallucination checks; each kept/rejected line is listed in the project log. Shouted commands ("Fire!", "Go, go, go!", "Cease fire!") are judged by their own words rather than the noisy window's confidence, and repeats of a neighbouring command are kept.
- **Subtitle start alignment** (on by default, `WHISPER_SNAP_STARTS=false` to disable): Whisper's word timestamps ignore silence, so a word that follows a pause is stamped right after the previous word and the subtitle can appear a second or two before anyone speaks. After transcription, Silero VAD finds where speech really begins and any word that spans a silent gap (0.4s or longer) is moved to just before the speech that follows it (starts are only ever moved later, and speech the detector misses is left as Whisper timed it). The job log shows how many word starts were moved.
- **Re-check timing (finished jobs)**: the *Re-check timing* button above the table re-extracts the audio, runs voice-activity detection over the whole video and moves every subtitle that starts in a silence shortly before speech to just before that speech (text and on-screen cues are never touched; there is an *Undo* right after). Results are cached in `storage/regions`. The API is `POST /api/jobs/{id}/retime`.
- **Drag to reorder**: drag a row by the handle in the `#` column to another row. The text moves, the time slots stay, and only the rows between the two positions are re-checked against the audio of that section (a subtitle sitting in silence is moved to the speech that follows it). Disabled while a search or the problems filter is active, and while the job runs. The API is `POST /api/jobs/{id}/segments/move`.
- **Click a row to play**: clicking a subtitle row jumps the video to 1.5 seconds before that subtitle and starts playing, so you hear the lead-in (never earlier than the start of the video).
- **Gender and who is being spoken to**: subtitles carry no speaker names, so "you" can come out in the wrong gender. Tick the checkbox next to a few rows (Shift-click for a range) and use *Speaking to a woman / man / group*, or *Re-translate with a note...* to also set the speaker's gender and add free text (optionally remembered in the job's context notes). Only the selected rows are translated again, with the note as a top-priority instruction (`POST /api/jobs/{id}/translate` with `segment_ids`, `speaker_gender`, `addressee_gender`, `hint`).
- **Characters & terms editor**: the *Characters & terms* button opens the glossary the translator was given (gender, name spelling, terms, tone). Fix it, save, and optionally re-translate everything. The API is `PUT /api/jobs/{id}/glossary`.
- **Series glossary profiles**: give a show a name (in the setup screen, or in the *Characters & terms* window) and its characters, genders, name spellings and terms are saved under `storage/glossaries/`. Later episodes that use the same name start from that list (it is treated as authoritative), the glossary pass only adds characters and terms that are new, and the profile grows with every episode. The API is `GET /api/glossary-profiles`.
- **Check quality**: a button above the table scans the translation without calling any model and lists untranslated Latin words, names/terms that differ from the glossary, "את/אתה" pronoun and verb mismatches, and missing translations. Click an item to jump to the row, or select all the flagged rows and re-translate them. The API is `GET /api/jobs/{id}/qa`.
- **Detect from video**: in the "re-translate with note" window, you can also pick the Gemini model and thinking level for that run (it starts from the project's model and your Settings), and this button sends 2-3 frames around the selected rows plus their dialogue to Gemini and fills in the speaker and addressee gender (left empty when the video is unclear). The API is `POST /api/jobs/{id}/detect-speakers`.
- **Storage and cleanup** (Settings): shows what the app keeps in `storage/` and frees it by category: temporary audio/frames, the timing cache, logs, exported files, uploaded video copies no project uses, and (with a second confirm) finished projects older than N days together with their uploaded copy. Videos you picked from your own folders, running projects and the live `server.log` are never touched. The API is `GET /api/storage` and `POST /api/storage/clean`.
- **Diagnostics** — *Download server log* (`storage/logs/server.log`, rotating 3 × 5 MB). Every project also keeps its own detailed log (`storage/logs/jobs/<job>.log`): settings, each stage with timings, Gemini errors, retries and Flex fallbacks, and a result summary. Download it with the **Log** button in the studio; it is deleted with the project.
  - Flex: after 2 requests in a row fall back to Standard, the rest of the job uses Standard for that model.
- **Cost estimate** — per-step requests, tokens and dollars for a video of any length, recalculated live as you change models (before saving). The setup screen also shows the estimate for the loaded file.

Prices come from the official Gemini API paid tier (checked October 2026, including promotional prices that end on a set date) and live in `backend/app/core/model_catalog.py` — update that file when Google changes prices. Every job records the **exact** tokens (input, output, thinking) and cost reported by the API per step; the total is shown in the studio header and the project list.

---

## 🏗️ Repository Architecture

Pipeline per job: **Probe → Audio → Transcribe → On-screen text** *(optional)* **→ Refine → Translate**, with live progress and segments pushed over a WebSocket (`/ws/jobs/{job_id}`).

```text
SubsForge Studio/
├── SPEC.md                     # Master technical specification
├── README.md                   # Setup and usage guide
├── run.bat                     # One-click Windows launcher (venv, .env, frontend build, server)
├── build_exe.bat               # Builds the standalone desktop app (dist\SubForge Studio\)
├── packaging/                  # Tray launcher, PyInstaller spec, icon, FFmpeg download script
├── backend/
│   ├── .env.example            # Configuration template (copy to .env)
│   ├── requirements.txt        # Python dependencies (incl. CUDA cuBLAS/cuDNN wheels)
│   ├── app/
│   │   ├── main.py             # FastAPI app: CORS, trusted hosts, routers, WebSocket, serves frontend/dist
│   │   ├── api/                # Routes: system, media, jobs, export, settings, glossary profiles, storage
│   │   ├── core/               # Config, SQLite DB, hardware detection, WS manager, model catalog & prices,
│   │   │                       #   user settings, glossary profiles, job logging, housekeeping, storage cleanup
│   │   ├── models/             # SQLModel tables & Pydantic schemas
│   │   ├── pipeline/
│   │   │   ├── orchestrator.py # Runs the stages: GPU queue, cancel, save-as-you-go, recovery after restart
│   │   │   ├── stages/         # probe, audio, transcribe, ocr (on-screen text), refine, translate
│   │   │   ├── gap_recovery.py # Missed-speech recovery (Whisper without VAD over uncovered audio)
│   │   │   ├── word_snap.py    # Subtitle start alignment
│   │   │   ├── speech_regions.py, retime.py   # Speech maps, "Re-check timing" and drag-to-reorder
│   │   │   ├── foreign_speech.py              # Passages in other languages
│   │   │   ├── speaker_detect.py, qa.py       # Gender detection from frames, quality check
│   │   │   └── context.py                     # Job context passed between stages
│   │   ├── providers/          # faster-whisper transcription; Gemini (+ Flex wrapper) and Noop translation
│   │   └── utils/              # SRT/VTT formatter, RTL fixer, language list, text rules
│   └── tests/                  # pytest suites
├── frontend/                   # React 19 + TypeScript + Vite 8 + Tailwind 4 + Zustand
│   ├── vite.config.ts          # Build & dev proxy to the backend
│   └── src/
│       ├── components/         # ingest/, monitor/, studio/, settings/, layout/
│       ├── store/              # Zustand stores (jobs + WebSocket listener, settings, UI)
│       ├── services/api.ts     # Typed REST client
│       ├── hooks/              # Cost estimate
│       ├── i18n/               # English & Hebrew UI strings
│       └── types/              # TypeScript data contracts
└── storage/                    # Created at runtime, git-ignored
    ├── subforge.db             # Projects and subtitles (SQLite)
    ├── settings.json           # Choices from the Settings screen
    ├── uploads/                # Browser-uploaded video copies
    ├── audio/, frames/         # Temporary audio and sampled frames
    ├── exports/                # Exported subtitle files
    ├── regions/                # Speech-map cache for "Re-check timing"
    ├── glossaries/             # Series glossary profiles
    └── logs/                   # server.log + jobs/<job>.log
```

### API at a glance

Interactive docs are at `http://localhost:8000/docs` while the server runs.

| Area | Endpoints |
|---|---|
| System | `GET /api/system/info` (hardware, models), `GET /api/system/log` (server log) |
| Media | `POST /api/media/probe-path`, `POST /api/media/upload`, `GET /api/media/upload/existing`, `GET /api/media/stream/{job_id}` (Range) |
| Jobs | `POST/GET /api/jobs`, `GET/DELETE /api/jobs/{id}`, `POST /api/jobs/{id}/cancel`, `POST /api/jobs/{id}/translate` |
| Editing | `PATCH /api/jobs/{id}/segments/{segment_id}`, `PATCH /api/jobs/{id}/segments` (bulk), `POST /api/jobs/{id}/segments/move`, `POST /api/jobs/{id}/retime` |
| Translation tools | `PUT /api/jobs/{id}/glossary`, `POST /api/jobs/{id}/detect-speakers`, `GET /api/jobs/{id}/qa`, `GET /api/glossary-profiles` |
| Export | `GET /api/jobs/{id}/export/srt`, `GET /api/jobs/{id}/export/vtt`, `GET /api/jobs/{id}/export/log` |
| Settings | `GET/PUT /api/settings`, `POST /api/settings/estimate`, `POST /api/settings/check` |
| Storage | `GET /api/storage`, `POST /api/storage/clean` |
| Live | `WS /ws/jobs/{job_id}` |

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- **Python 3.11+** (`run.bat` uses 3.12)
- **Node.js 20.19+ or 22.12+** & npm (required by Vite 8)
- **FFmpeg & FFprobe** installed and accessible on PATH
- **Optional:** an NVIDIA GPU for fast transcription. The CUDA cuBLAS/cuDNN runtime comes from `requirements.txt`, so no separate CUDA install is needed; without a GPU Whisper runs on the CPU (INT8).

### 2. Backend Setup
```bash
# Navigate to backend directory
cd backend

# Create virtual environment (using uv or python -m venv)
uv venv --python 3.12 .venv
# On Windows:
.venv\Scripts\activate

# Install dependencies
uv pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
```

Edit `backend/.env` to configure your Google Gemini API key:
```env
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.8-flash
WHISPER_DEFAULT_MODEL=medium
WHISPER_DEVICE=auto
```

`GEMINI_MODEL` / `GEMINI_VISION_MODEL` are only the first-run defaults: after that, models and thinking levels are chosen in **Settings** (saved to `storage/settings.json`).

Optional settings (defaults shown):

| Setting | Default | What it does |
|---|---|---|
| `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | `auto` / `auto` | `cuda` or `cpu`; `float16` or `int8`. `auto` picks CUDA FP16 when a GPU is found, otherwise CPU INT8. |
| `WHISPER_BATCH_SIZE` | `8` | Batched GPU inference. Lower if you run out of VRAM; `0` = sequential. |
| `WHISPER_GAP_RECOVERY` | `true` | Missed-speech recovery (see Settings above). |
| `WHISPER_SNAP_STARTS` | `true` | Subtitle start alignment (see Settings above). |
| `MAX_CONCURRENT_TRANSCRIPTIONS` | `1` | Jobs allowed on the GPU at once (others wait as *Queued*). |
| `GEMINI_BATCH_SIZE` | `30` | Subtitle lines per translation request. |
| `GEMINI_GLOSSARY_PASS` | `true` | Build a character/terminology glossary before translating. |
| `GEMINI_VISION_MODEL` | `gemini-3.5-flash-lite` | Initial model that reads on-screen text from frames. |
| `ONSCREEN_FRAME_INTERVAL` / `ONSCREEN_MAX_FRAMES` | `2.0` / `360` | Frame sampling interval (s) and the most frames sent to Gemini per job. |
| `ONSCREEN_BATCH_SIZE` | `12` | Frames per Gemini Vision request. |
| `MAX_CHARS_PER_LINE` | `42` | Line length limit for refinement and export. |
| `TARGET_CPS` / `MAX_CPS_WARNING` | `17` / `20` | Reading speed the timing pass aims for / when the editor flags a line. |
| `MIN_SUBTITLE_DURATION` / `MAX_SUBTITLE_DURATION` | `1.0` / `7.0` | Display time limits (seconds). |
| `UPLOAD_RETENTION_HOURS` | `24` | Unused browser uploads older than this are removed at startup. |
| `STORAGE_DIR` / `DATABASE_URL` | `./storage` / `sqlite:///./storage/subforge.db` | Where data and the database live. |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Server address. Keep `127.0.0.1` unless you really want LAN access: the API can read any file path on this PC. |
| `ALLOWED_HOSTS` | `["localhost","127.0.0.1","[::1]"]` | Host names the server answers to (blocks DNS-rebinding attacks). Add your PC's LAN name/IP if you set `HOST=0.0.0.0`. |

An API key typed into the UI is used for that run only and is never written to disk.

### 3. Frontend Setup
```bash
# Navigate to frontend directory
cd frontend

# Install dependencies
npm install

# Build the frontend production bundle
npm run build
```

---

## 🏃 Running the Application

### Option A: One-click (Windows)
Double-click `run.bat`. It creates the virtual environment if needed, rebuilds the frontend whenever its sources changed, and opens the browser.

### Option B: Standalone Production Mode (Single Command)
Run the backend server, which automatically serves the compiled frontend on port 8000:
```bash
# From workspace root or backend directory:
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```
Use `--host 0.0.0.0` only for LAN access, together with `ALLOWED_HOSTS` (see the table above).
Open your browser at: **`http://localhost:8000`**

### Option C: Development Mode (Hot Reloading)
**Terminal 1 — Backend:**
```bash
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

**Terminal 2 — Frontend:**
```bash
cd frontend
npm run dev
```
Open your browser at: **`http://localhost:5173`** (API requests & WebSockets proxy to `:8000`).

---

## 📦 Desktop App (Standalone EXE)

`build_exe.bat` turns the project into a standalone Windows program: a folder with **`SubForge Studio.exe`** that includes Python, every library, the built frontend, FFmpeg and the NVIDIA CUDA runtime. The target PC needs no Python, Node or FFmpeg (only an NVIDIA driver for GPU transcription).

### Build
Requires the same tools as the Quickstart (Python or `uv`, Node.js). Double-click **`build_exe.bat`**. It:
1. installs the backend and build dependencies (`packaging/requirements-build.txt`: PyInstaller, pystray, Pillow) into `backend\.venv`;
2. builds the frontend;
3. downloads a static FFmpeg build into `packaging\ffmpeg` (once; put your own `ffmpeg.exe` + `ffprobe.exe` there to skip it);
4. packages everything with PyInstaller (`packaging/subforge.spec`) into **`dist\SubForge Studio\`**;
5. copies `backend\.env` (your API key) to the app's data folder if that folder has none yet.

The output is about 2-3 GB, mostly the NVIDIA libraries. Copy or move the **whole** `dist\SubForge Studio` folder; the exe needs the `_internal` folder next to it.

### Using it
- Run `SubForge Studio.exe`: the server starts in the background, an icon appears in the system tray and the studio opens in your browser. Running the exe again while it's open just opens the browser.
- **Tray menu** (right-click): *Open SubForge Studio* (or just click the icon), *Open data folder*, *Edit settings (.env)*, *Server log*, *Restart server* (after editing `.env`) and *Quit*. Quit and Restart ask first if a project is still running.
- **Data folder:** the `.env`, projects database, settings and logs live in `%LOCALAPPDATA%\SubForge Studio`, not in the program folder, so rebuilding or replacing the program never touches them. To keep everything next to the exe instead (portable mode), create an empty `data` folder beside `SubForge Studio.exe`; the `SUBFORGE_HOME` environment variable overrides both.
- Startup errors are shown in a message box. Details are in `launcher.log` and `storage\logs\server.log` in the data folder.
- Whisper models are downloaded on first use to the usual Hugging Face cache in your user profile, shared with the developer setup.
- The launcher also runs from source, without building: `backend\.venv\Scripts\python.exe packaging\launcher.py` (add `--no-tray` to run in the console, `--no-browser` to skip opening the browser).

To change the icon, edit and run `packaging/make_icon.py` (it writes `packaging/icon.ico`).

---

## 🧪 Running Unit Tests

Run the backend test suite with `pytest`:
```bash
# From project root:
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\pytest.exe backend/tests
```

The test suite covers:
- **`test_rtl_fixer.py`**: Hebrew RTL detection, BiDi mark wrapping, dialogue hyphen normalization, and idempotency.
- **`test_srt_formatter.py`**: Timecode calculations, balanced line splitting at 42 chars, SRT/VTT formatting.
- **`test_refine_stage.py`** / **`test_refine_quality.py`**: re-joining cut sentences, fragments, no cross-speaker merges, clause-aware splitting, reading-speed timing, gaps, loop removal.
- **`test_whisper_provider.py`**: threading, real cancellation and batched→sequential fallback (fake model).
- **`test_gemini_provider.py`**: partial retries, failure flags, invalid-key abort, glossary (fake API client).
- **`test_improvements.py`**: retranslate flow, uploads, range requests, host check, bulk edits, DB migration.
- **`test_settings_pricing.py`**: promo price dates, cost math incl. thinking and long-context tiers, thinking config per model family, Settings API validation, estimates, and exact usage accumulating across runs.
- **`test_foreign_speech.py`**: foreign/untranscribed span detection, tight "cores" vs OCR windows, merge rules, Whisper analysis with a fake model, and an end-to-end run where burned-in subtitles replace garbled text and the scan extends past the window.
- **`test_comms_onscreen.py`**: timeline merging, OCR de-duplication, frame selection, bracketed RTL titles, forced-track parsing, comms filter/decoding/classification, and two real-ffmpeg end-to-end runs (forced track; frame scan with a fake Gemini Vision).
- **`test_pipeline_e2e.py`**: the whole pipeline on a generated clip through real ffmpeg (Whisper faked).
- **`test_api_and_pipeline.py`** / **`test_stage1_fixes.py`**: system info, persistence, exports, recovery, cancel guards.
- **`test_flex.py`**: Flex pricing, settings, the retry/fallback wrapper and how callers use it.
- **`test_gap_recovery.py`**: recovering speech the voice detector skipped, hallucination checks, shouted commands.
- **`test_word_snap.py`**: word starts inside a pause moved to the speech onset.
- **`test_retime.py`**: "Re-check timing" rules and drag-to-move rows.
- **`test_row_translate.py`**: re-translating selected rows with speaker/addressee hints, editing the glossary.
- **`test_glossary_profiles.py`**: series glossary profiles saved, reused and extended across episodes.
- **`test_speaker_detect.py`**: frame selection and gender detection from video.
- **`test_qa.py`**: quality check (untranslated words, glossary mismatches, pronoun/verb agreement).
- **`test_storage_cleanup.py`**: storage report and cleanup never touch active jobs, your own videos or the live server log.
- **`test_logs.py`**: per-job log capture (including worker threads) and log export.
