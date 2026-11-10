import sys
from pathlib import Path

# Add packaging directory to sys.path so we can import create_shortcut
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "packaging"))

import create_shortcut


def test_install_linux_desktop(tmp_path: Path):
    apps_dir = tmp_path / "applications"
    icons_dir = tmp_path / "icons" / "hicolor" / "512x512" / "apps"

    desktop_file = create_shortcut.install_linux_desktop(
        repo_root=REPO_ROOT,
        apps_dir=apps_dir,
        icons_dir=icons_dir,
    )

    assert desktop_file.exists()
    assert desktop_file.name == "subbuild-studio.desktop"

    content = desktop_file.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in content
    assert "Name=SubBuild Studio" in content
    assert "Exec=" in content
    assert "run.sh" in content
    assert "Icon=" in content
    assert "Categories=" in content

    installed_icon = icons_dir / "subbuild-studio.png"
    assert installed_icon.exists()

    # Test remove
    removed = create_shortcut.remove_linux_desktop(apps_dir=apps_dir, icons_dir=icons_dir)
    assert removed is True
    assert not desktop_file.exists()
    assert not installed_icon.exists()


def test_install_macos_app(tmp_path: Path):
    apps_dir = tmp_path / "Applications"

    app_bundle = create_shortcut.install_macos_app(
        repo_root=REPO_ROOT,
        apps_dir=apps_dir,
    )

    assert app_bundle.exists()
    assert app_bundle.name == "SubBuild Studio.app"

    contents_dir = app_bundle / "Contents"
    info_plist = contents_dir / "Info.plist"
    pkg_info = contents_dir / "PkgInfo"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"
    launcher_sh = macos_dir / "SubBuild Studio"
    icon_icns = resources_dir / "AppIcon.icns"

    assert info_plist.exists()
    assert "SubBuild Studio" in info_plist.read_text(encoding="utf-8")
    assert pkg_info.exists()
    assert launcher_sh.exists()
    assert icon_icns.exists()

    # Test remove
    removed = create_shortcut.remove_macos_app(apps_dir=apps_dir)
    assert removed is True
    assert not app_bundle.exists()


def test_install_and_remove_windows_shortcut(tmp_path: Path):
    if sys.platform != "win32":
        return

    desktop_dir = tmp_path / "Desktop"
    shortcut_path = create_shortcut.install_windows_shortcut(
        repo_root=REPO_ROOT,
        desktop_dir=desktop_dir,
    )

    assert shortcut_path.exists()
    assert shortcut_path.name == "SubBuild Studio.lnk"

    removed = create_shortcut.remove_windows_shortcut(desktop_dir=desktop_dir)
    assert removed is True
    assert not shortcut_path.exists()
