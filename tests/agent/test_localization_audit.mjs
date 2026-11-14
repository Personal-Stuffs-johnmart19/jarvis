import test from 'node:test';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {auditSource, jsxText, scan} from '../../tools/audit_localization.mjs';
import {speechPreview} from '../../apps/desktop/src/speechPreview.ts';
import {isLocale, supportedLocales, translate} from '../../apps/desktop/src/localization.ts';

test('locale audit distinguishes visible text from protocol, class and enum props', () => {
  const {findings, calls} = auditSource(`
    const t = {id: 'tab'};
    const ui = <div className={active ? 'active' : 'inactive'} aria-live={busy ? 'polite' : 'off'}>
      Open file
      <button title="Close" variant={danger ? 'destructive' : 'default'}>{busy ? 'Working' : 'Ready'}</button>
      <span>{\`Branch: \${branch}\`}</span>
      <code>git status</code><span translate="no">example.com/repo</span>
      <span>{translateMessage('Translated')}</span>
    </div>;
    toast.error('Save failed');
  `, 'fixture.tsx');
  assert.deepEqual(calls, ['Translated']);
  assert.deepEqual(findings.filter(x => x.kind === 'attribute').map(x => x.message), ['Close']);
  assert.deepEqual(findings.filter(x => x.kind === 'conditional').map(x => x.message), [['Working', 'Ready']]);
  assert.equal(findings.filter(x => x.kind === 'template').length, 1);
  assert.deepEqual(findings.filter(x => x.kind === 'runtime-message').map(x => x.message), ['Save failed']);
  assert(!findings.some(x => ['active', 'polite', 'destructive', 'git status', 'example.com/repo'].includes(x.message)));
  assert.equal(jsxText('\n    Hello\n    world\n  '), 'Hello world');
  assert.equal(jsxText(' leading and trailing '), ' leading and trailing ');
});

test('all extracted static frontend messages have shared catalog entries and English source literals', () => {
  const result = scan(fileURLToPath(new URL("../../", import.meta.url)));
  assert.deepEqual(result.missingCatalogKeys, []);
  assert.equal(result.totals['non-English-literal'] ?? 0, 0);
  assert.equal(result.totals['jsx-text'] ?? 0, 0);
  assert.equal(result.totals.attribute ?? 0, 0);
  assert.equal(result.totals['runtime-message'] ?? 0, 0);
  // Descriptor/runtime candidates are deliberate remaining review work, not a false full-coverage pass.
});

test('speech samples follow synthesis language, preserve custom data and reject inherited keys', () => {
  assert.equal(speechPreview('en'), 'Hello! This is a voice preview.');
  assert.equal(speechPreview('ru'), 'Привет! Проверяем выбранный голос.');
  assert.equal(speechPreview('ru', 'User text /tmp/file'), 'User text /tmp/file');
  assert.equal(speechPreview('en', ''), '');
  assert.equal(speechPreview('unknown'), '');
  assert.equal(speechPreview('__proto__'), '');
});

test('locale registry and speech keys use the shared language catalog', () => {
  assert.deepEqual(supportedLocales, ['en', 'ru']);
  assert(isLocale('en') && isLocale('ru'));
  for (const key of ['__proto__', 'constructor', 'invalid']) assert.equal(isLocale(key), false);
  assert.equal(translate('ru', 'TTS synthesis model'), 'Модель синтеза речи');
  assert.equal(translate('ru', 'Speaker voice'), 'Голос диктора');
  assert.equal(translate('en', 'Quit Jarvis?'), 'Quit Jarvis?');
});

// Whole messages permit Russian count inflection and different word order.
test('count messages select full localized plural templates', async () => {
  const {translateCount} = await import('../../apps/desktop/src/localization.ts');
  for (const [count, expected] of [[1, '1 файл'], [2, '2 файла'], [5, '5 файлов'], [11, '11 файлов'], [21, '21 файл'], [0, '0 файлов']])
    assert.equal(translateCount('ru', '{0} files', count), expected);
  assert.equal(translateCount('en', '{0} files', 1), '1 file');
  assert.equal(translateCount('en', '{0} files', 2), '2 files');
  assert.equal(translateCount('en', 'Uncatalogued {0}', 3), 'Uncatalogued 3');
  assert.equal(translateCount('ru', 'constructor', 2), 'constructor');
});

test('missing localized plural entries use English plural rules for English fallback', async () => {
  const {localeDefinitions, translateCount} = await import('../../apps/desktop/src/localization.ts');
  const catalog = localeDefinitions.ru.plurals;
  const original = catalog['{0} files'];
  try {
    delete catalog['{0} files'];
    assert.equal(translateCount('ru', '{0} files', 21), '21 files');
  } finally { catalog['{0} files'] = original; }
});


test('backend-owned messages share catalogs and retain English user/provider data', async () => {
  const {translate} = await import('../../apps/desktop/src/localization.ts');
  assert.equal(translate('en', 'Microphone selected.'), 'Microphone selected.');
  assert.equal(translate('ru', 'Microphone selected.'), 'Микрофон выбран.');
  assert.equal(translate('ru', '/private/user-file'), '/private/user-file');
});

test('all count catalogs preserve placeholders and Russian inflection', async () => {
  const {readFileSync} = await import('node:fs');
  const en = JSON.parse(readFileSync(new URL('../../apps/desktop/src/locales/en.plurals.json', import.meta.url), 'utf8'));
  const ru = JSON.parse(readFileSync(new URL('../../apps/desktop/src/locales/ru.plurals.json', import.meta.url), 'utf8'));
  assert.deepEqual(Object.keys(en).sort(), Object.keys(ru).sort());
  for (const key of Object.keys(en)) for (const form of Object.values(ru[key]))
    assert.deepEqual([...form.matchAll(/\{\d+\}/g)].map(x => x[0]).sort(), [...key.matchAll(/\{\d+\}/g)].map(x => x[0]).sort());
  const {translateCount} = await import('../../apps/desktop/src/localization.ts');
  assert.equal(translateCount('ru', '{0} lines', 2), '2 строки');
  assert.equal(translateCount('ru', '{0} replacements', 5), '5 замен');
});

test('toast options audit catches nested actions and conditional descriptions but preserves external text', () => {
  const {findings} = auditSource(`toast.error(translateMessage('Failure'), {
    description: slow ? 'Taking too long' : 'Try later',
    action: ready ? {label: 'Retry', onClick: retry} : undefined, cancel: hidden ? undefined : {label: 'Skip', onClick: skip}
  }); toast.error(error.message, {description: String(error)});
  toast('x', {description: translateMessage('Ready'), action: {label: translateMessage('Open')}});`, 'fixture.ts');
  assert.deepEqual(findings.filter(x => x.kind === 'runtime-message').map(x => x.message), ['Taking too long', 'Try later', 'Retry', 'Skip', 'x']);
});
test('toast audit follows short-circuit description and action branches', () => {
  const {findings} = auditSource(`toast.info(error.message, {
    description: offline && 'Reconnect manually',
    action: ready && {label: 'Retry', onClick: retry},
    cancel: hidden || {label: 'Skip', onClick: skip}
  });`, 'fixture.ts');
  assert.deepEqual(findings.filter(x=>x.kind==='runtime-message').map(x=>x.message), ['Reconnect manually','Retry','Skip']);
});