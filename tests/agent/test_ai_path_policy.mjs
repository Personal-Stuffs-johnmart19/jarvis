import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { checkReadable, checkReadableCanonical } from './security_fixture.mjs';
import { readProjectInstructions } from '../../apps/desktop/workbench/src/modules/ai/lib/projectInstructions.ts';

test('shared read policy blocks Jarvis credential files and Windows suffix aliases', () => {
  for (const name of ['bootstrap-key.json', 'api_keys.json', 'store.dpapi']) {
    for (const suffix of ['', '.', ' ', '.bak', ':stream', '::$DATA']) {
      for (const path of [`D:/owned/${name}${suffix}`, `D:\\owned\\${name.toUpperCase()}${suffix}`]) {
        assert.equal(checkReadable(path).ok, false, path);
      }
    }
  }
  for (const name of ['bootstrap-key.txt', 'api_keys.txt', 'store.json', 'README.md']) {
    assert.equal(checkReadable(`D:/owned/${name}`).ok, true, name);
  }
});

test('canonical targets cannot alias a Jarvis credential file', async () => {
  for (const name of ['bootstrap-key.json', 'api_keys.json', 'store.dpapi']) {
    assert.equal((await checkReadableCanonical('D:/owned/readme.txt', async () => `D:/owned/${name}`)).ok, false);
  }
});

function memoryReader(bindings) {
  const source = readFileSync(new URL('../../apps/desktop/workbench/src/modules/ai/lib/transport.ts', import.meta.url), 'utf8');
  const body = source.slice(source.indexOf('export async function readProjectMemory('), source.indexOf('type LiveSnapshot =')).replace('export ', '');
  return new Function(...Object.keys(bindings), stripTypeScriptTypes(body) + ';return readProjectMemory;')(...Object.values(bindings));
}

test('project memory reads stay bound to the captured workspace and never reuse stale root cache', async () => {
  let workspace = { kind: 'local' };
  let content = 'first rules';
  const calls = [];
  const read = memoryReader({ readProjectInstructions, checkReadableCanonical, currentWorkspaceEnv: () => workspace,
    native: {
      canonicalize: async (path, captured) => { workspace = { kind: 'wsl', distro: 'other' }; calls.push(['canonical', path, captured]); return path; },
      readProjectMemory: async (root, path, captured) => { calls.push(['read', root, path, captured]); return content; },
    },
  });
  assert.equal(await read('D:/owned'), 'first rules');
  assert.deepEqual(calls[0][2], { kind: 'local' });
  assert.equal(calls[1][3], calls[0][2]);
  assert.equal(calls[1][1], 'D:/owned');
  assert.equal(calls[1][2], 'D:/owned/JARVIS.md');
  content = 'edited rules';
  assert.equal(await read('D:/owned'), 'edited rules');
  assert.equal(calls.length, 4);
  assert.deepEqual(calls[2][2], { kind: 'wsl', distro: 'other' });
  assert.equal(calls[3][3], calls[2][2]);
});

test('bounded project memory supports legacy filenames and denies unsafe canonical targets before reading', async () => {
  const calls = [];
  const bindings = { readProjectInstructions, checkReadableCanonical, currentWorkspaceEnv: () => ({ kind: 'local' }),
    native: { canonicalize: async path => path,
      readProjectMemory: async (_root, path) => { calls.push(path); return path.endsWith('/JARVIS.md') ? null : 'legacy rules'; },
    },
  };
  assert.equal(await memoryReader(bindings)('D:/owned'), 'legacy rules');
  assert.deepEqual(calls, ['D:/owned/JARVIS.md', 'D:/owned/TERAX.md']);
  bindings.native.canonicalize = async () => 'D:/owned/api_keys.json';
  calls.length = 0;
  assert.equal(await memoryReader(bindings)('D:/owned'), null);
  assert.deepEqual(calls, []);
  bindings.native.canonicalize = async path => path;
  bindings.native.readProjectMemory = async () => 'x'.repeat(40000);
  assert.equal((await memoryReader(bindings)('D:/owned')).length, 32768);
});

test('Jarvis token stores and scoped network credentials reject aliases and backups', () => {
  const stores = ['config/google_token.json', 'config/bridge_token.txt', 'config/bridge_token.json',
    'data/network/network_preferences.json', 'data/network/proxy_cache.json'];
  for (const store of stores) {
    for (const suffix of ['', '.', ' ', '.bak', '_backup', '-copy', ':stream', '::$DATA']) {
      const path = `D:/owned/${store}${suffix}`;
      assert.equal(checkReadable(path).ok, false, path);
      assert.equal(checkReadable(path.toUpperCase().replaceAll('/', '\\')).ok, false, path);
    }
  }
  for (const path of ['D:/owned/config./bridge_token.txt', 'D:/owned/config /bridge_token.json',
    'D:/owned/data/network./proxy_cache.json', 'D:/owned/.codex/config.toml',
    'D:/owned/.CoDeX. /auth.json', 'D:/owned/.codex::$INDEX_ALLOCATION/auth.json',
    'D:/owned/data/credentials/oauth.json', 'D:/owned/data./credentials /oauth.json']) {
    assert.equal(checkReadable(path).ok, false, path);
  }
  for (const path of ['D:/owned/src/network_preferences.json', 'D:/owned/src/proxy_cache.json',
    'D:/owned/src/bridge_token.txt', 'D:/owned/config/bridge_token_example.txt',
    'D:/owned/network/network_preferences.json', 'D:/owned/.codex-tools/readme.md']) {
    assert.equal(checkReadable(path).ok, true, path);
  }
});

test('Git credential files, backups and Windows aliases share the protected policy', async () => {
  for (const suffix of ['', '.', ' ', '.backup', '-old', '_copy', ':stream', '\ufeff.old']) {
    const path = `D:/owned/.git-credentials${suffix}`;
    assert.equal(checkReadable(path).ok, false, path);
    assert.equal(checkReadable(path.toUpperCase().replaceAll('/', '\\')).ok, false, path);
    assert.equal((await checkReadableCanonical('D:/owned/notes.txt', async () => path)).ok, false, path);
  }
  for (const name of ['git-credentials.md', '.git-credentials-example.ts', 'credentials-guide.md']) {
    // Exact credential basenames stay protected even when followed by backup punctuation.
    assert.equal(checkReadable(`D:/owned/${name}`).ok, name !== '.git-credentials-example.ts', name);
  }
});