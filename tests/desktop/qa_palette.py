"""Focused palette localization in the own hidden native QA profile."""
from qa_locale import select_language
import json

from playwright.sync_api import expect


def run_palette(page, fixture):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.get_by_role('button', name='Files & Tools', exact=True).click()
    frame = page.frame_locator('iframe[src="workbench.html"]')
    frame.locator('[data-files-surface]').wait_for()
    frame.locator('[data-unified-chat]').wait_for(state='attached')
    target = fixture / 'palette-unsaved.txt'
    target.write_text('PALETTE_DISK', encoding='utf-8')
    page.evaluate("path=>window.__JARVIS_NATIVE__.event.emit('terax:open-file',[path])",
                  str(target).replace('\\', '/'))
    editor = frame.locator('.cm-content').filter(has_text='PALETTE_DISK')
    try:
        editor.wait_for(timeout=10000)
    except Exception:
        print('PALETTE_EDITOR_OPEN_FAILURE', frame.locator('body').inner_text()[-2400:], flush=True)
        page.screenshot(path=str(fixture / 'palette-failure.png'))
        raise
    editor.fill('PALETTE_UNSAVED')
    editor = frame.locator('.cm-content').filter(has_text='PALETTE_UNSAVED')
    editor.press('Control+p')
    palette = frame.get_by_role('dialog', name='Command Palette', exact=True)
    palette.wait_for()
    search = palette.get_by_role('combobox')
    expect(palette.get_by_text('Open settings', exact=True)).to_be_visible()
    expect(palette.get_by_text('No terminal tab', exact=True).first).to_be_visible()
    search.fill('New terminal')
    expect(palette.get_by_text('New terminal', exact=True)).to_be_visible()
    # Changing the shell language must update the already-open embedded palette.
    select_language(page, 'ru')
    palette = frame.get_by_role('dialog', name='Палитра команд', exact=True)
    search = palette.get_by_role('combobox')
    expect(search).to_have_value('New terminal')
    expect(palette.get_by_text('Новый терминал', exact=True)).to_be_visible()
    search.fill('')
    expect(palette.get_by_text('Нет вкладки терминала', exact=True).first).to_be_visible()
    expect(palette.get_by_text('Общие настройки', exact=True)).to_be_visible()
    search.fill('настройки')
    expect(palette.get_by_text('Открыть настройки', exact=True)).to_be_visible()
    page.screenshot(path=str(fixture / 'palette-ru.png'))
    search.fill('')
    select_language(page, 'en')
    palette = frame.get_by_role('dialog', name='Command Palette', exact=True)
    expect(palette.get_by_text('Open settings', exact=True)).to_be_visible()
    expect(palette.get_by_text('No terminal tab', exact=True).first).to_be_visible()
    palette.get_by_role('combobox').press('Escape')
    expect(palette).to_have_count(0)
    expect(editor).to_have_text('PALETTE_UNSAVED')
    assert target.read_text(encoding='utf-8') == 'PALETTE_DISK'
    assert not errors, errors
    print(json.dumps({'palette_live_locale': True, 'localized_search': True,
                      'english_search_in_russian': True, 'disabled_reasons': True,
                      'unsaved_buffer_preserved': True, 'page_errors': errors}))
