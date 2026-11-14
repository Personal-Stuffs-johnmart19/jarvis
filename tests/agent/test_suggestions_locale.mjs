import test from "node:test";
import assert from "node:assert/strict";
import { localizeSuggestions } from "../../apps/desktop/src/suggestions.ts";
import { translate } from "../../apps/desktop/src/localization.ts";

test("built-in quick prompts translate both label and inserted command", () => {
  const catalog={builtin:true,items:[["◉ News","Tell me today’s main news with links to sources."]]};
  assert.deepEqual(localizeSuggestions(catalog,text=>translate("en",text)),catalog.items);
  assert.equal(localizeSuggestions(catalog,text=>translate("ru",text))[0][0],"◉ Новости");
  assert.match(localizeSuggestions(catalog,text=>translate("ru",text))[0][1],/главные новости/);
});
test("user-authored quick prompts are never translated, even when their label matches a template", () => {
  const custom={builtin:false,items:[["◉ News","Моя личная команда"]]};
  assert.deepEqual(localizeSuggestions(custom,text=>translate("ru",text)),custom.items);
  assert.deepEqual(localizeSuggestions(custom,text=>translate("en",text)),custom.items);
});
