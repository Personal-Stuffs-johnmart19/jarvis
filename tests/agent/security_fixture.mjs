import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import vm from 'node:vm';

// Execute the production guards with their actual JSON policy. Vite handles the
// ordinary JSON import in the app; Node's TypeScript runner needs this binding.
const base = new URL('../../apps/desktop/workbench/src/modules/ai/lib/', import.meta.url);
const pathPolicy = JSON.parse(readFileSync(new URL('pathPolicy.json', base), 'utf8'));
let source = stripTypeScriptTypes(readFileSync(new URL('security.ts', base), 'utf8'));
const names = [...source.matchAll(/^export (?:async )?function (\w+)/gm)].map(match => match[1]);
source = source.replace(/^import pathPolicy from "\.\/pathPolicy\.json";\r?$/m, '')
  .replace(/^export (?=(?:async )?function )/gm, '');
const exports = {};
vm.runInThisContext('(function(pathPolicy,exports){' + source + '\nObject.assign(exports,{' + names.join(',') + '});})')(pathPolicy, exports);
export const { checkReadable, checkWritable, checkReadableCanonical, checkWritableCanonical, checkShellCommand } = exports;
