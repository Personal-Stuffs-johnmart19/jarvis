import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read=path=>readFileSync(new URL('../../'+path,import.meta.url),'utf8');
function callbacks(path){
 return [...read(path).matchAll(/onClick=\{(\(\) => (?:control\("[^"]+"\)|run\("control", \{ action: "[^"]+" \}\)))\}(?: aria-keyshortcuts="([^"]+)")?/g)]
  .map(match=>({code:match[1],shortcut:match[2]}));
}
test('F9, tray Stop Speech, footer and voice preview dispatch speech-only cancellation',()=>{
 const native=read('apps/desktop/src-tauri/src/main.rs');
 assert.equal(native.match(/"F9"\s*=>\s*"([^"]+)"/)[1],'speech_stop');
 assert.equal(native.match(/"stop"\s*=>\s*action\(app,\s*"([^"]+)"\)/)[1],'speech_stop');
 const footer=callbacks('apps/desktop/src/main.tsx').find(callback=>callback.shortcut==='F9');
 const calls=[];
 new Function('control',`return (${footer.code})();`)(action=>calls.push(action));
 assert.deepEqual(calls,['speech_stop']);
 const preview=callbacks('apps/desktop/src/SettingsPanel.tsx').filter(callback=>/run\("control"/.test(callback.code));
 assert.equal(preview.length,1);
 new Function('run',`return (${preview[0].code})();`)((method,args)=>calls.push({method,args}));
 assert.deepEqual(calls[1],{method:'control',args:{action:'speech_stop'}});
 // The intentionally general compact-window stop remains separate.
 assert.ok(callbacks('apps/desktop/src/main.tsx').some(callback=>callback.code==='() => control("stop")'));
});
test('explicit chat Stop still asks backend to stop agent work',()=>{
 const source=read('apps/desktop/workbench/src/modules/ai/lib/composer.tsx');
 const callback=source.match(/explicitAgentStop\([^,\n]+, (.*?)\);/)[1];
 let observed;
 new Function('invoke',`return (${callback})();`)((command,args)=>{observed={command,args};});
 assert.deepEqual(observed,{command:'backend',args:{method:'control',params:{action:'stop'}}});
});
