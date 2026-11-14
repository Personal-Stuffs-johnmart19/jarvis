"""Layout and accessible language-picker coverage in the own hidden native app."""
import json
import os

from playwright.sync_api import expect
from qa_locale import select_language


def _glass_palette(page, theme):
    field = 'rgba(10, 22, 45, 0.65)' if theme == 'dark' else 'rgba(244, 248, 255, 0.72)'
    page.frame_locator('iframe[src="workbench.html"]').locator('body').evaluate(
        """(_body, field) => window.parent.postMessage({type:'jarvis:theme',colors:{input:field}},location.origin)""", field)
    page.wait_for_function("field => getComputedStyle(document.documentElement).getPropertyValue('--field').trim() === field", arg=field, polling=50)
    return field


def _picker_surface(page, options, fixture, name):
    surface = options.evaluate("""el => {
        const style=getComputedStyle(el), rect=el.getBoundingClientRect(), doc=el.ownerDocument;
        const canvas=doc.createElement('canvas'); canvas.width=canvas.height=1;
        const context=canvas.getContext('2d'); context.fillStyle=style.backgroundColor; context.fillRect(0,0,1,1);
        const rgba=Array.from(context.getImageData(0,0,1,1).data);
        const field=getComputedStyle(doc.documentElement).getPropertyValue('--field').trim();
        context.clearRect(0,0,1,1); context.fillStyle=field; context.fillRect(0,0,1,1);
        const sourceRgba=Array.from(context.getImageData(0,0,1,1).data);
        const hit=doc.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2);
        return {background:style.backgroundColor,foreground:style.color,rgba,sourceRgba,field,opacity:style.opacity,z:style.zIndex,
            topmost:el.contains(hit), x:rect.x,y:rect.y,right:rect.right,bottom:rect.bottom,
            width:doc.defaultView.innerWidth,height:doc.defaultView.innerHeight};
    }""")
    assert surface['rgba'][3] == 255, surface
    assert 0 < surface['sourceRgba'][3] < 255, surface
    assert all(abs(a-b) <= 1 for a,b in zip(surface['rgba'][:3],surface['sourceRgba'][:3])), surface
    assert surface['opacity'] == '1' and surface['topmost'], surface
    assert surface['foreground'] != surface['background'], surface
    assert surface['x'] >= 0 and surface['y'] >= 0, surface
    assert surface['right'] <= surface['width'] + 1 and surface['bottom'] <= surface['height'] + 1, surface
    expect(options.get_by_role('option')).to_have_count(2)
    expect(options.get_by_role('option', name='English', exact=True)).to_be_visible()
    expect(options.get_by_role('option', name='Русский', exact=True)).to_be_visible()
    page.screenshot(path=str(fixture / f'language-picker-{name}.png'))
    return {'surface':name, **surface}


def _picker_basics(page, fixture):
    surfaces = []
    sidebar = page.locator('.sidebar-bottom')
    expect(sidebar.locator(':scope > svg')).to_have_count(0)
    expect(sidebar.get_by_text('Camera is off by default.', exact=True)).to_have_count(0)
    trigger = sidebar.get_by_role('combobox')
    trigger.focus()
    trigger.press('ArrowDown')
    options = page.get_by_role('listbox')
    expect(options.get_by_role('option')).to_have_count(2)
    expect(options.get_by_role('option', name='English', exact=True)).to_be_focused()
    page.keyboard.press('End')
    expect(options.get_by_role('option', name='Русский', exact=True)).to_be_focused()
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
        _glass_palette(page, theme)
        trigger.click()
        surfaces.append(_picker_surface(page, options, fixture, f'shell-{theme}'))
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
    for theme in ('dark', 'light'):
        frame.get_by_role('button', name=theme.title(), exact=True).click()
        expect(page.locator('html')).to_have_attribute('data-theme', theme)
        field = _glass_palette(page, theme)
        header_trigger.focus()
        header_trigger.press('ArrowDown')
        header_options = page.get_by_role('listbox')
        surfaces.append(_picker_surface(page, header_options, fixture, f'settings-{theme}'))
        header_options.press('Escape')
        expect(dialog).to_be_visible()
        expect(header_trigger).to_be_focused()
        frame.locator('html').evaluate("(el,field)=>{el.style.setProperty('--field',field);el.style.setProperty('--card',field)}", field)
        # Keep the focus locator stable when selecting a language relabels it.
        embedded_trigger = frame.locator('.language-picker-trigger')
        embedded_trigger.focus()
        embedded_trigger.press('ArrowDown')
        embedded_options = frame.get_by_role('listbox')
        surfaces.append(_picker_surface(page, embedded_options, fixture, f'embedded-{theme}'))
        embedded_options.press('Escape')
        expect(dialog).to_be_visible()
        expect(embedded_trigger).to_be_focused()
    embedded_trigger.press('ArrowDown')
    frame.get_by_role('listbox').press('End')
    russian_option = frame.get_by_role('option', name='Русский', exact=True)
    expect(russian_option).to_be_focused()
    russian_option.press('Enter')
    expect(page.locator('html')).to_have_attribute('lang', 'ru')
    expect(frame.locator('html')).to_have_attribute('lang', 'ru')
    expect(embedded_trigger).to_be_focused()
    select_language(page, 'en')
    dialog.get_by_role('button', name='Close Settings', exact=True).click()
    print(json.dumps({'language_picker_surfaces':surfaces,'keyboard_selection_and_focus':'passed'}))


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
