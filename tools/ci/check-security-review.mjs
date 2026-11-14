import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const normalizeSource = text => text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
export const sourceHash = text => createHash('sha256').update(text).digest('hex');
const auditRules = new Set([
  'Semgrep_rust.lang.security.unsafe-usage.unsafe-usage',
  'Semgrep_rust.lang.security.temp-dir.temp-dir',
  'Semgrep_rust.lang.security.current-exe.current-exe',
  'Semgrep_rust.lang.security.args-os.args-os',
]);

// Syntax warnings require native-contract review. A disposition covers one
// unchanged source span, never a file, platform, rule or analyzer failure.
export function checkSecurityReview(report, analyzerStatus, audit, readSource) {
  if (![0, 1].includes(analyzerStatus)) throw new Error(`Analyzer failed: ${analyzerStatus}`);
  if (report?.version !== '2.1.0' || !Array.isArray(report.runs) || report.runs.length !== 1)
    throw new Error('Expected a complete single-tool SARIF report');
  if (audit?.version !== 1 || !Array.isArray(audit.files)) throw new Error('Invalid native audit');
  const run = report.runs[0];
  if (!['Semgrep', 'Opengrep'].includes(run.tool?.driver?.name) || !Array.isArray(run.results))
    throw new Error('Missing expected analyzer results');
  for (const invocation of run.invocations ?? []) {
    if (invocation.executionSuccessful === false) throw new Error('Incomplete analyzer execution');
    for (const key of ['toolExecutionNotifications', 'toolConfigurationNotifications']) {
      if ((invocation[key] ?? []).some(item => item.level !== 'note'))
        throw new Error('Analyzer reported a warning or error');
    }
  }
  if (analyzerStatus === 1 && run.results.length === 0)
    throw new Error('Analyzer failed without reported findings');
  const files = new Map();
  for (const file of audit.files) {
    if (files.has(file.path) || !/^apps\/desktop\/src-tauri\/(?:[\w.-]+\/)*[\w.-]+\.rs$/.test(file.path)
        || file.path.split('/').some(part => part === '..') || !/^[a-f0-9]{64}$/.test(file.sha256)
        || !Array.isArray(file.findings)) throw new Error('Invalid audited source path or fingerprint');
    const source = normalizeSource(readSource(file.path));
    if (sourceHash(source) !== file.sha256) throw new Error(`Audited file changed: ${file.path}`);
    const lines = source.split('\n');
    const findings = new Map();
    for (const finding of file.findings) {
      const key = `${finding.rule_id}:${finding.line}:${finding.end_line}`;
      if (findings.has(key) || !auditRules.has(finding.rule_id)
          || !Number.isInteger(finding.line) || finding.line < 1
          || !Number.isInteger(finding.end_line) || finding.end_line < finding.line
          || finding.end_line > lines.length
          || !finding.function || !finding.rationale
          || !/^[a-f0-9]{64}$/.test(finding.line_sha256)
          || !/^[a-f0-9]{64}$/.test(finding.span_sha256)) throw new Error('Invalid native disposition');
      if (sourceHash(lines[finding.line - 1]) !== finding.line_sha256
          || sourceHash(lines.slice(finding.line - 1, finding.end_line).join('\n')) !== finding.span_sha256)
        throw new Error(`Audited span changed: ${file.path}:${finding.line}`);
      findings.set(key, finding);
    }
    files.set(file.path, { ...file, findings });
  }
  for (const result of run.results) {
    if (result.suppressions?.length || !Array.isArray(result.locations) || result.locations.length !== 1)
      throw new Error('Suppressed or ambiguous findings require review');
    const location = result.locations[0].physicalLocation;
    const path = location?.artifactLocation?.uri;
    const start = location?.region?.startLine;
    const end = location?.region?.endLine ?? start;
    const file = files.get(path);
    const finding = file?.findings.get(`${result.ruleId}:${start}:${end}`);
    if (!finding) throw new Error(`Unreviewed security finding: ${path}:${start} ${result.ruleId}`);
  }
  return { reviewedFindings: run.results.length, unreviewedFindings: 0 };
}

// Codacy's SARIF formatter discards execution errors. Validate the unmodified
// JSON AnalysisResult before adapting its issues to the reviewed span format.
export function checkCodacyReview(report, analyzerStatus, audit, readSource) {
  const metadata = report?.metadata;
  const start = Date.parse(metadata?.startedAt);
  const end = Date.parse(metadata?.completedAt);
  if (metadata?.executionMode !== 'strict' || !Number.isFinite(start) || !Number.isFinite(end) || end < start
      || !Array.isArray(report.errors) || report.errors.length
      || report.skippedFiles !== undefined && (!Array.isArray(report.skippedFiles) || report.skippedFiles.length)
      || !Array.isArray(report.issues) || !Array.isArray(report.toolResults) || report.toolResults.length !== 1)
    throw new Error('Incomplete or failed structured analyzer execution');
  const tool = report.toolResults[0];
  if (tool.toolId !== 'Semgrep' || tool.status !== 'success' || tool.errorCount !== 0
      || tool.issueCount !== report.issues.length || !Number.isInteger(tool.filesAnalyzed) || tool.filesAnalyzed < 1
      || !Number.isFinite(tool.durationMs) || tool.durationMs < 0)
    throw new Error('Expected one complete security tool with zero errors');
  const results = report.issues.map(issue => {
    if (issue.toolId !== 'Semgrep' || typeof issue.filePath !== 'string') throw new Error('Unexpected issue tool or path');
    const path = issue.filePath.replaceAll('\\', '/');
    if (path.startsWith('/') || /^[a-z]:/i.test(path) || path.split('/').includes('..'))
      throw new Error('Analyzer issue path escaped the repository');
    return { ruleId: issue.patternId, locations: [{ physicalLocation: {
      artifactLocation: { uri: path }, region: { startLine: issue.line, endLine: issue.endLine ?? issue.line },
    } }] };
  });
  return checkSecurityReview({ version: '2.1.0', runs: [{
    tool: { driver: { name: 'Semgrep' } }, results,
  }] }, analyzerStatus, audit, readSource);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const [reportPath, status] = process.argv.slice(2);
    if (!reportPath || !/^[01]$/.test(status ?? '')) throw new Error('Report and analyzer exit status are required');
    const report = JSON.parse(readFileSync(reportPath, 'utf8'));
    const audit = JSON.parse(readFileSync('.codacy/native-audit.json', 'utf8'));
    const result = checkCodacyReview(report, Number(status), audit,
      path => readFileSync(resolve(path), 'utf8'));
    console.log(`Security review: ${result.reviewedFindings} exact audited findings; no unreviewed findings`);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
