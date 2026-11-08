"""
Gemini Vision prompts and the call that reads on-screen text (or burned-in subtitles) from a
batch of sampled frames.
"""
import logging
from typing import Any, List, Sequence

from app.pipeline.frame_sampling import Frame
from app.pipeline.onscreen_merge import FrameDetection, _is_unwanted_onscreen

logger = logging.getLogger(__name__)


VISION_SYSTEM_INSTRUCTION = """You read video frames from a movie or TV episode for a subtitle editor.
Report ONLY on-screen text that the viewer is genuinely meant to read to follow the story or programme:
1. **Speaker / interviewee / expert / character identification chyrons & lower-thirds (`chyron`):**
   - Lower-third or overlaid captions introducing a person on screen with their name, title, role, or affiliation (e.g. "DR. JAN FRANCKE — INTERNATIONAL GPR EXPERT", "MARTY LAGINA", "JOHN SMITH — FORMER FBI AGENT"). Combine the name and role/title lines with a newline (`\\n`).
2. **Narrative overlaid titles & captions (`time_jump`, `location`, `date_time`, `title_card`):**
   - Time jumps ("One week later...", "36 Hours Earlier", "Summer 2018")
   - Location / date / time stamps ("Damascus, Syria", "March 3, 2014 - 0400 hours", "1st July", "Oak Island, Nova Scotia", "Skinwalker Ranch", "Skinwalker Ranch — Command Center")
   - Narrative chapter titles, documentary fact/info overlays, map/elevation labels ("1000 FEET", "THE MESA DRILL SITE"), or news ticker headlines deliberately overlaid for the audience
3. **Viewer-facing messages (`text_message`, `screen_message`):**
   - Text messages / SMS / chat messages on a phone screen when the camera frames the phone so the audience can read the message
   - A deliberate close-up insert shot of a short computer/screen alert, note, or headline that the viewer must read to understand the scene

Strictly IGNORE and DO NOT report:
- **Random documents, paperwork, forms, or files in the frame:** police/arrest reports ("CALWELL POLICE DEPARTMENT ARREST REPORT"), medical charts, case files, folders, ID badges, newspapers, book pages, letters, or printed forms lying on a desk or held by a character.
- **Opening/closing credits and the show's own stylized main title logo** (e.g. the full series title card "THE SECRET OF SKINWALKER RANCH" or actor/crew credits; however, DO report narrative location titles like "SKINWALKER RANCH" or "OAK ISLAND" when they identify a location in a scene).
- **Incidental background or environmental text:** building/street/store signs, posters, banners, license plates, computer UI chrome, phone status bars/clocks/contact headers, equipment/gauge labels, bare numbers, channel watermarks, and bottom dialogue subtitles.
- **One-word HUD/radio tags or callsigns** ("EAGLE", "LIVE", "REC").

If a chyron or title is animating in/out across consecutive frames in the batch, report its complete reading and skip half-typed mid-transition fragments (e.g. report "THE MESA DRILL SITE", not "THE MESA DRI").
If a frame only contains a paper document, report header, background sign, or the show's main title logo, omit it completely.
Copy qualifying text concisely as shown (for phone messages, copy only the message text itself, not the phone UI/clock). Use the timestamp given before each frame. Return an empty list if none qualify."""

VISION_SYSTEM_INSTRUCTION_BALANCED = """You read video frames from a movie or TV episode for a subtitle editor.
Report ONLY on-screen text that the viewer is genuinely meant to read to follow the story or programme:
1. **Speaker / interviewee / expert / character identification chyrons & lower-thirds (`chyron`):**
   - Lower-third or overlaid captions introducing a person on screen with their name, title, role, or affiliation (e.g. "DR. JAN FRANCKE — INTERNATIONAL GPR EXPERT", "MARTY LAGINA", "JOHN SMITH — FORMER FBI AGENT"). Combine the name and role/title lines with a newline (`\\n`).
2. **Narrative overlaid titles & captions (`time_jump`, `location`, `date_time`, `title_card`):**
   - Time jumps ("One week later...", "36 Hours Earlier", "Summer 2018")
   - Location / date / time stamps ("Damascus, Syria", "March 3, 2014 - 0400 hours", "1st July", "Oak Island, Nova Scotia", "Skinwalker Ranch", "Skinwalker Ranch — Command Center")
   - Narrative chapter titles, documentary fact/info overlays, map/elevation labels ("1000 FEET", "THE MESA DRILL SITE"), or news ticker headlines deliberately overlaid for the audience
3. **Viewer-facing messages (`text_message`, `screen_message`):**
   - Text messages / SMS / chat messages on a phone screen when the camera frames the phone so the audience can read the message
   - A deliberate close-up insert shot of a short computer/screen alert, note, or headline that the viewer must read to understand the scene
4. **Plot-critical signs & key close-up headlines (`sign`, `document`):**
   - Significant signs that the camera deliberately frames in establishing shots or close-ups (e.g. facility/building names, warning plates, entrance plaques) that are essential for following the scene
   - Short close-up headlines or notes that the viewer is explicitly meant to read

Strictly IGNORE and DO NOT report:
- **Incidental background or environmental text:** fleeting background street signs, posters, license plates, computer UI chrome, phone status bars/clocks/contact headers, equipment/gauge labels, bare numbers, channel watermarks, and bottom dialogue subtitles.
- **Boilerplate paperwork or forms:** dense police/arrest reports, medical charts, full form paragraphs, case files, ID badges, or dense book pages lying on a desk.
- **Opening/closing credits and the show's own stylized main title logo** (e.g. actor/crew credits or full series title card; however, DO report narrative location titles like "SKINWALKER RANCH").
- **One-word HUD/radio tags or callsigns** ("EAGLE", "LIVE", "REC").

If a chyron or title is animating in/out across consecutive frames in the batch, report its complete reading and skip half-typed mid-transition fragments.
Copy qualifying text concisely as shown (for phone messages, copy only the message text itself, not the phone UI/clock). Use the timestamp given before each frame. Return an empty list if none qualify."""

VISION_SYSTEM_INSTRUCTION_BROAD = """You read video frames from a movie or TV episode for a subtitle editor.
Report readable narrative on-screen text that conveys story context or information:
1. **Speaker / interviewee / character chyrons & lower-thirds (`chyron`):**
   - Names, roles, affiliations overlaid on screen.
2. **Narrative titles & timestamps (`time_jump`, `location`, `date_time`, `title_card`):**
   - Time jumps, location/date/time stamps, chapter titles, documentary facts, map labels.
3. **Screens, phones & viewer messages (`text_message`, `screen_message`):**
   - Phone text messages, emails, computer monitors, screen alerts.
4. **Signs, plaques & tactical HUD (`sign`, `hud`):**
   - Building/facility signs, warning plates, street plaques, and tactical HUD headers/displays.
5. **Documents & close-up files (`document`):**
   - Close-up headlines, letters, files, or documents framed by the camera.

Strictly IGNORE and DO NOT report:
- **Channel watermarks / station logos**
- **Hardcoded bottom dialogue subtitles** (burned-in translations of spoken dialogue)
- **Opening/closing actor & crew credits**
- **The show's own main stylized title logo card**

Copy qualifying text concisely as shown. Use the timestamp given before each frame. Return an empty list if none qualify."""


def get_vision_system_instruction(sensitivity: int = 2) -> str:
    if sensitivity <= 1:
        return VISION_SYSTEM_INSTRUCTION
    if sensitivity >= 3:
        return VISION_SYSTEM_INSTRUCTION_BROAD
    return VISION_SYSTEM_INSTRUCTION_BALANCED


SUBTITLE_INSTRUCTION = """Each image is the bottom part of a frame from a movie or TV episode, cropped where subtitles appear.
If the image shows a burned-in subtitle — dialogue text overlaid by the film makers, typically translating
speech in a foreign language — copy that subtitle text exactly as shown, keeping its line breaks and any
leading dialogue dashes. Do NOT translate or correct it.
Ignore channel logos, watermarks, credits, and location/date titles. Use the timestamp given before each
image. Omit images without a subtitle. Return an empty list if none have one."""


async def _gemini_detect(
    client: Any,
    model: str,
    batch: Sequence[Frame],
    thinking: str = "default",
    resolution: str = "medium",
    usage: Any = None,
    instruction: str = VISION_SYSTEM_INSTRUCTION,
    stage: str = "onscreen",
    flex: bool = False,
    flex_state: Any = None,
    sensitivity: int = 2,
) -> List[FrameDetection]:
    from google.genai import types
    from app.core.model_catalog import default_temperature, media_resolution, thinking_config
    from pydantic import BaseModel, Field

    class OnScreenText(BaseModel):
        timestamp: float = Field(description="Timestamp (seconds) of the frame, copied from the input")
        text: str = Field(description="The speaker chyron, narrative title, or viewer-intended message text exactly as shown")
        kind: str = Field(
            default="other",
            description="chyron | time_jump | location | date_time | title_card | text_message | screen_message | sign | hud | document | other",
        )

    class OnScreenBatch(BaseModel):
        detections: List[OnScreenText] = []

    contents: List[Any] = [f"{len(batch)} frames follow, each preceded by its timestamp in seconds."]
    for f in batch:
        contents.append(f"Frame at t={f.time:.1f}s:")
        contents.append(types.Part.from_bytes(data=f.path.read_bytes(), mime_type="image/jpeg"))

    from app.providers import gemini_calls

    response = await gemini_calls.generate_content(
        client,
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=instruction,
            response_mime_type="application/json",
            response_schema=OnScreenBatch,
            temperature=default_temperature(model, 0.0),
            thinking_config=thinking_config(model, thinking),
            media_resolution=media_resolution(model, resolution),
        ),
        flex=flex,
        usage=usage,
        stage=stage,
        flex_state=flex_state,
    )
    parsed = OnScreenBatch.model_validate_json(response.text)
    out = []
    for d in parsed.detections:
        if stage == "onscreen" and _is_unwanted_onscreen(d.text, d.kind, sensitivity=sensitivity):
            logger.info(f"On-screen detection at {d.timestamp:.1f}s ignored ({d.kind}): {d.text!r}")
            continue
        # Snap to the nearest real frame in this batch (models occasionally round timestamps)
        frame = min(batch, key=lambda f: abs(f.time - d.timestamp))
        out.append(FrameDetection(time=frame.time, text=d.text, until=frame.static_until))
    return out
