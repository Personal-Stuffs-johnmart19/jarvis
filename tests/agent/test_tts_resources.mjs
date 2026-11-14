import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {runInNewContext} from 'node:vm';
const require=createRequire(new URL('../../apps/desktop/package.json',import.meta.url));
const {rolldown}=await import(pathToFileURL(require.resolve('rolldown',{paths:[require.resolve('vite')]})).href);
const component=fileURLToPath(new URL('../../apps/desktop/src/TtsResources.tsx',import.meta.url));
const bundle=await rolldown({input:component,external:id=>id!==component,transform:{jsx:{runtime:'automatic'}}});
const {output}=await bundle.generate({format:'cjs'});await bundle.close();
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function text(tree){return typeof tree==='string'?tree:tree&&typeof tree==='object'?[tree.props?.children].flat(Infinity).map(text).join(''):'';}
async function resourcePanel(status){
 const state=[],effects=[],translations=[],calls=[];let index=0,initialized=false;
 const jsx=(type,props)=>({type,props});const exports={};
 const react={useState(initial){const n=index++;if(!(n in state))state[n]=initial;return[state[n],value=>{state[n]=value;}];},useEffect(fn){if(!initialized)effects.push(fn);}};
 runInNewContext(output[0].code,{exports,require(name){
  if(name==='react')return react;
  if(name==='react/jsx-runtime')return{jsx,jsxs:jsx};
  if(name==='@tauri-apps/api/core')return{isTauri:()=>true,invoke:async(command,args)=>{calls.push({command,args});return status;}};
  if(name==='./localization')return{t:value=>{translations.push(value);return value==='publisher/model'?'WRONG_TRANSLATED_REPOSITORY':value;}};
  throw Error(name);
 }});
 const render=()=>{index=0;const tree=exports.TtsResources();initialized=true;while(effects.length)effects.shift()();return tree;};
 render();await flush();return{tree:render(),translations,calls};
}
test('managed TTS repository identities stay verbatim despite catalog collisions',async()=>{
 const weights={repo_id:'publisher/model',revision:'a'.repeat(40),mode:'vulkan',managed:true};
 const ui=await resourcePanel({...weights,piper:{...weights,available:true,mode:'cpu'}});
 assert.ok(text(ui.tree).includes('publisher/model'));
 assert.ok(!text(ui.tree).includes('WRONG_TRANSLATED_REPOSITORY'));
 assert.ok(!ui.translations.includes('publisher/model'));
 assert.equal(ui.calls.length,1);assert.equal(ui.calls[0].args.params.name,'tts.model.status');
});
test('built-in TTS labels are app messages while unavailable Piper has no bogus weights',async()=>{
 const ui=await resourcePanel({repo_id:'raw',revision:'',mode:'vulkan',managed:false,piper:{repo_id:'ignored',revision:'',mode:'cpu',managed:false,available:false}});
 assert.ok(ui.translations.includes('Built-in Supertonic 3'));
 assert.ok(ui.translations.includes('Unavailable'));
 assert.ok(!text(ui.tree).includes('ignored'));
});
