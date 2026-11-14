import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {HelperTextRunner,helperTextWireReply,matchesHelperTextRoute,parseHelperTextRequest} from '../../apps/desktop/workbench/src/modules/ai/lib/helperText.ts';

async function bounded(operation,label){
 let timer;
 try{return await Promise.race([operation,new Promise((_resolve,reject)=>{timer=setTimeout(()=>reject(new Error(`Timed out waiting for ${label}`)),1000);})]);}
 finally{clearTimeout(timer);}
}
test('actual helper hook retains background work after parent ready and cancels only explicit backend event',{timeout:5000},async()=>{
 const source=readFileSync(new URL('../../apps/desktop/workbench/src/modules/ai/components/useHelperTextBridge.ts',import.meta.url),'utf8').replace(/^import .*;\r?$/gm,'').replace('export function','function');
 const slots=[],effects=[];
 let index=0,listener,finish,started,completed;
 let generating=new Promise(resolve=>{started=resolve;});
 let reply=new Promise(resolve=>{completed=resolve;});
 const state={selectedModelId:'lmstudio-local',apiKeys:{},customEndpointKeys:{},recordUsage:()=>{}};
 const prefs={customEndpoints:[]};
 const same=(a,b)=>a&&b&&a.length===b.length&&a.every((value,i)=>value===b[i]);
 const memo=(create,deps)=>{const i=index++;if(!same(slots[i]?.deps,deps))slots[i]={deps,value:create()};return slots[i].value;};
 const effect=(create,deps)=>{const i=index++;if(!same(slots[i]?.deps,deps)){slots[i]?.cleanup?.();slots[i]={deps};effects.push(()=>{slots[i].cleanup=create();});}};
 const bindings={t:value=>value,useMemo:memo,useEffect:effect,useRef:value=>memo(()=>({current:value}),[]),useChatStore:Object.assign(()=>state,{getState:()=>state}),usePreferencesStore:()=>prefs,HelperTextRunner,helperTextWireReply,matchesHelperTextRoute,parseHelperTextRequest,configuredModelContextLimit:async()=>4096,buildConfiguredLanguageModel:async()=> 'selected-model',estimateTokens:value=>String(value).length,replyTokenBudget:()=>614,generateHelperText:()=>{started();return new Promise(resolve=>{finish=resolve;});},listen:async(_event,callback)=>{listener=callback;return()=>{};},invoke:async(_command,args)=>{completed(args.params);}};
 const hook=new Function(...Object.keys(bindings),stripTypeScriptTypes(source)+';return useHelperTextBridge;')(...Object.values(bindings));
 const render=status=>{index=0;const route=hook('owner','session',status);effects.splice(0).forEach(run=>run());return route;};
 try {
 const route=render('streaming');
 listener({payload:{event:'helper.text.request',data:{id:'one',owner:'owner',session:'session',route,system:'',prompt:'background',timeout_ms:1000,max_output_tokens:512}}});
 await bounded(generating,'helper generation');
 assert.equal(render('ready'),route);
 finish({text:'background complete',finishReason:'stop',usage:{}});
 assert.equal((await bounded(reply,'helper reply')).text,'background complete');
 generating=new Promise(resolve=>{started=resolve;});reply=new Promise(resolve=>{completed=resolve;});
 listener({payload:{event:'helper.text.request',data:{id:'two',owner:'owner',session:'session',route,system:'',prompt:'background',timeout_ms:1000,max_output_tokens:512}}});
 await bounded(generating,'helper generation');
 listener({payload:{event:'helper.text.cancel',data:{id:'two'}}});
 assert.match((await bounded(reply,'helper reply')).error,/cancelled/);
 } finally { slots.forEach(slot=>slot.cleanup?.()); }
});
