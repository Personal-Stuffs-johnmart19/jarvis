import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createShortcutRouter } from '../../apps/desktop/src/shortcutRouter.ts';
import { createAppConfirmation } from '../../apps/desktop/src/appConfirmation.ts';

test('shared shortcuts execute once, reject foreign sources and release document handlers', () => {
  const root = new EventTarget(), workspace = new EventTarget(), settings = new EventTarget(), foreign = new EventTarget();
  const trusted = new Set([root, workspace, settings]);
  const router = createShortcutRouter(owner => trusted.has(owner));
  let calls = 0;
  const unregister = router.register(workspace, event => { calls++; event.preventDefault(); });
  const key = () => new Event('keydown', { cancelable: true });
  router.dispatch(key(), foreign); assert.equal(calls, 0);
  router.dispatch(key(), workspace); assert.equal(calls, 0);
  const event = key(); router.dispatch(event, settings); router.dispatch(event, root); assert.equal(calls, 1);
  const composing = key(); Object.defineProperty(composing, 'isComposing', {value:true}); router.dispatch(composing, root); assert.equal(calls, 1);
  workspace.dispatchEvent(new Event('pagehide')); router.dispatch(key(), root); assert.equal(calls, 1);
  unregister(); assert.throws(() => router.register(foreign, () => {}), /Untrusted/);
});

test('theme bootstrap paints saved colors before React and tolerates damaged storage', () => {
  const code = readFileSync(new URL('../../apps/desktop/public/jarvis-theme.js', import.meta.url), 'utf8');
  for (const [cache, saved, expected] of [
    [JSON.stringify({mode:'dark',variables:{'--background':'#123456'}}),'dark','dark'],
    [null,'light','light'], ['{broken','dark','dark'],
    [JSON.stringify({mode:'dark',variables:{'--background':'#123456'}}),'system','light'],
  ]) {
    const values = {}, classes = [];
    const style = {setProperty:(k,v)=>{values[k]=v;},getPropertyValue:k=>values[k]||''};
    const root = {style,dataset:{},classList:{add:v=>classes.push(v)}};
    vm.runInNewContext(code, {document:{documentElement:root},localStorage:{getItem:k=>k==='jarvis.theme.resolved'?cache:saved},CSS:{supports:()=>true},matchMedia:()=>({matches:false})});
    assert.deepEqual(classes,[expected]); assert.equal(style.colorScheme,expected); assert.equal(root.dataset.theme,expected);
    assert.match(style.backgroundColor, /var\(--background/);
    if (cache?.startsWith('{"') && JSON.parse(cache).mode === expected) assert.equal(values['--background'],'#123456');
    else assert.equal(values['--background'],undefined);
  }
});

test('Jarvis confirmations queue, cancel without approval and reject stale or foreign owners', async () => {
  const previous = globalThis.document; const dialogs=[];
  class Element extends EventTarget {
    children=[]; append(...children){this.children.push(...children);} remove(){this.removed=true;} showModal(){this.open=true;} focus(){}
  }
  globalThis.document={createElement:()=>new Element(),body:{append:dialog=>dialogs.push(dialog)}};
  try {
    const owner = new EventTarget(); owner.document={};
    const confirm=createAppConfirmation(source=>source===owner, text=>text);
    assert.equal(await confirm(new EventTarget(),'foreign'),false);
    const first=confirm(owner,'<b>Literal user text</b>'); await Promise.resolve();
    assert.equal(dialogs[0].children[1].textContent,'<b>Literal user text</b>');
    let stopped = 0; const escape = new Event('keydown'); escape.stopPropagation = () => { stopped++; }; dialogs[0].dispatchEvent(escape); assert.equal(stopped,1);
    const second=confirm(owner,'stale queued request'); owner.document={};
    dialogs[0].children[2].children[0].onclick();
    assert.equal(await first,false); assert.equal(await second,false); assert.equal(dialogs.length,1);
    const third=confirm(owner,'accept'); await Promise.resolve(); dialogs[1].children[2].children[1].onclick(); assert.equal(await third,true);
    const fourth=confirm(owner,'unload'); await Promise.resolve(); owner.dispatchEvent(new Event('pagehide')); assert.equal(await fourth,false);
    assert.ok(dialogs.every(dialog=>dialog.removed));
  } finally { globalThis.document=previous; }
});
