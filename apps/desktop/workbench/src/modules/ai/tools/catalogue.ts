export type Capability = {
  name: string;
  description: string;
  parameters: unknown;
  category: string;
  executor: string;
  approval: string;
  available: boolean;
};
/** Prefer live handlers over their published snapshots; broad searches stay compact. */
export function cataloguePage(
  remote: Capability[],
  local: Capability[],
  options: { query: string; category: string; cursor: number; limit: number },
) {
  const query = options.query.trim().toLowerCase();
  const words = query.split(/\s+/).filter(Boolean);
  const unique = new Map(
    [...remote, ...local].map((item) => [item.name, item]),
  );
  const all = [...unique.values()].filter(
    (item) =>
      (!options.category || item.category === options.category) &&
      (!words.length ||
        words.some((word) =>
          `${item.name} ${item.description}`.toLowerCase().includes(word),
        )),
  );
  const rank = (name: string) =>
    name === query
      ? 0
      : name.startsWith(query)
        ? 1
        : name.includes(query)
          ? 2
          : 3;
  all.sort(
    (a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name),
  );
  const end = options.cursor + options.limit;
  return {
    items: all.slice(options.cursor, end).map((item) =>
      item.name.toLowerCase() === query
        ? item
        : {
            ...item,
            parameters: undefined,
            description: item.description.slice(0, 280),
            description_truncated: item.description.length > 280,
            schema_query: item.name,
          },
    ),
    total: all.length,
    next_cursor: end < all.length ? end : null,
    hint: all.length
      ? "Query an exact capability name for its full schema."
      : "No matching capabilities. Try English tool names or an empty query. For CLI programs search utility_catalog, then utility_run; optional tools are invoked through jarvis_action.",
  };
}
