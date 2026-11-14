import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../desktop/qa_queued_task.py', import.meta.url), 'utf8');
const audit = Function(`return (${source.match(/NATIVE_AUDIT = r"""([\s\S]*?)"""/)[1]})`)();
const move = { source: 'D:/.cache/desktop-test/queued-task-own/source.txt', dest: 'D:/.cache/desktop-test/queued-task-own/dest.txt' };
const exact = { steps: [{ step: 1, tool: 'file_controller', critical: true,
  parameters: { action: 'move', path: move.source, destination: move.dest } }] };

async function setup(options = {}) {
  const forwarded = [];
  let listener;
  globalThis.window = { __JARVIS_NATIVE__: {
    core: { invoke: async (command, args) => { forwarded.push({ command, args }); return { ok: true }; } },
    event: { listen: async (_name, callback) => { listener = callback; return () => {}; } },
  } };
  await audit({ provider: 'fake', planOnly: true, moves: [move], session: 'owned-session', ...options });
  listener({ payload: { event: 'helper.text.request', data: { id: 'owned-id', session: 'owned-session' } } });
  return { forwarded, complete: text => window.__JARVIS_NATIVE__.core.invoke('backend', {
    method: 'helper.text.complete', params: { id: 'owned-id', owner: 'owned', session: 'owned-session', route: 'pinned', text },
  }) };
}

test('exact one-step owned move is forwarded unchanged before canonical approval', async () => {
  const fixture = await setup();
  const text = JSON.stringify(exact);
  await fixture.complete(text);
  assert.equal(fixture.forwarded.length, 1);
  assert.equal(fixture.forwarded[0].args.params.text, text);
  assert.equal(window.__qaQueued.helperCompletions, 1);
  assert.equal(window.__qaQueued.blocked, 0);
});

test('only known owned helper errors are recorded, never arbitrary response text', async () => {
  const fixture = await setup();
  await window.__JARVIS_NATIVE__.core.invoke('backend', { method: 'helper.text.complete',
    params: { id: 'owned-id', text: '', error: '[request_failed] Selected provider text request failed' } });
  assert.deepEqual(window.__qaQueued.helperErrors, [{ code: 'request_failed', text: 'Selected provider text request failed' }]);
  assert.equal(fixture.forwarded.length, 1);
  const second = await setup();
  await window.__JARVIS_NATIVE__.core.invoke('backend', { method: 'helper.text.complete',
    params: { id: 'owned-id', text: '', error: 'raw model content or credential must not be logged' } });
  assert.deepEqual(window.__qaQueued.helperErrors, [{ code: 'unclassified_helper_error', text: 'Unclassified helper error' }]);
  assert.equal(second.forwarded.length, 1);
});

test('recognizes the current native_format helper code', async () => {
  await setup();
  await window.__JARVIS_NATIVE__.core.invoke('backend', { method: 'helper.text.complete',
    params: { id: 'owned-id', text: '', error: '[native_format] Selected provider rejected the helper request format' } });
  assert.deepEqual(window.__qaQueued.helperErrors, [{ code: 'native_format', text: 'Selected provider rejected the helper request format' }]);
});

test('a valid native_format prefix cannot print arbitrary URLs, keys or model output', async () => {
  await setup();
  await window.__JARVIS_NATIVE__.core.invoke('backend', { method: 'helper.text.complete',
    params: { id: 'owned-id', text: '', error: '[native_format] https://private.invalid?api_key=SECRET private model text' } });
  assert.deepEqual(window.__qaQueued.helperErrors, [{ code: 'native_format', text: 'Selected provider rejected the helper request format' }]);
  assert.doesNotMatch(JSON.stringify(window.__qaQueued.helperErrors), /https|SECRET|private/);
});

test('every current HelperErrorCode maps to a fixed audit description', async () => {
  const helper = await readFile(new URL('../../apps/desktop/workbench/src/modules/ai/lib/helperText.ts', import.meta.url), 'utf8');
  const declaration = helper.match(/export type HelperErrorCode\s*=([\s\S]*?);/)[1];
  const codes = [...declaration.matchAll(/"([a-z0-9_]+)"/g)].map(match => match[1]);
  assert.ok(codes.length > 0);
  for (const code of codes) {
    await setup();
    await window.__JARVIS_NATIVE__.core.invoke('backend', { method: 'helper.text.complete',
      params: { id: 'owned-id', text: '', error: `[${code}] PRIVATE-CONTENT` } });
    assert.equal(window.__qaQueued.helperErrors[0].code, code);
    assert.doesNotMatch(window.__qaQueued.helperErrors[0].text, /PRIVATE/);
  }
});

test('wire records a bounded output limit and counts without prompt or credentials', async () => {
  const fixture = await setup({ model: 'owned', base: 'http://127.0.0.1:1/v1', deadline: performance.now() + 1000 });
  const body = { model: 'owned', max_tokens: 768, messages: [
    { role: 'system', content: 'private instructions' }, { role: 'user', content: 'QUEUE_APPROVAL_QA: private goal' },
  ] };
  await window.__JARVIS_NATIVE__.core.invoke('ai_http_stream', new TextEncoder().encode(JSON.stringify(body)), {
    headers: { 'x-terax-ai-request': JSON.stringify({ method: 'POST', url: 'http://127.0.0.1:1/v1/chat/completions', requestId: 'private-id', headers: { Authorization: 'private-key' } }) },
  });
  assert.equal(window.__qaQueued.wire[0].maxOutputTokens, 768);
  assert.equal(window.__qaQueued.wire[0].message_count, 2);
  assert.doesNotMatch(JSON.stringify(window.__qaQueued.wire), /private/);
  assert.equal(fixture.forwarded.length, 1);
});

for (const [label, plan] of [
  ['safe first step', { steps: [{ step: 1, tool: 'system_status', parameters: {} }, ...exact.steps] }],
  ['unexpected read', { steps: [{ ...exact.steps[0], tool: 'web_search', parameters: { query: 'unowned' } }] }],
  ['changed path', { steps: [{ ...exact.steps[0], parameters: { ...exact.steps[0].parameters, path: 'D:/other.txt' } }] }],
  ['extra argument', { steps: [{ ...exact.steps[0], parameters: { ...exact.steps[0].parameters, content: 'changed' } }] }],
  ['optional action', { steps: [{ ...exact.steps[0], critical: false }] }],
  ['schema discovery', { request_tools: ['file_controller'] }],
  ['plan with extra discovery', { ...exact, request_tools: ['web_search'] }],
  ['malformed JSON', null],
]) {
  test(`blocks ${label} before forwarding any plan to the backend`, async () => {
    const fixture = await setup();
    await assert.rejects(fixture.complete(plan === null ? 'invalid' : JSON.stringify(plan)), /exact single owned move/);
    assert.equal(fixture.forwarded.length, 1);
    assert.equal(fixture.forwarded[0].args.method, 'helper.text.complete');
    assert.equal(fixture.forwarded[0].args.params.text, '');
    assert.match(fixture.forwarded[0].args.params.error, /refused/);
    assert.equal(window.__qaQueued.blocked, 1);
  });
}
