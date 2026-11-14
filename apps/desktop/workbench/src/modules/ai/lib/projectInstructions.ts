/** Read bounded project instructions; older workspace files remain readable. */
export async function readProjectInstructions(
  root: string | null,
  read: (path: string) => Promise<{ kind: string; content?: string }>,
): Promise<string | null> {
  if (!root) return null;
  for (const name of ["JARVIS.md", "TERAX.md"]) {
    try {
      const result = await read(`${root.replace(/\/$/, "")}/${name}`);
      return result.kind === "text" && typeof result.content === "string"
        ? result.content.slice(0, 32 * 1024)
        : null;
    } catch { /* Try the legacy filename only when the preferred read fails. */ }
  }
  return null;
}
