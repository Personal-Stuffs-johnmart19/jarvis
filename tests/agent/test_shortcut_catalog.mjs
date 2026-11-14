import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { stripTypeScriptTypes } from 'node:module';
const read = path => fs.readFileSync(new URL(path, import.meta.url), 'utf8');

function loadShortcutCatalog(isMac) {
  // Execute both production tables while isolating the native platform detector.
  const sources = ['shortcutDefaults.ts', 'shortcuts.ts'].map(name =>
    stripTypeScriptTypes(read('../../apps/desktop/workbench/src/modules/shortcuts/' + name))
      .replace(/^import[\s\S]*?;\s*/gm, '')
      .replace(/\bexport\s+/g, '')
  ).join('\n');
  return vm.runInNewContext(sources + '\n({SHORTCUTS, SHORTCUT_GROUPS, MAC_DEFAULTS, WIN_LINUX_DEFAULTS})', { IS_MAC: isMac });
}

test('every configurable shortcut has a reachable group and translated presentation metadata', () => {
  const en = JSON.parse(read('../../apps/desktop/src/locales/en.json'));
  const ru = JSON.parse(read('../../apps/desktop/src/locales/ru.json'));
  for (const isMac of [false, true]) {
    const { SHORTCUTS: shortcuts, SHORTCUT_GROUPS: groups, MAC_DEFAULTS, WIN_LINUX_DEFAULTS } = loadShortcutCatalog(isMac);
    const defaults = isMac ? MAC_DEFAULTS : WIN_LINUX_DEFAULTS;
    assert.equal(new Set(shortcuts.map(item => item.id)).size, shortcuts.length);
    assert.deepEqual(Object.keys(defaults).sort(), Array.from(shortcuts, item => item.id).sort());
    for (const shortcut of shortcuts) {
      assert.ok(groups.includes(shortcut.group), `Unreachable shortcut group: ${shortcut.id}`);
      assert.deepEqual(shortcut.defaultBindings, defaults[shortcut.id]);
      assert.ok(Array.isArray(shortcut.defaultBindings), `Missing default bindings: ${shortcut.id}`);
      for (const binding of shortcut.defaultBindings) {
        assert.equal(typeof binding.key, 'string');
        assert.ok(binding.key.length > 0, `Empty default key: ${shortcut.id}`);
        for (const [key, value] of Object.entries(binding)) {
          assert.ok(['key', 'ctrl', 'shift', 'alt', 'meta'].includes(key), `Unknown binding property: ${key}`);
          if (key !== 'key') assert.equal(typeof value, 'boolean');
        }
      }
      if (shortcut.id === 'tab.selectByIndex') continue;
      for (const key of [shortcut.label, shortcut.group]) {
        assert.equal(en[key], key, `Missing English source: ${key}`);
        assert.ok(Object.hasOwn(ru, key), `Missing Russian text: ${key}`);
      }
    }
    assert.equal(new Set(groups).size, groups.length);
    assert.equal(shortcuts.filter(item => item.group === 'Spaces').length, 3);
  }
});
