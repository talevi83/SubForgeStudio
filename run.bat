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

:: 4. Make sure the window packages (pywebview, pystray, pillow) are installed
if /i "%~1"=="--browser" goto :browser_mode
"%~dp0backend\.venv\Scripts\python.exe" -c "import webview, pystray, PIL" >nul 2>nul
if errorlevel 1 (
    echo [4/4] Installing the desktop window packages ^(one time^)...
    where uv >nul 2>nul
    if !ERRORLEVEL! equ 0 (
        uv pip install -r packaging\requirements-desktop.txt --python backend\.venv\Scripts\python.exe
    ) else (
        backend\.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r packaging\requirements-desktop.txt
    )
    "%~dp0backend\.venv\Scripts\python.exe" -c "import webview, pystray, PIL" >nul 2>nul
    if errorlevel 1 (
        echo [WARNING] Could not install the window packages - opening in the browser instead.
        goto :browser_mode
    )
)

:: Start SubForge Studio in its own window (no browser, no console). The API key and the
:: projects stay in backend\ (backend\.env and backend\storage), like in the browser mode.
echo [4/4] Starting SubForge Studio in its own window...
set "SUBFORGE_HOME=%~dp0backend"
start "" "%~dp0backend\.venv\Scripts\pythonw.exe" "%~dp0packaging\launcher.py"
exit /b 0

:browser_mode
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
