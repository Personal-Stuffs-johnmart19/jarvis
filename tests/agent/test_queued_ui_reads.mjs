import assert from 'node:assert/strict';
import test from 'node:test';
import { getEventListeners } from 'node:events';
import { QueuedUiReadController, queuedWorkspaceFingerprint } from '../../apps/desktop/workbench/src/modules/ai/lib/queuedTaskApprovals.ts';
import { queuedReadOnly, isQueuedReadOnly } from '../../apps/desktop/workbench/src/modules/ai/tools/queuedReadOnly.ts';

const request = { task_id:'task',nonce:'nonce',owner:'owner',session:'session',route:'route',workspace:'same-workspace',name:'read_file',args:{path:'D:/owned/42.txt'} };
function setup(overrides={}) {
  const calls={claim:[],prepare:[],execute:[],complete:[],revoke:[]};
  const deps={current:()=> 'same-workspace',readable:()=>true,
    claim:async value=>{calls.claim.push(value);return {token:'one-use'};},
    prepare:async (...args)=>{calls.prepare.push(args);return false;},
    execute:async (...args)=>{calls.execute.push(args);return {content:'42'};},
    complete:async value=>{calls.complete.push(value);return {ok:true};},
    revoke:value=>calls.revoke.push(value),...overrides};
  return {calls,controller:new QueuedUiReadController('owner','session','route',deps)};
}

test('own absolute read uses canonical closure once and returns same-task result without model continuation',async()=>{
  const {controller,calls}=setup();
  await Promise.all([controller.receive(request),controller.receive(request)]);
  assert.equal(calls.claim.length,1);assert.equal(calls.prepare.length,1);assert.equal(calls.execute.length,1);
  assert.deepEqual(calls.complete,[{task_id:'task',owner:'owner',session:'session',route:'route',workspace:'same-workspace',token:'one-use',result:{content:'42'}}]);
  assert.ok(calls.execute[0][2] instanceof AbortSignal);
  assert.equal(getEventListeners(calls.execute[0][2],'abort').length,0);
  assert.equal(calls.revoke.length,1);
});

test('foreign owner/session/provider route never claims a dispatch',async()=>{
  const {controller,calls}=setup();
  for(const field of ['owner','session','route','workspace'])await controller.receive({...request,[field]:'other'});
  assert.equal(calls.claim.length,0);
});

test('Stop drops late native results and completes only a terminal cancellation',async()=>{
  let finish,signal;
  const {controller,calls}=setup({execute:async(_input,_id,value)=>{signal=value;return new Promise(resolve=>{finish=resolve;});}});
  const pending=controller.receive(request);
  while(!finish)await new Promise(resolve=>setTimeout(resolve,0));
  controller.clear();assert.equal(signal.aborted,true);finish({content:'late'});await pending;
  assert.deepEqual(calls.complete,[{task_id:'task',owner:'owner',session:'session',route:'route',workspace:'same-workspace',token:'one-use',error:true}]);
});

test('workspace change after claim never executes against a different context',async()=>{
  let identity='same-workspace';
  const {controller,calls}=setup({current:()=>identity,claim:async()=>{identity='new';return {token:'one-use'};}});
  await controller.receive(request);
  assert.equal(calls.execute.length,0);assert.equal(calls.complete[0].error,true);
});

test('replaced canonical runtime before receive cannot capture an invalid sentinel',async()=>{
  const {controller,calls}=setup({current:()=>null});await controller.receive(request);
  assert.equal(calls.claim.length,0);assert.equal(calls.execute.length,0);
});

test('canonical runtime replacement during claim completes failure without execution',async()=>{
  let identity='same-workspace';
  const {controller,calls}=setup({current:()=>identity,claim:async()=>{identity=null;return {token:'one-use'};}});
  await controller.receive(request);assert.equal(calls.execute.length,0);assert.equal(calls.complete[0].error,true);
});

test('synchronous Stop inside execute cannot lose the abort notification',{timeout:1000},async()=>{
  let controller;
  const fixture=setup({execute:(_input,_id,signal)=>{controller.clear();assert.equal(signal.aborted,true);return new Promise(()=>{});}});
  controller=fixture.controller;await controller.receive(request);
  assert.equal(fixture.calls.complete[0].error,true);
});

test('harmless rerender with stable context does not invalidate the read',async()=>{
  const {controller,calls}=setup({current:()=>request.workspace});
  await controller.receive(request);assert.equal(calls.execute.length,1);assert.equal(calls.complete[0].error,undefined);
});

test('same paths in different WSL distributions are different workspace fingerprints',async()=>{
  const a=queuedWorkspaceFingerprint('wsl:Ubuntu','/home/owned','/home/owned');
  const b=queuedWorkspaceFingerprint('wsl:Debian','/home/owned','/home/owned');
  assert.notEqual(a,b);assert.equal(a,queuedWorkspaceFingerprint('wsl:Ubuntu','/home/owned','/home/owned'));
  const {controller,calls}=setup({current:()=>b});
  await controller.receive({...request,workspace:a});assert.equal(calls.claim.length,0);assert.equal(calls.execute.length,0);
});

for(const [label,overrides] of [['mutation',{readable:()=>false}],['risk change',{prepare:async()=>true}],
  ['missing output',{execute:async()=>undefined}],
  ['oversized output',{execute:async()=>({content:'x'.repeat(48001)})}],['native failure',{execute:async()=>{throw Error('failed');}}]]) {
  test(`${label} completes terminal failure without replay`,async()=>{
    const {controller,calls}=setup(overrides);await controller.receive(request);await controller.receive(request);
    assert.equal(calls.complete.length,1);assert.equal(calls.complete[0].error,true);
    if(label==='mutation'||label==='risk change')assert.equal(calls.execute.length,0);
  });
}

test('only explicit canonical opt-in is readable; approval policy cannot opt itself in',()=>{
  const plain={};assert.equal(isQueuedReadOnly(plain),false);
  assert.equal(isQueuedReadOnly(queuedReadOnly(plain,'path')),true);
  assert.throws(()=>queuedReadOnly({needsApproval:true},'path'));
  assert.throws(()=>queuedReadOnly({needsApproval:()=>false},'path'));
});

test('completion drains a seventeenth pending read instead of silently starving it',async()=>{
  let controller;const finishes=[];
  const last={...request,task_id:'task17',nonce:'nonce17'};
  const fixture=setup({execute:async()=>new Promise(resolve=>finishes.push(resolve)),drained:()=>{void controller.receive(last);}});
  controller=fixture.controller;
  const jobs=Array.from({length:17},(_,i)=>controller.receive({...request,task_id:`task${i+1}`,nonce:`nonce${i+1}`}));
  while(finishes.length<16)await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(fixture.calls.claim.length,16);finishes[0]({content:'first'});
  while(finishes.length<17)await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(fixture.calls.claim.length,17);
  for(const finish of finishes.slice(1))finish({content:'done'});
  await Promise.all(jobs);
  while(fixture.calls.complete.length<17)await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(fixture.calls.complete.length,17);
});
