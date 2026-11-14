import test from 'node:test';
import assert from 'node:assert/strict';
import { microphoneChoices } from '../../apps/desktop/src/microphoneChoices.ts';
import { translate } from '../../apps/desktop/src/localization.ts';

test('duplicate OS microphone identities survive English/Russian presentation and unavailable selections do not fall back', () => {
  const devices = [{id:2,name:'Микрофон (Realtek USB)'},{id:8,name:'Микрофон (Realtek USB)'},{id:11,name:'Brand Model X'}];
  const original = structuredClone(devices);
  for (const locale of ['en','ru','en']) {
    const choices = microphoneChoices(devices,'8',(message,...values)=>translate(locale,message,...values));
    assert.deepEqual(choices.map(choice=>choice.value),['2','8','11']);
    assert.notEqual(choices[0].label,choices[1].label);
    assert.ok(choices[0].label.includes(devices[0].name));
    assert.ok(choices[1].label.includes(devices[1].name));
    assert.equal(choices[2].label,'Brand Model X');
    assert.equal(choices.find(choice=>choice.value==='8').unavailable,false);
  }
  assert.deepEqual(devices,original);
  const missing = microphoneChoices(devices,'99',(message,...values)=>translate('en',message,...values));
  assert.equal(missing[0].value,'99');
  assert.equal(missing[0].unavailable,true);
  assert.match(missing[0].label,/unavailable.*99/);
  assert.equal(microphoneChoices(devices,'',(message)=>message).some(choice=>choice.unavailable),false);
});
