import test from 'node:test';
import assert from 'node:assert/strict';
import { translate } from '../../apps/desktop/src/localization.ts';
import { localizeCommandItems } from '../../apps/desktop/workbench/src/modules/command-palette/localization.ts';

const translator = locale => (message, ...values) => translate(locale, message, ...values);

test('palette labels follow locale changes without rebuilding source commands or changing user data', () => {
  const name = 'General / C:\\Users\\Back {0}';
  const run = () => {};
  const source = [
    { id: 'settings.open', title: 'Open settings', group: 'General', run },
    {
      id: 'spaces.switch.custom', title: `Switch to ${name}`, group: 'Spaces',
      titleMessage: { message: 'Switch to {0}', values: [name] },
      keywords: [name], disabledReason: 'Current space', run,
    },
  ];
  const russian = localizeCommandItems(source, translator('ru'));
  assert.equal(russian[0].title, 'Открыть настройки');
  assert.equal(russian[0].group, 'General');
  assert.notEqual(translate('ru', 'General'), 'General');
  assert.ok(russian[0].keywords.includes(translate('ru', 'General')));
  assert.ok(russian[0].keywords.includes('Open settings'));
  assert.equal(russian[1].title, `Переключиться на ${name}`);
  assert.equal(russian[1].disabledReason, 'Текущее пространство');
  assert.ok(russian[1].keywords.includes(name));
  assert.equal(russian[1].id, source[1].id);
  assert.equal(russian[1].run, run);
  assert.equal(localizeCommandItems(source, translator('en'))[1].title, source[1].title);
  assert.equal(source[0].title, 'Open settings');
  assert.equal(source[1].disabledReason, 'Current space');
});
