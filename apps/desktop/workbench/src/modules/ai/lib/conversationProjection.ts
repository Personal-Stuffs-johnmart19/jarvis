import { assistantReply } from "./assistantReply";
import type { UIMessage } from "ai";
export type ConversationMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  time: number;
};
/** Read model history as a bounded UI projection, never as a second persisted chat. */
export function projectConversation(
  messages: UIMessage[],
  timestamp: (id: string, index: number) => number,
): ConversationMessage[] {
  const result: ConversationMessage[] = [];
  let bytes = 0;
  for (let i = messages.length - 1; i >= 0 && result.length < 200; i--) {
    const message = messages[i];
    if (message.role !== "user" && message.role !== "assistant") continue;
    const step =
      message.role === "assistant"
        ? message.parts.map((p) => p.type).lastIndexOf("step-start")
        : -1;
    const text = message.parts
      .slice(Math.max(0, step))
      .filter((p) => p.type === "text")
      .map((p) => (p.type === "text" ? p.text : ""))
      .join("\n")
      .slice(0, 12000);
    const displayText =
      text || (message.role === "assistant" ? assistantReply([message]) : "");
    if (!displayText) continue;
    const item = {
      id: message.id,
      role: message.role,
      text: displayText,
      time: timestamp(message.id, i),
    };
    const size = new TextEncoder().encode(JSON.stringify(item)).length;
    if (bytes + size > 45000) break;
    result.unshift(item);
    bytes += size;
  }
  return result;
}
