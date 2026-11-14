import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';

// Execute the production hook with disposable store selectors and effect lifetimes.
// No native transport, model server, or persistent preferences are involved.
const source = readFileSync(new URL('../../apps/desktop/workbench/src/modules/ai/hooks/useSharedContextLimit.ts',import.meta.url),'utf8')
  .replace(/^import[\s\S]*?from "[^"]+";\r?$/gm,'').replaceAll('export ','');
function fixture() {
  const chat = {selectedModelId:'lmstudio-local',agentMeta:{lastInputTokens:0}};
  const prefs = {lmstudioBaseURL:'http://127.0.0.1:1/v1',lmstudioModelId:'first',ollamaBaseURL:'http://127.0.0.1:2/v1',ollamaModelId:'ollama-first',customEndpoints:[],openaiCompatibleContextLimit:16384};
  let revision=0, stateIndex=0, effectIndex=0;
  const states=[], effects=[], pending=[], scheduled=[];
  const bindings = {
    useManagedModelCatalog:()=>[], useModelLifecycleRevision:()=>revision,
    isManagedModelId:()=>false, useChatStore:selector=>selector(chat), usePreferencesStore:selector=>selector(prefs),
    getModelContextLimit:()=>16384,isCompatModelId:()=>false,endpointIdFromCompatModel:()=>'',
    useState:initial=>{
      const index=stateIndex++;
      if (!(index in states)) states[index]=initial;
      return [states[index],value=>{states[index]=value;}];
    },
    useEffect:(run,deps)=>{
      const index=effectIndex++, old=effects[index];
      if (!old || deps.some((value,i)=>!Object.is(value,old.deps[i]))) {
        old?.cleanup?.();
        const effect={deps,cleanup:undefined};effects[index]=effect;
        scheduled.push(()=>{effect.cleanup=run();});
      }
    },
    localServerModelMetadata:(provider,base,key)=>new Promise((resolve,reject)=>pending.push({provider,base,key,resolve,reject})),
  };
  const hook = new Function(...Object.keys(bindings),stripTypeScriptTypes(source)+';return useSharedModelContext;')(...Object.values(bindings));
  return {chat,prefs,pending,lifecycle:()=>revision++,render:()=>{
    stateIndex=0;effectIndex=0;
    const result=hook();while(scheduled.length)scheduled.shift()();return result;
  }};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('shared context follows 4K to 8K and unload lifecycle while staying conservative until fresh',async()=>{
  const f=fixture();
  assert.deepEqual(f.render(),{contextLimit:4096,supportsVision:false});
  f.pending[0].resolve({contextLimit:4096,supportsVision:true});await tick();
  assert.deepEqual(f.render(),{contextLimit:4096,supportsVision:true});
  f.lifecycle();assert.deepEqual(f.render(),{contextLimit:4096,supportsVision:false});
  f.pending[1].resolve({contextLimit:8192,supportsVision:true});await tick();
  assert.deepEqual(f.render(),{contextLimit:8192,supportsVision:true});
  f.render();f.render();assert.equal(f.pending.length,2,'ordinary renders never poll metadata');
  f.lifecycle();assert.deepEqual(f.render(),{contextLimit:4096,supportsVision:false});
  f.pending[2].resolve({contextLimit:4096,supportsVision:false});await tick();
  assert.deepEqual(f.render(),{contextLimit:4096,supportsVision:false});
});

test('shared context rejects expired lifecycle responses and revoked endpoint or model preferences',async()=>{
  const f=fixture();f.render();
  f.lifecycle();f.render();
  f.pending[1].resolve({contextLimit:8192,supportsVision:true});await tick();
  assert.equal(f.render().contextLimit,8192);
  f.pending[0].resolve({contextLimit:65536,supportsVision:true});await tick();
  assert.equal(f.render().contextLimit,8192);
  f.prefs.lmstudioBaseURL='';
  assert.deepEqual(f.render(),{contextLimit:4096,supportsVision:false});
  assert.equal(f.pending.length,2,'revoked endpoint never requests a default server');
  f.prefs.lmstudioBaseURL='http://127.0.0.1:3/v1';f.prefs.lmstudioModelId='';
  assert.deepEqual(f.render(),{contextLimit:4096,supportsVision:false});
  assert.equal(f.pending.length,2,'revoked model never requests an implicit identity');
  assert.equal(f.chat.selectedModelId,'lmstudio-local');
});

test('shared context pins provider base and model while late completions cannot overwrite a changed selection',async()=>{
  const f=fixture();f.render();
  f.prefs.lmstudioModelId='second';f.render();
  f.chat.selectedModelId='ollama-local';f.render();
  assert.deepEqual(f.pending.map(({provider,base,key})=>[provider,base,key]),[
    ['lmstudio','http://127.0.0.1:1/v1','first'],['lmstudio','http://127.0.0.1:1/v1','second'],['ollama','http://127.0.0.1:2/v1','ollama-first'],
  ]);
  f.pending[2].resolve({contextLimit:8192,supportsVision:false});await tick();
  f.pending[1].resolve({contextLimit:32768,supportsVision:true});f.pending[0].resolve({contextLimit:65536,supportsVision:true});await tick();
  assert.deepEqual(f.render(),{contextLimit:8192,supportsVision:false});
  f.chat.agentMeta.lastInputTokens=123;
  assert.deepEqual(f.render(),{contextLimit:4096,supportsVision:false});
  f.pending[3].reject(new Error('synthetic server unavailable'));await tick();
  assert.deepEqual(f.render(),{contextLimit:4096,supportsVision:false});
});

test('shared context does not restore an old allocation after switching away or revoking and restoring the same endpoint',async()=>{
  const f=fixture();f.render();
  f.pending[0].resolve({contextLimit:8192,supportsVision:true});await tick();
  assert.equal(f.render().contextLimit,8192);
  f.chat.selectedModelId='gpt-5.6';f.render();
  f.chat.selectedModelId='lmstudio-local';
  assert.deepEqual(f.render(),{contextLimit:4096,supportsVision:false});
  f.pending[1].resolve({contextLimit:8192,supportsVision:true});await tick();
  assert.equal(f.render().contextLimit,8192);
  const base=f.prefs.lmstudioBaseURL;f.prefs.lmstudioBaseURL='';f.render();
  f.prefs.lmstudioBaseURL=base;
  assert.deepEqual(f.render(),{contextLimit:4096,supportsVision:false});
});
