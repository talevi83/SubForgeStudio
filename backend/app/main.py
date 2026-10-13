import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.core.ws_manager import ws_manager
from app.api import routes_system, routes_media, routes_jobs, routes_export

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("subforge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("Initializing SubForge Studio Database...")
    init_db()
    logger.info(f"SubForge Studio backend initialized. Gemini configured: {settings.is_gemini_configured}")
    yield
    logger.info("Shutting down SubForge Studio backend...")


app = FastAPI(
    title="SubForge Studio API",
    description="Professional local video subtitling, transcription & translation pipeline.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration for local frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(routes_system.router)
app.include_router(routes_media.router)
app.include_router(routes_jobs.router)
app.include_router(routes_export.router)


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


from pathlib import Path
from fastapi.staticfiles import StaticFiles

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
