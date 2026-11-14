import test from 'node:test';
import assert from 'node:assert/strict';
import { readProjectInstructions } from '../../apps/desktop/workbench/src/modules/ai/lib/projectInstructions.ts';
import { displayAgent } from '../../apps/desktop/workbench/src/modules/agents/lib/format.ts';
import { refersToEditor } from '../../apps/desktop/workbench/src/modules/ai/lib/editorContext.ts';

test('Jarvis agent identity also renders old activity records with current branding', () => {
  for (const name of ['terax', 'Terax', 'jarvis', 'Jarvis']) assert.equal(displayAgent(name), 'Jarvis');
  assert.equal(displayAgent('claude'), 'Claude Code');
});

test('project instructions prefer JARVIS.md and preserve existing workspace content', async () => {
  const calls = [];
  const content = 'User rules {0} — правила';
  assert.equal(await readProjectInstructions('/workspace/', async path => {
    calls.push(path); return {kind:'text', content};
  }), content);
  assert.deepEqual(calls, ['/workspace/JARVIS.md']);
  assert.equal(await readProjectInstructions('/workspace', async path => {
    if (path.endsWith('/JARVIS.md')) throw new Error('not found');
    return {kind:'text', content:'Legacy user instructions'};
  }), 'Legacy user instructions');
});

test('instruction reads are bounded and absent workspaces perform no reads', async () => {
  assert.equal((await readProjectInstructions('/workspace', async () => ({kind:'text',content:'x'.repeat(40000)}))).length, 32768);
  assert.equal(await readProjectInstructions(null, async () => {throw new Error('must not read');}), null);
  assert.equal(await readProjectInstructions('/workspace', async () => ({kind:'binary'})), null);
});

test('the open-file quick action uses the shared editor-context path in both languages', () => {
  assert.equal(refersToEditor('Explain the file open in the editor.'), true);
  assert.equal(refersToEditor('Объясни файл, открытый в редакторе.'), true);
});

import { findThemeFile, removeThemeFiles } from '../../apps/desktop/workbench/src/modules/theme/themeFileCompatibility.ts';

test('theme file editing reuses a legacy file and prefers a current file when both exist', async () => {
  const paths = ['/themes/user.jarvis-theme', '/themes/user.terax-theme'];
  const existing = new Set([paths[1]]);
  assert.equal(await findThemeFile(paths, async path => existing.has(path)), paths[1]);
  existing.add(paths[0]);
  assert.equal(await findThemeFile(paths, async path => existing.has(path)), paths[0]);
  existing.clear();
  assert.equal(await findThemeFile(paths, async path => existing.has(path)), paths[0]);
});

test('theme file deletion removes both recognized file variants', async () => {
  const paths = ['/themes/user.jarvis-theme', '/themes/user.terax-theme'];
  const existing = new Set(paths);
  await removeThemeFiles(paths, async path => existing.has(path), async path => existing.delete(path));
  assert.equal(existing.size, 0);
});

test('theme file deletion reports a failed removal instead of claiming success', async () => {
  await assert.rejects(removeThemeFiles(['/new', '/old'], async () => true, async () => {throw new Error('access denied');}), /access denied/);
});

import { probeThemeFile } from '../../apps/desktop/workbench/src/modules/theme/themeFileCompatibility.ts';

test('theme probe recognizes only native not-found errors across supported platforms', async () => {
  assert.equal(await probeThemeFile(async () => {throw 'Файл не найден. (os error 2)';}, true), false);
  assert.equal(await probeThemeFile(async () => {throw 'Path not found. (os error 3)';}, true), false);
  await assert.rejects(probeThemeFile(async () => {throw new Error('No such process (os error 3)');}, false), /No such process/);
  await assert.rejects(probeThemeFile(async () => {throw new Error('Access denied (os error 5)');}, true), /Access denied/);
});

test('theme deletion propagates a failed existence check without removing files', async () => {
  const removed = [];
  await assert.rejects(removeThemeFiles(['/new', '/old'], () => probeThemeFile(async () => {throw new Error('backend disconnected');}, true), async path => removed.push(path)), /backend disconnected/);
  assert.deepEqual(removed, []);
});
