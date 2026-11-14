"""Synthetic OpenAI-compatible QA by default; no personal browser/audio operations.
Real selected-helper inference requires an explicit provider and --live.
"""

import json
import os
import re
import socket
import time
import runpy
import threading
import uuid
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from qa_selected_helper import selected_helper_options

helper_options = selected_helper_options(sys.argv[1:])

calls = []
recoveries = []
editor_context_requests = []
child_started = threading.Event()
child_cancelled = threading.Event()
lifecycle = {"lmstudio": True, "ollama": True}
reasoning_context = "--reasoning-context-only" in sys.argv
fresh_context = "--fresh-context-only" in sys.argv or reasoning_context
fresh_stats = {"requests": 0, "payload_bytes": []}
selected_helper = helper_options.selected_helper_only
helper_stats = {"requests": 0}
queued_task = "--queued-task-only" in sys.argv
queued_plan = "--queued-plan-only" in sys.argv
if queued_plan and (not selected_helper or queued_task):
    raise SystemExit("--queued-plan-only requires --selected-helper-only and cannot combine with --queued-task-only")
if queued_task and (selected_helper or helper_options.selected_helper_provider or helper_options.live):
    raise SystemExit("--queued-task-only is synthetic and cannot be combined with live helper options")
queued_stats = {"requests": 0, "kinds": []}
pc_status = "--pc-status-only" in sys.argv
if pc_status and (selected_helper or queued_task or queued_plan or helper_options.live or fresh_context):
    raise SystemExit("--pc-status-only is a separate synthetic shared-chat fixture")
from qa_pc_status import pc_status_stats, emit_pc_status_response
pc_context = int(os.environ.get("JARVIS_QA_PC_CONTEXT", "32768"))
pc_stats = pc_status_stats(pc_context)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if pc_status and self.path == "/qa/pc-status":
            body = json.dumps(pc_stats).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if (queued_task or queued_plan) and self.path == "/qa/queued-task":
            body = json.dumps(queued_stats).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if selected_helper and self.path == "/qa/selected-helper":
            body = json.dumps(helper_stats).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if fresh_context and self.path == "/qa/fresh-context":
            body = json.dumps(fresh_stats).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in {"/stt-ready/health", "/stt-loading/health"}:
            ready = self.path == "/stt-ready/health"
            body = json.dumps({"status": "ok" if ready else "loading model"}).encode()
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/v1/models":
            if pc_status:
                body = json.dumps({"models": [{"key": "google/gemma-4-e4b", "type": "llm", "display_name": "Owned QA Model",
                    "max_context_length": 32768, "loaded_instances": [{"id": "qa-pc-status", "config": {"context_length": pc_context}}]}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if fresh_context:
                body = json.dumps({"models": [{"key":"google/gemma-4-e4b","type":"llm","display_name":"QA Gemma","max_context_length":131072,"loaded_instances":([{"id":"qa-gemma","config":{"context_length":8192 if reasoning_context else 4096}}] if reasoning_context or fresh_stats["requests"] else [])}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = json.dumps({"models": [{"key": "google/gemma-4-e4b", "type": "llm", "display_name": "QA Gemma", "max_context_length": 32768, "loaded_instances": ([{"id": "qa-gemma", "config": {"context_length": 8192}}] if lifecycle["lmstudio"] else [])}, {"key": "qa/unloaded-chat", "type": "llm", "display_name": "QA Unloaded Chat", "max_context_length": 16384, "loaded_instances": []}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in {"/api/tags", "/api/ps"}:
            rows = [{"model": "qa-loaded", "context_length": 8192}] if lifecycle["ollama"] or self.path == "/api/tags" else []
            if self.path == "/api/tags":
                rows += [{"model": "qa-installed-unloaded"}]
            body = json.dumps({"models": rows}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/qa/cancel":
            body = json.dumps({"started": child_started.is_set(), "cancelled": child_cancelled.is_set()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps(
            {"data": [{"id": "google/gemma-4-e4b", "object": "model"}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path in {"/api/v1/models/unload", "/api/generate"}:
            if self.path == "/api/v1/models/unload":
                assert payload == {"instance_id": "qa-gemma"}, payload
                lifecycle["lmstudio"] = False
                reply = {"instance_id": "qa-gemma"}
            else:
                assert payload == {"model":"qa-loaded", "prompt":"", "keep_alive":0, "stream":False}, payload
                lifecycle["ollama"] = False
                reply = {"model":"qa-loaded", "done":True, "done_reason":"unload"}
            body = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        assert all(
            t["function"]["parameters"].get("type") == "object"
            for t in payload.get("tools", [])
        ), "Provider tool schemas must have object roots"
        assert payload["model"] == "google/gemma-4-e4b", payload["model"]
        if pc_status:
            emit_pc_status_response(self, payload, pc_stats)
            return
        if queued_task or queued_plan:
            from qa_queued_task import queued_task_response
            queued_stats["requests"] += 1
            assert queued_stats["requests"] <= (1 if queued_plan else 3), "Queued fixture inference budget exceeded"
            assert not payload.get("stream", False) and not payload.get("tools")
            reply = queued_task_response(payload)
            queued_stats["kinds"].append(reply["kind"])
            body = json.dumps({"id":"qa-queued","object":"chat.completion","created":1,
                "model":payload["model"],"choices":[{"index":0,"message":{"role":"assistant","content":reply["content"]},"finish_reason":"stop"}],
                "usage":{"prompt_tokens":23,"completion_tokens":7,"total_tokens":30,"prompt_tokens_details":{"cached_tokens":3}}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if selected_helper:
            helper_stats["requests"] += 1
            assert helper_stats["requests"] == 1, "Selected helper must make exactly one inference request"
            assert self.path == "/v1/chat/completions" and not payload.get("stream", False)
            assert not payload.get("tools"), "Stateless helper must not advertise tools"
            messages = payload["messages"]
            assert [m["role"] for m in messages] == ["system", "user"], "Helper must not include chat history"
            assert all(isinstance(m["content"], str) for m in messages)
            assert "print(6*7)" in messages[1]["content"] and "Привіт" in messages[1]["content"]
            characters = sum(len(m["content"]) for m in messages)
            assert characters < 2000, "Parent agent prompt leaked into the stateless helper"
            assert 1 <= payload.get("max_tokens", payload.get("max_completion_tokens", 0)) <= 4096
            helper_stats.update(tools=0, message_count=len(messages), message_characters=characters,
                payload_bytes=len(json.dumps(payload).encode()))
            body = json.dumps({"id":"qa-helper","object":"chat.completion","created":1,
                "model":payload["model"],"choices":[{"index":0,"message":{"role":"assistant","content":"HELPER_EXPLAINED_42"},"finish_reason":"stop"}],
                "usage":{"prompt_tokens":23,"completion_tokens":7,"total_tokens":30,"prompt_tokens_details":{"cached_tokens":3}}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if fresh_context:
            names = {item["function"]["name"] for item in payload.get("tools", [])}
            assert {"read_file", "jarvis_catalog", "jarvis_action", "browser_control", "weather_report"} <= names
            assert names <= {"read_file", "jarvis_catalog", "jarvis_action", "browser_control", "weather_report"}, names
            expected_reply = 1228 if reasoning_context else 614
            assert payload.get("max_tokens", payload.get("max_completion_tokens", 0)) == expected_reply
            fresh_stats["requests"] += 1
            fresh_stats["payload_bytes"].append(len(json.dumps(payload).encode()))
        messages = payload["messages"]
        last = next(m for m in reversed(messages) if m["role"] == "user")
        content = last["content"]
        text = (
            content
            if isinstance(content, str)
            else "\n".join(p.get("text", "") for p in content)
        )
        fresh = messages[messages.index(last) + 1 :]
        tools = [m for m in fresh if m["role"] == "tool"]
        action = None
        args = {}
        answer = (
            "WORKBENCH_MIRROR_READY"
            if "WORKBENCH_MIRROR_READY" in text
            else "JARVIS_SHARED_READY"
        )
        reasoning_turn = reasoning_context and text.rstrip().endswith("123")
        if reasoning_turn:
            if len(tools) == 0:
                action = "read_file"
                args = {"path":str(Path(__file__).resolve().parents[2]/".cache/desktop-test/shared-agent/read.txt")}
            elif len(tools) == 1:
                action = "jarvis_catalog"
                args = {"query":"read_file","category":"","cursor":0,"limit":8}
            else:
                assert len(tools) == 2, tools
                reasoning = [m.get("reasoning_content", "") for m in fresh if m["role"] == "assistant"]
                assert [len(item) for item in reasoning if item] == [3089,3169], reasoning
        elif "CANCEL_CHILD_QA" in text:
            # Keep a child generateText response open until cancellation closes the socket.
            body = json.dumps({"id":"cancel-child","object":"chat.completion","created":1,"model":payload["model"],"choices":[{"index":0,"message":{"role":"assistant","content":"LATE_CHILD_REPLY"},"finish_reason":"stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body[:1]); self.wfile.flush()
            child_started.set()
            self.connection.settimeout(0.1)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    if not self.connection.recv(1):
                        child_cancelled.set()
                        return
                except socket.timeout:
                    continue
                except OSError:
                    child_cancelled.set()
                    return
            self.wfile.write(body[1:]); self.wfile.flush()
            return
        elif "MCP_SHARED_QA:" in text:
            spec = json.loads(text.split("MCP_SHARED_QA:", 1)[1])
            action = "jarvis_action"
            args = {"name": "mcp_call", "args": spec}
            answer = "MCP_SHARED_QA_OK"
            if tools:
                content = tools[-1]["content"]
                if isinstance(content, list):
                    assert all(part.get("type") == "text" for part in content), content
                    content = "\n".join(part["text"] for part in content)
                assert isinstance(content, str) and len(content) <= 12500, content
                assert "MCP tool reported an error." not in content, content
                assert "Full structured output is saved under tool call " in content, content
                assert "Use tool_result_read with tool_call_id" in content, content
                if spec["tool_name"] == "echo":
                    assert spec['arguments']['text'] in content, content
        elif "QA_EDITOR_CONTEXT" in text:
            assert '"source":"editor_buffer"' in text, "Live editor buffer was omitted"
            assert "XML_EDITOR_UNSAVED_MARKER" in text, "Unsaved editor text was omitted"
            assert not tools, "Simple editor question must not issue tool calls"
            editor_context_requests.append(len(text))
            Path(__file__).resolve().parents[2].joinpath(".cache/verification/editor-request-fixture.json").write_text(json.dumps(payload),encoding="utf8")
            answer = "EDITOR_CONTEXT_OK XML_EDITOR_UNSAVED_MARKER"
        elif "REPEATED_READ_FAILURE_QA" in text:
            assert len(tools) <= 2, "Read failure loop exceeded one retry"
            if len(tools) < 2:
                action = "read_file"
                args = {"path":str(Path(__file__).resolve().parents[2]/".cache/desktop-test/shared-agent/missing-loop-qa.xml")}
            else:
                assert not payload.get("tools"), "Final failure explanation must not expose tools"
                assert "File lookup made no progress" in json.dumps(messages)
                answer = "READ_LOOP_BOUNDED_QA"
        elif "STOP_PROCESS_QA:" in text:
            spec = json.loads(text.split("STOP_PROCESS_QA:", 1)[1])
            action = "jarvis_action"
            args = spec
            answer = "UNEXPECTED_PROCESS_COMPLETION"
        elif "CANCEL_PARENT_QA" in text:
            action = "jarvis_action"
            args = {"name":"run_subagent", "args":{"type":"explore","prompt":"CANCEL_CHILD_QA","description":"Cancel propagation"}}
        elif "SUBAGENT_CHILD_QA" in text:
            answer = "SUBAGENT_CHILD_QA_OK"
        elif "SUBAGENT_PROVIDER_QA" in text:
            action = "jarvis_action"
            args = {"name": "run_subagent", "args": {"type": "explore", "prompt": "Reply SUBAGENT_CHILD_QA_OK without tools.", "description": "Provider regression"}}
            answer = "SUBAGENT_PROVIDER_QA_OK"
            if tools:
                value = json.loads(tools[-1]["content"])
                assert "SUBAGENT_CHILD_QA_OK" in value.get("summary", ""), value
        elif "RECOVER_TOOL_RESULT_QA" in text:
            previous = next(call for m in reversed(messages[:messages.index(last)]) if m['role']=='assistant' for call in m.get('tool_calls',[]) if call['function']['name']=='jarvis_action' and json.loads(call['function']['arguments']).get('name')=='run_code' and '6*7' in json.dumps(call['function']['arguments']))
            action = 'jarvis_action'
            args = {'name':'tool_result_read','args':{'tool_call_id':previous['id'],'offset':0,'limit':2000}}
            answer = 'RECOVER_TOOL_RESULT_QA_OK'
            if tools:
                value=json.loads(tools[-1]['content'])
                assert '42' in value['text'] and value['next_offset'] is None,value
        elif "UTILITY_QA:" in text:
            spec = json.loads(text.split("UTILITY_QA:", 1)[1])
            action = "jarvis_action"
            args = {"name": spec["name"], "args": spec["args"]}
            answer = "UTILITY_QA_OK"
            if tools:
                value = json.loads(tools[-1]["content"])
                assert not value.get("error"), value
                if spec["name"] == "utility_catalog":
                    assert any(u["id"] == "python" and u["direct"] for u in value["utilities"]), value
                elif spec["name"] == "run_code":
                    assert value["stdout"].strip() == "42", value
                elif spec["name"] == "bash_run":
                    assert value["timed_out"] is True, value
                elif spec["name"] == "utility_run" and spec["args"].get("tool") == "agy":
                    assert value["exit_code"] == 0 and "JARVIS_ARGON_OK" in value["stdout"], value
                elif spec["name"] == "archive_list":
                    assert value["total_entries"] >= 1, value
        elif "RAG_TERMINAL_IMPORT_QA:" in text:
            spec = json.loads(text.split("RAG_TERMINAL_IMPORT_QA:", 1)[1])
            action = "jarvis_action"
            args = {"name": "documents.import_terminal", "args": spec}
            answer = "RAG_TERMINAL_IMPORT_OK"
            if tools:
                value = json.loads(tools[-1]["content"])
                source = value["source"]
                assert source["source_kind"] == "terminal", value
                assert source["session_id"] == spec["session_id"] and source["task_id"] == spec["task_id"], value
                assert source["stream"] == spec["stream"], value
        elif "RAG_WORKSPACE_IMPORT_QA" in text:
            action = "jarvis_action"
            folder = Path(__file__).resolve().parents[2] / ".cache/desktop-test/shared-agent"
            args = {"name":"documents.import_workspace","args":{"path":str(folder/"read.txt"),"workspace":str(folder)}}
            answer = "RAG_WORKSPACE_IMPORT_OK"
            if tools:
                value=json.loads(tools[-1]["content"])
                assert value["source"]["source_kind"]=="workspace" and value["source"]["path"].endswith("read.txt"),value
        elif "read.txt" in text:
            action = "read_file"
            args = {
                "path": str(
                    Path(__file__).resolve().parents[2]
                    / ".cache/desktop-test/shared-agent/read.txt"
                )
            }
            if tools:
                answer = json.loads(tools[-1]["content"])["content"]
        elif "SHARED_APPROVED_WRITE" in text:
            action = "jarvis_action"
            args = {
                "name": "write_file",
                "args": {
                    "path": re.search(r"файл (.*?) с содержимым", text).group(1),
                    "content": "SHARED_APPROVED_WRITE",
                },
            }
            if tools:
                answer = "SHARED_APPROVED_WRITE"
        elif "SUGGESTIONS_SERVICE_QA" in text:
            action = "jarvis_action"
            args = {
                "name": "suggestions.save",
                "args": {"items": [["SUGGESTIONS_SERVICE_QA", "Show capabilities"]]},
            }
            answer = "SUGGESTIONS_SERVICE_QA"
        elif "data_query" in text:
            action = "data_query"
            args = {"source": "suggestions", "limit": 2}
            answer = "SHARED_DATA_READY"
        elif "jarvis_catalog" in text:
            action = "jarvis_catalog"
            args = {"query": "translation.run"}
            answer = "translation.run requires approval"
        if not payload.get("stream", False):
            assert "SUBAGENT_CHILD_QA" in text, text
            body = json.dumps({"id": "qa-child", "object": "chat.completion", "created": 1,
                "model": payload["model"], "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(delta, reason=None):
            item = {
                "id": "qa-" + uuid.uuid4().hex,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "google/gemma-4-e4b",
                "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
            }
            self.wfile.write(("data: " + json.dumps(item) + "\n\n").encode())
            self.wfile.flush()

        retry_invalid = (
            action == "jarvis_action"
            and "SUGGESTIONS_SERVICE_QA" in text
            and bool(tools)
            and "error" in json.loads(tools[-1]["content"])
        )
        if action == "jarvis_action" and "SUGGESTIONS_SERVICE_QA" in text and not tools:
            args["name"] = "missing_service_for_qa"
        if retry_invalid:
            recoveries.append(True)
        if action and (not tools or retry_invalid or "REPEATED_READ_FAILURE_QA" in text or reasoning_turn):
            calls.append(action)
            if reasoning_turn:
                emit({"role":"assistant","reasoning_content":"r" * (3089 if not tools else 3169)})
            emit(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_" + uuid.uuid4().hex,
                            "type": "function",
                            "function": {"name": action, "arguments": json.dumps(args)},
                        }
                    ],
                }
            )
            emit({}, "tool_calls")
        else:
            if tools and "REPEATED_READ_FAILURE_QA" not in text and "MCP_SHARED_QA:" not in text:
                value = json.loads(tools[-1]["content"])
                assert not (isinstance(value, dict) and value.get("error")), value
                if action == "data_query":
                    assert "items" in value, value
                if action == "jarvis_catalog":
                    assert any(
                        i["name"] == "translation.run" for i in value["items"]
                    ), value
            emit({"role": "assistant", "content": "" if action=="jarvis_action" and "SHARED_APPROVED_WRITE" in text else answer})
            emit({}, "stop")
        self.wfile.write(("data: " + json.dumps({"id":"usage", "object":"chat.completion.chunk", "model":"google/gemma-4-e4b", "created":1, "choices":[], "usage":{"prompt_tokens":123,"completion_tokens":17,"total_tokens":140,"prompt_tokens_details":{"cached_tokens":11}}}) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["JARVIS_QA_MODEL_BASE"] = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        runpy.run_path(
            str(Path(__file__).with_name("qa_ecosystem.py")), run_name="__main__"
        )
        assert {"read_file", "jarvis_action", "data_query", "jarvis_catalog"} <= set(
            calls
        ), calls
        assert len(editor_context_requests) == 1, editor_context_requests
        assert child_cancelled.is_set(), "Stopped subagent HTTP request must close promptly"
        assert recoveries, (
            "Invalid actions must return feedback and allow a corrected approved action"
        )
        print(
            json.dumps(
                {
                    "synthetic_model_protocol": True,
                    "tool_calls": calls,
                    "invalid_action_recovered": True,
                }
            )
        )
    finally:
        server.shutdown()
        server.server_close()
