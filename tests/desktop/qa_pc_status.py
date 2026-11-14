"""PC Status chip -> production chat/tools -> real read-only status; synthetic model only."""
import json
import os
import re
import time
from contextlib import contextmanager

PROMPT = "Show the computer status."
MODEL = "google/gemma-4-e4b"


def pc_status_stats(context):
    assert context in {4096, 32768}
    return {"context": context, "requests": 0, "payload_bytes": [], "response_bytes": [],
            "tool_calls": [], "errors": [], "final_answer": None, "measured_result": None}


def _text(value):
    return value if isinstance(value, str) else "\n".join(part.get("text", "") for part in value if isinstance(part, dict))


def pc_status_response(payload, stats):
    stats["requests"] += 1
    assert stats["requests"] <= 3, "PC status fixture exceeded three synthetic requests"
    assert payload["model"] == MODEL and payload.get("stream") is True
    stats["payload_bytes"].append(len(json.dumps(payload).encode()))
    names = {item["function"]["name"] for item in payload.get("tools", [])}
    required = {"read_file", "jarvis_catalog", "jarvis_action", "browser_control", "weather_report"}
    expected = required if stats["context"] == 4096 else required | {"list_directory", "grep", "glob", "data_query", "conversation_read", "mcp_catalog"}
    assert names == expected, {"unexpected_profile": sorted(names), "expected": sorted(expected)}
    assert not names & {"bash_run", "utility_run", "run_code"}
    messages = payload["messages"]
    system = "\n".join(_text(message["content"]) for message in messages if message["role"] == "system")
    contract = re.search(r"Callable tools: ([^.]+)\.", system)
    assert contract and set(contract[1].split(", ")) == names, "Prompt/schema contract diverged"
    assert "computer status → catalogue system_status" in system
    user = [message for message in messages if message["role"] == "user"]
    assert len(user) == 1 and _text(user[0]["content"]).strip().endswith(PROMPT)
    assert 1 <= payload.get("max_tokens", payload.get("max_completion_tokens", 0)) < stats["context"]
    results = [message for message in messages if message["role"] == "tool"]
    if stats["requests"] == 1:
        assert not results
        reply = {"name": "jarvis_catalog", "args": {"query": "system_status", "cursor": 0, "limit": 1}}
    elif stats["requests"] == 2:
        assert len(results) == 1
        catalogue = json.loads(_text(results[0]["content"]))
        item = next(item for item in catalogue["items"] if item["name"] == "system_status")
        assert item["available"] and item["parameters"]["type"] == "object"
        assert "focus" in item["parameters"]["properties"]
        # A concrete useful live measurement avoids process names and network identifiers.
        reply = {"name": "jarvis_action", "args": {"name": "system_status", "args": {"focus": "memory"}}}
    else:
        assert len(results) == 2
        output = json.loads(_text(results[-1]["content"]))
        assert output.get("ok") is True and output.get("action") == "system_status"
        assert output.get("verification") == "tool_report"
        measured = output["result"]
        assert re.fullmatch(r"Memory: [0-9.]+% used \([0-9.]+/[0-9.]+ GB\)\.", measured), measured
        stats["measured_result"] = measured
        stats["final_answer"] = "Current computer status: " + measured + " This is a live memory reading."
        reply = {"content": stats["final_answer"]}
    if "name" in reply:
        stats["tool_calls"].append(reply["name"])
    return reply


def emit_pc_status_response(handler, payload, stats):
    try:
        reply = pc_status_response(payload, stats)
    except Exception as error:
        stats["errors"].append(str(error))
        handler.send_error(500, "PC status fixture contract failed")
        return
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()

    def emit(delta, reason=None):
        chunk = {"id": "qa-pc-status", "object": "chat.completion.chunk", "created": 1,
                 "model": MODEL, "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
        data = ("data: " + json.dumps(chunk) + "\n\n").encode()
        stats["response_bytes"].append(len(data))
        handler.wfile.write(data)

    if "name" in reply:
        emit({"role": "assistant", "tool_calls": [{"index": 0, "id": "pc-status-" + str(stats["requests"]),
              "type": "function", "function": {"name": reply["name"], "arguments": json.dumps(reply["args"])}}]})
        emit({}, "tool_calls")
    else:
        emit({"role": "assistant", "content": reply["content"]})
        emit({}, "stop")
    data = ("data: " + json.dumps({"id": "qa-pc-usage", "object": "chat.completion.chunk", "created": 1,
            "model": MODEL, "choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}) + "\n\ndata: [DONE]\n\n").encode()
    stats["response_bytes"].append(len(data))
    handler.wfile.write(data)
    handler.wfile.flush()


STATUS_OBSERVER = """() => {
 const host=window.__JARVIS_NATIVE__,native=host.core;
 const audit=window.__qaPcStatus={prepared:0,executed:0,approved:0,outputBytes:0,result:null};
 const tokens=new Set();
 host.core={...native,invoke:async(command,args,extra)=>{
  if(command==='backend'&&args.method==='agent.prepare'){
   if(args.params.name!=='system_status'||JSON.stringify(args.params.args)!==JSON.stringify({focus:'memory'}))throw Error('PC status used unexpected action');
   if(++audit.prepared!==1)throw Error('PC status prepared more than once');
   const result=await native.invoke(command,args,extra);
   if(result.requires_approval!==false)throw Error('Read-only PC status requested approval');
   tokens.add(result.token);return result;
  }
  if(command==='backend'&&args.method==='agent.execute'){
   if(!tokens.delete(args.params.token)||++audit.executed!==1)throw Error('PC status replayed or used an unrelated receipt');
   const result=await native.invoke(command,args,extra);
   audit.outputBytes=new TextEncoder().encode(JSON.stringify(result)).length;audit.result=result;return result;
  }
  return native.invoke(command,args,extra);
 }};
 window.__qaPcStatusRestore=()=>{host.core=native;};
}"""


@contextmanager
def builtin_qa_suggestions(root):
    """Exercise production defaults while preserving the shared owned-QA store byte-for-byte."""
    from qa_suggestions import preserve_qa_suggestions
    from local_mode.suggestion_defaults import SUGGESTIONS
    items = [{"label": label, "prompt": prompt} for label, prompt in SUGGESTIONS]
    with preserve_qa_suggestions(root, items) as path:
        original = path.read_bytes() if path.exists() else None
        path.unlink(missing_ok=True)
        try:
            yield
        finally:
            if path.is_symlink() or path.parent.resolve() != (root / '.cache/desktop-test').resolve() or path.exists():
                raise RuntimeError('QA suggestions changed during read-only PC status; current file preserved')
            if original is not None:
                path.write_bytes(original)


def run_pc_status(page, fixture):
    import httpx
    from playwright.sync_api import expect
    from qa_locale import select_language
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    select_language(page, "en")
    saved = page.evaluate("""async()=>{const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});return {model:await s.get('defaultModelId'),base:await s.get('lmstudioBaseURL'),local:await s.get('lmstudioModelId')};}""")
    assert saved == {"model": "lmstudio-local", "base": os.environ["JARVIS_QA_MODEL_BASE"], "local": MODEL}, saved
    page.get_by_role("button", name="Chat", exact=True).click()
    frame = page.frame_locator('iframe[src="workbench.html"]')
    frame.locator('[data-unified-chat]').wait_for()
    page.evaluate(STATUS_OBSERVER)
    try:
        chip = frame.locator(".jarvis-chat-hints").get_by_role("button", name="▣ PC Status", exact=True)
        expect(chip).to_have_count(1)
        page.screenshot(path=str(fixture / 'pc-status-before-click.png'))
        chip.evaluate("""el=>{
          window.__qaChipEvents=[];
          for(const type of ['pointerdown','pointerup','click','submit'])document.addEventListener(type,event=>{
            window.__qaChipEvents.push({type,target:event.target.outerHTML?.slice(0,500),prevented:event.defaultPrevented});
          },true);
        }""")
        chip.click()
        composer = frame.locator('[data-shared-composer] textarea')
        try:
            expect(composer).to_have_value(PROMPT)
        except Exception:
            diagnostic = frame.locator('[data-unified-chat]').evaluate("""el=>({
              events:window.__qaChipEvents,
              active:document.activeElement?.outerHTML,
              forms:[...document.forms].map(form=>form.outerHTML.slice(0,500)),
              textareas:[...document.querySelectorAll('textarea')].map(node=>({value:node.value,rect:node.getBoundingClientRect().toJSON()})),
              chips:[...el.querySelectorAll('.jarvis-hint')].map(node=>({text:node.textContent,rect:node.getBoundingClientRect().toJSON(),disabled:node.disabled,react:!!Object.keys(node).find(key=>key.startsWith('__reactProps'))})),
              pageText:el.innerText,
            })""")
            (fixture / 'pc-status-click-failure.json').write_text(json.dumps(diagnostic), encoding='utf8')
            page.screenshot(path=str(fixture / 'pc-status-click-failure.png'))
            print(json.dumps({'pc_status_click_failure_artifacts':str(fixture),'page_errors':errors}))
            raise
        frame.get_by_role("button", name="Send", exact=True).click()
        endpoint = os.environ["JARVIS_QA_MODEL_BASE"].removesuffix('/v1') + '/qa/pc-status'
        deadline = time.monotonic() + 25
        stats = {}
        while time.monotonic() < deadline:
            stats = httpx.get(endpoint, timeout=2, trust_env=False).json()
            assert not stats['errors'], stats
            if stats['final_answer']:
                break
            page.wait_for_timeout(100)
        assert stats.get('final_answer'), stats
        frame.get_by_text(stats['final_answer'], exact=True).first.wait_for(timeout=15000)
        frame.get_by_role('button', name='Send', exact=True).wait_for()
        assert frame.get_by_role('button', name='Stop', exact=True).count() == 0
        assert frame.get_by_role('button', name='Approve', exact=True).count() == 0
        assert frame.get_by_text('Request failed.', exact=True).count() == 0
        assert frame.get_by_text(re.compile(r'AI_NoSuchToolError|unavailable tool|LOCALIZED_GUIDANCE')).count() == 0
        audit = page.evaluate('()=>window.__qaPcStatus')
        assert audit['prepared'] == audit['executed'] == 1 and audit['result']['result'] == stats['measured_result'], audit
        assert audit['outputBytes'] > 0
        persisted = page.evaluate("""async expected=>{
          const store=await window.__JARVIS_NATIVE__.store.load('terax-ai-sessions.json',{defaults:{}});
          const deadline=performance.now()+8000;
          while(performance.now()<deadline){
            const id=await store.get('activeId');
            const data=await window.__JARVIS_NATIVE__.core.invoke('ai_session_load',{id});
            const messages=data.byteLength?JSON.parse(new TextDecoder().decode(data)):[];
            if(JSON.stringify(messages).includes(expected))return {id,messages};
            await new Promise(resolve=>setTimeout(resolve,50));
          }
          throw Error('PC status final response was not persisted');
        }""", stats['final_answer'])
        assistants = [message for message in persisted['messages'] if message['role'] == 'assistant']
        assert assistants and all(any(part.get('text', '').strip() or 'toolCallId' in part for part in message['parts']) for message in assistants)
        assert all(part.get('state') not in {'output-error', 'approval-requested'} for message in assistants for part in message['parts'])
        assert stats['measured_result'] in json.dumps(persisted['messages'])
        assert any(part.get('text') == stats['final_answer'] for part in assistants[-1]['parts'])
        assert stats['requests'] == 3 and stats['tool_calls'] == ['jarvis_catalog', 'jarvis_action'], stats
        page.screenshot(path=str(fixture / 'pc-status.png'))
    finally:
        page.evaluate('()=>window.__qaPcStatusRestore?.()')
    page.reload()
    page.get_by_role('button', name='Chat', exact=True).click()
    page.frame_locator('iframe[src="workbench.html"]').get_by_text(stats['final_answer'], exact=True).first.wait_for(timeout=15000)
    after = httpx.get(endpoint, timeout=2, trust_env=False).json()
    assert after['requests'] == 3 and not errors, {'stats': after, 'page_errors': errors}
    print(json.dumps({'pc_status_chip_production_prompt': True, 'actual_shared_chat_sdk': True,
          'canonical_system_status': 'memory', 'real_measurement': stats['measured_result'],
          'synthetic_model_requests': stats['requests'], 'actual_status_executions': audit['executed'],
          'backend_output_bytes': audit['outputBytes'], 'provider_request_bytes': sum(stats['payload_bytes']),
          'provider_response_bytes': sum(stats['response_bytes']), 'loaded_context': stats['context'], 'selected_model': saved['model'], 'provider_model': MODEL,
          'persisted_after_reload': True, 'page_errors': errors}))
