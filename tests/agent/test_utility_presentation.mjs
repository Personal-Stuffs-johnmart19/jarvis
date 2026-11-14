import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { registerHooks } from "node:module";
import en from "../../apps/desktop/src/locales/en.json" with { type: "json" };
import ru from "../../apps/desktop/src/locales/ru.json" with { type: "json" };
// Node's native TS runner requires an extension; the app uses bundler resolution.
const hook = registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier === "./localization" && context.parentURL?.endsWith("/utilityPresentation.ts"))
    return nextResolve("./localization.ts", context);
  return nextResolve(specifier, context);
} });
const { presentUtility, utilityMatchesQuery } = await import("../../apps/desktop/src/utilityPresentation.ts");
hook.deregister();

const item = { id: "rg", category: "Search", description: "ripgrep regex and file search",
  source: "built-in Rust", path: "Search", install_package: "Version control",
  stdout: "Search\nVersion control", stderr: "not installed" };

test("utility metadata searches both languages, source labels and stable IDs", () => {
  for (const query of ["RG", "regex", "Поиск", "регулярных", "Встроенная", "built-in Rust"])
    assert.equal(utilityMatchesQuery(item, query), true, query);
  assert.equal(utilityMatchesQuery(item, "not-a-utility"), false);
  assert.equal(utilityMatchesQuery(item, ""), true);
  assert.equal(presentUtility(item, "en").description, item.description);
  assert.equal(presentUtility(item, "ru").description, ru[item.description]);
});

test("presentation preserves raw paths, identifiers, installer data and output", () => {
  const shown = presentUtility(item, "ru");
  assert.notEqual(shown.category, item.category);
  for (const key of ["id", "path", "install_package", "stdout", "stderr"])
    assert.equal(shown[key], item[key], key);
  assert.equal(item.category, "Search");
  const future = { id: "future", category: "New category", description: "New description", source: "New source" };
  assert.deepEqual(presentUtility(future, "ru"), future);
  assert.equal(utilityMatchesQuery(future, "new description"), true);
});

test("both catalogs cover the native utility descriptions, categories and discovery sources", async () => {
  const native = await readFile(new URL("../../apps/desktop/src-tauri/workbench-core/src/modules/utilities.rs", import.meta.url), "utf8");
  const tools = native.split("const TOOLS:")[1].split("];", 1)[0];
  const entries = [...tools.matchAll(/\(\s*"([^"]+)",\s*"([^"]+)",\s*"([^"]+)"\s*,?\s*\)/g)];
  assert.equal(entries.length, 43, "parser must cover the complete native TOOLS table");
  const sources = ["jarvis-venv", "jarvis-managed Rust", "PATH", "installed PATH", "shared-runtime", "Git for Windows", "runtime module", "built-in Rust", "not installed"];
  const keys = new Set([...entries.flatMap(entry => [entry[2], entry[3]]), ...sources]);
  for (const source of sources) assert.ok(native.includes(`"${source}"`), source);
  for (const key of keys) {
    assert.equal(en[key], key, `English metadata: ${key}`);
    assert.equal(typeof ru[key], "string", `Russian metadata: ${key}`);
    assert.ok(ru[key].length, key);
  }
});
