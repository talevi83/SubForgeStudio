#!/usr/bin/env bash
set -euo pipefail

echo "==================================================="
echo "             SubForge Studio Launcher"
echo "==================================================="
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

OS="$(uname -s)"
VENV_PY="$SCRIPT_DIR/backend/.venv/bin/python"

# -----------------------------------------------------------------------------
# 1. Ensure backend virtual environment exists
# -----------------------------------------------------------------------------
if [[ ! -x "$VENV_PY" ]]; then
    echo "[1/4] Setting up Python virtual environment..."
    if command -v uv >/dev/null 2>&1; then
        uv venv --python 3.12 backend/.venv || uv venv backend/.venv
        uv pip install -r backend/requirements.txt --python "$VENV_PY"
    else
        PYTHON_CMD=""
        for cmd in python3.12 python3.13 python3.11 python3; do
            if command -v "$cmd" >/dev/null 2>&1; then
                PYTHON_CMD="$cmd"
                break
            fi
        done
        if [[ -z "$PYTHON_CMD" ]]; then
            echo "[ERROR] Python 3.11+ not found! Run ./install.sh first."
            exit 1
        fi
        "$PYTHON_CMD" -m venv backend/.venv
        "$VENV_PY" -m pip install -r backend/requirements.txt
    fi
    if [[ ! -x "$VENV_PY" ]]; then
        echo "[ERROR] Failed to set up Python virtual environment!"
        exit 1
    fi
else
    echo "[1/4] Python virtual environment ready."
fi

# -----------------------------------------------------------------------------
# 2. Ensure .env exists
# -----------------------------------------------------------------------------
if [[ ! -f "backend/.env" ]]; then
    echo "[2/4] Initializing backend/.env from template..."
    cp "backend/.env.example" "backend/.env"
else
    echo "[2/4] Configuration file backend/.env found."
fi

# -----------------------------------------------------------------------------
# 3. Build the frontend if it is missing or older than its sources
# -----------------------------------------------------------------------------
NEED_BUILD=0
if [[ ! -f "frontend/dist/index.html" ]]; then
    NEED_BUILD=1
elif [[ "frontend/index.html" -nt "frontend/dist/index.html" ]] || [[ "frontend/package.json" -nt "frontend/dist/index.html" ]]; then
    NEED_BUILD=1
elif [[ -n "$(find frontend/src -type f -newer frontend/dist/index.html -print -quit 2>/dev/null)" ]]; then
    NEED_BUILD=1
fi

if [[ "$NEED_BUILD" -eq 1 ]]; then
    echo "[3/4] Building frontend studio..."
    pushd frontend >/dev/null
    if [[ ! -d "node_modules" ]]; then
        npm install
    fi
    npm run build
    popd >/dev/null
else
    echo "[3/4] Frontend studio build is up to date."
fi

# -----------------------------------------------------------------------------
# 4. Launch SubForge Studio
# -----------------------------------------------------------------------------
# Set LD_LIBRARY_PATH on Linux if NVIDIA CUDA pip wheels are installed inside the venv
if [[ "$OS" == "Linux" ]]; then
    NVIDIA_LIBS="$(find backend/.venv/lib -type d -path "*/site-packages/nvidia/*/lib" 2>/dev/null | tr '\n' ':' | sed 's/:$//')"
    if [[ -n "$NVIDIA_LIBS" ]]; then
        export LD_LIBRARY_PATH="${NVIDIA_LIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi
fi

# Optional native desktop window mode (--window) if pywebview + pystray + Pillow are installed
if [[ "${1:-}" == "--window" ]]; then
    if "$VENV_PY" -c "import webview, pystray, PIL" >/dev/null 2>&1; then
        echo "[4/4] Starting SubForge Studio in its own window..."
        export SUBFORGE_HOME="$SCRIPT_DIR/backend"
        exec "$VENV_PY" "$SCRIPT_DIR/packaging/launcher.py"
    else
        echo "[WARNING] Desktop window packages (pywebview, pystray, Pillow) are not installed — opening in browser instead."
    fi
fi

PORT="${PORT:-8000}"
URL="http://localhost:$PORT"

echo "[4/4] Starting SubForge Studio on $URL ..."
echo
echo "==================================================="
echo "Server is running! Press Ctrl+C in this window to stop."
echo "==================================================="
echo

# Launch browser in background after 2 seconds (unless --no-browser is passed)
if [[ "${1:-}" != "--no-browser" && "${2:-}" != "--no-browser" ]]; then
    (
        sleep 2
        if [[ "$OS" == "Darwin" ]]; then
            open "$URL" >/dev/null 2>&1 || true
        elif command -v xdg-open >/dev/null 2>&1; then
            xdg-open "$URL" >/dev/null 2>&1 || true
        fi
    ) &
fi

# Start Uvicorn server
exec "$VENV_PY" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port "$PORT"
