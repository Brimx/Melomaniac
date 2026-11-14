from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from services import desktop_integration as di


class DesktopIntegrationTests(unittest.TestCase):
    def test_appimage_ignored_when_not_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_appimage = Path(tmp) / "Melomaniac.AppImage"
            fake_appimage.write_text("appimage", encoding="utf-8")
            with (
                mock.patch.object(di.sys, "platform", "linux"),
                mock.patch.object(di.sys, "frozen", False, create=True),
                mock.patch.dict(os.environ, {"APPIMAGE": str(fake_appimage)}),
            ):
                env = di.get_runtime_environment()
        self.assertFalse(env.is_appimage)
        self.assertIsNone(env.appimage_path)

    def test_appimage_detected_when_frozen_and_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_appimage = Path(tmp) / "Melomaniac.AppImage"
            fake_appimage.write_text("appimage", encoding="utf-8")
            with (
                mock.patch.object(di.sys, "platform", "linux"),
                mock.patch.object(di.sys, "frozen", True, create=True),
                mock.patch.dict(os.environ, {"APPIMAGE": str(fake_appimage)}),
            ):
                env = di.get_runtime_environment()
        self.assertTrue(env.is_appimage)
        self.assertEqual(env.appimage_path, fake_appimage.resolve())

    def test_ensure_icon_installed_avoids_double_icons_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xdg = Path(tmp) / "share"
            with (
                mock.patch.object(di.sys, "platform", "linux"),
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(xdg)}),
            ):
                dest = di.ensure_icon_installed()
            self.assertIsNotNone(dest)
            self.assertTrue((xdg / "icons" / "melomaniac.png").is_file())
            self.assertTrue((xdg / "icons" / "hicolor" / "256x256" / "apps" / "melomaniac.png").is_file())
            self.assertFalse((xdg / "icons" / "icons").exists())

    def test_linux_install_shortcut_includes_path_and_startup_notify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xdg = Path(tmp) / "share"
            fake_appimage = Path(tmp) / "Melomaniac.AppImage"
            fake_appimage.write_text("appimage", encoding="utf-8")
            with (
                mock.patch.object(di.sys, "platform", "linux"),
                mock.patch.object(di.sys, "frozen", True, create=True),
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(xdg), "APPIMAGE": str(fake_appimage)}),
                mock.patch.object(di.shutil, "which", return_value=None),
            ):
                ok, _ = di.install_shortcut()
            self.assertTrue(ok)
            desktop_file = xdg / "applications" / "melomaniac.desktop"
            content = desktop_file.read_text(encoding="utf-8")
            self.assertIn(f"Path={fake_appimage.resolve().parent}", content)
            self.assertIn("StartupNotify=true", content)

    def test_windows_desktop_dir_uses_winreg_onedrive_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            onedrive_desktop = Path(tmp) / "OneDrive" / "Desktop"
            onedrive_desktop.mkdir(parents=True)

            class _KeyCtx:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            fake_winreg = types.SimpleNamespace(
                HKEY_CURRENT_USER=1,
                OpenKey=lambda *_args: _KeyCtx(),
                QueryValueEx=lambda *_args: (str(onedrive_desktop), 1),
            )
            with (
                mock.patch.object(di.sys, "platform", "win32"),
                mock.patch.dict(sys.modules, {"winreg": fake_winreg}),
            ):
                resolved = di._get_windows_desktop_dir()
            self.assertEqual(resolved, onedrive_desktop)

    def test_windows_portable_status_detects_stale_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            current_exe = tmp_path / "v4.10.0" / "Melomaniac.exe"
            old_exe = tmp_path / "v4.9.0" / "Melomaniac.exe"
            shortcut = tmp_path / "Melomaniac.lnk"
            current_exe.parent.mkdir(parents=True)
            old_exe.parent.mkdir(parents=True)
            current_exe.write_text("new", encoding="utf-8")
            old_exe.write_text("old", encoding="utf-8")
            shortcut.write_text("lnk", encoding="utf-8")

            env = di.RuntimeEnvironment(
                os_name="windows",
                is_frozen=True,
                is_appimage=False,
                appimage_path=None,
                is_windows_installer=False,
                is_windows_portable=True,
                executable_path=current_exe,
                label="Windows (ZIP portable)",
            )
            with (
                mock.patch.object(di, "get_runtime_environment", return_value=env),
                mock.patch.object(di, "get_windows_shortcut_path", return_value=shortcut),
                mock.patch.object(di, "_read_windows_shortcut_target", return_value=str(old_exe)),
            ):
                status = di.get_shortcut_status()
            self.assertTrue(status.installed)
            self.assertFalse(status.is_current)
            self.assertFalse(status.is_broken)
            self.assertEqual(status.target_path, str(old_exe))


if __name__ == "__main__":
    unittest.main()
