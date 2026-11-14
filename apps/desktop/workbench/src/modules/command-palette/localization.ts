import type { PaletteItem } from "./types";

type Translate = (message: string, ...values: unknown[]) => string;

/** Localize owned labels at render time; identifiers and user data stay intact. */
export function localizeCommandItems(
  items: PaletteItem[],
  translate: Translate,
): PaletteItem[] {
  return items.map((item) => ({
    ...item,
    title: item.titleMessage
      ? translate(item.titleMessage.message, ...item.titleMessage.values)
      : translate(item.title),
    disabledReason: item.disabledReason
      ? translate(item.disabledReason)
      : undefined,
    // Keep the stable group ID for ordering, and search both language labels.
    keywords: [item.title, translate(item.group), ...(item.keywords ?? [])],
  }));
}
