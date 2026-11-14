import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { childCapabilityTools } from "../../apps/desktop/workbench/src/modules/ai/agents/childCapabilities.ts";
import { executeSubagentTool } from "../../apps/desktop/workbench/src/modules/ai/agents/subagentExecution.ts";

const options = (signal, id = "child-call") => ({
  toolCallId: id,
  messages: [],
  abortSignal: signal,
});

test("compact child catalogue actions validate and execute only local approval-free reads", async () => {
  const require = createRequire(new URL("../../apps/desktop/package.json", import.meta.url));
  const { z } = require("zod");
  const calls = [], handoffs = [];
  let prepared = 0;
  const canonical = {
    jarvis_catalog: { execute: async () => ({items: []}) },
    jarvis_action: { needsApproval: () => { prepared++; return true; }, execute: () => { throw new Error("Parent executor must not run"); } },
  };
  for (const name of ["grep", "list_directory", "data_query", "mcp_catalog", "utility_catalog"])
    canonical[name] = { inputSchema: z.object({query: z.string().min(1), limit: z.number().int().default(2)}), execute: async (input, opts) => { calls.push({name,input,opts}); return {ok:true}; } };
  const child = childCapabilityTools(canonical, handoffs);
  for (const name of ["grep", "list_directory", "data_query", "mcp_catalog", "utility_catalog"])
    assert.deepEqual(await child.jarvis_action.execute({name,args:{query:"Звіт"}}, options()), {ok:true});
  assert.equal(calls.length, 5);
  assert.ok(calls.every(call => call.input.limit === 2 && call.opts.toolCallId === "child-call"));
  const invalid = await child.jarvis_action.execute({name:"grep",args:{query:42}}, options());
  assert.match(invalid.error, /Invalid child read/);
  assert.equal(calls.length, 5);
  assert.equal(handoffs.length, 0);
  assert.equal(prepared, 0);
  for (const needsApproval of [true, () => false]) {
    canonical.grep = {...canonical.grep, needsApproval};
    const guarded = childCapabilityTools(canonical, handoffs);
    assert.equal((await guarded.jarvis_action.execute({name:"grep",args:{query:"secret"}}, options())).status, "requires_parent_approval");
  }
  assert.equal(calls.length, 5);
  assert.equal((await child.jarvis_action.execute({name:"documents.read",args:{id:"remote"}}, options())).status,"requires_parent_approval");
});

test("routed child reads preserve abort boundaries before dispatch and after completion", async () => {
  const require = createRequire(new URL("../../apps/desktop/package.json", import.meta.url));
  const { z } = require("zod");
  const controller = new AbortController();
  let calls = 0;
  const child = childCapabilityTools({
    jarvis_action: {execute: () => { throw new Error("Parent executor must not run"); }},
    grep: {inputSchema:z.object({query:z.string()}),execute:async()=>{calls++;controller.abort();return {text:"late"};}},
  }, []);
  await assert.rejects(child.jarvis_action.execute({name:"grep",args:{query:"x"}},options(controller.signal)), {name:"AbortError"});
  assert.equal(calls,1);
  await assert.rejects(child.jarvis_action.execute({name:"grep",args:{query:"x"}},options(controller.signal)), {name:"AbortError"});
  assert.equal(calls,1);
});

test("EN/RU child tasks reuse canonical lazy catalogue and Unicode-path read handlers", async () => {
  const calls = [];
  const handoffs = [];
  const canonical = {
    jarvis_catalog: {
      execute: async (input) => {
        calls.push(["catalog", input]);
        return { items: [{ name: "documents.read" }], next_cursor: null };
      },
    },
    mcp_catalog: {
      execute: async (input) => {
        calls.push(["mcp", input]);
        return { servers: [{ id: "files", enabled: true, connected: false }] };
      },
    },
    read_file: {
      execute: async (input) => {
        calls.push(["read", input]);
        return { path: input.path, content: "Звіт / report" };
      },
    },
  };
  const child = childCapabilityTools(canonical, handoffs);
  for (const query of ["read documents", "прочитать документы"]) {
    await child.jarvis_catalog.execute({ query }, options());
    await child.mcp_catalog.execute({}, options());
  }
  const result = await child.read_file.execute(
    { path: "D:/Проєкт/звіт 😀.txt" },
    options(),
  );
  assert.equal(result.path, "D:/Проєкт/звіт 😀.txt");
  assert.equal(calls.length, 5);
  assert.equal(handoffs.length, 0);
  assert.equal(Object.hasOwn(child, "run_subagent"), false);
});

test("child actions return exact bounded parent handoff without preparing receipts or executing", async () => {
  let prepared = 0,
    executed = 0;
  const handoffs = [];
  const child = childCapabilityTools(
    {
      jarvis_action: {
        needsApproval: () => {
          prepared++;
          return true;
        },
        execute: () => {
          executed++;
        },
      },
    },
    handoffs,
  );
  const input = {
    name: "mcp_call",
    args: {
      server_id: "files",
      tool_name: "write",
      arguments: { path: "D:/Проєкт/звіт.txt", text: "Готово" },
    },
  };
  const result = await child.jarvis_action.execute(
    input,
    options(undefined, "child-mcp"),
  );
  assert.equal(result.status, "requires_parent_approval");
  assert.equal(result.child_tool_call_id, "child-mcp");
  assert.deepEqual({ name: result.name, args: result.args }, input);
  input.args.arguments.text = "changed";
  assert.equal(result.args.arguments.text, "Готово");
  assert.equal(prepared, 0);
  assert.equal(executed, 0);
  assert.equal(child.jarvis_action.needsApproval, undefined);
  for (let i = 0; i < 8; i++)
    await child.jarvis_action.execute(
      { name: "write_file", args: { path: "report.txt" } },
      options(undefined, `child-${i}`),
    );
  assert.equal(handoffs.length, 4);
  assert.equal(executed, 0);
});

test("approval-marked reads are deferred and child result handoffs reach the parent", async () => {
  const handoffs = [];
  let executed = 0;
  const child = childCapabilityTools(
    {
      read_file: {
        needsApproval: true,
        execute: () => {
          executed++;
        },
      },
    },
    handoffs,
  );
  await child.read_file.execute({ path: "report.txt" }, options());
  const result = await executeSubagentTool({
    type: "general",
    prompt: "прочитать файл",
    captureConfig: () => ({}),
    run: async () => ({
      summary: "Pending parent policy",
      stepCount: 1,
      durationMs: 1,
      pending_actions: handoffs,
    }),
  });
  assert.equal(executed, 0);
  assert.equal(result.pending_actions[0].name, "read_file");
});

test("cancelled child performs no catalogue call or handoff and rejects late reads", async () => {
  const controller = new AbortController();
  controller.abort();
  let executed = 0;
  const handoffs = [];
  const child = childCapabilityTools(
    {
      jarvis_catalog: {
        execute: () => {
          executed++;
        },
      },
      jarvis_action: {
        execute: () => {
          executed++;
        },
      },
    },
    handoffs,
  );
  await assert.rejects(
    child.jarvis_catalog.execute({}, options(controller.signal)),
    { name: "AbortError" },
  );
  await assert.rejects(
    child.jarvis_action.execute(
      { name: "write_file", args: {} },
      options(controller.signal),
    ),
    { name: "AbortError" },
  );
  assert.equal(executed, 0);
  assert.equal(handoffs.length, 0);
  const late = new AbortController();
  const reads = childCapabilityTools(
    {
      read_file: {
        execute: async () => {
          late.abort();
          return { content: "late" };
        },
      },
    },
    [],
  );
  await assert.rejects(
    reads.read_file.execute({ path: "report.txt" }, options(late.signal)),
    { name: "AbortError" },
  );
});

test("child wrappers add zero schema bytes and catalogue pages keep full schemas lazy", async () => {
  const { cataloguePage } =
    await import("../../apps/desktop/workbench/src/modules/ai/tools/catalogue.ts");
  const descriptors = Array.from({ length: 100 }, (_, i) => ({
    name: `fixture_${i}`,
    description: "Проверить файл / inspect file ".repeat(30),
    parameters: {
      type: "object",
      properties: {
        path: { type: "string", description: "D:/Проєкт/звіт.txt" },
      },
    },
    category: "workspace",
    executor: "native-host",
    approval: "dynamic",
    available: true,
  }));
  const canonical = {
    jarvis_catalog: {
      description: "Discover tools",
      inputSchema: {
        type: "object",
        properties: { query: { type: "string" } },
      },
      execute: async () =>
        cataloguePage(descriptors, [], {
          query: "",
          category: "",
          cursor: 0,
          limit: 8,
        }),
    },
  };
  const child = childCapabilityTools(canonical, []);
  const schemas = (tools) =>
    JSON.stringify(
      Object.entries(tools).map(([name, t]) => ({
        name,
        description: t.description,
        inputSchema: t.inputSchema,
      })),
    );
  assert.equal(
    Buffer.byteLength(schemas(child)),
    Buffer.byteLength(schemas(canonical)),
  );
  const page = await child.jarvis_catalog.execute({}, options());
  assert.ok(Buffer.byteLength(JSON.stringify(page)) < 5000);
  assert.ok(Buffer.byteLength(JSON.stringify(descriptors)) > 150000);
  assert.ok(page.items.every((item) => item.parameters === undefined));
});

import {
  archiveChildResults,
  childModelOutput,
} from "../../apps/desktop/workbench/src/modules/ai/agents/childResults.ts";
import { readToolResult } from "../../apps/desktop/workbench/src/modules/ai/lib/toolHistory.ts";

test("saved parent output preserves child IDs and full evidence while model output stays compact", async () => {
  const archived = archiveChildResults([
    {
      toolResults: [
        {
          toolCallId: "child-read-ru",
          toolName: "read_file",
          output: {
            path: "D:/Проєкт/звіт.txt",
            text: "я".repeat(15000) + "RECOVER_CHILD_TAIL",
          },
        },
      ],
    },
  ]);
  const saved = await executeSubagentTool({
    type: "general",
    prompt: "inspect / прочитать",
    captureConfig: () => ({}),
    run: async () => ({
      summary: "Read complete",
      stepCount: 1,
      durationMs: 1,
      ...archived,
    }),
  });
  const restored = JSON.parse(JSON.stringify(saved));
  assert.equal(
    restored.child_tool_results[0].child_tool_call_id,
    "child-read-ru",
  );
  assert.equal(
    restored.child_tool_results[0].output.path,
    "D:/Проєкт/звіт.txt",
  );
  const projection = childModelOutput(restored, "outer-subagent");
  assert.equal(projection.value.full_output_tool_call_id, "outer-subagent");
  assert.ok(Buffer.byteLength(JSON.stringify(projection)) < 2000);
  assert.ok(!JSON.stringify(projection).includes("RECOVER_CHILD_TAIL"));
  const messages = [
    {
      parts: [
        {
          type: "dynamic-tool",
          state: "output-available",
          toolCallId: "outer-subagent",
          output: restored,
        },
      ],
    },
  ];
  const full = JSON.stringify(restored),
    offset = full.indexOf("RECOVER_CHILD_TAIL") - 10;
  assert.ok(
    readToolResult(messages, "outer-subagent", offset, 100).text.includes(
      "RECOVER_CHILD_TAIL",
    ),
  );
  assert.ok(readToolResult(messages, "other-session", 0, 100).error);
});

test("child evidence and large handoffs expose explicit truncation and recovery metadata", () => {
  const archived = archiveChildResults([
    {
      toolResults: Array.from({ length: 40 }, (_, i) => ({
        toolCallId: `child-${i}`,
        toolName: "read_file",
        output: "я".repeat(200000),
      })),
    },
  ]);
  assert.equal(archived.child_tool_results.length, 32);
  assert.equal(archived.child_tool_results_omitted, 8);
  assert.ok(archived.child_tool_results[0].truncated);
  assert.equal(archived.child_tool_results[0].output.total_bytes, 400002);
  assert.ok(
    archived.child_tool_results[0].output.retained_bytes <=
      archived.child_tool_result_limit_bytes,
  );
  const saved = {
    summary: "done".repeat(2000),
    ...archived,
    pending_actions: [
      {
        status: "requires_parent_approval",
        child_tool_call_id: "child-large",
        name: "write_file",
        args: { path: "D:/Проєкт/звіт.txt", text: "я".repeat(15000) },
      },
    ],
  };
  const projection = childModelOutput(saved, "outer-large");
  assert.equal(projection.value.pending_actions[0].args_omitted, true);
  assert.equal(projection.value.summary_truncated, true);
  assert.equal(projection.value.child_tool_results_omitted, 8);
  assert.ok(Buffer.byteLength(JSON.stringify(projection)) < 6000);
  assert.equal(saved.pending_actions[0].args.text.length, 15000);
});
