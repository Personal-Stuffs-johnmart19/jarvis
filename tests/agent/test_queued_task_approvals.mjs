import test from "node:test";
import assert from "node:assert/strict";
import { QueuedTaskApprovalController } from "../../apps/desktop/workbench/src/modules/ai/lib/queuedTaskApprovals.ts";

const event = (approval_id="pending") => ({owner:"owner",session:"chat",goal:"fixture",
  request:{name:"tasks.resume",args:{task_id:"task",approval_id,name:"write_file",args:{path:"D:/QA/a.txt",content:"exact"}}}});
function fixture(prepareOverride, executeOverride) {
  const prepared=[], executed=[], revoked=[], states=[];
  const receipts=new Set();
  const controller=new QueuedTaskApprovalController("owner","chat",{
    prepare:async(input,id)=>{prepared.push(input);receipts.add(id);return prepareOverride ? await prepareOverride(input,id) : input.name==="tasks.resume";},
    execute:async(input,id)=>{assert.ok(receipts.delete(id),"Missing single-use receipt");executed.push(input);return executeOverride ? await executeOverride(input,id) : {ok:true};},
    revoke:id=>{revoked.push(id);receipts.delete(id);},
  }, rows=>states.push(rows.map(row=>({...row}))));
  return {controller,prepared,executed,revoked,states,receipts};
}

test("queue card double-click consumes one receipt and never creates a model/chat continuation",async()=>{
  const f=fixture();
  await f.controller.receive(event());
  await f.controller.receive(event());
  assert.equal(f.prepared.length,1);
  assert.equal(f.executed.length,0);
  const key=f.states.at(-1)[0].key;
  await Promise.all([f.controller.respond(key,true),f.controller.respond(key,true)]);
  assert.deepEqual(f.executed,[{name:"tasks.resume",args:event().request.args}]);
  assert.equal(f.receipts.size,0);
  assert.equal(f.states.at(-1).length,0);
});

test("denial revokes the resume receipt and invokes only exact canonical tasks.deny",async()=>{
  const f=fixture();
  await f.controller.receive(event());
  await f.controller.respond(f.states.at(-1)[0].key,false);
  assert.deepEqual(f.executed,[{name:"tasks.deny",args:{task_id:"task",approval_id:"pending"}}]);
  assert.equal(f.receipts.size,0);
  assert.equal(f.states.at(-1).length,0);
});

test("wrong origins and a route disposed during preparation cannot execute stale actions",async()=>{
  let finish;
  const f=fixture(()=>new Promise(resolve=>{finish=resolve;}));
  await f.controller.receive({...event(),session:"other"});
  assert.equal(f.prepared.length,0);
  const pending=f.controller.receive(event());
  const key=f.states.at(-1)[0].key;
  f.controller.dispose();
  finish(true); await pending;
  await f.controller.respond(key,true);
  assert.equal(f.executed.length,0);
  assert.equal(f.receipts.size,0);
});

test("history reset revokes in-flight receipts while allowing later queue events",async()=>{
  let finish;
  const f=fixture(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=f.controller.receive(event());
  f.controller.clear();
  finish(true); await pending;
  assert.equal(f.states.at(-1).length,0);
  assert.equal(f.receipts.size,0);
  const next=f.controller.receive(event("next"));
  finish(true); await next;
  assert.equal(f.states.at(-1)[0].request.approval_id,"next");
});

test("policy-approved safe host steps continue once without a fabricated user approval",async()=>{
  const f=fixture(async()=>false);
  await f.controller.receive(event());
  assert.equal(f.executed.length,1);
  assert.equal(f.states.at(-1).length,0);
});

test("native owned failures retain provenance and local queue failures use the same message transport",async()=>{
  const native={source:"jarvis",message:"Expired or changed queued task approval",values:[]};
  const f=fixture(undefined,()=>({error:native}));
  await f.controller.receive(event());
  await f.controller.respond(f.states.at(-1)[0].key,true);
  assert.equal(f.states.at(-1)[0].error,native);
  const local=fixture(undefined,()=>({ok:false}));
  await local.controller.receive(event());
  await local.controller.respond(local.states.at(-1)[0].key,true);
  const error=local.states.at(-1)[0].error;
  assert.equal(error.source,"jarvis");
  assert.equal(error.message,"Queued task action failed");
  assert.deepEqual(error.values,[]);
});
