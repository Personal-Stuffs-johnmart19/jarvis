import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const modelSettings = read('../../apps/desktop/workbench/src/settings/sections/ModelsSection.tsx');
const embeddingSettings = read('../../apps/desktop/src/EmbeddingPanel.tsx');
const labels = [
  ['Vulkan — GPU required (no CPU fallback)', 'Vulkan — требуется ГП (без перехода на ЦП)'],
  ['Local — Vulkan GPU required (no CPU fallback)', 'Локально — требуется ГП с Vulkan (без перехода на ЦП)'],
  ['Google API → Local Vulkan GPU fallback (no CPU fallback)', 'Google API → резервный локальный ГП с Vulkan (без перехода на ЦП)'],
];

test('active Vulkan chat and embedding options describe required GPU without promising CPU fallback', () => {
  assert.match(modelSettings, /<option value="vulkan">\{t\("Vulkan — GPU required \(no CPU fallback\)"\)\}<\/option>/);
  assert.match(embeddingSettings, /<option value="local">\s*\{t\("Local — Vulkan GPU required \(no CPU fallback\)"\)\}/);
  assert.match(embeddingSettings, /<option value="auto">[\s\S]*?\{t\("Google API → Local Vulkan GPU fallback \(no CPU fallback\)"\)\}/);
  for (const stale of ['Vulkan with CPU fallback', 'Local — Vulkan automatically / CPU fallback', 'Google API → Local Vulkan / CPU on failure']) {
    assert.ok(!modelSettings.includes(`t("${stale}")`) && !embeddingSettings.includes(`t("${stale}")`));
  }
  assert.match(modelSettings, /<option value="cpu">\{translateMessage\("CPU"\)\}/);
  assert.match(embeddingSettings, /<option value="cpu"> \{t\("Local — CPU only"\)\}/);
});

test('both catalog pairs retain explicit GPU/no-CPU wording for the active labels', () => {
  for (const base of ['../../local_mode/locales/', '../../apps/desktop/src/locales/']) {
    const en = JSON.parse(read(base + 'en.json'));
    const ru = JSON.parse(read(base + 'ru.json'));
    for (const [english, russian] of labels) {
      assert.equal(en[english], english);
      assert.equal(ru[english], russian);
    }
  }
});
