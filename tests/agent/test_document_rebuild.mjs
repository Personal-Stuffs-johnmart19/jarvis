import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { runInNewContext } from "node:vm";
const require = createRequire(new URL("../../apps/desktop/package.json", import.meta.url));
const { rolldown } = await import(pathToFileURL(require.resolve("rolldown", { paths: [require.resolve("vite")] })).href);
const component = fileURLToPath(new URL("../../apps/desktop/src/EmbeddingPanel.tsx", import.meta.url));
const bundle = await rolldown({ input: component, external: id => id !== component, transform: { jsx: { runtime: "automatic" } } });
const { output } = await bundle.generate({ format: "cjs" });
await bundle.close();
const flush = () => new Promise(done => setImmediate(done));
function panel({settingsError} = {}) {
  const state = [], refs = [], effects = [], calls = [];
  let index = 0, refIndex = 0, initialized = false, resolve, reject;
  const pending = new Promise((done, fail) => { resolve = done; reject = fail; });
  const jsx = (type, props) => ({ type, props });
  const react = {
    useEffect(fn) { if (!initialized) effects.push(fn); },
    useRef(value) { const i = refIndex++; return refs[i] ??= { current: value }; },
    useState(value) { const i = index++; if (!(i in state)) state[i] = value; return [state[i], next => { state[i] = typeof next === "function" ? next(state[i]) : next; }]; },
  };
  const run = async (method, params) => {
    calls.push({ method, params });
    if (method === "services.read") { assert.deepEqual(JSON.parse(JSON.stringify(params)), {name:"models.installed",args:{}}); return []; }
    if (method === "embedding.settings") {
      if (settingsError && params?.chunk_size) throw settingsError;
      return { backend: params?.backend ?? "cpu" };
    }
    if (method === "documents.rebuild") return pending;
    if (method === "embedding.cancel") return {ok:true,cancelled:true};
    throw new Error(`Unexpected call ${method}`);
  };
  const exports = {};
  runInNewContext(output[0].code, { exports, crypto: {randomUUID:()=>"rebuild-test-id"}, require(name) {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
    if (name === "./displayError") return {displayError:value=>value,errorText:value=>typeof value === "string" ? value : value.message};
    if (name === "./DocumentFilePicker") return { DocumentFilePicker() {} };
    if (name === "./localization") return { t: (value, ...args) => (value === "Failed" ? "translated-provider-message" : value).replace(/\{(\d+)\}/g, (_, i) => String(args[i])) };
    throw new Error(`Unexpected dependency ${name}`);
  } });
  function render() { index = refIndex = 0; const tree = exports.EmbeddingPanel({ run }); if (!initialized) { initialized = true; effects.forEach(fn => fn()); } return tree; }
  return { render, calls, resolve, reject };
}
function elements(tree, result = []) {
  if (!tree || typeof tree !== "object") return result;
  result.push(tree);
  for (const child of [tree.props?.children].flat(Infinity)) elements(child, result);
  return result;
}
function button(tree, label) { return elements(tree).find(node => node.type === "button" && node.props.children === label); }
function text(tree) { return typeof tree === "string" || typeof tree === "number" ? String(tree) : tree && typeof tree === "object" ? [tree.props?.children].flat(Infinity).map(text).join("") : ""; }

test("rebuild confirmation cancellation dispatches no rebuild or embedding", async () => {
  const ui = panel(); ui.render(); await flush();
  button(ui.render(), "Rebuild document indexes").props.onClick();
  assert.ok(button(ui.render(), "Confirm index rebuild"));
  button(ui.render(), "Cancel").props.onClick();
  assert.equal(button(ui.render(), "Confirm index rebuild"), undefined);
  assert.deepEqual(ui.calls.map(call => call.method), ["services.read", "embedding.settings"]);
});

test("approved rebuild uses selected backend once, reports pending and partial failures", async () => {
  const ui = panel(); ui.render(); await flush();
  const select = elements(ui.render()).find(node => node.type === "select" && node.props["aria-label"] === "Embedding mode");
  select.props.onChange({ target: { value: "local" } }); await flush();
  button(ui.render(), "Rebuild document indexes").props.onClick();
  const confirm = button(ui.render(), "Confirm index rebuild");
  confirm.props.onClick(); confirm.props.onClick();
  const calls = ui.calls.filter(call => call.method === "documents.rebuild");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].params.backend, "local");
  const pending = ui.render();
  assert.ok(elements(pending).some(node => node.props?.role === "status" && text(node) === "Rebuilding document indexes…"));
  assert.equal(button(pending, "Rebuild document indexes").props.disabled, true);
  ui.resolve({ rebuilt: [{ document_id: "good", model: "new", dimensions: 4 }], failed: [{ document_id: "old-index-kept", error: "provider offline" }] });
  await flush();
  const done = ui.render();
  assert.ok(!text(done).includes("Rebuilding document indexes…"));
  const alert = elements(done).find(node => node.props?.role === "alert");
  assert.match(text(alert), /Indexes rebuilt: 1\. Failed: 1\./);
  assert.match(text(alert), /old-index-kept: provider offline/);
  assert.equal(button(done, "Rebuild document indexes").props.disabled, false);
});


test("running rebuild cancellation targets its owned request once and reports preserved indexes", async () => {
  const ui = panel(); ui.render(); await flush();
  button(ui.render(), "Rebuild document indexes").props.onClick();
  button(ui.render(), "Confirm index rebuild").props.onClick();
  button(ui.render(), "Cancel rebuild").props.onClick(); await flush();
  assert.equal(button(ui.render(), "Cancel rebuild").props.disabled, true);
  const call = ui.calls.find(call => call.method === "embedding.cancel");
  assert.equal(call.params.request_id, ui.calls.find(call => call.method === "documents.rebuild").params.request_id);
  ui.resolve({cancelled:true,rebuilt:[],failed:[]}); await flush();
  assert.match(text(ui.render()), /interrupted documents retain their previous index/);
  assert.equal(button(ui.render(), "Rebuild document indexes").props.disabled, false);
});


test("provider errors that collide with catalog keys remain verbatim", async () => {
  const ui = panel(); ui.render(); await flush();
  button(ui.render(), "Rebuild document indexes").props.onClick();
  button(ui.render(), "Confirm index rebuild").props.onClick();
  ui.reject("Failed"); await flush();
  assert.ok(elements(ui.render()).some(node => node.props?.role === "alert" && text(node) === "Failed"));
  assert.ok(!text(ui.render()).includes("translated-provider-message"));
});


test("chunk controls validate before dispatch and save without starting embeddings", async () => {
  const ui = panel(); ui.render(); await flush();
  const input = min => elements(ui.render()).find(node => node.type === "input" && node.props.min === min);
  input("256").props.onChange({target:{value:"256"}});
  input("0").props.onChange({target:{value:"256"}});
  button(ui.render(), "Save chunk settings").props.onClick(); await flush();
  assert.match(text(ui.render()), /Enter a chunk size/);
  assert.equal(ui.calls.filter(call=>call.method === "embedding.settings").length,1);
  input("0").props.onChange({target:{value:"32"}});
  button(ui.render(), "Save chunk settings").props.onClick(); await flush();
  const saved = ui.calls.filter(call=>call.method === "embedding.settings");
  assert.deepEqual(JSON.parse(JSON.stringify(saved[1].params)), {chunk_size:256,chunk_overlap:32});
  assert.ok(!ui.calls.some(call=>call.method === "documents.rebuild" || call.method === "embedding.run"));
  assert.match(text(ui.render()), /Chunk settings saved/);
});


test("failed chunk settings save shows raw error and releases busy state", async () => {
  const ui = panel({settingsError:"Failed"}); ui.render(); await flush();
  button(ui.render(), "Save chunk settings").props.onClick(); await flush();
  assert.ok(elements(ui.render()).some(node=>node.props?.role === "alert" && text(node) === "Failed"));
  assert.equal(elements(ui.render()).find(node=>node.type === "fieldset").props.disabled, false);
  assert.ok(!text(ui.render()).includes("Chunk settings saved"));
});
