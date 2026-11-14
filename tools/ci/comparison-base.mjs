import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

export function comparisonBase(candidate, cwd = process.cwd()) {
  const git = (args, input) => execFileSync('git', args, {
    cwd, input, encoding: 'utf8', windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
  }).trim();
  if (typeof candidate === 'string' && /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/i.test(candidate)
      && !/^0+$/.test(candidate)) {
    try {
      git(['cat-file', '-e', `${candidate}^{commit}`]);
      const revision = git(['merge-base', candidate, 'HEAD']);
      return { revision, strategy: revision === candidate ? 'incremental' : 'common-ancestor' };
    } catch {
      // Removed or unrelated history cannot establish a safe incremental baseline.
    }
  }
  return {
    revision: git(['hash-object', '-w', '-t', 'tree', '--stdin'], ''),
    strategy: 'full-tree',
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = comparisonBase(process.argv[2]);
  process.stdout.write(`revision=${result.revision}\nstrategy=${result.strategy}\n`);
}
