"""
Local frame pre-filtering for on-screen text detection (when `cost_saver_mode == True`).

Before sending sampled frames to Gemini Vision:
1. Drops blank / textless frames by cropping the upper 80% of the frame (ignoring bottom
   burned-in dialogue), resizing to 320x180 grayscale, applying `ImageFilter.FIND_EDGES`,
   and discarding frames with low edge density (< 0.008 high-contrast pixels).
2. Merges consecutive duplicate frames via a 64-bit difference hash (dHash, 9x8 grayscale):
   consecutive frames with Hamming distance <= 6 are grouped into a single `FrameCluster`
   covering `[start_time, end_time]`, and only `representative_path` is sent to Gemini Vision.
"""
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Sequence

from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

EDGE_DENSITY_THRESHOLD = 0.008
EDGE_CONTRAST_PIXEL_MIN = 48
DHASH_MAX_DISTANCE = 6
RESIZE_W, RESIZE_H = 320, 180
UPPER_CROP_RATIO = 0.80


@dataclass
class FrameCluster:
    representative_path: Path
    start_time: float
    end_time: float


def _pixels(img: Image.Image) -> list[int]:
    fn = getattr(img, "get_flattened_data", None)
    return list(fn() if callable(fn) else img.getdata())


def compute_edge_density(image_path: Path) -> float:
    """
    Crop upper 80% of the image, resize to 320x180 grayscale, run FIND_EDGES,
    and return the fraction of pixels with high edge contrast.
    """
    with Image.open(image_path) as img:
        gray = img.convert("L")
        w, h = gray.size
        crop_h = max(1, int(h * UPPER_CROP_RATIO))
        cropped = gray.crop((0, 0, w, crop_h))
        resized = cropped.resize((RESIZE_W, RESIZE_H), Image.Resampling.BILINEAR)
        edges = resized.filter(ImageFilter.FIND_EDGES)
        # Zero out the 1-pixel border artifact produced by FIND_EDGES
        pixels = _pixels(edges)
        total = RESIZE_W * RESIZE_H
        high_contrast = 0
        for y in range(1, RESIZE_H - 1):
            row_offset = y * RESIZE_W
            for x in range(1, RESIZE_W - 1):
                if pixels[row_offset + x] >= EDGE_CONTRAST_PIXEL_MIN:
                    high_contrast += 1
        return high_contrast / max(1, total)


def compute_dhash(image_path: Path) -> int:
    """Compute a 64-bit horizontal difference hash (9x8 grayscale) on the upper 80% of the frame."""
    with Image.open(image_path) as img:
        gray = img.convert("L")
        w, h = gray.size
        crop_h = max(1, int(h * UPPER_CROP_RATIO))
        cropped = gray.crop((0, 0, w, crop_h))
        small = cropped.resize((9, 8), Image.Resampling.BILINEAR)
        pixels = _pixels(small)
        bits = 0
        for row in range(8):
            offset = row * 9
            for col in range(8):
                bits = (bits << 1) | (1 if pixels[offset + col] > pixels[offset + col + 1] else 0)
        return bits


def hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def prefilter_frames_sync(
    frames: Sequence[Any],
    edge_threshold: float = EDGE_DENSITY_THRESHOLD,
    max_hamming: int = DHASH_MAX_DISTANCE,
) -> List[FrameCluster]:
    """
    Synchronous Pillow pass over `frames` (each having `.path`, `.time`, and optional `.static_until`).
    Returns merged `FrameCluster` items for frames that survive the edge density filter.
    """
    surviving: List[tuple[Any, int]] = []
    for f in frames:
        path = Path(getattr(f, "path", f))
        try:
            density = compute_edge_density(path)
            if density < edge_threshold:
                continue
            dhash = compute_dhash(path)
            surviving.append((f, dhash))
        except Exception as e:
            logger.debug(f"Could not pre-filter frame {path}: {e}")
            # Keep unreadable/mock frames so tests with synthetic dummy paths don't silently break
            surviving.append((f, -1))

    clusters: List[FrameCluster] = []
    prev_hash: int | None = None
    for f, dhash in surviving:
        path = Path(getattr(f, "path", f))
        t_start = float(getattr(f, "time", 0.0))
        t_end = max(t_start, float(getattr(f, "static_until", t_start) or t_start))
        if (
            clusters
            and prev_hash is not None
            and prev_hash >= 0
            and dhash >= 0
            and hamming_distance(prev_hash, dhash) <= max_hamming
        ):
            clusters[-1].end_time = max(clusters[-1].end_time, t_end, t_start)
        else:
            clusters.append(
                FrameCluster(
                    representative_path=path,
                    start_time=t_start,
                    end_time=t_end,
                )
            )
            prev_hash = dhash
    return clusters


async def prefilter_frames(
    frames: Sequence[Any],
    edge_threshold: float = EDGE_DENSITY_THRESHOLD,
    max_hamming: int = DHASH_MAX_DISTANCE,
) -> List[FrameCluster]:
    """Run `prefilter_frames_sync` on a worker thread so the event loop is never blocked."""
    return await asyncio.to_thread(prefilter_frames_sync, frames, edge_threshold, max_hamming)
