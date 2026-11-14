"""One native selected-model helper; synthetic unless a provider and --live are explicit."""
import argparse
import json
import os
import re
import time

def selected_helper_options(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--selected-helper-only", action="store_true")
    parser.add_argument("--selected-helper-provider", choices=("local", "google"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--selected-helper-preflight", action="store_true")
    options, _ = parser.parse_known_args(argv)
    if options.selected_helper_provider and (not options.live or not options.selected_helper_only):
        parser.error("A real helper requires --selected-helper-only, an explicit provider and --live")
    if options.live and not options.selected_helper_provider:
        parser.error("--live requires --selected-helper-provider local|google")
    if options.selected_helper_preflight:
        print(json.dumps({"selected_helper_preflight": True,
            "provider": options.selected_helper_provider or "fake", "runtime_calls": 0,
            "native_post_cap": 1, "cloud_sdk_cap": 1, "timeout_seconds": 45}))
        raise SystemExit(0)
    return options


NATIVE_AUDIT = r"""async options=>{
 const host=window.__JARVIS_NATIVE__,native=host.core;
 const bridge=options.provider==='google'?await native.invoke('backend',{method:'workbench.bootstrap',params:{}}):null;
 const audit=window.__qaHelper={requests:0,completions:0,linked:false,posts:0,metadata_gets:0,blocked:0,wire:[],requestIds:[],refusals:[]};
 const norm=p=>String(p??'').replaceAll(String.fromCharCode(92),'/').toLowerCase();
 let token=null;
 window.__qaHelperOff=await host.event.listen('backend-event',({payload})=>{
   if(payload.event==='helper.text.request'){audit.requests++;audit.id=payload.data.id;}
 });
 host.core={...native,invoke:async(command,args,extra)=>{
   const label=value=>typeof value==='string'&&/^[a-zA-Z0-9_.:|-]{1,128}$/.test(value)?value:null;
   let http=null;
   const fail=reason=>{audit.blocked++;
     if(audit.refusals.length<16)audit.refusals.push({command:label(command),method:command==='backend'?label(args?.method):null,reason,...(http??{})});
     throw Error('Selected-helper QA refused an unexpected operation: '+reason);};
   if(/^(browser_|chrome_|screen_|camera_|audio_|shell_|utility_|pty_)/.test(command))fail('device_or_process_command');
   if(command.startsWith('fs_')){
     if(!['fs_read_file','fs_read_dir','fs_canonicalize'].includes(command))fail('filesystem_command_not_allowlisted');
     if(!(norm(args.path)===norm(options.folder)||norm(args.path).startsWith(norm(options.folder)+'/')))fail('filesystem_outside_owned_fixture');
   }
   if(command==='backend'){
     const allowed=['snapshot','api.usage','workbench.bootstrap','agent.prepare','agent.execute','helper.text.complete',
       'agent.activity','agent.attach','agent.detach','agent.complete','agent.ui_speech','conversation.sync','services.register_workspace'];
     if(!allowed.includes(args.method))fail('backend_method_not_allowlisted');
     if(args.method==='agent.prepare'){
       const a=args.params.args;
       if(args.params.name!=='code_helper'||!a||Object.keys(a).sort().join(',')!=='action,file_path'||a.action!=='explain'||a.file_path!==options.file)fail('unexpected_tool_or_arguments');
       const receipt=await native.invoke(command,args,extra);token=receipt.token;return receipt;
     }
     if(args.method==='agent.execute'){if(!token||args.params.token!==token)fail('unexpected_receipt');token=null;}
     if(args.method==='helper.text.complete'){audit.completions++;audit.linked=args.params.id===audit.id;}
   }
   if(command==='ai_http_stream'){
     const meta=JSON.parse(extra.headers['x-terax-ai-request']);
     const url=new URL(meta.url), expected=options.provider==='google'?bridge.base:options.base;
     http={http_method:label(meta.method),http_path:['/api/v1/models','/v1/chat/completions'].includes(url.pathname)?url.pathname:'[unapproved-path]'};
     if(meta.method==='GET'){
       const bytes=args instanceof ArrayBuffer?new Uint8Array(args):args;
       const metadata=options.base.replace(/\/v1\/?$/,'')+'/api/v1/models';
       if(options.provider==='google'||meta.url!==metadata||url.hostname!=='127.0.0.1'||bytes.byteLength||audit.metadata_gets>=1)fail('metadata_request_not_allowlisted');
       audit.metadata_gets++;audit.requestIds.push(meta.requestId);
       return native.invoke(command,args,extra);
     }
     if(meta.method!=='POST')fail('inference_method');
     if(audit.posts>=1)fail('inference_post_budget');
     if(performance.now()>options.deadline)fail('inference_deadline');
     if(options.provider==='google'){
       if(meta.url!==expected+'/models/'+options.model+':generateContent'||meta.headers['x-goog-api-key']!==bridge.token)fail('provider_destination_or_auth');
     } else if(meta.url!==expected+'/chat/completions')fail('provider_destination');
     if(url.hostname!=='127.0.0.1')fail('non_loopback_provider');
     const bytes=args instanceof ArrayBuffer?new Uint8Array(args):args;
     const body=JSON.parse(new TextDecoder().decode(bytes)),messages=body.messages??body.contents??[];
     if(body.tools?.length||body.toolConfig||body.stream)fail('tools_or_streaming_forbidden');
     if(options.provider==='google'){
       if(messages.length!==1||messages[0].role!=='user'||!body.systemInstruction)fail('history_or_model_mismatch');
     } else if(body.model!==options.model||messages.length!==2||messages[0].role!=='system'||messages[1].role!=='user')fail('history_or_model_mismatch');
     const characters=JSON.stringify(messages).length+JSON.stringify(body.systemInstruction??'').length;
     const text=JSON.stringify(messages);
     if(characters>2500||!text.includes('print(6*7)')||!text.includes('Привіт'))fail('source_or_prompt_bounds');
     audit.posts++;audit.requestIds.push(meta.requestId);
     audit.wire.push({bytes:bytes.byteLength,message_count:messages.length,characters,tools:0,
       route:options.provider==='google'?'selected-loopback-broker':'selected-local-endpoint'});
   }
   return native.invoke(command,args,extra);
 }};
 window.__qaHelperRestore=async()=>{
   await Promise.allSettled(audit.requestIds.map(requestId=>native.invoke('ai_http_cancel',{requestId})));
   host.core=native;window.__qaHelperOff();
 };
}"""


def sdk_delta(before, after):
    counters = ("attempts", "successes", "failures", "reported_usage", "input_tokens", "output_tokens", "cached_tokens", "thought_tokens", "total_tokens")
    old = {(row["model"], row["operation"]): row for row in before}
    result = []
    for row in after:
        previous = old.get((row["model"], row["operation"]), {})
        delta = {key: row.get(key, 0) - previous.get(key, 0) for key in counters}
        if any(delta.values()):
            result.append({"model": row["model"], "operation": row["operation"], **delta})
    return result


def run_selected_helper(page, fixture, options=None):
    import httpx
    from qa_locale import select_language
    provider = options.selected_helper_provider if options else None
    provider = provider or "fake"
    model = page.evaluate("async()=>(await window.__JARVIS_NATIVE__.core.invoke('backend',{method:'workbench.bootstrap',params:{}})).model") if provider == "google" else "gpt-oss-20b" if provider == "local" else "google/gemma-4-e4b"
    base = "http://127.0.0.1:1234/v1" if provider == "local" else os.environ["JARVIS_QA_MODEL_BASE"]
    path = fixture / "helper.py"
    path.write_bytes("# Привіт\nprint(6*7)\n".encode("utf-8"))
    source = path.read_bytes()
    page.evaluate("""async options=>{
        const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});
        for(const[k,v]of Object.entries({lmstudioBaseURL:options.base,lmstudioModelId:options.model,lmstudioReasoning:'off',defaultModelId:options.provider==='google'?options.model:'lmstudio-local'}))await s.set(k,v);
        await s.save();
    }""", {"base": base, "model": model, "provider": provider})
    page.reload()
    select_language(page, "en")
    page.get_by_role("button", name="Chat", exact=True).click()
    frame = page.frame_locator('iframe[src="workbench.html"]')
    frame.locator("[data-unified-chat]").wait_for()
    picker = frame.locator('button[title="Switch session"]:visible')
    if picker.count():
        picker.click()
        frame.get_by_role("menuitem", name="New session", exact=True).click()
    for _ in range(60):
        state = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'snapshot',params:{}})")
        if state["active_model"] == model:
            break
        page.wait_for_timeout(100)
    assert state["active_model"] == model and state["muted"] and state["silent"]
    page.evaluate("path=>window.__JARVIS_NATIVE__.core.invoke('workspace_authorize',{path,workspace:{kind:'local'}})", str(fixture))
    cloud_before = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'api.usage',params:{}})")["rows"]
    page.evaluate(NATIVE_AUDIT, {"provider": provider, "model": model, "base": base,
        "folder": str(fixture), "file": str(path), "deadline": page.evaluate("performance.now()") + 45000})
    started = time.monotonic()
    report = {"native_selected_helper": True, "provider": provider, "ok": False,
        "native_post_cap": 1, "cloud_sdk_cap": 1, "timeout_seconds": 45}

    def session():
        return page.evaluate("""async()=>{
            const host=window.__JARVIS_NATIVE__,s=await host.store.load('terax-ai-sessions.json',{defaults:{}});
            const id=await s.get('activeId'),metadata=(await s.get('sessions'))?.find(x=>x.id===id);
            const data=await host.core.invoke('ai_session_load',{id});
            const messages=data.byteLength?JSON.parse(new TextDecoder().decode(data)):[];
            return {usage:metadata?.usage?.tokens??{inputTokens:0,outputTokens:0,cachedInputTokens:0},messages:messages.length};
        }""")

    try:
        before = session()
        assert before["messages"] == 0
        receipt = page.evaluate("""args=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'agent.prepare',params:{name:'code_helper',args}})""",
            {"action": "explain", "file_path": str(path)})
        assert receipt["requires_approval"] is False
        page.evaluate("""token=>{window.__qaHelperDone=false;window.__qaHelperResult=null;
            window.__JARVIS_NATIVE__.core.invoke('backend',{method:'agent.execute',params:{token}})
              .then(value=>{window.__qaHelperResult=value;window.__qaHelperDone=true;})
              .catch(()=>{window.__qaHelperDone=true;});}""", receipt["token"])
        while time.monotonic() - started < 45:
            if page.evaluate("()=>window.__qaHelperDone"):
                break
            page.wait_for_timeout(100)
        else:
            raise AssertionError("Selected helper exceeded its 45-second QA deadline")
        result = page.evaluate("()=>window.__qaHelperResult")
        assert isinstance(result, dict) and result.get("ok") is True
        text = str(result.get("result", ""))
        assert text.strip() and ("HELPER_EXPLAINED_42" in text if provider == "fake" else "42" in text)
        assert path.read_bytes() == source, "Explain modified the owned input file"
        deadline = min(started + 45, time.monotonic() + 5)
        while time.monotonic() < deadline:
            after = session()
            if after["usage"]["inputTokens"] > before["usage"]["inputTokens"]:
                break
            page.wait_for_timeout(100)
        assert after["messages"] == 0
        delta = {key: after["usage"][key] - before["usage"][key] for key in before["usage"]}
        if provider == "fake":
            assert delta == {"inputTokens": 23, "outputTokens": 7, "cachedInputTokens": 3}, delta
        else:
            assert delta["inputTokens"] > 0 and delta["outputTokens"] > 0, "Provider usage missing"
        audit = page.evaluate("()=>window.__qaHelper")
        assert audit["requests"] == audit["completions"] == 1 and audit["linked"], "Helper request/result linkage failed"
        assert audit["posts"] == 1
        if provider == "fake":
            endpoint = os.environ["JARVIS_QA_MODEL_BASE"].removesuffix("/v1") + "/qa/selected-helper"
            stats = httpx.get(endpoint, timeout=3, trust_env=False).json()
            assert stats["requests"] == 1 and stats["tools"] == 0 and stats["message_count"] == 2, stats
        after_state = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'snapshot',params:{}})")
        assert not [m for m in after_state["messages"] if m["role"] in {"user", "assistant"}]
        meter = frame.get_by_role("button", name=re.compile(r"≈[1-9][0-9]*(?:\.[0-9]+)?%"))
        meter.wait_for(timeout=5000)
        meter.hover()
        for label in ("Estimated request", "Instructions and tools", "Reserved reply", "Safety reserve"):
            frame.get_by_text(label, exact=True).wait_for(timeout=5000)
        cloud_after = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'api.usage',params:{}})")["rows"]
        sdk = sdk_delta(cloud_before, cloud_after)
        assert sum(row["attempts"] for row in sdk) == (1 if provider == "google" else 0)
        if provider == "google":
            assert sum(row["successes"] for row in sdk) == 1
        report.update(ok=True, actual_handler="code_helper.explain", helper_events_linked=True,
            inference_posts=audit["posts"], usage_delta=delta, sdk_usage=sdk,
            saved_chat_messages=0, wire=audit["wire"], result_characters=len(text), result_contains_42=True,
            context_meter_includes_fixed_and_reserved=True)
    finally:
        report["seconds"] = round(time.monotonic() - started, 3)
        try:
            audit = page.evaluate("()=>window.__qaHelper")
            report.update(inference_posts=audit["posts"], metadata_gets=audit["metadata_gets"], blocked=audit["blocked"], refusals=audit["refusals"], wire=audit["wire"],
                helper_requests=audit["requests"], helper_completions=audit["completions"], helper_events_linked=audit["linked"])
            if "before" in locals():
                after = session()
                report["usage_delta"] = {key: after["usage"][key] - before["usage"][key] for key in before["usage"]}
                report["saved_chat_messages"] = after["messages"]
            report["owned_input_unchanged"] = path.read_bytes() == source
            cloud_after = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'api.usage',params:{}})")["rows"]
            report["sdk_usage"] = sdk_delta(cloud_before, cloud_after)
        finally:
            page.evaluate("async()=>await window.__qaHelperRestore?.()")
            print(json.dumps(report))
