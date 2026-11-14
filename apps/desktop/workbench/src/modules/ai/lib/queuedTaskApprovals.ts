export type QueuedTaskRequest = {
  task_id: string;
  approval_id: string;
  name: string;
  args: Record<string, unknown>;
};
export type QueuedTaskCard = {
  key: string;
  goal: string;
  request: QueuedTaskRequest;
  callId: string;
  phase: "preparing" | "approval" | "running" | "error";
  error?: unknown;
  decision?: boolean;
};
type Action = { name: string; args: Record<string, unknown> };
type Dependencies = {
  prepare: (input: Action, callId: string) => Promise<boolean>;
  execute: (input: Action, callId: string, signal?: AbortSignal, nativeRequestId?:string) => Promise<unknown>;
  revoke: (callId: string) => void;
};

/** Same owned-message shape consumed by the host displayError/errorText transport. */
export class QueuedTaskUiError extends Error {
  source = "jarvis" as const;
  values: [] = [];
}

export function queuedWorkspaceFingerprint(scope:string,cwd:string|null,root:string|null):string|null {
  if(!scope)return null;
  const value=JSON.stringify([scope,cwd,root]);
  return new TextEncoder().encode(value).length<=8192?value:null;
}
export type QueuedUiRead = Action & {task_id:string;nonce:string;owner:string;session:string;route:string;workspace:string;dispatch?:"utility"|"mcp";cleanup_nonce?:string};
type ReadDependencies = Dependencies & {
  current: () => string | null;
  readable: (name:string) => boolean;
  claim: (request:QueuedUiRead) => Promise<{token:string}>;
  complete: (request:{task_id:string;owner:string;session:string;route:string;workspace:string;token:string;result?:unknown;error?:boolean}) => Promise<unknown>;
  drained?: () => void;
};

/** One-use out-of-band read callbacks, using the existing canonical action receipt. */
export class QueuedUiReadController {
  private active = new Map<string,{id:string;abort:AbortController}>();
  private seen = new Set<string>();
  private disposed = false;
  private owner:string; private session:string; private route:string; private deps:ReadDependencies;
  constructor(owner:string,session:string,route:string,deps:ReadDependencies) {
    this.owner=owner;this.session=session;this.route=route;this.deps=deps;
  }
  async receive(value:unknown) {
    if(this.disposed||!value||typeof value!=='object')return;
    const request=structuredClone(value) as QueuedUiRead;
    if("dispatch" in request)return;
    if(request.owner!==this.owner||request.session!==this.session||request.route!==this.route
      ||![request.task_id,request.nonce,request.name].every(v=>typeof v==='string'&&v.length>0&&v.length<=128)
      ||!request.args||typeof request.args!=='object'||Array.isArray(request.args)
      ||JSON.stringify(request).length>48000||this.seen.has(request.nonce)||this.active.size>=16)return;
    const identity=this.deps.current();if(identity===null||request.workspace!==identity)return;
    this.seen.add(request.nonce);if(this.seen.size>256)this.seen.delete(this.seen.values().next().value!);
    const id=crypto.randomUUID(),abort=new AbortController();
    this.active.set(request.nonce,{id,abort});
    const timer=setTimeout(()=>abort.abort(),120000);
    let token:string|undefined;
    let onAbort:(()=>void)|undefined;
    const check=()=>{abort.signal.throwIfAborted();if(this.disposed||identity!==this.deps.current())throw new QueuedTaskUiError("Queued task action failed");};
    try {
      check();token=(await this.deps.claim(request)).token;check();
      if(!this.deps.readable(request.name)||await this.deps.prepare(request,id))throw new QueuedTaskUiError("Queued task action failed");
      check();const cancelled=new Promise<never>((_resolve,reject)=>{
        onAbort=()=>reject(new DOMException("Aborted","AbortError"));
        abort.signal.addEventListener("abort",onAbort,{once:true});
        if(abort.signal.aborted)onAbort();
      });
      const result=await Promise.race([cancelled,this.deps.execute(request,id,abort.signal)]);check();
      if(result==null||new TextEncoder().encode(JSON.stringify(result)).length>48000)throw new QueuedTaskUiError("Queued task action failed");
      await this.deps.complete({task_id:request.task_id,owner:this.owner,session:this.session,route:this.route,workspace:identity,token,result:result??null});
    } catch {
      if(token)await this.deps.complete({task_id:request.task_id,owner:this.owner,session:this.session,route:this.route,workspace:identity,token,error:true}).catch(()=>{});
    } finally {
      clearTimeout(timer);if(onAbort)abort.signal.removeEventListener("abort",onAbort);
      this.deps.revoke(id);this.active.delete(request.nonce);
      if(!this.disposed)this.deps.drained?.();
    }
  }
  clear() {for(const item of this.active.values()){item.abort.abort();this.deps.revoke(item.id);}this.active.clear();}
  cancel(value:unknown) {
    if(!value||typeof value!=="object")return;
    const request=value as QueuedUiRead;
    if(request.owner===this.owner&&request.session===this.session&&request.route===this.route)
      this.active.get(request.nonce)?.abort.abort();
  }
  dispose(){this.disposed=true;this.clear();}
}

export type QueuedNativeCard = {
  key:string; callId:string; request:QueuedUiRead;
  phase:"preparing"|"approval"|"running"; decision?:boolean;
};
type NativeItem = QueuedNativeCard & {
  identity:string; abort:AbortController; token?:string; completing:boolean; claimSettled:boolean; executing:boolean;
  attemptedStart:boolean; settlementKnown:boolean;
  timer:ReturnType<typeof setTimeout>; heartbeat?:ReturnType<typeof setInterval>;
};
type NativeDependencies = Dependencies & {
  current:()=>string|null;
  dispatchable:(name:string,kind:"utility"|"mcp")=>boolean;
  preparation?:(input:Action,callId:string)=>"ask"|"allow"|"unavailable";
  claim:ReadDependencies["claim"];
  complete:(request:Parameters<ReadDependencies["complete"]>[0]&{cancelled?:boolean})=>Promise<unknown>;
  alive:(request:{task_id:string;owner:string;session:string;route:string;workspace:string;token:string})=>Promise<unknown>;
  start:(request:Parameters<NativeDependencies["alive"]>[0])=>Promise<{ok:boolean;waiting?:boolean}>;
  settled:(request:Parameters<NativeDependencies["alive"]>[0]&{nonce:string;kind?:"mcp";cleanup_nonce?:string})=>Promise<unknown>;
  probeNative:(nonce:string,kind?:"mcp",cleanupNonce?:string)=>Promise<{settled:boolean}>;
  changed:(cards:QueuedNativeCard[])=>void;
  drained?:()=>void;
};
const nativeCleanupClaims=new Map<string,Parameters<NativeDependencies["settled"]>[0]>();

/** Approval and execution stay in the same canonical receipt's owning closure. */
export class QueuedNativeDispatchController {
  private items=new Map<string,NativeItem>();
  private seen=new Set<string>();
  private disposed=false;
  private recovering=new Set<string>();
  private owner:string;private session:string;private route:string;private deps:NativeDependencies;
  constructor(owner:string,session:string,route:string,deps:NativeDependencies){
    this.owner=owner;this.session=session;this.route=route;this.deps=deps;
  }
  private publish(){if(!this.disposed)this.deps.changed([...this.items.values()].map(({key,callId,request,phase,decision})=>({key,callId,request,phase,decision})));}
  private check(item:NativeItem){
    item.abort.signal.throwIfAborted();
    if(this.disposed||this.items.get(item.key)!==item||this.deps.current()!==item.identity)
      throw new QueuedTaskUiError("Queued task action failed");
  }
  private origin(item:NativeItem){
    return {task_id:item.request.task_id,nonce:item.request.nonce,owner:this.owner,session:this.session,route:this.route,workspace:item.identity,token:item.token!,
      ...(item.request.dispatch==="mcp"?{kind:"mcp" as const,cleanup_nonce:item.request.cleanup_nonce}:{})};
  }
  private async finish(item:NativeItem,extra:{result?:unknown;error?:boolean;cancelled?:boolean}){
    if(item.completing||!item.claimSettled)return;
    item.completing=true;
    clearTimeout(item.timer);if(item.heartbeat)clearInterval(item.heartbeat);
    this.deps.revoke(item.callId);
    try{if(item.token)await this.deps.complete({...this.origin(item),...extra});}
    catch{/* Never repeat execution when the result transport is uncertain. */}
    finally{this.items.delete(item.key);this.publish();this.deps.drained?.();}
  }
  private abort(item:NativeItem){
    item.abort.abort();
    item.decision=false;this.publish();
    clearTimeout(item.timer);if(item.heartbeat)clearInterval(item.heartbeat);
    // A running canonical utility returns only after native process cleanup.
    // Keep the cancellation pending until that invocation settles.
    if(!item.executing)void this.finish(item,{error:true,cancelled:true});
  }
  async receive(value:unknown){
    if(this.disposed||!value||typeof value!=="object")return;
    const request=structuredClone(value) as QueuedUiRead&{dispatch?:unknown};
    if((request.dispatch!=="utility"&&request.dispatch!=="mcp")||request.owner!==this.owner||request.session!==this.session||request.route!==this.route
      ||(request.dispatch==="mcp"&&(!/^[a-f0-9]{32}$/.test(request.nonce)||typeof request.cleanup_nonce!=="string"||!/^[a-f0-9]{32}$/.test(request.cleanup_nonce)))
      ||![request.task_id,request.nonce,request.name].every(v=>typeof v==="string"&&v.length>0&&v.length<=128)
      ||!request.args||typeof request.args!=="object"||Array.isArray(request.args)
      ||new TextEncoder().encode(JSON.stringify(request)).length>48000||this.seen.has(request.nonce)||this.items.size>=16)return;
    const identity=this.deps.current();
    if(identity===null||identity!==request.workspace||!this.deps.dispatchable(request.name,request.dispatch))return;
    this.seen.add(request.nonce);if(this.seen.size>256)this.seen.delete(this.seen.values().next().value!);
    const item:NativeItem={key:request.nonce,request,callId:crypto.randomUUID(),phase:"preparing",identity,
      abort:new AbortController(),completing:false,claimSettled:false,executing:false,attemptedStart:false,settlementKnown:false,
      timer:setTimeout(()=>this.abort(item),900000)};
    this.items.set(item.key,item);this.publish();
    try{
      item.token=(await this.deps.claim(request)).token;item.claimSettled=true;this.check(item);
      item.heartbeat=setInterval(()=>{
        try{this.check(item);}catch{this.abort(item);return;}
        void this.deps.alive(this.origin(item)).catch(()=>this.abort(item));
      },500);
      const required=await this.deps.prepare(request,item.callId);
      // Stop may revoke before asynchronous preparation publishes its receipt.
      // Revoke the late receipt as well, even if cancellation already finished.
      try{this.check(item);}catch(error){this.deps.revoke(item.callId);throw error;}
      const policy=request.dispatch==="mcp"?this.deps.preparation?.(request,item.callId):required?"ask":"unavailable";
      if(policy!=="ask"&&policy!=="allow")throw new QueuedTaskUiError("Queued task action failed");
      this.check(item);item.phase="approval";this.publish();
      if(policy==="allow")await this.respond(item.key,true);
    }catch{item.claimSettled=true;await this.finish(item,{error:true,cancelled:item.abort.signal.aborted});}
  }
  async respond(key:string,approved:boolean){
    const item=this.items.get(key);
    if(!item||item.phase!=="approval"||item.completing)return;
    item.phase="running";item.decision=approved;this.publish();
    if(!approved){this.abort(item);return;}
    try{
      this.check(item);await this.deps.alive(this.origin(item));this.check(item);
      for(;;){
        item.attemptedStart=true;
        const started=await this.deps.start(this.origin(item));this.check(item);
        if(started.ok)break;
        if(!started.waiting)throw new QueuedTaskUiError("Queued task action failed");
        await new Promise(resolve=>setTimeout(resolve,250));this.check(item);
      }
      item.executing=true;
      const result=await this.deps.execute(item.request,item.callId,item.abort.signal,item.request.nonce);
      item.settlementKnown=item.request.dispatch==="mcp"
        ?Boolean(result&&typeof result==="object"&&"transport_settled" in result&&result.transport_settled===true)
        :Boolean(result&&typeof result==="object"&&"tool" in result&&typeof result.tool==="string"
        &&"stdout" in result&&typeof result.stdout==="string"&&"stderr" in result&&typeof result.stderr==="string"
        &&"exit_code" in result&&"timed_out" in result&&"truncated" in result&&"output_complete" in result);
      this.check(item);
      if(result==null||new TextEncoder().encode(JSON.stringify(result)).length>48000)
        throw new QueuedTaskUiError("Queued task action failed");
      await this.finish(item,{result});
    }catch{const cancelled=item.abort.signal.aborted;item.abort.abort();await this.finish(item,{error:true,cancelled});}
    finally{
      if(item.attemptedStart){
        const origin={...this.origin(item),nonce:item.request.nonce};
        nativeCleanupClaims.set(origin.nonce,origin);
        if(!item.executing||item.settlementKnown){
          try{await this.deps.settled(origin);nativeCleanupClaims.delete(origin.nonce);}catch{/* Retry cleanup acknowledgement only. */}
        }
        else await this.recover(origin);
        this.deps.drained?.();
      }
    }
  }
  async recover(value:unknown):Promise<boolean>{
    if(!value||typeof value!=="object")return false;
    const request=value as Parameters<NativeDependencies["settled"]>[0];
    if(![request.task_id,request.nonce,request.token,request.owner,request.session,request.route,request.workspace]
      .every(value=>typeof value==="string"&&value.length>0)||this.recovering.has(request.nonce))return false;
    this.recovering.add(request.nonce);
    try{
      if(request.kind!==undefined&&request.kind!=="mcp")return false;
      if(request.kind==="mcp"&&(typeof request.cleanup_nonce!=="string"||!/^[a-f0-9]{32}$/.test(request.cleanup_nonce)))return false;
      if(!(await this.deps.probeNative(request.nonce,request.kind,request.cleanup_nonce)).settled)return false;
      await this.deps.settled(request);nativeCleanupClaims.delete(request.nonce);return true;
    }catch{return false;}
    finally{this.recovering.delete(request.nonce);}
  }
  async recoverPending():Promise<boolean>{
    const recovered=await Promise.all([...nativeCleanupClaims.values()].map(value=>this.recover(value)));
    return recovered.every(Boolean);
  }
  cancel(value:unknown){
    if(!value||typeof value!=="object")return;
    const request=value as QueuedUiRead,item=this.items.get(request.nonce);
    if(item&&request.task_id===item.request.task_id&&request.owner===this.owner&&request.session===this.session
      &&request.route===this.route&&request.workspace===item.identity)this.abort(item);
  }
  clear(){for(const item of this.items.values())this.abort(item);}
  dispose(){this.disposed=true;this.clear();}
}

/** Independent UI decisions only: no chat messages, model continuation or saved transcript parts. */
export class QueuedTaskApprovalController {
  private cards = new Map<string, QueuedTaskCard>();
  private seen = new Set<string>();
  private disposed = false;
  private generation = 0;
  private ids = new Set<string>();
  private owner: string;
  private session: string;
  private deps: Dependencies;
  private changed: (cards: QueuedTaskCard[]) => void;
  constructor(owner: string, session: string, deps: Dependencies, changed: (cards: QueuedTaskCard[]) => void) {
    this.owner = owner; this.session = session; this.deps = deps; this.changed = changed;
  }

  private publish() { if (!this.disposed) this.changed([...this.cards.values()]); }
  private check(output: unknown) {
    if (output && typeof output === "object" && ("error" in output || ("ok" in output && output.ok === false)))
      throw "error" in output ? output.error : new QueuedTaskUiError("Queued task action failed");
  }
  private release(id: string) { this.deps.revoke(id); this.ids.delete(id); }

  async receive(payload: unknown) {
    if (this.disposed || !payload || typeof payload !== "object") return;
    const event = payload as Record<string, unknown>;
    if (event.owner !== this.owner || event.session !== this.session) return;
    const action = event.request as {name?: unknown; args?: unknown} | undefined;
    if (action?.name !== "tasks.resume" || !action.args || typeof action.args !== "object") return;
    const request = action.args as QueuedTaskRequest;
    if (![request.task_id, request.approval_id, request.name].every(item => typeof item === "string" && item.length > 0 && item.length <= 128)
      || !request.args || typeof request.args !== "object" || Array.isArray(request.args)
      || JSON.stringify(request).length > 50000) return;
    const key = `${request.task_id}:${request.approval_id}`;
    if (this.seen.has(key) || this.cards.size >= 16) return;
    this.seen.add(key);
    if (this.seen.size > 256) this.seen.delete(this.seen.values().next().value!);
    const card: QueuedTaskCard = {key, request: structuredClone(request),
      goal: typeof event.goal === "string" ? event.goal.slice(0, 256) : "", callId: crypto.randomUUID(), phase: "preparing"};
    this.cards.set(key, card);
    this.ids.add(card.callId);
    this.publish();
    const generation = this.generation;
    try {
      const required = await this.deps.prepare({name:"tasks.resume", args:card.request}, card.callId);
      if (this.disposed || generation !== this.generation) { this.release(card.callId); return; }
      card.phase = "approval";
      this.publish();
      if (!required) await this.respond(key, true);
    } catch (error) {
      this.release(card.callId);
      if (!this.disposed && generation === this.generation) { card.phase = "error"; card.error = error; this.publish(); }
    }
  }

  async respond(key: string, approved: boolean) {
    const card = this.cards.get(key);
    if (this.disposed || !card || (card.phase !== "approval" && !(card.phase === "error" && !approved))) return;
    card.phase = "running"; // Claim before awaiting: repeated clicks cannot execute twice.
    card.decision = approved;
    this.publish();
    let denyId: string | undefined;
    const generation = this.generation;
    try {
      if (approved) {
        const output = await this.deps.execute({name:"tasks.resume", args:card.request}, card.callId);
        this.check(output);
      } else {
        this.release(card.callId);
        const input = {name:"tasks.deny", args:{task_id:card.request.task_id, approval_id:card.request.approval_id}};
        denyId = crypto.randomUUID(); this.ids.add(denyId);
        if (await this.deps.prepare(input, denyId)) throw new QueuedTaskUiError("Task denial unexpectedly requires approval");
        if (this.disposed || generation !== this.generation) return;
        this.check(await this.deps.execute(input, denyId));
      }
      this.cards.delete(key);
    } catch (error) {
      if (!this.disposed && generation === this.generation) { card.phase = "error"; card.error = error; }
    } finally {
      this.release(card.callId);
      if (denyId) this.release(denyId);
      this.publish();
    }
  }

  clear() {
    this.generation++;
    for (const id of this.ids) this.deps.revoke(id);
    this.ids.clear(); this.cards.clear();
    this.publish();
  }
  dispose() {
    this.disposed = true;
    this.clear();
  }
}
