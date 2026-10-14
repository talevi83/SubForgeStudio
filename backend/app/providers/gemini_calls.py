"""
Gemini generate_content with an optional Flex service tier.

Flex (https://ai.google.dev/gemini-api/docs/flex-inference) bills tokens at half the Standard
price, but Google may take minutes to answer and sheds Flex traffic first when it is busy
(429 / 503). Google does not fall back to Standard on its own, so this module does:

  1. send the request with service_tier="flex" and a long client timeout,
  2. on a capacity error, retry with exponential backoff (5s, 10s),
  3. if Flex is still busy (or a request timed out), send it once on the Standard tier so the
     job finishes; the usage tracker counts these fallbacks so the job can warn about them.

  4. after FLEX_FALLBACK_LIMIT "bad" Flex requests in a row for a model - a fallback, or an answer
     slower than FLEX_SLOW_SECONDS - the rest of the job uses Standard for that model (FlexState).
     Translation runs batch after batch, so a few minutes per batch adds up to most of an hour.

Errors that are not about capacity (bad key, unknown model, invalid request) are raised at once
so the callers' own error handling stays unchanged.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

from app.core.model_catalog import is_capacity_error, is_timeout_error

logger = logging.getLogger(__name__)

# Google recommends client timeouts of 10+ minutes for Flex; the SDK takes milliseconds
FLEX_TIMEOUT_MS = 15 * 60 * 1000
FLEX_ATTEMPTS = 3
FLEX_BASE_DELAY = 5.0
FLEX_FALLBACK_LIMIT = 2
FLEX_SLOW_SECONDS = 60.0


@dataclass
class FlexState:
    """Per-job Flex bookkeeping, per model."""

    limit: int = FLEX_FALLBACK_LIMIT
    consecutive: Dict[str, int] = field(default_factory=dict)
    disabled: Set[str] = field(default_factory=set)

    def use_flex(self, model: str) -> bool:
        return model not in self.disabled

    def succeeded(self, model: str, seconds: float = 0.0) -> None:
        if seconds > FLEX_SLOW_SECONDS:
            self._bad(model, f"answered slowly ({seconds:.0f}s)")
        else:
            self.consecutive[model] = 0

    def fell_back(self, model: str) -> None:
        self._bad(model, "was busy")

    def _bad(self, model: str, what: str) -> None:
        self.consecutive[model] = self.consecutive.get(model, 0) + 1
        if self.consecutive[model] >= self.limit and model not in self.disabled:
            self.disabled.add(model)
            logger.warning(f"Flex {what} for {self.consecutive[model]} requests in a row to {model}; "
                           f"using the Standard tier for the rest of this job")


def flex_config(config: Any) -> Any:
    """Copy of a GenerateContentConfig that asks for the Flex tier."""
    from google.genai import types

    return config.model_copy(update={
        "service_tier": types.ServiceTier.FLEX,
        "http_options": types.HttpOptions(timeout=FLEX_TIMEOUT_MS),
    })


async def generate_content(
    client: Any,
    *,
    model: str,
    contents: Any,
    config: Any,
    flex: bool = False,
    usage: Any = None,
    stage: str = "",
    flex_state: Optional[FlexState] = None,
) -> Any:
    """Run client.models.generate_content in a worker thread (the SDK call is blocking) and
    record its token usage. With `flex`, use the Flex tier with retries and a Standard fallback."""

    async def call(cfg: Any) -> Any:
        return await asyncio.to_thread(client.models.generate_content, model=model, contents=contents, config=cfg)

    if flex and flex_state is not None and not flex_state.use_flex(model):
        flex = False  # Flex kept failing earlier in this job
    if flex:
        cfg = flex_config(config)
        last_error: Optional[Exception] = None
        for attempt in range(FLEX_ATTEMPTS):
            began = time.monotonic()
            try:
                response = await call(cfg)
                _record(usage, stage, model, response, flex=True)
                took = time.monotonic() - began
                logger.info(f"Flex request to {model} ({stage}) answered in {took:.0f}s")
                if flex_state is not None:
                    flex_state.succeeded(model, took)
                return response
            except Exception as e:
                timed_out = is_timeout_error(e)
                if not (timed_out or is_capacity_error(e)):
                    raise
                last_error = e
                logger.warning(f"Flex request to {model} failed (attempt {attempt + 1}/{FLEX_ATTEMPTS}): {e}")
                if timed_out:
                    break  # already waited the full Flex timeout; don't wait it out again
                if attempt + 1 < FLEX_ATTEMPTS:
                    await asyncio.sleep(FLEX_BASE_DELAY * (2 ** attempt))
        logger.warning(f"Flex unavailable for {model} ({last_error}); sending this request on the Standard tier")
        response = await call(config)
        _record(usage, stage, model, response, flex=False, fell_back=True)
        if flex_state is not None:
            flex_state.fell_back(model)
        return response

    response = await call(config)
    _record(usage, stage, model, response)
    return response


def _record(usage: Any, stage: str, model: str, response: Any, flex: bool = False, fell_back: bool = False) -> None:
    if usage is not None:
        usage.record(stage, model, getattr(response, "usage_metadata", None), flex=flex, fell_back=fell_back)
