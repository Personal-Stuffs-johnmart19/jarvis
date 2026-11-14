import type { UIMessage } from "ai";

const INTERRUPTED_ERROR =
  "Interrupted before completion. Outcome unknown; verify state before retrying side effects.";

/** Project interrupted calls from older turns without editing saved UI history. */
export function projectInterruptedHistory(messages: UIMessage[]): UIMessage[] {
  const lastUser = messages.map((message) => message.role).lastIndexOf("user");
  return messages.map((message, index) => {
    if (index >= lastUser || message.role !== "assistant") return message;
    let changed = false;
    const parts = message.parts.map((part): UIMessage["parts"][number] => {
      if (
        (part.type.startsWith("tool-") || part.type === "dynamic-tool") &&
        "state" in part
      ) {
        if (part.state === "input-available") {
          changed = true;
          return { ...part, state: "output-error", errorText: INTERRUPTED_ERROR };
        }
        if (part.state === "approval-responded") {
          changed = true;
          if (part.approval.approved === false) {
            return {
              ...part,
              state: "output-denied",
              approval: { ...part.approval, approved: false },
            };
          }
          return {
            ...part,
            state: "output-error",
            errorText: INTERRUPTED_ERROR,
            approval: { ...part.approval, approved: true },
          };
        }
      }
      return part;
    });
    return changed ? { ...message, parts } : message;
  });
}
