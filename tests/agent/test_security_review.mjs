import assert from 'node:assert/strict';
import test from 'node:test';
import { checkCodacyReview, checkSecurityReview, normalizeSource, sourceHash } from '../../tools/ci/check-security-review.mjs';

const path = 'apps/desktop/src-tauri/src/fixture.rs';
const rule = 'Semgrep_rust.lang.security.unsafe-usage.unsafe-usage';
const text = 'fn fixture() {\n    unsafe { native_call(); }\n}\n';
function fixture() {
  return {
    report: { version: '2.1.0', runs: [{ tool: { driver: { name: 'Semgrep' } }, results: [{
      ruleId: rule, locations: [{ physicalLocation: { artifactLocation: { uri: path }, region: { startLine: 2 } } }],
    }] }] },
    audit: { version: 1, files: [{ path, sha256: sourceHash(text), findings: [{
      rule_id: rule, line: 2, end_line: 2, function: 'fixture', rationale: 'Reviewed native contract',
      line_sha256: sourceHash(text.split('\n')[1]), span_sha256: sourceHash(text.split('\n')[1]),
    }] }] },
  };
}
const check = (value, status = 1, source = text) => checkSecurityReview(value.report, status, value.audit, () => source);
test('accepts only the reviewed unchanged span and normalizes Windows checkout newlines', () => {
  assert.equal(check(fixture()).reviewedFindings, 1);
  assert.equal(check(fixture(), 1, '\uFEFF' + text.replaceAll('\n', '\r\n')).reviewedFindings, 1);
  assert.equal(normalizeSource('a\rb\r\n'), 'a\nb\n');
});
test('new locations and rules stay blocking', () => {
  for (const change of [r => r.ruleId = 'unexpected-security-rule',
    r => r.locations[0].physicalLocation.region.startLine = 3,
    r => r.locations[0].physicalLocation.artifactLocation.uri = '../outside.rs']) {
    const value = fixture(); change(value.report.runs[0].results[0]);
    assert.throws(() => check(value), /Unreviewed/);
  }
});
test('file or span changes invalidate an earlier review', () => {
  assert.throws(() => check(fixture(), 1, text + '// new code\n'), /file changed/);
  const value = fixture(); value.audit.files[0].findings[0].span_sha256 = '0'.repeat(64);
  assert.throws(() => check(value), /span changed/);
  const omitted = fixture(); omitted.report.runs[0].results = [];
  assert.throws(() => check(omitted, 0, text + '// nosemgrep\n'), /file changed/);
});
test('analyzer failures and incomplete or missing reports cannot be waived', () => {
  assert.throws(() => check(fixture(), 2), /Analyzer failed/);
  assert.throws(() => check({ ...fixture(), report: null }), /complete/);
  const value = fixture(); value.report.runs[0].results = [];
  assert.throws(() => check(value), /failed without/);
  assert.equal(check(value, 0).reviewedFindings, 0);
  value.report.runs[0].invocations = [{ executionSuccessful: false }];
  assert.throws(() => check(value, 0), /Incomplete/);
});
test('errors, warnings and suppressed or ambiguous locations require review', () => {
  for (const invocation of [{ toolExecutionNotifications: [{ level: 'error' }] },
    { toolConfigurationNotifications: [{ level: 'warning' }] }]) {
    const value = fixture(); value.report.runs[0].invocations = [invocation];
    assert.throws(() => check(value), /warning or error/);
  }
  for (const change of [r => r.suppressions = [{ kind: 'inSource' }], r => r.locations.push(r.locations[0])]) {
    const value = fixture(); change(value.report.runs[0].results[0]);
    assert.throws(() => check(value), /Suppressed or ambiguous/);
  }
});
test('the disposition table cannot widen into a rule, file or platform exemption', () => {
  for (const change of [f => f.path = 'apps/desktop/src-tauri/../outside.rs',
    f => f.findings[0].rule_id = 'unexpected-security-rule',
    f => f.findings[0].rationale = '', f => f.findings.push(f.findings[0])]) {
    const value = fixture(); change(value.audit.files[0]);
    assert.throws(() => check(value), /Invalid/);
  }
});

function codacyFixture() {
  return {
    metadata: { executionMode: 'strict', startedAt: '2026-11-12T00:00:00Z', completedAt: '2026-11-12T00:00:01Z' },
    errors: [], skippedFiles: [],
    toolResults: [{ toolId: 'Semgrep', status: 'success', errorCount: 0, issueCount: 1, filesAnalyzed: 1, durationMs: 1 }],
    issues: [{ toolId: 'Semgrep', patternId: rule, filePath: path.replaceAll('/', '\\'), line: 2 }],
  };
}
test('structured execution accepts complete error-free scans with repository-relative Windows paths', () => {
  assert.equal(checkCodacyReview(codacyFixture(), 1, fixture().audit, () => text).reviewedFindings, 1);
  const withoutSkips = codacyFixture(); delete withoutSkips.skippedFiles;
  assert.equal(checkCodacyReview(withoutSkips, 1, fixture().audit, () => text).reviewedFindings, 1);
});
test('structured execution rejects partial/error/zero-file scans even with reviewed findings', () => {
  for (const change of [r => r.errors.push({ message: 'timeout' }), r => delete r.errors,
    r => r.skippedFiles.push('unscanned.rs'), r => r.toolResults[0].status = 'partial',
    r => r.toolResults[0].errorCount = 1, r => r.toolResults[0].filesAnalyzed = 0,
    r => r.toolResults[0].issueCount = 0, r => r.toolResults.push(r.toolResults[0]),
    r => r.metadata.executionMode = 'inspect', r => delete r.metadata.completedAt,
    r => r.issues[0].filePath = 'C:\\outside.rs', r => r.issues[0].filePath = '../outside.rs']) {
    const report = codacyFixture(); change(report);
    assert.throws(() => checkCodacyReview(report, 1, fixture().audit, () => text));
  }
});

const argsRule = 'Semgrep_rust.lang.security.args-os.args-os';
const argsText = 'fn main_entry() {\n    let args = std::env::args_os().skip(1);\n}\n';
function argsFixture() {
  const value = fixture();
  value.report.runs[0].results[0].ruleId = argsRule;
  const file = value.audit.files[0];
  file.sha256 = sourceHash(argsText);
  Object.assign(file.findings[0], {
    rule_id: argsRule, function: 'main_entry',
    rationale: 'Reviewed explicit CLI argument collection; authentication remains separate',
    line_sha256: sourceHash(argsText.split('\n')[1]),
    span_sha256: sourceHash(argsText.split('\n')[1]),
  });
  return value;
}

test('args-os syntax eligibility still requires an exact reviewed site', () => {
  assert.equal(check(argsFixture(), 1, argsText).reviewedFindings, 1);
  for (const change of [
    result => result.locations[0].physicalLocation.region.startLine = 3,
    result => result.locations[0].physicalLocation.artifactLocation.uri = 'apps/desktop/src-tauri/src/other.rs',
    result => result.ruleId = 'Semgrep_rust.lang.security.args.args',
  ]) {
    const value = argsFixture();
    const unreviewed = structuredClone(value.report.runs[0].results[0]);
    change(unreviewed);
    value.report.runs[0].results.push(unreviewed);
    assert.throws(() => check(value, 1, argsText), /Unreviewed security finding/);
  }
});

test('args-os file, line and span changes invalidate the reviewed input source', () => {
  assert.throws(() => check(argsFixture(), 1, argsText + '// changed file\n'), /Audited file changed/);
  for (const field of ['line_sha256', 'span_sha256']) {
    const value = argsFixture();
    value.audit.files[0].findings[0][field] = '0'.repeat(64);
    assert.throws(() => check(value, 1, argsText), /Audited span changed/);
  }
  const changedInput = argsText.replace('args_os().skip(1)', 'args_os().skip(2)');
  const refreshedFileOnly = argsFixture();
  refreshedFileOnly.audit.files[0].sha256 = sourceHash(changedInput);
  assert.throws(() => check(refreshedFileOnly, 1, changedInput), /Audited span changed/);
});
