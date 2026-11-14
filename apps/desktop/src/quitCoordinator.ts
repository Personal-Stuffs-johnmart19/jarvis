export type QuitGuard = () => Promise<boolean>;
export type QuitRole = "shell" | "workspace";

/** An explicit quit must visit both live surfaces, even if one is hidden. */
export function createQuitCoordinator(deps: {
  reveal(): Promise<void>;
  snapshotDownloads(): Promise<number[]>;
  confirmDownloads(): Promise<boolean>;
  complete(id: number, approved: boolean, downloads: number[]): Promise<void>;
  failed(error: unknown): void;
}) {
  const guards = new Map<QuitRole, { run: QuitGuard; validate: QuitGuard }>();
  let running = false;
  return {
    register(role: QuitRole, guard: QuitGuard, validate: QuitGuard = async () => true) {
      const entry = { run: guard, validate };
      guards.set(role, entry);
      return () => { if (guards.get(role) === entry) guards.delete(role); };
    },
    async request(id: number) {
      if (running) return;
      running = true;
      let approved = false;
      let downloads: number[] = [];
      try {
        await deps.reveal();
        downloads = await deps.snapshotDownloads();
        const shell = guards.get("shell"), workspace = guards.get("workspace");
        if (!shell || !workspace) throw new Error("Quit protection is still loading. Please try again.");
        approved = (!downloads.length || await deps.confirmDownloads()) &&
          await shell.run() && guards.get("shell") === shell &&
          await workspace.run() && await workspace.validate() && await shell.validate() &&
          guards.get("workspace") === workspace && guards.get("shell") === shell;
        if (approved) {
          const currentDownloads = await deps.snapshotDownloads();
          approved = currentDownloads.every(task => downloads.includes(task)) &&
            guards.get("workspace") === workspace && guards.get("shell") === shell;
        }
      } catch (error) { approved = false; deps.failed(error); }
      try { await deps.complete(id, approved, approved ? downloads : []); }
      catch (error) { deps.failed(error); }
      finally { running = false; }
    },
  };
}
