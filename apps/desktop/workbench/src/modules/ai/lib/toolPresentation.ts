/** Display the canonical action without changing its stored gateway call or IDs. */
export function toolPresentation(toolName: string, input: unknown) {
  if (toolName === "jarvis_action" && input && typeof input === "object" && !Array.isArray(input)) {
    const action = input as Record<string, unknown>;
    if (Object.hasOwn(action, "name") && Object.hasOwn(action, "args") &&
        typeof action.name === "string" && action.name.trim() &&
        action.args && typeof action.args === "object" && !Array.isArray(action.args)) {
      return { name: action.name, input: action.args };
    }
  }
  return { name: toolName, input };
}
