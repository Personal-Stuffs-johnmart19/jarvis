"""Перенос Get-StartApps: ни один тест не запускает реальные приложения."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch
from actions import open_app as apps
from local_mode.tools import ToolRegistry


class ApplicationLaunchTests(unittest.TestCase):
    def setUp(self):
        directory = patch.object(apps, "_windows_directory", return_value=Path("C:/Windows"))
        directory.start()
        self.addCleanup(directory.stop)
        apps._windows_start_apps.cache_clear()
        self.addCleanup(apps._windows_start_apps.cache_clear)

    @patch.object(apps, "_find_registry_exe", return_value=None)
    @patch.object(apps, "_launch_start_menu", return_value=False)
    @patch.object(apps, "_windows_start_apps", return_value=[])
    @patch.object(apps.subprocess, "Popen")
    def test_missing_application_is_failed_without_shell_fallback(self, launch, catalog, shortcuts, registry):
        with patch.object(apps.platform, "system", return_value="Windows"):
            result = ToolRegistry().execute("open_app", {"app_name": "Definitely Missing Application"})
        self.assertFalse(result["ok"])
        launch.assert_not_called()

    @patch.object(apps, "_windows_start_apps", return_value=[{"Name": "Discord", "AppID": "known.discord"}])
    @patch.object(apps.subprocess, "Popen")
    def test_catalog_launch_uses_only_observed_app_id(self, launch, catalog):
        self.assertTrue(apps._launch_windows("Discord"))
        args = launch.call_args.args[0]
        self.assertTrue(args[0].lower().endswith("explorer.exe"))
        self.assertEqual(args[1], "shell:AppsFolder\\known.discord")
        self.assertFalse(launch.call_args.kwargs["shell"])

    @patch.object(apps, "_windows_start_apps", return_value=[{"Name": "Visual Studio", "AppID": "vs"}, {"Name": "Visual Studio Code", "AppID": "code"}])
    @patch.object(apps.subprocess, "Popen")
    def test_ambiguous_match_requires_precise_name(self, launch, catalog):
        with self.assertRaisesRegex(ValueError, "Уточните"):
            apps._launch_windows("Visual")
        launch.assert_not_called()
        self.assertTrue(apps._launch_windows("Visual Studio"))
        self.assertEqual(launch.call_count, 1)

    @patch.object(apps, "_windows_start_apps", return_value=[{"Name": "Рисование", "AppID": "Microsoft.Paint_8wekyb3d8bbwe!App"}])
    @patch.object(apps.Path, "is_file", return_value=False)
    @patch.object(apps.subprocess, "Popen")
    def test_packaged_paint_is_found_by_installed_id_without_old_exe(self, launch, exists, catalog):
        self.assertTrue(apps._launch_windows("mspaint.exe"))
        self.assertEqual(launch.call_args.args[0][1], "shell:AppsFolder\\Microsoft.Paint_8wekyb3d8bbwe!App")

    @patch.object(apps, "_windows_start_apps", return_value=[])
    @patch.object(apps, "_launch_start_menu", return_value=False)
    @patch.object(apps, "_find_registry_exe", return_value=None)
    @patch.object(apps.Path, "is_file", return_value=False)
    @patch.object(apps.subprocess, "Popen")
    def test_uninstalled_packaged_id_is_not_guessed(self, launch, exists, registry, shortcut, catalog):
        self.assertFalse(apps._launch_windows("mspaint.exe"))
        launch.assert_not_called()

    def test_model_text_is_never_interpolated_into_powershell(self):
        output = Mock(stdout=json.dumps([{"Name": "Блокнот", "AppID": "safe"}, {"Name": 4, "AppID": "bad"}]).encode())
        with patch.object(apps.subprocess, "run", return_value=output) as run:
            self.assertEqual(apps._windows_start_apps(1), [{"Name": "Блокнот", "AppID": "safe"}])
            apps._windows_start_apps(1)
            run.assert_called_once()
            args = run.call_args.args[0]
            self.assertIn("Get-StartApps", args[-1])
            self.assertTrue(run.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)

    @patch.object(apps.platform, "system", return_value="Windows")
    def test_windows_aliases_are_exact(self, system):
        self.assertEqual(apps._normalize("chrome"), "chrome")
        self.assertEqual(apps._normalize("chrome & calc"), "chrome & calc")
        self.assertEqual(apps._normalize("My Chrome Helper"), "My Chrome Helper")

    @patch.object(apps, "_windows_start_apps", return_value=[{"Name": "Discord", "AppID": "known.discord"}])
    @patch.object(apps.subprocess, "Popen")
    @patch.object(apps.platform, "system", return_value="Windows")
    def test_launch_report_does_not_claim_window_is_ready(self, system, launch, catalog):
        result = ToolRegistry().execute("open_app", {"app_name": "Discord"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"], "tool_report")
        self.assertIn("ещё не подтверждена", result["result"])

    @patch.object(apps, "_find_registry_exe", return_value=None)
    @patch.object(apps, "_launch_start_menu", return_value=False)
    @patch.object(apps, "_windows_start_apps", return_value=[])
    @patch.object(apps.subprocess, "Popen")
    def test_command_lines_are_not_accepted_as_applications(self, launch, catalog, shortcuts, registry):
        for name in ["cmd /c calc", "notepad.exe /x", "chrome & calc"]:
            self.assertFalse(apps._launch_windows(name))
        launch.assert_not_called()


class WindowsDirectoryTests(unittest.TestCase):
    def resolve(self, value, length=None):
        def query(buffer, capacity):
            buffer.value = value
            return len(value) if length is None else length
        kernel = Mock()
        kernel.GetWindowsDirectoryW.side_effect = query
        with patch.object(apps.ctypes, "WinDLL", return_value=kernel, create=True):
            return apps._windows_directory()

    def test_directory_comes_from_win32_not_environment(self):
        expected = str(Path.cwd() / "Windows")
        with patch.dict(os.environ, {"SystemRoot": "attacker-controlled", "WINDIR": "also-untrusted"}):
            self.assertEqual(self.resolve(expected), Path(expected))

    def test_failed_or_truncated_query_fails_closed(self):
        for length in (0, 32768, 40000):
            with self.subTest(length=length), self.assertRaises(OSError):
                self.resolve("", length)

    def test_relative_directory_is_rejected(self):
        with self.assertRaises(OSError):
            self.resolve("relative/windows")
