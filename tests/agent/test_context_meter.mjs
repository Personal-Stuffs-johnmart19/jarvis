import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire,stripTypeScriptTypes} from 'node:module';
import {selectSystemPrompt} from '../../apps/desktop/workbench/src/modules/ai/config.ts';
import {activeAgentToolNames,JARVIS_SERVICE_INSTRUCTIONS,COMPACT_CONTEXT_LIMIT,toolProfileInstructions,everydayCapabilityRoutes} from '../../apps/desktop/workbench/src/modules/ai/lib/serviceRouting.ts';
import {estimateTokens,replyTokenBudget,historyBudget,compactModelMessagesDetailed} from '../../apps/desktop/workbench/src/modules/ai/lib/compact.ts';
const require=createRequire(new URL('../../apps/desktop/package.json',import.meta.url));
const {tool,asSchema}=require('ai'),{z}=require('zod');
const read=path=>readFileSync(new URL('../../'+path,import.meta.url),'utf8');
function module(path,bindings,exports){
 const source=read(path).replace(/^import[\s\S]*?;\r?$/gm,'').replaceAll('export ','');
 return new Function(...Object.keys(bindings),stripTypeScriptTypes(source)+';return {'+exports.join(',')+'};')(...Object.values(bindings));
}
const {contextBudgetEstimate,contextBudgetKey,currentContextBudget}=module('apps/desktop/workbench/src/modules/ai/lib/contextBudget.ts',{estimateTokens,historyBudget,replyTokenBudget},['contextBudgetEstimate','contextBudgetKey','currentContextBudget']);
let schemaCompiles=0;
const budget=module('apps/desktop/workbench/src/modules/ai/lib/agentBudget.ts',{asSchema:schema=>{schemaCompiles++;return asSchema(schema);},selectSystemPrompt,estimateTokens,historyBudget,COMPACT_CONTEXT_LIMIT,JARVIS_SERVICE_INSTRUCTIONS,activeAgentToolNames,toolProfileInstructions,everydayCapabilityRoutes},['prepareAgentBudget','FIXED_INSTRUCTIONS_FULL_MESSAGE']);
const tools={read_file:tool({description:'Read text',inputSchema:z.object({path:z.string()})}),jarvis_catalog:tool({description:'Discover',inputSchema:z.object({query:z.string()})}),jarvis_action:tool({description:'Act',inputSchema:z.object({name:z.string(),args:z.record(z.string(),z.unknown())})}),browser_control:tool({description:'Browse',inputSchema:z.object({action:z.string()})})};

test('actual five schemas leave 4K room for an approved print42 continuation without changing saved results',async()=>{
 const {buildFsTools}=module('apps/desktop/workbench/src/modules/ai/tools/fs.ts',{tool,z,queuedReadOnly:value=>value},['buildFsTools']);
 const {buildJarvisTools}=module('apps/desktop/workbench/src/modules/ai/tools/jarvis.ts',{tool,z,registerQueuedGateway:value=>value},['buildJarvisTools']);
 const {buildBrowserTools}=module('apps/desktop/workbench/src/modules/ai/tools/browser.ts',{tool,z},['buildBrowserTools']);
 const {buildWeatherTools}=module('apps/desktop/workbench/src/modules/ai/tools/weather.ts',{tool,z},['buildWeatherTools']);
 const shared=buildJarvisTools({},false);
 const actual={read_file:buildFsTools({}).read_file,...shared,...buildBrowserTools(shared.jarvis_action),...buildWeatherTools(shared.jarvis_action)};
 const fixed=await budget.prepareAgentBudget({modelId:'lmstudio-local',contextLimit:4096,tools:actual});
 assert.deepEqual(new Set(fixed.activeTools),new Set(['read_file','jarvis_catalog','jarvis_action','browser_control','weather_report']));
 const cwd='D:/Jarvis/.cache/desktop-test/shared-agent';
 const input={name:'run_code',args:{language:'python',code:'print(6*7)',cwd}};
 const callId='call_synthetic_approval_print42';
 const latest={role:'tool',content:[{type:'tool-result',toolCallId:callId,toolName:'jarvis_action',output:{type:'json',value:{stdout:'42\n',stderr:'',exit_code:0,timed_out:false,truncated:false,cwd,tool:'python',duration_ms:83}}}]};
 const messages=[
  {role:'user',content:`<env>workspace_root: ${cwd}; active_terminal_cwd: ${cwd}</env>\n123`},
  {role:'assistant',content:[{type:'text',text:'JARVIS_SHARED_READY'}]},
  {role:'user',content:`<env>workspace_root: ${cwd}; active_terminal_cwd: ${cwd}</env>\nUTILITY_QA:${JSON.stringify(input)}`},
  {role:'assistant',content:[{type:'tool-call',toolCallId:callId,toolName:'jarvis_action',input},{type:'tool-approval-request',approvalId:'approval_print42',toolCallId:callId}]},
  {role:'tool',content:[{type:'tool-approval-response',approvalId:'approval_print42',approved:true}]},latest,
 ];
 const saved=JSON.stringify(messages);
 const guard=compactModelMessagesDetailed(messages,4096,{reservedTokens:fixed.reservedTokens});
 assert.ok(guard.historyBudgetTokens>=850,'Keep useful continuation room with the actual five schemas');
 const fullSystem=selectSystemPrompt('gemini-3.1-flash-lite-preview',false)+JARVIS_SERVICE_INSTRUCTIONS;
 const fullReserved=estimateTokens(fullSystem)+fixed.schemaTokens+128;
 assert.equal(compactModelMessagesDetailed(messages,4096,{reservedTokens:fullReserved}).overBudget,true,'The fixture must reproduce the former repeated full-instruction overflow');
 assert.equal(guard.overBudget,false,JSON.stringify({reserved:fixed.reservedTokens,history:guard.historyBudgetTokens,estimated:guard.estimatedTokensAfter}));
 assert.equal(guard.messages.at(-1),latest);
 assert.equal(JSON.stringify(messages),saved);
 assert.equal(replyTokenBudget(4096),614);
 console.log(JSON.stringify({context:4096,old_instruction_bytes:Buffer.byteLength(fullSystem),instruction_bytes:Buffer.byteLength(fixed.system),schema_tokens:fixed.schemaTokens,reserved_tokens:fixed.reservedTokens,history_tokens:guard.historyBudgetTokens,continuation_tokens:guard.estimatedTokensAfter,saved_history_bytes:Buffer.byteLength(saved)}));
});
test('fresh 4K meter reserves the same fixed prompt, advertised schemas, reply and safety as guard',async()=>{
 const fixed=await budget.prepareAgentBudget({modelId:'lmstudio-local',contextLimit:4096,tools});
 const messages=[{role:'user',content:'123'}];
 const estimate=contextBudgetEstimate(4096,fixed.reservedTokens,messages);
 const guard=compactModelMessagesDetailed(messages,4096,{reservedTokens:fixed.reservedTokens});
 assert.ok(estimate.fixedTokens>500);assert.equal(estimate.replyTokens,614);
 assert.equal(estimate.historyBudgetTokens,guard.historyBudgetTokens);
 assert.equal(estimate.overBudget,guard.overBudget);assert.equal(guard.overBudget,false);
 assert.equal(estimate.allocatedTokens,estimate.inputTokens+estimate.replyTokens+estimate.safetyTokens);
 const compiled=schemaCompiles;
 await budget.prepareAgentBudget({modelId:'lmstudio-local',contextLimit:4096,tools,customInstructions:'New instructions'});
 assert.equal(schemaCompiles,compiled,'instruction changes must not compile unchanged schemas again');
});
test('changed model/endpoint/instructions cannot reuse a previous meter projection',()=>{
 const first=contextBudgetKey('lmstudio-local',{model:'one',endpoint:'local',instructions:'first'});
 const value={key:first,value:{inputTokens:2000}};
 assert.equal(currentContextBudget(value,first),value.value);
 for(const key of [contextBudgetKey('google',{}),contextBudgetKey('lmstudio-local',{model:'two',endpoint:'local',instructions:'first'}),contextBudgetKey('lmstudio-local',{model:'one',endpoint:'other',instructions:'first'})])assert.equal(currentContextBudget(value,key),undefined);
});
test('oversized optional instructions get actionable guidance; tiny base/schema profiles do not blame user instructions',async()=>{
 const oversized=await budget.prepareAgentBudget({modelId:'lmstudio-local',contextLimit:4096,tools,customInstructions:'x'.repeat(15000)});
 assert.equal(oversized.fixedInstructionsFull,true);assert.match(budget.FIXED_INSTRUCTIONS_FULL_MESSAGE,/Shorten those instructions/);
 assert.match(budget.FIXED_INSTRUCTIONS_FULL_MESSAGE,/new chat will not/);
 const tiny=await budget.prepareAgentBudget({modelId:'lmstudio-local',contextLimit:512,tools});
 assert.equal(tiny.fixedInstructionsFull,false);
 assert.equal(historyBudget(512,tiny.reservedTokens),0);
});

test('plan instructions alone never get attributed to custom or project instructions',async()=>{
 for(const contextLimit of [1024,2048,4096]){
  const fixed=await budget.prepareAgentBudget({modelId:'lmstudio-local',contextLimit,tools,planMode:true});
  assert.equal(fixed.fixedInstructionsFull,false);
 }
});

test('shared transport and meter projection includes allowed editor context and enforces read safety',async()=>{
 const {allowsLocalEditorContext,refersToEditor,formatEditorContext}=await import('../../apps/desktop/workbench/src/modules/ai/lib/editorContext.ts');
 const source=read('apps/desktop/workbench/src/modules/ai/lib/transport.ts');
 const projection=source.slice(source.indexOf('export async function projectRequestMessages('),source.indexOf('export function createContextAwareTransport(')).replace('export ','');
 const helpers=source.slice(source.indexOf('export function injectEnvIntoLastUser(')).replaceAll('export ','');
 let readable=true;
 const make=new Function('allowsLocalEditorContext','refersToEditor','formatEditorContext','checkReadableCanonical','native',stripTypeScriptTypes(helpers+projection)+';return projectRequestMessages;');
 const project=make(allowsLocalEditorContext,refersToEditor,formatEditorContext,async()=>({ok:readable}),{});
 const messages=[{role:'user',parts:[{type:'text',text:'Explain the open file'}]}];
 const live={cwd:'D:/QA',workspaceRoot:'D:/QA',activeFile:'D:/QA/a.ts',terminalPrivate:false,activeEditor:{path:'D:/QA/a.ts',text:'EDITOR_BOUND_CONTENT',truncated:false}};
 const permitted=await project(messages,live,'lmstudio-local','http://127.0.0.1:1234/v1');
 assert.ok(JSON.stringify(permitted).includes('EDITOR_BOUND_CONTENT'));
 readable=false;
 assert.ok(!JSON.stringify(await project(messages,live,'lmstudio-local','http://127.0.0.1:1234/v1')).includes('EDITOR_BOUND_CONTENT'));
 assert.ok(!JSON.stringify(await project(messages,live,'google','https://example.com')).includes('EDITOR_BOUND_CONTENT'));
 assert.equal(messages[0].parts[0].text,'Explain the open file');
});

test('uncached project instructions keep the 4K budget accounting and oversized-instruction guard', async () => {
 const plain = await budget.prepareAgentBudget({modelId:'lmstudio-local',contextLimit:4096,tools});
 const projectMemory = 'Keep responses concise. Preserve user files.';
 const withMemory = await budget.prepareAgentBudget({modelId:'lmstudio-local',contextLimit:4096,tools,projectMemory});
 assert.ok(withMemory.system.includes(projectMemory));
 assert.equal(withMemory.schemaTokens,plain.schemaTokens);
 assert.ok(withMemory.instructionTokens>plain.instructionTokens);
 const messages=[{role:'user',content:'123'}];
 const guard=compactModelMessagesDetailed(messages,4096,{reservedTokens:withMemory.reservedTokens});
 assert.equal(guard.overBudget,false);
 assert.equal(contextBudgetEstimate(4096,withMemory.reservedTokens,messages).historyBudgetTokens,guard.historyBudgetTokens);
 const oversized = await budget.prepareAgentBudget({modelId:'lmstudio-local',contextLimit:4096,tools,projectMemory:'x'.repeat(32768)});
 assert.equal(oversized.fixedInstructionsFull,true);
 assert.equal(historyBudget(4096,oversized.reservedTokens),0);
});

test('custom subset instructions mention only available direct routes and gateway-discoverable capabilities',async()=>{
 const readOnly=await budget.prepareAgentBudget({modelId:'gemini-3.1-flash-lite-preview',contextLimit:32768,tools:{read_file:tools.read_file}});
 assert.doesNotMatch(readOnly.system,/browser_control|weather_report|system_status|jarvis_catalog|jarvis_action|screen_capture/);
 assert.match(readOnly.system,/Callable tools: read_file\./);
 const gateway=await budget.prepareAgentBudget({modelId:'gemini-3.1-flash-lite-preview',contextLimit:32768,tools:{jarvis_catalog:tools.jarvis_catalog,jarvis_action:tools.jarvis_action}});
 assert.match(gateway.system,/computer status → catalogue system_status/);
 assert.doesNotMatch(gateway.system,/browser_control|weather_report|screen_capture/);
 const browserOnly=await budget.prepareAgentBudget({modelId:'gemini-3.1-flash-lite-preview',contextLimit:32768,tools:{browser_control:tools.browser_control}});
 assert.match(browserOnly.system,/direct browser_control immediately/);
 assert.doesNotMatch(browserOnly.system,/weather_report|system_status|jarvis_catalog|jarvis_action|screen_capture/);
});
