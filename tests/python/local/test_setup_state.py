"""Отпечаток окружения: без сети, установки пакетов и внешних процессов."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_mode import setup_state


class SetupStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "requirements").mkdir()
        (self.root / "requirements/full.lock.txt").write_text("sample-package==1.0\n")
        (self.root / "setup_local.ps1").write_text("# fixture")
        (self.root / "model.bin").write_bytes(b"model")
        (self.root / "site/sample.dist-info").mkdir(parents=True)
        (self.root / "site/sample.dist-info/METADATA").write_text("Version: 1.0")
        for context in [patch.object(setup_state, "MODEL_FILES", ("model.bin",)),
                        patch.object(setup_state.sysconfig, "get_path", return_value=str(self.root / "site"))]:
            context.start()
            self.addCleanup(context.stop)

    def test_successful_record_and_fast_readiness(self):
        self.assertFalse(setup_state.is_ready(self.root))
        setup_state.record(self.root)
        self.assertTrue(setup_state.is_ready(self.root))
        with patch("subprocess.run") as process:
            setup_state.check_environment(self.root)
            process.assert_not_called()

    def test_changed_lock_is_repaired_and_requires_fresh_process(self):
        setup_state.record(self.root)
        (self.root / "requirements/full.lock.txt").write_text("sample-package==2.0")
        with patch.object(setup_state, "_run_repair", side_effect=lambda root: setup_state.record(root)) as repair:
            self.assertTrue(setup_state.check_environment(self.root))
            repair.assert_called_once_with(self.root)
            self.assertTrue(setup_state.is_ready(self.root))

    def test_metadata_change_and_missing_model_invalidate_state(self):
        setup_state.record(self.root)
        (self.root / "site/sample.dist-info/METADATA").write_text("Version: 2.0 plus modified")
        self.assertFalse(setup_state.is_ready(self.root))
        setup_state.record(self.root)
        (self.root / "model.bin").unlink()
        self.assertFalse(setup_state.is_ready(self.root))

    def test_python_and_pythonw_share_environment_identity(self):
        with patch.object(setup_state.sys, "executable", str(self.root / "Scripts/python.exe")):
            first = setup_state.fingerprint(self.root)
        with patch.object(setup_state.sys, "executable", str(self.root / "Scripts/pythonw.exe")):
            self.assertEqual(setup_state.fingerprint(self.root), first)

    def test_corrupt_saved_state_is_rejected(self):
        setup_state.record(self.root)
        (self.root / "data/setup-state.json").write_text("[]")
        self.assertFalse(setup_state.is_ready(self.root))

    def test_legacy_verified_install_migrates_without_package_changes(self):
        (self.root / "data").mkdir()
        (self.root / "data/setup-complete.txt").write_text("unified-v4")
        with patch.object(setup_state.metadata, "version", return_value="1.0+cpu"), patch("subprocess.run") as process:
            setup_state.check_environment(self.root)
            self.assertTrue(setup_state.is_ready(self.root))
            process.assert_not_called()

    def test_legacy_install_with_wrong_package_is_not_accepted(self):
        (self.root / "data").mkdir()
        (self.root / "data/setup-complete.txt").write_text("unified-v4")
        with patch.object(setup_state.metadata, "version", return_value="2.0"), patch.object(setup_state, "_run_repair", side_effect=RuntimeError("install failed")):
            with self.assertRaises(RuntimeError):
                setup_state.check_environment(self.root)
        self.assertFalse((self.root / "data/setup-state.json").exists())


    def test_successful_exit_without_ready_state_is_rejected(self):
        with patch.object(setup_state, "_run_repair") as repair:
            with self.assertRaisesRegex(RuntimeError, "не подтвердил"):
                setup_state.check_environment(self.root)
            repair.assert_called_once()
        self.assertFalse(setup_state.is_ready(self.root))

    def test_failed_repair_can_be_retried_on_next_click(self):
        with patch.object(setup_state, "_run_repair", side_effect=RuntimeError("network offline")) as repair:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "network offline"):
                    setup_state.check_environment(self.root)
            self.assertEqual(repair.call_count, 2)
        self.assertFalse((self.root / "data/setup-state.json").exists())

    def test_repair_uses_hidden_noninteractive_installer_without_launch_recursion(self):
        import subprocess
        from types import SimpleNamespace
        with patch.object(setup_state.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            setup_state._run_repair(self.root)
        args = run.call_args.args[0]
        self.assertIn("-NonInteractive", args)
        self.assertNotIn("-Launch", args)
        self.assertEqual(run.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertEqual(run.call_args.kwargs["cwd"], self.root)
        self.assertTrue((self.root / "data/logs/repair.log").is_file())

    def test_nonzero_installer_exit_is_reported_with_log(self):
        from types import SimpleNamespace
        with patch.object(setup_state.subprocess, "run", return_value=SimpleNamespace(returncode=7)):
            with self.assertRaisesRegex(RuntimeError, "код 7"):
                setup_state._run_repair(self.root)

    def test_repair_ignores_attacker_controlled_windows_environment(self):
        from types import SimpleNamespace
        trusted = self.root / "trusted-system32"
        with patch.dict(setup_state.os.environ, {"SystemRoot": "attacker", "WINDIR": "attacker"}), \
                patch.object(setup_state, "_system_directory", return_value=trusted), \
                patch.object(setup_state.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            setup_state._run_repair(self.root)
        self.assertEqual(run.call_args.args[0][0], str(trusted / "WindowsPowerShell/v1.0/powershell.exe"))

    def test_system_directory_accepts_only_checked_absolute_win32_result(self):
        from unittest.mock import Mock
        def directory(buffer, capacity):
            buffer.value = str(self.root)
            return len(buffer.value)
        function = Mock(side_effect=directory)
        with patch.object(setup_state.ctypes, "WinDLL", create=True) as library:
            library.return_value.GetSystemDirectoryW = function
            self.assertEqual(setup_state._system_directory(), self.root)
            for invalid in (0, 32768):
                function.side_effect = None
                function.return_value = invalid
                with self.assertRaises(OSError):
                    setup_state._system_directory()
            def relative(buffer, capacity):
                buffer.value = "relative"
                return len(buffer.value)
            function.side_effect = relative
            with self.assertRaises(OSError):
                setup_state._system_directory()

    def test_stale_existing_state_is_adopted_only_after_strict_validation(self):
        (self.root / "data").mkdir()
        (self.root / "data/setup-complete.txt").write_text("unified-v4")
        setup_state.record(self.root)
        (self.root / "setup_local.ps1").write_text("# updated ASCII launcher")
        self.assertFalse(setup_state.is_ready(self.root))
        with patch.object(setup_state.metadata, "version", return_value="1.0"), \
                patch.object(setup_state, "record", wraps=setup_state.record) as record, \
                patch.object(setup_state, "_run_repair", side_effect=AssertionError("Ready environment must not reinstall")) as repair:
            self.assertFalse(setup_state.check_environment(self.root))
        record.assert_called_once_with(self.root)
        repair.assert_not_called()
        self.assertTrue(setup_state.is_ready(self.root))

    def test_stale_existing_state_never_adopts_missing_or_wrong_packages_models_or_marker(self):
        (self.root / "data").mkdir()
        marker = self.root / "data/setup-complete.txt"
        marker.write_text("unified-v4")
        setup_state.record(self.root)
        (self.root / "setup_local.ps1").write_text("# updated ASCII launcher")
        for failure in ("missing_package", "wrong_pin", "missing_model", "wrong_marker"):
            with self.subTest(failure=failure):
                version = setup_state.metadata.PackageNotFoundError("sample-package") if failure == "missing_package" else "2.0" if failure == "wrong_pin" else "1.0"
                if failure == "missing_model":
                    (self.root / "model.bin").unlink()
                if failure == "wrong_marker":
                    marker.write_text("obsolete")
                try:
                    with patch.object(setup_state.metadata, "version", **({"side_effect": version} if isinstance(version, Exception) else {"return_value": version})), \
                            patch.object(setup_state, "record") as record, \
                            patch.object(setup_state, "_run_repair", side_effect=RuntimeError("repair required")) as repair:
                        with self.assertRaisesRegex(RuntimeError, "repair required"):
                            setup_state.check_environment(self.root)
                    record.assert_not_called()
                    repair.assert_called_once_with(self.root)
                finally:
                    (self.root / "model.bin").write_bytes(b"model")
                    marker.write_text("unified-v4")
