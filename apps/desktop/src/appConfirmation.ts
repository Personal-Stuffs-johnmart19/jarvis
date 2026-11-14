export function createAppConfirmation(allowed: (owner: Window) => boolean, t: (message: string) => string) {
  let queue = Promise.resolve();
  return (owner: Window, message: string): Promise<boolean> => {
    if (!allowed(owner) || typeof message !== "string" || message.length > 8192) return Promise.resolve(false);
    const sourceDocument = owner.document;
    const result = queue.then(() => new Promise<boolean>(resolve => {
      if (!allowed(owner) || owner.document !== sourceDocument) { resolve(false); return; }
      const dialog = document.createElement("dialog");
      dialog.className = "jarvis-confirmation"; dialog.ariaLabel = "Jarvis";
      const heading = document.createElement("h3"); heading.textContent = "Jarvis";
      const text = document.createElement("p"); text.textContent = message;
      const actions = document.createElement("div");
      const cancel = document.createElement("button"); cancel.textContent = t("Cancel");
      const accept = document.createElement("button"); accept.textContent = t("Confirm");
      actions.append(cancel, accept); dialog.append(heading, text, actions);
      let settled = false;
      const finish = (value: boolean) => {
        if (settled) return; settled = true;
        owner.removeEventListener("pagehide", abandoned);
        dialog.remove(); resolve(value);
      };
      const abandoned = () => finish(false);
      cancel.onclick = () => finish(false); accept.onclick = () => finish(true);
      dialog.addEventListener("keydown", event => event.stopPropagation());
      dialog.addEventListener("cancel", event => { event.preventDefault(); finish(false); });
      dialog.addEventListener("close", () => finish(false));
      owner.addEventListener("pagehide", abandoned, { once: true });
      document.body.append(dialog);
      try { dialog.showModal(); cancel.focus(); } catch { finish(false); }
    }));
    queue = result.then(() => undefined, () => undefined);
    return result;
  };
}
