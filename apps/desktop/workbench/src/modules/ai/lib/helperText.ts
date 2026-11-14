/** Stateless text requests use the selected provider without tools or chat history. */
export type HelperTextRequest = {
  id: string; owner: string; session: string; route: string;
  system: string; prompt: string; max_output_tokens: number; timeout_ms: number;
  temperature?: number; json_mode?: boolean;
};
export type HelperTextReply = Pick<HelperTextRequest, "id" | "owner" | "session" | "route"> & { text: string; error?: string; error_code?: HelperErrorCode };
export function matchesHelperTextRoute(request: Pick<HelperTextRequest, "owner" | "session" | "route">, owner: string, session: string, route: string) {
  return request.owner === owner && request.session === session && request.route === route;
}
export type HelperErrorCode = "busy" | "context_limit" | "native_format" | "timeout" | "cancelled"
  | "api_400" | "api_401" | "api_403" | "api_404" | "api_408" | "api_429" | "api_5xx" | "api_status_other"
  | "incomplete_output" | "invalid_json" | "output_size" | "request_failed";
type HelperFailure = { code: HelperErrorCode; message: string };

/** Inspect bounded SDK diagnostics in memory; only a finite owned code/message may leave this function. */
export function classifyHelperError(error: unknown): HelperFailure {
  const structured = error && typeof error === "object" ? error as Record<string, unknown> : undefined;
  if (typeof structured?.name === "string" && structured.name.includes("NoObjectGeneratedError"))
    return typeof structured.finishReason === "string" && structured.finishReason !== "stop"
      ? {code:"incomplete_output",message:"Selected provider did not complete helper text"}
      : {code:"invalid_json",message:"Selected provider returned invalid helper JSON"};
  let current = error, status: number | undefined, text = "";
  for (let depth = 0; depth < 3 && current && typeof current === "object"; depth++) {
    const value = current as Record<string, unknown>;
    try {
      for (const key of ["statusCode", "status", "status_code"])
        if (status === undefined && typeof value[key] === "number" && Number.isInteger(value[key]) && value[key] >= 400 && value[key] <= 599) status = value[key];
      for (const key of ["name", "message", "responseBody"])
        if (typeof value[key] === "string") text += " " + value[key].slice(0, 1024);
      current = value.cause;
    } catch { break; }
  }
  if (typeof current === "string") text += " " + current.slice(0, 1024);
  if (status === undefined) {
    const match = /\b(?:HTTP(?:\/[0-9.]+)?|status(?:[ _-]?code)?)[:\s]+([45]\d{2})\b/i.exec(text);
    if (match) status = Number(match[1]);
  }
  if (/timeout|timed out|deadline|ETIMEDOUT/i.test(text)) return {code:"timeout",message:"Selected provider text request timed out"};
  if (/context(?:[_ -]length| window| limit)|exceed.{0,32}(?:token|context)|too many tokens|tokens.{0,32}(?:exceed|limit)/i.test(text))
    return {code:"context_limit",message:"Helper text exceeds selected model context"};
  if (/native[_ -]?format|parse.{0,40}(?:json|native|chat template)|invalid.{0,20}(?:json|response format)|json.{0,20}pars/i.test(text))
    return {code:"native_format",message:"Selected provider rejected the helper request format"};
  const statuses = {400:"api_400",401:"api_401",403:"api_403",404:"api_404",408:"api_408",429:"api_429"} as const;
  if (status !== undefined) {
    const code = statuses[status as keyof typeof statuses] ?? (status >= 500 ? "api_5xx" : "api_status_other");
    return {code,message:"Selected provider returned an API error"};
  }
  return {code:"request_failed",message:"Selected provider text request failed"};
}

/** The Python reply schema stays unchanged; bounded codes travel inside the owned error string. */
export function helperTextWireReply(reply: HelperTextReply, translate: (message: string) => string): Omit<HelperTextReply, "error_code"> {
  const { error_code, ...wire } = reply;
  return reply.error ? { ...wire, error: `${error_code ? `[${error_code}] ` : ""}${translate(reply.error)}` } : wire;
}


type Usage = { inputTokens?: number; outputTokens?: number; inputTokenDetails?: { cacheReadTokens?: number } };
export type HelperTextDependencies = {
  context: (signal: AbortSignal) => Promise<number>;
  model: (signal: AbortSignal) => Promise<unknown>;
  generate: (options: { model: unknown; system: string; prompt: string; maxOutputTokens: number; temperature?: number; jsonMode: boolean; maxRetries: 0; abortSignal: AbortSignal }) => Promise<{ text: string; usage: Usage; finishReason: string }>;
  estimate: (value: unknown) => number;
  usage: (usage: Usage) => void;
};
const bytes = (value: string) => new TextEncoder().encode(value).byteLength;
async function abortable<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  signal.throwIfAborted();
  let onAbort: (() => void) | undefined;
  try {
    return await Promise.race([operation, new Promise<never>((_resolve, reject) => {
      onAbort = () => reject(new DOMException("Cancelled", "AbortError"));
      signal.addEventListener("abort", onAbort, { once: true });
      if (signal.aborted) onAbort();
    })]);
  } finally { if (onAbort) signal.removeEventListener("abort", onAbort); }
}
export function parseHelperTextRequest(value: unknown): HelperTextRequest {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid helper text request");
  const request = value as Record<string, unknown>;
  const allowed = ["id", "owner", "session", "route", "system", "prompt", "max_output_tokens", "timeout_ms", "temperature", "json_mode"];
  if (Object.keys(request).some(key => !allowed.includes(key)) ||
      ["id", "owner", "session", "route"].some(key => typeof request[key] !== "string" || !/^[a-zA-Z0-9_-]{1,128}$/.test(request[key] as string)) ||
      typeof request.system !== "string" || typeof request.prompt !== "string" || !request.prompt.trim() ||
      bytes(request.system) + bytes(request.prompt) > 48000 ||
      !Number.isInteger(request.max_output_tokens) || (request.max_output_tokens as number) < 1 || (request.max_output_tokens as number) > 4096 ||
      !Number.isInteger(request.timeout_ms) || (request.timeout_ms as number) < 1 || (request.timeout_ms as number) > 120000 ||
      (request.temperature !== undefined && (typeof request.temperature !== "number" || !Number.isFinite(request.temperature) || request.temperature < 0 || request.temperature > 2)) ||
      (request.json_mode !== undefined && typeof request.json_mode !== "boolean")) throw new Error("Invalid helper text request");
  return request as HelperTextRequest;
}

/** One controller per attached frontend owner; failures never change provider. */
export class HelperTextRunner {
  private pending?: { id: string; controller: AbortController };
  private dependencies: HelperTextDependencies;
  constructor(dependencies: HelperTextDependencies) { this.dependencies = dependencies; }
  cancel(id?: string) { if (!id || this.pending?.id === id) this.pending?.controller.abort(); }
  async run(request: HelperTextRequest): Promise<HelperTextReply> {
    const identity = { id: request.id, owner: request.owner, session: request.session, route: request.route };
    if (this.pending) return { ...identity, text: "", error: "Selected provider text helper is busy", error_code: "busy" };
    const controller = new AbortController();
    this.pending = { id: request.id, controller };
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, request.timeout_ms);
    let failure: HelperFailure | undefined;
    let usageRecorded = false;
    try {
      const context = await abortable(this.dependencies.context(controller.signal), controller.signal);
      controller.signal.throwIfAborted();
      const system = request.system + (request.json_mode ? "\nReturn exactly one valid JSON value, without Markdown fences or commentary." : "");
      const inputTokens = this.dependencies.estimate(system) + this.dependencies.estimate(request.prompt) + 128;
      const availableOutputTokens = Math.floor(context * 0.9) - inputTokens;
      if (!Number.isFinite(context) || context < 1 || !Number.isFinite(inputTokens) || availableOutputTokens < 1) {
        failure = { code: "context_limit", message: "Helper text exceeds selected model context" };
        throw new Error(failure.message);
      }
      // Stateless helpers have no history or tool schemas. Allocate their actual
      // remaining context instead of the conversation loop's fixed reply share.
      const maxOutputTokens = Math.min(request.max_output_tokens, 4096, availableOutputTokens);
      const model = await abortable(this.dependencies.model(controller.signal), controller.signal);
      controller.signal.throwIfAborted();
      const result = await abortable(this.dependencies.generate({ model, system, prompt: request.prompt, maxOutputTokens, temperature: request.temperature, jsonMode: request.json_mode === true, maxRetries: 0, abortSignal: controller.signal }), controller.signal);
      this.dependencies.usage(result.usage);
      usageRecorded = true;
      controller.signal.throwIfAborted();
      if (result.finishReason !== "stop") {
        failure = { code: "incomplete_output", message: "Selected provider did not complete helper text" };
        throw new Error(failure.message);
      }
      if (!result.text.trim() || bytes(result.text) > 48000 || bytes(JSON.stringify({ ...identity, text: result.text })) > 60000) {
        failure = { code: "output_size", message: "Selected provider returned empty or oversized helper text" };
        throw new Error(failure.message);
      }
      if (request.json_mode) {
        failure = { code: "invalid_json", message: "Selected provider returned invalid helper JSON" };
        JSON.parse(result.text);
      }
      return { ...identity, text: result.text };
    } catch (error) {
      // Structured-output parsing may reject after a billable provider response.
      const structured = error && typeof error === "object" ? error as {name?:unknown;usage?:unknown} : undefined;
      if (!usageRecorded && typeof structured?.name === "string" && structured.name.includes("NoObjectGeneratedError") && structured.usage && typeof structured.usage === "object")
        this.dependencies.usage(structured.usage as Usage);
      const diagnostic: HelperFailure = controller.signal.aborted
        ? (timedOut ? { code: "timeout", message: "Selected provider text request timed out" } : { code: "cancelled", message: "Selected provider text request was cancelled" })
        : (failure ?? classifyHelperError(error));
      return { ...identity, text: "", error: diagnostic.message, error_code: diagnostic.code };
    } finally {
      clearTimeout(timer);
      if (this.pending?.controller === controller) this.pending = undefined;
    }
  }
}
