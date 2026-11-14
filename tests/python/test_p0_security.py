"""Tests for the P0 security & robustness fixes.

Covers:
  - document_qa SSRF guard (private/literal-IP targets are refused)
  - desktop_control rejects generated-code execution requests
  - executor UnknownToolError (deterministic skip, no LLM loop)

Run: python -m unittest tests.python.test_p0_security -v
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class DocumentQaSsrTest(unittest.TestCase):
    def setUp(self):
        from actions import document_qa
        self._is_private_target = document_qa._is_private_target

    def test_loopback_refused(self):
        self.assertTrue(self._is_private_target("http://127.0.0.1:8000/x"))
        self.assertTrue(self._is_private_target("http://localhost/x"))
        self.assertTrue(self._is_private_target("http://[::1]/x"))

    def test_private_literal_ip_refused(self):
        self.assertTrue(self._is_private_target("http://10.0.0.1/x"))
        self.assertTrue(self._is_private_target("http://192.168.1.1/x"))
        self.assertTrue(self._is_private_target("http://172.16.0.1/x"))

    def test_link_local_metadata_refused(self):
        self.assertTrue(self._is_private_target("http://169.254.169.254/latest/meta-data/"))
        self.assertTrue(self._is_private_target("http://metadata.google.internal/x"))

    def test_public_host_allowed(self):
        self.assertFalse(self._is_private_target("http://example.com/x"))
        self.assertFalse(self._is_private_target("https://en.wikipedia.org/x"))


class DesktopSandboxTest(unittest.TestCase):
    def test_generated_code_has_no_desktop_execution_route(self):
        from actions import desktop
        from local_mode.tools import ToolRegistry
        from jsonschema import ValidationError

        self.assertFalse(hasattr(desktop, "_execute_generated_code"))
        for code in [
            'ctypes.windll.kernel32.WinExec("cmd /c calc")',
            "f = open ('C:/x', 'w')", "import   os", "().__class__.__subclasses__()",
            "pyautogui.moveTo(10, 10)",
        ]:
            with self.subTest(code=code):
                self.assertFalse(desktop.desktop_control({"action": "task", "task": code})["ok"])
                with self.assertRaises(ValidationError):
                    ToolRegistry().validate("desktop_control", {"action": "task", "task": code})


class ExecutorUnknownToolTest(unittest.TestCase):
    def test_unknown_tool_raises_specific_type(self):
        from agent.executor import _call_tool, UnknownToolError
        with self.assertRaises(UnknownToolError):
            _call_tool("does_not_exist_xyz", {}, None)

    def test_known_tool_dispatches(self):
        from agent.executor import _call_tool
        out = _call_tool("system_status", {}, None)
        self.assertIsInstance(out, str)

    def test_newly_wired_tools_dispatch(self):
        # Проверяем маршрутизацию, а не реальные клавиши, сеть или оборудование.
        import json
        from types import SimpleNamespace
        from unittest.mock import patch
        from agent.executor import _call_tool
        from local_mode.tools import ToolRegistry

        def worker(_command, *, payload, on_line, **_kwargs):
            request = json.loads(payload)
            self.assertEqual(request["tool"], tool)
            self.assertEqual(request["args"], params)
            result = ToolRegistry().execute(request["tool"], request["args"], approved=request["approved"])
            on_line(json.dumps({"event": "result", "result": result}))
            return SimpleNamespace(returncode=0)

        for tool, params in [
            ("translate", {"text": "hello"}),
            ("media_control", {"action": "play"}),
            ("pc_health", {"action": "status"}),
        ]:
            with self.subTest(tool=tool), patch.object(ToolRegistry,"_invoke",return_value={"ok":True,"result":"test-dispatch"}) as invoke, patch("local_mode.owned_process.run_owned", side_effect=worker) as process:
                out=_call_tool(tool,params,None)
                self.assertIsInstance(out,str)
                invoke.assert_called_once_with(tool,params)
                if tool == "translate":
                    process.assert_called_once()
                else:
                    process.assert_not_called()





class BridgeTokenTest(unittest.TestCase):
    def test_auto_provisions_token_when_missing(self):
        import tempfile
        import remote.bluetooth_transport as bt
        tmp = Path(tempfile.mkdtemp())
        try:
            old_file = bt.TOKEN_FILE
            old_env = os.environ.get("JARVIS_BRIDGE_TOKEN")
            os.environ.pop("JARVIS_BRIDGE_TOKEN", None)
            bt.TOKEN_FILE = tmp / "bridge_token.txt"
            token = bt.get_bridge_token()
            self.assertGreaterEqual(len(token), 16)
            self.assertTrue((tmp / "bridge_token.txt").exists())
            # Second call returns the same persisted token.
            self.assertEqual(token, bt.get_bridge_token())
            bt.TOKEN_FILE = old_file
            if old_env is not None:
                os.environ["JARVIS_BRIDGE_TOKEN"] = old_env
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_env_token_wins_over_generated(self):
        import tempfile
        import remote.bluetooth_transport as bt
        tmp = Path(tempfile.mkdtemp())
        old_env = os.environ.get("JARVIS_BRIDGE_TOKEN")
        try:
            bt.TOKEN_FILE = tmp / "bridge_token.txt"
            os.environ["JARVIS_BRIDGE_TOKEN"] = "a" * 40
            self.assertEqual(bt.get_bridge_token(), "a" * 40)
            self.assertFalse((tmp / "bridge_token.txt").exists())
            if old_env is not None:
                os.environ["JARVIS_BRIDGE_TOKEN"] = old_env
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
