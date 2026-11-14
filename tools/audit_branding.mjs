import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { auditSource } from './audit_localization.mjs';

// Inspect owned display text, not persistence keys, framework imports or terminal protocols.
const oldBrand = /\b(?:terax|kaizumi|zephium|tauri)\b/i;
export function auditBranding(source, filename) {
  const { findings, calls } = auditSource(source, filename, { includeNonTranslatable: true });
  return [...findings.filter(item => item.kind !== 'non-English-literal').map(item => item.message), ...calls]
    .flat().filter(text => oldBrand.test(text));
}
export function scanBranding(root) {
  const findings = [];
  const check = (file, values) => {
    for (const text of values) if (oldBrand.test(text)) findings.push({ file, text });
  };
  function walk(dir) {
    for (const item of fs.readdirSync(path.join(root, dir), { withFileTypes: true })) {
      const file = `${dir}/${item.name}`;
      if (item.isDirectory()) walk(file);
      else if (/\.tsx?$/.test(file) && !file.endsWith('.d.ts'))
        check(file, auditBranding(fs.readFileSync(path.join(root, file), 'utf8'), file));
      else if (file.endsWith('.json') && dir.endsWith('/locales'))
        check(file, Object.values(JSON.parse(fs.readFileSync(path.join(root, file), 'utf8'))));
    }
  }
  walk('apps/desktop/src');
  walk('apps/desktop/workbench/src');
  const manifest = 'integrations/companions.json';
  check(manifest, Object.values(JSON.parse(fs.readFileSync(path.join(root, manifest), 'utf8')).components).map(item => item.name));
  return findings;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const findings = scanBranding(fileURLToPath(new URL('..', import.meta.url)));
  console.log(JSON.stringify({ findings }, null, 2));
  process.exitCode = findings.length ? 1 : 0;
}