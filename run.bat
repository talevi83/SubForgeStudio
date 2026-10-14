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

:: 3. Build the frontend if it is missing or older than its sources
set NEED_BUILD=1
if not exist "frontend\dist\index.html" goto :build_frontend
for /f %%i in ('powershell -NoProfile -Command "$b=(Get-Item 'frontend\dist\index.html').LastWriteTime; $s=(Get-ChildItem 'frontend\src','frontend\index.html','frontend\package.json' -Recurse -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime; if ($s -gt $b) {1} else {0}"') do set NEED_BUILD=%%i
:build_frontend
if not "%NEED_BUILD%"=="1" goto :frontend_ready
echo [3/4] Building frontend studio...
pushd frontend
if not exist "node_modules" call npm install
call npm run build
set BUILD_RESULT=%ERRORLEVEL%
popd
if not "%BUILD_RESULT%"=="0" (
    echo [ERROR] Frontend build failed!
    pause
    exit /b 1
)
goto :frontend_done
:frontend_ready
echo [3/4] Frontend studio build is up to date.
:frontend_done

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
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

if %ERRORLEVEL% neq 0 (
    echo.
    echo Server stopped with error.
    pause
)
