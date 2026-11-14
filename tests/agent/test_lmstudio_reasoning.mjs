import test from 'node:test';
import assert from 'node:assert/strict';
import {lmstudioReasoningEffort} from '../../apps/desktop/workbench/src/modules/ai/lib/lmstudioReasoning.ts';

test('GPT-OSS uses its lowest supported effort for an existing off preference',()=>{
  for(const id of ['gpt-oss-20b','openai/gpt-oss-120b','gpt-oss-20b-MXFP4',' gpt-oss-20b ']) {
    assert.equal(lmstudioReasoningEffort(id,'off'),'low');
    assert.equal(lmstudioReasoningEffort(id,'low'),'low');
    assert.equal(lmstudioReasoningEffort(id,'medium'),'medium');
    assert.equal(lmstudioReasoningEffort(id,'high'),'high');
    assert.equal(lmstudioReasoningEffort(id,'on'),'high');
    assert.equal(lmstudioReasoningEffort(id,'default'),undefined);
  }
});

test('unrelated models retain their configured mode without guessed capabilities',()=>{
  for(const id of ['qwen/qwen3.5-9b','my-custom-instance','gpt-oss-20b-unrelated/model','other/gpt-oss-20b']) {
    // Publisher-qualified aliases are not resolved by basename.
    assert.equal(lmstudioReasoningEffort(id,'off'),'none');
    assert.equal(lmstudioReasoningEffort(id,'default'),undefined);
  }
});
