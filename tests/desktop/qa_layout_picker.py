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
        const context=canvas.getContext('2d');
        const rgba=color=>{context.clearRect(0,0,1,1);context.fillStyle=color;context.fillRect(0,0,1,1);return Array.from(context.getImageData(0,0,1,1).data);};
        const over=(fg,bg)=>fg.slice(0,3).map((v,i)=>v*fg[3]/255+bg[i]*(1-fg[3]/255));
        const luminance=rgb=>rgb.map(v=>v/255).map(v=>v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4).reduce((n,v,i)=>n+v*[0.2126,0.7152,0.0722][i],0);
        const contrast=(a,b)=>{const x=luminance(a),y=luminance(b);return (Math.max(x,y)+0.05)/(Math.min(x,y)+0.05);};
        const popup=rgba(style.backgroundColor);
        const rows=[...el.querySelectorAll('[role="option"]')].map(option=>{
            const s=getComputedStyle(option), background=over(rgba(s.backgroundColor),popup);
            const label=getComputedStyle(option.querySelector('span[lang]') || option);
            const foreground=over(rgba(label.color),background);
            return {text:option.textContent,selected:option.getAttribute('aria-selected'),state:option.dataset.state,
                highlighted:option.hasAttribute('data-highlighted'),background:s.backgroundColor,foreground:label.color,
                compositeBackground:background,compositeForeground:foreground,contrast:contrast(foreground,background),
                opacity:s.opacity,labelOpacity:label.opacity,visibility:label.visibility};
        });
        const tokens=Object.fromEntries(['--field','--card','--text','--foreground','--accent','--ring','--popover','--popover-foreground'].map(key=>[key,getComputedStyle(doc.documentElement).getPropertyValue(key).trim()]));
        const hit=doc.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2);
        return {background:style.backgroundColor,foreground:style.color,rgba:popup,tokens,options:rows,opacity:style.opacity,
            topmost:el.contains(hit),x:rect.x,y:rect.y,right:rect.right,bottom:rect.bottom,
            width:doc.defaultView.innerWidth,height:doc.defaultView.innerHeight};
    }""")
    evidence = {'surface':name, **surface}
    page.screenshot(path=str(fixture / f'language-picker-{name}.png'))
    (fixture / f'language-picker-{name}.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf8')
    assert surface['rgba'][3] == 255, evidence
    assert surface['opacity'] == '1' and surface['topmost'], evidence
    assert surface['x'] >= 0 and surface['y'] >= 0, evidence
    assert surface['right'] <= surface['width'] + 1 and surface['bottom'] <= surface['height'] + 1, evidence
    expect(options.get_by_role('option')).to_have_count(2)
    expect(options.get_by_role('option', name='English', exact=True)).to_be_visible()
    expect(options.get_by_role('option', name='Русский', exact=True)).to_be_visible()
    assert all(row['contrast'] >= 4.5 and row['opacity'] == '1' and row['labelOpacity'] == '1' and row['visibility'] == 'visible' for row in surface['options']), evidence
    assert any(row['state'] == 'checked' for row in surface['options']), evidence
    assert any(row['highlighted'] for row in surface['options']), evidence
    return evidence


def _picker_contrast_cases(page, fixture):
    """Use production defaults first; white-alpha inputs must not become white popups."""
    surfaces = []
    page.add_init_script("window.addEventListener('message',event=>{if(event.origin===location.origin&&event.data?.type==='jarvis:theme'&&event.data.id)window.__qaResolvedTheme={id:event.data.id,mode:event.data.mode};});")
    select_language(page, 'en')
    for theme_id in ('terax-default', 'jarvis-glass'):
        page.evaluate("""async id=>{const store=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});await store.set('themeId',id);await store.save();}""", theme_id)
        page.reload()
        page.get_by_role('button', name='Chat', exact=True).wait_for()
        page.wait_for_function("id=>window.__qaResolvedTheme?.id===id", arg=theme_id)
        for theme in ('dark', 'light'):
            if page.locator('html').get_attribute('data-theme') != theme:
                page.get_by_role('button', name='Toggle Light and Dark Theme', exact=True).click()
            # Reload reinstates production theme-derived colors, without stale test overrides.
            page.reload()
            page.get_by_role('button', name='Chat', exact=True).wait_for()
            expect(page.locator('html')).to_have_attribute('data-theme', theme)
            page.wait_for_function("({id,mode})=>window.__qaResolvedTheme?.id===id&&window.__qaResolvedTheme?.mode===mode", arg={'id':theme_id,'mode':theme})
            for overlay in ('default', 'input-overlay', 'partial-palette'):
                if overlay != 'default':
                    _set_input_overlay(page, 'rgba(255, 255, 255, 0.13)' if theme == 'dark' else 'rgba(0, 0, 0, 0.13)', preserve_popup=overlay != 'partial-palette')
                trigger = page.locator('.sidebar-bottom').get_by_role('combobox')
                _check_open_picker(page, page, trigger, fixture, f'shell-{theme_id}-{theme}-{overlay}', surfaces)
                page.get_by_role('button', name='Settings', exact=True).click()
                dialog = page.locator('.global-settings[role="dialog"]')
                frame = dialog.frame_locator('iframe[src="workbench-settings.html?embedded=1"]')
                frame.locator('.language-picker-trigger').wait_for()
                if overlay != 'default':
                    _set_input_overlay(page, 'rgba(255, 255, 255, 0.13)' if theme == 'dark' else 'rgba(0, 0, 0, 0.13)', preserve_popup=overlay != 'partial-palette')
                header_trigger = dialog.locator('.settings-header-actions').get_by_role('combobox')
                _check_open_picker(page, page, header_trigger, fixture, f'settings-{theme_id}-{theme}-{overlay}', surfaces)
                if overlay != 'default':
                    field = 'rgba(255, 255, 255, 0.13)' if theme == 'dark' else 'rgba(0, 0, 0, 0.13)'
                    frame.locator('html').evaluate("(el,field)=>el.style.setProperty('--field',field)", field)
                _check_open_picker(page, frame, frame.locator('.language-picker-trigger'), fixture, f'embedded-{theme_id}-{theme}-{overlay}', surfaces)
                dialog.get_by_role('button', name='Close Settings', exact=True).click()

    (fixture / 'language-picker-contrast.json').write_text(json.dumps(surfaces, ensure_ascii=False, indent=2), encoding='utf8')
    _picker_locale_retention(page)
    print(json.dumps({'language_picker_surfaces':len(surfaces),
                      'minimum_text_contrast':min(row['contrast'] for surface in surfaces for row in surface['options']),
                      'keyboard_focus_and_en_ru_retention':'passed'}))


def _set_input_overlay(page, field, preserve_popup=True):
    page.frame_locator('iframe[src="workbench.html"]').locator('body').evaluate(
        """(_body,{field,preserve})=>{const root=window.parent.document.documentElement,s=getComputedStyle(root);const colors={input:field};if(preserve){colors.popover=s.getPropertyValue('--popover').trim();colors.popoverForeground=s.getPropertyValue('--popover-foreground').trim();}window.parent.postMessage({type:'jarvis:theme',colors},location.origin);}""", {'field':field,'preserve':preserve_popup})
    page.wait_for_function("""({field,preserve})=>{const root=document.documentElement;return getComputedStyle(root).getPropertyValue('--field').trim()===field&&(preserve||(!root.style.getPropertyValue('--popover')&&!root.style.getPropertyValue('--popover-foreground')));}""", arg={'field':field,'preserve':preserve_popup})


def _check_open_picker(page, context, trigger, fixture, name, surfaces):
    trigger.focus()
    trigger.press('ArrowDown')
    options = context.get_by_role('listbox')
    expect(options.get_by_role('option', name='English', exact=True)).to_be_focused()
    surfaces.append(_picker_surface(page, options, fixture, name + '-selected'))
    options.press('End')
    expect(options.get_by_role('option', name='Русский', exact=True)).to_be_focused()
    surfaces.append(_picker_surface(page, options, fixture, name + '-highlighted'))
    options.press('Escape')
    expect(options).to_have_count(0)
    expect(trigger).to_be_focused()

def _picker_locale_retention(page):
    # Exercise keyboard selection and persistence through each actual picker owner.
    for owner in ('shell', 'settings', 'embedded'):
        if owner != 'shell':
            page.get_by_role('button', name='Settings', exact=True).click()
            dialog = page.locator('.global-settings[role="dialog"]')
            frame = dialog.frame_locator('iframe[src="workbench-settings.html?embedded=1"]')
            expect(frame.locator('html')).to_have_attribute('lang', 'en')
            trigger = (dialog.locator('.settings-header-actions').get_by_role('combobox')
                       if owner == 'settings' else frame.locator('.language-picker-trigger'))
            context = page if owner == 'settings' else frame
        else:
            trigger = page.locator('.sidebar-bottom').get_by_role('combobox')
            context = page
        trigger.focus()
        trigger.press('ArrowDown')
        options = context.get_by_role('listbox')
        options.press('End')
        expect(options.get_by_role('option', name='Русский', exact=True)).to_be_focused()
        options.get_by_role('option', name='Русский', exact=True).press('Enter')
        expect(trigger).to_be_focused()
        expect(page.locator('html')).to_have_attribute('lang', 'ru')
        if owner != 'shell':
            expect(frame.locator('html')).to_have_attribute('lang', 'ru')
        page.reload()
        page.get_by_role('button', name='Чат', exact=True).wait_for()
        expect(page.locator('.sidebar-bottom').get_by_role('combobox')).to_contain_text('Русский')
        select_language(page, 'en')
        page.reload()
        page.get_by_role('button', name='Chat', exact=True).wait_for()
        expect(page.locator('.sidebar-bottom').get_by_role('combobox')).to_contain_text('English')

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
        _picker_contrast_cases(page, fixture)
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
