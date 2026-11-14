import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { registerHooks, stripTypeScriptTypes } from 'node:module';

const hook = registerHooks({ resolve(specifier, context, next) {
  if (['./displayError', './localization'].includes(specifier) && context.parentURL?.endsWith('.ts'))
    return next(specifier + '.ts', context);
  return next(specifier, context);
} });
const { applySettingsAction } = await import('../../apps/desktop/src/settingsFeedback.ts');
const { displayError, errorText } = await import('../../apps/desktop/src/displayError.ts');
hook.deregister();

// Exercise the actual shell wrapper: its default swallow behavior caused the regression.
const main = readFileSync(new URL('../../apps/desktop/src/main.tsx', import.meta.url), 'utf8');
const wrapper = main.slice(main.indexOf('  const run = async'), main.indexOf('  const refresh = async'));
const createRun = new Function('rpc', 'setError', 'displayError', stripTypeScriptTypes(wrapper) + '; return run;');
const params = { voice: 'qwen3:VoiceDesign', preview: true };

test('preview rejection reaches the settings status with owned details and live English/Russian rendering', async () => {
  const detail = 'raw engine detail: C:\\models\\voice {0}';
  const failure = { source: 'jarvis', message: 'Speech synthesis failed: {0}', values: [detail] };
  const errors = [], statuses = [];
  const run = createRun(async (method, args) => {
    assert.equal(method, 'settings.voice');
    assert.equal(args, params);
    throw failure;
  }, error => errors.push(error), displayError);
  assert.equal(await applySettingsAction(run, 'settings.voice', params, status => statuses.push(status)), undefined);
  assert.equal(statuses.at(-1), failure);
  assert.equal(errorText(statuses.at(-1), 'en'), `Speech synthesis failed: ${detail}`);
  assert.equal(errorText(statuses.at(-1), 'ru'), `Ошибка озвучки: ${detail}`);
  assert.ok(errorText(statuses.at(-1), 'ru').endsWith(detail));
  assert.equal(errors.at(-1), failure);
  // Existing callers still receive undefined and global error feedback.
  assert.equal(await run('settings.voice', params), undefined);
});

test('raw diagnostics stay unchanged and failed results do not masquerade as success', async () => {
  for (const failure of ['Failed', new Error('provider error: Failed')]) {
    let status;
    await applySettingsAction(async () => { throw failure; }, 'settings.voice', params, value => { status = value; });
    assert.equal(errorText(status, 'ru'), failure instanceof Error ? failure.message : failure);
  }
  let status;
  const failure = { source: 'jarvis', message: 'Speech synthesis failed: {0}', values: ['raw'] };
  assert.equal(await applySettingsAction(async () => ({ok: false, error: failure}), 'settings.voice', params, value => { status = value; }), undefined);
  assert.equal(status, failure);
  const result = { ok: true };
  assert.equal(await applySettingsAction(async () => result, 'settings.voice', params, value => { status = value; }), result);
  assert.equal(errorText(status, 'ru'), 'Готово');
});

test('successful settings details retain owned provenance after JSON and follow live locale changes', async () => {
  const messages = [
    ['Speech settings saved. The next response will use the selected voice.', 'Настройки озвучки сохранены. Следующий ответ использует выбранный голос.'],
    ['Microphone selected.', 'Микрофон выбран.'],
    ['The key is protected by Windows DPAPI.', 'Ключ защищён Windows DPAPI.'],
  ];
  for (const [message, russian] of messages) {
    const result = JSON.parse(JSON.stringify({ok: true, message: {source: 'jarvis', message, values: []}}));
    let status;
    assert.equal(await applySettingsAction(async () => result, 'settings.voice', params, value => { status = value; }), result);
    assert.equal(errorText(status, 'ru'), russian);
    assert.equal(errorText(status, 'en'), message);
  }
  // A provider can return the same text as a catalog key; that does not imply ownership.
  let status;
  const message = 'Microphone selected.';
  await applySettingsAction(async () => JSON.parse(JSON.stringify({ok: true, message})), 'settings.voice', params, value => { status = value; });
  assert.equal(errorText(status, 'ru'), message);
});

test('network failure message reaches Settings and renders in the live locale without rewriting diagnostics', async () => {
  const raw = 'Microphone selected. {0} provider/raw';
  const message = { source: 'jarvis', message: 'Gemini check: {0}', values: [raw] };
  const result = JSON.parse(JSON.stringify({ok: false, message, error_kind: 'api'}));
  let status;
  assert.equal(await applySettingsAction(async () => result, 'network.check', {}, value => { status = value; }), undefined);
  assert.deepEqual(status, message);
  assert.equal(errorText(status, 'en'), `Gemini check: ${raw}`);
  assert.equal(errorText(status, 'ru'), `Проверка Gemini: ${raw}`);
  assert.equal(errorText(status, 'en'), `Gemini check: ${raw}`);
  await applySettingsAction(async () => ({ok: false, message: 'Microphone selected.'}), 'network.check', {}, value => { status = value; });
  assert.equal(errorText(status, 'ru'), 'Microphone selected.');
});
