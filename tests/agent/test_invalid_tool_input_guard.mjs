import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {createRegistryFixture} from './tool_registry_fixture.mjs';
import {stopOnRepeatedInvalidToolCall,stopOnUnavailableToolCall,formatAiError,formatToolInputErrors,INVALID_TOOL_INPUT_MESSAGE} from '../../apps/desktop/workbench/src/modules/ai/lib/errors.ts';
const require=createRequire(new URL('../../apps/desktop/package.json',import.meta.url));
const {generateText,isStepCount,InvalidToolInputError,NoSuchToolError}=require('ai');
const {MockLanguageModelV4}=require('ai/test');
const invalid={type:'tool-call',toolCallId:'invalid-read',toolName:'read_file',input:'{"path":[]}'};
const usage={inputTokens:{total:20},outputTokens:{total:10}};
function response(content){return{content,finishReason:{unified:content[0].type==='text'?'stop':'tool-calls',raw:'fixture'},warnings:[],usage};}
function fixture(model){
 let physical=0,unexpected=0;
 const host=async(command,args)=>{
  if(command==='backend'&&args.method==='services.register_workspace')return{ok:true};
  if(command==='fs_canonicalize')return args.path;
  if(command==='ai_read_file'){physical++;return{kind:'text',content:'42',size:2};}
  unexpected++;throw Error('Unexpected native dispatch '+command);
 };
 const f=createRegistryFixture(host,{subagentModel:model});
 const ctx={getSessionId:()=> 'owned-invalid-guard',getCwd:()=>'/owned',getWorkspaceRoot:()=>'/owned',readCache:new Map()};
 const tools=f.load('modules/ai/tools/tools.ts').buildTools(ctx);
 return {...f,ctx,tools,physical:()=>physical,unexpected:()=>unexpected};
}
async function parent(f,model){
 const budget=await f.load('modules/ai/lib/agentBudget.ts').prepareAgentBudget({modelId:'lmstudio-local',contextLimit:4096,tools:f.tools});
 return generateText({model,tools:f.tools,activeTools:budget.activeTools,instructions:budget.system,prompt:'Read the owned file.',maxRetries:0,
   stopWhen:[isStepCount(24),stopOnUnavailableToolCall,stopOnRepeatedInvalidToolCall]});
}

test('actual SDK parent permits one correction then stops repeated typed invalid input after two attempts',async()=>{
 let calls=0;
 const model=new MockLanguageModelV4({doGenerate:async()=>{calls++;return response([{...invalid,toolCallId:'invalid-'+calls}]);}});
 const f=fixture(model),result=await parent(f,model);
 assert.equal(calls,2);assert.equal(result.steps.length,2);assert.equal(f.physical(),0);assert.equal(f.unexpected(),0);
 assert.ok(result.steps.every(step=>InvalidToolInputError.isInstance(step.toolCalls[0].error)));
 assert.equal(formatAiError(result.steps.at(-1).toolCalls[0].error),INVALID_TOOL_INPUT_MESSAGE);
 const part={type:'tool-input-error',toolCallId:'invalid',errorText:String(result.steps[0].toolCalls[0].error)};
 const projected=await Array.fromAsync(formatToolInputErrors(new ReadableStream({start(c){c.enqueue(part);c.close();}}),()=> 'LOCALIZED_INPUT_GUIDANCE'));
 assert.equal(projected[0].errorText,'LOCALIZED_INPUT_GUIDANCE');assert.equal(projected[0].toolCallId,'invalid');
});

test('actual SDK correction succeeds once through the real native read handler without approval or replay',async()=>{
 let calls=0;
 const model=new MockLanguageModelV4({doGenerate:async options=>{
  calls++;
  if(calls===1)return response([invalid]);
  if(calls===2)return response([{...invalid,toolCallId:'valid-read',input:'{"path":"/owned/fixture.txt"}'}]);
  const evidence=JSON.stringify(options.prompt);
  assert.ok(evidence.includes('42')&&evidence.includes('valid-read'));
  return response([{type:'text',text:'The owned file contains 42.'}]);
 }});
 const f=fixture(model),result=await parent(f,model);
 assert.equal(calls,3);assert.equal(result.text,'The owned file contains 42.');assert.equal(f.physical(),1);assert.equal(f.unexpected(),0);
 assert.equal(result.steps[1].toolResults[0].toolCallId,'valid-read');assert.equal(result.steps[1].toolResults[0].output.content,'42');
 assert.equal(stopOnRepeatedInvalidToolCall({steps:result.steps.slice(0,2)}),false,'successful step resets the consecutive failure guard');
 assert.equal(f.ctx.readCache.size,1);
});

test('production child actual generateText applies the same two-attempt bound at its unchanged twelve-step policy',async()=>{
 let calls=0;
 const model=new MockLanguageModelV4({doGenerate:async()=>{calls++;return response([{...invalid,toolCallId:'child-invalid-'+calls}]);}});
 const f=fixture(model),run=f.load('modules/ai/agents/runSubagent.ts').runSubagent;
 const result=await run({type:'general',prompt:'Read the owned file.',keys:{},modelId:'lmstudio-local',toolContext:f.ctx,
   buildChildTools:ctx=>f.load('modules/ai/tools/tools.ts').buildTools(ctx,true)});
 assert.equal(calls,2);assert.equal(result.stepCount,2);assert.equal(result.summary,INVALID_TOOL_INPUT_MESSAGE);
 assert.equal(f.physical(),0);assert.equal(f.unexpected(),0);assert.equal(result.pending_actions,undefined);
});

test('only consecutive typed SDK failures count; success resets and unknown tool remains an immediate stop',()=>{
 const error=new InvalidToolInputError({toolName:'read_file',toolInput:'bad',cause:new Error('fixture')});
 const fail={toolCalls:[{invalid:true,error}]},success={toolCalls:[]};
 assert.equal(stopOnRepeatedInvalidToolCall({steps:[fail]}),false);
 assert.equal(stopOnRepeatedInvalidToolCall({steps:[fail,fail]}),true);
 assert.equal(stopOnRepeatedInvalidToolCall({steps:[fail,success,fail]}),false);
 assert.equal(stopOnRepeatedInvalidToolCall({steps:[{toolCalls:[{invalid:true,error:{name:'AI_InvalidToolInputError',message:'untrusted name'}}]},fail]}),false);
 assert.equal(stopOnUnavailableToolCall({steps:[{toolCalls:[{invalid:true,error:new NoSuchToolError({toolName:'missing'})}]}]}),true);
 for(const language of ['en','ru'])assert.ok(JSON.parse(readFileSync(new URL(`../../apps/desktop/src/locales/${language}.json`,import.meta.url),'utf8'))[INVALID_TOOL_INPUT_MESSAGE]);
});
