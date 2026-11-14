import test from 'node:test';
import assert from 'node:assert/strict';
import {HelperTextRunner,classifyHelperError,helperTextWireReply,parseHelperTextRequest,matchesHelperTextRoute} from '../../apps/desktop/workbench/src/modules/ai/lib/helperText.ts';
const request=(overrides={})=>parseHelperTextRequest({id:'request',owner:'owner',session:'session',route:'route',system:'system',prompt:'Привіт',max_output_tokens:512,timeout_ms:1000,...overrides});
function setup(overrides={}){
 const calls=[],usage=[];
 const dependencies={context:async()=>4096,model:async()=> 'selected-local-model',generate:async options=>{calls.push(options);return {text:'42',finishReason:'stop',usage:{inputTokens:25,outputTokens:2,inputTokenDetails:{cacheReadTokens:4}}};},estimate:value=>String(value).length,usage:value=>usage.push(value),...overrides};
 return {runner:new HelperTextRunner(dependencies),calls,usage};
}
test('strict plaintext contract rejects unknown options, UTF8 overflow and invalid limits',()=>{
 for(const override of [{tools:[]},{prompt:'я'.repeat(24001)},{timeout_ms:120001},{max_output_tokens:0},{max_output_tokens:4097},{temperature:NaN},{json_mode:'true'},{route:'bad/route'}])assert.throws(()=>request(override),/Invalid helper/);
 assert.equal(matchesHelperTextRoute(request(),'owner','session','route'),true);
 for(const identity of [['other','session','route'],['owner','other','route'],['owner','session','old-route']])assert.equal(matchesHelperTextRoute(request(),...identity),false);
});
test('selected provider receives stateless options with no tools/history/retries; usage is canonical',async()=>{
 const {runner,calls,usage}=setup();
 assert.deepEqual(await runner.run(request()),{id:'request',owner:'owner',session:'session',route:'route',text:'42'});
 assert.equal(calls.length,1);assert.equal(calls[0].model,'selected-local-model');
 assert.equal(calls[0].maxRetries,0);assert.equal(calls[0].maxOutputTokens,512);
 assert.ok(!('tools' in calls[0])&&!('messages' in calls[0]));assert.equal(usage[0].inputTokens,25);
});
test('context overflow and provider errors never start fallback or disclose secret errors',async()=>{
 const small=setup({context:async()=>512});assert.match((await small.runner.run(request({prompt:"x".repeat(500)}))).error,/context/);assert.equal(small.calls.length,0);
 let attempts=0;
 const failed=setup({generate:async()=>{attempts++;throw new Error('Bearer PRIVATE_ENDPOINT_SECRET');}});
 const reply=await failed.runner.run(request());assert.equal(attempts,1);assert.ok(!JSON.stringify(reply).includes('SECRET'));assert.equal(reply.text,'');
});
test('one pending request, explicit cancel and timeout settle even noncooperative model promises',async()=>{
 const {runner}=setup({model:()=>new Promise(()=>{})});
 const first=runner.run(request());
 assert.match((await runner.run(request({id:'second'}))).error,/busy/);
 runner.cancel('other');runner.cancel('request');assert.match((await first).error,/cancelled/);
 const timed=setup({context:()=>new Promise(()=>{})});assert.match((await timed.runner.run(request({timeout_ms:5}))).error,/timed out/);
});
test('JSON mode validates returned JSON and empty/UTF8 oversized output fails safely',async()=>{
 for(const text of ['not JSON','', 'я'.repeat(24001), '\u0000'.repeat(12000), '"'.repeat(31000)]){
  const {runner}=setup({generate:async()=>({text,finishReason:'stop',usage:{}})});assert.ok((await runner.run(request({json_mode:true}))).error);
 }
 let options;
 const valid=setup({generate:async value=>{options=value;return {text:'{"answer":42}',finishReason:'stop',usage:{}};}});
 assert.equal((await valid.runner.run(request({json_mode:true}))).text,'{"answer":42}');assert.match(options.system,/valid JSON/);
});

test('truncated, filtered and abnormal completion never returns partial text; real usage remains counted',async()=>{
 for(const finishReason of ['length','content-filter','error','tool-calls','other']){
  const {runner,usage}=setup({generate:async()=>({text:'partial code or text',finishReason,usage:{inputTokens:100,outputTokens:20}})});
  const reply=await runner.run(request());
  assert.equal(reply.text,'');assert.match(reply.error,/did not complete/);
  assert.equal(usage.length,1);assert.equal(usage[0].outputTokens,20);
 }
});
test('escaped text responses are rejected by actual JSON wire size even below raw UTF8 cap',async()=>{
 for(const text of ['\u0000'.repeat(12000), '"'.repeat(31000)]){
  const {runner}=setup({generate:async()=>({text,finishReason:'stop',usage:{}})});
  const reply=await runner.run(request());
  assert.equal(reply.text,'');assert.match(reply.error,/oversized/);
 }
});
test('normal parent completion leaves background helper running; explicit Stop cancels backend and local chat',async()=>{
 const {explicitAgentStop}=await import('../../apps/desktop/workbench/src/modules/ai/lib/explicitAgentStop.ts');
 let complete,entered,localStops=0,backendStops=0;
 const generating=new Promise(resolve=>{entered=resolve;});
 const {runner}=setup({generate:()=>{entered();return new Promise(resolve=>{complete=resolve;});}});
 const running=runner.run(request());
 await generating;
 // Parent returning to ready has no lifecycle callback into this independent helper.
 await Promise.resolve();
 complete({text:'background complete',finishReason:'stop',usage:{}});
 assert.equal((await running).text,'background complete');
 const pending=setup({model:()=>new Promise(()=>{})});
 const cancelled=pending.runner.run(request());
 await explicitAgentStop(()=>{localStops++;},async()=>{backendStops++;pending.runner.cancel();});
 assert.match((await cancelled).error,/cancelled/);
 assert.equal(localStops,1);assert.equal(backendStops,1);
 await explicitAgentStop(()=>{localStops++;throw new Error('local failure');},async()=>{backendStops++;throw new Error('backend failure');});
 assert.equal(localStops,2);assert.equal(backendStops,2);
});
test('stateless 4K helper can use a long requested reply without the chat 15-percent cap',async()=>{
 const {estimateTokens}=await import('../../apps/desktop/workbench/src/modules/ai/lib/compact.ts');
 let sent;
 const input=request({system:'Translate faithfully.',prompt:'Привіт '.repeat(100),max_output_tokens:2048});
 const {runner}=setup({estimate:estimateTokens,generate:async options=>{sent=options;return {text:'Long complete translation. '.repeat(200),finishReason:'stop',usage:{inputTokens:400,outputTokens:1500}};}});
 const reply=await runner.run(input);
 assert.equal(reply.error,undefined);assert.equal(sent.maxOutputTokens,2048);
 assert.equal(sent.prompt,input.prompt);assert.equal(sent.system,input.system);
 assert.ok(estimateTokens(sent.system)+estimateTokens(sent.prompt)+128+sent.maxOutputTokens<=Math.floor(4096*.9));
 const bounded=setup({estimate:estimateTokens});
 await bounded.runner.run(request({max_output_tokens:4096}));
 assert.ok(bounded.calls[0].maxOutputTokens>3000);
 assert.equal(bounded.calls[0].maxOutputTokens,Math.floor(4096*.9)-estimateTokens('system')-estimateTokens('Привіт')-128);
 const oversized=setup({estimate:estimateTokens});
 const failed=await oversized.runner.run(request({prompt:'x'.repeat(12000),max_output_tokens:1024}));
 assert.equal(failed.error_code,'context_limit');assert.equal(oversized.calls.length,0);
});

test('provider diagnostics are finite owned codes and never forward URLs, keys or provider response text',async()=>{
 const cases=[
  ["HTTP 403 https://secret.example SECRET", "api_403"],
  [{statusCode:400,message:'bad request https://secret.example/?key=SECRET'},'api_400'],
  [{statusCode:401,message:'Bearer SECRET'},'api_401'],
  [{statusCode:429,responseBody:'SECRET quota'},'api_429'],
  [{statusCode:503,responseBody:'SECRET unavailable'},'api_5xx'],
  [{cause:{statusCode:400,message:'maximum context length exceeded SECRET'}},'context_limit'],
  [{statusCode:400,responseBody:'native_format JSON parsing failed SECRET'},'native_format'],
  [{name:'TimeoutError',message:'deadline at https://secret.example SECRET'},'timeout'],
 ];
 for(const [error,expected] of cases){
  assert.equal(classifyHelperError(error).code,expected);
  const {runner}=setup({generate:async()=>{throw error;}});
  const reply=await runner.run(request());
  assert.equal(reply.error_code,expected);assert.equal(reply.text,'');
  const wire=helperTextWireReply(reply,text=>'translated '+text);
  assert.ok(!Object.hasOwn(wire,'error_code'));assert.match(wire.error,new RegExp('^\\['+expected+'\\] translated '));
  assert.ok(!JSON.stringify(wire).includes('SECRET'));assert.ok(!JSON.stringify(wire).includes('secret.example'));
 }
});