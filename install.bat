@echo off
setlocal enabledelayedexpansion
title SubBuild Studio - Installer

echo ===================================================
echo            SubBuild Studio - Installer
echo ===================================================
echo.

cd /d "%~dp0"

:: 1. Check prerequisites
echo [1/5] Checking prerequisites...
where npm >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Node.js / npm not found on PATH! Please install Node.js 20.19+ or 22.12+.
    pause
    exit /b 1
)
where ffmpeg >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [WARNING] FFmpeg not found on PATH. SubBuild Studio needs FFmpeg and FFprobe to process media.
)

:: 2. Python virtual environment + backend & desktop dependencies
echo.
echo [2/5] Setting up Python virtual environment (backend\.venv)...
where uv >nul 2>nul
if %ERRORLEVEL% equ 0 (
    if not exist "backend\.venv\Scripts\python.exe" uv venv --python 3.12 backend\.venv
    uv pip install -r backend\requirements.txt -r packaging\requirements-desktop.txt --python backend\.venv\Scripts\python.exe
) else (
    if not exist "backend\.venv\Scripts\python.exe" python -m venv backend\.venv
    backend\.venv\Scripts\python.exe -m pip install --upgrade pip
    backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt -r packaging\requirements-desktop.txt
)
if not exist "backend\.venv\Scripts\python.exe" (
    echo [ERROR] Failed to set up Python virtual environment!
    pause
    exit /b 1
)

:: 3. Ensure .env exists
echo.
if not exist "backend\.env" (
    echo [3/5] Initializing backend\.env from template...
    copy "backend\.env.example" "backend\.env" >nul
) else (
    echo [3/5] Configuration file backend\.env found.
)

:: 4. Build frontend
echo.
echo [4/5] Installing and building frontend studio...
pushd frontend
call npm install
call npm run build
set BUILD_RESULT=%ERRORLEVEL%
popd
if not "%BUILD_RESULT%"=="0" (
    echo [ERROR] Frontend build failed!
    pause
    exit /b 1
)

:: 5. Create desktop shortcut
echo.
echo [5/5] Desktop shortcut setup...
set DO_SHORTCUT=Y
if /i "%~1"=="--no-shortcut" set DO_SHORTCUT=N
if /i "%~2"=="--no-shortcut" set DO_SHORTCUT=N
if /i "%~1"=="--yes" set DO_SHORTCUT=Y
if /i "%~1"=="-y" set DO_SHORTCUT=Y
if /i "%~1"=="--shortcut" set DO_SHORTCUT=Y

if /i "%DO_SHORTCUT%"=="Y" (
    if not "%~1"=="--yes" if not "%~1"=="-y" if not "%~1"=="--shortcut" (
        set /p "PROMPT_SHORTCUT=Create a desktop shortcut for SubBuild Studio? [Y/n]: "
        if /i "!PROMPT_SHORTCUT!"=="n" set DO_SHORTCUT=N
        if /i "!PROMPT_SHORTCUT!"=="no" set DO_SHORTCUT=N
    )
)

if /i "%DO_SHORTCUT%"=="Y" (
    "%~dp0backend\.venv\Scripts\python.exe" "%~dp0packaging\create_shortcut.py"
    if !ERRORLEVEL! neq 0 (
        echo [WARNING] Could not create desktop shortcut. You can run packaging\create_shortcut.py later.
    )
) else (
    echo Desktop shortcut skipped.
)

echo.
echo ===================================================
echo  Installation complete!
echo  Double-click SubBuild Studio on your Desktop,
echo  or double-click run.bat to start the studio.
echo ===================================================
pause
exit /b 0
