import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { cataloguePage } from "../../apps/desktop/workbench/src/modules/ai/tools/catalogue.ts";
import { activeAgentToolNames, COMPACT_CONTEXT_LIMIT, JARVIS_SERVICE_INSTRUCTIONS, everydayCapabilityRoutes } from "../../apps/desktop/workbench/src/modules/ai/lib/serviceRouting.ts";
test("catalogue deduplicates snapshots, ranks names first and sends schemas only on exact lookup", () => {
  const base = {
    description: "utility ".repeat(300),
    parameters: { type: "object" },
    category: "workspace",
    executor: "native-host",
    approval: "required",
    available: true,
  };
  const remote = [
    { ...base, name: "utility_run", approval: "native-policy" },
    { ...base, name: "archive_list" },
  ];
  const local = [
    { ...base, name: "utility_run" },
    { ...base, name: "utility_catalog" },
  ];
  const options = { query: "utility", category: "", cursor: 0, limit: 8 };
  const broad = cataloguePage(remote, local, options);
  assert.equal(broad.total, 3);
  assert.equal(broad.items[0].name, "utility_catalog");
  assert.equal(broad.items[1].name, "utility_run");
  assert.equal(broad.items[1].approval, "required");
  assert.ok(
    broad.items.every(
      (item) => item.parameters === undefined && item.description.length <= 280,
    ),
  );
  const exact = cataloguePage(remote, local, {
    ...options,
    query: "utility_run",
  });
  assert.deepEqual(exact.items[0].parameters, { type: "object" });
  assert.equal(
    cataloguePage(remote, local, { ...options, query: "unknown" }).total,
    0,
  );
});

test("browser, YouTube and music discovery expose the real browser handler and full exact schema", () => {
  const declarations = JSON.parse(readFileSync(new URL("../../local_mode/tool_declarations.json", import.meta.url), "utf8"));
  const capabilities = declarations.map(item => ({...item, category:"tools", executor:"python", approval:"dynamic", available:true}));
  for (const query of ["browser", "YouTube", "music", "browser_control"]) {
    const page = cataloguePage(capabilities, [], {query, category:"", cursor:0, limit:8});
    const browser = page.items.find(item => item.name === "browser_control");
    assert.ok(browser, `No browser capability for ${query}`);
    assert.match(browser.description, /YouTube\/music playback/);
    if (query === "browser_control") {
      assert.match(browser.parameters.properties.action.description, /media_play/);
      assert.deepEqual(browser.parameters.required, ["action"]);
    } else assert.equal(browser.parameters, undefined);
  }
});

test("generic execution remains registered for history/approval but is discovered through the shared catalogue", () => {
  const tools = Object.fromEntries(["read_file", "jarvis_catalog", "jarvis_action", "utility_catalog", "utility_run", "run_code"].map(name => [name, {}]));
  assert.deepEqual(activeAgentToolNames(tools), ["read_file", "jarvis_catalog", "jarvis_action"]);
  assert.ok(tools.utility_run);
  assert.deepEqual(activeAgentToolNames({run_code:{}}), ["run_code"]);
  assert.match(everydayCapabilityRoutes(["browser_control"]), /direct browser_control immediately/);
  assert.match(everydayCapabilityRoutes(["browser_control"]), /only verified=true confirms playback/);
  assert.doesNotMatch(JARVIS_SERVICE_INSTRUCTIONS, /If a catalogue search is empty, search utility_catalog/);
});

test("4K through 8K models keep the compact direct set and child result recovery while optional handlers stay registered", () => {
  const tools=Object.fromEntries(["read_file","grep","glob","data_query","mcp_catalog","jarvis_catalog","jarvis_action","browser_control","screen_capture","tool_result_read","utility_run"].map(name=>[name,{}]));
  assert.equal(COMPACT_CONTEXT_LIMIT,8192);
  for (const context of [4096,4097,6144,8192])
    assert.deepEqual(activeAgentToolNames(tools,context),["read_file","jarvis_catalog","jarvis_action","browser_control","screen_capture","tool_result_read"]);
  assert.ok(tools.grep && tools.data_query && tools.mcp_catalog && tools.utility_run);
  assert.ok(activeAgentToolNames(tools,8193).includes("grep"));
});
