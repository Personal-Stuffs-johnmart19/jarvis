import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
const source = stripTypeScriptTypes(readFileSync(new URL('../../apps/desktop/src/displayError.ts',import.meta.url),'utf8'))
  .replace('from "./localization"', 'from '+JSON.stringify(new URL('../../apps/desktop/src/localization.ts',import.meta.url).href));
const { displayError, errorText, isOwnedError } = await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));

test('only explicitly owned errors are translated; provider key collisions stay raw', () => {
  assert.equal(errorText(displayError('Failed'),'ru'),'Failed');
  const error={source:'jarvis',message:'Local embeddings returned HTTP {0}. Split input above 8192 tokens; see server.log.',values:[413]};
  assert.ok(isOwnedError(error));
  assert.match(errorText(displayError(error),'ru'),/Локальный embedding вернул HTTP 413/);
  assert.match(errorText(displayError(error),'en'),/Local embeddings returned HTTP 413/);
  assert.equal(errorText(displayError({source:'external',message:'Failed',values:[]}),'ru'),'[object Object]');
  assert.equal(isOwnedError({source:'jarvis',message:'x',values:[{}]}),false);
  assert.equal(isOwnedError({source:'jarvis',message:'x',values:[Infinity]}),false);
});


test('owned speech status localizes while raw status and diagnostics stay verbatim', () => {
  const status = {source:'jarvis',message:'Vulkan check failed: {0} Using Silero CPU.',values:['raw provider detail']};
  assert.match(errorText(displayError(status),'ru'), /Проверка Vulkan не пройдена: raw provider detail/);
  assert.match(errorText(displayError(status),'en'), /Vulkan check failed: raw provider detail/);
  assert.equal(errorText(displayError('Failed'),'ru'),'Failed');
});
