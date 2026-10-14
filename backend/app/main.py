import logging
from contextlib import asynccontextmanager
from pathlib import Path

from app.core import compat as _compat  # noqa: F401  (applies PyAV/faster-whisper compatibility shim)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.core.housekeeping import cleanup_storage
from app.core.ws_manager import ws_manager
from app.api import routes_system, routes_media, routes_jobs, routes_export, routes_settings, routes_glossary, routes_storage
from app.pipeline.orchestrator import recover_interrupted_jobs

from app.core.job_logging import setup_logging

setup_logging()
logger = logging.getLogger("subforge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("Initializing SubForge Studio Database...")
    init_db()
    recovered = recover_interrupted_jobs()
    if recovered:
        logger.warning(f"Recovered {recovered} job(s) interrupted by a restart.")
    try:
        cleanup_storage()
    except Exception as e:  # never block startup on housekeeping
        logger.warning(f"Storage cleanup failed: {e}")
    logger.info(f"SubForge Studio backend initialized. Gemini configured: {settings.is_gemini_configured}")
    yield
    logger.info("Shutting down SubForge Studio backend...")


app = FastAPI(
    title="SubForge Studio API",
    description="Professional local video subtitling, transcription & translation pipeline.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS: only allow the local frontend origins (Vite dev server + same-origin prod build).
# A wildcard origin would let any website the user visits drive this API, which can read
# arbitrary local files via job creation + streaming.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Reject requests whose Host header isn't this machine. Without this, a malicious website
# could use DNS rebinding to reach the API (which can read any local file) via the browser.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

# Register API routers
app.include_router(routes_system.router)
app.include_router(routes_media.router)
app.include_router(routes_jobs.router)
app.include_router(routes_export.router)
app.include_router(routes_settings.router)
app.include_router(routes_glossary.router)
app.include_router(routes_storage.router)


@app.websocket("/ws/jobs/{job_id}")
async def websocket_job_endpoint(websocket: WebSocket, job_id: str):
    """Real-time WebSocket connection endpoint for live progress and segment streaming."""
    await ws_manager.connect(job_id, websocket)
    try:
        while True:
            # Keep listener active; clients can send keepalive pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(job_id, websocket)
    except Exception:
        ws_manager.disconnect(job_id, websocket)



# If production frontend build exists, serve it; otherwise provide API root
frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
else:
    @app.get("/")
    def root():
        return {
            "status": "online",
            "app": "SubForge Studio API",
            "gemini_configured": settings.is_gemini_configured,
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
