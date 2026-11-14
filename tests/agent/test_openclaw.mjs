import test from "node:test";
import assert from "node:assert/strict";
import {
  OPENCLAW_DEFAULT_BASE_URL,
  OPENCLAW_ENDPOINT_NAME,
  OpenClawError,
  discoverOpenClawModels,
  isOpenClawAgentTarget,
  normalizeOpenClawBaseURL,
  parseOpenClawModels,
} from "../../apps/desktop/workbench/src/modules/ai/lib/openclaw.ts";

test("constants match expectations", () => {
  assert.equal(OPENCLAW_DEFAULT_BASE_URL, "http://127.0.0.1:18789/v1");
  assert.equal(OPENCLAW_ENDPOINT_NAME, "OpenClaw Gateway");
});

test("isOpenClawAgentTarget classifies agent and model IDs", () => {
  assert.equal(isOpenClawAgentTarget("openclaw"), true);
  assert.equal(isOpenClawAgentTarget("openclaw/default"), true);
  assert.equal(isOpenClawAgentTarget("openclaw/sub-agent"), true);
  assert.equal(isOpenClawAgentTarget("openclaw:assistant"), true);
  assert.equal(isOpenClawAgentTarget("agent:planner"), true);
  assert.equal(isOpenClawAgentTarget("gpt-4o"), false);
  assert.equal(isOpenClawAgentTarget("claude-3-5-sonnet"), false);
  assert.equal(isOpenClawAgentTarget("qwen-2.5-coder"), false);
});

test("normalizeOpenClawBaseURL normalizes valid endpoints", () => {
  assert.equal(normalizeOpenClawBaseURL("  "), "");
  assert.equal(normalizeOpenClawBaseURL(""), "");
  assert.equal(
    normalizeOpenClawBaseURL("127.0.0.1:18789"),
    "http://127.0.0.1:18789/v1",
  );
  assert.equal(
    normalizeOpenClawBaseURL("localhost:18789"),
    "http://localhost:18789/v1",
  );
  assert.equal(
    normalizeOpenClawBaseURL("http://127.0.0.1:18789"),
    "http://127.0.0.1:18789/v1",
  );
  assert.equal(
    normalizeOpenClawBaseURL("http://127.0.0.1:18789/"),
    "http://127.0.0.1:18789/v1",
  );
  assert.equal(
    normalizeOpenClawBaseURL("http://127.0.0.1:18789/v1"),
    "http://127.0.0.1:18789/v1",
  );
  assert.equal(
    normalizeOpenClawBaseURL("http://127.0.0.1:18789/v1/"),
    "http://127.0.0.1:18789/v1",
  );
  assert.equal(
    normalizeOpenClawBaseURL("https://gateway.internal:9000"),
    "https://gateway.internal:9000/v1",
  );
  assert.equal(
    normalizeOpenClawBaseURL("https://gw.example.com/custom/api"),
    "https://gw.example.com/custom/api/v1",
  );
  assert.equal(
    normalizeOpenClawBaseURL("https://gw.example.com/custom/api/v1/"),
    "https://gw.example.com/custom/api/v1",
  );
});

test("normalizeOpenClawBaseURL rejects non-http schemes", () => {
  assert.throws(
    () => normalizeOpenClawBaseURL("ftp://127.0.0.1:18789"),
    (err) => err instanceof OpenClawError && err.kind === "invalid",
  );
  assert.throws(
    () => normalizeOpenClawBaseURL("ws://127.0.0.1:18789"),
    (err) => err instanceof OpenClawError && err.kind === "invalid",
  );
  assert.throws(
    () => normalizeOpenClawBaseURL("file:///etc/hosts"),
    (err) => err instanceof OpenClawError && err.kind === "invalid",
  );
});

test("normalizeOpenClawBaseURL rejects embedded credentials", () => {
  assert.throws(
    () => normalizeOpenClawBaseURL("http://user:pass@127.0.0.1:18789"),
    (err) =>
      err instanceof OpenClawError &&
      err.kind === "invalid" &&
      err.message.includes("credentials"),
  );
  assert.throws(
    () => normalizeOpenClawBaseURL("admin:secret@localhost:18789"),
    (err) =>
      err instanceof OpenClawError &&
      err.kind === "invalid" &&
      err.message.includes("credentials"),
  );
});

test("normalizeOpenClawBaseURL rejects query parameters and hash fragments", () => {
  assert.throws(
    () => normalizeOpenClawBaseURL("http://127.0.0.1:18789/v1?token=xyz"),
    (err) =>
      err instanceof OpenClawError &&
      err.kind === "invalid" &&
      err.message.includes("query parameters"),
  );
  assert.throws(
    () => normalizeOpenClawBaseURL("127.0.0.1:18789?token=xyz"),
    (err) =>
      err instanceof OpenClawError &&
      err.kind === "invalid" &&
      err.message.includes("query parameters"),
  );
  assert.throws(
    () => normalizeOpenClawBaseURL("http://127.0.0.1:18789/v1#section"),
    (err) =>
      err instanceof OpenClawError &&
      err.kind === "invalid" &&
      err.message.includes("hash fragment"),
  );
  assert.throws(
    () => normalizeOpenClawBaseURL("127.0.0.1:18789#section"),
    (err) =>
      err instanceof OpenClawError &&
      err.kind === "invalid" &&
      err.message.includes("hash fragment"),
  );
});

test("discoverOpenClawModels mock transport: success discovery with header and agent sorting", async () => {
  let requestedUrl = "";
  let authHeader = "";

  const mockTransport = async (url, init) => {
    requestedUrl = String(url);
    authHeader = init?.headers?.Authorization ?? "";
    return new Response(
      JSON.stringify({
        data: [
          { id: "gpt-4o" },
          { id: "openclaw/coder" },
          { id: "agent:planner" },
          { id: "llama-3.3-70b" },
        ],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };

  const models = await discoverOpenClawModels(
    "127.0.0.1:18789",
    "secret-gw-token",
    undefined,
    mockTransport,
  );

  assert.equal(requestedUrl, "http://127.0.0.1:18789/v1/models");
  assert.equal(authHeader, "Bearer secret-gw-token");
  assert.deepEqual(models, [
    { id: "agent:planner", agent: true },
    { id: "openclaw/coder", agent: true },
    { id: "gpt-4o", agent: false },
    { id: "llama-3.3-70b", agent: false },
  ]);
});

test("discoverOpenClawModels mock transport: 401 and 403 authentication failures", async () => {
  const mock401 = async () =>
    new Response("Unauthorized", { status: 401 });

  await assert.rejects(
    () =>
      discoverOpenClawModels(
        "http://127.0.0.1:18789",
        "bad-key",
        undefined,
        mock401,
      ),
    (err) =>
      err instanceof OpenClawError &&
      err.kind === "auth" &&
      err.message.includes("Gateway rejected the credential"),
  );

  const mock403 = async () =>
    new Response("Forbidden", { status: 403 });

  await assert.rejects(
    () =>
      discoverOpenClawModels(
        "http://127.0.0.1:18789",
        null,
        undefined,
        mock403,
      ),
    (err) => err instanceof OpenClawError && err.kind === "auth",
  );
});

test("discoverOpenClawModels mock transport: abort signal propagation and pre-aborted checks", async () => {
  const controller = new AbortController();

  const mockAbortingTransport = async (_url, init) => {
    assert.equal(init?.signal, controller.signal);
    controller.abort();
    const abortErr = new Error("The operation was aborted");
    abortErr.name = "AbortError";
    throw abortErr;
  };

  await assert.rejects(
    () =>
      discoverOpenClawModels(
        "http://127.0.0.1:18789",
        null,
        controller.signal,
        mockAbortingTransport,
      ),
    (err) => err?.name === "AbortError",
  );

  const preAborted = AbortSignal.abort();
  await assert.rejects(
    () =>
      discoverOpenClawModels(
        "http://127.0.0.1:18789",
        null,
        preAborted,
        async () => {
          throw new Error("Should not be called");
        },
      ),
    (err) => err?.name === "AbortError",
  );
});

test("discoverOpenClawModels mock transport: malformed response handling", async () => {
  const mockHtmlTransport = async () =>
    new Response("<html>502 Bad Gateway</html>", {
      status: 200,
      headers: { "Content-Type": "text/html" },
    });

  await assert.rejects(
    () =>
      discoverOpenClawModels(
        "http://127.0.0.1:18789",
        null,
        undefined,
        mockHtmlTransport,
      ),
    (err) =>
      err instanceof OpenClawError &&
      err.kind === "invalid" &&
      err.message.includes("unexpected /models response"),
  );

  const mockNoDataTransport = async () =>
    new Response(JSON.stringify({ models: ["a"] }), { status: 200 });

  await assert.rejects(
    () =>
      discoverOpenClawModels(
        "http://127.0.0.1:18789",
        null,
        undefined,
        mockNoDataTransport,
      ),
    (err) => err instanceof OpenClawError && err.kind === "invalid",
  );
});

test("discoverOpenClawModels mock transport: unreachable and http errors", async () => {
  const mockNetworkFail = async () => {
    throw new TypeError("Failed to fetch");
  };

  await assert.rejects(
    () =>
      discoverOpenClawModels(
        "http://127.0.0.1:18789",
        null,
        undefined,
        mockNetworkFail,
      ),
    (err) =>
      err instanceof OpenClawError &&
      err.kind === "unreachable" &&
      err.message.includes("Cannot reach the OpenClaw Gateway"),
  );

  const mock500 = async () =>
    new Response("Internal error", { status: 500 });

  await assert.rejects(
    () =>
      discoverOpenClawModels(
        "http://127.0.0.1:18789",
        null,
        undefined,
        mock500,
      ),
    (err) =>
      err instanceof OpenClawError &&
      err.kind === "http" &&
      err.message.includes("HTTP 500"),
  );
});
