# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for "SubForge Studio.exe" (a one-folder build with a tray launcher).

Build it with build_exe.bat at the repo root, which also builds the frontend and fetches FFmpeg.
Manual build (from the repo root, after `npm run build` in frontend/):
    backend\\.venv\\Scripts\\python.exe -m PyInstaller packaging\\subforge.spec --noconfirm
Output: dist\\SubForge Studio\\SubForge Studio.exe
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
BACKEND = ROOT / "backend"
PACKAGING = ROOT / "packaging"
FRONTEND_DIST = ROOT / "frontend" / "dist"

if not (FRONTEND_DIST / "index.html").exists():
    raise SystemExit("frontend/dist is missing - run `npm run build` in frontend/ first (build_exe.bat does this).")

# --- data files -------------------------------------------------------------------------------
datas = [
    (str(FRONTEND_DIST), "frontend/dist"),
    (str(BACKEND / ".env.example"), "backend"),
    (str(PACKAGING / "icon.ico"), "packaging"),
]
ffmpeg_dir = PACKAGING / "ffmpeg"
if (ffmpeg_dir / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")).exists():
    datas.append((str(ffmpeg_dir), "ffmpeg"))
else:
    print("WARNING: packaging/ffmpeg not found - the exe will use ffmpeg/ffprobe from PATH.")

datas += collect_data_files("faster_whisper")   # Silero VAD model (assets/*.onnx)

# NVIDIA cuBLAS / cuDNN runtime for GPU transcription. Copied as plain files (no dependency
# scan); app/core/hardware.py adds <bundle>/nvidia/*/bin to the DLL search path at startup.
if sys.platform == "win32":
    try:
        import nvidia
        for location in nvidia.__path__:
            for bin_dir in Path(location).glob("*/bin"):
                for dll in bin_dir.glob("*.dll"):
                    datas.append((str(dll), f"nvidia/{bin_dir.parent.name}/bin"))
    except ImportError:
        print("WARNING: nvidia-cublas-cu12 / nvidia-cudnn-cu12 not installed - the exe will be CPU-only.")

binaries = collect_dynamic_libs("ctranslate2")

# Desktop window: pywebview on Edge WebView2 (WinForms through pythonnet)
if sys.platform == "win32":
    for package in ("webview", "clr_loader", "pythonnet"):
        try:
            datas += collect_data_files(package)        # webview's JS + WebView2 DLLs, ClrLoader.dll
            binaries += collect_dynamic_libs(package)
        except Exception as e:
            print(f"WARNING: could not collect {package}: {e}")

# --- modules ------------------------------------------------------------------------------------
# Every backend module, listed from the source tree (importing them here would run their setup code)
app_modules = []
for py in sorted((BACKEND / "app").rglob("*.py")):
    parts = list(py.relative_to(BACKEND).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    app_modules.append(".".join(parts))

hiddenimports = (
    app_modules
    + collect_submodules("uvicorn")
    + collect_submodules("websockets")
    + collect_submodules("google.genai", filter=lambda name: ".tests" not in name)
    + ["sqlalchemy.dialects.sqlite", "pystray._win32" if sys.platform == "win32" else "pystray._xorg"]
    + (["webview", "webview.platforms.winforms", "webview.platforms.edgechromium", "webview.platforms.mshtml",
        "clr", "clr_loader", "pythonnet"] if sys.platform == "win32" else ["webview"])
)

a = Analysis(
    [str(PACKAGING / "launcher.py")],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "_pytest", "matplotlib", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SubForge Studio",
    icon=str(PACKAGING / "icon.ico"),
    console=False,          # tray app: no console window (logs go to the data folder)
    upx=False,
    debug=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="SubForge Studio",
    upx=False,
)
