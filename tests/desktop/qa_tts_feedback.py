"""TTS error feedback using rejected controls, without synthesis or playback."""
from qa_locale import select_language
import json

from playwright.sync_api import expect


def run_tts_feedback(page, fixture):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.get_by_role('button', name='Settings', exact=True).click()
    dialog = page.locator('.global-settings[role="dialog"]')
    dialog.get_by_role('button', name='Speech & Voice', exact=True).click()
    dialog.get_by_label('TTS synthesis model', exact=True).select_option('supertonic')
    dialog.get_by_label('Automatic speech speed', exact=True).uncheck()
    dialog.get_by_label('Speech speed multiplier', exact=True).fill('0')
    # Native validation rejects before voice persistence, GPU startup or audio.
    dialog.get_by_role('button', name='Listen', exact=True).click()
    status = dialog.locator('.settings-status')
    expect(status).to_contain_text('0 is less than the minimum of 0.5')
    expect(status).not_to_contain_text('Execution failed.')
    raw = status.inner_text()
    select_language(page, 'ru')
    expect(status).to_have_text(raw)
    page.screenshot(path=str(fixture / 'tts-feedback-ru.png'))
    select_language(page, 'en')
    expect(status).to_have_text(raw)
    expect(dialog.get_by_role('button', name='Listen', exact=True)).to_be_enabled()
    # Saving the installed Qwen voice does not synthesize or open audio output.
    dialog.get_by_label('TTS synthesis model', exact=True).select_option('qwen3')
    dialog.get_by_role('button', name='Save', exact=True).click()
    expect(status).to_contain_text('Speech settings saved.')
    select_language(page, 'ru')
    expect(status).to_contain_text('Настройки озвучки сохранены.')
    select_language(page, 'en')
    expect(status).to_contain_text('Speech settings saved.')
    assert not errors, errors
    print(json.dumps({'native_preview_error_visible': True, 'raw_detail_preserved': True,
                      'generic_error_removed': True, 'action_reenabled': True,
                      'success_live_locale': True, 'synthesis_calls': 0, 'page_errors': errors}))
