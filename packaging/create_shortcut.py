"""Creates application and desktop shortcuts for SubBuild Studio across Windows, macOS, and Linux.

Usage:
    python packaging/create_shortcut.py             # Create shortcut for current platform
    python packaging/create_shortcut.py --remove    # Remove shortcut for current platform
    python packaging/create_shortcut.py --help
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "SubBuild Studio"
APP_DESC = "SubBuild Studio - AI-Powered Subtitle Editor & Translator"
DESKTOP_ID = "subbuild-studio"


def get_repo_root() -> Path:
    """Return the absolute path to the repository root directory."""
    return Path(__file__).resolve().parent.parent


def get_windows_desktop_dir() -> Path:
    """Locate the user's Desktop directory on Windows, handling OneDrive redirection."""
    # 1. Try PowerShell to query the official shell Desktop folder
    try:
        ps_cmd = "[Environment]::GetFolderPath('Desktop')"
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if out and Path(out).is_dir():
            return Path(out)
    except Exception:
        pass

    # 2. Check OneDrive Desktop
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        onedrive_desktop = Path(userprofile) / "OneDrive" / "Desktop"
        if onedrive_desktop.is_dir():
            return onedrive_desktop
        std_desktop = Path(userprofile) / "Desktop"
        if std_desktop.is_dir():
            return std_desktop

    return Path.home() / "Desktop"


def install_windows_shortcut(repo_root: Path, desktop_dir: Path | None = None) -> Path:
    """Create a Windows desktop shortcut (.lnk) pointing to run.bat."""
    desktop = desktop_dir or get_windows_desktop_dir()
    desktop.mkdir(parents=True, exist_ok=True)
    shortcut_path = desktop / f"{APP_NAME}.lnk"

    target_bat = repo_root / "run.bat"
    icon_ico = repo_root / "packaging" / "icon.ico"

    if not target_bat.exists():
        raise FileNotFoundError(f"Target batch file not found: {target_bat}")

    icon_arg = f"{icon_ico.resolve()},0" if icon_ico.exists() else ""

    # PowerShell script to create the shortcut via WScript.Shell
    ps_script = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{shortcut_path.resolve()}')
$s.TargetPath = '{target_bat.resolve()}'
$s.WorkingDirectory = '{repo_root.resolve()}'
if ('{icon_arg}') {{
    $s.IconLocation = '{icon_arg}'
}}
$s.Description = '{APP_DESC}'
$s.WindowStyle = 1
$s.Save()
"""
    res = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"Failed to create Windows shortcut: {res.stderr.strip()}")

    return shortcut_path


def remove_windows_shortcut(desktop_dir: Path | None = None) -> bool:
    """Remove Windows desktop shortcut."""
    desktop = desktop_dir or get_windows_desktop_dir()
    shortcut_path = desktop / f"{APP_NAME}.lnk"
    if shortcut_path.exists():
        shortcut_path.unlink()
        return True
    return False


def install_macos_app(repo_root: Path, apps_dir: Path | None = None) -> Path:
    """Create a macOS .app bundle in ~/Applications pointing to run.sh / run.command."""
    target_apps = apps_dir or (Path.home() / "Applications")
    target_apps.mkdir(parents=True, exist_ok=True)

    app_bundle = target_apps / f"{APP_NAME}.app"
    contents_dir = app_bundle / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"

    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)

    # 1. Info.plist
    info_plist = contents_dir / "Info.plist"
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>com.subbuild.studio</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleExecutable</key>
    <string>{APP_NAME}</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
"""
    info_plist.write_text(plist_content, encoding="utf-8")

    # 2. PkgInfo
    (contents_dir / "PkgInfo").write_text("APPL????", encoding="utf-8")

    # 3. Icon
    src_icns = repo_root / "packaging" / "icon.icns"
    if src_icns.exists():
        shutil.copy2(src_icns, resources_dir / "AppIcon.icns")

    # 4. Executable wrapper
    launcher_sh = macos_dir / APP_NAME
    abs_repo = repo_root.resolve().as_posix()
    exec_content = f"""#!/usr/bin/env bash
DIR="{abs_repo}"
cd "$DIR" || exit 1

if command -v osascript >/dev/null 2>&1; then
    osascript <<EOF
tell application "Terminal"
    activate
    do script "cd \\"$DIR\\" && ./run.sh"
end tell
EOF
elif [[ -f "./run.command" ]]; then
    exec ./run.command "$@"
else
    exec ./run.sh "$@"
fi
"""
    launcher_sh.write_text(exec_content, encoding="utf-8")
    launcher_sh.chmod(0o755)

    # 5. Register with macOS LaunchServices if available
    lsregister_paths = [
        "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister",
        "/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister",
    ]
    for lsr in lsregister_paths:
        if os.path.exists(lsr):
            try:
                subprocess.run([lsr, "-f", str(app_bundle)], check=False, capture_output=True)
            except Exception:
                pass
            break

    try:
        os.utime(app_bundle, None)
    except Exception:
        pass

    return app_bundle


def remove_macos_app(apps_dir: Path | None = None) -> bool:
    """Remove macOS .app bundle from ~/Applications."""
    target_apps = apps_dir or (Path.home() / "Applications")
    app_bundle = target_apps / f"{APP_NAME}.app"
    if app_bundle.exists():
        shutil.rmtree(app_bundle, ignore_errors=True)
        return True
    return False


def install_linux_desktop(repo_root: Path, apps_dir: Path | None = None, icons_dir: Path | None = None) -> Path:
    """Create a FreeDesktop .desktop entry in ~/.local/share/applications."""
    target_apps = apps_dir or (Path.home() / ".local" / "share" / "applications")
    target_apps.mkdir(parents=True, exist_ok=True)

    target_icons = icons_dir or (Path.home() / ".local" / "share" / "icons" / "hicolor" / "512x512" / "apps")
    target_icons.mkdir(parents=True, exist_ok=True)

    src_png = repo_root / "packaging" / "icon.png"
    installed_icon = target_icons / f"{DESKTOP_ID}.png"
    if src_png.exists():
        shutil.copy2(src_png, installed_icon)
        icon_ref = str(installed_icon)
    else:
        icon_ref = str((repo_root / "packaging" / "icon.ico").resolve())

    desktop_file = target_apps / f"{DESKTOP_ID}.desktop"
    run_sh = (repo_root / "run.sh").resolve().as_posix()
    abs_repo = repo_root.resolve().as_posix()

    content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name={APP_NAME}
GenericName=Subtitle Editor & Translator
Comment={APP_DESC}
Exec="{run_sh}"
Path={abs_repo}
Icon={icon_ref}
Terminal=true
Categories=AudioVideo;AudioVideoEditing;Translation;
StartupWMClass={DESKTOP_ID}
Keywords=subtitles;subtitle;translation;video;transcription;
"""
    desktop_file.write_text(content, encoding="utf-8")
    desktop_file.chmod(0o755)

    # Update desktop database if tools are present
    if shutil.which("update-desktop-database"):
        try:
            subprocess.run(["update-desktop-database", str(target_apps)], capture_output=True, check=False)
        except Exception:
            pass

    if shutil.which("gtk-update-icon-cache"):
        try:
            hicolor_root = target_icons.parent.parent.parent
            subprocess.run(["gtk-update-icon-cache", "-f", "-t", str(hicolor_root)], capture_output=True, check=False)
        except Exception:
            pass

    return desktop_file


def remove_linux_desktop(apps_dir: Path | None = None, icons_dir: Path | None = None) -> bool:
    """Remove FreeDesktop .desktop entry and installed icon."""
    target_apps = apps_dir or (Path.home() / ".local" / "share" / "applications")
    desktop_file = target_apps / f"{DESKTOP_ID}.desktop"

    removed = False
    if desktop_file.exists():
        desktop_file.unlink()
        removed = True

    target_icons = icons_dir or (Path.home() / ".local" / "share" / "icons" / "hicolor" / "512x512" / "apps")
    installed_icon = target_icons / f"{DESKTOP_ID}.png"
    if installed_icon.exists():
        installed_icon.unlink()
        removed = True

    if shutil.which("update-desktop-database"):
        try:
            subprocess.run(["update-desktop-database", str(target_apps)], capture_output=True, check=False)
        except Exception:
            pass

    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Manage {APP_NAME} desktop and system application shortcuts.")
    parser.add_argument("--remove", action="store_true", help="Remove the shortcut instead of creating it")
    parser.add_argument("--os", choices=["windows", "darwin", "linux"], default=None, help="Override target OS")
    parser.add_argument("--quiet", "-q", action="store_true", help="Quiet mode (minimal output)")
    args = parser.parse_args()

    current_os = (args.os or sys.platform).lower()
    if current_os.startswith("win"):
        target_os = "windows"
    elif current_os == "darwin":
        target_os = "darwin"
    else:
        target_os = "linux"

    repo_root = get_repo_root()

    if args.remove:
        if target_os == "windows":
            ok = remove_windows_shortcut()
        elif target_os == "darwin":
            ok = remove_macos_app()
        else:
            ok = remove_linux_desktop()

        if not args.quiet:
            if ok:
                print(f"[OK] Removed {APP_NAME} shortcut for {target_os}.")
            else:
                print(f"[*] No existing {APP_NAME} shortcut found for {target_os}.")
        return 0

    try:
        if target_os == "windows":
            path = install_windows_shortcut(repo_root)
        elif target_os == "darwin":
            path = install_macos_app(repo_root)
        else:
            path = install_linux_desktop(repo_root)

        if not args.quiet:
            print(f"[OK] Successfully installed {APP_NAME} shortcut:")
            print(f"     -> {path}")
        return 0
    except Exception as exc:
        print(f"[ERROR] Failed to install shortcut: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
