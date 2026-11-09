"""Put a job's subtitles into a copy of its video.

Two ways:
  soft  - add a subtitle track (MKV: SubRip, MP4/MOV: mov_text). Every other stream is copied
          untouched, so it takes about as long as copying the file and quality is unchanged.
          Players can switch the track on and off.
  burn  - draw the subtitles onto the picture (libass) and re-encode to an H.264 MP4 that plays
          anywhere (TVs, phones, WhatsApp). Slow; tries NVENC first when ffmpeg has it and falls
          back to libx264.

The work runs as a background asyncio task with ffmpeg's `-progress` output turned into a percent.
The result is written to a hidden temp name first and renamed at the end, so a cancelled or failed
run never leaves a half-written video behind (or replaces a good one).
"""
import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app.pipeline.stages.audio_stage import get_ffmpeg_bin
from app.pipeline.stages.probe_stage import TEXT_SUBTITLE_CODECS, get_ffprobe_bin

logger = logging.getLogger(__name__)

MP4_FAMILY = {".mp4": "mp4", ".m4v": "mp4", ".mov": "mov"}

# Font size on libass's 288-line canvas for SRT input (scales with the video's height)
BURN_FONT_SIZES = {"small": 16, "medium": 19, "large": 23}

# x264 settings per quality choice: (preset, crf). NVENC: (preset, cq)
X264_QUALITY = {"standard": ("veryfast", 20), "high": ("slow", 18)}
NVENC_QUALITY = {"standard": ("p4", 23), "high": ("p6", 19)}

# Audio codecs that play in an MP4 on practically every device; anything else becomes AAC
PORTABLE_AUDIO = {"aac", "mp3"}


@dataclass
class EmbedOptions:
    mode: str = "soft"                 # soft | burn
    language3: Optional[str] = None    # ISO 639-2 tag for the new track ("heb")
    track_title: Optional[str] = None  # shown in the player's subtitle menu
    set_default: bool = True           # soft: make the new track the default one
    font_size: str = "medium"          # burn
    quality: str = "standard"          # burn
    audio_stream: int = 0              # burn: which audio track goes into the MP4 (relative index)


@dataclass
class EmbedTask:
    id: str
    job_id: str
    mode: str
    target: Path
    in_app_storage: bool               # True: the file is offered as a download, not saved next to the video
    state: str = "running"             # running | done | error | cancelled
    percent: float = 0.0
    error: Optional[str] = None
    encoder: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    cancel_requested: bool = False
    process: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)
    runner: Optional[asyncio.Task] = field(default=None, repr=False)


_tasks: Dict[str, EmbedTask] = {}
_nvenc_available: Optional[bool] = None


class EmbedCancelled(Exception):
    pass


# ---------------------------------------------------------------- planning

def output_extension(video_path: str, mode: str) -> str:
    """Soft keeps MP4/MOV (mov_text track) and turns everything else into MKV; burn is always MP4."""
    if mode == "burn":
        return ".mp4"
    ext = Path(video_path).suffix.lower()
    return ext if ext in MP4_FAMILY else ".mkv"


def output_name(video_name: str, lang_code: Optional[str], mode: str, ext: str) -> str:
    """Movie.mkv -> Movie.he.subs.mkv (soft) / Movie.he.burned.mp4 (burn)."""
    stem = Path(video_name).stem
    tag = "subs" if mode == "soft" else "burned"
    return f"{stem}.{lang_code}.{tag}{ext}" if lang_code else f"{stem}.{tag}{ext}"


def temp_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.subbuild-tmp")


async def probe_streams(video_path: str) -> dict:
    """ffprobe streams + duration: {"duration": float, "streams": [...]}."""
    proc = await asyncio.create_subprocess_exec(
        get_ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
        str(Path(video_path).resolve()),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe could not read the video: {err.decode(errors='ignore')[-500:]}")
    data = json.loads(out.decode(errors="ignore") or "{}")
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"duration": duration, "streams": data.get("streams") or []}


def build_soft_command(video_path: str, subs_path: str, out_path: str, probe: dict, opts: EmbedOptions) -> List[str]:
    """Copy every stream and add the subtitle file as one more subtitle track."""
    ext = output_extension(video_path, "soft")
    is_mp4 = ext in MP4_FAMILY
    streams = probe.get("streams") or []
    cmd = [get_ffmpeg_bin(), "-y", "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1",
           "-i", str(Path(video_path).resolve()), "-i", str(subs_path)]

    if is_mp4:
        # MP4 holds only text subtitles as mov_text, and no unknown data tracks
        cmd += ["-map", "0:v?", "-map", "0:a?"]
        kept_subs = [s for s in streams if s.get("codec_type") == "subtitle"
                     and (s.get("codec_name") or "").lower() in TEXT_SUBTITLE_CODECS]
        for s in kept_subs:
            cmd += ["-map", f"0:{s['index']}"]
    else:
        # Matroska takes video/audio/subtitles/attachments (fonts); data tracks (e.g. from .ts) would fail
        cmd += ["-map", "0:v?", "-map", "0:a?", "-map", "0:s?", "-map", "0:t?"]
        kept_subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    cmd += ["-map", "1:0", "-c", "copy"]
    if is_mp4:
        cmd += ["-c:s", "mov_text"]

    new = len(kept_subs)  # index of the new track among the output's subtitle tracks
    if opts.language3:
        cmd += [f"-metadata:s:s:{new}", f"language={opts.language3}"]
    if opts.track_title:
        cmd += [f"-metadata:s:s:{new}", f"title={opts.track_title}"]
    if opts.set_default:
        cmd += ["-disposition:s", "0", f"-disposition:s:{new}", "default"]
    cmd += ["-max_muxing_queue_size", "4096"]
    if is_mp4:
        cmd += ["-movflags", "+faststart"]
    cmd += ["-f", MP4_FAMILY.get(ext, "matroska"), str(out_path)]
    return cmd


def burn_style(font_size: str) -> str:
    size = BURN_FONT_SIZES.get(font_size, BURN_FONT_SIZES["medium"])
    return (f"FontName=Arial,FontSize={size},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            f"BackColour=&H80000000,BorderStyle=1,Outline=1.6,Shadow=0.6,MarginV=16")


def build_burn_command(video_path: str, subs_name: str, out_path: str, probe: dict, opts: EmbedOptions,
                       encoder: str) -> List[str]:
    """Re-encode the first video stream with the subtitles drawn on it, plus one audio track.

    `subs_name` is a bare file name: ffmpeg runs with the subtitle file's folder as its working
    directory, which avoids the filter-graph escaping of Windows paths (C\\:/...).
    """
    streams = probe.get("streams") or []
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    a_idx = opts.audio_stream if 0 <= opts.audio_stream < len(audio) else 0

    vf = f"subtitles={subs_name}:charenc=UTF-8:force_style='{burn_style(opts.font_size)}',format=yuv420p"
    cmd = [get_ffmpeg_bin(), "-y", "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1",
           "-i", str(Path(video_path).resolve()), "-map", "0:v:0"]
    if audio:
        cmd += ["-map", f"0:a:{a_idx}"]
    cmd += ["-vf", vf]
    if encoder == "h264_nvenc":
        preset, cq = NVENC_QUALITY.get(opts.quality, NVENC_QUALITY["standard"])
        cmd += ["-c:v", "h264_nvenc", "-preset", preset, "-rc", "vbr", "-cq", str(cq), "-b:v", "0"]
    else:
        preset, crf = X264_QUALITY.get(opts.quality, X264_QUALITY["standard"])
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
    if audio:
        codec = (audio[a_idx].get("codec_name") or "").lower()
        if codec in PORTABLE_AUDIO:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    cmd += ["-sn", "-dn", "-map_metadata", "0", "-map_chapters", "0",
            "-max_muxing_queue_size", "4096", "-movflags", "+faststart", "-f", "mp4", str(out_path)]
    return cmd


async def nvenc_available() -> bool:
    """Whether this ffmpeg build lists h264_nvenc (it may still fail without an NVIDIA GPU)."""
    global _nvenc_available
    if _nvenc_available is None:
        try:
            proc = await asyncio.create_subprocess_exec(
                get_ffmpeg_bin(), "-hide_banner", "-encoders",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            _nvenc_available = b"h264_nvenc" in out
        except OSError:
            _nvenc_available = False
    return _nvenc_available


# ---------------------------------------------------------------- running

async def _run(task: EmbedTask, cmd: List[str], duration: float, cwd: Optional[str] = None,
               start: float = 0.0, span: float = 100.0) -> None:
    """Run ffmpeg, mapping its progress onto task.percent in [start, start+span]."""
    logger.info("Embed %s (%s): %s", task.id, task.mode, " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    task.process = proc
    stderr_task = asyncio.ensure_future(proc.stderr.read())
    try:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            key, _, value = line.decode(errors="ignore").strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and duration > 0:
                try:
                    done = int(value) / 1_000_000 / duration
                except ValueError:
                    continue
                task.percent = round(start + span * max(0.0, min(0.99, done)), 1)
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        task.process = None
    err = (await stderr_task).decode(errors="ignore").strip()
    if task.cancel_requested:
        raise EmbedCancelled()
    if proc.returncode != 0:
        raise RuntimeError(err[-1500:] or f"ffmpeg exited with code {proc.returncode}")


async def _execute(task: EmbedTask, video_path: str, subtitle_text: str, opts: EmbedOptions) -> None:
    tmp_out = temp_path(task.target)
    workdir = Path(tempfile.mkdtemp(prefix="subbuild_embed_"))
    try:
        subs = workdir / "subs.srt"
        subs.write_text(subtitle_text, encoding="utf-8")
        probe = await probe_streams(video_path)
        duration = probe["duration"]
        if task.cancel_requested:
            raise EmbedCancelled()

        if opts.mode == "soft":
            task.encoder = "copy"
            await _run(task, build_soft_command(video_path, str(subs), str(tmp_out), probe, opts), duration)
        else:
            encoders = (["h264_nvenc"] if await nvenc_available() else []) + ["libx264"]
            for i, encoder in enumerate(encoders):
                task.encoder = encoder
                task.percent = 0.0
                try:
                    await _run(task, build_burn_command(video_path, subs.name, str(tmp_out), probe, opts, encoder),
                               duration, cwd=str(workdir))
                    break
                except RuntimeError as exc:
                    last = i == len(encoders) - 1
                    if last:
                        raise
                    logger.warning("Embed %s: %s failed, retrying with %s: %s",
                                   task.id, encoder, encoders[i + 1], str(exc)[-300:])
                    tmp_out.unlink(missing_ok=True)

        if task.cancel_requested:
            raise EmbedCancelled()
        os.replace(tmp_out, task.target)
        task.percent = 100.0
        task.state = "done"
        logger.info("Embed %s done: %s", task.id, task.target)
    except EmbedCancelled:
        task.state = "cancelled"
    except asyncio.CancelledError:
        task.state = "cancelled"
        raise
    except Exception as exc:  # ffmpeg errors, disk full, ...
        task.state = "error"
        task.error = str(exc) or exc.__class__.__name__
        logger.error("Embed %s failed: %s", task.id, task.error)
    finally:
        tmp_out.unlink(missing_ok=True)
        shutil.rmtree(workdir, ignore_errors=True)
        task.finished_at = time.time()


def start_embed(job_id: str, video_path: str, target: Path, subtitle_text: str, opts: EmbedOptions,
                in_app_storage: bool) -> EmbedTask:
    """Start the background run (needs a running event loop) and return its task record."""
    task = EmbedTask(id=uuid.uuid4().hex[:12], job_id=job_id, mode=opts.mode, target=target,
                     in_app_storage=in_app_storage)
    _tasks[task.id] = task
    task.runner = asyncio.get_running_loop().create_task(_execute(task, video_path, subtitle_text, opts))
    return task


def get_task(task_id: str) -> Optional[EmbedTask]:
    return _tasks.get(task_id)


def latest_task(job_id: str) -> Optional[EmbedTask]:
    tasks = [t for t in _tasks.values() if t.job_id == job_id]
    return max(tasks, key=lambda t: t.started_at) if tasks else None


def running_task(job_id: str) -> Optional[EmbedTask]:
    return next((t for t in _tasks.values() if t.job_id == job_id and t.state == "running"), None)


def cancel_task(task: EmbedTask) -> None:
    if task.state != "running":
        return
    task.cancel_requested = True
    if task.process is not None and task.process.returncode is None:
        try:
            task.process.kill()
        except ProcessLookupError:
            pass


async def shutdown_embeds() -> None:
    """Stop every running ffmpeg on app shutdown so no orphan keeps writing."""
    running = [t for t in _tasks.values() if t.state == "running"]
    for t in running:
        cancel_task(t)
    runners = [t.runner for t in running if t.runner is not None]
    if runners:
        await asyncio.wait(runners, timeout=5)
