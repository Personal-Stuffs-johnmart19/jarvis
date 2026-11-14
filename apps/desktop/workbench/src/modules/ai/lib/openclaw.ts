/**
 * OpenClaw Gateway helpers. The Gateway is an agent runtime exposing an
 * OpenAI-compatible HTTP API (see docs/OPENCLAW.md). It is stored as a normal
 * custom endpoint so chat, subagents, approvals, AbortSignal and credential
 * storage (native keyring) go through the shared path.
 */
export const OPENCLAW_DEFAULT_BASE_URL = "http://127.0.0.1:18789/v1";
export const OPENCLAW_ENDPOINT_NAME = "OpenClaw Gateway";

export function isOpenClawAgentTarget(id: string): boolean {
  const trimmed = id.trim();
  return (
    trimmed === "openclaw" ||
    trimmed === "openclaw/default" ||
    trimmed.startsWith("openclaw/") ||
    trimmed.startsWith("openclaw:") ||
    trimmed.startsWith("agent:")
  );
}

export class OpenClawError extends Error {
  kind: "unreachable" | "auth" | "http" | "invalid";

  constructor(
    kind: "unreachable" | "auth" | "http" | "invalid",
    message: string,
  ) {
    super(message);
    this.name = "OpenClawError";
    this.kind = kind;
  }
}

export function normalizeOpenClawBaseURL(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";

  const hasExplicitScheme = /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed);
  const isSchemelessHostPort =
    !hasExplicitScheme || /^[a-zA-Z0-9.-]+:\d+(\/|$)/.test(trimmed);

  if (!trimmed.includes("://") && trimmed.includes("@")) {
    throw new OpenClawError(
      "invalid",
      "Gateway URL must not contain embedded credentials.",
    );
  }

  // Explicit scheme validation: only http:// and https:// allowed
  if (hasExplicitScheme && !isSchemelessHostPort) {
    if (!/^https?:\/\//i.test(trimmed)) {
      throw new OpenClawError(
        "invalid",
        "Only http:// and https:// URLs are supported.",
      );
    }
  }

  const withScheme = /^https?:\/\//i.test(trimmed)
    ? trimmed
    : `http://${trimmed}`;

  let parsed: URL;
  try {
    parsed = new URL(withScheme);
  } catch {
    throw new OpenClawError("invalid", "Enter a valid Gateway URL.");
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new OpenClawError(
      "invalid",
      "Only http:// and https:// URLs are supported.",
    );
  }

  if (parsed.username || parsed.password) {
    throw new OpenClawError(
      "invalid",
      "Gateway URL must not contain embedded credentials.",
    );
  }

  if (parsed.search) {
    throw new OpenClawError(
      "invalid",
      "Gateway URL must not contain query parameters.",
    );
  }

  if (parsed.hash) {
    throw new OpenClawError(
      "invalid",
      "Gateway URL must not contain a hash fragment.",
    );
  }

  if (!parsed.hostname) {
    throw new OpenClawError("invalid", "Enter a valid Gateway URL.");
  }

  let pathname = parsed.pathname.replace(/\/+$/, "");
  if (!pathname || pathname === "/") {
    pathname = "/v1";
  } else if (!pathname.endsWith("/v1")) {
    pathname = `${pathname}/v1`;
  }

  return `${parsed.origin}${pathname}`;
}

export type OpenClawModel = { id: string; agent: boolean };

/** Gateway agent targets match official OpenClaw agent naming conventions. */
export function parseOpenClawModels(body: unknown): OpenClawModel[] {
  const data = (body as { data?: unknown } | null)?.data;
  if (!Array.isArray(data)) {
    throw new OpenClawError(
      "invalid",
      "Gateway returned an unexpected /models response.",
    );
  }
  const seen = new Set<string>();
  const out: OpenClawModel[] = [];
  for (const m of data) {
    const id =
      typeof (m as { id?: unknown })?.id === "string"
        ? (m as { id: string }).id.trim()
        : "";
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push({ id, agent: isOpenClawAgentTarget(id) });
  }
  return out.sort(
    (a, b) => Number(b.agent) - Number(a.agent) || a.id.localeCompare(b.id),
  );
}

export type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

let defaultProxyFetch: FetchLike | null = null;
async function resolveFetch(customFetch?: FetchLike): Promise<FetchLike> {
  if (customFetch) return customFetch;
  if (defaultProxyFetch) return defaultProxyFetch;
  const mod = await import("./proxyFetch");
  defaultProxyFetch = mod.createProxyFetch({ allowPrivateNetwork: true });
  return defaultProxyFetch;
}

export async function discoverOpenClawModels(
  baseURL: string,
  token?: string | null,
  signal?: AbortSignal,
  customFetch?: FetchLike,
): Promise<OpenClawModel[]> {
  const base = normalizeOpenClawBaseURL(baseURL);
  if (!base) {
    throw new OpenClawError("invalid", "Enter the Gateway URL first.");
  }
  signal?.throwIfAborted?.();

  const fetcher = await resolveFetch(customFetch);
  let res: Response;
  try {
    res = await fetcher(`${base}/models`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal,
    });
  } catch (e: unknown) {
    if (signal?.aborted || (e instanceof Error && e.name === "AbortError")) {
      throw e;
    }
    throw new OpenClawError(
      "unreachable",
      `Cannot reach the OpenClaw Gateway at ${base}.`,
    );
  }

  if (res.status === 401 || res.status === 403) {
    throw new OpenClawError(
      "auth",
      "Gateway rejected the credential. Sign in with the official OpenClaw login and paste the Gateway token.",
    );
  }

  if (!res.ok) {
    throw new OpenClawError(
      "http",
      `Gateway responded with HTTP ${res.status}.`,
    );
  }

  const rawJson = await res.json().catch(() => null);
  return parseOpenClawModels(rawJson);
}
