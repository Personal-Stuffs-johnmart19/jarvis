import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const require = createRequire(new URL('./dev/package.json', import.meta.url));
const { parse } = require('@babel/parser');

const visibleAttributes = new Set(['aria-label', 'aria-description', 'title', 'placeholder', 'alt', 'label', 'description', 'emptyMessage', 'searchPlaceholder']);
const literal = node => node?.type === 'StringLiteral' || (node?.type === 'TemplateLiteral' && !node.expressions.length);
const value = node => node.type === 'StringLiteral' ? node.value : node.quasis[0].value.cooked;
export function jsxText(raw) {
  const lines = raw.replace(/\t/g, ' ').split(/\r\n|\n|\r/);
  let last = 0;
  lines.forEach((line, i) => { if (/[^ ]/.test(line)) last = i; });
  return lines.map((line, i) => {
    if (i) line = line.replace(/^ +/, '');
    if (i < lines.length - 1) line = line.replace(/ +$/, '');
    return line && i < last ? line + ' ' : line;
  }).join('');
}
export function auditSource(source, filename, { includeNonTranslatable = false } = {}) {
  const ast = parse(source, {sourceType: 'module', plugins: ['typescript', ...(filename.endsWith('x') ? ['jsx'] : [])]});
  const findings = [], calls = [];
  const add = (node, kind, message) => findings.push({line: node.loc.start.line, kind, message, start: node.start, end: node.end});
  function runtimeText(node) {
    if (literal(node)) add(node, 'runtime-message', value(node));
    else if (node?.type === 'ConditionalExpression') { runtimeText(node.consequent); runtimeText(node.alternate); }
    else if (node?.type === 'LogicalExpression') { runtimeText(node.left); runtimeText(node.right); }
  }
  const property = (node, key) => node?.type === 'ObjectExpression' ? node.properties.find(item => item.type === 'ObjectProperty' && (item.key.name ?? item.key.value) === key)?.value : undefined;
  function runtimeAction(node) {
    if (node?.type === 'ConditionalExpression') { runtimeAction(node.consequent); runtimeAction(node.alternate); }
    else if (node?.type === 'LogicalExpression') { runtimeAction(node.left); runtimeAction(node.right); }
    else runtimeText(property(node, 'label'));
  }
  function visit(node, parent) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'CallExpression' && node.callee.type === 'Identifier' && ['t', 'translateMessage'].includes(node.callee.name) && literal(node.arguments[0])) calls.push(value(node.arguments[0]));
    if (node.type === 'JSXText') {
      const tag = parent?.openingElement?.name?.name;
      const message = jsxText(node.value);
      const noTranslate = parent?.openingElement?.attributes?.some(attr => attr.type === 'JSXAttribute' && attr.name.name === 'translate' && attr.value?.value === 'no');
      if ((!noTranslate || includeNonTranslatable) && !['code', 'pre', 'script', 'style'].includes(tag) && /[A-Za-z\u0400-\u04ff]/.test(message)) add(node, 'jsx-text', message);
    }
    if (node.type === 'JSXAttribute' && visibleAttributes.has(node.name.name) && node.value?.type === 'StringLiteral' && /[A-Za-z\u0400-\u04ff]/.test(node.value.value)) add(node.value, 'attribute', node.value.value);
    if (node.type === 'JSXExpressionContainer' && (parent?.type !== 'JSXAttribute' || visibleAttributes.has(parent.name.name))) {
      const expression = node.expression;
      if (literal(expression) && /[A-Za-z\u0400-\u04ff]/.test(value(expression))) add(expression, 'jsx-expression', value(expression));
      else if (expression.type === 'ConditionalExpression' && literal(expression.consequent) && literal(expression.alternate)) add(expression, 'conditional', [value(expression.consequent), value(expression.alternate)]);
      else if (expression.type === 'TemplateLiteral') add(expression, 'template', source.slice(expression.start, expression.end));
    }
    if (node.type === 'CallExpression') {
      const callee = source.slice(node.callee.start, node.callee.end);
      if (/^(?:(?:window\.)?(?:alert|confirm|prompt)|toast(?:\.(?:error|success|info|warning|message))?)$/.test(callee) && literal(node.arguments[0])) add(node.arguments[0], 'runtime-message', value(node.arguments[0]));
      if (/^toast(?:\.(?:error|success|info|warning|message))?$/.test(callee)) {
        const options = node.arguments[1];
        runtimeText(property(options, 'description'));
        for (const key of ['action', 'cancel']) runtimeAction(property(options, key));
      }
    }
    if (node.type === 'ObjectProperty' && literal(node.value) && ['label', 'title', 'description', 'placeholder', 'emptyText'].includes(node.key.name ?? node.key.value)) add(node.value, 'descriptor-candidate', value(node.value));
    if (literal(node) && /[\u0400-\u04ff]/.test(value(node))) add(node, 'non-English-literal', value(node));
    for (const [key, child] of Object.entries(node)) {
      if (['loc', 'comments', 'tokens', 'extra'].includes(key)) continue;
      if (Array.isArray(child)) { for (const item of child) if (item?.type) visit(item, node); }
      else if (child?.type) visit(child, node);
    }
  }
  visit(ast, null);
  return {findings, calls};
}
export function scan(root) {
  const report = { files: [], missingCatalogKeys: [], untranslatedRussianKeys: [], totals: {} };
  const en = JSON.parse(fs.readFileSync(path.join(root, 'apps/desktop/src/locales/en.json'), 'utf8'));
  const ru = JSON.parse(fs.readFileSync(path.join(root, 'apps/desktop/src/locales/ru.json'), 'utf8'));
  const missing = new Map();
  function walk(dir) {
    for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(file);
      else if (/\.tsx?$/.test(file) && !file.endsWith('.d.ts')) {
        const {findings, calls} = auditSource(fs.readFileSync(file, 'utf8'), file);
        if (findings.length) report.files.push({file: path.relative(root, file).replaceAll('\\', '/'), findings});
        for (const key of calls) if (!Object.hasOwn(en, key) || !Object.hasOwn(ru, key)) missing.set(key, true);
      }
    }
  }
  walk(path.join(root, 'apps/desktop/src'));
  walk(path.join(root, 'apps/desktop/workbench/src'));
  report.missingCatalogKeys = [...missing.keys()].sort();
  report.untranslatedRussianKeys = Object.keys(en).filter(key => ru[key] === en[key]);
  for (const {findings} of report.files) for (const {kind} of findings) report.totals[kind] = (report.totals[kind] ?? 0) + 1;
  return report;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const report = scan(root);
  const output = process.argv[2];
  if (output) fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({files: report.files.length, totals: report.totals, missingCatalogKeys: report.missingCatalogKeys.length, untranslatedRussianKeys: report.untranslatedRussianKeys.length}));
}
