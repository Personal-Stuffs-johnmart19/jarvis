"""Owned native queue/approval integration with a synthetic selected text provider."""
import json
import os
import re
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

CHECKOUT_ROOT = Path(__file__).resolve().parents[2]

READ_GOAL_PREFIX = "Use read_file to read the exact UTF-8 text file. QUEUE_READ_QA:"
HELPER_SYSTEM_SUFFIXES = {
    "Preserve the requested language and JSON/code output format.",
    "Return exactly one valid JSON value, without Markdown fences or commentary.",
}


def _owned_read_spec(spec):
    assert isinstance(spec, dict) and set(spec) == {"path"}
    path = spec["path"]
    assert isinstance(path, str) and "\\" not in path
    assert re.fullmatch(r"[A-Za-z]:/[^:]*[.]cache/desktop-test/queued-task-[a-f0-9]{8,32}/queued-read-[a-f0-9]{32}[.]txt", path)
    assert ".." not in PurePosixPath(path).parts
    return path


def queued_task_response(payload):
    """Pure fake-provider response; never substitutes a tool or executes a handler."""
    if os.environ.get("JARVIS_QA_QUEUED_FAILURE") == "1":
        from qa_queued_failure import queued_failure_response
        return queued_failure_response(payload)
    assert payload.get("model") == "google/gemma-4-e4b"
    assert not payload.get("stream", False) and not payload.get("tools")
    messages = payload["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert all(isinstance(message["content"], str) for message in messages)
    prompt = messages[1]["content"]
    read = re.fullmatch(r"Goal:\s*" + re.escape(READ_GOAL_PREFIX) + r"(\{.*\})", prompt, flags=re.S)
    if read:
        path = _owned_read_spec(json.loads(read.group(1)))
        # Schema discovery is a real extra planning round, never a summary. The
        # native read fixture still requires exactly plan+summary on its target.
        schemas = messages[0]["content"].partition("\nSchemas:\n")[2]
        shown = []
        for line in schemas.splitlines():
            if not line.strip() or line in HELPER_SYSTEM_SUFFIXES:
                continue
            item = json.loads(line)
            assert isinstance(item, dict) and isinstance(item.get("name"), str)
            shown.append(item)
        if not any(item.get("name") == "read_file" for item in shown):
            return {"kind": "schema_discovery", "content": json.dumps({"request_tools": ["read_file"]})}
        return {"kind": "plan", "content": json.dumps({"goal": prompt.removeprefix("Goal:").strip(), "steps": [{
            "step": 1, "tool": "read_file", "description": "Read the exact owned queued QA fixture",
            "parameters": {"path": path}, "critical": True,
        }]})}
    if prompt.startswith("User goal:") and "QUEUE_READ_QA:" in prompt:
        assert "Completed steps:" in prompt and "Read the exact owned queued QA fixture" in prompt
        return {"kind": "summary", "content": "QUEUED_READ_COMPLETED"}
    plan = re.fullmatch(r"Goal:\s*QUEUE_APPROVAL_QA:(\{.*\})", prompt, flags=re.S)
    if plan:
        spec = json.loads(plan.group(1))
        assert set(spec) == {"source", "dest"}
        paths = [PurePosixPath(value.replace("\\", "/")) for value in spec.values()]
        assert paths[0].parent == paths[1].parent and paths[0] != paths[1]
        assert all(".." not in path.parts and "/.cache/desktop-test/" in str(path).lower() for path in paths)
        content = json.dumps({"goal": prompt.removeprefix("Goal:").strip(), "steps": [{
            "step": 1, "tool": "file_controller", "description": "Move the owned queued QA fixture",
            "parameters": {"action": "move", "path": spec["source"], "destination": spec["dest"]},
            "critical": True,
        }]})
        return {"kind": "plan", "content": content}
    assert prompt.startswith("User goal:") and "QUEUE_APPROVAL_QA:" in prompt and "Completed steps:" in prompt
    assert "Move the owned queued QA fixture" in prompt
    return {"kind": "summary", "content": "QUEUED_MOVE_COMPLETED"}


NATIVE_AUDIT = r"""async options=>{
 const host=window.__JARVIS_NATIVE__,native=host.core;
 const bridge=options.provider==='google'?await native.invoke('backend',{method:'workbench.bootstrap',params:{}}):null;
 const cap=options.readOnly?2:options.planOnly?1:3;
 const audit=window.__qaQueued={posts:0,metadataReads:0,blocked:0,blockedOperations:[],prepared:[],executed:[],approvals:0,helperRequests:0,helperCompletions:0,helperErrors:[],wire:[],requestIds:[],nativeReads:0,readEvents:0,readClaims:0,readCompletions:0,readTaskIds:[],readPredicates:[]};
 const helperErrorCodes=new Map([
   ['busy','Selected provider text helper is busy'],['context_limit','Helper text exceeds selected model context'],
   ['native_format','Selected provider rejected the helper request format'],['timeout','Selected provider text request timed out'],
   ['cancelled','Selected provider text request was cancelled'],
   ['api_400','Selected provider returned an API error'],['api_401','Selected provider returned an API error'],
   ['api_403','Selected provider returned an API error'],['api_404','Selected provider returned an API error'],
   ['api_408','Selected provider returned an API error'],['api_429','Selected provider returned an API error'],
   ['api_5xx','Selected provider returned an API error'],['api_status_other','Selected provider returned an API error'],
   ['incomplete_output','Selected provider did not complete helper text'],['invalid_json','Selected provider returned invalid helper JSON'],
   ['output_size','Selected provider returned empty or oversized helper text'],['request_failed','Selected provider text request failed']
 ]);
 const pending=new Map(),receipts=new Map(),helpers=new Set(),reads=new Map(),claims=new Map();
 const fail=(reason,command='',method='')=>{audit.blocked++;audit.blockedOperations.push({reason,command,method});throw Error('Queued-task QA refused: '+reason);};
 const canonical=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
 const same=(a,b)=>canonical(a)===canonical(b);
 const norm=p=>String(p??'').replaceAll(String.fromCharCode(92),'/').toLowerCase();
 const step=args=>args&&args.action==='move'&&options.moves.some(move=>same(args,{action:'move',path:move.source,destination:move.dest}));
 const workspace=value=>typeof value==='string'&&value.length>0&&new TextEncoder().encode(value).byteLength<=8192;
 const coreRead=request=>Object.fromEntries(['args','name','nonce','owner','route','session','task_id','workspace'].map(key=>[key,request[key]]));
 const inspectRead=request=>{
   const keys=request&&typeof request==='object'?Object.keys(request).sort():[];
   const expected=['args','name','nonce','owner','route','session','task_id','workspace'];
   const enriched=[...expected,'request_id','monotonic'].sort();
   const transportKeysMatch=keys.join(',')===enriched.join(',');
   const transportValid=!transportKeysMatch||((request.request_id===null||typeof request.request_id==='string')&&typeof request.monotonic==='number'&&Number.isFinite(request.monotonic));
   const argumentKeys=request?.args&&typeof request.args==='object'?Object.keys(request.args).sort():[];
   return {readOnly:options.readOnly===true,sessionMatch:request?.session===options.session,nameMatch:request?.name==='read_file',pathMatch:same(request?.args,{path:options.readPath}),
     taskIdString:typeof request?.task_id==='string',nonceString:typeof request?.nonce==='string',ownerString:typeof request?.owner==='string',routeString:typeof request?.route==='string',
     workspaceType:typeof request?.workspace,workspaceBytes:typeof request?.workspace==='string'?Math.min(8193,new TextEncoder().encode(request.workspace).byteLength):0,workspaceValid:workspace(request?.workspace),
     fieldKeys:keys.slice(0,16).map(key=>enriched.includes(key)?key:'[other]'),argumentKeys:argumentKeys.slice(0,8).map(key=>key==='path'?key:'[other]'),
     fieldKeysMatch:(keys.join(',')===expected.join(',')||transportKeysMatch)&&transportValid};
 };
 const ownedRead=request=>{const check=inspectRead(request);return ['readOnly','sessionMatch','nameMatch','pathMatch','taskIdString','nonceString','ownerString','routeString','workspaceValid','fieldKeysMatch'].every(key=>check[key]);};
 const recordRead=(source,request)=>{if(audit.readPredicates.length<4)audit.readPredicates.push({source,...inspectRead(request)});};
 const rememberRead=request=>{if(!ownedRead(request)){recordRead('event_or_pending',request);fail('Unexpected queued native read');}reads.set(request.nonce,coreRead(request));};
 const off=await host.event.listen('backend-event',({payload})=>{
   if(payload.event==='helper.text.request'){
     if(payload.data.session!==options.session)fail('Unexpected helper session');
     helpers.add(payload.data.id);audit.helperRequests++;
   }
   if(payload.event==='tasks.approval'){
     if(options.readOnly)fail('Read-only task unexpectedly requested approval');
     const request=payload.data.request;
     if(payload.data.session!==options.session||request?.name!=='tasks.resume'||request.args?.name!=='file_controller'||!step(request.args.args))fail('Unexpected queued approval event');
     pending.set(request.args.task_id,request.args);audit.approvals++;
   }
   if(payload.event==='tasks.ui.read'){rememberRead(payload.data);audit.readEvents++;audit.readTaskIds.push(payload.data.task_id);}
 });
 host.core={...native,invoke:async(command,args,extra)=>{
   const refuse=reason=>fail(reason,command,command==='backend'?args.method:'');
   if(/^(browser_|chrome_|screen_|camera_|audio_|shell_|utility_|pty_)/.test(command))refuse('Unexpected personal or execution surface');
   if(command.startsWith('fs_')&&(!['fs_read_file','fs_read_dir','fs_canonicalize'].includes(command)||!(norm(args.path)===norm(options.folder)||norm(args.path).startsWith(norm(options.folder)+'/'))))refuse('Unexpected filesystem operation');
   if(options.readOnly&&command==='fs_read_file'){
     if(norm(args.path)!==norm(options.readPath)||audit.nativeReads!==0||audit.readClaims!==1)refuse('Unexpected or repeated native file read');audit.nativeReads++;
   }
   if(command==='backend'){
     const allowed=['snapshot','api.usage','workbench.bootstrap','agent.prepare','agent.execute','helper.text.complete',
       'agent.activity','agent.attach','agent.detach','agent.complete','agent.ui_speech','conversation.sync','services.register_workspace','services.read','tasks.ui.pending',
       ...(options.readOnly?['tasks.ui.claim','tasks.ui.complete']:[])];
     if(!allowed.includes(args.method))refuse('Unexpected backend method');
     if(args.method==='tasks.ui.pending'){
       if(args.params.session!==options.session||!workspace(args.params.workspace))refuse('Unexpected read polling session or workspace');
       const items=await native.invoke(command,args,extra);for(const item of items)rememberRead(item);return items;
     }
     if(args.method==='tasks.ui.claim'){
       const input=args.params;
       if(!ownedRead(input)){recordRead('claim',input);refuse('Unexpected native read claim identity or path');}
       if(audit.readClaims!==0)refuse('Repeated native read claim');
       // UI listeners may claim before this audit observes the event. Require
       // exact authoritative unclaimed state, rather than observer order.
       const items=await native.invoke('backend',{method:'tasks.ui.pending',params:{owner:input.owner,session:input.session,route:input.route,workspace:input.workspace}});
       const original=items.find(item=>item.nonce===input.nonce);
       if(!same(original,coreRead(input)))refuse('Native read claim does not match authoritative pending state');
       rememberRead(original);
       const receipt=await native.invoke(command,args,extra);if(typeof receipt.token!=='string')refuse('Missing read claim token');
       claims.set(receipt.token,coreRead(input));audit.readClaims++;return receipt;
     }
     if(args.method==='tasks.ui.complete'){
       const input=args.params,request=claims.get(input.token);
       if(!request||input.task_id!==request.task_id||input.owner!==request.owner||input.session!==request.session||input.route!==request.route||input.workspace!==request.workspace||Object.keys(input).sort().join(',')!=='owner,result,route,session,task_id,token,workspace'||audit.nativeReads!==1||audit.readCompletions!==0)refuse('Unexpected native read completion');
       claims.delete(input.token);audit.readCompletions++;audit.readTaskIds.push(input.task_id);
     }
     if(args.method==='services.read'&&!['tasks.status','tasks.pending'].includes(args.params.name))refuse('Unexpected read service');
     if(args.method==='agent.prepare'){
       const {name,args:input}=args.params;
       if(name==='agent_task'){
         if(!options.goals.includes(input.goal)||input.priority!=='high')refuse('Unexpected queued goal');
       } else if(name==='tasks.resume'){
         if(options.readOnly)refuse('Read-only task unexpectedly required approval resume');
         // UI listeners may run before this audit's event observer. Verify the
         // authoritative owned pending state instead of relying on listener order.
         const state=await native.invoke('backend',{method:'services.read',params:{name:'tasks.status',args:{task_id:input.task_id}}});
         if(state.status!=='awaiting_approval'||!options.goals.includes(state.goal)||!same(state.pending_action,input)||input.name!=='file_controller'||!step(input.args))refuse('Changed or unowned queued resume');
         pending.set(input.task_id,state.pending_action);
       } else if(name==='tasks.deny'){
         const state=await native.invoke('backend',{method:'services.read',params:{name:'tasks.status',args:{task_id:input.task_id}}});
         const original=state.pending_action;
         if(state.status!=='awaiting_approval'||!options.goals.includes(state.goal)||!original||input.approval_id!==original.approval_id||Object.keys(input).sort().join(',')!=='approval_id,task_id')refuse('Changed or unowned queued denial');
       } else refuse('Unexpected action capability');
       const receipt=await native.invoke(command,args,extra);
       if((name==='agent_task'||name==='tasks.resume')&&!receipt.requires_approval)refuse('Mutation bypassed required approval');
       if(name==='tasks.deny'&&receipt.requires_approval)refuse('Denial unexpectedly requires approval');
       receipts.set(receipt.token,{name,args:input});audit.prepared.push(name);return receipt;
     }
     if(args.method==='agent.execute'){
       const receipt=receipts.get(args.params.token);if(!receipt)refuse('Missing single-use action receipt');receipts.delete(args.params.token);
       if(options.planOnly&&receipt.name==='tasks.resume')refuse('Plan-only fixture forbids executing the pending move');
       audit.executed.push({name:receipt.name,task_id:receipt.args.task_id??null});
     }
     if(args.method==='helper.text.complete'){
       if(args.params.error&&audit.helperErrors.length<3){
         const match=typeof args.params.error==='string'?/^\[([a-z0-9_]{1,32})\](?:\s|$)/.exec(args.params.error):null;
         const code=match?.[1],text=helperErrorCodes.get(code);
         audit.helperErrors.push({code:text?code:'unclassified_helper_error',text:text??'Unclassified helper error'});
       }
       if(options.planOnly&&!args.params.error){
         let plan;try{plan=JSON.parse(args.params.text.trim().replace(/^```(?:json)?\s*|\s*```$/g,''));}catch{}
         const item=plan?.steps?.[0];
         if(!Array.isArray(plan?.steps)||Object.hasOwn(plan,'request_tools')||plan.steps.length!==1||item?.step!==1||item.tool!=='file_controller'||item.critical!==true||!step(item.parameters)){
           // Reject before Python receives a plan: even an unexpected safe first
           // step must never reach a backend handler in this plan-only fixture.
           await native.invoke(command,{...args,params:{...args.params,text:'',error:'Queued-plan QA refused an unexpected action plan'}},extra);
           refuse('Planner did not return the exact single owned move');
         }
       }
       if(!helpers.delete(args.params.id))refuse('Unlinked helper completion');audit.helperCompletions++;
     }
   }
   if(command==='ai_http_stream'){
     const meta=JSON.parse(extra.headers['x-terax-ai-request']),url=new URL(meta.url);
     const bytes=args instanceof ArrayBuffer?new Uint8Array(args):args;
     const refuseHttp=reason=>fail(reason+' '+url.pathname,command,meta.method);
     if(meta.method==='GET'){
       if(options.provider==='google'||meta.url!==options.base.replace(/\/v1$/,'')+'/api/v1/models'||url.hostname!=='127.0.0.1'||audit.metadataReads>=cap||bytes.byteLength!==0||performance.now()>options.deadline)refuseHttp('Unexpected selected model metadata read');
       audit.metadataReads++;return native.invoke(command,args,extra);
     }
     const endpoint=options.provider==='google'?bridge.base+'/models/'+options.model+':generateContent':options.base+'/chat/completions';
     if(meta.method!=='POST'||meta.url!==endpoint||url.hostname!=='127.0.0.1'||audit.posts>=cap||performance.now()>options.deadline)refuseHttp('Unexpected selected helper destination or request count');
     const body=JSON.parse(new TextDecoder().decode(bytes)),messages=body.messages??body.contents;
     if(body.stream||body.tools?.length||body.toolConfig||!JSON.stringify(messages).includes(options.readOnly?'QUEUE_READ_QA:':'QUEUE_APPROVAL_QA:'))refuse('Unexpected selected helper payload');
     if(options.provider==='google'){
       if(meta.headers['x-goog-api-key']!==bridge.token||messages?.length!==1||messages[0].role!=='user'||!body.systemInstruction)refuse('Unexpected Google planner route');
     }else if(body.model!==(options.model??'google/gemma-4-e4b')||messages?.length!==2||messages[0].role!=='system'||messages[1].role!=='user')refuse('Unexpected local planner route');
     const limit=body.max_tokens??body.max_completion_tokens??body.generationConfig?.maxOutputTokens;
     const maxOutputTokens=Number.isInteger(limit)&&limit>0&&limit<=8192?limit:null;
     audit.posts++;audit.requestIds.push(meta.requestId);audit.wire.push({tools:0,message_count:messages.length,bytes:bytes.byteLength,maxOutputTokens});
   }
   return native.invoke(command,args,extra);
 }};
 window.__qaQueuedRestore=async()=>{
   await Promise.allSettled(audit.requestIds.map(requestId=>native.invoke('ai_http_cancel',{requestId})));
   host.core=native;off();
 };
}"""


def run_queued_plan(page, fixture, options):
    """One selected-model plan, then deny its exact pending action without execution."""
    return run_queued_task(page, fixture, plan_only=True, options=options)


def run_queued_task(page, fixture, *, plan_only=False, options=None):
    if os.environ.get("JARVIS_QA_QUEUED_FAILURE") == "1":
        assert not plan_only and options is None and os.environ.get("JARVIS_QA_QUEUED_READ") != "1"
        from qa_queued_failure import run_queued_failure
        return run_queued_failure(page, fixture)
    import httpx
    from playwright.sync_api import expect
    from qa_locale import select_language

    provider = (options.selected_helper_provider if options else None) or "fake"
    read_only = os.environ.get("JARVIS_QA_QUEUED_READ") == "1"
    assert not read_only or (not plan_only and provider == "fake"), "Queued read fixture is synthetic only"
    assert provider == "fake" or (plan_only and options.live and options.selected_helper_only)
    model = (page.evaluate("async()=>(await window.__JARVIS_NATIVE__.core.invoke('backend',{method:'workbench.bootstrap',params:{}})).model")
             if provider == "google" else "gpt-oss-20b" if provider == "local" else "google/gemma-4-e4b")
    base = ("http://127.0.0.1:1234/v1" if provider == "local" else os.environ["JARVIS_QA_MODEL_BASE"]).rstrip("/")
    parsed = urlsplit(base)
    assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.path == "/v1"
    assert not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    fixture = fixture.resolve()
    assert fixture.parent.name == "desktop-test" and fixture.name.startswith("queued-task-")
    moves = []
    for _ in range(1 if plan_only or read_only else 2):
        key = uuid.uuid4().hex
        source, dest = fixture / ("queued-source-" + key + ".txt"), fixture / ("queued-destination-" + key + ".txt")
        source.write_bytes(b"42")
        assert not dest.exists()
        moves.append({"source":source.as_posix(), "dest":dest.as_posix()})
    goals = ["QUEUE_APPROVAL_QA:" + json.dumps(move, separators=(",", ":")) for move in moves]
    read_path = None
    read_content = "QUEUED_NATIVE_READ_42 Привіт\n"
    if read_only:
        # This file is the only native tool read admitted after instrumentation.
        read_path = (fixture / ("queued-read-" + uuid.uuid4().hex + ".txt")).as_posix()
        _owned_read_spec({"path": read_path})
        Path(read_path).write_bytes(read_content.encode("utf-8"))
        goals = [READ_GOAL_PREFIX + json.dumps({"path": read_path}, separators=(",", ":"))]
    if plan_only and provider != "fake":
        goals = [f'Move exactly the owned file "{move["source"]}" to "{move["dest"]}" using file_controller with action="move", path="{move["source"]}", destination="{move["dest"]}". One required step. Do not inspect or change anything else.\n' + goal
                 for move, goal in zip(moves, goals)]
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.evaluate("""async o=>{const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});
      for(const[k,v]of Object.entries({lmstudioBaseURL:o.base,lmstudioModelId:o.model,lmstudioReasoning:'off',defaultModelId:o.provider==='google'?o.model:'lmstudio-local'}))await s.set(k,v);await s.save();}""", {"base":base,"model":model,"provider":provider})
    page.reload()
    select_language(page, "en")
    page.get_by_role("button", name="Chat", exact=True).click()
    frame = page.frame_locator('iframe[src="workbench.html"]')
    frame.locator("[data-unified-chat]").wait_for()
    picker = frame.locator('button[title="Switch session"]:visible')
    if picker.count():
        picker.click()
        frame.get_by_role("menuitem", name="New session", exact=True).click()

    def rpc(method, params=None):
        return page.evaluate("request=>window.__JARVIS_NATIVE__.core.invoke('backend',request)", {"method":method,"params":params or {}})

    for _ in range(60):
        state = rpc("snapshot")
        if state["active_model"] == model:
            break
        page.wait_for_timeout(100)
    assert state["active_model"] == model and state["muted"] and state["silent"]
    assert rpc("workbench.bootstrap")["test"] is True
    page.evaluate("path=>window.__JARVIS_NATIVE__.core.invoke('workspace_authorize',{path,workspace:{kind:'local'}})", str(fixture))

    def session():
        return page.evaluate("""async()=>{const h=window.__JARVIS_NATIVE__,s=await h.store.load('terax-ai-sessions.json',{defaults:{}});
          const id=await s.get('activeId'),data=await h.core.invoke('ai_session_load',{id});
          return {id,messages:data.byteLength?JSON.parse(new TextDecoder().decode(data)).length:0};}""")

    before = session()
    assert before["messages"] == 0
    cloud_before = rpc("api.usage")["rows"] if plan_only else []
    page.evaluate(NATIVE_AUDIT, {"base":base,"model":model,"provider":provider,"planOnly":plan_only,"readOnly":read_only,"readPath":read_path,"folder":str(fixture),"moves":moves,"goals":goals,
        "session":before["id"],"deadline":page.evaluate("performance.now()")+60000})
    report = {"native_queued_approval":not read_only,"native_queued_read":read_only,"synthetic_only":provider=="fake","plan_only":plan_only,"provider":provider,"ok":False,"handler":"read_file" if read_only else "file_controller.move"}
    started = time.monotonic()

    def status(task_id):
        return rpc("services.read", {"name":"tasks.status","args":{"task_id":task_id}})

    def wait_status(task_id, expected):
        deadline = min(started + 60, time.monotonic() + 20)
        last = None
        while time.monotonic() < deadline:
            last = status(task_id)
            if last["status"] == expected:
                return last
            assert last["status"] not in {"failed","cancelled","completed"}, last
            page.wait_for_timeout(100)
        raise AssertionError({"expected":expected,"last_status":last})

    def submit(goal):
        receipt = rpc("agent.prepare", {"name":"agent_task","args":{"goal":goal,"priority":"high"}})
        assert receipt["requires_approval"] is True
        result = rpc("agent.execute", {"token":receipt["token"]})
        assert result["ok"] is True and result["status"] == "queued"
        return result["task_id"]

    try:
        task_id = submit(goals[0])
        if read_only:
            completed = wait_status(task_id, "completed")
            assert completed["task_id"] == task_id and completed["priority"] == 1
            assert len(completed["completed_steps"]) == len(completed["step_results"]) == 1
            step = completed["completed_steps"][0]
            assert step["tool"] == "read_file" and step["parameters"] == {"path": read_path}
            result = json.loads(next(iter(completed["step_results"].values())))
            assert result["content"] == read_content and result["path"].replace("\\", "/") == read_path
            assert Path(read_path).read_bytes() == read_content.encode("utf-8")
            assert not Path(moves[0]["dest"]).exists() and Path(moves[0]["source"]).read_bytes() == b"42"
            stats = httpx.get(base.removesuffix("/v1") + "/qa/queued-task", timeout=3, trust_env=False).json()
            assert stats == {"requests": 2, "kinds": ["plan", "summary"]}, stats
            audit = page.evaluate("()=>window.__qaQueued")
            assert audit["blocked"] == audit["approvals"] == 0, audit
            assert audit["posts"] == audit["helperRequests"] == audit["helperCompletions"] == 2, audit
            assert audit["nativeReads"] == audit["readClaims"] == audit["readCompletions"] == 1, audit
            assert audit["readEvents"] == 1 and set(audit["readTaskIds"]) == {task_id}, audit
            assert [item["name"] for item in audit["executed"]] == ["agent_task"], audit
            assert frame.get_by_role("complementary", name="Queued task approvals").count() == 0
            assert session()["messages"] == 0 and not errors, errors
            assert not [message for message in rpc("snapshot")["messages"] if message["role"] in {"user", "assistant"}]
            report.update(ok=True,task_id=task_id,completed_step_results=1,same_task_resumed_once=True,
                          exact_native_read_content=True,native_reads=1,planning_posts=1,summary_posts=1,
                          saved_chat_messages=0,approval_cards=0,owned_files_unchanged=True,
                          parent_cache_primed=False,cache_scope="fresh owned file; primed-cache semantics covered separately by canonical tests")
            return
        pending = wait_status(task_id, "awaiting_approval")
        assert pending["task_id"] == task_id and pending["priority"] == 1
        assert pending["completed_steps"] == [] and pending["step_results"] == {}
        assert not Path(moves[0]["dest"]).exists()
        pane = frame.get_by_role("complementary", name="Queued task approvals")
        pane.get_by_role("button", name="Approve", exact=True).wait_for(timeout=15000)
        expect(pane.get_by_text("file_controller", exact=True)).to_be_visible()
        expect(pane.locator("pre")).to_contain_text(moves[0]["dest"])
        if plan_only:
            assert pending["pending_action"]["name"] == "file_controller"
            assert pending["pending_action"]["args"] == {"action":"move","path":moves[0]["source"],"destination":moves[0]["dest"]}
            pane.get_by_role("button", name="Deny", exact=True).click()
            cancelled = wait_status(task_id, "cancelled")
            assert cancelled["completed_steps"] == [] and cancelled["step_results"] == {}
            assert Path(moves[0]["source"]).read_bytes() == b"42" and not Path(moves[0]["dest"]).exists()
            audit = page.evaluate("()=>window.__qaQueued")
            assert audit["blocked"] == 0 and audit["posts"] == audit["helperRequests"] == audit["helperCompletions"] == 1, audit
            assert [item["name"] for item in audit["executed"]] == ["agent_task","tasks.deny"], audit
            assert audit["executed"][1]["task_id"] == task_id and session()["messages"] == 0 and not errors
            from qa_selected_helper import sdk_delta
            sdk = sdk_delta(cloud_before, rpc("api.usage")["rows"])
            assert sum(row["attempts"] for row in sdk) == (1 if provider == "google" else 0), sdk
            if provider == "google":
                assert sum(row["successes"] for row in sdk) == 1, sdk
            report.update(ok=True,task_id=task_id,exact_pending_args=True,owned_input_unchanged=True,
                          denial_executed_nothing=True,saved_chat_messages=0,inference_posts=1,sdk_usage=sdk,wire=audit["wire"])
            return
        pane.get_by_role("button", name="Approve", exact=True).click()
        completed = wait_status(task_id, "completed")
        assert completed["task_id"] == task_id and completed["priority"] == 1
        assert len(completed["completed_steps"]) == len(completed["step_results"]) == 1
        step = completed["completed_steps"][0]
        assert step["tool"] == "file_controller" and step["parameters"] == {"action":"move","path":moves[0]["source"],"destination":moves[0]["dest"]}
        step_result = json.loads(next(iter(completed["step_results"].values())))
        assert step_result["ok"] is True and step_result["action"] == "file_controller"
        assert "Moved:" in str(step_result["result"])
        assert not Path(moves[0]["source"]).exists() and Path(moves[0]["dest"]).read_bytes() == b"42"
        endpoint = base.removesuffix("/v1") + "/qa/queued-task"
        first = httpx.get(endpoint, timeout=3, trust_env=False).json()
        assert first["requests"] == 2 and first["kinds"] == ["plan","summary"], first
        assert session()["messages"] == 0
        denied_id = submit(goals[1])
        wait_status(denied_id, "awaiting_approval")
        pane.get_by_role("button", name="Deny", exact=True).wait_for(timeout=15000)
        assert not Path(moves[1]["dest"]).exists()
        pane.get_by_role("button", name="Deny", exact=True).click()
        cancelled = wait_status(denied_id, "cancelled")
        assert cancelled["task_id"] == denied_id and cancelled["step_results"] == {}
        assert cancelled["completed_steps"] == []
        assert Path(moves[1]["source"]).read_bytes() == b"42" and not Path(moves[1]["dest"]).exists()
        final = httpx.get(endpoint, timeout=3, trust_env=False).json()
        assert final["requests"] == 3 and final["kinds"] == ["plan","summary","plan"], final
        audit = page.evaluate("()=>window.__qaQueued")
        assert audit["blocked"] == 0 and audit["posts"] == audit["helperRequests"] == audit["helperCompletions"] == 3, audit
        assert [item["name"] for item in audit["executed"]] == ["agent_task","tasks.resume","agent_task","tasks.deny"], audit
        assert audit["executed"][1]["task_id"] == task_id and audit["executed"][3]["task_id"] == denied_id
        assert session()["messages"] == 0 and not errors, errors
        assert not [message for message in rpc("snapshot")["messages"] if message["role"] in {"user","assistant"}]
        report.update(ok=True,approved_task_id=task_id,denied_task_id=denied_id,
            completed_step_results=1,approved_helper_posts=2,denied_helper_posts=1,
            saved_chat_messages=0,actual_move_once=True,denial_executed_nothing=True,wire=audit["wire"])
    finally:
        report["seconds"] = round(time.monotonic()-started,3)
        report["page_errors"] = errors
        if read_only:
            try:
                stats = httpx.get(base.removesuffix("/v1") + "/qa/queued-task", timeout=3, trust_env=False).json()
                report["provider_requests"] = {"requests": stats["requests"], "kinds": stats["kinds"]}
            except Exception:
                report["provider_request_diagnostics_unavailable"] = True
        report["audit"] = page.evaluate("()=>{const a=window.__qaQueued;return {posts:a.posts,metadataReads:a.metadataReads,blocked:a.blocked,blockedOperations:a.blockedOperations,prepared:a.prepared,executed:a.executed,approvals:a.approvals,helperRequests:a.helperRequests,helperCompletions:a.helperCompletions,helperErrors:a.helperErrors,wire:a.wire,nativeReads:a.nativeReads,readEvents:a.readEvents,readClaims:a.readClaims,readCompletions:a.readCompletions,readTaskIds:a.readTaskIds,readPredicates:a.readPredicates};}")
        report["wire"] = report["audit"]["wire"]
        if not report["ok"]:
            report["approval_panes"] = frame.locator('[aria-label="Queued task approvals"]').all_text_contents()
            report["chat_frame_tail"] = frame.locator("body").inner_text()[-3000:]
            page.screenshot(path=str(fixture / "queued-ui-failure.png"))
        page.evaluate("async()=>await window.__qaQueuedRestore?.()")
        print(json.dumps(report))


def queued_read_guard_cases():
    """Pure protocol guards: no browser, provider, filesystem or native execution."""
    path = (CHECKOUT_ROOT / ".cache/desktop-test/queued-task-1234abcd" / ("queued-read-" + "a" * 32 + ".txt")).as_posix()
    def payload(spec):
        return {"model": "google/gemma-4-e4b", "messages": [
            {"role": "system", "content": 'Owned synthetic planner\nSchemas:\n{"name":"read_file","parameters":{"path":"absolute"}}\n'
             + "Preserve the requested language and JSON/code output format.\n"
             + "Return exactly one valid JSON value, without Markdown fences or commentary."},
            {"role": "user", "content": "Goal: " + READ_GOAL_PREFIX + json.dumps(spec)}]}
    reply = queued_task_response(payload({"path": path}))
    step = json.loads(reply["content"])["steps"]
    assert reply["kind"] == "plan" and len(step) == 1
    assert step[0]["tool"] == "read_file" and step[0]["parameters"] == {"path": path}
    for spec in ({"path": path, "destination": path + ".copy"},
                 {"path": "D:/Users/private.txt"}, {"path": path.replace("queued-read-", "../queued-read-")},
                 {"path": path + ":hidden"}, {"path": path.replace("D:/", "//host/")}):
        try:
            queued_task_response(payload(spec))
        except AssertionError:
            pass
        else:
            raise AssertionError("Synthetic planner accepted an unowned/ambiguous read path")
    missing = payload({"path": path})
    missing["messages"][0]["content"] = 'Owned synthetic planner\nSchemas:\n{"name":"file_controller"}\n'
    discovered = queued_task_response(missing)
    assert discovered["kind"] == "schema_discovery" and json.loads(discovered["content"]) == {"request_tools": ["read_file"]}
    summary = payload({"path": path})
    summary["messages"][1]["content"] = "User goal: " + READ_GOAL_PREFIX + json.dumps({"path": path}) + "\nCompleted steps: Read the exact owned queued QA fixture"
    assert queued_task_response(summary)["kind"] == "summary"
    replan = payload({"path": path})
    replan["messages"][1]["content"] += '\nAlready completed (do not repeat): []'
    try:
        queued_task_response(replan)
    except (AssertionError, json.JSONDecodeError):
        pass
    else:
        raise AssertionError("Fixture accepted a hidden replan as the first plan")
    return {"ok": True, "pure_guard_cases": 9, "inference_calls": 0, "native_reads": 0}


def queued_read_audit_guard_script():
    """Exercise the actual JS audit with serialized IPC shapes; every native call is mocked."""
    path = (CHECKOUT_ROOT / ".cache/desktop-test/queued-task-1234abcd" / ("queued-read-" + "a" * 32 + ".txt")).as_posix()
    return "const install=" + NATIVE_AUDIT + ";\nconst path=" + json.dumps(path) + ";\n" + r"""
const options={provider:'fake',readOnly:true,session:'qa-session',readPath:path,folder:path.slice(0,path.lastIndexOf('/')),moves:[],goals:[]};
const request=JSON.parse(JSON.stringify({task_id:'qa-task',nonce:'qa-nonce',owner:'qa-owner',session:'qa-session',route:'qa-route',workspace:JSON.stringify(['local',options.folder,options.folder]),name:'read_file',args:{path}}));
for(const enriched of [false,true]){
const event=JSON.parse(JSON.stringify(enriched?{...request,request_id:null,monotonic:123.456}:request));
let listener;
globalThis.window={__JARVIS_NATIVE__:{event:{listen:async(_event,callback)=>{listener=callback;return()=>{};}},core:{invoke:async(command,args)=>{
 if(command==='backend'&&args.method==='tasks.ui.pending')return [request];
 if(command==='backend'&&args.method==='tasks.ui.claim')return {token:'qa-token'};
 if(command==='backend'&&args.method==='tasks.ui.complete')return {ok:true};
 if(command==='fs_read_file')return {content:'42'};
 throw Error('Unexpected mocked native command');
}}}};
await install(JSON.parse(JSON.stringify(options)));
listener({payload:{event:'tasks.ui.read',data:event}});
const invoke=window.__JARVIS_NATIVE__.core.invoke;
await invoke('backend',{method:'tasks.ui.claim',params:event});
await invoke('fs_read_file',{path});
await invoke('backend',{method:'tasks.ui.complete',params:{task_id:request.task_id,owner:request.owner,session:request.session,route:request.route,workspace:request.workspace,token:'qa-token',result:{content:'42'}}});
const audit=window.__qaQueued;
if(audit.blocked||audit.readEvents!==1||audit.readClaims!==1||audit.nativeReads!==1||audit.readCompletions!==1)throw Error('Serialized backend read shape rejected');
for(const invalid of [{...event,unexpected_transport:'must refuse'},{...event,args:{path:'D:/unowned.txt'}}]){
 let rejected=false;try{listener({payload:{event:'tasks.ui.read',data:invalid}});}catch{rejected=true;}
 if(!rejected)throw Error('Unexpected extra or unowned read admitted');
}
if(audit.blocked!==2)throw Error('Malformed event guards did not reject');
}
console.log(JSON.stringify({ok:true,actualAuditSerializedIpcCases:2,rejectedUnknownOrUnowned:4,appCalls:0,modelCalls:0}));
"""
