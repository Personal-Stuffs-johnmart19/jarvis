"""Offline canonical-handler conformance with retained, disposable owned stores.

No inference, network, native UI, watchdog, external processes or user stores.
The report distinguishes executed handlers from unavailable native integrations.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[2]


def artifact(path, root):
    path = Path(path).resolve(strict=True)
    assert path.is_relative_to(root.resolve()), "Artifact escaped the owned fixture root"
    data = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest()}


class Calls:
    def __init__(self, registry, bridge):
        self.registry, self.bridge = registry, bridge
        self.rows = []

    def __call__(self, name, args, *, expect_failure=False):
        started = time.monotonic()
        row = {"handler": name, "category": "dispatch_pending"}
        self.rows.append(row)
        required = self.registry.risky(name, args)
        row["approval_required"] = required
        if required:
            try:
                self.registry.execute(name, args, approved=False)
            except ValueError as error:
                assert "approval" in str(error).lower()
                row["unapproved_rejected"] = True
            else:
                raise AssertionError("Unapproved mutation reached its handler")
        receipt = self.bridge.prepare(self.registry, name, args)
        assert receipt["requires_approval"] == required
        value = self.bridge.execute(self.registry, receipt["token"])
        row["category"] = "executed_real_handler"
        try:
            self.bridge.execute(self.registry, receipt["token"])
        except ValueError as error:
            assert "consumed" in str(error)
            row["receipt_consumed_once"] = True
        else:
            raise AssertionError("Receipt replay executed a second time")
        if expect_failure:
            assert isinstance(value, dict), "Expected a typed handler failure"
        if isinstance(value, dict):
            if expect_failure:
                assert value.get("ok") is False and value.get("error"), "Handler hid an expected failure"
                row["expected_failure_reported"] = True
            else:
                assert not value.get("error") and value.get("ok", True) is not False, "Handler reported failure"
        row.update(ok=True, milliseconds=round((time.monotonic() - started) * 1000, 3))
        return value


def run(output, selected=None):
    output.parent.mkdir(parents=True, exist_ok=True)
    owned = Path(tempfile.mkdtemp(prefix="fixtures-", dir=output.parent)).resolve()
    report = {"scope": "offline canonical handlers; synthetic owned inputs",
        "ok": False, "fixtures": str(owned), "fixtures_retained": True,
        "model_calls": 0, "network_calls": 0, "child_processes_started": 0,
        "selected_cases": selected or "all", "cases": [], "operations": [], "artifacts": []}
    attempts = []

    def forbidden(*_args, **_kwargs):
        attempts.append(True)
        raise RuntimeError("Offline conformance forbids network, inference and process launch")

    try:
        with ExitStack() as stack:
            for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection", "subprocess.Popen"):
                stack.enter_context(patch(target, side_effect=forbidden))
            from local_mode import document_store, local_embeddings, translation, rag_sources, gemini, embeddings, model
            from memory import memory_manager
            from local_mode.background import BackgroundServices
            from local_mode.services import ServiceRegistry
            from local_mode.shared_agent import SharedAgent
            from local_mode.tools import ToolRegistry
            from actions import file_controller, office_builder

            for target in ((gemini, "create_client"), (embeddings, "selected"), (embeddings, "batch_selected"),
                           (model, "generate_content"), (memory_manager, "generate_content"), (file_controller, "generate_content")):
                stack.enter_context(patch.object(*target, side_effect=forbidden))
            stack.enter_context(patch.object(office_builder, "OUT_DIR", owned / "office"))
            stack.enter_context(patch.object(document_store, "ROOT", owned))
            stack.enter_context(patch.object(translation, "ROOT", owned))
            stack.enter_context(patch.object(local_embeddings, "preferences_path", lambda: owned / "embedding-settings.json"))
            stack.enter_context(patch.object(memory_manager, "MEMORY_PATH", owned / "memory.json"))
            conversations = owned / "conversations"
            conversations.mkdir()
            stack.enter_context(patch.object(rag_sources, "_conversations_root", lambda: conversations))
            notifications = []
            background = BackgroundServices(threading.Event(), notifications.append, owned / "schedules.json")
            core = ToolRegistry()
            # Same binding as LocalAgent.__init__; intentionally never start().
            core.special["schedule"] = background.schedule
            runtime = SimpleNamespace(suggestions_path=owned / "suggestions.json", event=lambda *_: None)
            registry = ServiceRegistry(runtime, lambda: core)
            calls = Calls(registry, SharedAgent(lambda *_: None))
            report["operations"] = calls.rows

            def case(name, action):
                row = {"name": name, "category": "executed_real_handler", "ok": False}
                report["cases"].append(row)
                start = time.monotonic()
                try:
                    row["evidence"] = action()
                    row["ok"] = True
                except (ImportError, ModuleNotFoundError) as error:
                    row.update(category="dependency_setup", dependency=error.name)
                except Exception as error:
                    row["failure_type"] = type(error).__name__
                    row["failure_line"] = traceback.extract_tb(error.__traceback__)[-1].lineno
                row["milliseconds"] = round((time.monotonic() - start) * 1000, 3)

            def settings():
                calls("translation.settings", {"backend": "local"})
                assert calls("translation.settings", {})["backend"] == "local"
                calls("embedding.settings", {"backend": "local", "chunk_size": 256, "chunk_overlap": 32})
                value = calls("embedding.settings", {})
                assert (value["backend"], value["chunk_size"], value["chunk_overlap"]) == ("local", 256, 32)
                report["artifacts"].append(artifact(owned / "embedding-settings.json", owned))
                return {"saved_and_reloaded": True, "inference": "not invoked"}

            def documents():
                attachments = owned / ".cache/attachments"
                attachments.mkdir(parents=True)
                upload = attachments / "attachment-owned.txt"
                upload.write_bytes(b"uploaded needle_upload\n")
                uploaded = calls("documents.import", {"path": str(upload), "name": "owned.txt"})
                assert calls("documents.read", {"identifier": uploaded["id"]})["text"] == upload.read_text(encoding="utf-8")
                workspace = owned / "workspace"
                workspace.mkdir()
                source = workspace / "guide.txt"
                source.write_bytes(b"heading\nneedle_workspace\n")
                indexed = calls("documents.import_workspace", {"path": str(source), "workspace": str(workspace)})
                turns = [{"id": "turn-user", "role": "user", "parts": [{"type": "text", "text": "needle_chat"}]},
                    {"id": "turn-terminal", "role": "assistant", "parts": [{"type": "tool-run_code", "toolCallId": "owned-task", "state": "output-available",
                        "output": {"stdout": "needle_terminal\n", "stderr": "", "exit_code": 0}}]}]
                saved = conversations / "session-owned.json"
                saved.write_text(json.dumps(turns), encoding="utf-8")
                chat = calls("documents.import_chat", {"session_id": "owned", "turn_id": "turn-user"})
                terminal = calls("documents.import_terminal", {"session_id": "owned", "task_id": "owned-task", "stream": "stdout"})
                for query, expected in (("needle_upload", uploaded), ("needle_workspace", indexed), ("needle_chat", chat), ("needle_terminal", terminal)):
                    result = calls("documents.search", {"query": query, "semantic": False})
                    assert any(hit["id"] == expected["id"] for hit in result["items"])
                hit = calls("documents.search", {"query": "needle_workspace"})["items"][0]
                citation = hit["citation"]
                text = source.read_text(encoding="utf-8")
                assert hit["excerpt"] == text[citation["start"]:citation["end"]]
                source.write_text("replacement_workspace\n", encoding="utf-8")
                calls("documents.refresh", {"identifier": indexed["id"]})
                assert not calls("documents.search", {"query": "needle_workspace"})["items"]
                calls("documents.remove", {"identifier": uploaded["id"]})
                assert upload.exists() and not calls("documents.search", {"query": "needle_upload"})["items"]
                report["artifacts"].extend([artifact(upload, owned), artifact(source, owned), artifact(saved, owned)])
                return {"lexical_sources": ["upload", "workspace", "saved_chat", "saved_terminal"],
                    "citation_matches_source": True, "refresh_visible": True, "removed_source_preserved": True,
                    "terminal_input": "synthetic saved result; no process executed", "semantic_inference": "not invoked"}

            def memory():
                calls("save_memory", {"category": "notes", "key": "owned_note", "value": "needle_owned_note"})
                assert "needle_owned_note" in json.dumps(calls("recall_memory", {"query": "needle_owned_note", "category": "notes"}))
                assert "needle_owned_note" in json.dumps(calls("data.query", {"source": "memory", "query": "needle_owned_note"}))
                calls("forget_memory", {"key": "owned_note", "category": "notes"})
                assert "owned_note" not in memory_manager.load_memory()["notes"]
                report["artifacts"].append(artifact(owned / "memory.json", owned))
                return {"canonical_note_roundtrip": True, "user_memory_accessed": False}

            def scheduler():
                item = calls("schedule", {"action": "set", "seconds": 60, "message": "owned reminder"})["schedule"]
                assert calls("schedule", {"action": "list"})["schedules"][0]["id"] == item["id"]
                reloaded = BackgroundServices(threading.Event(), notifications.append, owned / "schedules.json")
                reloaded.tick(item["fire_at"] + 1)
                reloaded.tick(item["fire_at"] + 2)
                assert len(notifications) == 1 and reloaded.items[0].get("fired") is True
                # Rebind to the reloaded real scheduler so cancellation also uses canonical dispatch.
                core.special["schedule"] = reloaded.schedule
                calls("schedule", {"action": "cancel", "id": item["id"]})
                assert not calls("schedule", {"action": "list"})["schedules"]
                assert background.thread is None and reloaded.thread is None
                report["artifacts"].append(artifact(owned / "schedules.json", owned))
                return {"reloaded_and_fired_once": True, "cancelled": True,
                    "delivery": "owned callback only; no desktop notification/audio", "watchdog_started": False}

            def files():
                folder = owned / "files"
                folder.mkdir()
                source, copied, renamed = folder / "one.txt", folder / "two.txt", folder / "renamed.txt"
                calls("file_controller", {"action": "create_file", "path": str(source), "content": "owned archive bytes"})
                calls("file_controller", {"action": "copy", "path": str(source), "destination": str(copied)})
                calls("file_controller", {"action": "rename", "path": str(copied), "new_name": renamed.name})
                assert renamed.read_bytes() == source.read_bytes() and not copied.exists()
                archive, extracted = owned / "owned.zip", owned / "extracted"
                calls("file_controller", {"action": "zip", "path": str(folder), "destination": str(archive)})
                calls("file_controller", {"action": "unzip", "path": str(archive), "destination": str(extracted)})
                matches = list(extracted.rglob("renamed.txt"))
                assert len(matches) == 1 and matches[0].read_bytes() == source.read_bytes()
                report["artifacts"].extend([artifact(source, owned), artifact(renamed, owned), artifact(archive, owned), artifact(matches[0], owned)])
                return {"copy_rename_zip_extract_exact_bytes": True,
                    "executor": "actual Python actions.file_controller; does not certify native archive_*"}

            def presentations():
                from pptx import Presentation

                for title in ("", "   "):
                    calls("create_presentation", {"title": title}, expect_failure=True)
                assert not (owned / "office").exists()
                result = calls("create_presentation", {"title": "Owned title",
                    "slides": [{"title": "Owned slide", "bullets": ["Owned bullet"]}]})
                path = owned / "office" / "Owned title.pptx"
                assert result["path"] == str(path)
                deck = Presentation(path)
                assert len(deck.slides) == 2 and deck.slides[0].shapes.title.text == "Owned title"
                assert "Owned bullet" in deck.slides[1].placeholders[1].text
                blocker = owned / "office-blocker"
                blocker.write_text("owned blocker", encoding="utf-8")
                with patch.object(office_builder, "OUT_DIR", blocker):
                    calls("create_presentation", {"title": "Blocked title"}, expect_failure=True)
                assert blocker.read_text(encoding="utf-8") == "owned blocker"
                report["artifacts"].extend([artifact(path, owned), artifact(blocker, owned)])
                return {"readable_owned_presentation": True, "missing_title_rejected": True,
                    "save_failure_reported": True, "failed_attempts_created_no_presentation": True}

            for name, action in (("settings", settings), ("documents_lexical_rag", documents),
                                 ("memory_notes", memory), ("in_app_scheduler", scheduler), ("canonical_file_archives", files), ("canonical_presentations", presentations)):
                if selected is None or name in selected:
                    case(name, action)
            report["cases"].extend([
                {"name": "native_archive_and_utility", "category": "dependency_setup", "reason": "Requires actual AppHandle/native execution; no Python substitute counted"},
                {"name": "mcp_transport", "category": "dependency_setup", "reason": "Not invoked by this runner; native/server transport checks are separate evidence"}])
            report["forbidden_attempts"] = len(attempts)
            executed = [case for case in report["cases"] if case["category"] == "executed_real_handler"]
            report["ok"] = len(executed) == (len(selected) if selected else 6) and all(case["ok"] for case in executed) and not attempts
    except Exception as error:
        report["failure_type"] = type(error).__name__
    finally:
        report["artifacts"] = [artifact(path, owned) for path in sorted(owned.rglob("*")) if path.is_file()]
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": report["ok"], "executed_cases": sum(case.get("ok", False) for case in report["cases"]),
            "operations": len(report["operations"]), "report": str(output)}, ensure_ascii=False))
    return 0 if report["ok"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", dest="cases", action="append", choices=("settings", "documents_lexical_rag", "memory_notes", "in_app_scheduler", "canonical_file_archives", "canonical_presentations"),
        help="Run only this owning sequence; repeat to select several")
    args = parser.parse_args()
    output = args.output or ROOT / ".cache/verification/service-conformance" / uuid.uuid4().hex / "report.json"
    return run(output.resolve(), list(dict.fromkeys(args.cases)) if args.cases else None)


if __name__ == "__main__":
    raise SystemExit(main())
