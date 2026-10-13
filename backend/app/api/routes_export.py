from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Query, Response
from sqlmodel import Session

from app.core.database import get_session
from app.models.db_models import Job
from app.utils.srt_formatter import serialize_to_srt, serialize_to_vtt

router = APIRouter(prefix="/api/jobs", tags=["Export"])


@router.get("/{job_id}/export/srt")
def export_srt(
    job_id: str,
    use_translated: bool = Query(False, description="Export translated subtitle text if True"),
    apply_rtl: bool = Query(True, description="Apply BiDi RTL marks for Hebrew/Arabic"),
    max_chars_per_line: int = Query(42, description="Line length limit"),
    session: Session = Depends(get_session),
):
    """Export and download subtitles as standard .srt format."""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    srt_content = serialize_to_srt(
        job.segments,
        use_translated=use_translated,
        apply_rtl=apply_rtl,
        max_chars_per_line=max_chars_per_line,
    )

    base_name = Path(job.file_name).stem
    suffix = ".he.srt" if use_translated else ".srt"
    filename = f"{base_name}{suffix}"

    return Response(
        content=srt_content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache",
        },
    )


@router.get("/{job_id}/export/vtt")
def export_vtt(
    job_id: str,
    use_translated: bool = Query(False, description="Export translated subtitle text if True"),
    apply_rtl: bool = Query(True, description="Apply BiDi RTL marks for Hebrew/Arabic"),
    max_chars_per_line: int = Query(42, description="Line length limit"),
    session: Session = Depends(get_session),
):
    """Export and download subtitles as WebVTT (.vtt) format."""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    vtt_content = serialize_to_vtt(
        job.segments,
        use_translated=use_translated,
        apply_rtl=apply_rtl,
        max_chars_per_line=max_chars_per_line,
    )

    base_name = Path(job.file_name).stem
    suffix = ".he.vtt" if use_translated else ".vtt"
    filename = f"{base_name}{suffix}"

    return Response(
        content=vtt_content,
        media_type="text/vtt; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache",
        },
    )
