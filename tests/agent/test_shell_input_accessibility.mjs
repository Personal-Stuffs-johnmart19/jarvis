import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createRequire, stripTypeScriptTypes } from 'node:module';

const devRequire = createRequire(new URL('../../tools/dev/package.json', import.meta.url));
const { parse } = devRequire('@babel/parser');
const read = path => fs.readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const composerSource = read('apps/desktop/workbench/src/modules/ai/components/AiComposerInput.tsx');
const appSource = read('apps/desktop/workbench/src/app/App.tsx');
const shellSource = read('apps/desktop/src/main.tsx');
function nodes(source, predicate) {
  const found = [];
  function visit(node) {
    if (!node || typeof node !== 'object') return;
    if (predicate(node)) found.push(node);
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === 'object') visit(value);
    }
  }
  visit(parse(source, { sourceType: 'module', plugins: ['typescript', 'jsx'] }));
  return found;
}
function evaluate(source, node, context) {
  const code = stripTypeScriptTypes(`(${source.slice(node.start, node.end)})`);
  return vm.runInNewContext(code, context);
}
const composerHandler = nodes(composerSource, n => n.type === 'JSXAttribute' && n.name.name === 'onKeyDown')[0].value.expression;
const workspaceHandler = nodes(appSource, n => n.type === 'VariableDeclarator' && n.id.name === 'shortcut')[0].init;
function event(key, extra = {}) {
  return {
    key, shiftKey: false, ctrlKey: false, defaultPrevented: false,
    isComposing: false, keyCode: 0, nativeEvent: { isComposing: false, keyCode: 0 },
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.stopped = true; },
    ...extra,
  };
}
function composer(pickerOpen = false) {
  const calls = [];
  const context = {
    pickerOpen, fileTrigger: null, filteredFiles: [], filteredItems: ['fixture'],
    c: { submit: () => calls.push('submit'), setValue: () => calls.push('value') },
    pickActive: () => calls.push('pick'), setActiveIndex: () => calls.push('index'),
    setTrigger: () => calls.push('trigger'), setFileTrigger: () => calls.push('file'),
  };
  return { handle: evaluate(composerSource, composerHandler, context), calls };
}

test('production composer leaves IME candidate and already-consumed keys untouched', () => {
  for (const pickerOpen of [false, true]) {
    for (const key of ['Enter', 'ArrowDown', 'ArrowUp', 'Escape', 'Tab']) {
      for (const nativeEvent of [{ isComposing: true, keyCode: 0 }, { isComposing: false, keyCode: 229 }]) {
        const { handle, calls } = composer(pickerOpen);
        const input = event(key, { nativeEvent });
        handle(input);
        assert.deepEqual(calls, [], `${key}, picker=${pickerOpen}`);
        assert.equal(input.defaultPrevented, false);
      }
    }
    const { handle, calls } = composer(pickerOpen);
    handle(event('Enter', { defaultPrevented: true }));
    assert.deepEqual(calls, []);
  }
});

test('production composer preserves Enter, Shift+Enter and picker keyboard behavior', () => {
  const plain = composer();
  const enter = event('Enter');
  plain.handle(enter);
  assert.deepEqual(plain.calls, ['submit']);
  assert.equal(enter.defaultPrevented, true);
  const multiline = composer();
  const shiftEnter = event('Enter', { shiftKey: true });
  multiline.handle(shiftEnter);
  assert.deepEqual(multiline.calls, []);
  assert.equal(shiftEnter.defaultPrevented, false);
  for (const [key, expected] of [['Enter', 'pick'], ['Tab', 'pick'], ['ArrowDown', 'index'], ['ArrowUp', 'index'], ['Escape', 'trigger']]) {
    const picker = composer(true);
    const input = event(key);
    picker.handle(input);
    assert.deepEqual(picker.calls, [expected]);
    assert.equal(input.defaultPrevented, true);
  }
});

test('workspace capture shortcuts ignore composing/consumed keys and preserve normal host routing', () => {
  const messages = [];
  const handle = evaluate(appSource, workspaceHandler, {
    window: { parent: { postMessage: value => messages.push(value.type) } },
    location: { origin: 'http://127.0.0.1' },
  });
  for (const key of ['F11', 'k']) {
    for (const extra of [{ isComposing: true }, { keyCode: 229 }, { defaultPrevented: true }]) {
      handle(event(key, { ctrlKey: true, ...extra }));
      assert.deepEqual(messages, []);
    }
  }
  const fullscreen = event('F11');
  handle(fullscreen);
  const focus = event('k', { ctrlKey: true });
  handle(focus);
  assert.deepEqual(messages, ['jarvis:fullscreen', 'jarvis:focus-main']);
  assert.equal(fullscreen.defaultPrevented && focus.defaultPrevented, true);
});

test('compact icon controls expose translated names and preserve their exact action routes', () => {
  const compact = nodes(shellSource, n => n.type === 'JSXElement' && n.openingElement.attributes.some(a => a.name?.name === 'className' && a.value?.value === 'compact-actions'))[0];
  const buttons = compact.children.filter(n => n.type === 'JSXElement');
  assert.equal(buttons.length, 3);
  for (const locale of ['en', 'ru']) {
    const catalog = JSON.parse(read(`apps/desktop/src/locales/${locale}.json`));
    for (const muted of [false, true]) {
      const actions = [];
      const context = { snapshot: { muted }, t: key => { assert.ok(catalog[key]); return catalog[key]; }, control: action => actions.push(action) };
      const names = buttons.map(button => {
        const attribute = name => button.openingElement.attributes.find(a => a.name?.name === name);
        const label = attribute('aria-label');
        assert.ok(label, 'Every compact icon control must have an accessible name');
        const name = evaluate(shellSource, label.value.expression, context);
        evaluate(shellSource, attribute('onClick').value.expression, context)();
        const icon = button.children.find(n => n.type === 'JSXElement');
        assert.ok(icon.openingElement.attributes.some(a => a.name?.name === 'aria-hidden' && a.value.value === 'true'));
        return name;
      });
      assert.deepEqual(names, [catalog[muted ? 'Microphone Off' : 'Microphone On'], catalog['Pause or Resume'], catalog.Stop]);
      assert.deepEqual(actions, ['mute', 'toggle_pause', 'stop']);
    }
  }
});
