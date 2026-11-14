import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync, spawnSync} from 'node:child_process';
import {mkdtempSync, mkdirSync, writeFileSync, unlinkSync, rmSync, realpathSync} from 'node:fs';
import {dirname, join, resolve, sep} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const guard = join(root, 'tools/ci/check-public-tree.mjs');
const fixtureRoot = join(root, '.cache/verification/history-purge-20261113-230754/fixtures');

function fixture(t) {
  mkdirSync(fixtureRoot, {recursive:true});
  const directory = mkdtempSync(join(fixtureRoot, 'public-tree-'));
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.startsWith('GIT_')));
  const emptyConfig = join(directory, 'empty-git-config');
  writeFileSync(emptyConfig, '');
  Object.assign(env, {GIT_CONFIG_NOSYSTEM:'1', GIT_CONFIG_GLOBAL:emptyConfig});
  const template = join(directory, 'empty-template');
  mkdirSync(template);
  const git = (...args) => execFileSync('git', ['-c','user.name=Synthetic QA','-c','user.email=qa@example.invalid','-c','commit.gpgsign=false',...args],
    {cwd:directory,env,encoding:'utf8',timeout:10000,windowsHide:true});
  t.after(() => {
    const base = realpathSync(fixtureRoot) + sep;
    const target = realpathSync(directory);
    assert.ok(target.startsWith(base) && resolve(target) !== resolve(fixtureRoot), 'Cleanup must stay inside the owned fixture directory');
    rmSync(target, {recursive:true,force:true,maxRetries:2,retryDelay:20});
  });
  git('init','--quiet',`--template=${template}`);
  for (const name of ['README.md','CONTRIBUTING.md','SECURITY.md']) writeFileSync(join(directory,name),'Synthetic public fixture\n');
  const privateFile = join(directory,'config/synthetic-key.dpapi');
  mkdirSync(dirname(privateFile));
  writeFileSync(privateFile,'SYNTHETIC NONSECRET BYTES');
  git('add','README.md','CONTRIBUTING.md','SECURITY.md','config/synthetic-key.dpapi');
  git('commit','--quiet','-m','Synthetic historical private pathname');
  unlinkSync(privateFile);
  git('add','-u');
  git('commit','--quiet','-m','Delete private file from current tree');
  const check = () => spawnSync(process.execPath,[guard],{cwd:directory,env,encoding:'utf8',timeout:15000,windowsHide:true});
  return {directory,git,check};
}

test('public guard rejects a deleted historical private path despite a clean current index', t => {
  const {git,check} = fixture(t);
  assert.doesNotMatch(git('ls-files'), /dpapi/);
  const result = check();
  assert.ifError(result.error);
  assert.notEqual(result.status,0);
  assert.match(result.stderr,/remain in reachable HEAD history/);
  assert.match(result.stderr,/config\/synthetic-key\.dpapi/);
});

test('public guard accepts a clean orphan snapshot without rejecting unreachable old history', t => {
  const {git,check} = fixture(t);
  git('checkout','--quiet','--orphan','clean-public-snapshot');
  git('commit','--quiet','-m','Clean public snapshot');
  assert.equal(git('rev-list','--count','HEAD').trim(),'1');
  const result = check();
  assert.ifError(result.error);
  assert.equal(result.status,0,result.stderr);
  assert.match(result.stdout,/Public tree: 3 tracked paths checked/);
});

test('public guard fails closed when shallow history hides the historical private path', t => {
  const {directory,git,check} = fixture(t);
  writeFileSync(join(directory,'.git/shallow'),git('rev-parse','HEAD'));
  assert.equal(git('rev-parse','--is-shallow-repository').trim(),'true');
  assert.equal(git('rev-list','--count','HEAD').trim(),'1');
  const result = check();
  assert.ifError(result.error);
  assert.notEqual(result.status,0);
  assert.match(result.stderr,/Complete reachable HEAD history is required/);
});
