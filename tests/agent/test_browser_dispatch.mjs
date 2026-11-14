import test from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { browserObservation, budgetBrowserObservations } from '../../apps/desktop/workbench/src/modules/ai/lib/browserObservation.ts';
import { readToolResult } from '../../apps/desktop/workbench/src/modules/ai/lib/toolHistory.ts';
registerHooks({resolve(specifier, context, next) {
  if (specifier === '@/host/core') return {url:'data:text/javascript,export const invoke=(command,args)=>globalThis.__browserDispatchInvoke(command,args);', shortCircuit:true};
  if (specifier.startsWith('.') && context.parentURL?.endsWith('.ts')) {
    const url = new URL(specifier+'.ts', context.parentURL);
    if (existsSync(url)) return {url:url.href, shortCircuit:true};
  }
  return next(specifier, context);
}});

test('direct browser control uses saved profile and canonical approval receipt exactly once without catalogue discovery', async () => {
  const calls=[];
  globalThis.__browserDispatchInvoke=async (command, request) => {
    assert.equal(command,'backend'); calls.push(request);
    if(request.method==='agent.prepare') return {token:'fixture-receipt',requires_approval:true};
    if(request.method==='agent.execute') return {ok:true,verified:false,fixture:true};
    throw new Error('Unexpected discovery call');
  };
  const {buildJarvisTools,revokePendingActions}=await import('../../apps/desktop/workbench/src/modules/ai/tools/jarvis.ts');
  const {buildBrowserTools}=await import('../../apps/desktop/workbench/src/modules/ai/tools/browser.ts');
  try {
    const shared=buildJarvisTools({},false);
    const browser=buildBrowserTools(shared.jarvis_action).browser_control;
    const input={action:'go_to',url:'https://www.youtube.com/results?search_query=music'};
    const options={toolCallId:'browser-direct',messages:[]};
    assert.equal(await browser.needsApproval(input,options),true);
    assert.deepEqual(calls.map(call=>call.method),['agent.prepare']);
    assert.deepEqual(calls[0].params,{name:'browser_control',args:input});
    assert.equal(Object.hasOwn(calls[0].params.args,'profile_id'),false);
    assert.equal((await browser.execute(input,options)).ok,true);
    await assert.rejects(browser.execute(input,options),/Expired action/);
    assert.deepEqual(calls.map(call=>call.method),['agent.prepare','agent.execute']);
  } finally { revokePendingActions();delete globalThis.__browserDispatchInvoke; }
});

test('model browser excerpts preserve verification and errors while full raw observations remain recoverable', () => {
  const raw={ok:false,verified:false,error:'Player not ready',text:'x'.repeat(5999)+'😀'+'y'.repeat(14000)+'TAIL'};
  const original=JSON.stringify(raw);
  const projected=browserObservation(raw,'observed-call');
  assert.equal(projected.text.length,5999);
  assert.equal(projected.verified,false);
  assert.equal(projected.error,'Player not ready');
  assert.equal(projected.model_excerpt,true);
  assert.equal(projected.full_result.tool_call_id,'observed-call');
  assert.equal(JSON.stringify(raw),original);
  const messages=[{parts:[{type:'tool-browser_control',state:'output-available',toolCallId:'observed-call',output:raw}]}];
  assert.ok(readToolResult(messages,'observed-call',19000,3000).text.includes('TAIL'));
  assert.ok(JSON.stringify(projected).length < original.length/2);
});

test('per-run browser projections fit small allocations without changing approval/execution or unrelated output', async () => {
  const execute=async()=>({ok:true}), needsApproval=async()=>true;
  const original={execute,needsApproval,toModelOutput:({output})=>({type:'json',value:output})};
  const raw={ok:true,text:'x'.repeat(20000)};
  for(const [context,limit] of [[4096,1000],[8192,3000],[32768,6000]]) {
    const tools=budgetBrowserObservations({browser_control:original,jarvis_action:original},context);
    assert.equal(tools.browser_control.execute,execute);
    assert.equal(tools.browser_control.needsApproval,needsApproval);
    for(const [name,input] of [['browser_control',{action:'snapshot'}],['jarvis_action',{name:'browser_control',args:{action:'snapshot'}}]]) {
      const projected=await tools[name].toModelOutput({input,output:raw,toolCallId:'saved'});
      assert.equal(projected.value.text.length,limit);
      assert.equal(projected.value.full_result.tool_call_id,'saved');
    }
    const other=await tools.jarvis_action.toModelOutput({input:{name:'documents.read'},output:raw,toolCallId:'doc'});
    assert.equal(other.value,raw);
  }
  assert.equal(raw.text.length,20000);
});

test('advanced browser operations keep the full canonical schema instead of the compact direct subset', async () => {
  const items=JSON.parse(readFileSync(new URL('../../local_mode/tool_declarations.json',import.meta.url),'utf8'));
  globalThis.__browserDispatchInvoke=async () => ({items,next_cursor:null});
  const {buildJarvisTools}=await import('../../apps/desktop/workbench/src/modules/ai/tools/jarvis.ts');
  try {
    const catalogue=await buildJarvisTools({},false).jarvis_catalog.execute({query:'browser_control',category:'',cursor:0,limit:8},{});
    assert.match(catalogue.items[0].parameters.properties.action.description,/playlist_save/);
    assert.ok(catalogue.items[0].parameters.properties.profile_id);
  } finally { delete globalThis.__browserDispatchInvoke; }
});

test('music intent is advertised and dispatched through the canonical browser gateway with optional query', async () => {
  const items=JSON.parse(readFileSync(new URL('../../local_mode/tool_declarations.json',import.meta.url),'utf8'));
  const descriptor=items.find(item=>item.name==='browser_control');
  assert.match(descriptor.description,/music_play.*default music/);
  assert.match(descriptor.parameters.properties.action.description,/music_play/);
  assert.match(descriptor.parameters.properties.query.description,/music_play.*defaults to music/);
  const {buildBrowserTools}=await import('../../apps/desktop/workbench/src/modules/ai/tools/browser.ts');
  const calls=[];
  const shared={
    needsApproval:async(input)=>{calls.push(['prepare',input]);return true;},
    execute:async(input)=>{calls.push(['execute',input]);return {ok:true,verified:false};},
  };
  const browser=buildBrowserTools(shared).browser_control;
  for(const input of [{action:'music_play'},{action:'music_play',query:'instrumental music'}]) {
    const parsed=browser.inputSchema.parse(input);
    assert.deepEqual(parsed,input);
    assert.equal(await browser.needsApproval(parsed,{}),true);
    assert.deepEqual(await browser.execute(parsed,{}),{ok:true,verified:false});
    assert.deepEqual(calls.splice(0),[
      ['prepare',{name:'browser_control',args:input}],
      ['execute',{name:'browser_control',args:input}],
    ]);
  }
  assert.equal(browser.inputSchema.safeParse({action:'music_play',query:'   '}).success,false);
  assert.equal(browser.inputSchema.safeParse({action:'music_play',query:'x'.repeat(201)}).success,false);
});
