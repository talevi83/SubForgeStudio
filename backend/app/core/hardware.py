import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional


def setup_cuda_dll_paths() -> None:
    """Preload NVIDIA CUDA/cuBLAS/cuDNN shared libraries on Windows and Linux into process space."""
    if sys.platform not in ("win32", "linux"):
        return

    import ctypes
    if sys.platform == "win32":
        candidates = [Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"]
        if getattr(sys, "frozen", False):  # packaged exe: the DLLs sit in <bundle>/nvidia/*/bin
            candidates.insert(0, Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "nvidia")
        nvidia_dir = next((c for c in candidates if c.exists()), None)
        if nvidia_dir is None:
            return

        bin_dirs = list(nvidia_dir.glob("*/bin"))
        for b in bin_dirs:
            try:
                os.add_dll_directory(str(b))
            except Exception:
                pass

        if bin_dirs:
            os.environ["PATH"] = ";".join(str(b) for b in bin_dirs) + ";" + os.environ.get("PATH", "")

        # Preload cuBLAS and cuDNN DLLs into memory
        for b in bin_dirs:
            for dll_file in b.glob("*.dll"):
                try:
                    ctypes.CDLL(str(dll_file))
                except Exception:
                    pass
    else:
        candidates = list((Path(sys.prefix) / "lib").glob("python*/site-packages/nvidia"))
        nvidia_dir = next((c for c in candidates if c.exists()), None)
        if nvidia_dir is None:
            return
        lib_dirs = list(nvidia_dir.glob("*/lib"))
        if lib_dirs:
            ld_path = ":".join(str(d) for d in lib_dirs)
            existing = os.environ.get("LD_LIBRARY_PATH", "")
            os.environ["LD_LIBRARY_PATH"] = f"{ld_path}:{existing}" if existing else ld_path
        rtld_global = getattr(ctypes, "RTLD_GLOBAL", os.RTLD_GLOBAL if hasattr(os, "RTLD_GLOBAL") else 0)
        for d in lib_dirs:
            for so_file in sorted(d.glob("lib*.so*")):
                try:
                    ctypes.CDLL(str(so_file), mode=rtld_global)
                except Exception:
                    pass


# Execute DLL setup immediately
setup_cuda_dll_paths()

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
