"""Change language through the accessible picker without bypassing modal inertness."""
from playwright.sync_api import expect


def select_language(page, locale):
    label = {'en': 'English', 'ru': 'Русский'}[locale]
    settings = page.locator('.global-settings[role="dialog"]')
    opened_drawer = False
    if settings.count() and settings.is_visible():
        trigger = settings.locator('.settings-header-actions .language-picker-trigger')
    else:
        trigger = page.locator('.sidebar-bottom .language-picker-trigger')
        if not trigger.is_visible():
            page.locator('.nav-toggle').click()
            opened_drawer = True
    trigger.click()
    page.get_by_role('option', name=label, exact=True).click()
    expect(page.locator('html')).to_have_attribute('lang', locale)
    expect(page.get_by_role('listbox')).to_have_count(0)
    if opened_drawer:
        page.keyboard.press('Escape')
