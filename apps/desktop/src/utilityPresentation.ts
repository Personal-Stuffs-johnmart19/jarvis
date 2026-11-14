import { supportedLocales, translate, type Locale } from "./localization";

type UtilityMetadata = { id: string; category: string; description: string; source: string };

/** Translate human-readable metadata while preserving executable and installer data. */
export function presentUtility<T extends UtilityMetadata>(item: T, locale: Locale): Omit<T, "category" | "description" | "source"> & UtilityMetadata {
  return { ...item, category: translate(locale, item.category),
    description: translate(locale, item.description), source: translate(locale, item.source) };
}

export function utilityMatchesQuery(item: UtilityMetadata, query: string): boolean {
  const metadata = [item.category, item.description, item.source];
  return [item.id, ...metadata, ...supportedLocales.flatMap(locale =>
    metadata.map(message => translate(locale, message)))].join(" ").toLowerCase()
    .includes(query.toLowerCase());
}
