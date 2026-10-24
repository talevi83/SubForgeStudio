from pathlib import Path
from typing import Optional
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Depends, Query, Response
from sqlmodel import Session

from app.core.config import settings
from app.core.database import get_session
from app.models.db_models import Job
from app.utils.languages import language_code
from app.utils.srt_formatter import serialize_to_srt, serialize_to_vtt

router = APIRouter(prefix="/api/jobs", tags=["Export"])


def _lang_code(language: Optional[str]) -> Optional[str]:
    """Short language code for a language name/code (Movie.he.srt), or None if unknown/auto."""
    return language_code(language)


def _build_filename(job: Job, use_translated: bool, ext: str) -> str:
    code = _lang_code(job.target_language if use_translated else job.effective_source_language)
    suffix = f".{code}.{ext}" if code else f".{ext}"
    return f"{Path(job.file_name).stem}{suffix}"


def _attachment_headers(filename: str) -> dict:
    """Content-Disposition that works for non-ASCII names (RFC 6266 / RFC 5987)."""
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_").replace('"', "_")
    return {
        "Content-Disposition": f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "no-cache",
    }


def _export(job_id: str, session: Session, use_translated: bool, apply_rtl: bool,
            max_chars_per_line: Optional[int], fmt: str) -> Response:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    serializer = serialize_to_srt if fmt == "srt" else serialize_to_vtt
    onscreen_pos = getattr(job, "onscreen_position", "top_center") or "top_center"
    content = serializer(
        job.segments,
        use_translated=use_translated,
        apply_rtl=apply_rtl,
        max_chars_per_line=max_chars_per_line or settings.max_chars_per_line,
        onscreen_position=onscreen_pos,
    )
    media_type = "application/x-subrip; charset=utf-8" if fmt == "srt" else "text/vtt; charset=utf-8"
    # UTF-8 BOM: some players (and Windows Notepad) otherwise mis-detect Hebrew as ANSI.
    body = ("﻿" + content) if fmt == "srt" else content
    return Response(
        content=body.encode("utf-8"),
        media_type=media_type,
        headers=_attachment_headers(_build_filename(job, use_translated, fmt)),
    )


@router.get("/{job_id}/export/srt")
def export_srt(
    job_id: str,
    use_translated: bool = Query(False, description="Export translated subtitle text if True"),
    apply_rtl: bool = Query(True, description="Apply BiDi RTL marks for Hebrew/Arabic"),
    max_chars_per_line: Optional[int] = Query(None, description="Line length limit"),
    session: Session = Depends(get_session),
):
    """Export and download subtitles as standard .srt format."""
    return _export(job_id, session, use_translated, apply_rtl, max_chars_per_line, "srt")


@router.get("/{job_id}/export/vtt")
def export_vtt(
    job_id: str,
    use_translated: bool = Query(False, description="Export translated subtitle text if True"),
    apply_rtl: bool = Query(True, description="Apply BiDi RTL marks for Hebrew/Arabic"),
    max_chars_per_line: Optional[int] = Query(None, description="Line length limit"),
    session: Session = Depends(get_session),
):
    """Export and download subtitles as WebVTT (.vtt) format."""
    return _export(job_id, session, use_translated, apply_rtl, max_chars_per_line, "vtt")


def _job_log_header(job: Job) -> str:
    """A readable summary of the job to put on top of its log."""
    import json
    from datetime import datetime

    from app.core.user_settings import load_settings

    lines = [
        "SubForge Studio - job log",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        f"Job:        {job.id}",
        f"File:       {job.file_name}  ({job.video_duration or 0:.1f}s)",
        f"Status:     {job.status.value}  (stage {job.current_stage}, {job.progress_percent:.1f}%)",
        f"Created:    {job.created_at}   Updated: {job.updated_at}",
        f"Whisper:    {job.whisper_model}, source {job.source_language} (detected {job.detected_language}), "
        f"VAD {job.vad_filter}, comms boost {job.comms_boost}, on-screen text {job.detect_onscreen_text}",
        f"Translation:{' on' if job.enable_translation else ' off'} -> {job.target_language}, model {job.gemini_model}",
    ]
    if job.warning_message:
        lines.append(f"Warning:    {job.warning_message}")
    if job.error_message:
        lines.append(f"Error:      {job.error_message}")

    counts: dict = {}
    for seg in job.segments:
        kind = seg.segment_type or "DIALOGUE"
        counts[kind] = counts.get(kind, 0) + 1
    untranslated = [seg for seg in job.segments if not seg.translated_text]
    failed = [seg for seg in job.segments if seg.translation_failed]
    lines.append(f"Subtitles:  {len(job.segments)} {counts}; untranslated {len(untranslated)}, "
                 f"flagged failed {len(failed)}")
    if job.enable_translation and untranslated:
        sample = ", ".join(f"#{s.index} {s.start_time:.1f}s" for s in sorted(untranslated, key=lambda s: s.start_time)[:20])
        lines.append(f"            untranslated: {sample}{' ...' if len(untranslated) > 20 else ''}")

    if job.usage_json:
        try:
            usage = json.loads(job.usage_json)
            lines.append(f"Gemini cost (all runs): {usage.get('total_cost_usd')}")
            for name, st in (usage.get("stages") or {}).items():
                lines.append(
                    f"  {name}: {st.get('model')}, {st.get('requests')} request(s), in {st.get('input_tokens')}, "
                    f"out {st.get('output_tokens')}, thinking {st.get('thinking_tokens')}, cost {st.get('cost_usd')}, "
                    f"Flex {st.get('flex_requests', 0)}, Flex->Standard {st.get('flex_fallbacks', 0)}"
                )
            loc = usage.get("local")
            if isinstance(loc, dict) and (loc.get("requests") or loc.get("saved_tokens") or loc.get("frames_filtered")):
                lines.append(
                    f"  Local AI & Cost-Saver: model {loc.get('model') or 'local'}, "
                    f"{loc.get('requests', 0)} local query(s), saved {loc.get('saved_tokens', 0)} tokens "
                    f"(~${loc.get('saved_cost_usd', 0.0):.4f})"
                )
        except ValueError:
            pass
    try:
        lines.append(f"Current AI settings: {load_settings().model_dump_json()}")
    except Exception:
        pass
    return "\n".join(lines) + "\n"


@router.get("/{job_id}/export/log")
def export_job_log(job_id: str, session: Session = Depends(get_session)):
    """Everything logged while this job ran (all runs, incl. retranslations), with a summary on top."""
    from app.core.job_logging import job_log_path

    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    path = job_log_path(job.id)
    if path is not None and path.exists():
        body = path.read_text(encoding="utf-8", errors="replace")
    else:
        body = "(No log was recorded for this job - it ran before detailed logging was added.)\n"
    content = _job_log_header(job) + "\n" + "=" * 30 + " log " + "=" * 30 + "\n" + body
    return Response(
        content=content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers=_attachment_headers(f"{Path(job.file_name).stem}.{job.id}.log"),
    )
