export type SuggestionCatalog = { items: [string, string][]; builtin: boolean };
/** Translate only templates owned by Jarvis; saved user content is literal data. */
export function localizeSuggestions(catalog: SuggestionCatalog, translate: (text: string) => string): [string, string][] {
  return catalog.builtin ? catalog.items.map(([label, prompt]) => [translate(label), translate(prompt)]) : catalog.items;
}
