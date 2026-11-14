"""Offline browser read/navigation contracts, without starting MCP or a browser."""
import json
import pytest
from benchmarks.browser_efficiency import SyntheticPage, synthetic_session, legacy_execute, report
from local_mode.chrome_mcp import PAGE_TEXT_SCRIPT


@pytest.mark.parametrize('locale', ['en', 'ru'])
def test_get_text_reads_visible_text_without_snapshot_markup(locale):
    page = SyntheticPage(locale)
    baseline = synthetic_session(page)
    before = legacy_execute(baseline, 'get_text')
    session = synthetic_session(page)
    result = session.execute({'action':'get_text', 'function':'untrusted code must not be evaluated'})
    assert session.calls == [('browser_evaluate', {'function':PAGE_TEXT_SCRIPT})]
    assert 'document.body?.innerText' in PAGE_TEXT_SCRIPT
    assert 'innerHTML' not in PAGE_TEXT_SCRIPT
    assert result['ok'] and result['untrusted_content'] and result['profile_id'] == 'Default'
    parsed = json.JSONDecoder().raw_decode(result['text'].split('### Result\n', 1)[1])[0]
    assert parsed == page.read_payload
    assert parsed['scope'] == 'main_document_rendered_text'
    assert parsed['iframe_count'] == 0
    assert 'frames, shadow DOM and accessibility labels' in parsed['hint']
    assert '[ref=' not in parsed['text'] and 'hidden fixture content' not in parsed['text']
    assert all(line in before['text'] and line in parsed['text'] for line in page.lines)
    assert len(result['text'].encode()) < len(before['text'].encode()) * .5


@pytest.mark.parametrize('action, native', [('reload','browser_reload'), ('forward','browser_navigate_forward')])
def test_advertised_navigation_uses_single_call_with_its_result(action, native):
    session = synthetic_session(SyntheticPage('en'), 'native')
    result = session.execute({'action':action})
    assert session.calls == [(native, {})]
    assert result['ok'] and result['untrusted_content'] and result['profile_id'] == 'Default'
    assert '### Snapshot' in result['text']


@pytest.mark.parametrize('action', ['reload', 'forward'])
def test_legacy_evaluate_snapshot_is_not_requested_again(action):
    session = synthetic_session(SyntheticPage('ru'), 'legacy_snapshot')
    result = session.execute({'action':action})
    assert len(session.calls) == 1
    assert session.calls[0][0] == 'browser_evaluate'
    assert '### Result\nundefined' in result['text'] and '### Snapshot' in result['text']


@pytest.mark.parametrize('action', ['reload', 'forward'])
def test_legacy_plain_result_observes_once_and_retains_action_result(action):
    session = synthetic_session(SyntheticPage('en'))
    result = session.execute({'action':action})
    assert [name for name, _ in session.calls] == ['browser_evaluate', 'browser_snapshot']
    assert result['action_completed'] and result['action'] == action
    assert result['action_result']['ok'] and '### Result' in result['action_result']['text']
    assert result['untrusted_content'] and result['profile_id'] == 'Default'


@pytest.mark.parametrize('action', ['reload', 'forward', 'get_text'])
@pytest.mark.parametrize('mode', ['native', 'legacy_plain', 'legacy_snapshot'])
def test_errors_remain_redacted_and_never_repeat_a_side_effect(action, mode):
    session = synthetic_session(SyntheticPage('en'), mode, error=True)
    result = session.execute({'action':action})
    assert not result['ok'] and result['untrusted_content'] and result['profile_id'] == 'Default'
    assert len(session.calls) == 1
    assert session._secret not in result['text'] and '[redacted]' in result['text']


def test_explicit_tab_selection_precedes_targeted_read_under_profile_lock():
    session = synthetic_session(SyntheticPage('en'))
    session.execute({'action':'get_text', 'tab_id':'3'})
    assert session.calls == [('browser_tabs', {'action':'select', 'index':3}), ('browser_evaluate', {'function':PAGE_TEXT_SCRIPT})]


def test_benchmark_call_and_byte_reductions_and_honest_fallback():
    data = report(iterations=3)
    assert len(data['cases']) == 14
    assert 'not tokenizer output or billed usage' in data['token_estimate']
    for case in data['cases']:
        before, after = case['before'], case['after']
        if case['action'] == 'get_text':
            assert before['mcp_calls'] == after['mcp_calls'] == 1
            assert after['model_result_bytes_utf8'] < before['model_result_bytes_utf8'] * .5
        elif case['server_mode'] == 'legacy_plain':
            assert before['mcp_calls'] == after['mcp_calls'] == 2
        else:
            assert before['mcp_calls'] == 2 and after['mcp_calls'] == 1
            assert after['all_mcp_response_bytes_utf8'] < before['all_mcp_response_bytes_utf8']
        assert before['synthetic_latency_us_p50'] > 0 and after['synthetic_latency_us_p95'] > 0

@pytest.mark.parametrize('action', ['reload', 'forward'])
def test_failed_observation_retains_successful_action_and_never_repeats_it(action):
    session = synthetic_session(SyntheticPage('ru'))
    wire = session.request
    def request(method, params):
        result = wire(method, params)
        if params['name'] == 'browser_snapshot':
            result['isError'] = True
            result['content'][0]['text'] = 'Observation failed'
        return result
    session.request = request
    result = session.execute({'action':action})
    assert not result['ok'] and result['text'] == 'Observation failed'
    assert result['action_completed'] and result['action_result']['ok']
    assert [name for name, _ in session.calls] == ['browser_evaluate', 'browser_snapshot']

@pytest.mark.parametrize('locale', ['en', 'ru'])
def test_get_text_frame_only_page_returns_explicit_empty_scope_without_retry(locale):
    page = SyntheticPage(locale, frame_only=True)
    session = synthetic_session(page)
    result = session.execute({'action':'get_text'})
    parsed = json.JSONDecoder().raw_decode(result['text'].split('### Result\n', 1)[1])[0]
    assert parsed['text'] == '' and parsed['iframe_count'] == 1
    assert parsed['scope'] == 'main_document_rendered_text'
    assert 'snapshot' in parsed['hint'] and 'shadow DOM' in parsed['hint']
    assert result['ok'] and result['untrusted_content']
    assert len(session.calls) == 1 and session.calls[0][0] == 'browser_evaluate'