import test from 'node:test';
import assert from 'node:assert/strict';
import {synthesisChoices, synthesisId} from '../../apps/desktop/src/speechChoices.ts';
test('synthesis model groups preserve speakers and separate cloud model selection', () => {
  const voices = [{id:'supertonic:F1',name:'F1'}, {id:'supertonic:M2',name:'M2'}, {id:'gemini:auto',name:'auto'}, {id:'gemini:lite',name:'lite'}, {id:'sapi:registry:voice',name:'System voice'}];
  const choices = synthesisChoices(voices);
  assert.equal(choices.length, 4);
  assert.deepEqual(choices[0].voices, voices.slice(0,2));
  assert.equal(choices[1].cloud, true);
  assert.equal(choices[2].id, 'gemini:lite');
  assert.equal(choices[3].voices[0].id, 'sapi:registry:voice');
  assert.equal(synthesisId('sapi:registry:voice'), 'sapi');
});
test('synthesis choices never advertise unconfigured engines or mutate backend voices', () => {
  const voices = [{id:'silero:xenia',name:'Xenia'}];
  const choices = synthesisChoices(voices);
  assert.deepEqual(choices.map(item => item.id), ['silero']);
  assert.equal(choices[0].cloud, false);
  assert.deepEqual(synthesisChoices([]), []);
  assert.deepEqual(voices, [{id:'silero:xenia',name:'Xenia'}]);
});
