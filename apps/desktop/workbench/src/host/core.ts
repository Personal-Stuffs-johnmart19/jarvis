import { isOwnedError, errorText } from "../../../src/displayError";
import * as native from "@tauri-apps/api/core";
export * from "@tauri-apps/api/core";
function host() {
  if (window.parent === window) return native;
  return (
    window.parent as unknown as { __JARVIS_NATIVE__: { core: typeof native } }
  ).__JARVIS_NATIVE__.core;
}
export const invoke: typeof native.invoke = new Proxy(native.invoke, {
  apply: (_target, _this, args) =>
    Reflect.apply(host().invoke, undefined, args).catch((error: unknown) => { throw isOwnedError(error) ? errorText(error) : error; }),
  construct: (_target, args) => Reflect.construct(host().invoke, args),
});
// Opt in when a consumer stores structured errors and localizes them at render time.
// The legacy invoke adapter above remains string-compatible for existing callers.
export const invokeOwned: typeof native.invoke = new Proxy(native.invoke, {
  apply: (_target, _this, args) => Reflect.apply(host().invoke, undefined, args),
  construct: (_target, args) => Reflect.construct(host().invoke, args),
});
export const Channel: typeof native.Channel = new Proxy(native.Channel, {
  apply: (_target, _this, args) =>
    Reflect.apply(host().Channel, undefined, args),
  construct: (_target, args) => Reflect.construct(host().Channel, args),
});
export const transformCallback: typeof native.transformCallback = new Proxy(
  native.transformCallback,
  {
    apply: (_target, _this, args) =>
      Reflect.apply(host().transformCallback, undefined, args),
    construct: (_target, args) =>
      Reflect.construct(host().transformCallback, args),
  },
);
export const convertFileSrc: typeof native.convertFileSrc = new Proxy(
  native.convertFileSrc,
  {
    apply: (_target, _this, args) =>
      Reflect.apply(host().convertFileSrc, undefined, args),
    construct: (_target, args) =>
      Reflect.construct(host().convertFileSrc, args),
  },
);
export const isTauri: typeof native.isTauri = new Proxy(native.isTauri, {
  apply: (_target, _this, args) =>
    Reflect.apply(host().isTauri, undefined, args),
  construct: (_target, args) => Reflect.construct(host().isTauri, args),
});
export const Resource: typeof native.Resource = new Proxy(native.Resource, {
  apply: (_target, _this, args) =>
    Reflect.apply(host().Resource, undefined, args),
  construct: (_target, args) => Reflect.construct(host().Resource, args),
});
export const addPluginListener: typeof native.addPluginListener = new Proxy(
  native.addPluginListener,
  {
    apply: (_target, _this, args) =>
      Reflect.apply(host().addPluginListener, undefined, args),
    construct: (_target, args) =>
      Reflect.construct(host().addPluginListener, args),
  },
);
