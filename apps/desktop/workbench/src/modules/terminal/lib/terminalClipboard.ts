import { isTauri } from "@/host/core";

export async function readTerminalClipboard(): Promise<string> {
  try {
    if (isTauri()) {
      const { readText } = await import("@/host/clipboard");
      return await readText();
    }
    return (await navigator.clipboard?.readText()) ?? "";
  } catch {
    return "";
  }
}

export async function writeTerminalClipboard(text: string): Promise<void> {
  if (isTauri()) {
    const { writeText } = await import("@/host/clipboard");
    await writeText(text);
    return;
  }
  if (!navigator.clipboard) throw new Error("Clipboard is unavailable");
  await navigator.clipboard.writeText(text);
}
