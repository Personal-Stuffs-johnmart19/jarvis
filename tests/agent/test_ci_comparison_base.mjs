import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { comparisonBase } from '../../tools/ci/comparison-base.mjs';

test('comparison uses ancestry and scans the full tree when rewritten history is absent', () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'jarvis-ci-base-'));
  const git = (...args) => execFileSync('git', args, {
    cwd: directory, encoding: 'utf8', windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
  }).trim();
  const commit = value => {
    writeFileSync(path.join(directory, 'fixture.txt'), value);
    git('add', 'fixture.txt');
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-qm', value);
    return git('rev-parse', 'HEAD');
  };
  try {
    git('init', '-q');
    const base = commit('base');
    const previous = commit('original');
    git('checkout', '--detach', base);
    commit('rewritten');
    assert.deepEqual(comparisonBase(base, directory), { revision: base, strategy: 'incremental' });
    assert.deepEqual(comparisonBase(previous, directory), { revision: base, strategy: 'common-ancestor' });
    for (const missing of ['f'.repeat(40), '0'.repeat(40), '', '--help']) {
      const selected = comparisonBase(missing, directory);
      assert.equal(selected.strategy, 'full-tree');
      assert.equal(git('diff', '--name-only', selected.revision, 'HEAD'), 'fixture.txt');
    }
  } finally {
    // Delete only the fresh test directory, never a path supplied by a caller.
    assert.equal(path.dirname(directory), tmpdir());
    assert.ok(path.basename(directory).startsWith('jarvis-ci-base-'));
    rmSync(directory, { recursive: true, force: true });
  }
});
