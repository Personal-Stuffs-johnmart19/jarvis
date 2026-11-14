"""One approved owned-script failure: synthetic planner, actual queued executor."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

CHECKOUT_ROOT = Path(__file__).resolve().parents[2]

SCRIPT = '''from pathlib import Path
with Path(__file__).with_name("once.txt").open("ab") as stream:
    stream.write(b"ONCE\\n")
print("QUEUED_FAILURE_STDOUT", flush=True)
raise RuntimeError("QUEUED_EXPECTED_FAILURE")
'''
SHA = hashlib.sha256(SCRIPT.encode()).hexdigest()
PREFIX = "Use code_helper action run on the existing Python file with args [] and timeout 5. QUEUE_FAILURE_QA:"


def parameters(spec):
    assert set(spec) == {"file_path", "sha256"} and spec["sha256"] == SHA
    path = spec["file_path"]
    assert isinstance(path, str) and re.fullmatch(r"[A-Za-z]:/[^:]*[.]cache/desktop-test/queued-task-[a-f0-9]{8,32}/failure[.]py", path)
    assert ".." not in path.split("/") and "\\" not in path
    return {"action": "run", "file_path": path, "args": [], "timeout": 5}


def queued_failure_response(payload):
    assert payload.get("model") == "google/gemma-4-e4b" and not payload.get("stream") and not payload.get("tools")
    messages = payload["messages"]
    assert [item["role"] for item in messages] == ["system", "user"]
    prompt = messages[1]["content"]
    match = re.fullmatch(r"Goal:\s*" + re.escape(PREFIX) + r"(\{.*\})", prompt, re.S)
    assert match, "Summary, recovery and replan requests are forbidden"
    args = parameters(json.loads(match.group(1)))
    # A missing initial schema must fail rather than disguise discovery as a pass.
    from qa_queued_task import HELPER_SYSTEM_SUFFIXES
    schema_lines = messages[0]["content"].partition("\nSchemas:\n")[2].splitlines()
    shown = [json.loads(line) for line in schema_lines if line.strip() and line not in HELPER_SYSTEM_SUFFIXES]
    assert any(item.get("name") == "code_helper" for item in shown), "Expected schema must be selected in the only planning request"
    return {"kind": "plan", "content": json.dumps({"goal": prompt.removeprefix("Goal:").strip(), "steps": [{
        "step": 1, "tool": "code_helper", "description": "Run the exact owned failure fixture once",
        "parameters": args, "critical": True}]})}


AUDIT = r"""async o=>{
const h=window.__JARVIS_NATIVE__,native=h.core;
const a=window.__qaFailure={posts:0,metadataReads:0,approvals:0,claims:[],executed:[],blocked:[],helperRequests:0,helperCompletions:0,wire:[],requestIds:[]};
const receipts=new Map(),helpers=new Set();
const same=(x,y)=>JSON.stringify(x,(_k,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v)===JSON.stringify(y,(_k,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v);
const fail=(reason,command='',method='')=>{if(a.blocked.length<8)a.blocked.push({reason,command,method});throw Error('Owned queued failure QA refused: '+reason);};
const action=v=>v?.name==='code_helper'&&same(v.args,o.args);
const fsAllowed=(command,path)=>{
 const normalized=String(path).replaceAll(String.fromCharCode(92),'/');
 return !normalized.split('/').includes('..')&&['fs_read_file','fs_read_dir','fs_canonicalize'].includes(command)&&(normalized.startsWith(o.folder+'/')||(normalized===o.folder&&['fs_read_dir','fs_canonicalize'].includes(command)));
};
const off=await h.event.listen('backend-event',({payload:p})=>{
 if(p.event==='helper.text.request'){if(p.data.session!==o.session)fail('Unexpected helper session');helpers.add(p.data.id);a.helperRequests++;}
 if(p.event==='tasks.approval'){
  if(p.data.session!==o.session||p.data.request?.name!=='tasks.resume'||!action(p.data.request.args))fail('Unexpected action approval');a.approvals++;
 }
 if(p.event==='tasks.ui.read')fail('Unexpected native queued read');
});
h.core={...native,invoke:async(command,args,extra)=>{
 const refuse=reason=>fail(reason,command,command==='backend'?args.method:'');
 if(/^(browser_|chrome_|screen_|camera_|audio_|shell_|utility_|pty_|computer_)/.test(command))refuse('Unexpected personal or execution surface');
 if(command.startsWith('fs_')&&!fsAllowed(command,args.path))refuse('Unexpected filesystem operation');
 if(command==='backend'){
  const allowed=['snapshot','api.usage','agent.prepare','agent.execute','helper.text.complete','agent.activity','agent.attach','agent.detach','agent.complete','agent.ui_speech','conversation.sync','services.register_workspace','services.read','tasks.ui.pending'];
  if(!allowed.includes(args.method))refuse('Unexpected backend method');
  if(args.method==='tasks.ui.pending'){if(args.params.session!==o.session)refuse('Unexpected pending session');const items=await native.invoke(command,args,extra);if(items.length)refuse('Unexpected queued native read');return items;}
  if(args.method==='services.read'&&!['tasks.status','tasks.pending'].includes(args.params.name))refuse('Unexpected service read');
  if(args.method==='agent.prepare'){
   const {name,args:input}=args.params;
   if(name==='agent_task'){if(!same(input,{goal:o.goal,priority:'high'}))refuse('Unexpected goal');}
   else if(name==='tasks.resume'){
    const state=await native.invoke('backend',{method:'services.read',params:{name:'tasks.status',args:{task_id:input.task_id}}});
    if(state.status!=='awaiting_approval'||state.goal!==o.goal||!same(state.pending_action,input)||!action(input)||a.claims.length)refuse('Changed or repeated queued action');
    a.claims.push(input.task_id);
   }else refuse('Unexpected action capability');
   const receipt=await native.invoke(command,args,extra);if(!receipt.requires_approval)refuse('Required approval bypassed');receipts.set(receipt.token,{name,args:input});return receipt;
  }
  if(args.method==='agent.execute'){
   const receipt=receipts.get(args.params.token);if(!receipt)refuse('Missing single-use receipt');receipts.delete(args.params.token);a.executed.push({name:receipt.name,task_id:receipt.args.task_id??null});
  }
  if(args.method==='helper.text.complete'){
   if(!helpers.delete(args.params.id)||args.params.error)refuse('Unlinked or failed planner completion');
   let plan;try{plan=JSON.parse(args.params.text);}catch{refuse('Invalid plan JSON');}
   const step=plan?.steps?.[0];
    if(!plan||!same(Object.keys(plan).sort(),['goal','steps'])||plan.goal!==o.goal||!Array.isArray(plan.steps)||plan.steps.length!==1||!step||!same(Object.keys(step).sort(),['critical','description','parameters','step','tool'])||step.step!==1||step.tool!=='code_helper'||step.critical!==true||typeof step.description!=='string'||!same(step.parameters,o.args)){
    await native.invoke(command,{...args,params:{...args.params,text:'',error:'Owned failure QA rejected an unexpected plan'}},extra);refuse('Unexpected plan before execution');
   }a.helperCompletions++;
  }
 }
 if(command==='ai_http_stream'){
  const meta=JSON.parse(extra.headers['x-terax-ai-request']),bytes=args instanceof ArrayBuffer?new Uint8Array(args):args;
  if(meta.method==='GET'){
   if(meta.url!==o.base.replace(/\/v1$/,'')+'/api/v1/models'||bytes.byteLength||a.metadataReads>=1)refuse('Unexpected metadata request');a.metadataReads++;return native.invoke(command,args,extra);
  }
  if(meta.method!=='POST'||meta.url!==o.base+'/chat/completions'||a.posts!==0||performance.now()>o.deadline)refuse('Unexpected destination, extra inference or deadline');
  const body=JSON.parse(new TextDecoder().decode(bytes));
  if(body.model!=='google/gemma-4-e4b'||body.stream||body.tools?.length||body.messages?.length!==2||body.messages[0].role!=='system'||body.messages[1].role!=='user'||body.messages[1].content!=='Goal: '+o.goal)refuse('Unexpected planner request');
  a.posts++;a.requestIds.push(meta.requestId);a.wire.push({bytes:bytes.byteLength,tools:0,messages:2});
 }
 return native.invoke(command,args,extra);
}};
window.__qaFailureRestore=async()=>{await Promise.allSettled(a.requestIds.map(requestId=>native.invoke('ai_http_cancel',{requestId})));h.core=native;off();};
}"""


def run_queued_failure(page, fixture):
    import httpx
    from playwright.sync_api import expect
    from qa_locale import select_language
    fixture = fixture.resolve()
    assert fixture.parent.name == "desktop-test" and fixture.name.startswith("queued-task-")
    script, marker = fixture / "failure.py", fixture / "once.txt"
    assert not script.exists() and not marker.exists()
    script.write_bytes(SCRIPT.encode())
    spec = {"file_path": script.as_posix(), "sha256": SHA}
    args = parameters(spec)
    goal = PREFIX + json.dumps(spec, separators=(",", ":"))
    base = os.environ["JARVIS_QA_MODEL_BASE"].rstrip("/")
    address = urlsplit(base)
    assert address.scheme == "http" and address.hostname == "127.0.0.1" and address.path == "/v1"
    assert not address.username and not address.password and not address.query and not address.fragment
    errors = []
    page.on("pageerror", lambda error: errors.append(type(error).__name__))
    page.evaluate("""async base=>{const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});for(const[k,v]of Object.entries({lmstudioBaseURL:base,lmstudioModelId:'google/gemma-4-e4b',lmstudioReasoning:'off',defaultModelId:'lmstudio-local'}))await s.set(k,v);await s.save();}""", base)
    page.reload(); select_language(page, "en")
    page.get_by_role("button", name="Chat", exact=True).click()
    frame = page.frame_locator('iframe[src="workbench.html"]')
    frame.locator("[data-unified-chat]").wait_for()
    picker = frame.locator('button[title="Switch session"]:visible')
    if picker.count():
        picker.click(); frame.get_by_role("menuitem", name="New session", exact=True).click()
    def rpc(method, params=None):
        return page.evaluate("request=>window.__JARVIS_NATIVE__.core.invoke('backend',request)", {"method":method,"params":params or {}})
    for _ in range(60):
        snapshot = rpc("snapshot")
        if snapshot["active_model"] == "google/gemma-4-e4b": break
        page.wait_for_timeout(100)
    assert snapshot["active_model"] == "google/gemma-4-e4b" and snapshot["muted"] and snapshot["silent"]
    assert rpc("workbench.bootstrap")["test"] is True
    page.evaluate("path=>window.__JARVIS_NATIVE__.core.invoke('workspace_authorize',{path,workspace:{kind:'local'}})", str(fixture))
    def session():
        return page.evaluate("""async()=>{const h=window.__JARVIS_NATIVE__,s=await h.store.load('terax-ai-sessions.json',{defaults:{}}),id=await s.get('activeId'),data=await h.core.invoke('ai_session_load',{id});return {id,messages:data.byteLength?JSON.parse(new TextDecoder().decode(data)).length:0};}""")
    before = session(); assert before["messages"] == 0
    cloud_before = rpc("api.usage")["rows"]
    page.evaluate(AUDIT, {"args":args,"goal":goal,"folder":fixture.as_posix(),"base":base,"session":before["id"],"deadline":page.evaluate("performance.now()")+45000})
    report = {"ok":False,"synthetic_planner":True,"script_sha256":SHA,"handler":"code_helper.run"}
    started = time.monotonic()
    def wait(task_id, expected):
        while time.monotonic() < started + 40:
            value = rpc("services.read", {"name":"tasks.status","args":{"task_id":task_id}})
            if value["status"] == expected: return value
            assert value["status"] not in {"failed","completed","cancelled"}, value["status"]
            page.wait_for_timeout(100)
        raise AssertionError("Owned queue fixture exceeded deadline")
    try:
        receipt = rpc("agent.prepare", {"name":"agent_task","args":{"goal":goal,"priority":"high"}})
        assert receipt["requires_approval"]
        admitted = rpc("agent.execute", {"token":receipt["token"]})
        assert admitted["ok"] and admitted["status"] == "queued"
        task_id = admitted["task_id"]
        pending = wait(task_id, "awaiting_approval")
        assert pending["pending_action"]["name"] == "code_helper" and pending["pending_action"]["args"] == args
        assert not marker.exists() and hashlib.sha256(script.read_bytes()).hexdigest() == SHA
        pane = frame.get_by_role("complementary", name="Queued task approvals")
        pane.get_by_role("button", name="Approve", exact=True).wait_for(timeout=10000)
        expect(pane.locator("pre")).to_contain_text(script.as_posix())
        pane.get_by_role("button", name="Approve", exact=True).click()
        failed = wait(task_id, "failed")
        assert failed["pending_action"] is None
        outcome = failed["result"]
        result = outcome["failed_step_result"]
        assert outcome["failure_stage"] == "execution" and outcome["failed_step"]["tool"] == "code_helper"
        assert result["ok"] is False and result["status"] == "failed" and result["exit_code"] == 1
        assert result["stdout"] == "QUEUED_FAILURE_STDOUT" + os.linesep and "QUEUED_EXPECTED_FAILURE" in result["stderr"]
        assert result["timed_out"] is False and result["truncated"] is False and result["output_complete"] is True
        assert result["cancelled"] is False and result["decoding_loss"] is False
        assert failed["completed_steps"] == [] and failed["step_results"] == {} and failed["skipped_steps"] == []
        assert marker.read_bytes() == b"ONCE\n" and hashlib.sha256(script.read_bytes()).hexdigest() == SHA
        page.wait_for_timeout(300)
        stats = httpx.get(base.removesuffix("/v1") + "/qa/queued-task", timeout=3, trust_env=False).json()
        assert stats == {"requests":1,"kinds":["plan"]}
        audit = page.evaluate("()=>window.__qaFailure")
        assert not audit["blocked"] and audit["posts"] == audit["helperRequests"] == audit["helperCompletions"] == audit["approvals"] == 1
        assert audit["claims"] == [task_id] and audit["executed"] == [{"name":"agent_task","task_id":None},{"name":"tasks.resume","task_id":task_id}]
        assert session()["messages"] == 0 and not errors
        from qa_selected_helper import sdk_delta
        assert sum(row["attempts"] for row in sdk_delta(cloud_before, rpc("api.usage")["rows"])) == 0
        assert not [message for message in rpc("snapshot")["messages"] if message["role"] in {"user","assistant"}]
        report.update(ok=True,task_id=task_id,marker_exact_once=True,failed_step_result=result,planning_posts=1,recovery_or_summary_posts=0,saved_chat_messages=0)
    finally:
        report.update(seconds=round(time.monotonic()-started,3),audit=page.evaluate("()=>window.__qaFailure"),page_errors=errors)
        page.evaluate("async()=>await window.__qaFailureRestore?.()")
        print(json.dumps(report, ensure_ascii=False))


def pure_guards():
    path = (CHECKOUT_ROOT / ".cache/desktop-test/queued-task-1234abcd/failure.py").as_posix()
    spec = {"file_path":path,"sha256":SHA}
    payload = {"model":"google/gemma-4-e4b","messages":[{"role":"system","content":'Schemas:\n'}, {"role":"user","content":"Goal: " + PREFIX + json.dumps(spec)}]}
    payload["messages"][0]["content"] = 'Planner\nSchemas:\n{"name":"code_helper"}\nPreserve the requested language and JSON/code output format.\nReturn exactly one valid JSON value, without Markdown fences or commentary.'
    reply = queued_failure_response(payload)
    assert json.loads(reply["content"])["steps"][0]["parameters"] == parameters(spec)
    for invalid in ({**spec,"code":"print(1)"}, {**spec,"sha256":"0"*64}, {**spec,"file_path":"D:/private.py"}):
        try: parameters(invalid)
        except AssertionError: pass
        else: raise AssertionError("Unsafe run admitted")
    for prompt in ("User goal: " + PREFIX, payload["messages"][1]["content"] + "\nAlready completed: []"):
        attempt = {**payload,"messages":[payload["messages"][0],{"role":"user","content":prompt}]}
        try: queued_failure_response(attempt)
        except (AssertionError, json.JSONDecodeError): pass
        else: raise AssertionError("Extra inference admitted")
    return {"ok":True,"pure_cases":6,"model_calls":0,"executions":0}


def filesystem_guard_script():
    """Actual audit, mocked native directory calls only; no app or filesystem access."""
    folder = (CHECKOUT_ROOT / ".cache/desktop-test/queued-task-1234abcd").as_posix()
    return "const install=" + AUDIT + ";\nconst folder=" + json.dumps(folder) + ";\n" + r"""
const called=[];
globalThis.window={__JARVIS_NATIVE__:{event:{listen:async()=>()=>{}},core:{invoke:async(command,args)=>{called.push(command);return command==='fs_read_dir'?[]:args.path;}}}};
await install({folder,session:'qa-session'});
const invoke=window.__JARVIS_NATIVE__.core.invoke;
for(const command of ['fs_read_dir','fs_canonicalize'])await invoke(command,{path:folder});
for(const [command,path] of [['fs_read_file',folder],['fs_write_file',folder+'/file'],['fs_read_dir','D:/private'],['fs_canonicalize',folder+'-outside'],['fs_read_file',folder+'/../private']]){
 let rejected=false;try{await invoke(command,{path});}catch{rejected=true;}
 if(!rejected)throw Error('Filesystem whitelist admitted a forbidden operation');
}
if(called.join(',')!=='fs_read_dir,fs_canonicalize'||window.__qaFailure.blocked.length!==5)throw Error('Filesystem guard did not preserve exact directory exception');
console.log(JSON.stringify({ok:true,ownedDirectoryCases:2,rejectedCases:5,appCalls:0,filesystemCalls:0}));
"""
