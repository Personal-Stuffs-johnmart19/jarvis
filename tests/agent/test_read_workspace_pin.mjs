import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, stripTypeScriptTypes } from 'node:module';
import vm from 'node:vm';
import * as context from '../../apps/desktop/workbench/src/modules/ai/tools/context.ts';
import * as security from './security_fixture.mjs';
import * as readers from '../../apps/desktop/workbench/src/modules/ai/tools/queuedReadOnly.ts';
import * as dispatch from '../../apps/desktop/workbench/src/modules/ai/tools/queuedDispatch.ts';
import * as readOnlyRequest from '../../apps/desktop/workbench/src/modules/ai/lib/readOnlyRequest.ts';
import { QueuedUiReadController, queuedWorkspaceFingerprint } from '../../apps/desktop/workbench/src/modules/ai/lib/queuedTaskApprovals.ts';

const require = createRequire(new URL('../../apps/desktop/package.json', import.meta.url));
function load(path, imports) {
  const source = readFileSync(new URL('../../apps/desktop/workbench/src/modules/ai/' + path, import.meta.url), 'utf8');
  let code = stripTypeScriptTypes(source);
  const names=[...code.matchAll(/^export (?:async )?(?:function|const) (\w+)/gm)].map(match=>match[1]);
  code=code.replace(/^import\s*\{([\s\S]*?)\}\s*from\s*["']([^"']+)["'];/gm,(_match,names,id)=>`const {${names.replace(/\bas\b/g,':')}}=require(${JSON.stringify(id)});`)
    .replace(/^export (?=(?:async )?(?:function|const) )/gm,'');
  code+='\nObject.assign(exports,{'+names.join(',')+'});';
  const exports = {};
  vm.runInNewContext(code, { exports, module: { exports }, console, TextEncoder, structuredClone,
    require: name => { if (!(name in imports)) throw Error('Unexpected test import ' + name); return imports[name]; },
  });
  return exports;
}

test('queued controller receives full canonical read content when the parent read cache is primed',async()=>{
  const f=fixture({switchOnCanonicalize:false}),input={path:'/home/owned/fixture.txt'};
  assert.equal((await f.fs.read_file.execute(input)).content,'42');
  assert.equal((await f.fs.read_file.execute(input)).unchanged,true,'ordinary chat still deduplicates cached reads');
  const workspace=queuedWorkspaceFingerprint('wsl:Ubuntu','/home/owned','/home/owned'),completed=[];
  const cacheKey=context.readCacheKey({kind:"wsl",distro:"Ubuntu"},input.path);
  const prior=f.cache.get(cacheKey);let cacheReads=0,cacheWrites=0;
  const get=f.cache.get.bind(f.cache),set=f.cache.set.bind(f.cache);
  f.cache.get=key=>{cacheReads++;return get(key);};
  f.cache.set=(key,value)=>{cacheWrites++;return set(key,value);};
  const before=f.calls.filter(call=>call.command==='ai_read_file').length;
  const action=canonicalAction(f);
  const controller=new QueuedUiReadController('owner','session','route',{
    current:()=>workspace,readable:()=>readers.isQueuedReadOnly(f.fs.read_file),
    claim:async()=>({token:'one-use'}),prepare:(value,id)=>action.needsApproval(value,{toolCallId:id,messages:[],context:{}}),revoke:()=>{},
    execute:(value,id,signal)=>action.execute(value,{toolCallId:id,messages:[],abortSignal:signal,context:{[readers.QUEUED_READ_CONTEXT]:true}}),
    complete:async value=>{completed.push(value);return {ok:true};},
  });
  await controller.receive({task_id:'task',nonce:'nonce',owner:'owner',session:'session',route:'route',workspace,name:'read_file',args:input});
  assert.equal(completed.length,1);assert.equal(completed[0].result.content,'42');
  assert.equal(completed[0].result.unchanged,undefined);
  assert.equal(f.calls.filter(call=>call.command==='ai_read_file').length-before,1,'one physical queued read, no replay');
  assert.equal(cacheReads,0);assert.equal(cacheWrites,0);assert.equal(get(cacheKey),prior);
  assert.equal((await f.fs.read_file.execute(input)).unchanged,true);
});

function canonicalAction(fixture) {
  return load('tools/jarvis.ts',{
    ai:{tool:value=>value,asSchema:require('ai').asSchema},zod:require('zod'),'./queuedReadOnly':readers,'./queuedDispatch':dispatch,
    '../../mcp/client':{revokeMcpReceipts:()=>{}},'./catalogue':{cataloguePage:()=>{}},
    '../lib/browserObservation':{browserObservation:value=>value},
    '../lib/readOnlyRequest':readOnlyRequest,
    '@/host/core':{invoke:()=>{throw Error('Read receipt must use its local canonical handler');}},
  }).buildJarvisTools({read_file:fixture.fs.read_file},false).jarvis_action;
}

function fixture({switchOnCanonicalize=true}={}) {
  let environment = {kind:'wsl', distro:'Ubuntu'};
  const calls = [];
  const workspace = {currentWorkspaceEnv:()=>environment};
  const native = load('lib/native.ts', {
    '@/modules/workspace':workspace,
    '@/host/core':{invoke:async(command,args)=>{
      calls.push({command,workspace:JSON.parse(JSON.stringify(args.workspace))});
      if(command==='fs_canonicalize'){
        if(switchOnCanonicalize)environment={kind:'wsl',distro:'Debian'};
        await Promise.resolve();
        return args.path;
      }
      if(command==='ai_read_file')return {kind:'text',content:'42',size:2};
      if(command==='ai_read_dir')return [];
      if(command==='ai_grep'||command==='ai_glob')return {hits:[],truncated:false,files_scanned:0};
      throw Error('Unexpected native operation '+command);
    }},
  }).native;
  const imports={ai:{tool:value=>value,asSchema:require('ai').asSchema},zod:require('zod'),'@/modules/workspace':workspace,
    '../lib/native':{native},'../lib/security':security,'./queuedReadOnly':readers,'./context':context,
    '../store/planStore':{newQueuedEditId:()=>'',usePlanStore:{getState:()=>({active:false})}}};
  const ctx={getCwd:()=>'/home/owned',getWorkspaceRoot:()=>'/home/owned',readCache:new Map()};
  return {calls,native,cache:ctx.readCache,fs:load('tools/fs.ts',imports).buildFsTools(ctx),search:load('tools/search.ts',imports).buildSearchTools(ctx)};
}

test('discarded queued read cannot prime the parent cache or suppress its next normal result',async()=>{
  const f=fixture(),input={path:'/home/owned/fixture.txt'},completed=[];
  let workspace='original';
  const action=canonicalAction(f);
  const controller=new QueuedUiReadController('owner','session','route',{
    current:()=>workspace,readable:()=>true,claim:async()=>({token:'one-use'}),prepare:(value,id)=>action.needsApproval(value,{toolCallId:id,messages:[],context:{}}),revoke:()=>{},
    execute:async(value,id,signal)=>{
      const result=await action.execute(value,{toolCallId:id,messages:[],abortSignal:signal,context:{[readers.QUEUED_READ_CONTEXT]:true}});
      workspace='changed';return result;
    },
    complete:async value=>{completed.push(value);return {ok:true};},
  });
  await controller.receive({task_id:'task',nonce:'nonce',owner:'owner',session:'session',route:'route',workspace,name:'read_file',args:input});
  assert.equal(completed[0].error,true);assert.equal(f.cache.size,0);
  const normal=await f.fs.read_file.execute(input);
  assert.equal(normal.content,'42');assert.equal(normal.unchanged,undefined);
});

for(const [name,input,command] of [
  ['read_file',{path:'/home/owned/fixture.txt'},'ai_read_file'],
  ['list_directory',{path:'/home/owned'},'ai_read_dir'],
  ['grep',{root:'/home/owned',pattern:'42'},'ai_grep'],
  ['glob',{root:'/home/owned',pattern:'*.txt'},'ai_glob'],
]) {
  test(`${name} keeps the original WSL distribution across awaited canonicalize and read`,async()=>{
    const f=fixture();
    await (f.fs[name]??f.search[name]).execute(input);
    assert.deepEqual(f.calls.map(call=>call.command),['fs_canonicalize',command]);
    assert.deepEqual(f.calls.map(call=>call.workspace),[{kind:'wsl',distro:'Ubuntu'},{kind:'wsl',distro:'Ubuntu'}]);
    await f.native.readFile('/home/owned/fixture.txt');
    assert.equal(f.calls.at(-1).workspace.distro,'Debian','existing callers still default to the current environment');
  });
}
