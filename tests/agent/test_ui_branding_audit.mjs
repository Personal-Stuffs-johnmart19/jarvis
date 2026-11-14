import assert from 'node:assert/strict';
import test from 'node:test';
import { auditBranding, scanBranding } from '../../tools/audit_branding.mjs';
import { fileURLToPath } from 'node:url';

test('branding audit catches owned labels, translated text and image alternatives', () => {
  assert.deepEqual(auditBranding('const x = <img alt="Terax" title={t("About Kaizumi")} />;', 'view.tsx'), ['Terax', 'About Kaizumi']);
  assert.deepEqual(auditBranding('const x = <img alt="Tauri" />;', 'view.tsx'), ['Tauri']);
  assert.deepEqual(auditBranding('const x = <span translate="no">Terax</span>;', 'view.tsx'), ['Terax']);
  assert.deepEqual(auditBranding('const x = {label: "Zephium Browser"};', 'view.ts'), ['Zephium Browser']);
});
test('branding audit preserves compatibility keys and third-party identifiers', () => {
  assert.deepEqual(auditBranding('import { invoke } from "@tauri-apps/api/core"; const key = "terax-settings.json"; const event = "terax:open-file"; const x = <div className="terax-pane">Jarvis</div>;', 'view.tsx'), []);
});
test('owned frontend display text and companion labels use current branding', () => {
  assert.deepEqual(scanBranding(fileURLToPath(new URL("../../", import.meta.url))), []);
});