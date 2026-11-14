import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createRequire, stripTypeScriptTypes } from 'node:module';

const helperSource = fs.readFileSync(new URL('../../apps/desktop/workbench/src/modules/ai/lib/explicitAgentStop.ts', import.meta.url), 'utf8');
const shellSource = fs.readFileSync(new URL('../../apps/desktop/src/main.tsx', import.meta.url), 'utf8');
const nativeSource = fs.readFileSync(new URL('../../apps/desktop/src/nativeHost.ts', import.meta.url), 'utf8');
const require = createRequire(new URL('../../tools/dev/package.json', import.meta.url));
const { parse } = require('@babel/parser');
function helper(owner) {
  const code = stripTypeScriptTypes(helperSource).replaceAll('export ', '');
  return vm.runInNewContext(`${code}\n({createExplicitStopRegistry, subscribeExplicitAgentStop, explicitAgentStop})`, owner ? { window: owner } : {});
}
function control(explicitAgentStop, run) {
  let expression;
  function visit(node) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'VariableDeclarator' && node.id.name === 'control') expression = node.init;
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === 'object') visit(value);
    }
  }
  visit(parse(shellSource, { sourceType: 'module', plugins: ['typescript', 'jsx'] }));
  assert.ok(expression, 'Use the actual shell control callback');
  return vm.runInNewContext(stripTypeScriptTypes(`(${shellSource.slice(expression.start, expression.end)})`), { explicitAgentStop, run });
}
function owner(parent) {
  const target = new EventTarget();
  target.location = { origin: 'http://127.0.0.1' };
  target.parent = parent ?? target;
  return target;
}
function realms() {
  const root = owner(), child = owner(root), outsider = owner(root);
  const allowed = new Set([root, child]);
  const registry = helper().createExplicitStopRegistry(value => allowed.has(value));
  root.__JARVIS_NATIVE__ = { explicitStops: registry };
  return { root, child, outsider, allowed, registry, shell: helper(root), workspace: helper(child) };
}

test('actual shell Stop notifies the separate workspace realm synchronously once before a rejected backend', async () => {
  const { shell, workspace } = realms();
  const events = [];
  workspace.subscribeExplicitAgentStop(() => events.push('workspace-abort'));
  const invoke = control(shell.explicitAgentStop, async (method, params) => {
    events.push(`${method}:${params.action}`);
    throw new Error('Backend disconnected');
  });
  const pending = invoke('stop');
  assert.deepEqual(events, ['workspace-abort'], 'Cross-realm abort must precede the first await/backend invocation');
  await pending;
  assert.deepEqual(events, ['workspace-abort', 'control:stop']);
  await assert.rejects(invoke('speech_stop'), /Backend disconnected/);
  assert.deepEqual(events, ['workspace-abort', 'control:stop', 'control:speech_stop']);
});

test('shared Stop registry rejects untrusted owners, removes unloaded/stale owners and isolates failures', () => {
  const { root, child, outsider, allowed, registry, workspace } = realms();
  const events = [];
  registry.register(outsider, () => events.push('untrusted'));
  workspace.subscribeExplicitAgentStop(() => { events.push('throwing'); throw new Error('Independent owner failed'); });
  workspace.subscribeExplicitAgentStop(() => events.push('second'));
  registry.notify(outsider);
  assert.deepEqual(events, []);
  registry.notify(root);
  assert.deepEqual(events, ['throwing', 'second']);
  child.dispatchEvent(new Event('pagehide'));
  registry.notify(root);
  assert.deepEqual(events, ['throwing', 'second']);
  workspace.subscribeExplicitAgentStop(() => events.push('detached'));
  allowed.delete(child);
  registry.notify(root);
  assert.deepEqual(events, ['throwing', 'second']);
  assert.ok(nativeSource.includes('createExplicitStopRegistry(allowedShortcutOwner)'), 'Use existing trusted-document predicate');
  assert.match(nativeSource, /value:\s*\{[\s\S]*explicitStops,/);
});

test('header readiness stays neutral for unprobed and previously connected models', () => {
  let label, header;
  function visit(node) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'VariableDeclarator' && node.id.name === 'connectionLabel') label = node.init;
    if (node.type === 'JSXElement' && node.openingElement.attributes.some(attribute => attribute.name?.name === 'className' && attribute.value?.value === 'connection')) header = node;
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === 'object') visit(value);
    }
  }
  visit(parse(shellSource, { sourceType: 'module', plugins: ['typescript', 'jsx'] }));
  assert.ok(label && header);
  const evaluate = (node, snapshot) => vm.runInNewContext(stripTypeScriptTypes(`(${shellSource.slice(node.start, node.end)})`), { snapshot, t: value => value });
  assert.equal(evaluate(label, null), 'Checking connection');
  for (const connected of [false, true]) assert.equal(evaluate(label, { connected }), 'AI on demand');
  const attribute = name => header.openingElement.attributes.find(item => item.name?.name === name);
  assert.equal(attribute('aria-label').value.expression.name, 'connectionLabel');
  const tooltip = attribute('title').value.expression;
  assert.equal(evaluate(tooltip, null), undefined);
  assert.equal(evaluate(tooltip, { connected: false }), 'Model connectivity is checked when you send a request.');
  const dot = header.children.find(child => child.type === 'JSXElement');
  assert.equal(dot.openingElement.attributes.some(item => item.name?.name === 'className'), false, 'No stale green online indicator');
  assert.ok(dot.openingElement.attributes.some(item => item.name?.name === 'aria-hidden' && item.value.value === 'true'));
});
