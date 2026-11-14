import * as native from "@tauri-apps/api/window";
export * from "@tauri-apps/api/window";
function host() {
  if (window.parent === window) return native;
  return (
    window.parent as unknown as { __JARVIS_NATIVE__: { window: typeof native } }
  ).__JARVIS_NATIVE__.window;
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

export const getCurrentWindow: typeof native.getCurrentWindow = new Proxy(
  native.getCurrentWindow,
  {
    apply: (_target, _this, args) =>
      owned(Reflect.apply(host().getCurrentWindow, undefined, args)),
    construct: (_target, args) =>
      owned(Reflect.construct(host().getCurrentWindow, args), false),
  },
);
export const getAllWindows: typeof native.getAllWindows = new Proxy(
  native.getAllWindows,
  {
    apply: (_target, _this, args) =>
      Reflect.apply(host().getAllWindows, undefined, args),
    construct: (_target, args) =>
      owned(Reflect.construct(host().getAllWindows, args), false),
  },
);
export const Window: typeof native.Window = new Proxy(native.Window, {
  apply: (_target, _this, args) =>
    Reflect.apply(host().Window, undefined, args),
  construct: (_target, args) =>
    owned(Reflect.construct(host().Window, args), false),
});
