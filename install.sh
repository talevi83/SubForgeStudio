#!/usr/bin/env bash
set -euo pipefail

echo "==================================================="
echo "           SubForge Studio - Installer"
echo "==================================================="
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

OS="$(uname -s)"
echo "[*] Detected OS: $OS ($(uname -m))"

# -----------------------------------------------------------------------------
# 1. System Prerequisites (Python 3.11+, Node.js 20+, FFmpeg)
# -----------------------------------------------------------------------------
echo
echo "[1/4] Checking system prerequisites..."

find_python() {
    for cmd in python3.12 python3.13 python3.11 python3; do
        if command -v "$cmd" >/dev/null 2>&1; then
            if "$cmd" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

install_system_packages() {
    if [[ "$OS" == "Darwin" ]]; then
        if ! command -v brew >/dev/null 2>&1; then
            echo "[!] Homebrew is not installed. Installing Homebrew or install python@3.12, node, and ffmpeg manually:"
            echo "    https://brew.sh"
            return 1
        fi
        echo "    Installing missing tools via Homebrew..."
        brew install python@3.12 node ffmpeg
    elif [[ "$OS" == "Linux" ]]; then
        if command -v apt-get >/dev/null 2>&1; then
            echo "    Installing missing tools via apt..."
            sudo apt-get update
            sudo apt-get install -y python3 python3-venv python3-pip ffmpeg nodejs npm curl
        elif command -v dnf >/dev/null 2>&1; then
            echo "    Installing missing tools via dnf..."
            sudo dnf install -y python3 python3-pip ffmpeg nodejs npm curl
        elif command -v pacman >/dev/null 2>&1; then
            echo "    Installing missing tools via pacman..."
            sudo pacman -Sy --needed --noconfirm python python-pip ffmpeg nodejs npm curl
        elif command -v zypper >/dev/null 2>&1; then
            echo "    Installing missing tools via zypper..."
            sudo zypper install -y python3 python3-pip ffmpeg nodejs npm curl
        else
            echo "[!] Unsupported package manager. Please install Python 3.11+, Node.js 20+, and FFmpeg manually."
            return 1
        fi
    fi
}

NEED_SYS_INSTALL=0
PYTHON_CMD="$(find_python || true)"
if [[ -z "$PYTHON_CMD" ]] && ! command -v uv >/dev/null 2>&1; then
    echo "    - Python 3.11+ not found."
    NEED_SYS_INSTALL=1
fi
if ! command -v npm >/dev/null 2>&1; then
    echo "    - Node.js / npm not found."
    NEED_SYS_INSTALL=1
fi
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    echo "    - FFmpeg / FFprobe not found."
    NEED_SYS_INSTALL=1
fi

if [[ "$NEED_SYS_INSTALL" -eq 1 ]]; then
    if [[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]]; then
        REPLY="y"
    else
        read -r -p "    Attempt to install missing system packages automatically? [Y/n] " REPLY || REPLY="n"
    fi
    if [[ -z "$REPLY" || "$REPLY" =~ ^[Yy]$ ]]; then
        install_system_packages || true
    fi
fi

PYTHON_CMD="$(find_python || true)"
if [[ -z "$PYTHON_CMD" ]] && ! command -v uv >/dev/null 2>&1; then
    echo "[ERROR] Python 3.11+ (or uv) is required. Please install Python 3.11+ and re-run ./install.sh"
    exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
    echo "[ERROR] Node.js (v20.19+ or v22.12+) and npm are required. Please install Node.js and re-run ./install.sh"
    exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    echo "[WARNING] ffmpeg / ffprobe not found on PATH. SubForge Studio needs FFmpeg to process media files."
else
    echo "    - FFmpeg:  $(command -v ffmpeg)"
fi
echo "    - Node.js: $(node -v) (npm $(npm -v))"

# -----------------------------------------------------------------------------
# 2. Backend Python Virtual Environment & Dependencies
# -----------------------------------------------------------------------------
echo
echo "[2/4] Setting up Python virtual environment (backend/.venv)..."

VENV_PY="backend/.venv/bin/python"
if command -v uv >/dev/null 2>&1; then
    echo "    Using uv to create virtual environment and install packages..."
    if [[ ! -x "$VENV_PY" ]]; then
        uv venv --python 3.12 backend/.venv || uv venv backend/.venv
    fi
    uv pip install -r backend/requirements.txt --python "$VENV_PY"
else
    echo "    Using $PYTHON_CMD to create virtual environment and install packages..."
    if [[ ! -x "$VENV_PY" ]]; then
        "$PYTHON_CMD" -m venv backend/.venv
    fi
    "$VENV_PY" -m pip install --upgrade pip
    "$VENV_PY" -m pip install -r backend/requirements.txt
fi

if [[ ! -x "$VENV_PY" ]]; then
    echo "[ERROR] Failed to create backend/.venv!"
    exit 1
fi

# Optional desktop window packages (best-effort; falls back cleanly to browser mode if GTK/Qt/Cocoa deps are absent)
if [[ "${1:-}" == "--desktop" || "${2:-}" == "--desktop" ]]; then
    echo "    Installing optional desktop window packages..."
    if command -v uv >/dev/null 2>&1; then
        uv pip install -r packaging/requirements-desktop.txt --python "$VENV_PY" || echo "    [Note] Optional desktop packages skipped (browser mode will be used)."
    else
        "$VENV_PY" -m pip install -r packaging/requirements-desktop.txt || echo "    [Note] Optional desktop packages skipped (browser mode will be used)."
    fi
fi

# -----------------------------------------------------------------------------
# 3. Configuration (.env)
# -----------------------------------------------------------------------------
echo
if [[ ! -f "backend/.env" ]]; then
    echo "[3/4] Creating backend/.env from template..."
    cp "backend/.env.example" "backend/.env"
    echo "      Remember to set GEMINI_API_KEY in backend/.env (or enter it in the UI)."
else
    echo "[3/4] Configuration file backend/.env already exists."
fi

# -----------------------------------------------------------------------------
# 4. Frontend Build
# -----------------------------------------------------------------------------
echo
echo "[4/4] Installing and building frontend studio..."
pushd frontend >/dev/null
npm install
npm run build
popd >/dev/null

chmod +x "$SCRIPT_DIR/run.sh" "$SCRIPT_DIR/install.sh" 2>/dev/null || true
[[ -f "$SCRIPT_DIR/run.command" ]] && chmod +x "$SCRIPT_DIR/run.command" 2>/dev/null || true
[[ -f "$SCRIPT_DIR/install.command" ]] && chmod +x "$SCRIPT_DIR/install.command" 2>/dev/null || true

echo
echo "==================================================="
echo " Installation complete!"
echo " Start SubForge Studio anytime with:  ./run.sh"
if [[ "$OS" == "Darwin" ]]; then
    echo " (or double-click run.command in Finder)"
fi
echo "==================================================="
