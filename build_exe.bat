@echo off
setlocal
title SubForge Studio - Build EXE
cd /d "%~dp0"

echo ===================================================
echo           SubForge Studio - Build EXE
echo ===================================================
echo.

set "PY=backend\.venv\Scripts\python.exe"

:: 1. Python environment with backend + build dependencies
echo [1/5] Installing Python dependencies...
where uv >nul 2>nul
if %ERRORLEVEL% equ 0 (
    if not exist "%PY%" uv venv --python 3.12 backend\.venv
    uv pip install -r backend\requirements.txt -r packaging\requirements-build.txt --python "%PY%"
) else (
    if not exist "%PY%" python -m venv backend\.venv
    "%PY%" -m pip install -r backend\requirements.txt -r packaging\requirements-build.txt
)
if not exist "%PY%" goto :fail
"%PY%" -c "import PyInstaller, pystray, PIL" || goto :fail

:: 2. Frontend production build
echo.
echo [2/5] Building frontend...
pushd frontend
if not exist "node_modules" call npm install
call npm run build
set BUILD_RESULT=%ERRORLEVEL%
popd
if not "%BUILD_RESULT%"=="0" goto :fail

:: 3. FFmpeg to bundle (downloaded once)
echo.
if exist "packaging\ffmpeg\ffmpeg.exe" if exist "packaging\ffmpeg\ffprobe.exe" (
    echo [3/5] FFmpeg already in packaging\ffmpeg.
    goto :ffmpeg_done
)
echo [3/5] Downloading FFmpeg...
powershell -NoProfile -ExecutionPolicy Bypass -File "packaging\get_ffmpeg.ps1"
if %ERRORLEVEL% neq 0 (
    echo [WARNING] FFmpeg download failed. Put ffmpeg.exe and ffprobe.exe in packaging\ffmpeg
    echo           and run this again, or the exe will rely on FFmpeg from PATH.
)
:ffmpeg_done

:: 4. PyInstaller
echo.
echo [4/5] Packaging with PyInstaller (this takes a few minutes)...
"%PY%" -m PyInstaller packaging\subforge.spec --noconfirm --clean --distpath dist --workpath build
if %ERRORLEVEL% neq 0 goto :fail

:: 5. First-run data folder: reuse the API key from backend\.env
echo.
set "DATA=%LOCALAPPDATA%\SubForge Studio"
echo [5/5] Data folder: "%DATA%"
if exist "%DATA%\webview\EBWebView\Default\Cache" rmdir /s /q "%DATA%\webview\EBWebView\Default\Cache" >nul 2>nul
if exist "%DATA%\webview\EBWebView\Default\Code Cache" rmdir /s /q "%DATA%\webview\EBWebView\Default\Code Cache" >nul 2>nul
if exist "backend\webview\EBWebView\Default\Cache" rmdir /s /q "backend\webview\EBWebView\Default\Cache" >nul 2>nul
if exist "backend\webview\EBWebView\Default\Code Cache" rmdir /s /q "backend\webview\EBWebView\Default\Code Cache" >nul 2>nul
if exist "%DATA%\.env" goto :data_done
if not exist "backend\.env" goto :data_done
if not exist "%DATA%" mkdir "%DATA%"
copy "backend\.env" "%DATA%\.env" >nul
echo       Copied your backend\.env (API key) there.
:data_done

echo.
echo ===================================================
echo  Done:  dist\SubForge Studio\SubForge Studio.exe
echo  Copy the whole "dist\SubForge Studio" folder; the exe needs the files next to it.
echo ===================================================
pause
exit /b 0

:fail
echo.
echo [ERROR] Build failed - see the messages above.
pause
exit /b 1
