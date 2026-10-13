import os
import shutil
import subprocess
from typing import Dict, Any, Optional

try:
    import ctranslate2
except ImportError:
    ctranslate2 = None


def get_hardware_info() -> Dict[str, Any]:
    """
    Detect available hardware acceleration (CUDA/GPU), VRAM, and CPU details.
    Provides recommended Whisper device and compute type.
    """
    cuda_detected = False
    device_count = 0

    if ctranslate2 is not None:
        try:
            device_count = ctranslate2.get_cuda_device_count()
            cuda_detected = device_count > 0
        except Exception:
            cuda_detected = False

    gpu_name: Optional[str] = None
    vram_total_mb: Optional[int] = None
    vram_free_mb: Optional[int] = None

    if shutil.which("nvidia-smi"):
        try:
            res = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                line = res.stdout.strip().split("\n")[0]
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    gpu_name = parts[0]
                    vram_total_mb = int(parts[1])
                    vram_free_mb = int(parts[2])
                    cuda_detected = True
        except Exception:
            pass

    cpu_count = os.cpu_count() or 1
    recommended_device = "cuda" if cuda_detected else "cpu"
    recommended_compute = "float16" if cuda_detected else "int8"

    return {
        "cuda_available": cuda_detected,
        "device_count": device_count,
        "gpu_name": gpu_name,
        "vram_total_mb": vram_total_mb,
        "vram_free_mb": vram_free_mb,
        "cpu_count": cpu_count,
        "recommended_device": recommended_device,
        "recommended_compute_type": recommended_compute,
    }
