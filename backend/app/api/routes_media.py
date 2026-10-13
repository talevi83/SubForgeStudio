import asyncio
import os
import re
import uuid
import shutil
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Header, Depends, Query
from fastapi.responses import StreamingResponse, FileResponse
from sqlmodel import Session

from app.core.config import settings
from app.core.database import get_session
from app.models.db_models import Job
from app.models.schemas import ProbeResponse, ProbePathRequest
from app.pipeline.stages.probe_stage import probe_media_file

router = APIRouter(prefix="/api/media", tags=["Media"])


@router.post("/probe-path", response_model=ProbeResponse)
async def probe_local_path(req: ProbePathRequest):
    """Inspect and probe media streams of a file directly from the local filesystem."""
    file_path = req.file_path.strip().strip('"').strip("'")
    path = Path(file_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found on local disk: {file_path}")

    try:
        return await probe_media_file(str(path))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to probe media file: {exc}")


@router.post("/upload", response_model=ProbeResponse)
async def upload_media_file(file: UploadFile = File(...)):
    """Upload a video file from browser dropzone and probe its streams."""
    safe_name = f"{uuid.uuid4().hex[:8]}_{file.filename}"
    upload_dest = settings.storage_dir / "uploads" / safe_name
    upload_dest.parent.mkdir(parents=True, exist_ok=True)

    def _save():
        with open(upload_dest, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

    try:
        await asyncio.to_thread(_save)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {exc}")

    try:
        return await probe_media_file(str(upload_dest))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Probing uploaded file failed: {exc}")


def _stream_file_range(file_path: Path, start: int, end: int, chunk_size: int = 1024 * 1024):
    """Generator to yield chunks for HTTP Range requests."""
    with open(file_path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            read_size = min(chunk_size, remaining)
            data = f.read(read_size)
            if not data:
                break
            remaining -= len(data)
            yield data


@router.get("/stream/{job_id}")
async def stream_video(
    job_id: str,
    range_header: Optional[str] = Header(None, alias="Range"),
    session: Session = Depends(get_session),
):
    """
    HTTP Range streaming endpoint for smooth seeking in HTML5 Video Player.
    Supports 206 Partial Content for both direct paths and uploaded files.
    """
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    video_path = Path(job.video_path)
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail=f"Video file missing on disk: {job.video_path}")

    file_size = os.path.getsize(video_path)

    # If no Range header, return initial full file response or first chunk
    if not range_header:
        return FileResponse(
            str(video_path),
            media_type="video/mp4",
            headers={"Accept-Ranges": "bytes"},
        )

    # Parse range header: e.g. "bytes=0-1000" or "bytes=1000-"
    range_match = re.search(r"bytes=(\d+)-(\d*)", range_header)
    if not range_match:
        raise HTTPException(status_code=416, detail="Invalid Range header")

    start = int(range_match.group(1))
    end_str = range_match.group(2)
    end = int(end_str) if end_str else file_size - 1

    if start >= file_size or end >= file_size or start > end:
        raise HTTPException(
            status_code=416,
            detail="Requested range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    content_length = end - start + 1
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": "video/mp4",
    }

    return StreamingResponse(
        _stream_file_range(video_path, start, end),
        status_code=206,
        headers=headers,
    )
