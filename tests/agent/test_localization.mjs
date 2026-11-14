import test from "node:test";
import assert from "node:assert/strict";
import en from "../../apps/desktop/src/locales/en.json" with { type: "json" };
import ru from "../../apps/desktop/src/locales/ru.json" with { type: "json" };
import backendEn from "../../local_mode/locales/en.json" with { type: "json" };
import backendRu from "../../local_mode/locales/ru.json" with { type: "json" };
import { translate, getLocale, setLocale, t, localeKey } from "../../apps/desktop/src/localization.ts";

test("locale catalogs preserve English sources, key parity and interpolation", () => {
  assert.deepEqual(Object.keys(en).sort(), Object.keys(ru).sort());
  for (const [key, value] of Object.entries(en)) {
    assert.equal(key, value);
    assert.deepEqual([...value.matchAll(/\{\d+\}/g)].map(x => x[0]).sort(), [...ru[key].matchAll(/\{\d+\}/g)].map(x => x[0]).sort(), key);
  }
  assert.equal(translate("en", "Home"), "Home");
  assert.equal(translate("ru", "Home"), "Главная");
  assert.equal(translate("en", "Device: {0}.", "GPU {1}"), "Device: GPU {1}.");
  assert.equal(translate("ru", "user-supplied/file.ts"), "user-supplied/file.ts");
  for (const name of ["__proto__", "constructor", "toString"]) assert.equal(translate("ru", name), name);
});


test("runtime repair diagnostic catalogs preserve English, Russian and opaque interpolation", () => {
  const keys = [
    "Provide a music search query of 1 to 200 characters.",
    "No ordinary music video was found in the visible YouTube search results.",
    "The browser rejected playback: {0}.",
    "The browser did not complete the action in time. Inspect the tab before retrying.",
    "Could not start the Chrome connection. Check the browser settings.",
    "No matching location found; include a country or region. Ask for clarification rather than repeat this request",
    "No exact matching city and region found; specify the provider's city name, country or region. Ask for clarification rather than repeat this request",
    "Location is ambiguous; specify a country or region. Ask for clarification rather than repeat this request",
    "Ask for a country or a more specific region; do not repeat the same location request"
  ];
  for (const key of keys) {
    assert.equal(backendEn[key], key);
    assert.equal(typeof backendRu[key], "string");
    assert.match(backendRu[key], /[А-Яа-яЁё]/);
    assert.deepEqual([...key.matchAll(/\{\d+\}/g)].map(x => x[0]), [...backendRu[key].matchAll(/\{\d+\}/g)].map(x => x[0]));
    assert.equal(translate("en", key), key);
    assert.equal(translate("ru", key), backendRu[key]);
  }
  assert.equal(translate("ru", "The browser rejected playback: {0}.", "Provider raw {1}"), "Браузер отклонил воспроизведение: Provider raw {1}.");
  assert.equal(translate("ru", "Uncatalogued provider text / private city"), "Uncatalogued provider text / private city");
});

test("English defaults, language selection persists, unsupported locales stay rejected", () => {
  const saved = globalThis.localStorage, win = globalThis.window;
  const values = new Map();
  globalThis.localStorage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  globalThis.window = new EventTarget();
  try {
    assert.equal(getLocale(), "en");
    setLocale("ru");
    assert.equal(values.get(localeKey), "ru");
    assert.equal(t("Home"), "Главная");
    assert.throws(() => setLocale("invalid"));
    values.set(localeKey, "invalid");
    assert.equal(getLocale(), "en");
  } finally { globalThis.localStorage = saved; globalThis.window = win; }
});
