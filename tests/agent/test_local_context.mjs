import {test} from 'node:test';
import assert from 'node:assert/strict';
import {lmstudioModelMetadata,lmstudioContextLimit,localContextLimit,ollamaContextLimit,localMetadata} from '../../apps/desktop/workbench/src/modules/ai/lib/localContext.ts';
test('loaded context takes precedence over model maximum; unloaded values are conservative',()=>{
 assert.equal(localContextLimit({max_context_length:131072,loaded_instances:[{config:{context_length:8192}}]}),8192);
 assert.equal(localContextLimit({max_context_length:131072,loaded_instances:[]}),4096);
 assert.equal(localContextLimit({max_context_length:2048,loaded_instances:[]}),2048);
 assert.equal(localContextLimit(),4096);
});

import {addSessionUsage,sessionUsage} from '../../apps/desktop/workbench/src/modules/ai/lib/sessionUsage.ts';
test('session counters accumulate independently and old conversations start at zero',()=>{
 const first=addSessionUsage(undefined,{inputTokens:123,outputTokens:17,cachedInputTokens:11,lastInputTokens:123,lastCachedTokens:11});
 assert.equal(first.tokens.inputTokens,123);
 assert.deepEqual(sessionUsage(JSON.parse(JSON.stringify(first))),first);
 assert.equal(addSessionUsage(first,{inputTokens:9,outputTokens:2,cachedInputTokens:0,lastInputTokens:9,lastCachedTokens:0}).tokens.inputTokens,132);
 assert.equal(first.tokens.inputTokens,123);
 assert.equal(sessionUsage().tokens.inputTokens,0);
 assert.equal(sessionUsage({tokens:{inputTokens:-1,outputTokens:NaN,cachedInputTokens:Infinity}}).tokens.inputTokens,0);
});

test('Ollama uses reported running context and stays conservative for unloaded or unknown state',()=>{
 assert.equal(ollamaContextLimit({loaded:true,context_length:2048}),2048);
 assert.equal(ollamaContextLimit({loaded:true,context_length:8192}),8192);
 assert.equal(ollamaContextLimit({loaded:false,context_length:131072}),4096);
 assert.equal(ollamaContextLimit({loaded:null,context_length:131072}),4096);
 assert.equal(ollamaContextLimit(),4096);
});

import {readModelToolResult} from '../../apps/desktop/workbench/src/modules/ai/lib/toolHistory.ts';
import {compactModelMessagesDetailed} from '../../apps/desktop/workbench/src/modules/ai/lib/compact.ts';
test('child compaction keeps full results recoverable without exposing parent messages',()=>{
 const original=[{role:'user',content:'inspect'},{role:'assistant',content:[{type:'tool-call',toolCallId:'child-read',toolName:'read_file',input:{path:'a'}}]},{role:'tool',content:[{type:'tool-result',toolCallId:'child-read',toolName:'read_file',output:{type:'json',value:{text:'x'.repeat(20000)+'CHILD_TAIL'}}}]},{role:'assistant',content:[{type:'tool-call',toolCallId:'latest',toolName:'glob',input:{}}]},{role:'tool',content:[{type:'tool-result',toolCallId:'latest',toolName:'glob',output:{type:'json',value:[]}}]}];
 const compact=compactModelMessagesDetailed(original,4096,{recoveryTool:'direct'});
 assert.equal(compact.compacted,true);
 assert.ok(!JSON.stringify(compact.messages).includes('via jarvis_action'));
 assert.ok(readModelToolResult(original,'child-read',19990,100).text.includes('CHILD_TAIL'));
 assert.ok(readModelToolResult(original,'parent-only',0,100).error);
 assert.equal(original[2].content[0].output.value.text.length,20010);
});

test('context metadata propagates cancellation and bounds the response before parsing',async()=>{
 const controller=new AbortController();
 let closed=false;
 const pending=localMetadata('http://localhost/api/ps',async(_url,{signal})=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>{closed=true;reject(new DOMException('Stopped','AbortError'));},{once:true})),controller.signal);
 controller.abort();
 await assert.rejects(pending,{name:'AbortError'});
 assert.equal(closed,true);
 await assert.rejects(localMetadata('http://localhost/api/ps',async()=>new Response('x'.repeat(1024*1024+1))),/1 MiB/);
 assert.deepEqual(await localMetadata('http://localhost/api/ps',async()=>new Response('{"models":[]}')),{models:[]});
});


test('LM Studio resolves inference instance IDs independently of canonical catalogue keys',()=>{
 const models=[{key:'openai/gpt-oss-20b',max_context_length:131072,loaded_instances:[{id:'gpt-oss-20b',config:{context_length:8192}},{id:'localmodelgptoss20b',config:{context_length:32768}}]}];
 assert.equal(lmstudioContextLimit(models,'gpt-oss-20b'),8192);
 assert.equal(lmstudioContextLimit(models,'localmodelgptoss20b'),32768);
 assert.equal(lmstudioContextLimit(models,'openai/gpt-oss-20b'),8192);
 assert.equal(lmstudioContextLimit(models,' gpt-oss-20b '),8192);
 assert.equal(lmstudioContextLimit(models,''),4096);
 assert.equal(lmstudioContextLimit(models,'unknown'),4096);
});

test('LM Studio keeps unloaded and ambiguous model identities conservative',()=>{
 const entry=(key,id,context)=>({key,max_context_length:131072,loaded_instances:id?[{id,config:{context_length:context}}]:[]});
 assert.equal(lmstudioContextLimit([entry('openai/gpt-oss-20b','custom',32768)],'gpt-oss-20b'),4096);
 assert.equal(lmstudioContextLimit([entry('openai/gpt-oss-20b','gpt-oss-20b',32768),entry('other/gpt-oss-20b','gpt-oss-20b',8192)],'gpt-oss-20b'),4096);
 assert.equal(lmstudioContextLimit([entry('gpt-oss-20b','custom',32768),entry('other/model','gpt-oss-20b',8192)],'gpt-oss-20b'),4096);
 assert.equal(lmstudioContextLimit([entry('gpt-oss-20b',null)],'gpt-oss-20b'),4096);
 assert.equal(lmstudioContextLimit([entry('same','a',32768),entry('same','b',8192)],'same'),4096);
 assert.equal(localContextLimit({loaded_instances:[{config:{context_length:32768}},{config:{context_length:8192}}]}),8192);
});
test('vision eligibility requires explicit capability on the exact selected LM Studio identity',()=>{
 const entry=(key,id,vision,type='llm')=>({key,type,capabilities:{vision},max_context_length:131072,loaded_instances:id?[{id,config:{context_length:8192}}]:[]});
 const vision=entry('publisher/vision','custom-vision',true);
 const text=entry('openai/gpt-oss-20b','gpt-oss-20b',false);
 assert.deepEqual(lmstudioModelMetadata([vision,text],'custom-vision'),{contextLimit:8192,supportsVision:true});
 assert.equal(lmstudioModelMetadata([vision,text],'publisher/vision').supportsVision,true);
 assert.equal(lmstudioModelMetadata([vision,text],'gpt-oss-20b').supportsVision,false);
 assert.equal(lmstudioModelMetadata([vision,text],'vision').supportsVision,false);
 assert.equal(lmstudioModelMetadata([vision,text],'unknown').supportsVision,false);
 assert.equal(lmstudioModelMetadata([entry('missing','missing',undefined)],'missing').supportsVision,false);
 assert.equal(lmstudioModelMetadata([entry('embed','embed',true,'embedding')],'embed').supportsVision,false);
 assert.equal(lmstudioModelMetadata([vision,entry('other','custom-vision',true)],'custom-vision').supportsVision,false);
 assert.equal(lmstudioModelMetadata([vision,entry('custom-vision','other',true)],'custom-vision').supportsVision,false);
 assert.equal(lmstudioModelMetadata([vision,entry('publisher/vision','other',true)],'publisher/vision').supportsVision,false);
 // A known unloaded vision model remains eligible for explicit JIT use; its context stays conservative.
 assert.deepEqual(lmstudioModelMetadata([entry('known-vision',null,true)],'known-vision'),{contextLimit:4096,supportsVision:true});
});