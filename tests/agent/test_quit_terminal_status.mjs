import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
const source = readFileSync(new URL('../../apps/desktop/workbench/src/modules/terminal/ghostty/useGhosttyTerminalSession.ts', import.meta.url), 'utf8');
const start = source.indexOf('export async function ghosttyLeafHasForegroundProcess(');
const end = source.indexOf('export function disposeGhosttySession', start);
const executable = stripTypeScriptTypes(source.slice(start, end).replace('export ', ''));
function create(sessions, invoke) {
  return new Function('sessions', 'invoke', `${executable}; return ghosttyLeafHasForegroundProcess;`)(sessions, invoke);
}
test('strict Quit treats exited or never-mounted terminals as idle and rejects live uninitialized sessions', async () => {
  const check = create(new Map([[1, {shellExited:true, pty:null}], [2, {shellExited:false, pty:null}]]), async () => { throw new Error('must not query'); });
  assert.equal(await check(1, true), false);
  await assert.rejects(check(2, true), /Terminal state is unavailable/);
  assert.equal(await check(3, true), false);
  assert.equal(await check(2), false);
});
test('strict Quit propagates query failure and preserves true busy status', async () => {
  const sessions = new Map([[1, {shellExited:false, pty:{id:7}}]]);
  const check = create(sessions, async () => { throw new Error('IPC failed'); });
  await assert.rejects(check(1, true), /IPC failed/);
  assert.equal(await check(1), false);
  assert.equal(await create(sessions, async () => true)(1, true), true);
});
