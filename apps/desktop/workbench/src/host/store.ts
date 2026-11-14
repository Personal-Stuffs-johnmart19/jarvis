import * as native from "@tauri-apps/plugin-store";
export * from "@tauri-apps/plugin-store";
function host() {
  if (window.parent === window) return native;
  return (
    window.parent as unknown as { __JARVIS_NATIVE__: { store: typeof native } }
  ).__JARVIS_NATIVE__.store;
}
const bridge = () =>
  (
    window.parent as unknown as {
      __JARVIS_NATIVE__?: {
        owned: <T extends object>(
          owner: Window,
          value: T,
          release?: boolean,
        ) => T;
      };
    }
  ).__JARVIS_NATIVE__;
function owned<T extends object>(value: T, release = false): T {
  return bridge()?.owned(window, value, release) ?? value;
}

export const load: typeof native.load = new Proxy(native.load, {
  apply: (_target, _this, args) => Reflect.apply(host().load, undefined, args),
  construct: (_target, args) =>
    owned(Reflect.construct(host().load, args), true),
});
export const Store: typeof native.Store = new Proxy(native.Store, {
  apply: (_target, _this, args) => Reflect.apply(host().Store, undefined, args),
  construct: (_target, args) =>
    owned(Reflect.construct(host().Store, args), true),
});
export const LazyStore: typeof native.LazyStore = new Proxy(native.LazyStore, {
  apply: (_target, _this, args) =>
    Reflect.apply(host().LazyStore, undefined, args),
  construct: (_target, args) =>
    owned(Reflect.construct(host().LazyStore, args), true),
});
