import { stopOnUnavailableToolCall, stopOnRepeatedInvalidToolCall } from "../../apps/desktop/workbench/src/modules/ai/lib/errors.ts";
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {localCaptureBase,restrictScreenHistory} from '../../apps/desktop/workbench/src/modules/ai/lib/screenCapture.ts';
import {COMPACT_CONTEXT_LIMIT} from '../../apps/desktop/workbench/src/modules/ai/lib/serviceRouting.ts';

const source=readFileSync(new URL('../../apps/desktop/workbench/src/modules/ai/lib/agent.ts',import.meta.url),'utf8');
const runtimeSource=source.slice(source.indexOf('export async function configuredModelRuntime('),source.indexOf('export async function buildConfiguredLanguageModel('));
const runSource=source.slice(source.indexOf('export async function runAgentStream('));
function runtime(metadata){
 const bindings={localServerModelMetadata:metadata,isManagedModelId:()=>false,isCompatModelId:()=>false,getModelContextLimit:()=>16384,LMSTUDIO_DEFAULT_BASE_URL:'http://localhost:1234/v1',OLLAMA_DEFAULT_BASE_URL:'http://localhost:11434/v1'};
 return new Function(...Object.keys(bindings),stripTypeScriptTypes(runtimeSource.replaceAll('export ',''))+';return {configuredModelRuntime,configuredModelContextLimit};')(...Object.values(bindings));
}

test('configured runtime reads context and capability together; context-only callers stay compatible',async()=>{
 let count=0;
 const api=runtime(async()=>{count++;return {contextLimit:8192,supportsVision:true};});
 assert.deepEqual(await api.configuredModelRuntime('lmstudio-local',{lmstudioModelId:'actual-instance'}),{contextLimit:8192,supportsVision:true});
 assert.equal(count,1);
 assert.equal(await api.configuredModelContextLimit('lmstudio-local'),8192);
 assert.equal(count,2);
 const failed=runtime(async()=>{throw new Error('metadata unavailable');});
 assert.deepEqual(await failed.configuredModelRuntime('lmstudio-local'),{contextLimit:4096,supportsVision:false});
 const controller=new AbortController();
 const cancelled=runtime(async()=>{controller.abort();throw new DOMException('Stopped','AbortError');});
 await assert.rejects(cancelled.configuredModelRuntime('lmstudio-local',{},controller.signal),{name:'AbortError'});
});

test('agent exposes and replays capture only for explicit vision metadata at the pinned endpoint',async()=>{
 for(const [vision,base,allowed] of [[false,'http://localhost:1234/v1',false],[true,'http://localhost:1234/v1',true],[true,'http://example.com:1234/v1',false]]){
  let reads=0;
  const manual={role:'user',content:[{type:'image',image:'manual attachment'}]};
  const history=[{role:'tool',content:[{type:'tool-result',toolName:'screen_capture',toolCallId:'capture',output:{type:'content',value:[{type:'image-data',data:'CAPTURE_BYTES',mediaType:'image/jpeg'}]}}]},manual];
  const bindings={stopOnUnavailableToolCall,stopOnRepeatedInvalidToolCall,resolveModel:()=>({provider:'lmstudio'}),localCaptureBase,buildConfiguredLanguageModel:async()=>({}),configuredModelRuntime:async()=>{reads++;return {contextLimit:4096,supportsVision:vision};},buildStableSystem:()=>'',budgetBrowserObservations:tools=>({...tools}),buildScreenTools:(_session,canCapture)=>({screen_capture:{inputSchema:{},toModelOutput:()=>canCapture}}),restrictScreenHistory,convertToModelMessages:async messages=>messages,projectInterruptedHistory:messages=>messages,modelKeepsReasoning:()=>false,pruneMessages:({messages})=>messages,activeAgentToolNames:tools=>Object.keys(tools),asSchema:()=>({jsonSchema:{}}),estimateTokens:()=>0,prepareAgentPrompt:(system,_plan,messages)=>({system,messages}),createReadFailureGuard:()=>({limited:false}),replyTokenBudget:()=>1024,streamText:options=>options,isStepCount:()=>()=>false,MAX_AGENT_STEPS:5};
  Object.assign(bindings,{COMPACT_CONTEXT_LIMIT,prepareAgentBudget:async({tools})=>({system:"",plan:null,activeTools:Object.keys(tools),reservedTokens:128,fixedInstructionsFull:false}),compactModelMessagesDetailed:messages=>({messages,compacted:false}),assertContextBudget:()=>{},t:value=>value,CONTEXT_FULL_MESSAGE:'full'});
  const run=new Function(...Object.keys(bindings),stripTypeScriptTypes(runSource.replace('export ',''))+';return runAgentStream;')(...Object.values(bindings));
  const result=await run({modelId:'lmstudio-local',keys:{},lmstudioBaseURL:base,lmstudioModelId:'selected',toolContext:{getSessionId:()=> 'session'},tools:{},uiMessages:history});
  assert.equal(reads,1);
  assert.equal('screen_capture' in result.tools,allowed);
  assert.equal(JSON.stringify(result.messages).includes('CAPTURE_BYTES'),allowed);
  assert.equal(result.messages[1],manual);
  const prepared=result.prepareStep({messages:history});
  assert.equal(JSON.stringify(prepared.messages).includes('CAPTURE_BYTES'),allowed);
 }
});
