import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { analyzerPolicy, selectSecurityScope, securityScope } from '../../tools/ci/security-scope.mjs';

const policy = analyzerPolicy(process.env.CODACY_TEST_ROOT || process.cwd());
const scope = paths => selectSecurityScope(paths, 'incremental', policy);

test('actual managed routing excludes Cargo metadata but includes supported source', () => {
  assert.equal(scope(['apps/desktop/src-tauri/Cargo.toml', 'apps/desktop/src-tauri/Cargo.lock']).scan, false);
  for (const path of ['a.rs', 'local_mode/actions/a.py', 'apps/desktop/src/a.tsx', 'a.mjs', 'a.json', 'a.yml', 'Dockerfile', 'a.sh', 'a.kt', '.hidden/source.py', '.env']) {
    assert.equal(scope([path]).scan, true, path);
  }
  assert.equal(scope(['README.md', 'image.png']).scan, false);
  assert.equal(scope(['apps/desktop/target/generated.rs', '.cache/probe.py']).scan, false);
});

test('security policy, audit, validator and analyzer configuration require full validation', () => {
  for (const path of ['.codacy.yaml', '.codacy/native-audit.json', '.codacy/generated/Semgrep/rules.yaml',
    'tools/ci/check-security-review.mjs', 'tools/ci/security-scope.mjs', 'tools/dev/pnpm-lock.yaml',
    '.github/workflows/source-quality.yml', 'apps/desktop/workbench/src/modules/ai/lib/pathPolicy.json',
    'local_mode/file_policy.py', '.semgrepignore', '.gitignore', '.node-version']) {
    assert.equal(scope([path]).strategy, 'full-tree', path);
    assert.equal(scope([path]).scan, true, path);
  }
  assert.equal(selectSecurityScope([], 'full-tree', policy).scan, true);
  assert.equal(selectSecurityScope(['a.py'], 'common-ancestor', policy).scan, true);
  assert.throws(() => selectSecurityScope([], 'invalid', policy));
});

test('workflow uses selected scope without weakening analyzer or dependency validation', () => {
  const workflow = readFileSync(new URL('../../.github/workflows/source-quality.yml', import.meta.url), 'utf8');
  const analysis = workflow.slice(workflow.indexOf('      - name: Codacy source analysis'));
  assert.match(analysis, /if: steps\.security_scope\.outputs\.scan == 'true'/);
  assert.match(analysis, /QUALITY_STRATEGY: \$\{\{ steps\.security_scope\.outputs\.strategy \}\}/);
  assert.match(analysis, /node tools\/ci\/check-security-review\.mjs quality\.json "\$analyzer_status"/);
  assert.match(workflow, /No changed supported source; SAST is inapplicable/);
  assert.match(workflow, /cargo metadata --locked/);
  assert.match(workflow, /audit --audit-level moderate/);
  const triggerPaths = workflow.split('\n').filter(line => line.trimStart().startsWith('paths:'));
  assert.equal(triggerPaths.length, 2);
  for (const paths of triggerPaths) {
    assert.ok(paths.includes("'.gitignore'"));
    assert.ok(paths.includes("'.semgrepignore'"));
  }
});

test('Git scope uses nondeleted destinations, catches renamed source and deleted policy', () => {
  const cwd = mkdtempSync(join(tmpdir(), 'jarvis-security-scope-'));
  const git = (...args) => execFileSync('git', args, { cwd, encoding: 'utf8', windowsHide: true }).trim();
  const commit = () => { git('add', '-A'); git('-c', 'user.name=Scope test', '-c', 'user.email=scope@example.invalid', 'commit', '-qm', 'fixture'); };
  try {
    git('init', '-q');
    mkdirSync(join(cwd, '.codacy'));
    writeFileSync(join(cwd, 'removed.py'), 'print(1)\n');
    writeFileSync(join(cwd, 'note.txt'), 'print(2)\n');
    writeFileSync(join(cwd, '.codacy', 'native-audit.json'), '{}');
    commit();
    const base = git('rev-parse', 'HEAD');
    rmSync(join(cwd, 'removed.py'));
    commit();
    assert.equal(securityScope(base, 'incremental', cwd, policy).scan, false);
    git('mv', 'note.txt', 'renamed.py'); commit();
    assert.equal(securityScope(base, 'incremental', cwd, policy).scan, true);
    const beforePolicy = git('rev-parse', 'HEAD');
    rmSync(join(cwd, '.codacy', 'native-audit.json')); commit();
    assert.equal(securityScope(beforePolicy, 'incremental', cwd, policy).strategy, 'full-tree');
  } finally {
    rmSync(cwd, { recursive: true, force: true });
  }
});
