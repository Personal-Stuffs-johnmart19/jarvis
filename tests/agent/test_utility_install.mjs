import test from "node:test";
import assert from "node:assert/strict";
import { createRequire, registerHooks } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { runInNewContext } from "node:vm";
const hook = registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier === "./localization" && /\/(utilityPresentation|displayError)\.ts$/.test(context.parentURL ?? ""))
    return nextResolve("./localization.ts", context);
  return nextResolve(specifier, context);
} });
const presentation = await import("../../apps/desktop/src/utilityPresentation.ts");
const displayErrors = await import("../../apps/desktop/src/displayError.ts");
hook.deregister();
const require = createRequire(new URL("../../apps/desktop/package.json", import.meta.url));
const { rolldown } = await import(pathToFileURL(require.resolve("rolldown", { paths: [require.resolve("vite")] })).href);
const component = fileURLToPath(new URL("../../apps/desktop/src/UtilitiesPanel.tsx", import.meta.url));
const bundle = await rolldown({ input: component, external: id => id !== component, transform: { jsx: { runtime: "automatic" } } });
const { output } = await bundle.generate({ format: "cjs" });
const code = output[0].code;
await bundle.close();
const deferred = () => { let resolve, reject; const promise = new Promise((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject }; };
const flush = () => new Promise(done => setImmediate(done));

function panel(cancelError = false, locale = "en", failures = {}) {
  const utility = { category: "Development", description: "Rust package manager", source: "not installed", direct: false, builtin: false, id: "cargo", install_package: "Rustlang.Rustup", installer_available: true, available: false, path: null };
  const state = [[utility], "", false, "", utility, "user", true];
  const refs = [];
  let stateIndex = 0, refIndex = 0;
  const install = deferred(), calls = [];
  const jsx = (type, props) => ({ type, props });
  const react = {
    useEffect() {},
    useRef(value) { const index = refIndex++; return refs[index] ??= { current: value }; },
    useState(value) {
      const index = stateIndex++;
      if (!(index in state)) state[index] = value;
      return [state[index], next => { state[index] = typeof next === "function" ? next(state[index]) : next; }];
    },
  };
  const invoke = async (method, args) => {
    calls.push({ method, args });
    if (failures[method]) throw failures[method];
    if (method === "utility_install") return install.promise;
    if (method === "utility_install_cancel" && cancelError) throw new Error("cancel failed");
    return method === "utility_inventory" ? [utility] : undefined;
  };
  const exports = {};
  runInNewContext(code, { exports, crypto: { randomUUID: () => "request-1" }, require(name) {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
    if (name === "@tauri-apps/api/core") return { invoke };
    if (name === "./localization") return { t: value => value };
    if (name === "./i18n") return { useLocale: () => locale };
    if (name === "./displayError") return displayErrors;
    if (name === "./utilityPresentation") return presentation;
    throw new Error(`Unexpected dependency ${name}`);
  } });
  function render() { stateIndex = refIndex = 0; return exports.UtilitiesPanel(); }
  return { render, state, calls, install, setLocale(value) { locale = value; } };
}

function elements(tree, inheritedDisabled = false, result = []) {
  if (!tree || typeof tree !== "object") return result;
  const disabled = inheritedDisabled || (tree.type === "fieldset" && tree.props.disabled);
  result.push({ ...tree, disabled });
  for (const child of [tree.props?.children].flat(Infinity)) elements(child, disabled, result);
  return result;
}
function renderedText(tree, predicate) {
  const node = elements(tree).find(predicate);
  assert.ok(node, "expected rendered element");
  const text = value => Array.isArray(value) ? value.map(text).join("") : value && typeof value === "object" ? text(value.props?.children) : value == null ? "" : String(value);
  return text(node.props.children);
}
const alertText = ui => renderedText(ui.render(), node => node.props?.role === "alert");
const outputText = ui => renderedText(ui.render(), node => node.type === "pre");
function button(tree, label) {
  return elements(tree).find(node => node.type === "button" && node.props.children === label && !node.disabled);
}

test("active installation has an enabled scoped Cancel outside the disabled confirmation form", async () => {
  const ui = panel();
  const start = button(ui.render(), "Install package");
  start.props.onClick();
  start.props.onClick();
  assert.equal(ui.calls.filter(call => call.method === "utility_install").length, 1, "synchronous guard prevents duplicate install dispatch");
  assert.equal(ui.calls[0].args.requestId, "request-1");
  button(ui.render(), "Cancel").props.onClick();
  await flush();
  assert.equal(ui.calls.find(call => call.method === "utility_install_cancel").args.requestId, "request-1");
  const cancelling = elements(ui.render()).find(node => node.type === "button" && node.props.children === "Cancel" && !node.disabled);
  assert.equal(cancelling.props.disabled, true);
  ui.install.resolve({ cancelled: true, success: false, available: false, timed_out: false, exit_code: null, stdout: "partial", stderr: "" });
  await flush(); await flush();
  assert.equal(ui.state[7], false);
  assert.match(alertText(ui), /Detached Windows installer services may still be running/);
  assert.match(outputText(ui), /Package installation cancelled/);
  assert.ok(ui.calls.some(call => call.method === "utility_inventory"));
});

test("failed cancellation reports the error and allows a deliberate retry without restarting installation", async () => {
  const ui = panel(true);
  button(ui.render(), "Install package").props.onClick();
  button(ui.render(), "Cancel").props.onClick();
  await flush();
  assert.match(alertText(ui), /cancel failed/);
  assert.equal(ui.state[8], false);
  button(ui.render(), "Cancel").props.onClick();
  await flush();
  assert.equal(ui.calls.filter(call => call.method === "utility_install_cancel").length, 2);
  assert.equal(ui.calls.filter(call => call.method === "utility_install").length, 1);
  ui.install.resolve({ cancelled: false, success: true, available: true, timed_out: false, exit_code: 0, stdout: "done", stderr: "" });
  await flush(); await flush();
  assert.match(outputText(ui), /Package installation completed/);
});

test("native owned installer failures render in the live locale after JSON transport", async () => {
  for (const message of [
    "Invalid utility installation request ID", "Utility installer is shutting down",
    "Another utility installation is running", "No managed installer for this utility",
    "Review the package and accept its agreements before installation", "Invalid installation scope",
    "Installation requires Windows App Installer (WinGet)", "Package installation is disabled in ordinary QA",
  ]) {
    const error = JSON.parse(JSON.stringify({ source: "jarvis", message, values: [] }));
    const ui = panel();
    button(ui.render(), "Install package").props.onClick();
    ui.install.reject(error);
    await flush();
    assert.equal(alertText(ui), message);
    ui.setLocale("ru");
    assert.notEqual(alertText(ui), message, `Russian translation: ${message}`);
    assert.match(alertText(ui), /[А-Яа-яЁё]/);
    ui.setLocale("en");
    assert.equal(alertText(ui), message);
  }
});

test("owned cancellation and inventory rejections keep their provenance", async () => {
  const error = { source: "jarvis", message: "Invalid utility installation request ID", values: [] };
  const cancel = panel(false, "ru", { utility_install_cancel: error });
  button(cancel.render(), "Install package").props.onClick();
  button(cancel.render(), "Cancel").props.onClick();
  await flush();
  assert.notEqual(alertText(cancel), error.message);
  assert.equal(cancel.state[8], false);
  cancel.install.resolve({ success: true, available: true, cancelled: false, exit_code: 0, stdout: "", stderr: "" });
  await flush(); await flush();
  const inventory = panel(false, "ru", { utility_inventory: error });
  button(inventory.render(), "Refresh").props.onClick();
  await flush();
  assert.notEqual(alertText(inventory), error.message);
});

test("unowned OS failures and WinGet output retain raw catalog collisions and braces", async () => {
  const raw = "Another utility installation is running";
  const ui = panel(false, "ru");
  button(ui.render(), "Install package").props.onClick();
  ui.install.reject(raw);
  await flush();
  assert.equal(alertText(ui), raw);
  const output = panel(false, "ru");
  button(output.render(), "Install package").props.onClick();
  output.install.resolve({ success: false, available: false, cancelled: false, timed_out: false,
    exit_code: 1, stdout: `${raw} {0}`, stderr: "C:/raw/WinGet: Invalid installation scope" });
  await flush(); await flush();
  assert.ok(outputText(output).includes(`${raw} {0}`));
  assert.ok(outputText(output).includes("C:/raw/WinGet: Invalid installation scope"));
});
