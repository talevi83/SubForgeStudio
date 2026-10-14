"""
Gemini model catalog: prices, thinking options and image token costs.

Prices are the official Gemini API *Standard (paid tier)* rates in USD per 1M tokens as published
on https://ai.google.dev/gemini-api/docs/pricing (checked October 2026). Output prices include
thinking tokens, which are billed as output. Some models have promotional prices until a date;
the price that applies on the day of the call is used.

Thinking:
  - Gemini 2.5 models are configured with `thinking_budget` (tokens; 0 disables on Flash/Flash-Lite).
  - Newer models use `thinking_level` (minimal / low / medium / high, model dependent).
  "default" sends nothing and lets the model decide.

Availability was verified against a real API key (ListModels + a test request per model) in
October 2026. Left out on purpose:
  - Gemini 2.5 (Pro/Flash/Flash-Lite): Google returns 404 "no longer available to new users".
  - gemini-3.5-flash / gemini-3.7-flash: deprecated, silently routed to 3.6 / 3.8 Flash.
  - gemini-3-flash-preview, gemini-3.1-flash-lite-preview: legacy / shut down.
  - Omni, Live, TTS, image and transcribe models: not usable with generateContent text+JSON calls.
  - gemini-3.8-flash-lite: does not exist (only gemini-3.8-flash-lite-tts, a speech model).
RETIRED_MODELS maps those IDs to a replacement so old saved settings keep working.

Flex tier (https://ai.google.dev/gemini-api/docs/flex-inference): `service_tier="flex"` bills every
token at 50% of the Standard price. Requests may take minutes and can be shed (429/503) when Google
is busy; see app/providers/gemini_calls.py for the retry + Standard fallback.

Each model lists the pipeline tasks it is suitable for:
  translation - subtitle translation (needs strong multilingual quality + structured JSON)
  glossary    - one-shot names/terms extraction from the transcript (structured JSON)
  vision      - reading on-screen text from video frames (image input, many cheap requests)

Update this file when Google changes prices or ships new models; unknown models still work but
their cost is reported as "unknown".
"""
from dataclasses import dataclass, field
from datetime import date
from math import ceil
from typing import Any, Dict, List, Optional, Tuple

THINKING_CHOICES = ("default", "off", "minimal", "low", "medium", "high")
TASKS = ("translation", "glossary", "vision")


def is_unavailable_error(exc: Exception) -> bool:
    """Errors that retrying can't fix: bad key, no access to the model, unknown model."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    msg = str(exc).lower()
    if code in (401, 403, 404):
        return True
    return any(k in msg for k in (
        "api key not valid", "api_key_invalid", "not found", "not available", "no longer available",
        "permission denied", "does not have access", "not supported for", "is not supported",
    ))

def is_capacity_error(exc: Exception) -> bool:
    """Temporary "Google is busy" errors (Flex requests are shed first under load)."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    msg = str(exc).lower()
    if code in (429, 500, 503, 504):
        return True
    return any(k in msg for k in (
        "resource_exhausted", "unavailable", "overloaded", "at capacity", "deadline_exceeded",
    ))


def is_timeout_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name or "timed out" in str(exc).lower()


# Flex requests are billed at this share of the Standard price (all token types)
FLEX_PRICE_FACTOR = 0.5

# Requests above this prompt size use the "long context" price on models that have one
LONG_CONTEXT_THRESHOLD = 200_000


@dataclass(frozen=True)
class PricePeriod:
    input: float                      # USD per 1M input tokens (text/image/video)
    output: float                     # USD per 1M output tokens, thinking included
    until: Optional[date] = None      # last day this price applies (None = open ended)
    input_long: Optional[float] = None   # > 200k-token prompts
    output_long: Optional[float] = None


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    family: str                       # "2.5" (thinking_budget) or "3" (thinking_level)
    prices: Tuple[PricePeriod, ...]
    thinking: Tuple[str, ...]         # selectable levels besides "default"
    default_level: str                # what "default" behaves like (for estimates)
    tasks: Tuple[str, ...] = TASKS     # pipeline tasks this model is suitable for
    flex: bool = True                 # offered on the Flex service tier (half price, slower)
    preview: bool = False
    note: str = ""

    def price_on(self, day: Optional[date] = None) -> PricePeriod:
        day = day or date.today()
        for period in self.prices:
            if period.until is None or day <= period.until:
                return period
        return self.prices[-1]

    def next_price_change(self, day: Optional[date] = None) -> Optional[Tuple[date, PricePeriod]]:
        day = day or date.today()
        for i, period in enumerate(self.prices):
            if period.until is not None and day <= period.until and i + 1 < len(self.prices):
                return period.until, self.prices[i + 1]
        return None


_P = PricePeriod
_TR, _GL, _VI = "translation", "glossary", "vision"
CATALOG: Dict[str, ModelInfo] = {m.id: m for m in [
    ModelInfo("gemini-3.8-flash", "Gemini 3.8 Flash", "3",
              (_P(0.75, 3.75, until=date(2026, 12, 31)), _P(1.50, 7.50)),
              ("low", "medium", "high"), "medium", tasks=(_TR, _GL, _VI)),
    ModelInfo("gemini-3.6-flash", "Gemini 3.6 Flash", "3",
              (_P(0.75, 3.75, until=date(2026, 12, 31)), _P(1.50, 7.50)),
              ("minimal", "low", "medium", "high"), "medium", tasks=(_TR, _GL, _VI),
              note="Previous-generation Flash; same price as 3.8 Flash."),
    ModelInfo("gemini-4-argon", "Gemini 4 Argon", "3",
              (_P(2.00, 10.00, until=date(2026, 11, 30)), _P(4.00, 20.00)),
              ("low", "medium", "high"), "medium", tasks=(_TR,),
              note="Top quality, highest cost. Not available on the free tier."),
    ModelInfo("gemini-3.1-pro-preview", "Gemini 3.1 Pro (preview)", "3",
              (_P(2.00, 12.00, input_long=4.00, output_long=18.00),),
              ("low", "medium", "high"), "high", tasks=(_TR,), preview=True),
    ModelInfo("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", "3",
              (_P(0.30, 2.50),), ("minimal", "low", "medium", "high"), "minimal", tasks=(_GL, _VI)),
    ModelInfo("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", "3",
              (_P(0.25, 1.50),), (), "minimal", tasks=(_GL, _VI),
              note="Cheapest option. Thinking levels are not documented for this model; it runs with its default."),
]}

# Models this app used to offer (or that Google retired) -> what to use instead
RETIRED_MODELS: Dict[str, str] = {
    "gemini-2.5-pro": "gemini-3.1-pro-preview",
    "gemini-2.5-flash": "gemini-3.8-flash",
    "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-3.5-flash": "gemini-3.8-flash",
    "gemini-3.7-flash": "gemini-3.8-flash",
    "gemini-3-flash-preview": "gemini-3.8-flash",
    "gemini-3.1-flash-lite-preview": "gemini-3.1-flash-lite",
}

# Preferred model per task when the saved/configured one can't be used
TASK_DEFAULTS: Dict[str, str] = {
    "translation": "gemini-3.8-flash",
    "glossary": "gemini-3.8-flash",
    "vision": "gemini-3.5-flash-lite",
}


def models_for(task: str) -> List[str]:
    return [m.id for m in CATALOG.values() if task in m.tasks]


def resolve_model(model_id: str, task: str) -> str:
    """Map a saved/configured model to one that is available and suitable for `task`.
    Unknown (custom) model IDs are kept as-is so power users can still try them."""
    mid = (model_id or "").replace("models/", "").strip()
    mid = RETIRED_MODELS.get(mid, mid)
    info = CATALOG.get(mid)
    if not mid or (info is None and mid.startswith("gemini-2.")):
        return TASK_DEFAULTS[task]
    if info is not None and task not in info.tasks:
        return TASK_DEFAULTS[task]
    return mid


# thinking_budget values used for Gemini 2.5 models
_BUDGETS = {"off": 0, "low": 1024, "medium": 8192, "high": 24576}
_PRO_BUDGETS = {"low": 1024, "medium": 8192, "high": 32768}

# Rough thinking tokens per request, used ONLY for cost estimates (real runs report exact counts)
_ESTIMATED_THINKING = {"off": 0, "minimal": 80, "low": 600, "medium": 1800, "high": 5000}

# Tokens per sampled video frame (640px wide JPEG)
IMAGE_TOKENS_2_5 = 258  # one tile
IMAGE_TOKENS_BY_RESOLUTION = {"low": 280, "medium": 560, "high": 1120}


def get_model(model_id: str) -> Optional[ModelInfo]:
    return CATALOG.get((model_id or "").replace("models/", ""))


def family_of(model_id: str) -> str:
    info = get_model(model_id)
    if info:
        return info.family
    return "2.5" if (model_id or "").replace("models/", "").startswith("gemini-2.") else "3"


def supports_flex(model_id: str) -> bool:
    """Catalog models say so explicitly; unknown (custom) models are allowed to try."""
    info = get_model(model_id)
    return info.flex if info else True


def validate_flex(model_id: str, flex: bool) -> bool:
    if flex and not supports_flex(model_id):
        raise ValueError(f"{model_id} is not available on the Flex tier")
    return flex


def allowed_thinking(model_id: str) -> Tuple[str, ...]:
    info = get_model(model_id)
    return ("default",) + (info.thinking if info else ())


def validate_thinking(model_id: str, level: str) -> str:
    if level not in allowed_thinking(model_id):
        raise ValueError(f"Thinking level '{level}' is not supported by {model_id}. "
                         f"Allowed: {', '.join(allowed_thinking(model_id))}")
    return level


def thinking_config(model_id: str, level: str):
    """google.genai ThinkingConfig for a model + level, or None for the model's default."""
    if not level or level == "default" or level not in allowed_thinking(model_id):
        return None
    from google.genai import types

    if family_of(model_id) == "2.5":
        budgets = _PRO_BUDGETS if "pro" in model_id else _BUDGETS
        if level not in budgets:
            return None
        return types.ThinkingConfig(thinking_budget=budgets[level])
    return types.ThinkingConfig(thinking_level=types.ThinkingLevel(level.upper()))


def default_temperature(model_id: str, wanted: float) -> Optional[float]:
    """Gemini 3+ models are tuned for their default temperature (1.0); Google advises not
    lowering it (it can cause loops), so only 2.5 models get an explicit temperature."""
    return wanted if family_of(model_id) == "2.5" else None


def media_resolution(model_id: str, resolution: str):
    """MediaResolution for vision calls on models that support it (3+), else None."""
    if family_of(model_id) == "2.5":
        return None
    from google.genai import types

    return {
        "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
        "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
        "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
    }.get(resolution)


def image_tokens(model_id: str, resolution: str = "medium") -> int:
    if family_of(model_id) == "2.5":
        return IMAGE_TOKENS_2_5
    return IMAGE_TOKENS_BY_RESOLUTION.get(resolution, IMAGE_TOKENS_BY_RESOLUTION["high"])


def request_cost(model_id: str, input_tokens: int, output_tokens: int, day: Optional[date] = None,
                 flex: bool = False) -> Optional[float]:
    """USD for one request (output_tokens must include thinking tokens). None if unpriced."""
    info = get_model(model_id)
    if info is None:
        return None
    p = info.price_on(day)
    long = input_tokens > LONG_CONTEXT_THRESHOLD
    rate_in = p.input_long if (long and p.input_long) else p.input
    rate_out = p.output_long if (long and p.output_long) else p.output
    cost = input_tokens / 1e6 * rate_in + output_tokens / 1e6 * rate_out
    return cost * FLEX_PRICE_FACTOR if flex else cost


def estimated_thinking_tokens(model_id: str, level: str) -> int:
    info = get_model(model_id)
    if level in (None, "", "default"):
        level = info.default_level if info else "medium"
    return _ESTIMATED_THINKING.get(level, _ESTIMATED_THINKING["medium"])


def catalog_payload(day: Optional[date] = None) -> List[Dict[str, Any]]:
    out = []
    for info in CATALOG.values():
        p = info.price_on(day)
        change = info.next_price_change(day)
        out.append({
            "id": info.id,
            "label": info.label,
            "family": info.family,
            "preview": info.preview,
            "thinking": list(allowed_thinking(info.id)),
            "default_level": info.default_level,
            "input_per_m": p.input,
            "output_per_m": p.output,
            "input_long_per_m": p.input_long,
            "output_long_per_m": p.output_long,
            "price_change": (
                {"after": change[0].isoformat(), "input_per_m": change[1].input, "output_per_m": change[1].output}
                if change else None
            ),
            "supports_media_resolution": info.family != "2.5",
            "tasks": list(info.tasks),
            "supports_flex": info.flex,
            "flex_price_factor": FLEX_PRICE_FACTOR if info.flex else None,
            "note": info.note,
        })
    return out


# ---------------------------------------------------------------------------
# Usage tracking (exact numbers from the API's usage_metadata)
# ---------------------------------------------------------------------------

@dataclass
class StageUsage:
    model: str = ""
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0      # visible answer tokens
    thinking_tokens: int = 0
    cost_usd: Optional[float] = 0.0
    flex_requests: int = 0      # requests billed on the Flex tier
    flex_fallbacks: int = 0     # Flex was busy, so the request ran on Standard (full price)

    def add(self, other: "StageUsage") -> None:
        self.model = other.model or self.model
        self.requests += other.requests
        self.flex_requests += other.flex_requests
        self.flex_fallbacks += other.flex_fallbacks
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.thinking_tokens += other.thinking_tokens
        if self.cost_usd is None or other.cost_usd is None:
            self.cost_usd = None
        else:
            self.cost_usd += other.cost_usd


@dataclass
class UsageTracker:
    stages: Dict[str, StageUsage] = field(default_factory=dict)

    def record(self, stage: str, model: str, usage_metadata: Any, flex: bool = False,
               fell_back: bool = False) -> None:
        """Add one API response's usage. Safe to call with a missing usage_metadata.

        `flex` says the request was sent on the Flex tier; the response's traffic_type (when the API
        reports it) has the final word on which price applies."""
        if usage_metadata is None:
            return
        prompt = int(getattr(usage_metadata, "prompt_token_count", 0) or 0)
        output = int(getattr(usage_metadata, "candidates_token_count", 0) or 0)
        thoughts = int(getattr(usage_metadata, "thoughts_token_count", 0) or 0)
        traffic = str(getattr(getattr(usage_metadata, "traffic_type", None), "value",
                              getattr(usage_metadata, "traffic_type", None)) or "").upper()
        if traffic and "UNSPECIFIED" not in traffic:
            flex = traffic == "ON_DEMAND_FLEX"
        cost = request_cost(model, prompt, output + thoughts, flex=flex)
        self.stages.setdefault(stage, StageUsage()).add(
            StageUsage(model=model, requests=1, input_tokens=prompt, output_tokens=output,
                       thinking_tokens=thoughts, cost_usd=cost, flex_requests=int(flex),
                       flex_fallbacks=int(fell_back))
        )

    def flex_fallbacks(self) -> int:
        return sum(s.flex_fallbacks for s in self.stages.values())

    def merge_dict(self, data: Optional[Dict[str, Any]]) -> None:
        for stage, values in ((data or {}).get("stages") or {}).items():
            self.stages.setdefault(stage, StageUsage()).add(StageUsage(**values))

    def to_dict(self) -> Dict[str, Any]:
        costs = [s.cost_usd for s in self.stages.values()]
        total = None if any(c is None for c in costs) else round(sum(costs), 6)
        return {
            "stages": {k: vars(v) for k, v in self.stages.items()},
            "total_cost_usd": total,
        }


# ---------------------------------------------------------------------------
# Cost estimates
# ---------------------------------------------------------------------------

# From real episodes: ~14 subtitles per minute of dialogue-heavy TV
SEGMENTS_PER_MINUTE = 14
_TRANSLATION_TOKENS_PER_SEGMENT_IN = 36   # JSON item incl. original text
_TRANSLATION_TOKENS_PER_SEGMENT_OUT = 32  # JSON item incl. translated text
_GLOSSARY_TOKENS_PER_SEGMENT = 14
_GLOSSARY_OUTPUT = 1500
_VISION_PROMPT = 450
_VISION_OUTPUT = 150
_FRAMES_SENT_RATIO = 0.5  # share of sampled frames that survive the speech-gap / scene filter
_FOREIGN_SPEECH_SHARE = 0.10  # assumed share of the runtime spoken in another language
_FOREIGN_FRAMES_PER_SECOND = 2   # subtitle crops are read every 0.5s
_FOREIGN_CROP_TOKENS_2_5 = 258   # a 768px-wide crop is a single tile


def _stage_estimate(model: str, thinking: str, requests: int, input_per: int, output_per: int,
                    flex: bool = False) -> Dict[str, Any]:
    think = estimated_thinking_tokens(model, thinking)
    input_tokens = requests * input_per
    output_tokens = requests * (output_per + think)
    per_request = request_cost(model, input_per, output_per + think, flex=flex)
    return {
        "model": model,
        "thinking": thinking,
        "flex": flex,
        "requests": requests,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thinking_tokens": requests * think,
        "cost_usd": None if per_request is None else round(per_request * requests, 4),
    }


def estimate_job_cost(
    duration_seconds: float,
    enable_translation: bool,
    detect_onscreen_text: bool,
    app_settings: Any,
    glossary_pass: bool = True,
    translation_batch_size: int = 30,
    system_prompt_tokens: int = 1900,
    frame_interval: float = 2.0,
    max_frames: int = 360,
    vision_batch_size: int = 12,
    has_forced_track: bool = False,
) -> Dict[str, Any]:
    minutes = max(0.0, duration_seconds) / 60
    segments = max(1, round(minutes * SEGMENTS_PER_MINUTE))
    stages: Dict[str, Any] = {}

    if enable_translation:
        if glossary_pass:
            g = app_settings.glossary
            stages["glossary"] = _stage_estimate(
                g.model, g.thinking, 1, 350 + segments * _GLOSSARY_TOKENS_PER_SEGMENT, _GLOSSARY_OUTPUT,
                flex=getattr(g, "flex", False))
        t = app_settings.translation
        batches = ceil(segments / translation_batch_size)
        per_batch_in = (system_prompt_tokens + (700 if glossary_pass else 0)
                        + translation_batch_size * _TRANSLATION_TOKENS_PER_SEGMENT_IN + 8 * 40)
        stages["translation"] = _stage_estimate(
            t.model, t.thinking, batches, per_batch_in, translation_batch_size * _TRANSLATION_TOKENS_PER_SEGMENT_OUT,
            flex=getattr(t, "flex", False))

    if detect_onscreen_text and not has_forced_track:
        v = app_settings.vision
        frames = min(max_frames, int(duration_seconds / frame_interval * _FRAMES_SENT_RATIO))
        batches = ceil(frames / vision_batch_size) if frames else 0
        img = image_tokens(v.model, app_settings.vision_media_resolution)
        stages["onscreen"] = _stage_estimate(
            v.model, v.thinking, batches, vision_batch_size * img + _VISION_PROMPT, _VISION_OUTPUT,
            flex=getattr(v, "flex", False))
        stages["onscreen"]["frames"] = frames

    if detect_onscreen_text and not has_forced_track:
        # Burned-in subtitles for foreign-language speech (cropped frames, every 0.5s)
        v = app_settings.vision
        crops = int(duration_seconds * _FOREIGN_SPEECH_SHARE * _FOREIGN_FRAMES_PER_SECOND)
        if crops:
            img = _FOREIGN_CROP_TOKENS_2_5 if family_of(v.model) == "2.5" else image_tokens(v.model, app_settings.vision_media_resolution)
            stages["foreign"] = _stage_estimate(
                v.model, v.thinking, ceil(crops / vision_batch_size), vision_batch_size * img + _VISION_PROMPT, _VISION_OUTPUT,
                flex=getattr(v, "flex", False))
            stages["foreign"]["frames"] = crops

    costs = [s["cost_usd"] for s in stages.values()]
    return {
        "duration_seconds": duration_seconds,
        "segments": segments,
        "stages": stages,
        "total_cost_usd": None if any(c is None for c in costs) else round(sum(costs), 4),
    }
