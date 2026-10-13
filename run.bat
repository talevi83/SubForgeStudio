@echo off
setlocal enabledelayedexpansion
title SubForge Studio - Launcher

echo ===================================================
echo             SubForge Studio Launcher
echo ===================================================
echo.

cd /d "%~dp0"

:: 1. Ensure backend virtual environment exists
if not exist "backend\.venv\Scripts\python.exe" (
    echo [1/4] Setting up Python virtual environment...
    where uv >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        uv venv --python 3.12 backend\.venv
        uv pip install -r backend\requirements.txt --python backend\.venv\Scripts\python.exe
    ) else (
        python -m venv backend\.venv
        backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
    )
    if not exist "backend\.venv\Scripts\python.exe" (
        echo [ERROR] Failed to set up Python virtual environment!
        pause
        exit /b 1
    )
) else (
    echo [1/4] Python virtual environment ready.
)

:: 2. Ensure .env exists
if not exist "backend\.env" (
    echo [2/4] Initializing backend\.env from template...
    copy "backend\.env.example" "backend\.env" >nul
) else (
    echo [2/4] Configuration file backend\.env found.
)

:: 3. Ensure frontend build exists
if not exist "frontend\dist\index.html" (
    echo [3/4] Building frontend studio...
    cd frontend
    if not exist "node_modules" (
        call npm install
    )
    call npm run build
    cd ..
    if not exist "frontend\dist\index.html" (
        echo [ERROR] Frontend build failed!
        pause
        exit /b 1
    )
) else (
    echo [3/4] Frontend studio build ready.
)

:: 4. Start backend and open browser
echo [4/4] Starting SubForge Studio on http://localhost:8000 ...
echo.
echo ===================================================
echo Server is running! Press Ctrl+C in this window to stop.
echo ===================================================
echo.

:: Launch browser in background after 2 seconds
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:8000"

:: Start Uvicorn server
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000

if %ERRORLEVEL% neq 0 (
    echo.
    echo Server stopped with error.
    pause
)
