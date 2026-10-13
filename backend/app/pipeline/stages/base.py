from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Optional, Any
from app.pipeline.context import PipelineContext

ProgressCallback = Callable[[float, Optional[float], Optional[float], Optional[str], Optional[Any]], Awaitable[None]]


class PipelineStage(ABC):
    """Abstract base class for all pipeline stages."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stage name matching JobStatus enum or pipeline stage identifier."""
        pass

    @abstractmethod
    async def execute(
        self,
        ctx: PipelineContext,
        on_progress: Optional[ProgressCallback] = None,
    ) -> None:
        """
        Execute the stage logic asynchronously.
        on_progress: (progress_percent, speed_factor, eta_seconds, message, extra_data) -> Awaitable[None]
        """
        pass
