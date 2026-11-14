import { InvalidToolInputError } from "ai";

type ErrorDetails = {
  code: string | null;
  message: string;
};

const MAX_ERROR_LENGTH = 800;
const FALLBACK_ERROR =
  "The AI provider rejected the request. Check the selected model and provider settings, then try again.";

export const UNAVAILABLE_TOOL_MESSAGE = "The model requested a tool that is unavailable in this chat. Ask Jarvis to continue using its capability catalogue without repeating completed actions.";

export const INVALID_TOOL_INPUT_MESSAGE = "The model supplied invalid tool arguments. That call was not executed. Ask Jarvis to use the capability's exact schema without repeating completed actions.";

export function formatAiError(error: unknown, translate: (message: string) => string = message => message): string {
  const details = extractErrorDetails(error);
  if (!details) return FALLBACK_ERROR;
  if (details.code === "AI_NoSuchToolError") return translate(UNAVAILABLE_TOOL_MESSAGE);
  if (details.code === "AI_InvalidToolInputError") return translate(INVALID_TOOL_INPUT_MESSAGE);
  const message = sanitizeErrorMessage(details.message);
  if (!message) return FALLBACK_ERROR;
  const prefix = errorPrefix(details.code, message);
  return prefix ? `${prefix}: ${message}` : message;
}

function extractErrorDetails(
  value: unknown,
  depth = 0,
  seen = new Set<unknown>(),
): ErrorDetails | null {
  if (depth > 5 || value == null || seen.has(value)) return null;
  if (typeof value === "string") {
    const parsed = parseJson(value);
    if (parsed !== null) return extractErrorDetails(parsed, depth + 1, seen);
    return { code: /^(AI_NoSuchToolError|AI_InvalidToolInputError): /.exec(value)?.[1] ?? null, message: value };
  }
  if (typeof value !== "object") return null;
  seen.add(value);

  const record = value as Record<string, unknown>;
  const code = record.name === "AI_NoSuchToolError" || record.name === "AI_InvalidToolInputError" ? record.name : stringValue(record.code);
  for (const key of ["error", "responseBody", "body", "data", "cause"]) {
    const nested = extractErrorDetails(record[key], depth + 1, seen);
    if (nested) return { code: nested.code ?? code, message: nested.message };
  }
  const message = stringValue(record.message);
  return message ? { code, message } : null;
}

function parseJson(value: string): unknown | null {
  const trimmed = value.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function sanitizeErrorMessage(message: string): string {
  const redacted = message
    .replace(/Bearer\s+\S+/gi, "Bearer [redacted]")
    .replace(
      /\b(?:sk-(?:ant-|proj-)?|xai-|gsk_)[A-Za-z0-9_-]{8,}\b/g,
      "[redacted]",
    )
    .replace(/\bAIza[A-Za-z0-9_-]{20,}\b/g, "[redacted]")
    .replace(/\s+/g, " ")
    .trim();
  return redacted.length <= MAX_ERROR_LENGTH
    ? redacted
    : `${redacted.slice(0, MAX_ERROR_LENGTH - 3)}...`;
}

function errorPrefix(code: string | null, message: string): string | null {
  switch (code?.toLowerCase()) {
    case "model_not_found":
    case "not_found_error":
      return "Model unavailable";
    case "invalid_api_key":
    case "authentication_error":
      return "Authentication failed";
    case "insufficient_quota":
      return "Quota exceeded";
    case "rate_limit_exceeded":
    case "rate_limit_error":
      return "Rate limit reached";
  }
  return /\bmodel\b.*\b(?:limited preview|not available|not found)\b/i.test(
    message,
  )
    ? "Model unavailable"
    : null;
}

/** SDK tool-input failures are UI parts, rather than stream errors. Keep their linkage intact. */
export function formatToolInputErrors<T extends { type: string; errorText?: string }>(
  stream: ReadableStream<T>, translate: (message: string) => string,
): ReadableStream<T> {
  return stream.pipeThrough(new TransformStream<T, T>({
    transform(part, controller) {
      if ((part.type === "tool-input-error" || part.type === "tool-output-error") && part.errorText
        && ["AI_NoSuchToolError", "AI_InvalidToolInputError"].includes(extractErrorDetails(part.errorText)?.code ?? "")) {
        controller.enqueue({ ...part, errorText: formatAiError(part.errorText, translate) });
      } else controller.enqueue(part);
    },
  }));
}

/** An unknown tool must not trigger a model repair loop or replay completed work. */
export function stopOnUnavailableToolCall({ steps }: {
  steps: readonly { toolCalls?: readonly { invalid?: boolean; error?: unknown }[] }[];
}): boolean {
  return steps.at(-1)?.toolCalls?.some(call => call.invalid === true
    && extractErrorDetails(call.error)?.code === "AI_NoSuchToolError") ?? false;
}

/** Allow one schema correction; a successful step resets this consecutive-failure bound. */
export function stopOnRepeatedInvalidToolCall({ steps }: {
  steps: readonly { toolCalls?: readonly { invalid?: boolean; error?: unknown }[] }[];
}): boolean {
  const invalid = (index: number) => steps.at(index)?.toolCalls?.some(call => call.invalid === true
    && InvalidToolInputError.isInstance(call.error)) ?? false;
  return steps.length >= 2 && invalid(-1) && invalid(-2);
}
