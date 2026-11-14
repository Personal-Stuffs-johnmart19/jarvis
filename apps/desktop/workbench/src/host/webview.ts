import * as native from "@tauri-apps/api/webview";
export * from "@tauri-apps/api/webview";
function host() {
  if (window.parent === window) return native;
  return (
    window.parent as unknown as {
      __JARVIS_NATIVE__: { webview: typeof native };
    }
  ).__JARVIS_NATIVE__.webview;
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

export const getCurrentWebview: typeof native.getCurrentWebview = new Proxy(
  native.getCurrentWebview,
  {
    apply: (_target, _this, args) =>
      owned(Reflect.apply(host().getCurrentWebview, undefined, args)),
    construct: (_target, args) =>
      owned(Reflect.construct(host().getCurrentWebview, args), false),
  },
);
export const getAllWebviews: typeof native.getAllWebviews = new Proxy(
  native.getAllWebviews,
  {
    apply: (_target, _this, args) =>
      Reflect.apply(host().getAllWebviews, undefined, args),
    construct: (_target, args) =>
      owned(Reflect.construct(host().getAllWebviews, args), false),
  },
);
export const Webview: typeof native.Webview = new Proxy(native.Webview, {
  apply: (_target, _this, args) =>
    Reflect.apply(host().Webview, undefined, args),
  construct: (_target, args) =>
    owned(Reflect.construct(host().Webview, args), false),
});
