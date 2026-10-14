import asyncio
import gc
import math
import logging
import threading
from typing import Dict, List, Optional, Tuple

from app.core import compat as _compat  # noqa: F401  (must run before faster_whisper import)

from faster_whisper import WhisperModel

try:  # faster-whisper >= 1.1
    from faster_whisper import BatchedInferencePipeline
except ImportError:  # pragma: no cover
    BatchedInferencePipeline = None  # type: ignore[assignment]

from app.core.config import settings
from app.core.hardware import get_hardware_info
from app.pipeline.context import RawSegment, TranscribedWord, PipelineCancelled
from app.pipeline.foreign_speech import SAMPLE_RATE, build_foreign_spans, group_speech, load_wav
from app.pipeline.word_snap import SnapStats, snap_word_starts
from app.providers.transcription.base import BaseTranscriber, SegmentProgressCallback

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# "Tactical Comms Boost" decoding profile
# ---------------------------------------------------------------------------
TACTICAL_PRIMER = (
    "Clear transcript including military radio transmissions, walkie-talkie comms, callsigns, "
    "over, copy, roger, sitrep, klicks, and low-voice tactical dialogue."
)
# Short vocabulary list passed as `hotwords`: unlike `initial_prompt` (which faster-whisper only
# uses for the first window when condition_on_previous_text=False) hotwords bias every window.
TACTICAL_HOTWORDS = "over, copy, roger, wilco, sitrep, klicks, callsign, actual, say again, radio check"

# A permissive VAD so band-limited / static-laden transmissions are not discarded as noise
COMMS_VAD_PARAMETERS = dict(
    threshold=0.28,
    min_speech_duration_ms=120,
    min_silence_duration_ms=400,
    speech_pad_ms=450,
)
COMMS_NO_SPEECH_THRESHOLD = 0.88
COMMS_LOG_PROB_THRESHOLD = -1.25
_MAX_NOTES_IN_PROMPT = 300


def build_comms_prompt(context_notes: Optional[str], language: Optional[str]) -> Optional[str]:
    """
    Combine the user's show notes with the tactical primer for Whisper's initial_prompt.

    - Notes written in a right-to-left script (e.g. Hebrew notes for the translator) are left out:
      a prompt in another script pushes Whisper toward that language.
    - The English primer is only added when the audio is (or may be) English.
    - Whisper keeps the *last* ~224 prompt tokens, so the primer goes last and survives truncation.
    """
    from app.utils.rtl_fixer import contains_rtl

    parts = []
    notes = (context_notes or "").strip()
    if notes and not contains_rtl(notes):
        parts.append(notes[:_MAX_NOTES_IN_PROMPT])
    lang = (language or "auto").strip().lower()
    if lang in ("auto", "none", "", "en", "english"):
        parts.append(TACTICAL_PRIMER)
    return " ".join(parts) if parts else None

# ---------------------------------------------------------------------------
# Process-wide model cache. Loading large-v3 takes many seconds and several GB of
# VRAM, so the most recently used model is kept and reused by the next job. Only one
# model is kept at a time so switching sizes doesn't pile models up on the GPU.
# ---------------------------------------------------------------------------
_model_cache: Dict[Tuple[str, str, str], WhisperModel] = {}
_model_lock = threading.Lock()


def _load_model(name: str, device: str, compute_type: str) -> WhisperModel:
    key = (name, device, compute_type)
    with _model_lock:
        if key in _model_cache:
            return _model_cache[key]
        if _model_cache:
            logger.info("Unloading previously cached Whisper model(s)")
            _model_cache.clear()
            gc.collect()
        logger.info(f"Loading Faster-Whisper model '{name}' on {device} ({compute_type})...")
        model = WhisperModel(name, device=device, compute_type=compute_type)
        _model_cache[key] = model
        return model


class FasterWhisperTranscriber(BaseTranscriber):
    """
    Local Speech-to-Text provider powered by faster-whisper (CTranslate2).
    Supports CUDA FP16 / CPU INT8, Silero VAD, word-level timestamps, batched GPU
    inference and cooperative cancellation.
    """

    def __init__(
        self,
        model_size_or_path: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
        vad_filter: bool = True,
        batch_size: Optional[int] = None,
        comms_boost: bool = False,
        context_notes: Optional[str] = None,
    ):
        self.model_name = model_size_or_path or settings.whisper_default_model

        target_device = device or settings.whisper_device
        if target_device == "auto":
            target_device = "cuda" if get_hardware_info()["cuda_available"] else "cpu"
        self.device = target_device

        target_compute = compute_type or settings.whisper_compute_type
        if target_compute == "auto":
            target_compute = "float16" if self.device == "cuda" else "int8"
        self.compute_type = target_compute

        self.comms_boost = comms_boost
        self.context_notes = context_notes
        # Comms mode always runs VAD — with the permissive parameters below
        self.vad_filter = vad_filter or comms_boost
        self.batch_size = settings.whisper_batch_size if batch_size is None else batch_size
        self.detected_language: Optional[str] = None

    def _get_model(self) -> WhisperModel:
        """Load (or reuse) the model, falling back to CPU INT8 if CUDA init fails."""
        try:
            return _load_model(self.model_name, self.device, self.compute_type)
        except Exception as e:
            if self.device != "cuda":
                raise
            logger.warning(f"Failed to initialize Faster-Whisper on CUDA ({e}). Falling back to CPU INT8...")
            self.device, self.compute_type = "cpu", "int8"
            return _load_model(self.model_name, "cpu", "int8")

    def _use_batched(self) -> bool:
        # Batched inference needs VAD to cut the audio into chunks and only pays off on GPU.
        return (
            BatchedInferencePipeline is not None
            and self.batch_size > 0
            and self.device == "cuda"
            and self.vad_filter
        )

    def _start_transcription(self, model: WhisperModel, audio_path: str, lang_arg: Optional[str], batched: bool):
        common = dict(
            language=lang_arg,
            word_timestamps=True,
            # Prevents the classic long-form failure where one hallucinated line is
            # repeated for minutes: each window is decoded without the previous text.
            condition_on_previous_text=False,
        )
        if self.comms_boost:
            common.update(
                vad_parameters=dict(COMMS_VAD_PARAMETERS),
                no_speech_threshold=COMMS_NO_SPEECH_THRESHOLD,
                log_prob_threshold=COMMS_LOG_PROB_THRESHOLD,
            )
            prompt = build_comms_prompt(self.context_notes, lang_arg)
            if prompt:
                common["initial_prompt"] = prompt
            if not lang_arg or lang_arg.lower() in ("en", "english"):
                common["hotwords"] = TACTICAL_HOTWORDS
        if batched:
            logger.info(f"Transcribing with batched inference (batch_size={self.batch_size})")
            pipeline = BatchedInferencePipeline(model=model)
            return pipeline.transcribe(audio_path, batch_size=self.batch_size, vad_filter=True, **common)
        return model.transcribe(audio_path, vad_filter=self.vad_filter, **common)

    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        on_segment_callback: Optional[SegmentProgressCallback] = None,
        cancel_flag: Optional[threading.Event] = None,
    ) -> List[RawSegment]:
        """
        Transcribe audio without blocking the event loop, streaming each segment
        through on_segment_callback as soon as it is recognized.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        lang_arg = None if (not language or language.lower() in ("auto", "none")) else language
        cancel_flag = cancel_flag or threading.Event()
        # Local stop signal: set when we stop listening early (error in a callback etc.)
        # without marking the whole job as cancelled.
        stop = threading.Event()

        def _should_stop() -> bool:
            return cancel_flag.is_set() or stop.is_set()

        def _put(item) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        def _run(model: WhisperModel, batched: bool) -> int:
            """Decode the whole file; returns the number of segments emitted."""
            segments_iter, info = self._start_transcription(model, audio_path, lang_arg, batched)
            _put(("INFO", (float(info.duration or 1.0), getattr(info, "language", None))))
            emitted = 0
            # segments_iter is lazy: decoding happens while we iterate, so breaking
            # out of the loop really stops the GPU work.
            try:
                for s in segments_iter:
                    if _should_stop():
                        _put(("CANCELLED", None))
                        return -1
                    words = [
                        TranscribedWord(
                            word=w.word,
                            start=float(w.start),
                            end=float(w.end),
                            probability=float(getattr(w, "probability", 1.0)),
                        )
                        for w in (getattr(s, "words", None) or [])
                    ]
                    avg_logprob = getattr(s, "avg_logprob", None)
                    conf = max(0.0, min(1.0, math.exp(avg_logprob))) if avg_logprob is not None else 0.95
                    emitted += 1
                    _put(("SEGMENT", RawSegment(
                        index=emitted,
                        start_time=float(s.start),
                        end_time=float(s.end),
                        text=s.text.strip(),
                        confidence=round(conf, 2),
                        words=words,
                    )))
            except Exception as e:
                e.emitted = emitted  # type: ignore[attr-defined]
                raise
            return emitted

        def _worker():
            try:
                model = self._get_model()
                if _should_stop():
                    _put(("CANCELLED", None))
                    return
                batched = self._use_batched()
                try:
                    result = _run(model, batched)
                except Exception as e:
                    # Batched decoding can run out of VRAM. If nothing was emitted yet we
                    # can transparently retry with classic sequential decoding.
                    if batched and getattr(e, "emitted", 0) == 0:
                        logger.warning(f"Batched transcription failed ({e}); retrying sequentially")
                        gc.collect()
                        result = _run(model, False)
                    elif batched:
                        raise RuntimeError(
                            f"{e} (batched GPU inference; try lowering WHISPER_BATCH_SIZE in backend/.env)"
                        ) from e
                    else:
                        raise
                if result >= 0:
                    _put(("DONE", None))
            except Exception as e:
                _put(("ERROR", e))

        worker_task = asyncio.ensure_future(asyncio.to_thread(_worker))

        results: List[RawSegment] = []
        audio_duration = 1.0
        try:
            while True:
                msg_type, payload = await queue.get()
                if msg_type == "INFO":
                    audio_duration = max(1.0, payload[0])
                    self.detected_language = payload[1]
                elif msg_type == "SEGMENT":
                    results.append(payload)
                    progress_pct = min(99.0, round((payload.end_time / audio_duration) * 100, 2))
                    if on_segment_callback:
                        await on_segment_callback(payload, progress_pct)
                elif msg_type == "DONE":
                    break
                elif msg_type == "CANCELLED":
                    raise PipelineCancelled()
                elif msg_type == "ERROR":
                    raise RuntimeError(f"Whisper transcription failed: {payload}") from payload
        finally:
            # If we leave early (cancel / callback error), tell the worker to stop and
            # wait for it so the GPU is actually free before the next job starts.
            stop.set()
            await asyncio.gather(worker_task, return_exceptions=True)
        return results


    # ------------------------------------------------------------------
    # Foreign-language passages
    # ------------------------------------------------------------------

    def _analyze_foreign_sync(
        self,
        audio_path: str,
        main_language: Optional[str],
        spoken: List[RawSegment],
        cancel_flag: threading.Event,
    ):
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        model = self._get_model()
        audio = load_wav(audio_path)
        timestamps = get_speech_timestamps(
            audio, VadOptions(min_speech_duration_ms=250, min_silence_duration_ms=300, speech_pad_ms=100)
        )
        chunks = []
        for start, end in group_speech(timestamps):
            if cancel_flag.is_set():
                raise PipelineCancelled()
            piece = audio[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)]
            language, probability, _ = model.detect_language(audio=piece)
            chunks.append({"start": start, "end": end, "language": language, "probability": float(probability)})

        spans = build_foreign_spans(chunks, main_language, [(s.start_time, s.end_time) for s in spoken])

        # Fallback text: re-run Whisper on each foreign span in that span's own language.
        # (Transcribing rather than Whisper's "translate" task: large-v3-turbo can't translate,
        # and Gemini translates the original language better than a double translation.)
        fallback: List[RawSegment] = []
        for span in spans:
            if not span["language"]:
                continue
            for core_start, core_end in span["cores"]:
                if cancel_flag.is_set():
                    raise PipelineCancelled()
                piece = audio[int(core_start * SAMPLE_RATE):int(core_end * SAMPLE_RATE)]
                segments, _ = model.transcribe(
                    piece,
                    language=span["language"],
                    word_timestamps=True,
                    vad_filter=True,
                    condition_on_previous_text=False,
                )
                for s in segments:
                    text = s.text.strip()
                    if not text:
                        continue
                    words = [
                        TranscribedWord(word=w.word, start=float(w.start) + core_start, end=float(w.end) + core_start,
                                        probability=float(getattr(w, "probability", 1.0)))
                        for w in (getattr(s, "words", None) or [])
                    ]
                    avg = getattr(s, "avg_logprob", None)
                    fallback.append(RawSegment(
                        index=len(fallback) + 1,
                        start_time=float(s.start) + core_start,
                        end_time=float(s.end) + core_start,
                        text=text,
                        confidence=round(max(0.0, min(1.0, math.exp(avg))), 2) if avg is not None else 0.9,
                        words=words,
                    ))
        logger.info(f"Foreign speech analysis: {len(chunks)} chunks, {len(spans)} span(s)")
        return spans, fallback

    def _recover_gaps_sync(
        self,
        audio_path: str,
        language: Optional[str],
        spoken: List[RawSegment],
        exclude: List[Tuple[float, float]],
        cancel_flag: threading.Event,
    ) -> List[RawSegment]:
        from app.pipeline import gap_recovery as gr

        audio = load_wav(audio_path)
        duration = len(audio) / SAMPLE_RATE
        gaps = gr.find_gaps(gr.covered_intervals(spoken), duration, exclude)
        gaps = [g for g in gr.split_long(gaps) if gr.level_db(audio, *g) > gr.MIN_LEVEL_DB]
        if not gaps:
            logger.info("Gap recovery: no uncovered audio worth re-listening to")
            return []
        total = sum(e - s for s, e in gaps)
        logger.info(f"Gap recovery: re-listening to {len(gaps)} uncovered stretch(es), {total:.0f}s, without VAD")

        model = self._get_model()
        options = dict(
            language=language,
            clip_timestamps=[t for g in gaps for t in g],
            vad_filter=False,
            word_timestamps=True,
            condition_on_previous_text=False,
            hallucination_silence_threshold=2.0,
        )
        # No initial prompt / hotwords here: on noise Whisper tends to echo them back as "speech"
        segments, _ = model.transcribe(audio, **options)

        context = [{"start": s.start_time, "end": s.end_time, "text": s.text} for s in spoken]
        recovered: List[RawSegment] = []
        rejected = 0
        for seg in segments:
            if cancel_flag.is_set():
                raise PipelineCancelled()
            start, end, text = float(seg.start), float(seg.end), (seg.text or "").strip()
            mid = (start + end) / 2
            if not any(a - 0.3 <= mid <= b + 0.3 for a, b in gaps):
                continue
            words = [w for w in (getattr(seg, "words", None) or []) if w.end > w.start]
            # Judge the span of the words, not the segment: Whisper pads segments with silence
            spoken_span = (float(words[-1].end) - float(words[0].start)) if words else end - start
            reason = gr.rejection_reason(
                text,
                getattr(seg, "avg_logprob", None),
                getattr(seg, "no_speech_prob", None),
                getattr(seg, "compression_ratio", None),
                [float(getattr(w, "probability", 1.0)) for w in words],
                spoken_span,
                gr.neighbours_of(start, end, context + [{"start": r.start_time, "end": r.end_time, "text": r.text} for r in recovered]),
            )
            stats = (f"logprob {getattr(seg, 'avg_logprob', 0):.2f}, no_speech {getattr(seg, 'no_speech_prob', 0):.2f}")
            if reason:
                rejected += 1
                logger.info(f"Gap recovery: - {start:.1f}-{end:.1f}s {text[:70]!r} rejected: {reason} ({stats})")
                continue
            logger.info(f"Gap recovery: + {start:.1f}-{end:.1f}s {text[:70]!r} ({stats})")
            avg = getattr(seg, "avg_logprob", None)
            recovered.append(RawSegment(
                index=0,
                start_time=start,
                end_time=end,
                text=text,
                confidence=round(max(0.0, min(1.0, math.exp(avg))), 2) if avg is not None else 0.8,
                words=[TranscribedWord(word=w.word, start=float(w.start), end=float(w.end),
                                       probability=float(getattr(w, "probability", 1.0))) for w in words],
            ))
        logger.info(f"Gap recovery: kept {len(recovered)} line(s), rejected {rejected}")
        return recovered

    def _snap_starts_sync(self, audio_path: str, spoken: List[RawSegment]) -> SnapStats:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        audio = load_wav(audio_path)
        # Tight settings on purpose: the question is where speech *begins*, so no padding and
        # short pauses count as pauses
        timestamps = get_speech_timestamps(
            audio, VadOptions(threshold=0.5, min_speech_duration_ms=100, min_silence_duration_ms=150, speech_pad_ms=0)
        )
        regions = [(t["start"] / SAMPLE_RATE, t["end"] / SAMPLE_RATE) for t in timestamps]
        stats = snap_word_starts(spoken, regions)
        logger.info(
            f"Word timing: {stats.moved} of {stats.words} word start(s) moved to the speech onset "
            f"(mean {stats.mean_shift:.2f}s, max {stats.max_shift:.2f}s)"
        )
        return stats

    async def snap_starts_to_speech(self, audio_path: str, spoken: List[RawSegment]) -> SnapStats:
        """Correct word starts that Whisper placed inside a pause (see app.pipeline.word_snap)."""
        return await asyncio.to_thread(self._snap_starts_sync, audio_path, spoken)

    async def recover_missed_speech(
        self,
        audio_path: str,
        language: Optional[str],
        spoken: List[RawSegment],
        exclude: Optional[List[Tuple[float, float]]] = None,
        cancel_flag: Optional[threading.Event] = None,
    ) -> List[RawSegment]:
        """Re-listen (without VAD) to audio no subtitle covers; returns the lines worth keeping."""
        return await asyncio.to_thread(
            self._recover_gaps_sync, audio_path, language, spoken, list(exclude or []), cancel_flag or threading.Event()
        )

    async def analyze_foreign_speech(
        self,
        audio_path: str,
        main_language: Optional[str],
        spoken: List[RawSegment],
        cancel_flag: Optional[threading.Event] = None,
    ):
        if not main_language:
            return [], []
        return await asyncio.to_thread(
            self._analyze_foreign_sync, audio_path, main_language, spoken, cancel_flag or threading.Event()
        )
