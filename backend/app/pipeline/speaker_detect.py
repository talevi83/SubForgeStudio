"""Infer who is speaking to whom (gender) for a few selected subtitle rows by showing Gemini
a handful of video frames next to the dialogue. Used to settle he/she "you" in languages
where the 2nd person is gendered (e.g. Hebrew)."""
import subprocess
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

MAX_FRAMES = 3
MAX_SPAN_S = 30.0

SYSTEM_PROMPT = (
    "You look at a few video frames and the subtitle lines spoken around them. Decide the gender of the "
    "person SPEAKING these lines and of the person (or people) being ADDRESSED. Use visible cues: who is "
    "on screen, mouth/gaze direction, over-the-shoulder shots, radio/comms scenes, and the dialogue itself "
    "(names, titles, 'sir/ma'am'). Answer 'unknown' when the evidence is weak - do not guess. "
    "Values: male, female, group (several people) or unknown."
)


class SpeakerGuess(BaseModel):
    speaker_gender: str = Field(default="unknown", description="male | female | group | unknown")
    addressee_gender: str = Field(default="unknown", description="male | female | group | unknown")
    explanation: str = Field(default="", description="One short sentence: what you saw or read that decided it")


def frame_times(start: float, end: float, count: int = MAX_FRAMES) -> List[float]:
    """Evenly spaced timestamps inside the rows' span (capped at MAX_SPAN_S)."""
    start = max(0.0, start)
    span = max(0.5, min(end - start, MAX_SPAN_S))
    if count <= 1:
        return [round(start + span / 2, 2)]
    return [round(start + span * (i + 0.5) / count, 2) for i in range(count)]


def grab_frame(video_path: str, t: float, ffmpeg: str = "ffmpeg") -> Optional[bytes]:
    """One JPEG frame (480px high) at time t, or None if ffmpeg could not produce it."""
    cmd = [ffmpeg, "-v", "error", "-ss", f"{t:.2f}", "-i", video_path, "-frames:v", "1",
           "-vf", "scale=-2:480", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


async def detect_speakers(
    client: Any,
    model: str,
    frames: Sequence[Tuple[float, bytes]],
    lines: Sequence[Tuple[float, str]],
    thinking: str = "default",
    usage: Any = None,
    flex: bool = False,
    flex_state: Any = None,
) -> SpeakerGuess:
    from google.genai import types
    from app.core.model_catalog import default_temperature, thinking_config
    from app.providers import gemini_calls

    contents: List[Any] = ["Subtitle lines (time in seconds):"]
    contents.append("\n".join(f"[{t:.1f}] {text}" for t, text in lines))
    for t, data in frames:
        contents.append(f"Frame at t={t:.1f}s:")
        contents.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
    response = await gemini_calls.generate_content(
        client,
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=SpeakerGuess,
            temperature=default_temperature(model, 0.0),
            thinking_config=thinking_config(model, thinking),
        ),
        flex=flex,
        usage=usage,
        stage="speaker_detect",
        flex_state=flex_state,
    )
    guess = SpeakerGuess.model_validate_json(response.text)
    ok = {"male", "female", "group", "unknown"}
    guess.speaker_gender = guess.speaker_gender if guess.speaker_gender in ok else "unknown"
    guess.addressee_gender = guess.addressee_gender if guess.addressee_gender in ok else "unknown"
    return guess
