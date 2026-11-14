import assert from "node:assert/strict";
import test from "node:test";
import { registerHooks, createRequire } from "node:module";
import { pathToFileURL } from "node:url";
const root = new URL("../../apps/desktop/", import.meta.url);
const require = createRequire(new URL("package.json", root));
const zodUrl = pathToFileURL(require.resolve("zod")).href;
const securityUrl = new URL("./security_fixture.mjs", import.meta.url).href;
const data = source => `data:text/javascript,${encodeURIComponent(source)}`;
const fixture = globalThis.__fileMutationFixture = {};
const hook = registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier === "../lib/security") return { url: securityUrl, shortCircuit:true };
  const mocks = {
    ai: "export const tool=x=>x;",
    "@/modules/workspace": "export const currentWorkspaceEnv=()=>globalThis.__fileMutationFixture.workspace;",
    "../lib/native": "export const native=globalThis.__fileMutationFixture.native;",
    "../store/planStore": "export const usePlanStore={getState:()=>globalThis.__fileMutationFixture.plan}; export const newQueuedEditId=()=> 'owned-edit';",
    "@/host/core": "export const invoke=(...args)=>globalThis.__fileMutationFixture.invoke(...args);",
  };
  if (specifier in mocks) return { url: data(mocks[specifier]), shortCircuit:true };
  if (specifier === "zod") return nextResolve(zodUrl, context);
  if (specifier.startsWith(".") && context.parentURL?.includes("/modules/ai/") && !/\.[a-z]+$/.test(specifier))
    return nextResolve(specifier+".ts", context);
  return nextResolve(specifier, context);
} });
fixture.native = {};
const { buildFsTools } = await import(new URL("workbench/src/modules/ai/tools/fs.ts", root));
const { buildEditTools } = await import(new URL("workbench/src/modules/ai/tools/edit.ts", root));
const { buildUtilityTools } = await import(new URL("workbench/src/modules/ai/tools/utilities.ts", root));
hook.deregister();
const {readCacheKey}=await import("../../apps/desktop/workbench/src/modules/ai/tools/context.ts");
const scopedKey=()=>readCacheKey({kind:"wsl",distro:"owned-A"},"/owned/project/file.txt");
function setup(active=false) {
  const calls=[], queued=[];
  fixture.workspace={kind:"wsl",distro:"owned-A"};
  fixture.plan={active,enqueue:edit=>queued.push(edit)};
  const record=(name,args)=>{calls.push([name,...args]);};
  fixture.native.canonicalize=async (...args)=>{
    record("canonicalize",args);
    fixture.workspace={kind:"local"};
    await Promise.resolve(); return args[0];
  };
  fixture.native.readFile=async(...args)=>{record("read",args);return {kind:"text",content:"old",size:3};};
  fixture.native.readSnapshot=fixture.native.readFile;
  fixture.native.writeFile=async(...args)=>{record("write",args);};
  fixture.native.createDir=async(...args)=>{record("mkdir",args);};
  fixture.native.readDir=async(...args)=>{record("list",args);return [];};
  fixture.invoke=async(command,args)=>{record(command,[args]);return {entries:[]};};
  const ctx={getCwd:()=>"/owned/project",getWorkspaceRoot:()=>"/owned/project",readCache:new Map()};
  return {calls,queued,ctx,fs:buildFsTools(ctx),edit:buildEditTools(ctx),utilities:buildUtilityTools(ctx)};
}
function assertPinned(calls) {
  for(const [name,...args] of calls) {
    const env=name.startsWith("archive_")?args[0].workspace:name==="write"?args[2]:args[1];
    assert.deepEqual(env,{kind:"wsl",distro:"owned-A"},name);
  }
}
for (const kind of ["write_file","create_directory","edit","multi_edit"]) {
  for(const active of [false,true]) test(`${kind}: ${active?"Plan review stays deferred":"persistence reports completion"} and pins workspace across await`,async()=>{
    const {calls,queued,ctx,fs,edit}=setup(active);
    ctx.readCache.set(scopedKey(),{size:3,hash:0});
    const args={path:"file.txt",content:"é😀",old_string:"old",new_string:"é😀",edits:[{old_string:"old",new_string:"é😀"}]};
    const result=await (fs[kind]??edit[kind]).execute(args,{});
    assert.equal(result.ok,true);assert.equal(result.completed,!active);
    assert.equal(result.status,active?"pending_review":kind==="create_directory"?"created":"written");
    assertPinned(calls);
    if(active) {
      assert.equal(result.queued_for_plan_review,true);assert.equal(queued.length,1);assert.deepEqual(queued[0].workspace,{kind:"wsl",distro:"owned-A"});
      assert.equal(calls.some(([name])=>name==="write"||name==="mkdir"),false);
      assert.equal("bytesWritten" in result,false);
      if(kind!=="create_directory")assert.equal(result.proposedBytes,6);
    } else if(kind!=="create_directory") {
      assert.equal(result.bytesWritten,6);assert.equal(ctx.readCache.get(scopedKey()).size,6);
    }
  });
}
for (const kind of ["archive_list","archive_create","archive_extract"]) test(`${kind} uses one workspace for every path check and native operation`,async()=>{
  const {calls,utilities}=setup();
  const result=await utilities[kind].execute({path:"input.zip",sources:["a.txt","b.txt"],destination:"output"},{});
  assert.equal("error" in result,false);assertPinned(calls);
});
for(const kind of ["read_file","list_directory"])test(`${kind} retains the workspace selected before canonicalization`,async()=>{
  const {calls,fs}=setup();await fs[kind].execute({path:"file.txt"},{});assertPinned(calls);
});
test("catalogue gateway lookup uses actual registered handler without model schemas",async()=>{
  const {registerQueuedGateway,queuedGatewayAction,queuedUtility,isQueuedUtility}=await import("../../apps/desktop/workbench/src/modules/ai/tools/queuedDispatch.ts");
  const handler=queuedUtility({needsApproval:true}), local={utility_run:handler}, gateway={inputSchema:{name:"slim"}};
  assert.equal(registerQueuedGateway(gateway,local),gateway);
  assert.equal(queuedGatewayAction(gateway,"utility_run"),handler);
  assert.equal(isQueuedUtility(queuedGatewayAction(gateway,"utility_run")),true);
  local.utility_run={};assert.equal(queuedGatewayAction(gateway,"utility_run"),handler);
  assert.equal(queuedGatewayAction({},"utility_run"),undefined);
  assert.equal(queuedGatewayAction(gateway,"toString"),undefined);
  assert.deepEqual(Object.keys(gateway),["inputSchema"]);
});
for(const kind of ['write_file','create_directory','edit','multi_edit'])test(`${kind}: rejected native persistence never reports written or completed`,async()=>{
  const {ctx,fs,edit}=setup();ctx.readCache.set(scopedKey(),{size:3,hash:0});
  fixture.native.writeFile=fixture.native.createDir=async()=>{throw new Error('owned persistence rejected');};
  const result=await (fs[kind]??edit[kind]).execute({path:'file.txt',content:'new',old_string:'old',new_string:'new',edits:[{old_string:'old',new_string:'new'}]},{});
  assert.match(result.error,/owned persistence rejected/);assert.equal('completed' in result,false);assert.equal('bytesWritten' in result,false);
  assert.deepEqual(ctx.readCache.get(scopedKey()),{size:3,hash:0});
});

test('archive destinations use approved cwd even if source canonicalization switches live cwd',async()=>{
  for(const kind of ['archive_create','archive_extract']){
    const {calls,utilities,ctx}=setup();let directory='/owned/project';ctx.getCwd=()=>directory;
    const canonicalize=fixture.native.canonicalize;
    fixture.native.canonicalize=async(...args)=>{directory='/different/project';return canonicalize(...args);};
    await utilities[kind].execute({path:'input.zip',sources:['a.txt'],destination:'output'},{});
    const invocation=calls.find(([name])=>name===kind);
    assert.equal(invocation[1].destination,'/owned/project/output');assertPinned(calls);
  }
});
test('Plan write preparation requires full typed original and never treats an unknown read error as absence',async()=>{
  for(const snapshot of [{kind:'binary',size:3},{kind:'toolarge',size:30,limit:10},new Error('owned permission failure')]){
    const {fs,queued}=setup(true);fixture.native.readSnapshot=async()=>{if(snapshot instanceof Error)throw snapshot;return snapshot;};
    const result=await fs.write_file.execute({path:'file.txt',content:'replacement'},{});
    assert.equal(typeof result.error,'string');assert.equal(queued.length,0);
  }
  const {fs,queued}=setup(true);fixture.native.readSnapshot=async()=>({kind:'missing'});
  const result=await fs.write_file.execute({path:'file.txt',content:'new'},{});
  assert.equal(result.status,'pending_review');assert.equal(queued[0].isNewFile,true);
});
for(const kind of ['edit','multi_edit'])test(`${kind} cannot reuse same POSIX path proof from another WSL distro or a legacy path key`,async()=>{
  const {ctx,fs,edit,calls}=setup();fixture.native.canonicalize=async path=>path;
  const path='/owned/project/file.txt';
  await fs.read_file.execute({path},{});
  ctx.readCache.set(path,{size:3,hash:0});
  fixture.workspace={kind:'wsl',distro:'owned-B'};
  const args={path,old_string:'old',new_string:'new',edits:[{old_string:'old',new_string:'new'}]};
  const blocked=await edit[kind].execute(args,{});
  assert.match(blocked.error,/must call read_file/);assert.equal(calls.some(([name])=>name==='write'),false);
  fixture.workspace={kind:'wsl',distro:'owned-A'};
  const accepted=await edit[kind].execute(args,{});
  assert.equal(accepted.status,'written');assert.equal(calls.filter(([name])=>name==='write').length,1);
  assert.equal(ctx.readCache.has(readCacheKey({kind:'wsl',distro:'owned-B'},path)),false);
});
test('ordinary read dedupe stays in one immutable workspace and misses the same path in another distro/local scope',async()=>{
  const {ctx,fs}=setup();fixture.native.canonicalize=async path=>path;
  const path='/owned/project/file.txt';
  assert.equal((await fs.read_file.execute({path},{})).content,'old');
  assert.equal((await fs.read_file.execute({path},{})).unchanged,true);
  for(const workspace of [{kind:'wsl',distro:'owned-B'},{kind:'local'}]){
    fixture.workspace=workspace;
    assert.equal((await fs.read_file.execute({path},{})).content,'old');
    assert.equal((await fs.read_file.execute({path},{})).unchanged,true);
  }
  assert.equal(ctx.readCache.size,3);
});
test('a path-only legacy cache entry cannot suppress a new scoped normal read',async()=>{
  const {ctx,fs}=setup();fixture.native.canonicalize=async path=>path;
  const path='/owned/project/file.txt';ctx.readCache.set(path,{size:3,hash:193501540});
  assert.equal((await fs.read_file.execute({path},{})).content,'old');
  assert.equal(ctx.readCache.has(scopedKey()),true);
});
