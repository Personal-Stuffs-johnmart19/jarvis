"""Offline MCP routing benchmark; synthetic HTML, no browser/process/provider connection.

Timing measures in-process JSON transport and routing only. Token estimates use
ceil(characters/4), are illustrative, and are neither tokenizer counts nor billed usage.
"""
from __future__ import annotations
import argparse
import hashlib
import html
import json
import math
from pathlib import Path
import statistics
import threading
import time
from local_mode.chrome_mcp import ChromeSession, PAGE_TEXT_SCRIPT


class SyntheticPage:
    def __init__(self, locale: str, frame_only=False):
        self.title = 'Example article' if locale == 'en' else 'Пример статьи'
        self.lines = [f'{index}: ' + ('Readable article paragraph.' if locale == 'en' else 'Читаемый абзац статьи.') for index in range(96)]
        link = 'Read more' if locale == 'en' else 'Читать далее'
        self.text = '\n'.join([self.title, *[part for line in self.lines for part in (line, link)]])
        self.url = 'https://example.test/article'
        self.html = '<body><h1>' + self.title + '</h1><main>' + ''.join('<p>' + html.escape(line) + '</p><a href="https://example.test/articles/' + str(index) + '?source=fixture">' + link + '</a>' for index, line in enumerate(self.lines)) + '</main><div hidden>hidden fixture content</div></body>'
        self.snapshot = '### Page\n- Page URL: ' + self.url + '\n- Page Title: ' + self.title + '\n### Snapshot\n```yaml\n- heading "' + self.title + '" [level=1] [ref=e0]\n- main [ref=e1]:\n' + '\n'.join(f'  - paragraph [ref=p{index}]: {line}\n  - link "{link}" [ref=a{index}]:\n    - /url: https://example.test/articles/{index}?source=fixture' for index, line in enumerate(self.lines)) + '\n```'
        self.iframe_count = 1 if frame_only else 0
        if frame_only:
            self.lines = []
            self.text = ''
            self.html = '<body><iframe src="https://example.test/embedded"></iframe></body>'
            self.snapshot = '### Snapshot\n- iframe [ref=e1]: embedded document'
        self.read_payload = {'scope':'main_document_rendered_text', 'iframe_count':self.iframe_count,
                             'hint':'Use snapshot for frames, shadow DOM and accessibility labels.',
                             'url':self.url, 'title':self.title, 'text':self.text}
        self.read_result = '### Result\n' + json.dumps(self.read_payload, ensure_ascii=False) + '\n### Ran Playwright code\nawait page.evaluate(' + PAGE_TEXT_SCRIPT + ');'


def synthetic_session(page: SyntheticPage, mode='legacy_plain', error=False):
    session = ChromeSession.__new__(ChromeSession)
    session.lock = threading.RLock()
    session.profile = 'Default'
    session._secret = 'synthetic_private_token_123456789'
    names = ['browser_snapshot', 'browser_evaluate', 'browser_tabs']
    if mode == 'native':
        names += ['browser_reload', 'browser_navigate_forward']
    session.tools = {name:{'inputSchema':{'properties':{}}} for name in names}
    session.calls = []
    session.response_chars = 0
    session.response_bytes = 0
    def request(method, params):
        assert method == 'tools/call'
        assert session.lock._is_owned(), 'Per-profile lock must surround the entire action'
        session.calls.append((params['name'], params['arguments']))
        if error:
            text = 'Upstream failed: ' + session._secret
        elif params['name'] == 'browser_snapshot' or params['name'] in {'browser_reload', 'browser_navigate_forward'}:
            text = page.snapshot
        elif params['name'] == 'browser_tabs':
            text = '### Result\nSelected tab'
        elif params['arguments'].get('function') == PAGE_TEXT_SCRIPT:
            text = page.read_result
        else:
            text = '### Result\nundefined\n### Ran Playwright code\n' + params['arguments']['function']
            if mode == 'legacy_snapshot':
                text += '\n' + page.snapshot
        payload = {'content':[{'type':'text', 'text':text}], 'isError':error}
        wire = json.dumps(payload, ensure_ascii=False)
        session.response_chars += len(wire)
        session.response_bytes += len(wire.encode('utf8'))
        return json.loads(wire)
    session.request = request
    return session


def legacy_execute(session, action):
    """Frozen pre-change routing, used only for identical-fixture comparison."""
    with session.lock:
        if action == 'get_text':
            return session.tool('browser_snapshot', {})
        function = '() => { history.forward(); }' if action == 'forward' else '() => { location.reload(); }'
        result = session.tool('browser_evaluate', {'function':function})
        return session.tool('browser_snapshot', {}) if result['ok'] else result


def measure(page, mode, action, optimized, iterations):
    samples = []
    for _ in range(iterations):
        session = synthetic_session(page, mode)
        started = time.perf_counter()
        result = session.execute({'action':action}) if optimized else legacy_execute(session, action)
        samples.append((time.perf_counter() - started) * 1_000_000)
    serialized = json.dumps(result, ensure_ascii=False)
    samples.sort()
    return {
        'mcp_calls':len(session.calls),
        'mcp_tool_names':[name for name, _ in session.calls],
        'all_mcp_response_chars':session.response_chars,
        'all_mcp_response_bytes_utf8':session.response_bytes,
        'model_result_chars':len(serialized),
        'model_result_bytes_utf8':len(serialized.encode('utf8')),
        'estimated_model_result_tokens_chars_div_4':math.ceil(len(serialized)/4),
        'synthetic_latency_us_p50':round(statistics.median(samples), 2),
        'synthetic_latency_us_p95':round(samples[math.ceil(.95 * len(samples))-1], 2),
    }


def report(iterations=200):
    cases = []
    for locale in ['en', 'ru']:
        page = SyntheticPage(locale)
        for action, mode in [('get_text','legacy_plain'), ('reload','native'), ('forward','native'), ('reload','legacy_snapshot'), ('forward','legacy_snapshot'), ('reload','legacy_plain'), ('forward','legacy_plain')]:
            cases.append({'locale':locale, 'action':action, 'server_mode':mode, 'identical_html_sha256':hashlib.sha256(page.html.encode()).hexdigest(), 'before':measure(page, mode, action, False, iterations), 'after':measure(page, mode, action, True, iterations)})
    return {'scope':'offline synthetic JSON transport and routing; excludes real browser, network and model latency', 'token_estimate':'ceil(characters / 4); illustrative heuristic, not tokenizer output or billed usage', 'iterations_per_case':iterations, 'cases':cases}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--iterations', type=int, default=200)
    args = parser.parse_args()
    if args.iterations < 2:
        parser.error('--iterations must be at least 2')
    result = report(args.iterations)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'cases':len(result['cases']), 'iterations_per_case':args.iterations, 'report':str(args.output) if args.output else result}))
