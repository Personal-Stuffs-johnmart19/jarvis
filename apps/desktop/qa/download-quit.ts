import { createQuitCoordinator } from "../src/quitCoordinator";
import { createAppConfirmation } from "../src/appConfirmation";

// Source-only UI harness: production coordinator/dialog, injected task/guard deps.
// No Tauri runtime, native interception, real download, or application exit.
const qa = {
  downloads: [910001, 910002], dirty: true,
  completions: [] as { id: number; approved: boolean; downloads: number[] }[],
  failures: [] as string[], workspaceVisits: 0, nextId: 0,
};
Object.assign(window, { __downloadQuitQA: qa });
const confirm = createAppConfirmation(owner => owner === window, text => text);
const coordinator = createQuitCoordinator({
  reveal: async () => {},
  snapshotDownloads: async () => [...qa.downloads],
  confirmDownloads: () => confirm(window, "Model downloads are still running. Quit Jarvis and interrupt them? Partial downloads can resume later."),
  complete: async (id, approved, downloads) => {
    qa.completions.push({ id, approved, downloads });
    document.getElementById("status")!.textContent = approved ? "Approved" : "Cancelled";
  },
  failed: error => { qa.failures.push(String(error)); },
});
coordinator.register("shell", async () => true);
coordinator.register("workspace", async () => {
  qa.workspaceVisits++;
  return !qa.dirty || confirm(window, "Unsaved files: 1. Quitting will discard these changes.");
});
document.getElementById("quit")!.onclick = () => {
  document.getElementById("status")!.textContent = "Pending";
  void coordinator.request(++qa.nextId);
};
