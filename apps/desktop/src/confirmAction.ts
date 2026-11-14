// App-owned confirmation; native browser dialogs expose the framework origin.
export async function confirmAction(message: string): Promise<boolean> {
  const root = window.parent as unknown as { __JARVIS_NATIVE__?: { confirm(owner: Window, message: string): Promise<boolean> } };
  return root.__JARVIS_NATIVE__?.confirm(window, message) ?? false;
}
