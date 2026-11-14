import * as native from "@tauri-apps/api/webviewWindow";
export * from "@tauri-apps/api/webviewWindow";
function host() {
  if (window.parent === window) return native;
  return (
    window.parent as unknown as {
      __JARVIS_NATIVE__: { webviewWindow: typeof native };
    }
  ).__JARVIS_NATIVE__.webviewWindow;
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

export const getCurrentWebviewWindow: typeof native.getCurrentWebviewWindow =
  new Proxy(native.getCurrentWebviewWindow, {
    apply: (_target, _this, args) =>
      owned(Reflect.apply(host().getCurrentWebviewWindow, undefined, args)),
    construct: (_target, args) =>
      owned(Reflect.construct(host().getCurrentWebviewWindow, args), false),
  });
export const getAllWebviewWindows: typeof native.getAllWebviewWindows =
  new Proxy(native.getAllWebviewWindows, {
    apply: (_target, _this, args) =>
      Reflect.apply(host().getAllWebviewWindows, undefined, args),
    construct: (_target, args) =>
      owned(Reflect.construct(host().getAllWebviewWindows, args), false),
  });
export const WebviewWindow: typeof native.WebviewWindow = new Proxy(
  native.WebviewWindow,
  {
    apply: (_target, _this, args) =>
      Reflect.apply(host().WebviewWindow, undefined, args),
    construct: (_target, args) =>
      owned(Reflect.construct(host().WebviewWindow, args), false),
  },
);
