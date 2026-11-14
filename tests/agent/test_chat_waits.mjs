import test from "node:test";
import assert from "node:assert/strict";
import { readOnlyRequest } from "../../apps/desktop/workbench/src/modules/ai/lib/readOnlyRequest.ts";
import { modelFetch } from "../../apps/desktop/workbench/src/modules/ai/lib/modelFetch.ts";

test("a stuck metadata read releases the chat when its deadline signal aborts",async()=>{
 const controller=new AbortController();let calls=0;
 const pending=readOnlyRequest(()=>{calls++;return new Promise(()=>{});},controller.signal);
 const reason=new Error("lookup deadline");controller.abort(reason);
 await assert.rejects(pending,error=>error===reason);assert.equal(calls,1);
});
test("cancelled discovery does not start a new backend read",async()=>{
 const controller=new AbortController();controller.abort();let calls=0;
 await assert.rejects(readOnlyRequest(async()=>{calls++;},controller.signal));assert.equal(calls,0);
});
test("successful read retains its exact result and does not replay",async()=>{
 const output={items:[{name:"weather_report"}]};let calls=0;
 assert.equal(await readOnlyRequest(async()=>{calls++;return output;},new AbortController().signal),output);
 assert.equal(calls,1);
});
test("model header wait times out even when no stream has opened",async()=>{
 let calls=0;
 const fetcher=async(_request,init)=>{calls++;return new Promise((_,reject)=>init.signal.addEventListener("abort",()=>reject(new DOMException("Aborted","AbortError")),{once:true}));};
 await assert.rejects(modelFetch(fetcher,5)("https://example.invalid"),error=>error.name==="TimeoutError");assert.equal(calls,1);
});
test("header deadline stops after headers; later user cancellation still propagates",async()=>{
 let signal;const controller=new AbortController();
 const response=await modelFetch(async(_request,init)=>{signal=init.signal;return new Response("actual response");},5)("https://example.invalid",{signal:controller.signal});
 await new Promise(resolve=>setTimeout(resolve,15));assert.equal(signal.aborted,false);assert.equal(await response.text(),"actual response");
 controller.abort();assert.equal(signal.aborted,true);
});
