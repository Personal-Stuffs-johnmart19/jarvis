/** Prefer the current theme file, but keep editing existing legacy files in place. */
export async function findThemeFile(paths: readonly [string, string], exists: (path: string) => Promise<boolean>) {
  for (const path of paths) if (await exists(path)) return path;
  return paths[0];
}

/** A removed theme must not leave another recognized file variant behind. */
export async function removeThemeFiles(paths: readonly [string, string], exists: (path: string) => Promise<boolean>, remove: (path: string) => Promise<unknown>) {
  for (const path of paths) if (await exists(path)) await remove(path);
}

/** Rust fs_stat returns localized OS text with a stable numeric error suffix. */
export async function probeThemeFile(stat: () => Promise<unknown>, windows: boolean): Promise<boolean> {
  try {
    await stat();
    return true;
  } catch (error) {
    const text = typeof error === "string" ? error : error instanceof Error ? error.message : "";
    if (/\(os error 2\)$/.test(text) || (windows && /\(os error 3\)$/.test(text))) return false;
    throw error;
  }
}
