import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const source = readFileSync(new URL('../../local_mode/chrome_mcp.py', import.meta.url), 'utf8');
const script = JSON.parse(source.match(/^PAGE_TEXT_SCRIPT = (".*")$/m)[1]);
for (const locale of ['en', 'ru']) {
  test(`fixed text extraction executes only rendered-text access for ${locale}`, () => {
    const text = locale === 'en' ? 'A visible paragraph.\nRead more' : 'Видимый абзац.\nЧитать далее';
    const body = { innerText: text };
    for (const property of ['innerHTML', 'outerHTML', 'textContent']) Object.defineProperty(body, property, { get() { throw Error('Markup or hidden-text access'); } });
    const frames = [{}, {}];
    const context = { document: { title:locale, body, querySelectorAll:selector => { assert.equal(selector, 'iframe'); return frames; } }, location:{href:'https://example.test/article'} };
    const result = vm.runInNewContext(`(${script})()`, context);
    assert.equal(result.text, text);
    assert.equal(result.scope, 'main_document_rendered_text');
    assert.equal(result.iframe_count, 2);
    assert.match(result.hint, /snapshot.*frames, shadow DOM and accessibility labels/);
    assert.equal(result.title, locale);
    assert.equal(result.url, context.location.href);
    const blank = vm.runInNewContext(`(${script})()`, { document:{title:'', body:null, querySelectorAll:() => [{}]}, location:{href:'about:blank'} });
    assert.equal(blank.text, '');
    assert.equal(blank.iframe_count, 1);
    assert.equal(blank.scope, 'main_document_rendered_text');
  });
}
