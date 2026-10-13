# SubForge Studio 🎬

**SubForge Studio** is a modular local web application for automated video subtitling, high-precision Speech-to-Text transcription with word-level timing, broadcast-grade subtitle refinement, and context-aware batch translation powered by Google Gemini API (with first-class RTL/Hebrew support).

---

## 🌟 Key Features

1. **Zero-Copy Local Ingest & Stream Discovery:**
   - Ingest multi-gigabyte files (MKV, MP4, AVI, WebM) directly from disk paths without duplicating files.
   - Discovers all video and audio tracks via `ffprobe` and lets you select the exact audio stream to transcribe.

2. **Hardware-Accelerated Transcription:**
   - Powered by `faster-whisper` (CTranslate2) with automatic hardware detection (`CUDA FP16` on NVIDIA GPUs or `CPU INT8`).
   - Built-in Silero VAD (Voice Activity Detection) filter to eliminate hallucinations and silence artifacts.

3. **Broadcast-Grade Subtitle Formatting:**
   - Enforces industry standard 42 Characters Per Line (CPL) and maximum 2 lines per subtitle block.
   - Word-level timestamp splitting for rapid speech and long sentences.
   - Real-time Reading Speed calculation (Characters Per Second - CPS) with warning badges for fast reading rates (> 20 CPS).

4. **Context-Aware Gemini Subtitle Translation:**
   - Batch translation using `gemini-2.5-flash` with sliding context window (`previous_context` + `lookahead_context`) to preserve split-sentence flow.
   - Strict 1-to-1 subtitle index mapping via Pydantic structured output.
   - Show/movie specific notes support for gender and terminology consistency.

5. **Bidirectional RTL Punctuation Safety (Hebrew & Arabic):**
   - Built-in BiDi fixer using Unicode Right-to-Left Marks (`\u200f`) to keep question marks, exclamation marks, and dialogue hyphens on the correct side in media players (VLC, Plex, HTML5 WebVTT).

6. **Interactive Studio Workspace:**
   - Split-screen workspace with synced HTML5 video player (HTTP Range seeking).
   - Real-time WebSocket event streaming as segments are transcribed and translated.
   - Inline table editor with +/- 100ms timecode nudge buttons.
   - Instant Search & Replace across original or translated dialogue.
   - Export to `.srt` or `.vtt` with optional RTL BiDi post-processing.

---

## 🏗️ Repository Architecture

```text
subforge-studio/
├── SPEC.md                     # Master Technical Specification
├── README.md                   # Setup and usage guide
├── backend/
│   ├── .env                    # Local configuration (API keys, ports)
│   ├── .env.example            # Configuration template
│   ├── requirements.txt        # Python backend dependencies
│   ├── app/
│   │   ├── main.py             # FastAPI entrypoint, CORS, static mounting
│   │   ├── core/               # Settings, SQLite database, hardware detection, WS manager
│   │   ├── models/             # SQLModel database models & Pydantic schemas
│   │   ├── pipeline/           # Orchestrator & stages (Probe, Audio, Transcribe, Refine, Translate)
│   │   ├── providers/          # Transcription (faster-whisper) & Translation (Gemini, Noop)
│   │   ├── utils/              # SRT/VTT formatter & RTL BiDi fixer
│   │   └── api/                # Media, Jobs, Export, and System API routes
│   └── tests/                  # Automated pytest test suites (100% pass)
└── frontend/
    ├── package.json
    ├── vite.config.ts          # Vite build & backend reverse proxy
    └── src/
        ├── types/              # TypeScript data contracts
        ├── services/           # Typed REST API client
        ├── store/              # Zustand global store & WebSocket listener
        └── components/         # Ingest, Live Monitor, and Studio Workspace UI
```

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- **Python 3.11+**
- **Node.js 18+** & npm
- **FFmpeg & FFprobe** installed and accessible on PATH

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
GEMINI_MODEL=gemini-2.5-flash
WHISPER_DEFAULT_MODEL=medium
WHISPER_DEVICE=auto
```

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

### Option A: Standalone Production Mode (Single Command)
Run the backend server, which automatically serves the compiled frontend on port 8000:
```bash
# From workspace root or backend directory:
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
```
Open your browser at: **`http://localhost:8000`**

### Option B: Development Mode (Hot Reloading)
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
- **`test_refine_stage.py`**: Broadcast rules, word timestamp segmentation, CPS computation, and overlap prevention.
- **`test_api_and_pipeline.py`**: System info, SQLite persistence, Noop translator, and SRT/VTT download endpoints.
