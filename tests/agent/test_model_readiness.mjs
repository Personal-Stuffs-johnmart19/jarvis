import test from 'node:test';
import assert from 'node:assert/strict';
import { getConfiguredChatModels, isModelSelectionConfigured, lmstudioOptionId, ollamaOptionId } from '../../apps/desktop/workbench/src/modules/ai/config.ts';

const lm = key => ({ key, type: 'llm', display_name: key, loaded_instances: [] });
test('readiness LM discovery excludes configured but absent exact identities', () => {
  const state = { apiKeys: {}, lmstudioBaseURL: 'http://127.0.0.1:1234/v1', lmstudioModelId: 'removed', lmstudioCatalog: [lm('installed')], lmstudioDetectedIds: [] };
  assert.deepEqual(getConfiguredChatModels(state).map(row => row.id), [lmstudioOptionId('installed')]);
  assert.equal(isModelSelectionConfigured(lmstudioOptionId('removed'), state), false);
  assert.equal(isModelSelectionConfigured('lmstudio-local', state), false);
  assert.equal(isModelSelectionConfigured(lmstudioOptionId('installed'), state), true);
  assert.equal(state.lmstudioModelId, 'removed', 'readiness never rewrites user intent');
});
test('readiness authoritative LM empty cannot become configured-only fallback', () => {
  const legacy = { apiKeys: {}, lmstudioModelId: 'saved', lmstudioBaseURL: 'http://127.0.0.1:1234/v1' };
  assert.equal(isModelSelectionConfigured('lmstudio-local', legacy), true);
  for (const observed of [{lmstudioCatalog: []}, {lmstudioDetectedIds: []}, {lmstudioCatalog: [], lmstudioDetectedIds: []}]) {
    const state = {...legacy, ...observed};
    assert.deepEqual(getConfiguredChatModels(state), []);
    assert.equal(isModelSelectionConfigured('lmstudio-local', state), false);
    assert.equal(isModelSelectionConfigured(lmstudioOptionId('saved'), state), false);
  }
});
test('readiness cloud key revocation and local unloaded transitions preserve exact selection intent', () => {
  const original = { apiKeys: {openai:'disposable-test-value'}, lmstudioCatalog: [lm('installed')], ollamaCatalog: [{id:'chat', loaded:true, context_length:8192}] };
  assert.equal(isModelSelectionConfigured('gpt-5.6', original), true);
  const revoked = {...original, apiKeys:{}, ollamaCatalog:[{id:'chat', loaded:false, context_length:null}]};
  assert.equal(isModelSelectionConfigured('gpt-5.6', revoked), false);
  assert.equal(isModelSelectionConfigured(lmstudioOptionId('installed'), revoked), true);
  assert.equal(isModelSelectionConfigured(ollamaOptionId('chat'), revoked), true);
  assert.equal(isModelSelectionConfigured(ollamaOptionId('deleted'), revoked), false);
});
