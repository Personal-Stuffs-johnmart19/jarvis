/** Plugin functions must execute in the trusted root document's IPC realm. */
export function nativePlugin<T>(name: string, native: T): T {
  if (window.parent === window) return native;
  return (window.parent as unknown as {
    __JARVIS_NATIVE__: { plugins: Record<string, unknown> };
  }).__JARVIS_NATIVE__.plugins[name] as T;
}
