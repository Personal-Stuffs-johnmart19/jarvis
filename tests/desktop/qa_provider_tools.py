"""Explicit, bounded real-provider QA in an owned hidden Jarvis host.

No provider calls, application launch, or credential reads without --live.
Run providers separately; never import another QA module with launch side effects.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import uuid
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
MAX_REQUESTS = 8
SCENARIO_SECONDS = 90
QA_QUERY = "qa-provider"
QA_RESULT = "SYNTHETIC_QA_RESULT"
QA_OUTPUT = "QA_DONE"


def safe_diagnostics(audit):
    """Persist structural evidence, excluding all prompts, headers, arguments and text."""
    parts = [p for m in audit.get("messages", []) for p in m.get("parts", [])
             if p.get("type", "").startswith("tool-")]
    calls = []
    for part in parts:
        value = part.get("input")
        action = value.get("name") if isinstance(value, dict) else None
        output = part.get("output")
        calls.append({"id": part.get("toolCallId"), "tool": part["type"][5:],
            "action": action, "state": part.get("state"),
            "error_present": bool(part.get("errorText")) or isinstance(output, dict) and bool(output.get("error"))})
    return {"model_requests": audit.get("requests", 0), "blocked_operations": audit.get("blocked", 0),
        "native_executions": audit.get("executions", 0), "tools": calls,
        "session_saves": audit.get("saves", 0), "durable_session_saves": audit.get("durableSaves", 0),
        "wire": audit.get("wire", []), "refusals": audit.get("refusals", [])}


def error_category(text):
    lowered = text.casefold()
    for category, words in (("request_budget", ("model request budget", "request budget exhausted", "request limit")),
                            ("provider_protocol", ("nativeformat", "native format", "jsonparse", "json parse", "parse json", "invalid json", "json parsing", "failed to parse")),
                            ("context_budget", ("context", "budget", "oversized")),
                            ("authorization", ("approval", "refused", "unauthorized")),
                            ("deadline", ("timeout", "deadline")),
                            ("provider", ("429", "quota", "provider", "http"))):
        if any(word in lowered for word in words):
            return category
    return "other"


def scenario(folder: Path, token: str):
    source = folder / "read.txt"
    target = folder / "output.txt"
    sentinel = "HELLO Привіт " + token
    code = "from pathlib import Path\nPath('output.txt').write_text('QA_DONE', encoding='utf-8')\nprint(6*7)"
    prompt = (
        f"Read {source.as_posix()} with read_file. "
        "Discover data_query and run_code using jarvis_catalog. "
        f"Query data_query with source='suggestions', query='{QA_QUERY}', limit=1. "
        f"Then run exactly this Python in cwd='{folder.as_posix()}' after approval:\n{code}\n"
        "Report the exact read text, suggestion prompt and Python stdout. "
        "Do not use shell, browser, audio, other code, or modify other files."
    )
    return source, target, sentinel, code, prompt


def validate_approval(args, folder, code):
    # Some hosts expose jarvis_action's wrapper; current cards render actualInput.
    # Accept exactly that one known wrapper, never approval/control fields.
    if isinstance(args, dict) and "name" in args:
        if set(args) != {"name", "args"} or args["name"] != "run_code":
            return False
        args = args["args"]
    return (isinstance(args, dict) and set(args) <= {"language", "code", "cwd", "timeout_secs", "args"}
            and args.get("language") == "python" and args.get("code") == code
            and Path(args.get("cwd", "")).resolve() == folder.resolve()
            and not args.get("args")
            and ("timeout_secs" not in args or type(args["timeout_secs"]) is int
                 and 0 < args["timeout_secs"] <= 30))


def python_prompt(folder, code):
    action = {"name": "run_code", "args": {"language": "python", "code": code,
        "cwd": folder.as_posix()}}
    return ("Call jarvis_action once with exactly this JSON input; wait for approval, "
        "then report the actual Python stdout. Do not call any other tool:\n"
        + json.dumps(action, ensure_ascii=False))


def session_usage(page):
    return page.evaluate("""async()=>{
        const s=await window.__JARVIS_NATIVE__.store.load('terax-ai-sessions.json',{defaults:{}});
        const id=await s.get('activeId');
        return (await s.get('sessions'))?.find(x=>x.id===id)?.usage??null;
    }""")


def result_evidence(audit, target, sentinel, only_python):
    messages = audit.get("messages", [])
    parts = [p for m in messages for p in m.get("parts", []) if p.get("type", "").startswith("tool-")]
    python_outputs = [p.get("output") for p in parts if p.get("type") == "tool-run_code"
        or isinstance(p.get("input"), dict) and p["input"].get("name") == "run_code"]
    python_ok = any(isinstance(value, dict) and value.get("stdout", "").strip() == "42"
        and value.get("exit_code") == 0 and not value.get("timed_out") for value in python_outputs)
    try:
        file_ok = target.read_text(encoding="utf-8") == QA_OUTPUT
    except (OSError, UnicodeError):
        file_ok = False
    verified = {"python_stdout": "42" if python_ok else None, "owned_file_exact": file_ok}
    if not only_python:
        outputs = json.dumps([p.get("output") for p in parts], ensure_ascii=False)
        verified.update(bilingual_read=sentinel in outputs, synthetic_data=QA_RESULT in outputs)
    assistant = next((m for m in reversed(messages) if m.get("role") == "assistant"), {})
    last_parts = assistant.get("parts", [])
    last_tool = max((i for i, p in enumerate(last_parts) if p.get("type", "").startswith("tool-")), default=-1)
    final_chars = sum(len(p.get("text", "").strip()) for p in last_parts[last_tool + 1:]
        if p.get("type") == "text" and p.get("state") in (None, "done"))
    return {"verified_results": verified, "final_assistant_text_characters": final_chars}


def ui_error_evidence(frame):
    failed = frame.locator('div.text-destructive').filter(has_text="Request failed.")
    errors = frame.locator('[role="alert"], [data-error="true"]').all_text_contents() + failed.all_text_contents()
    return {"ui_request_failed": failed.count() > 0,
        "ui_errors": [{"category": error_category(text), "characters": len(text)} for text in errors]}


AUDIT = """options => {
 const host=window.__JARVIS_NATIVE__, native=host.core;
 const audit=window.__providerQA={requests:0,messages:[],blocked:0,approved:false,executions:0,requestIds:[],saves:0,durableSaves:0,lastSave:0,wire:[],refusals:[]};
 const norm=p=>{let n=String(p??'').replaceAll(String.fromCharCode(92),'/').toLowerCase();if(n.startsWith('//?/'))n=n.slice(4);return n.endsWith('/')?n.slice(0,-1):n;};
 const owned=p=>{const n=norm(p);return !n.split('/').includes('..') && (n===norm(options.folder)||n.startsWith(norm(options.folder)+'/'));};
 host.core={...native,invoke:async(command,args,extra)=>{
   const label=value=>typeof value==='string'&&/^[a-zA-Z0-9_.:|-]{1,128}$/.test(value)?value:null;
   const refuse=()=>{audit.blocked++;
     if(audit.refusals.length<20)audit.refusals.push({command:label(command),method:label(args?.method),service:label(args?.params?.name)});
     throw Error('QA refused an unexpected native operation');};
   if(options.onlyPython && command==='fs_read_file')refuse();
   if(command.startsWith('fs_') && !['fs_canonicalize','fs_read_file','fs_read_dir'].includes(command)) refuse();
   if(['fs_canonicalize','fs_read_file','fs_read_dir'].includes(command) && !owned(args.path)) refuse();
   if(/^(shell_|browser_|chrome_|mcp_|screen_|git_)/.test(command) && command!=='shell_run_cancel') refuse();
   if(command==='utility_run') {
     if(!audit.approved || audit.executions || args.tool!=='python' || norm(args.cwd)!==norm(options.folder)
        || JSON.stringify(args.args)!==JSON.stringify(['-c',options.code]) || args.timeoutSecs>30) refuse();
     audit.executions++;
   }
   if(command==='backend') {
     const allowed=['services.catalog','services.register_workspace','snapshot','services.read',
       'agent.activity','conversation.sync','agent.attach','agent.detach','agent.complete','agent.ui_speech'];
     if(!allowed.includes(args.method)) refuse();
     if(options.onlyPython && args.method==='services.catalog')refuse();
     if(args.method==='services.read') {
       const p=args.params??{}, a=p.args??{};
       if(p.name==='runtime.status') {if(Object.keys(a).length)refuse();}
       else if(p.name==='data.query') {
         if(options.onlyPython)refuse();
         if(a.source!=='suggestions'||a.query!==options.query||a.limit!==1||(a.cursor??0)!==0
            ||Object.keys(a).some(k=>!['source','query','limit','cursor'].includes(k)))refuse();
       } else refuse();
     }
     if(args.method==='agent.ui_speech' && !['begin','end'].includes(args.params.phase))refuse();
   }
   if(command==='ai_http_stream') {
     const meta=JSON.parse(extra.headers['x-terax-ai-request']);
     audit.requestIds.push(meta.requestId);
     const u=new URL(meta.url);
     if(!['127.0.0.1','localhost'].includes(u.hostname)) throw Error('QA forbids remote native transport');
     if(meta.method==='POST') {
       if(audit.requests>=options.max || performance.now()>options.deadline) {
         audit.blocked++; throw Error('QA model request budget exhausted');
       }
       audit.requests++;
       const bytes=args instanceof ArrayBuffer?new Uint8Array(args):args;
       const body=JSON.parse(new TextDecoder().decode(bytes));
       const messages=body.messages??body.contents??[];
       const schemas=body.tools??[];
       const system=body.systemInstruction??body.instructions??null;
       audit.wire.push({body_bytes:bytes.byteLength,
         instructions_chars:system===null?messages.filter(m=>m.role==='system').reduce((n,m)=>n+JSON.stringify(m.content).length,0):JSON.stringify(system).length,
         schema_count:schemas.length,schema_bytes:new TextEncoder().encode(JSON.stringify(schemas)).byteLength,
         message_count:messages.length,
         messages:messages.map(m=>({role:m.role??'unknown',chars:JSON.stringify(m).length}))});
     }
   }
   if(command==='ai_session_save') {
     const bytes=args instanceof ArrayBuffer?new Uint8Array(args):args;
     audit.messages=JSON.parse(new TextDecoder().decode(bytes));
     const metadata=JSON.parse(extra.headers['x-terax-session']);
     const result=await native.invoke(command,args,extra);
     audit.saves++; if(metadata.durable)audit.durableSaves++;
     audit.lastSave=performance.now();return result;
   }
   return native.invoke(command,args,extra);
 }};
}"""


def run(args):
    import httpx
    from playwright.sync_api import sync_playwright
    from qa_locale import select_language

    profile = uuid.uuid4().hex
    token = profile[:8]
    folder = ROOT / ".cache/desktop-test" / ("p-" + token)
    folder.mkdir(parents=True)
    source, target, sentinel, code, prompt = scenario(folder, token)
    if args.only_python:
        prompt = python_prompt(folder, code)
    budget = 3 if args.only_python else MAX_REQUESTS
    if not args.only_python:
        source.write_text(sentinel, encoding="utf-8")
    output = args.output or folder / "report.json"
    # Only the dedicated QA suggestion file is touched, restored in finally.
    suggestions = ROOT / ".cache/desktop-test/quick-suggestions.json"
    old_suggestions = None
    suggestions_lock = suggestions.with_name(".provider-suggestions.lock")
    fixture_locked = False
    fixture_saved = False
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "JARVIS_ROOT": str(ROOT), "JARVIS_DESKTOP_TEST": "1",
           "JARVIS_QA_PROFILE": profile,
           "JARVIS_QA_ALLOW_CLOUD": "1" if args.provider == "google" else "0",
           "JARVIS_QA_MAX_CLOUD_CALLS": str(budget),
           "JARVIS_AUTO_FALLBACK": "0",
           "WEBVIEW2_USER_DATA_FOLDER": str(folder / "webview"),
           "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS":
               f"--remote-debugging-port={port} --remote-debugging-address=127.0.0.1"}
    manifest = json.loads((ROOT / "desktop-bin/manifest.json").read_text(encoding="utf-8-sig"))
    report = {"provider": args.provider, "only_python": args.only_python, "ok": False, "max_model_requests": budget}
    process = None
    log = (folder / "native.log").open("w", encoding="utf-8")
    page = None
    playwright = None
    started = None
    approvals = 0
    late_failure = False
    try:
        if not args.only_python:
            with suggestions_lock.open("x", encoding="ascii") as lock:
                lock.write(str(os.getpid()))
            fixture_locked = True
            old_suggestions = suggestions.read_bytes() if suggestions.exists() else None
            fixture_saved = True
            suggestions.write_text(json.dumps({"version": 1, "items": [
                {"label": QA_QUERY, "prompt": QA_RESULT}]}), encoding="utf-8")
        expected_profile = "com.mrcrous.jarvis.qa." + profile
        report["qa_isolation"] = {"profile": expected_profile, "identity_verified": False}
        process = subprocess.Popen([str(ROOT / "desktop-bin" / manifest["file"])],
            cwd=ROOT, env=env, stdout=log, stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        playwright = sync_playwright().start()
        pw = playwright
        ready = time.monotonic() + 25
        while time.monotonic() < ready:
            assert process.poll() is None, "Owned QA host exited"
            try:
                if httpx.get(url + "/json/version", timeout=.3, trust_env=False).is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        browser = pw.chromium.connect_over_cdp(url)
        page = browser.contexts[0].pages[0]
        page.wait_for_url("**tauri.localhost/**", timeout=25000)
        if not args.bundled_assets:
            def assets(route):
                base = (ROOT / "apps/desktop/dist").resolve()
                path = (base / unquote(urlsplit(route.request.url).path).lstrip("/")).resolve()
                if path == base:
                    path = base / "index.html"
                if path.is_relative_to(base) and path.is_file():
                    route.fulfill(path=str(path))
                else:
                    route.continue_()
            page.route("http://tauri.localhost/**", assets)
        page.reload()
        page.get_by_role("button", name="Chat", exact=True).wait_for()
        # Installed Tauri path API + existing permission; no new identity command.
        config_path = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('plugin:path|resolve_directory',{directory:13})")
        assert Path(config_path).name == expected_profile, "Host lacks isolated QA profile support; rebuild before live QA"
        report["qa_isolation"]["identity_verified"] = True
        # Obtain only model metadata. Never return or print the bridge token/base.
        cloud_model = page.evaluate("""async()=>{
            const b=await window.__JARVIS_NATIVE__.core.invoke('backend',{method:'workbench.bootstrap',params:{}});
            return b.model;
        }""") if args.provider == "google" else None
        model = cloud_model if cloud_model else "lmstudio-local"
        page.evaluate("""async options=>{
            const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});
            for(const[k,v]of Object.entries({defaultModelId:options.model,lmstudioBaseURL:'http://127.0.0.1:1234/v1',lmstudioModelId:'gpt-oss-20b',lmstudioReasoning:'off'}))await s.set(k,v);
            await s.save();
        }""", {"model": model})
        page.reload()
        select_language(page, "en")
        page.get_by_role("button", name="Chat", exact=True).click()
        frame = page.frame_locator('iframe[src="workbench.html"]')
        frame.locator("[data-unified-chat]").wait_for()
        picker = frame.locator('button[title="Switch session"]:visible')
        if picker.count():
            picker.click()
            frame.get_by_role("menuitem", name="New session", exact=True).click()
        expected_model = cloud_model or "gpt-oss-20b"
        for _ in range(50):
            active = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'snapshot',params:{}})")
            if active["active_model"] == expected_model:
                break
            page.wait_for_timeout(100)
        assert active["active_model"] == expected_model, "Configured model did not become active"
        page.evaluate("path=>window.__JARVIS_NATIVE__.core.invoke('workspace_authorize',{path,workspace:{kind:'local'}})", str(folder))
        page.evaluate(AUDIT, {"max": budget, "deadline": page.evaluate("performance.now()") + SCENARIO_SECONDS * 1000,
            "folder": str(folder), "code": code, "query": QA_QUERY, "onlyPython": args.only_python})
        started = time.monotonic()
        frame.locator("[data-shared-composer] textarea").fill(prompt)
        frame.get_by_role("button", name="Send", exact=True).click()
        settled_at = None
        last_saves = -1
        while time.monotonic() - started < SCENARIO_SECONDS:
            approve = frame.get_by_role("button", name="Approve", exact=True)
            if approve.count():
                pending = approve.first.locator("xpath=../..")
                assert "run_code" in pending.inner_text(), "Unexpected approval action; left unapproved"
                value = json.loads(pending.locator("pre").inner_text())
                assert validate_approval(value, folder, code), "Unexpected code/arguments; left unapproved"
                assert approvals == 0 and not target.exists(), "Repeated mutation refused"
                page.evaluate("window.__providerQA.approved=true")
                approve.first.click()
                approvals += 1
                settled_at = None
            state = page.evaluate("""async()=>({
                requests:window.__providerQA.requests,saves:window.__providerQA.saves,
                durable:window.__providerQA.durableSaves,
                savedAgo:performance.now()-window.__providerQA.lastSave,
                busy:(await window.__JARVIS_NATIVE__.core.invoke('backend',{method:'services.read',params:{name:'runtime.status',args:{}}})).busy})""")
            failed_ui = frame.locator('div.text-destructive').filter(has_text="Request failed.").count() > 0
            idle = (state["requests"] > 0 and not state["busy"] and state["saves"] > 0 and (state["durable"] > 0 or failed_ui)
                    and state["savedAgo"] >= 600 and not approve.count()
                    and frame.get_by_role("button", name="Send", exact=True).is_visible())
            if idle and last_saves == state["saves"]:
                settled_at = settled_at or time.monotonic()
                if time.monotonic() - settled_at >= .6:
                    report["idle_settled"] = True
                    break
            else:
                settled_at = None
            last_saves = state["saves"]
            page.wait_for_timeout(100)
        else:
            raise AssertionError("QA scenario deadline exhausted")
        page.wait_for_timeout(250)
        audit = page.evaluate("()=>window.__providerQA")
        report.update(safe_diagnostics(audit), approvals=approvals,
            seconds=round(time.monotonic() - started, 3), model=model)
        usage = session_usage(page)
        report["usage"] = usage
        report.update(result_evidence(audit, target, sentinel, args.only_python))
        report.update(ui_error_evidence(frame))
        # Structural evidence is persisted before any outcome assertion can fail.
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        assert 2 <= audit["requests"] <= budget, "Native request budget was not observed"
        parts = [p for m in audit["messages"] for p in m.get("parts", []) if p.get("type", "").startswith("tool-")]
        calls = [{"id": p.get("toolCallId"), "tool": p["type"][5:], "state": p.get("state"),
                  "action": p.get("input", {}).get("name") if isinstance(p.get("input"), dict) else None} for p in parts]
        names = {c["action"] or c["tool"] for c in calls if c["state"] == "output-available"}
        required = {"run_code"} if args.only_python else {"read_file", "jarvis_catalog", "data_query", "run_code"}
        assert required <= names, "Required completed tools absent"
        serialized = json.dumps(parts, ensure_ascii=False)
        if not args.only_python:
            assert sentinel in serialized and QA_RESULT in serialized, "Canonical results absent"
        assert approvals == 1 and target.read_text(encoding="utf-8") == QA_OUTPUT
        python_outputs = [p.get("output") for p in parts if p.get("input", {}).get("name") == "run_code" or p["type"] == "tool-run_code"]
        assert any(isinstance(value, dict) and value.get("stdout", "").strip() == "42"
                   and value.get("exit_code") == 0 and not value.get("timed_out")
                   for value in python_outputs), "Exact successful Python stdout absent"
        assert audit["durableSaves"] > 0, "Turn did not complete a durable session save"
        assert usage and usage["tokens"]["inputTokens"] > 0, "Provider usage unavailable"
        assert not report["ui_request_failed"], "Request failed UI remains after provider turn"
        assert report["final_assistant_text_characters"] > 0, "Provider turn has no final assistant text after tools"
        report.update(ok=True, model=model, model_requests=audit["requests"], approvals=approvals,
            tools=calls, usage=usage, seconds=round(time.monotonic() - started, 3))
    except BaseException as error:
        category = error_category(str(error))
        if category == "other" and report.get("ui_request_failed"):
            category = next((item["category"] for item in report.get("ui_errors", [])
                             if item["category"] != "other"), "incomplete_provider_turn")
        report["failure"] = {"type": type(error).__name__, "category": category}
        raise
    finally:
        if page:
            try:
                audit = page.evaluate("()=>window.__providerQA??{}")
                report.update(safe_diagnostics(audit), approvals=approvals)
                report.update(result_evidence(audit, target, sentinel, args.only_python))
                report["usage"] = session_usage(page)
                if started:
                    report["seconds"] = round(time.monotonic() - started, 3)
                # Read only visible error surfaces; retain classifications and lengths.
                report.update(ui_error_evidence(page.frame_locator('iframe[src="workbench.html"]')))
                if report["ui_request_failed"] or not report["final_assistant_text_characters"]:
                    late_failure = report.get("ok", False)
                    report["ok"] = False
                    if late_failure:
                        report["failure"] = {"type": "LateCompletionFailure", "category": "incomplete_provider_turn"}
            except Exception:
                pass
            try:
                stop = page.frame_locator('iframe[src="workbench.html"]').get_by_role("button", name="Stop", exact=True)
                if stop.count():
                    stop.first.click(timeout=1000)
            except Exception:
                pass
            try:
                page.evaluate("""()=>Promise.allSettled((window.__providerQA?.requestIds??[]).map(requestId=>window.__JARVIS_NATIVE__.core.invoke('ai_http_cancel',{requestId})))""")
                page.evaluate("window.__JARVIS_NATIVE__.core.invoke('plugin:window|close')")
                process.wait(timeout=3)
            except Exception:
                pass
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        if playwright:
            playwright.stop()
        log.close()
        if fixture_locked:
            try:
                if fixture_saved:
                    if old_suggestions is None:
                        suggestions.unlink(missing_ok=True)
                    else:
                        suggestions.write_bytes(old_suggestions)
            finally:
                suggestions_lock.unlink(missing_ok=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        if late_failure:
            raise AssertionError("Provider turn has a UI error or lacks final assistant text")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("local", "google"), required=True)
    parser.add_argument("--live", action="store_true", help="Explicitly authorize this one real-provider scenario")
    parser.add_argument("--only-python", action="store_true", help="Run only the remaining exact approved Python action; at most three model requests")
    parser.add_argument("--bundled-assets", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"preflight": True, "provider": args.provider,
            "only_python": args.only_python, "live_calls": 0, "max_model_requests": 3 if args.only_python else MAX_REQUESTS,
            "scenario_seconds": SCENARIO_SECONDS,
            "tools": ["run_code"] if args.only_python else ["read_file", "jarvis_catalog", "data_query", "run_code"],
            "approval": "exact Python code and owned cwd only", "requires": "--live"}))
        return
    run(args)


if __name__ == "__main__":
    main()
