"""Запуск после восстановления, без GUI и настоящих установок в тестах."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from local_mode.instance import SingleInstance, is_running
import launch_local


class AutoStartupTests(unittest.TestCase):
    def test_running_instance_is_detected_without_gui(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertFalse(is_running(root))
            with SingleInstance(root) as owned:
                self.assertTrue(owned)
                self.assertTrue(is_running(root))
            self.assertFalse(is_running(root))

    def test_running_agent_skips_repair_and_application_import(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(launch_local, "ROOT", Path(folder)), patch("local_mode.instance.is_running", return_value=True), patch("local_mode.setup_state.check_environment") as repair:
            self.assertEqual(launch_local.main(), 0)
            repair.assert_not_called()

    def test_repaired_environment_relaunches_with_original_arguments(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(launch_local, "ROOT", Path(folder)), patch("local_mode.instance.is_running", return_value=False), patch("local_mode.setup_state.check_environment", return_value=True), patch.object(launch_local.subprocess, "Popen") as start, patch.object(sys, "argv", ["launch_local.py", "--remote", "--model", "gemini-3.5-flash-lite"]):
            self.assertEqual(launch_local.main(), 0)
            args = start.call_args.args[0]
            self.assertEqual(args[-3:], ["--remote", "--model", "gemini-3.5-flash-lite"])
            self.assertEqual(args[0], sys.executable)
            self.assertEqual(start.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
            self.assertTrue(Path(args[3]).is_absolute())

    def test_ready_environment_continues_to_application(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(launch_local, "ROOT", Path(folder)), patch("local_mode.instance.is_running", return_value=False), patch("local_mode.setup_state.check_environment", return_value=False), patch("local_mode.cli.main", return_value=0) as run, patch.object(launch_local.subprocess, "Popen") as start, patch.object(sys, "argv", ["launch_local.py", "--remote"]):
            self.assertEqual(launch_local.main(), 0)
            run.assert_called_once_with(["--remote"])
            start.assert_not_called()

    def test_two_processes_only_repair_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "requirements").mkdir()
            (root / "requirements/full.lock.txt").write_text("fixture==1")
            (root / "setup_local.ps1").write_text("# fixture")
            (root / "model.bin").write_bytes(b"model")
            child = root / "check.py"
            child.write_text("""from pathlib import Path
import sys, time
from local_mode import setup_state
root=Path(sys.argv[1])
setup_state.MODEL_FILES=("model.bin",)
def repair(root):
    with (root/"repairs.txt").open("a") as stream:
        stream.write("repair\\n")
    time.sleep(0.3)
    setup_state.record(root)
setup_state._run_repair=repair
setup_state.check_environment(root)
""")
            children = [subprocess.Popen([sys.executable, str(child), str(root)], cwd=Path(__file__).resolve().parents[3],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW) for _ in range(2)]
            try:
                for process in children:
                    stdout, stderr = process.communicate(timeout=20)
                    self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
                self.assertEqual((root / "repairs.txt").read_text().count("repair"), 1)
            finally:
                for process in children:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)
