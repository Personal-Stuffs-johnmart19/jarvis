import assert from 'node:assert/strict';
import test from 'node:test';
import { registerHooks, createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const require=createRequire(new URL('../../apps/desktop/package.json',import.meta.url));
const zustandUrl=pathToFileURL(require.resolve('zustand')).href;
const calls=[];
const native={readSnapshot:async()=>({kind:"text",content:"old",size:3}),createDir:async(...args)=>{calls.push(['mkdir',...args]);},writeFile:async(...args)=>{calls.push(['write',...args]);}};
globalThis.__planWorkspaceNative=native;
const hook=registerHooks({resolve(specifier,context,nextResolve){
  if(specifier==='zustand')return nextResolve(zustandUrl,context);
  if(specifier==='../lib/native'&&context.parentURL?.endsWith('/store/planStore.ts'))return {url:'data:text/javascript,export const native=globalThis.__planWorkspaceNative;',shortCircuit:true};
  if(specifier==='../lib/explicitAgentStop'||specifier==='../../../../../src/displayError'||specifier==='./localization')return nextResolve(specifier+'.ts',context);
  return nextResolve(specifier,context);
}});
const {usePlanStore}=await import('../../apps/desktop/workbench/src/modules/ai/store/planStore.ts');
hook.deregister();
function enqueue(kind,workspace={kind:'wsl',distro:'owned-A'},overrides={}){
  usePlanStore.getState().enqueue({id:kind,kind,path:'/owned/same-path',workspace,originalContent:'old',proposedContent:'new',isNewFile:kind==='create_directory',...overrides});
}
test('deferred proposals apply to their own workspace and distro even after producer env mutation',async()=>{
  usePlanStore.getState().clear();calls.length=0;
  const env={kind:'wsl',distro:'owned-A'};enqueue('write_file',env);env.distro='unapproved-B';enqueue('create_directory',{kind:'local'});
  const results=await usePlanStore.getState().applyAll();
  assert.deepEqual(results,[{id:'write_file',ok:true},{id:'create_directory',ok:true}]);
  assert.deepEqual(calls,[['write','/owned/same-path','new',{kind:'wsl',distro:'owned-A'}],['mkdir','/owned/same-path',{kind:'local'}]]);
});
test('deferred native persistence failure is returned as failure without false completion',async()=>{
  usePlanStore.getState().clear();calls.length=0;enqueue('edit');
  const previous=native.writeFile;native.writeFile=async()=>{throw new Error('owned synthetic persistence failed');};
  try { const result=await usePlanStore.getState().applyAll();assert.equal(result[0].ok,false);assert.deepEqual(result[0].error,{source:'jarvis',message:'Could not apply this change: {0}',values:['Error: owned synthetic persistence failed']});assert.equal(usePlanStore.getState().queue.length,1);assert.deepEqual(usePlanStore.getState().queue[0].failure,result[0].error); }
  finally {native.writeFile=previous;}
});

function reset(){usePlanStore.getState().clear();calls.length=0;native.readSnapshot=async()=>({kind:'text',content:'old',size:3});}
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
test('changed or deleted originals and newly occupied destinations preserve proposals without writes',async()=>{
  for(const [snapshot,isNewFile] of [[{kind:'text',content:'user edit',size:9},false],[{kind:'missing'},false],[{kind:'text',content:'new owner',size:9},true],[{kind:'binary',size:3},false]]){
    reset();enqueue('edit',undefined,{isNewFile});native.readSnapshot=async()=>snapshot;
    const results=await usePlanStore.getState().applyAll();
    assert.equal(results[0].ok,false);assert.equal(calls.length,0);assert.equal(usePlanStore.getState().queue.length,1);
    assert.equal(usePlanStore.getState().queue[0].failure.source,'jarvis');
  }
});
test('retry retains failure until real persistence succeeds and removes only that proposal',async()=>{
  reset();enqueue('edit');native.readSnapshot=async()=>({kind:'text',content:'user edit',size:9});
  await usePlanStore.getState().applyAll();assert.equal(usePlanStore.getState().queue.length,1);
  native.readSnapshot=async()=>({kind:'text',content:'old',size:3});
  assert.deepEqual(await usePlanStore.getState().applyAll(),[{id:'edit',ok:true}]);assert.equal(usePlanStore.getState().queue.length,0);
});
for(const action of ['clear','disable','stop'])test(`${action} during preflight prevents every undispatched write`,async()=>{
  reset();enqueue('edit');enqueue('write_file');const reading=deferred();native.readSnapshot=()=>reading.promise;
  const applying=usePlanStore.getState().applyAll();
  if(action==='stop'){
    const {explicitAgentStop}=await import('../../apps/desktop/workbench/src/modules/ai/lib/explicitAgentStop.ts');
    await explicitAgentStop(()=>{},async()=>{});
  }else usePlanStore.getState()[action]();
  reading.resolve({kind:'text',content:'old',size:3});assert.deepEqual(await applying,[]);assert.equal(calls.length,0);
  assert.equal(usePlanStore.getState().queue.length,action==='stop'?2:0);
});
test('clear during a started write preserves its actual success and prevents the remainder',async()=>{
  reset();enqueue('edit');enqueue('write_file');const writing=deferred(),started=deferred(),oldWrite=native.writeFile;
  native.writeFile=(...args)=>{calls.push(['write',...args]);started.resolve();return writing.promise;};
  try{
    const applying=usePlanStore.getState().applyAll();await started.promise;usePlanStore.getState().clear();
    enqueue('edit',undefined,{proposedContent:'replacement with same ID'});writing.resolve();
    assert.deepEqual(await applying,[{id:'edit',ok:true}]);assert.equal(calls.length,1);
    assert.equal(usePlanStore.getState().queue[0].proposedContent,'replacement with same ID');
  }finally{native.writeFile=oldWrite;}
});
test('enqueue during apply is retained and simultaneous Apply never dispatches twice',async()=>{
  reset();enqueue('edit');const writing=deferred(),started=deferred(),oldWrite=native.writeFile;
  native.writeFile=(...args)=>{calls.push(['write',...args]);started.resolve();return writing.promise;};
  try{
    const applying=usePlanStore.getState().applyAll();await started.promise;enqueue('write_file');
    assert.deepEqual(await usePlanStore.getState().applyAll(),[]);writing.resolve();await applying;
    assert.equal(calls.length,1);assert.deepEqual(usePlanStore.getState().queue.map(q=>q.id),['write_file']);
  }finally{native.writeFile=oldWrite;}
});test('valid owned native failures retain localization provenance and malformed lookalikes are untrusted detail',async()=>{
  const original=native.writeFile;
  try{
    for(const value of [{source:'jarvis',message:'Could not apply this change: {0}',values:['raw detail']},{source:'jarvis',message:'spoofed',values:[{}]}]){
      reset();enqueue('edit');native.writeFile=async()=>{throw value;};
      const [result]=await usePlanStore.getState().applyAll();
      if(value.message==='spoofed')assert.equal(result.error.message,'Could not apply this change: {0}');
      else assert.deepEqual(result.error,value);
      assert.equal(usePlanStore.getState().queue.length,1);
    }
  }finally{native.writeFile=original;}
});
test('Stop after write dispatch reports actual completion and retains the undispatched remainder',async()=>{
  reset();enqueue('edit');enqueue('write_file');const writing=deferred(),started=deferred(),oldWrite=native.writeFile;
  native.writeFile=(...args)=>{calls.push(['write',...args]);started.resolve();return writing.promise;};
  try{
    const applying=usePlanStore.getState().applyAll();await started.promise;
    const {explicitAgentStop}=await import('../../apps/desktop/workbench/src/modules/ai/lib/explicitAgentStop.ts');
    await explicitAgentStop(()=>{},async()=>{});writing.resolve();
    assert.deepEqual(await applying,[{id:'edit',ok:true}]);assert.equal(calls.length,1);
    assert.deepEqual(usePlanStore.getState().queue.map(q=>q.id),['write_file']);
    assert.equal(usePlanStore.getState().applyNotice.message,'Plan application stopped. Changes already being written may still finish.');
  }finally{native.writeFile=oldWrite;}
});
