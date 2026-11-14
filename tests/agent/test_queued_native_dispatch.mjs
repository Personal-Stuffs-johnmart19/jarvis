import assert from 'node:assert/strict';
import test from 'node:test';
import { QueuedNativeDispatchController, QueuedUiReadController } from '../../apps/desktop/workbench/src/modules/ai/lib/queuedTaskApprovals.ts';
import { queuedUtility, isQueuedUtility } from '../../apps/desktop/workbench/src/modules/ai/tools/queuedDispatch.ts';
import { runCancellableProcess } from '../../apps/desktop/workbench/src/modules/ai/lib/processExecution.ts';
import { explicitAgentStop, subscribeExplicitAgentStop } from '../../apps/desktop/workbench/src/modules/ai/lib/explicitAgentStop.ts';

const request={task_id:'task',nonce:'nonce',owner:'owner',session:'session',route:'route',workspace:'workspace',
  dispatch:'utility',name:'utility_run',args:{tool:'python',args:['literal space','$value','a&b','Привет'],cwd:'D:/owned'}};
const result={tool:'python',stdout:'42',stderr:'',exit_code:0,timed_out:false,truncated:false,decoding_loss:false,output_complete:true};
function setup(t,overrides={}){
  const calls={claim:[],prepare:[],execute:[],complete:[],revoke:[],alive:[],start:[],settled:[],probe:[]},cards=[];
  const deps={current:()=>request.workspace,dispatchable:()=>true,
    claim:async value=>{calls.claim.push(structuredClone(value));return {token:'one-use'};},
    prepare:async(...values)=>{calls.prepare.push(structuredClone(values));return true;},
    execute:async(...values)=>{calls.execute.push(values);return result;},
    complete:async value=>{calls.complete.push(value);return {ok:true};},
    alive:async value=>{calls.alive.push(value);return {ok:true};},
    start:async value=>{calls.start.push(value);return {ok:true};},
    settled:async value=>{calls.settled.push(value);return {ok:true};},
    probeNative:async value=>{calls.probe.push(value);return {settled:true};},
    revoke:value=>calls.revoke.push(value),changed:rows=>{cards.splice(0,cards.length,...rows);},...overrides};
  const controller=new QueuedNativeDispatchController('owner','session','route',deps);
  t.after(()=>controller.dispose());
  return {controller,calls,cards};
}
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));

test('utility approval binds exact literal args and executes one canonical receipt once',async t=>{
  const {controller,calls,cards}=setup(t),input=structuredClone(request);
  await Promise.all([controller.receive(input),controller.receive(input)]);
  assert.equal(cards[0].phase,'approval');assert.equal(calls.execute.length,0);
  input.args.args[0]='changed';
  await Promise.all([controller.respond('nonce',true),controller.respond('nonce',true)]);
  assert.equal(calls.claim.length,1);assert.equal(calls.prepare.length,1);assert.equal(calls.execute.length,1);
  assert.equal(calls.start.length,1);
  assert.deepEqual(calls.execute[0][0].args,request.args);
  assert.equal(calls.execute[0][1],calls.prepare[0][1]);
  assert.ok(calls.execute[0][2] instanceof AbortSignal);
  assert.deepEqual(calls.complete[0].result,result);assert.equal(cards.length,0);
  assert.equal(calls.settled.length,1);assert.equal(calls.probe.length,0);
  await controller.receive(request);assert.equal(calls.execute.length,1);
});

test('Deny consumes the prepared receipt without any process execution',async t=>{
  const {controller,calls}=setup(t);await controller.receive(request);await controller.respond('nonce',false);await settle();
  assert.equal(calls.execute.length,0);assert.equal(calls.complete.length,1);
  assert.equal(calls.start.length,0);
  assert.equal(calls.complete[0].cancelled,true);assert.equal(calls.revoke.length,1);
});

test('approved action waits for its queue concurrency slot without executing early',async t=>{
  let attempts=0;
  const {controller,calls}=setup(t,{start:async()=>++attempts===1?{ok:false,waiting:true}:{ok:true}});
  await controller.receive(request);const running=controller.respond('nonce',true);
  await settle();assert.equal(calls.execute.length,0);
  await running;assert.equal(attempts,2);assert.equal(calls.execute.length,1);
});

test('task-specific backend cancellation reaches canonical native request cancellation',async t=>{
  let finish,signal;const nativeCancels=[];
  const {controller,calls}=setup(t,{execute:async(_input,_id,abortSignal)=>{
    signal=abortSignal;
    return runCancellableProcess(()=>new Promise(resolve=>{finish=resolve;}),async id=>{nativeCancels.push(id);finish(result);},abortSignal,()=> 'owned-native-request');
  }});
  await controller.receive(request);const running=controller.respond('nonce',true);
  while(!signal)await settle();
  controller.cancel({...request,nonce:'foreign'});assert.equal(signal.aborted,false);
  controller.cancel(request);assert.equal(signal.aborted,true);
  await running;assert.deepEqual(nativeCancels,['owned-native-request']);
  assert.equal(calls.complete.length,1);assert.equal(calls.complete[0].cancelled,true);
});

test('global Stop aborts locally despite backend failure and acknowledges only after physical settlement',async t=>{
  let finish,signal,physicallySettled=false;const cancelled=[];
  const {controller,calls}=setup(t,{execute:async(_input,_id,value,nativeId)=>{
    signal=value;
    return runCancellableProcess(()=>new Promise(resolve=>{finish=resolve;}),async id=>{cancelled.push(id);},value,()=>nativeId);
  },probeNative:async()=>({settled:physicallySettled})});
  const unsubscribe=subscribeExplicitAgentStop(()=>controller.clear());t.after(unsubscribe);
  await controller.receive(request);const running=controller.respond('nonce',true);
  while(!signal)await settle();
  await explicitAgentStop(()=>{},async()=>{throw Error('Python disconnected');});
  assert.equal(signal.aborted,true);assert.deepEqual(cancelled,[request.nonce]);
  assert.equal(calls.settled.length,0);assert.equal(calls.complete.length,0);
  physicallySettled=true;finish(result);await running;
  assert.equal(calls.settled.length,1);assert.equal(calls.settled[0].nonce,request.nonce);
});

test('orphan recovery never releases a reservation before native admission is sealed and settled',async t=>{
  let physicallySettled=false;
  const {controller,calls}=setup(t,{probeNative:async()=>({settled:physicallySettled})});
  const original={task_id:'old-task',nonce:'old-nonce',owner:'old-owner',session:'old-session',route:'old-route',workspace:'old-workspace',token:'private-token'};
  assert.equal(await controller.recover(original),false);assert.equal(calls.settled.length,0);
  physicallySettled=true;assert.equal(await controller.recover(original),true);
  assert.deepEqual(calls.settled,[original]);assert.equal(calls.execute.length,0);
});

test('backend liveness loss aborts active native work even without cancel notification',{timeout:2000},async t=>{
  let finish,signal,alive=0;
  const {controller,calls}=setup(t,{alive:async()=>{if(++alive>1)throw Error('task cancelled');},
    execute:async(_input,_id,value)=>{signal=value;return runCancellableProcess(()=>new Promise(resolve=>{finish=resolve;}),async()=>finish(result),value);}});
  await controller.receive(request);const running=controller.respond('nonce',true);
  while(!signal)await settle();await running;
  assert.equal(signal.aborted,true);assert.equal(calls.complete.length,1);
});

test('Stop while claim is pending consumes the eventual lease without preparing or executing',async t=>{
  let claimed;
  const {controller,calls}=setup(t,{claim:()=>new Promise(resolve=>{claimed=resolve;})});
  const pending=controller.receive(request);controller.clear();claimed({token:'late-token'});await pending;
  assert.equal(calls.prepare.length,0);assert.equal(calls.execute.length,0);
  assert.equal(calls.complete[0].token,'late-token');assert.equal(calls.complete[0].cancelled,true);
});

test('toolset or workspace replacement during preparation revokes without execution',async t=>{
  let identity=request.workspace;
  const receipts=new Set(),removed=[];
  const {controller,calls,cards}=setup(t,{current:()=>identity,
    prepare:async(input,id)=>{calls.prepare.push([input,id]);receipts.add(id);identity=null;return true;},
    revoke:id=>{calls.revoke.push(id);removed.push(receipts.delete(id));}});
  await controller.receive(request);assert.equal(calls.execute.length,0);assert.equal(calls.complete[0].error,true);
  assert.equal(calls.prepare.length,1);
  const callId=calls.prepare[0][1];
  assert.deepEqual(calls.revoke,[callId,callId]);
  assert.deepEqual(removed,[true,false]); // Late-receipt revocation and final cleanup are idempotent.
  assert.equal(receipts.size,0);assert.equal(cards.length,0);
});

test('result transport failure cannot replay a possibly completed native action',async t=>{
  const {controller,calls}=setup(t,{complete:async()=>{throw Error('IPC unavailable');}});
  await controller.receive(request);await controller.respond('nonce',true);
  await controller.receive(request);await controller.respond('nonce',true);
  assert.equal(calls.execute.length,1);assert.equal(calls.revoke.length,1);
});

test('failed process payload is retained verbatim for backend outcome classification',async t=>{
  const failed={...result,exit_code:7,stderr:'owned failure'};
  const {controller,calls}=setup(t,{execute:async()=>failed});
  await controller.receive(request);await controller.respond('nonce',true);
  assert.deepEqual(calls.complete[0].result,failed);
});

for(const [label,overrides] of [['no approval',{prepare:async()=>false}],['no executor opt-in',{dispatchable:()=>false}],
  ['wrong owner',{request:{...request,owner:'other'}}],['wrong route',{request:{...request,route:'other'}}]]){
  test(`${label} cannot execute a native queue action`,async t=>{
    const {request:input=request,...deps}=overrides;
    const {controller,calls}=setup(t,deps);await controller.receive(input);await controller.respond('nonce',true);
    assert.equal(calls.execute.length,0);
  });
}

test('read controller never claims execution dispatch, and approval alone cannot opt in',async()=>{
  let claimed=0;
  const reads=new QueuedUiReadController('owner','session','route',{current:()=>request.workspace,
    readable:()=>true,claim:async()=>{claimed++;return {token:'wrong'};},prepare:async()=>false,
    execute:async()=>result,complete:async()=>{},revoke:()=>{}});
  await reads.receive(request);reads.dispose();assert.equal(claimed,0);
  assert.equal(isQueuedUtility({needsApproval:true}),false);
  assert.throws(()=>queuedUtility({needsApproval:false}));
  assert.equal(isQueuedUtility(queuedUtility({needsApproval:true})),true);
});

const mcpRequest={...request,nonce:'a'.repeat(32),dispatch:'mcp',name:'mcp_call',cleanup_nonce:'b'.repeat(32),
  args:{server_id:'fake',tool_name:'write',arguments:{path:'opaque/non-filesystem'}}};
const mcpEnvelope={result:{content:[{type:'text',text:'written'}],structuredContent:{id:42}},transport_settled:true,output_complete:true};
for(const policy of ['ask','allow','unavailable']){
  test(`MCP ${policy} follows typed normal policy with one prepared receipt`,async t=>{
    const {controller,calls,cards}=setup(t,{prepare:async()=>policy==='ask',preparation:()=>policy,
      dispatchable:(name,kind)=>name==='mcp_call'&&kind==='mcp',execute:async()=>{calls.execute.push(true);return mcpEnvelope;}});
    await controller.receive(mcpRequest);
    if(policy==='ask'){
      assert.equal(calls.execute.length,0);assert.deepEqual(cards[0].request.args,mcpRequest.args);
      await Promise.all([controller.respond(mcpRequest.nonce,true),controller.respond(mcpRequest.nonce,true)]);
    }
    assert.equal(calls.execute.length,policy==='unavailable'?0:1);
    if(policy!=='unavailable'){
      assert.deepEqual(calls.complete[0].result,mcpEnvelope);
      assert.equal(calls.settled[0].kind,'mcp');assert.equal(calls.settled[0].cleanup_nonce,mcpRequest.cleanup_nonce);
    }
  });
}

test('MCP uncertain rejection keeps operation-specific cleanup claim and never reexecutes',async t=>{
  let physical=false;const probes=[];
  const {controller,calls}=setup(t,{prepare:async()=>true,preparation:()=> 'ask',
    execute:async()=>{calls.execute.push(true);throw Error('lost result');},
    probeNative:async(...args)=>{probes.push(args);return {settled:physical};}});
  await controller.receive(mcpRequest);await controller.respond(mcpRequest.nonce,true);
  assert.equal(calls.settled.length,0);assert.deepEqual(probes[0],[mcpRequest.nonce,'mcp',mcpRequest.cleanup_nonce]);
  physical=true;assert.equal(await controller.recoverPending(),true);
  assert.equal(calls.settled[0].kind,'mcp');
  await controller.receive(mcpRequest);assert.equal(calls.execute.length,1);
});

test('queued MCP uses one pinned native request, opaque operation and native settlement proof',async t=>{
  const {createMcpExecutor,createMcpOperation}=await import('../../apps/desktop/workbench/src/modules/mcp/client.ts');
  const nativeCalls=[];
  const nativeInvoke=async(command,args)=>{
    nativeCalls.push({command,args});
    if(command==='mcp_tool_info')return {policy:'ask',config_revision:'revision',connection_generation:'generation'};
    if(command==='mcp_call_tool')return mcpEnvelope.result;
    if(command==='mcp_cancel')return {settled:true};
  };
  const executor=createMcpExecutor(nativeInvoke,()=> 'session',runCancellableProcess,new Map());
  const {controller,calls}=setup(t,{
    prepare:(input,id)=>executor.prepare(input.args,id),preparation:(_input,id)=>executor.preparation(id),
    revoke:id=>executor.revoke(id),
    execute:async(input,id,signal,nonce)=>{
      const operation=createMcpOperation(nativeInvoke,{requestId:nonce,cleanupNonce:input.cleanup_nonce});
      const result=await executor.execute(input.args,id,signal,operation.context);
      return {result,transport_settled:await operation.settle(),output_complete:true};
    },
  });
  await controller.receive(mcpRequest);await controller.respond(mcpRequest.nonce,true);
  const physical=nativeCalls.filter(c=>c.command==='mcp_call_tool');
  assert.equal(physical.length,1);assert.equal(physical[0].args.requestId,mcpRequest.nonce);
  assert.equal(physical[0].args.cleanupNonce,mcpRequest.cleanup_nonce);
  assert.equal(physical[0].args.configRevision,'revision');assert.equal(physical[0].args.connectionGeneration,'generation');
  assert.deepEqual(calls.complete[0].result,mcpEnvelope);assert.equal(calls.settled.length,1);
  assert.equal(nativeCalls.filter(c=>c.command==='mcp_connect').length,1);
  assert.deepEqual(nativeCalls.find(c=>c.command==='mcp_cancel').args,{requestId:mcpRequest.nonce,cleanupNonce:mcpRequest.cleanup_nonce});
});

test('Stop during deferred MCP preparation revokes the late-published receipt',async t=>{
  const {createMcpExecutor}=await import('../../apps/desktop/workbench/src/modules/mcp/client.ts');
  let release,entered;
  const waiting=new Promise(resolve=>{entered=resolve;}),receipts=new Map();
  const executor=createMcpExecutor(async(command)=>{
    if(command==='mcp_tool_info'){entered();return new Promise(resolve=>{release=resolve;});}
  },()=> 'session',runCancellableProcess,receipts);
  const {controller,calls}=setup(t,{prepare:(input,id)=>executor.prepare(input.args,id),
    preparation:(_input,id)=>executor.preparation(id),revoke:id=>executor.revoke(id)});
  const receiving=controller.receive(mcpRequest);await waiting;
  controller.dispose();
  release({policy:'ask',config_revision:'revision',connection_generation:'generation'});
  await receiving;
  assert.equal(receipts.size,0);assert.equal(calls.execute.length,0);
});
