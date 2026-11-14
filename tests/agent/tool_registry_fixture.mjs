import { readFileSync, existsSync, statSync } from 'node:fs';
import { createRequire, stripTypeScriptTypes } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';

const sourceRoot = fileURLToPath(new URL('../../apps/desktop/workbench/src/', import.meta.url));
const requirePackage = createRequire(new URL('../../apps/desktop/package.json', import.meta.url));
const ai = requirePackage('ai'), zod = requirePackage('zod');
const blocked = () => { throw Error('External action is not permitted by the offline registry fixture'); };

/** Run real constructors/schemas/gateway code; mock only host/state/provider boundaries. */
export function createRegistryFixture(invoke = async () => { throw Error('Unexpected native invocation'); }, options = {}) {
  const cache = new Map();
  const settings = {getState:()=>({customEndpoints:[],customInstructions:'',lmstudioBaseURL:'http://127.0.0.1:1/v1',lmstudioModelId:'owned-fixture'})};
  const chat = {getState:()=>({apiKeys:{},customEndpointKeys:{},selectedModelId:'lmstudio-local',sessions:[],live:{},agentMeta:{}})};
  const store = {getState:()=>({active:false,sessions:[],agents:[],customAgents:[],snippets:[],getBySessionId:()=>undefined,enqueue:blocked})};
  const overrides = new Map(Object.entries({
    'modules/ai/store/chatStore.ts':{useChatStore:chat},
    'modules/ai/store/agentsStore.ts':{useAgentsStore:store},
    'modules/ai/store/snippetsStore.ts':{useSnippetsStore:store},
    'modules/ai/store/planStore.ts':{usePlanStore:store,newQueuedEditId:()=> 'owned-edit'},
    'modules/ai/store/todoStore.ts':{useTodosStore:store},
    'modules/ai/lib/sessions.ts':{loadMessages:blocked},
    'modules/ai/lib/lmstudio.ts':{lmCatalog:blocked},
    'modules/ai/agents/runSubagent.ts':{runSubagent:blocked},
    'modules/settings/preferences.ts':{usePreferencesStore:settings},
    'modules/settings/store.ts':new Proxy({}, {get:()=>blocked}),
    'modules/agents/store/managedAgentsStore.ts':{useManagedAgentsStore:store},
    'modules/terminal/index.ts':{writeToSession:blocked},
    'modules/workspace/index.ts':{currentWorkspaceEnv:()=>({kind:'local'}),workspaceScopeKey:()=> 'local'},
    'host/core.ts':{invoke},
    'host/store.ts':{LazyStore:class {}},
  }).map(([name,value])=>[path.resolve(sourceRoot,name),value]));
  if(options.subagentModel){
    overrides.delete(path.resolve(sourceRoot,'modules/ai/agents/runSubagent.ts'));
    overrides.set(path.resolve(sourceRoot,'modules/ai/lib/agent.ts'),{buildConfiguredLanguageModel:async()=>options.subagentModel,configuredModelContextLimit:async()=>options.contextLimit??4096});
    overrides.set(path.resolve(sourceRoot,'../../src/localization.ts'),{t:message=>message});
  }
  function load(filename) {
    filename=path.resolve(filename);
    if(overrides.has(filename))return overrides.get(filename);
    if(cache.has(filename))return cache.get(filename);
    if(filename.endsWith('.json'))return JSON.parse(readFileSync(filename,'utf8'));
    let source=stripTypeScriptTypes(readFileSync(filename,'utf8'));
    const exports={};cache.set(filename,exports);
    const names=[...source.matchAll(/^export (?:async )?(?:function|const|class) (\w+)/gm)].map(match=>match[1]);
    source=source.replace(/^import\s*\{([\s\S]*?)\}\s*from\s*["']([^"']+)["'];/gm,(_match,names,id)=>`const {${names.replace(/\bas\b/g,':')}}=require(${JSON.stringify(id)});`)
      .replace(/^import (\w+) from ["']([^"']+)["'];/gm,(_match,name,id)=>`const ${name}=require(${JSON.stringify(id)});`)
      .replace(/^export \{[^;]*;\r?$/gm,'')
      .replace(/^export (?=(?:async )?(?:function|const|class) )/gm,'');
    function imported(id) {
      if(id==='ai')return ai;
      if(id==='zod')return zod;
      const bare=id.startsWith('@/')?path.resolve(sourceRoot,id.slice(2)):path.resolve(path.dirname(filename),id);
      const target=[bare,bare+'.ts',bare+'.tsx',path.join(bare,'index.ts')].find(name=>existsSync(name)&&statSync(name).isFile());
      if(!target)throw Error('Unresolved fixture import '+id+' in '+filename);
      return load(target);
    }
    vm.runInThisContext('(function(require,exports){'+source+'\nObject.assign(exports,{'+names.join(',')+'});})',{filename})(imported,exports);
    return exports;
  }
  return {load:relative=>load(path.resolve(sourceRoot,relative)),ai,z:zod.z};
}
