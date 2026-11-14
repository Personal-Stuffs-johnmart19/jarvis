"""Layout and accessible language-picker coverage in the own hidden native app."""
import json
import os

from playwright.sync_api import expect
from qa_locale import select_language


def _picker_basics(page, fixture):
    sidebar = page.locator('.sidebar-bottom')
    expect(sidebar.locator(':scope > svg')).to_have_count(0)
    expect(sidebar.get_by_text('Camera is off by default.', exact=True)).to_have_count(0)
    trigger = sidebar.get_by_role('combobox')
    trigger.focus()
    trigger.press('ArrowDown')
    options = page.get_by_role('listbox')
    expect(options.get_by_role('option')).to_have_count(2)
    page.keyboard.press('End')
    page.keyboard.press('Enter')
    expect(page.locator('html')).to_have_attribute('lang', 'ru')
    expect(trigger).to_be_focused()
    page.reload()
    page.get_by_role('button', name='Чат', exact=True).wait_for()
    expect(sidebar.get_by_role('combobox')).to_contain_text('Русский')
    select_language(page, 'en')
    trigger = sidebar.get_by_role('combobox')
    for theme in ('dark', 'light'):
        if page.locator('html').get_attribute('data-theme') != theme:
            page.get_by_role('button', name='Toggle Light and Dark Theme', exact=True).click()
        expect(page.locator('html')).to_have_attribute('data-theme', theme)
        trigger.click()
        box = options.bounding_box()
        viewport = page.evaluate('({width:innerWidth,height:innerHeight})')
        assert box and box['x'] >= 0 and box['y'] >= 0, box
        assert box['x'] + box['width'] <= viewport['width'], box
        colors = options.evaluate('(el)=>({fg:getComputedStyle(el).color,bg:getComputedStyle(el).backgroundColor})')
        assert colors['fg'] != colors['bg'], colors
        page.screenshot(path=str(fixture / f'language-picker-{theme}.png'))
        options.press('Escape')
        expect(options).to_have_count(0)
        expect(trigger).to_be_focused()
    page.get_by_role('button', name='Settings', exact=True).click()
    dialog = page.locator('.global-settings[role="dialog"]')
    select_language(page, 'ru')
    frame = dialog.frame_locator('iframe[src="workbench-settings.html?embedded=1"]')
    expect(frame.locator('html')).to_have_attribute('lang', 'ru')
    header_trigger = dialog.locator('.settings-header-actions').get_by_role('combobox')
    header_trigger.click()
    page.get_by_role('listbox').press('Escape')
    expect(dialog).to_be_visible()
    expect(header_trigger).to_be_focused()
    select_language(page, 'en')
    dialog.get_by_role('button', name='Close Settings', exact=True).click()


def run_layout_picker(page, fixture):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    basics_checked = os.environ.get('JARVIS_QA_LAYOUT_GEOMETRY_ONLY') != '1'
    if basics_checked:
        _picker_basics(page, fixture)
    rows = []
    for zoom in (1, 2, 4):
        page.evaluate('factor=>window.__JARVIS_NATIVE__.webview.getCurrentWebview().setZoom(factor)', zoom)
        page.locator('.footer').scroll_into_view_if_needed()
        geometry = page.locator('.footer').evaluate('''footer => {
          const rect = el => {const r=el.getBoundingClientRect(); return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
          const overlap = (a,b) => Math.min(a.right,b.right)-Math.max(a.x,b.x)>0.5 && Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y)>0.5;
          const buttons=[...footer.querySelectorAll('button')];
          const boxes=buttons.map(rect);
          const collisions=[];
          boxes.forEach((box,i)=>boxes.slice(i+1).forEach((other,j)=>{if(overlap(box,other))collisions.push([i,i+j+1]);}));
          const internal=[];
          buttons.forEach((button,i)=>{
            const parts=[...button.children].map(rect);
            [...button.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE&&n.textContent.trim()).forEach(n=>{const range=document.createRange();range.selectNode(n);parts.push(rect({getBoundingClientRect:()=>range.getBoundingClientRect()}));});
            parts.forEach((box,j)=>parts.slice(j+1).forEach(other=>{if(overlap(box,other))internal.push(i);}));
          });
          return {width:innerWidth,footer:rect(footer),boxes,collisions,internal,overflow:boxes.some(b=>b.x<0||b.right>innerWidth+1)};
        }''')
        if geometry['collisions'] or geometry['internal'] or geometry['overflow']:
            page.screenshot(path=str(fixture / 'footer-overflow.png'))
            raise AssertionError(geometry)
        select_language(page, 'ru')
        select_language(page, 'en')
        rows.append({'zoom': zoom, **geometry})
    page.evaluate('window.__JARVIS_NATIVE__.webview.getCurrentWebview().setZoom(1)')
    assert not errors, errors
    print(json.dumps({'picker_checks': 'passed' if basics_checked else 'reused_earlier_evidence',
                      'layout': rows, 'page_errors': errors}))
