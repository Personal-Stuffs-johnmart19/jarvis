import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, stripTypeScriptTypes } from 'node:module';
import { selectSystemPrompt } from '../../apps/desktop/workbench/src/modules/ai/config.ts';
import { activeAgentToolNames, toolProfileInstructions, everydayCapabilityRoutes } from '../../apps/desktop/workbench/src/modules/ai/lib/serviceRouting.ts';
import { formatAiError, formatToolInputErrors, stopOnUnavailableToolCall, stopOnRepeatedInvalidToolCall, UNAVAILABLE_TOOL_MESSAGE, INVALID_TOOL_INPUT_MESSAGE } from '../../apps/desktop/workbench/src/modules/ai/lib/errors.ts';
const require = createRequire(new URL('../../apps/desktop/package.json', import.meta.url));
const { streamText, generateText, asSchema, tool, NoSuchToolError, isStepCount } = require('ai');
const { MockLanguageModelV4, simulateReadableStream } = require('ai/test');
const { Chat } = require('@ai-sdk/react');
const { z } = require('zod');
const read = path => readFileSync(new URL('../../' + path, import.meta.url), 'utf8');

function productionTools(child = false) {
  const source = stripTypeScriptTypes(read('apps/desktop/workbench/src/modules/ai/tools/tools.ts'))
    .replace(/^import[\s\S]*?;\r?$/gm, '').replace(/^export \{[^;]*;\r?$/gm, '').replaceAll('export ', '');
  const names = ['read_file','list_directory','grep','glob','data_query','conversation_read','bash_run','bash_background','write_file','edit','get_terminal_output'];
  const catalogue = {};
  const stub = () => Object.fromEntries(names.map(name => [name, tool({ inputSchema: z.object({}), execute: () => { throw Error('Unrequested native operation'); } })]));
  const bindings = Object.fromEntries(['buildDataServices','buildFsTools','buildEditTools','buildSearchTools','buildShellTools','buildSubagentTools','buildTerminalTools','buildTodoTools','buildManagedAgentTools'].map(name => [name, stub]));
  bindings.buildUtilityTools = () => Object.fromEntries(['utility_catalog','utility_run','run_code'].map(name => [name, stub().read_file]));
  bindings.buildMcpTools = () => ({mcp_catalog:stub().read_file});
  bindings.buildJarvisTools = local => {Object.assign(catalogue, local);return {jarvis_catalog:stub().read_file,jarvis_action:stub().read_file};};
  bindings.buildWeatherTools = () => ({weather_report:stub().read_file});
  bindings.buildBrowserTools = () => ({browser_control:stub().read_file});
  return {tools:new Function(...Object.keys(bindings), 'child', source + ';return buildTools({},child);')(...Object.values(bindings),child), catalogue};
}

test('production parent tool projection supplies the authoritative direct names and preserves gateway-only handlers', () => {
  const {tools,catalogue} = productionTools();
  assert.ok(catalogue.bash_run && catalogue.write_file && catalogue.get_terminal_output);
  assert.equal(tools.bash_run,undefined);
  for (const contextLimit of [4096,8192,32768]) {
    const active = activeAgentToolNames(tools,contextLimit),contract = toolProfileInstructions(active);
    assert.equal(contract.split('Callable tools: ')[1].split('.')[0],active.join(', '));
    assert.match(contract,/Only these names may be called directly/);
    assert.match(contract,/jarvis_action \{name,args\}/);
    assert.equal(active.includes('bash_run'),false);
    assert.equal(active.includes('utility_run'),false);
  }
});

test('base prompts, child exploration and catalogue no longer advertise unavailable direct tools', () => {
  for (const compact of [false,true]) {
    const base=selectSystemPrompt('gemini-3.1-flash-lite-preview',compact);
    assert.doesNotMatch(base,/Tools: read_file|Mutate \(approval required\)|- Read: read_file/);
    assert.match(base,compact ? /approval card/ : /Only those exact names may be called directly/);
  }
  const registry=read('apps/desktop/workbench/src/modules/ai/agents/registry.ts');
  assert.match(registry,/discover omitted search\/directory capabilities/);
  assert.match(read('apps/desktop/workbench/src/modules/ai/agents/runSubagent.ts'),/toolProfileInstructions\(activeTools\)/);
  assert.match(read('apps/desktop/workbench/src/modules/ai/tools/jarvis.ts'),/Call only names in the current callable tool list directly/);
  for (const route of ['browser_control','weather_report','notes','system_status','features','screen_capture']) assert.ok(everydayCapabilityRoutes(['browser_control','weather_report','jarvis_catalog','jarvis_action','screen_capture']).includes(route),route);
});

test('actual SDK unavailable-tool errors produce bounded localized guidance and the Chat releases its running state', async () => {
  let requests=0,executions=0;
  const model=new MockLanguageModelV4({doStream: async () => {
    requests++;
    return {stream:simulateReadableStream({chunks:[
      {type:'stream-start',warnings:[]},
      {type:'tool-call',toolCallId:'stale-shell',toolName:'bash_run',input:'{"command":"anything"}'},
      {type:'finish',finishReason:{unified:'tool-calls',raw:'tool_calls'},usage:{inputTokens:{total:20},outputTokens:{total:10}}},
    ],initialDelayInMs:null,chunkDelayInMs:null})};
  }});
  const tools={jarvis_catalog:tool({inputSchema:z.object({query:z.string()}),execute:()=>{executions++;return {};}})};
  const chat=new Chat({transport:{sendMessages:async()=>formatToolInputErrors(streamText({model,tools,activeTools:['jarvis_catalog'],messages:[{role:'user',content:'Show the computer status.'}],stopWhen:[isStepCount(24),stopOnUnavailableToolCall],maxRetries:0,onError:()=>{}}).toUIMessageStream({onError:error=>formatAiError(error,()=> 'LOCALIZED_GUIDANCE')}),()=> 'LOCALIZED_GUIDANCE'),reconnectToStream:async()=>null}});
  await chat.sendMessage({text:'Show the computer status.'});
  assert.equal(requests,1,'no automatic repair or replay request');
  assert.equal(executions,0,'unknown names never reach a handler');
  assert.equal(chat.status,'ready');
  assert.equal(chat.error,undefined);
  const failure=chat.messages.at(-1).parts.find(part=>part.type==='dynamic-tool');
  assert.equal(failure.state,'output-error');
  assert.equal(failure.errorText,'LOCALIZED_GUIDANCE');
  assert.equal(failure.toolCallId,'stale-shell');
  assert.deepEqual(failure.input,{command:'anything'});
  assert.doesNotMatch(failure.errorText,/bash_run|Available tools|AI_NoSuchToolError/);
  assert.equal(formatAiError(new NoSuchToolError({toolName:'bash_run',availableTools:['jarvis_catalog']})),UNAVAILABLE_TOOL_MESSAGE);
});

test('unavailable-tool handling preserves unrelated errors and earlier completed work', async () => {
  const completed={type:'tool-output-available',toolCallId:'done',output:{ok:true,value:42}};
  const denied={type:'tool-output-error',toolCallId:'denied',errorText:'Permission denied\nKeep the exact native failure.'};
  const parts=await Array.fromAsync(formatToolInputErrors(new ReadableStream({start(controller){controller.enqueue(completed);controller.enqueue(denied);controller.close();}}),()=> 'translated'));
  assert.equal(parts[0],completed);
  assert.equal(parts[1],denied);
  assert.equal(stopOnUnavailableToolCall({steps:[{toolCalls:[{invalid:true,error:new Error('bad input')}]}]}),false);
  const unknown={invalid:true,error:new NoSuchToolError({toolName:'bash_run'})};
  assert.equal(stopOnUnavailableToolCall({steps:[{toolCalls:[unknown]}]}),true);
  assert.equal(stopOnUnavailableToolCall({steps:[{toolCalls:[unknown]},{toolCalls:[]}]}),false,'only the current step controls stopping');
  const message=UNAVAILABLE_TOOL_MESSAGE;
  for(const language of ['en','ru']) assert.ok(JSON.parse(read(`apps/desktop/src/locales/${language}.json`))[message]);
});

test('quick-action route hints follow custom direct subsets and gateway availability',()=>{
 assert.equal(everydayCapabilityRoutes([]),'');
 assert.equal(everydayCapabilityRoutes(['read_file']),'');
 assert.equal(everydayCapabilityRoutes(['jarvis_catalog']),'');
 assert.match(everydayCapabilityRoutes(['browser_control']),/direct browser_control immediately/);
 assert.doesNotMatch(everydayCapabilityRoutes(['browser_control']),/weather_report|system_status|catalogue notes|screen_capture/);
 assert.doesNotMatch(everydayCapabilityRoutes(['jarvis_catalog','jarvis_action']),/browser_control|weather_report|screen_capture/);
});

test('production child generateText stops its real twelve-step policy after one unknown call without execution or approval replay',async()=>{
 const {childReadContext}=await import('../../apps/desktop/workbench/src/modules/ai/agents/subagentExecution.ts');
 const {childCapabilityTools}=await import('../../apps/desktop/workbench/src/modules/ai/agents/childCapabilities.ts');
 const {archiveChildResults}=await import('../../apps/desktop/workbench/src/modules/ai/agents/childResults.ts');
 const {readModelToolResult}=await import('../../apps/desktop/workbench/src/modules/ai/lib/toolHistory.ts');
 const {assertContextBudget,replyTokenBudget,CONTEXT_FULL_MESSAGE,compactModelMessagesDetailed,estimateTokens}=await import('../../apps/desktop/workbench/src/modules/ai/lib/compact.ts');
 const {SUBAGENTS}=await import('../../apps/desktop/workbench/src/modules/ai/agents/registry.ts');
 let requests=0,executions=0,approvals=0;
 const model=new MockLanguageModelV4({doGenerate:async options=>{
  requests++;
  const names=options.tools.map(action=>action.name);
  const system=options.prompt.filter(message=>message.role==='system').map(message=>message.content).join('\n');
  assert.deepEqual(new Set(system.match(/Callable tools: ([^.]+)\./)[1].split(', ')),new Set(names));
  assert.ok(!names.includes('bash_run'));
  return {content:[{type:'tool-call',toolCallId:'child-stale-shell',toolName:'bash_run',input:'{"command":"must-not-run"}'}],
    finishReason:{unified:'tool-calls',raw:'tool_calls'},warnings:[],usage:{inputTokens:{total:20},outputTokens:{total:10}}};
 }});
 const bindings={stopOnUnavailableToolCall,stopOnRepeatedInvalidToolCall,UNAVAILABLE_TOOL_MESSAGE,INVALID_TOOL_INPUT_MESSAGE,t:message=>message,z,childReadContext,childCapabilityTools,archiveChildResults,readModelToolResult,
   asSchema,generateText,isStepCount,tool,assertContextBudget,replyTokenBudget,CONTEXT_FULL_MESSAGE,compactModelMessagesDetailed,estimateTokens,SUBAGENTS,
   activeAgentToolNames,toolProfileInstructions,buildConfiguredLanguageModel:async()=>model,configuredModelContextLimit:async()=>32768};
 const source=stripTypeScriptTypes(read('apps/desktop/workbench/src/modules/ai/agents/runSubagent.ts')).replace(/^import[\s\S]*?;\r?$/gm,'').replaceAll('export ','');
 assert.match(source,/const SUBAGENT_MAX_STEPS = 12;/,'exercise the actual production child policy unchanged');
 const run=new Function(...Object.keys(bindings),source+';return runSubagent;')(...Object.values(bindings));
 const {tools}=productionTools(true);
 for(const action of Object.values(tools))action.execute=()=>{executions++;throw Error('Unexpected child handler execution');};
 tools.jarvis_action.needsApproval=()=>{approvals++;return true;};
 const readCache=new Map([['completed-parent-read',{size:2,hash:42}]]),before=JSON.stringify([...readCache]);
 const result=await run({type:'general',prompt:'Inspect the requested status.',keys:{},modelId:'lmstudio-local',
   toolContext:{getCwd:()=>'/owned',getWorkspaceRoot:()=>'/owned',getSessionId:()=> 'owned-child',readCache},buildChildTools:()=>tools});
 assert.equal(requests,1,'the shared unknown-tool stop prevents every follow-up provider attempt');
 assert.equal(result.stepCount,1);
 assert.equal(executions,0);
 assert.equal(approvals,0);
 assert.equal(result.summary,UNAVAILABLE_TOOL_MESSAGE);
 assert.equal(result.pending_actions,undefined);
 assert.deepEqual(result.child_tool_results,[]);
 assert.equal(JSON.stringify([...readCache]),before,'completed parent evidence is not changed or replayed');
});
