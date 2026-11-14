import type { UIMessage } from "ai";

/** User-facing outcome for a completed tool-only turn; never guesses that an action succeeded. */
export function assistantReply(messages: UIMessage[]): string {
  const turn = messages.slice(messages.map((m) => m.role).lastIndexOf("user") + 1);
  const last = turn.filter((m) => m.role === "assistant").at(-1);
  if (!last) return "";
  const step = last.parts.map((p) => p.type).lastIndexOf("step-start");
  const text = last.parts
    .slice(Math.max(step, 0))
    .filter((p) => p.type === "text")
    .map((p) => (p.type === "text" ? p.text : ""))
    .join("\n")
    .trim();
  if (text) return text;
  let fallback = "";
  for (const message of [...turn].reverse()) {
    if (message.role !== "assistant") continue;
    for (const part of [...message.parts].reverse()) {
      if (!("state" in part)) continue;
      if (part.state === "output-denied")
        return "Action rejected and not executed.";
      if (part.state === "output-error")
        return "Failed to execute action. Error details are available in the workspace.";
      if (part.state !== "output-available" || !("output" in part)) continue;
      const output = part.output;
      const name = part.type === "dynamic-tool" && "toolName" in part
        ? part.toolName : part.type.slice(5);
      if (name === "todo_write" && output && typeof output === "object" &&
          (output as Record<string, unknown>).ok === true) {
        fallback ||= "The tool has finished working. Result details are available in the workspace.";
        continue;
      }
      if (typeof output === "string" && output.trim())
        return "The tool returned a result. Details are available in the workspace.";
      if (output && typeof output === "object") {
        const value = output as Record<string, unknown>;
        if (value.error || value.ok === false)
          return typeof value.error === "string"
            ? `Failed to execute action: ${value.error.slice(0, 1800)}`
            : "The tool reported an error. Details are available in the workspace.";
        for (const key of ["reply", "message", "summary"])
          if (typeof value[key] === "string" && value[key])
            return (value[key] as string).slice(0, 2000);
        if (value.ok === true)
          return "The tool has finished working. Result details are available in the workspace.";
      }
      return "Tool result received. Details are available in the workspace.";
    }
  }
  return fallback;
}
