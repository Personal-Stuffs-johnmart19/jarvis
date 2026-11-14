import test from 'node:test';
import assert from 'node:assert/strict';
import { createLocaleSync } from '../../apps/desktop/src/localeSync.ts';

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test('rapid locale changes cannot overtake a pending backend write', async () => {
  const gate = deferred(), calls = [];
  let current = '';
  const sync = createLocaleSync(async locale => {
    calls.push(locale);
    if (calls.length === 1) await gate.promise;
    current = locale;
  });
  const first = sync('ru'), second = sync('en');
  await Promise.resolve();
  assert.deepEqual(calls, ['ru']);
  gate.resolve();
  await Promise.all([first, second]);
  assert.deepEqual(calls, ['ru', 'en']);
  assert.equal(current, 'en');
});

test('a failed locale write is reported without blocking the next choice', async () => {
  const gate = deferred(), calls = [];
  const sync = createLocaleSync(async locale => {
    calls.push(locale);
    if (calls.length === 1) await gate.promise;
  });
  const first = sync('ru'), second = sync('en');
  const failure = assert.rejects(first, /disconnected/);
  gate.reject(new Error('disconnected'));
  await failure;
  await second;
  assert.deepEqual(calls, ['ru', 'en']);
});
