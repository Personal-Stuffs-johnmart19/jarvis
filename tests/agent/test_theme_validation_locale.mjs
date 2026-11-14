import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createRequire, registerHooks, stripTypeScriptTypes } from 'node:module';
import { validateTheme } from '../../apps/desktop/workbench/src/modules/theme/validateTheme.ts';

const hook = registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier === './localization' && context.parentURL?.endsWith('/displayError.ts')) return nextResolve('./localization.ts', context);
  return nextResolve(specifier, context);
} });
const { displayError, errorText } = await import('../../apps/desktop/src/displayError.ts');
hook.deregister();
const require = createRequire(new URL('../../tools/dev/package.json', import.meta.url));
const { parse } = require('@babel/parser');
const source = fs.readFileSync(new URL('../../apps/desktop/workbench/src/settings/sections/ThemesSection.tsx', import.meta.url), 'utf8');
function handler(name, context) {
  let found;
  function visit(node) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'VariableDeclarator' && node.id.name === name) found = node.init;
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === 'object') visit(value);
    }
  }
  visit(parse(source, { sourceType: 'module', plugins: ['typescript', 'jsx'] }));
  assert.ok(found, `Production handler ${name}`);
  return vm.runInNewContext(stripTypeScriptTypes(`(${source.slice(found.start, found.end)})`), context);
}
const base = () => ({ id: 'fixture-theme', name: 'Fixture', variants: { dark: {} } });

test('owned theme validation retains legacy English diagnostics and localizes only its structured issue', () => {
  const cases = [
    [null, 'Theme must be a JSON object'],
    [{ ...base(), id: '../bad' }, 'id must be a kebab-case string (a-z, 0-9, -)'],
    [{ ...base(), name: '' }, 'name must be a non-empty string'],
    [{ ...base(), variants: {} }, 'variants must contain at least one of: light, dark'],
    [{ ...base(), variants: [] }, 'variants must be an object'],
    [{ ...base(), variants: { dark: { colors: [] } } }, 'variants.dark.colors must be an object'],
    [{ ...base(), variants: { dark: { colors: { 'literal {0}': 'x' } } } }, 'variants.dark.colors.literal {0} is not a recognized color key'],
    [{ ...base(), variants: { dark: { colors: { primary: '' } } } }, 'variants.dark.colors.primary must be a non-empty string'],
    [{ ...base(), variants: { dark: { terminal: { cursor: 3 } } } }, 'variants.dark.terminal.cursor must be a string'],
    [{ ...base(), variants: { dark: { terminal: { fontWeight: 'wrong' } } } }, 'variants.dark.terminal.fontWeight must be normal, bold, or a weight from 100 to 900'],
    [{ ...base(), variants: { dark: { terminal: { fontSize: 100 } } } }, 'variants.dark.terminal.fontSize must be an integer from 8 to 32'],
    [{ ...base(), variants: { dark: { terminal: { ansi: [] } } } }, 'variants.dark.terminal.ansi must be an array of 16 strings'],
  ];
  for (const [input, english] of cases) {
    const result = validateTheme(input);
    assert.equal(result.ok, false);
    assert.equal(result.error, english);
    assert.equal(errorText(result.issue, 'en'), english);
    assert.notEqual(errorText(result.issue, 'ru'), english);
    assert.equal(errorText(result.error, 'ru'), english, 'Legacy raw diagnostic must remain literal');
  }
  const unknown = validateTheme(cases[6][0]);
  assert.ok(errorText(unknown.issue, 'ru').includes('variants.dark.colors.literal {0}'));
  assert.deepEqual(validateTheme(base()), { ok: true, theme: { ...base(), variants: { dark: { colors: {}, terminal: {} } } } });
});

test('production theme import handler separates owned validation from file/JSON/store diagnostics', async () => {
  let state;
  const apply = handler('handleThemeFiles', {
    validateTheme, displayError, setImportError: value => { state = value; },
    saveCustomTheme: async () => {}, setThemeId: () => { throw new Error('Invalid theme applied'); },
  });
  await apply([{ name: 'literal {0}.json', text: async () => JSON.stringify(null) }]);
  assert.equal(state.name, 'literal {0}.json');
  assert.equal(state.error.source, 'jarvis');
  assert.notEqual(errorText(state.error, 'ru'), errorText(state.error, 'en'));
  const raw = 'Theme must be a JSON object';
  await apply([{ name: 'raw.json', text: async () => { throw raw; } }]);
  assert.equal(errorText(state.error, 'ru'), raw);
  await apply([{ name: 'broken.json', text: async () => '{invalid json' }]);
  assert.equal(typeof state.error, 'string');
  assert.equal(errorText(state.error, 'ru'), state.error);
  const storeFailure = handler('handleThemeFiles', {
    validateTheme, displayError, setImportError: value => { state = value; },
    saveCustomTheme: async () => { throw raw; }, setThemeId: () => { throw new Error('Failed store applied'); },
  });
  await storeFailure([{ name: 'raw.json', text: async () => JSON.stringify(base()) }]);
  assert.equal(errorText(state.error, 'ru'), raw);
});

test('production background import keeps filename data and raw runtime failures separate', async () => {
  let state;
  const apply = handler('handleBgFiles', {
    displayError, setBgError: value => { state = value; }, backgroundImageId: null,
    importBgImageFromFile: async () => { throw 'Theme must be a JSON object'; },
  });
  await apply([{ name: 'literal {0}.txt', type: 'text/plain' }]);
  assert.equal(state.source, 'jarvis');
  assert.ok(errorText(state, 'ru').includes('literal {0}.txt'));
  assert.notEqual(errorText(state, 'en'), errorText(state, 'ru'));
  await apply([{ name: 'image.png', type: 'image/png' }]);
  assert.equal(errorText(state, 'ru'), 'Theme must be a JSON object');
});
