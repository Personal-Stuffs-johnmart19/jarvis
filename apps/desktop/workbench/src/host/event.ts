import * as native from "@tauri-apps/api/event";
export * from "@tauri-apps/api/event";
function host() {
  if (window.parent === window) return native;
  return (
    window.parent as unknown as { __JARVIS_NATIVE__: { event: typeof native } }
  ).__JARVIS_NATIVE__.event;
}
const bridge = () =>
  (
    window.parent as unknown as {
      __JARVIS_NATIVE__?: {
        lease: <T>(owner: Window, value: T) => T;
      };
    }
  ).__JARVIS_NATIVE__;
function lease<T>(value: T): T {
  return bridge()?.lease(window, value) ?? value;
}

export const listen: typeof native.listen = new Proxy(native.listen, {
  apply: (_target, _this, args) =>
    lease(Reflect.apply(host().listen, undefined, args)),
  construct: (_target, args) => Reflect.construct(host().listen, args),
});
export const once: typeof native.once = new Proxy(native.once, {
  apply: (_target, _this, args) =>
    lease(Reflect.apply(host().once, undefined, args)),
  construct: (_target, args) => Reflect.construct(host().once, args),
});
export const emit: typeof native.emit = new Proxy(native.emit, {
  apply: (_target, _this, args) =>
    lease(Reflect.apply(host().emit, undefined, args)),
  construct: (_target, args) => Reflect.construct(host().emit, args),
});
export const emitTo: typeof native.emitTo = new Proxy(native.emitTo, {
  apply: (_target, _this, args) =>
    lease(Reflect.apply(host().emitTo, undefined, args)),
  construct: (_target, args) => Reflect.construct(host().emitTo, args),
});
