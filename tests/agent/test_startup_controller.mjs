import assert from 'node:assert/strict';
import test from 'node:test';
import { createStartupController, createStartupService } from '../../apps/desktop/workbench/src/settings/startupController.ts';
import { translate } from '../../apps/desktop/src/localization.ts';
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise,resolve}; };
function fixture(overrides = {}) {
  const states = [], changes = [], saves = [];
  const io = {read: async () => false, change: async next => { changes.push(next); }, persist: async next => { saves.push(next); }, ...overrides};
  const controller = createStartupController(io, state => states.push(state));
  return {controller, states, changes, saves, last: () => states.at(-1)};
}
test('startup read failures are visible, do not save and can recover', async () => {
  let fails = true;
  const f = fixture({read: async () => { if(fails) throw 'RAW startup {0} ошибка'; return true; }});
  await f.controller.refresh();
  assert.equal(f.last().enabled, null); assert.equal(f.last().busy, false);
  assert.deepEqual(f.saves, []);
  assert.equal(f.last().error.message, 'Could not read the startup setting: {0}');
  assert.deepEqual(f.last().error.values, ['RAW startup {0} ошибка']);
  fails=false; await f.controller.refresh();
  assert.equal(f.last().enabled,true); assert.equal(f.last().error,null); assert.deepEqual(f.saves,[true]);
});
test('failed native changes retain confirmed state and skip preference writes', async () => {
  const f = fixture({change: async () => { throw 'RAW native failure'; }});
  await f.controller.refresh(); f.saves.length=0;
  await f.controller.setEnabled(true);
  assert.equal(f.last().enabled,false); assert.equal(f.last().busy,false);
  assert.deepEqual(f.saves,[]); assert.equal(f.last().error.message,'Could not change the startup setting: {0}');
});
test('preference synchronization failure preserves the successful system change', async () => {
  let fails=false;
  const f=fixture({persist: async () => {if(fails) throw 'RAW persistence failure';}});
  await f.controller.refresh(); fails=true;
  await f.controller.setEnabled(true);
  assert.deepEqual(f.changes,[true]); assert.equal(f.last().enabled,true);
  assert.equal(f.last().error.message,'Could not synchronize the startup preference: {0}');
  assert.equal(f.last().busy,false);
});
test('startup operations stay serialized through initialization and synchronization', async () => {
  const read=deferred(), save=deferred(); let reads=0, changes=0;
  const f=fixture({read:async()=>{reads++;return read.promise;},change:async()=>{changes++;},persist:async()=>save.promise});
  const first=f.controller.refresh(); await f.controller.refresh(); await f.controller.setEnabled(true);
  assert.equal(reads,1); assert.equal(changes,0); assert.equal(f.last().busy,true);
  read.resolve(false); await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.last().enabled,false); assert.equal(f.last().busy,true);
  await f.controller.setEnabled(true); assert.equal(changes,0);
  save.resolve(); await first; assert.equal(f.last().busy,false);
});
test('startup failure prefixes translate without altering raw native details', async () => {
  const f=fixture({read:async()=>{throw 'Launch at login {0} ошибка';}});
  await f.controller.refresh(); const error=f.last().error;
  assert.equal(translate('en',error.message,...error.values),'Could not read the startup setting: Launch at login {0} ошибка');
  assert.equal(translate('ru',error.message,...error.values),'Не удалось прочитать параметр автозапуска: Launch at login {0} ошибка');
});
test('host startup state survives subscriber replacement during a pending change', async () => {
  let enabled=false, reads=0, changes=0;
  const pending=deferred();
  const service=createStartupService({read:async()=>{reads++;return enabled;},change:async next=>{changes++;await pending.promise;enabled=next;},persist:async()=>{}});
  const first=[]; const unsubscribe=service.subscribe(()=>first.push(service.getSnapshot()));
  await service.refresh();
  const change=service.setEnabled(true);
  unsubscribe();
  const second=[]; service.subscribe(()=>second.push(service.getSnapshot()));
  assert.equal(service.getSnapshot().busy,true);
  await service.refresh(); assert.equal(reads,1);
  pending.resolve(); await change;
  assert.equal(service.getSnapshot().enabled,true); assert.equal(service.getSnapshot().busy,false);
  assert.equal(second.at(-1).enabled,true); assert.equal(changes,1);
  assert.equal(first.at(-1).enabled,false);
});
