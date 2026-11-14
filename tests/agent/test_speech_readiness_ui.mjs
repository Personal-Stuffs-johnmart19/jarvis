import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {synthesisChoices,synthesisId} from '../../apps/desktop/src/speechChoices.ts';

const source=readFileSync(new URL('../../apps/desktop/src/SettingsPanel.tsx',import.meta.url),'utf8');

test('revoked-key speech UI contract preserves saved identity and disables unavailable Save/Listen',()=>{
  const saved='gemini:auto';
  const configured=[{id:saved,name:'Gemini TTS — Automatic'},{id:'silero:xenia',name:'Silero — Xenia'}];
  assert.ok(synthesisChoices(configured).some(model=>model.id===synthesisId(saved)));
  const available=configured.filter(voice=>!voice.id.startsWith('gemini:'));
  const choices=synthesisChoices(available);
  assert.equal(choices.some(model=>model.id===synthesisId(saved)),false);
  assert.deepEqual(choices.map(model=>model.id),['silero']);
  assert.equal(configured[0].id,saved);
  // This is an explicit source-wiring contract, not rendered/browser evidence.
  const disabled=[...source.matchAll(/disabled=\{([^\n]+)\}/g)].map(match=>match[1]);
  assert.equal(disabled.filter(expression=>expression.startsWith('applying || !synthesisModel ||')).length,2,'Both Save and Listen must refuse the missing saved model');
  assert.match(source,/voice && !synthesisModel && <p role="status">/);
  assert.match(source,/Your saved voice has not been changed/);
  assert.match(source,/\[tab, snapshot\?\.key_configured\]/,'Credential revocation must refresh advertised voices');
  assert.match(source,/onClick=\{\(\) => setTab\("Integrations"\)\}/);
});
