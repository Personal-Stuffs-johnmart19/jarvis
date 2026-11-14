import type { ModelMessage } from "ai";

type Message = { parts: readonly unknown[] };
export function readToolResult(
  messages: readonly Message[],
  id: string,
  offset: number,
  limit: number,
) {
  for (const message of messages)
    for (const raw of message.parts) {
      if (!raw || typeof raw !== "object") continue;
      const part = raw as Record<string, unknown>;
      if (
        part.toolCallId !== id ||
        typeof part.type !== "string" ||
        (!part.type.startsWith("tool-") && part.type !== "dynamic-tool")
      )
        continue;
      if (part.state !== "output-available" && part.state !== "output-error")
        continue;
      const value =
        part.state === "output-error" ? { error: part.errorText } : part.output;
      const text =
        typeof value === "string" ? value : (JSON.stringify(value) ?? "");
      // Offsets stay in UTF-16 code units, but pages contain whole code points.
      let start = Math.min(text.length, offset);
      if (start > 0 && isPair(text, start)) start--;
      let end = Math.min(text.length, start + limit);
      if (isPair(text, end)) end += end - start === 1 ? 1 : -1;
      return {
        tool_call_id: id,
        text: text.slice(start, end),
        total_characters: text.length,
        next_offset: end < text.length ? end : null,
      };
    }
  return { error: "Completed tool result not found in this session" };
}

function isPair(text: string, boundary: number): boolean {
  const before = text.charCodeAt(boundary - 1);
  const after = text.charCodeAt(boundary);
  return before >= 0xd800 && before <= 0xdbff && after >= 0xdc00 && after <= 0xdfff;
}

/** Child agents recover only their own completed SDK results, never parent chat state. */
export function readModelToolResult(messages: readonly ModelMessage[], id: string, offset: number, limit: number) {
  return readToolResult(messages.filter((message) => message.role === "tool").map((message) => ({
    parts: Array.isArray(message.content) ? message.content.filter((part) => part.type === "tool-result").map((part) => ({
      type: "dynamic-tool", state: "output-available", toolCallId: part.toolCallId,
      output: "value" in part.output ? part.output.value : part.output,
    })) : [],
  })), id, offset, limit);
}
