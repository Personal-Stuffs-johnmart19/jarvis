import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

// Use the pinned analyzer's public routing metadata, not language-detection
// filenames (which include Cargo.lock even though Opengrep cannot scan it).
export function analyzerPolicy(cwd = process.cwd()) {
  const devRequire = createRequire(resolve(cwd, 'tools/dev/package.json'));
  const cliRequire = createRequire(devRequire.resolve('@codacy/analysis-cli'));
  const adaptersRequire = createRequire(cliRequire.resolve('@codacy/analysis-adapters'));
  const runnerRequire = createRequire(adaptersRequire.resolve('@codacy/analysis-runner'));
  const { minimatch } = runnerRequire('minimatch');
  const { descriptor } = adaptersRequire('@codacy/tools-opengrep-1/metadata');
  const toolRequire = createRequire(adaptersRequire.resolve('@codacy/tools-opengrep-1/metadata'));
  const { parse } = toolRequire('yaml');
  const config = parse(readFileSync(resolve(cwd, '.codacy.yaml'), 'utf8'));
  if (!Array.isArray(descriptor.filePatterns) || !descriptor.filePatterns.length
      || !Array.isArray(config.exclude_paths)
      || [...descriptor.filePatterns, ...config.exclude_paths].some(value => typeof value !== 'string')) {
    throw new Error('Invalid managed analyzer file policy');
  }
  // Match the runner's dot:true glob semantics, including hidden source dirs.
  return { patterns: descriptor.filePatterns, excludes: config.exclude_paths,
    matches: (path, pattern) => minimatch(path, pattern, { dot: true }) };
}

export function selectSecurityScope(paths, strategy, { patterns, excludes, matches }) {
  if (!['incremental', 'common-ancestor', 'full-tree'].includes(strategy)) {
    throw new Error('Invalid comparison strategy');
  }
  const fullScan = path => path === '.codacy.yaml' || path.startsWith('.codacy/')
    || path === '.semgrepignore' || path === '.gitignore' || path === '.node-version'
    || path.startsWith('tools/ci/') || path.startsWith('tools/dev/')
    || path === '.github/workflows/source-quality.yml'
    || path.endsWith('/pathPolicy.json') || path === 'local_mode/file_policy.py';
  if (strategy === 'full-tree' || paths.some(fullScan)) {
    return { scan: true, strategy: 'full-tree', reason: 'Full security policy validation required' };
  }
  const source = paths.filter(path => patterns.some(pattern => matches(path, pattern))
    && !excludes.some(pattern => matches(path, pattern)));
  return {
    scan: source.length > 0,
    strategy,
    reason: source.length ? 'Changed supported source requires analysis' : 'No changed supported source; SAST is inapplicable',
  };
}

export function securityScope(revision, strategy, cwd = process.cwd(), policy = analyzerPolicy(cwd)) {
  // All changed names include removed policy files; only extant nondeleted
  // destinations can make an incremental source scan applicable.
  const gitPaths = filter => execFileSync('git', [
    'diff', '--name-only', '-z', '--no-renames', `--diff-filter=${filter}`, revision, 'HEAD', '--',
  ], { cwd, encoding: 'utf8', windowsHide: true }).split('\0').filter(Boolean);
  const all = gitPaths('ACDMRT');
  const extant = gitPaths('ACMRT');
  const policyScope = selectSecurityScope(all, strategy, { patterns: [], excludes: [] });
  return policyScope.scan ? policyScope : selectSecurityScope(extant, strategy, policy);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = securityScope(process.argv[2], process.argv[3]);
  process.stdout.write(`scan=${result.scan}\nstrategy=${result.strategy}\nreason=${result.reason}\n`);
}
