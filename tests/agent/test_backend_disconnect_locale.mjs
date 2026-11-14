import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
const source = stripTypeScriptTypes(readFileSync(new URL('../../apps/desktop/src/displayError.ts', import.meta.url), 'utf8'))
  .replace('from "./localization"', 'from ' + JSON.stringify(new URL('../../apps/desktop/src/localization.ts', import.meta.url).href));
const { displayError, errorText } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const main = readFileSync(new URL('../../apps/desktop/src/main.tsx', import.meta.url), 'utf8');
const native = readFileSync(new URL('../../apps/desktop/src-tauri/src/bridge.rs', import.meta.url), 'utf8');
const handler = main.match(/if \(event\.event === "backend\.error"\) setError\(displayError\(data\.text\)\);/)?.[0];
assert.ok(handler, 'use the production backend.error consumer');
const consume = new Function('event', 'data', 'setError', 'displayError', handler);

test('native disconnect payload reaches the shell error state with owned live English/Russian rendering', () => {
  const payloadSource = native.match(/fn backend_stopped_event\(\) -> Value \{\s*json!\(([^\n]+)\)/)?.[1];
  assert.ok(payloadSource);
  const event = JSON.parse(payloadSource);
  let error;
  consume(event, event.data, value => { error = value; }, displayError);
  assert.equal(errorText(error, 'en'), 'Python backend stopped. Check data/logs/desktop-backend.log.');
  assert.equal(errorText(error, 'ru'), 'Обработчик Python остановлен. Проверьте data/logs/desktop-backend.log.');
  assert.equal(errorText(error, 'en'), event.data.text.message);
  assert.ok(native.includes('sender.send(Err(backend_disconnected_error()))'));
  assert.ok(native.includes('handle.emit("backend-event", backend_stopped_event())'));
});

test('backend provider and OS diagnostic strings remain raw, including catalog collisions', () => {
  for (const raw of ['Python backend stopped. Check data/logs/desktop-backend.log.', 'Access denied: C:/raw/provider {0}']) {
    let error;
    consume({event: 'backend.error'}, {text: raw}, value => { error = value; }, displayError);
    assert.equal(errorText(error, 'ru'), raw);
  }
});
