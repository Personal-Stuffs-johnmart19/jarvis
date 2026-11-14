import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire,registerHooks} from 'node:module';
import {existsSync} from 'node:fs';
import {HelperTextRunner,parseHelperTextRequest,helperTextWireReply} from '../../apps/desktop/workbench/src/modules/ai/lib/helperText.ts';
import {generateHelperText} from '../../apps/desktop/workbench/src/modules/ai/lib/helperTextGeneration.ts';
import {estimateTokens} from '../../apps/desktop/workbench/src/modules/ai/lib/compact.ts';
const require=createRequire(new URL('../../apps/desktop/package.json',import.meta.url));
registerHooks({resolve(specifier,context,next){
 if(specifier==='@/host/core')return {url:'data:text/javascript,export const invoke=()=>{throw Error("No native calls in JSON wire test")};',shortCircuit:true};
 if(specifier.startsWith('.')&&context.parentURL?.endsWith('.ts')){
  const url=new URL(specifier+'.ts',context.parentURL);
  if(existsSync(url))return {url:url.href,shortCircuit:true};
 }
 return next(specifier,context);
}});
const {normalizeLmstudioRequest}=await import('../../apps/desktop/workbench/src/modules/ai/lib/lmstudio.ts');
const {createOpenAICompatible}=require('@ai-sdk/openai-compatible');
const {createGoogle}=require('@ai-sdk/google');

function setup(provider,replyText){
 const calls=[],usage=[];
 const fetch=async(_url,options)=>{
  const normalized=provider==='local'?normalizeLmstudioRequest(options.body,'gpt-oss-20b','default'):options.body;
  calls.push(JSON.parse(normalized));
  const response=provider==='local'
   ? {id:'qa',object:'chat.completion',created:1,model:'gpt-oss-20b',choices:[{index:0,message:{role:'assistant',content:replyText},finish_reason:'stop'}],usage:{prompt_tokens:17,completion_tokens:9,total_tokens:26}}
   : {candidates:[{content:{role:'model',parts:[{text:replyText}]},finishReason:'STOP'}],usageMetadata:{promptTokenCount:17,candidatesTokenCount:9,totalTokenCount:26}};
  return new Response(JSON.stringify(response),{status:200,headers:{'Content-Type':'application/json'}});
 };
 const model=provider==='local'
  ? createOpenAICompatible({name:'lmstudio',baseURL:'http://qa.invalid/v1',fetch})('gpt-oss-20b')
  : createGoogle({apiKey:'QA_NOT_A_REAL_KEY',fetch})('gemini-3.5-flash-lite');
 const runner=new HelperTextRunner({context:async()=>4096,model:async()=>model,generate:generateHelperText,estimate:estimateTokens,usage:value=>usage.push(value)});
 const request=json=>parseHelperTextRequest({id:'request',owner:'owner',session:'session',route:'route',system:'Return the requested data.',prompt:'Return JSON data.',max_output_tokens:768,timeout_ms:1000,json_mode:json});
 return {runner,calls,usage,request};
}

for(const provider of ['local','google']){
 test(`actual SDK ${provider} JSON mode sends structured wire and accepts every JSON value kind`,async()=>{
  for(const value of [{answer:42},[1,true,null],42,'literal',null]){
   const text=JSON.stringify(value),{runner,calls,usage,request}=setup(provider,text);
   const result=await runner.run(request(true));
   assert.equal(result.error,undefined);assert.equal(result.text,text);assert.deepEqual(JSON.parse(result.text),value);
   assert.equal(calls.length,1);assert.equal(usage.length,1);assert.equal(usage[0].inputTokens,17);
   const wire=calls[0];assert.ok(!wire.tools?.length);assert.equal(wire.stream,undefined);assert.equal(wire.toolConfig,undefined);
   if(provider==='local'){
    assert.equal(wire.response_format.type,'json_schema');
    assert.equal(wire.response_format.json_schema.strict,true);
    assert.deepEqual(wire.response_format.json_schema.schema.anyOf.map(item=>item.type),['object','array','string','number','boolean','null']);
    assert.equal(wire.messages.length,2);assert.deepEqual(wire.messages.map(message=>message.role),['system','user']);
   }else{
    assert.equal(wire.generationConfig.responseMimeType,'application/json');
    assert.equal(wire.contents.length,1);assert.equal(wire.contents[0].role,'user');assert.ok(wire.systemInstruction);
   }
  }
 });
 test(`actual SDK ${provider} text mode stays unstructured and has no tool/history payload`,async()=>{
  const {runner,calls,request}=setup(provider,'Plain response');
  assert.equal((await runner.run(request(false))).text,'Plain response');
  assert.equal(calls.length,1);assert.equal(calls[0].response_format,undefined);assert.equal(calls[0].generationConfig?.responseMimeType,undefined);
  assert.ok(!calls[0].tools?.length);assert.equal(calls[0].toolConfig,undefined);
 });
 test(`actual SDK ${provider} JSON parse failure rejects partial output and preserves billable usage`,async()=>{
  const {runner,calls,usage,request}=setup(provider,'not JSON SECRET_PROVIDER_CONTENT');
  const result=await runner.run(request(true));
  assert.equal(calls.length,1);assert.equal(result.text,'');assert.equal(result.error_code,'invalid_json');
  assert.equal(usage.length,1);assert.equal(usage[0].inputTokens,17);assert.equal(usage[0].outputTokens,9);
  assert.ok(!JSON.stringify(helperTextWireReply(result,text=>text)).includes('SECRET_PROVIDER_CONTENT'));
 });
}

test('canonical LM Studio normalization preserves plain requests and explicit schemas and existing reasoning policy',()=>{
 const plain='{"messages":[{"role":"user","content":"plain"}],"max_tokens":768}';
 assert.equal(normalizeLmstudioRequest(plain,'gpt-oss-20b','default'),plain);
 const explicit=JSON.stringify({response_format:{type:'json_schema',json_schema:{name:'explicit',schema:{type:'string'}}}});
 assert.equal(normalizeLmstudioRequest(explicit,'gpt-oss-20b','default'),explicit);
 const formatted=JSON.parse(normalizeLmstudioRequest(JSON.stringify({response_format:{type:'json_object'}}),'gpt-oss-20b','off'));
 assert.equal(formatted.response_format.type,'json_schema');assert.equal(formatted.reasoning_effort,'low');
});