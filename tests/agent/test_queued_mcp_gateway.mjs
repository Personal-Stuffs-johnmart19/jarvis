import assert from 'node:assert/strict';
import test from 'node:test';
import {registerHooks,createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const root=new URL('../../apps/desktop/',import.meta.url),require=createRequire(new URL('package.json',root));
const zodUrl=pathToFileURL(require.resolve('zod')).href;
const data=source=>`data:text/javascript,${encodeURIComponent(source)}`;
const fixture=globalThis.__queuedMcpGatewayFixture={asSchema:require('ai').asSchema};
const hook=registerHooks({resolve(specifier,context,nextResolve){
  const mocks={ai:'export const tool=x=>x; export const asSchema=x=>globalThis.__queuedMcpGatewayFixture.asSchema(x);',
    '@/host/core':'export const invoke=(...args)=>globalThis.__queuedMcpGatewayFixture.invoke(...args);',
    '../lib/browserObservation':'export const browserObservation=()=>null;'};
  if(specifier in mocks)return {url:data(mocks[specifier]),shortCircuit:true};
  if(specifier==='zod')return nextResolve(zodUrl,context);
  if(specifier.startsWith('.')&&context.parentURL?.includes('/workbench/src/modules/')&&!/\.[a-z]+$/.test(specifier))return nextResolve(specifier+'.ts',context);
  return nextResolve(specifier,context);
}});
const {buildMcpTools}=await import(new URL('workbench/src/modules/ai/tools/mcp.ts',root));
const {buildJarvisTools,revokePendingAction}=await import(new URL('workbench/src/modules/ai/tools/jarvis.ts',root));
const {queuedGatewayAction,queuedMcpPreparation,isQueuedMcp}=await import(new URL('workbench/src/modules/ai/tools/queuedDispatch.ts',root));
hook.deregister();
for(const policy of ['ask','allow','deny'])test(`canonical gateway MCP ${policy} keeps exact private handler and one preparation`,async()=>{
  const calls=[];fixture.invoke=async(command,args)=>{
    calls.push({command,args});
    if(command==='mcp_tool_info')return {policy,config_revision:'revision',connection_generation:'generation'};
    if(command==='mcp_call_tool')return {content:[{type:'text',text:'done'}]};
  };
  const local=buildMcpTools({getSessionId:()=> 'session'}),original=local.mcp_call;
  const gateway=buildJarvisTools(local,false).jarvis_action;
  local.mcp_call={...original,needsApproval:()=>true,execute:()=>{throw Error('replacement must not execute');}};
  assert.equal(queuedGatewayAction(gateway,'mcp_call'),original);
  assert.equal(isQueuedMcp(local.mcp_call),false);
  assert.equal(queuedGatewayAction({...gateway},'mcp_call'),undefined);
  const input={name:'mcp_call',args:{server_id:'fake',tool_name:'write',arguments:{path:'opaque'}}};
  const options={toolCallId:crypto.randomUUID(),context:{},messages:[]};
  assert.equal(await gateway.needsApproval(input,options),policy==='ask');
  assert.equal(queuedMcpPreparation(original,options.toolCallId),policy==='deny'?'unavailable':policy);
  if(policy!=='deny')assert.deepEqual(await gateway.execute(input,options),{content:[{type:'text',text:'done'}]});
  else revokePendingAction(options.toolCallId);
  assert.equal(calls.filter(c=>c.command==='mcp_tool_info').length,1);
  assert.equal(calls.filter(c=>c.command==='mcp_call_tool').length,policy==='deny'?0:1);
  assert.equal(queuedMcpPreparation(original,options.toolCallId),'unavailable');
});
