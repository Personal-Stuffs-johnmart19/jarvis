import test from 'node:test';
import assert from 'node:assert/strict';
import {createRegistryFixture} from './tool_registry_fixture.mjs';
import {activeAgentToolNames} from '../../apps/desktop/workbench/src/modules/ai/lib/serviceRouting.ts';

const GROUPS={
 data:['data_query','conversation_read','tool_result_read','voice_activation','preferences_set','models_list','model_select','agent_select','snippet_save'],
 filesystem:['read_file','list_directory','write_file','create_directory','edit','multi_edit','grep','glob'],
 shell:['bash_run','bash_background','bash_logs','bash_list','bash_kill'],
 agents:['run_subagent','spawn_coding_agent','send_to_agent','read_agent_output'],
 workspace:['suggest_command','get_terminal_output','open_preview','todo_write'],
 utilities:['utility_catalog','utility_run','run_code','archive_list','archive_create','archive_extract'],
 mcp:['mcp_catalog','mcp_call'],
};
const REQUIRED=['preferences_set','model_select','agent_select','snippet_save','write_file','create_directory','edit','multi_edit','bash_run','bash_background','spawn_coding_agent','send_to_agent','utility_run','run_code','archive_create','archive_extract'];
const sorted=values=>[...values].sort();
function setup(extraInvoke){
 const calls=[];
 const runtime=createRegistryFixture(async(command,args)=>{
  calls.push({command,args});
  if(command==='backend'&&args.method==='services.register_workspace')return {ok:true};
  if(command==='backend'&&args.method==='services.catalog')return {items:[],next_cursor:null};
  if(extraInvoke)return extraInvoke(command,args);
  throw Error('Unexpected fixture host dispatch '+command);
 });
 const ctx={getSessionId:()=> 'owned-inventory',getCwd:()=>'/owned',getWorkspaceRoot:()=>'/owned',readCache:new Map()};
 const tools=runtime.load('modules/ai/tools/tools.ts').buildTools(ctx);
 return {...runtime,calls,ctx,tools,descriptors:calls.find(call=>call.args?.method==='services.register_workspace').args.params.items};
}

test('all38real workspace constructors publish unique executable SDK schemas and exact lazy lookup',async()=>{
 const f=setup(),expected=Object.values(GROUPS).flat();
 assert.equal(expected.length,38);
 assert.deepEqual(sorted(f.descriptors.map(item=>item.name)),sorted(expected));
 const {queuedGatewayAction}=f.load('modules/ai/tools/queuedDispatch.ts');
 for(const descriptor of f.descriptors){
  const action=queuedGatewayAction(f.tools.jarvis_action,descriptor.name);
  assert.equal(typeof action?.execute,'function',descriptor.name);
  const sdk=await f.ai.asSchema(action.inputSchema).jsonSchema;
  assert.equal(sdk.type,'object',descriptor.name);
  assert.equal(descriptor.parameters.type,'object',descriptor.name);
  assert.deepEqual(descriptor.parameters,sdk,descriptor.name+' catalogue must describe actual SDK input, including optional defaults');
  const page=await f.tools.jarvis_catalog.execute({query:descriptor.name,category:'',cursor:0,limit:1},{toolCallId:'lookup-'+descriptor.name,messages:[]});
  assert.equal(page.items[0].name,descriptor.name);
  assert.deepEqual(page.items[0].parameters,descriptor.parameters);
  assert.equal(page.items[0].executor,'native-host');
  assert.equal(page.items[0].available,true);
 }
 assert.equal(f.calls.length,1,'constructing and looking up every local handler needs only the workspace registration');
 assert.deepEqual(sorted(f.descriptors.filter(item=>item.approval==='required').map(item=>item.name)),sorted(REQUIRED));
 assert.deepEqual(f.descriptors.filter(item=>item.approval==='dynamic').map(item=>item.name),['mcp_call']);
});

test('actual full compact and child surfaces retain all lazy handlers without directly advertising execution',async()=>{
 const f=setup();
 assert.equal(Object.keys(f.tools).length,14);
 assert.equal(activeAgentToolNames(f.tools,32768).length,11);
 assert.deepEqual(sorted(activeAgentToolNames(f.tools,4096)),sorted(['read_file','jarvis_catalog','jarvis_action','weather_report','browser_control']));
 assert.ok(!activeAgentToolNames(f.tools,32768).includes('bash_run'));
 const child=f.load('modules/ai/tools/tools.ts').buildTools(f.ctx,true);
 assert.equal(Object.keys(child).length,11);
 assert.equal(activeAgentToolNames(child,32768).length,10);
 assert.equal(activeAgentToolNames(child,4096).length,4);
 const {queuedGatewayAction}=f.load('modules/ai/tools/queuedDispatch.ts');
 assert.equal(queuedGatewayAction(child.jarvis_action,'run_subagent'),undefined);
 for(const name of Object.values(GROUPS).flat().filter(name=>name!=='run_subagent'))assert.ok(queuedGatewayAction(child.jarvis_action,name),name);
});

test('lazy native read validates schema and preserves canonical approval receipt arguments without replay',async()=>{
 let physical=0;
 const f=setup(async(command,args)=>{
  if(command==='fs_canonicalize')return args.path;
  if(command==='ai_read_file'){physical++;return {kind:'text',content:'owned result',size:12};}
  throw Error('Unexpected native '+command);
 });
 const input={name:'read_file',args:{path:'/owned/fixture.txt'}},options={toolCallId:'owned-read',messages:[],abortSignal:new AbortController().signal};
 assert.equal(await f.tools.jarvis_action.needsApproval(input,options),false);
 const output=await f.tools.jarvis_action.execute({name:'read_file',args:{path:'/owned/other.txt'}},options);
 assert.equal(output.content,'owned result');
 assert.equal(output.path,'/owned/fixture.txt','execution uses the schema-checked receipt, not changed arguments');
 assert.equal(physical,1);
 await assert.rejects(f.tools.jarvis_action.execute(input,options),/Expired|missing|receipt/i);
 assert.equal(physical,1);
 const invalid={name:'read_file',args:{path:[]}},bad={...options,toolCallId:'invalid-read'};
 assert.equal(await f.tools.jarvis_action.needsApproval(invalid,bad),false);
 assert.ok((await f.tools.jarvis_action.execute(invalid,bad)).error);
 assert.equal(physical,1);
});

test('MCP discovery remains enabled-only lazy and preserves exact schema pagination without connecting personal services',async()=>{
 const f=setup(async(command,args)=>{
  if(command==='mcp_config_get')return {servers:[{id:'enabled',name:'Owned fixture'},{id:'disabled',name:'Disabled fixture'}]};
  if(command==='mcp_status')return [{id:'enabled',enabled:true,connected:false},{id:'disabled',enabled:false,connected:false}];
  if(command==='mcp_connect'){assert.equal(args.serverId,'enabled');return {ok:true};}
  if(command==='mcp_tools'){assert.deepEqual(args,{serverId:'enabled',query:'read',cursor:4,limit:2,includeParameters:true});return {items:[{name:'owned_read',inputSchema:{type:'object'}}],next_cursor:null};}
  throw Error('Unexpected MCP host dispatch '+command);
 });
 const options={toolCallId:'mcp-discovery',messages:[],abortSignal:new AbortController().signal};
 const index=await f.tools.mcp_catalog.execute({},options);
 assert.deepEqual(index.servers.map(server=>server.id),['enabled']);
 assert.equal(f.calls.filter(call=>call.command==='mcp_connect').length,0);
 const exact=await f.tools.mcp_catalog.execute({server_id:'enabled',query:'read',cursor:4,limit:2,include_parameters:true},options);
 assert.equal(exact.items[0].name,'owned_read');
 assert.equal(f.calls.filter(call=>call.command==='mcp_connect').length,1);
 assert.equal(f.calls.filter(call=>call.command==='mcp_tools').length,1);
 assert.equal(f.calls.filter(call=>call.command==='mcp_execute').length,0);
});

test('catalogue input schemas allow omission and real gateway admission applies runtime defaults exactly once',async()=>{
 let dispatched;
 const f=setup(async(command,args)=>{
  assert.equal(command,'backend');assert.equal(args.method,'services.read');
  dispatched=args.params;return {ok:true,state:'owned-read'};
 });
 const descriptor=f.descriptors.find(item=>item.name==='data_query');
 assert.deepEqual(descriptor.parameters.required,['source']);
 const input={name:'data_query',args:{source:'runtime'}},options={toolCallId:'defaulted-read',messages:[]};
 assert.equal(await f.tools.jarvis_action.needsApproval(input,options),false);
 assert.deepEqual(await f.tools.jarvis_action.execute(input,options),{ok:true,state:'owned-read'});
 assert.deepEqual(dispatched,{name:'data.query',args:{source:'runtime',query:'',cursor:0,limit:20}});
 assert.equal(f.calls.filter(call=>call.args?.method==='services.read').length,1);
});
