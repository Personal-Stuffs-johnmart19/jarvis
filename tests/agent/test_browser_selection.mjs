import test from 'node:test';
import assert from 'node:assert/strict';
import { browserSelection } from '../../apps/desktop/src/browserSelection.ts';
const profiles = [{id:'Default',name:'First'}, {id:'Profile 2',name:'Second'}];
test('empty or stale current profile resolves to a real selectable identity', () => {
  for (const current of ['', 'Profile 99']) assert.deepEqual(browserSelection({mode:'current',profile:''}, profiles, current), {mode:'current',profile:'Default'});
  assert.equal(browserSelection({mode:'current',profile:''},profiles,'Profile 2').profile,'Profile 2');
  assert.equal(browserSelection({mode:'current',profile:''},[],'Default').profile,'');
});
test('explicit unavailable selection cannot silently receive another profile token', () => {
  const saved = {mode:'specific',profile:'Profile 99'};
  assert.deepEqual(browserSelection(saved,profiles,'Default'),saved);
  assert.equal(browserSelection({mode:'new',profile:'Default'},profiles,'Default').profile,'');
});
