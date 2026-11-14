import test from "node:test";
import assert from "node:assert/strict";
import { allowsLocalEditorContext, refersToEditor, formatEditorContext, EDITOR_CONTEXT_CHAR_LIMIT } from "../../apps/desktop/workbench/src/modules/ai/lib/editorContext.ts";

test("automatic editor excerpts stay confined to the explicitly local LM Studio destination", () => {
  assert.equal(allowsLocalEditorContext("lmstudio-local", "http://127.0.0.1:1234/v1"), true);
  for (const [model,url] of [["gemini-3.8-flash","http://localhost:1234/v1"],["lmstudio-local","https://example.com/v1"],["lmstudio-local","http://localhost.evil.test:1234"],["lmstudio-local",undefined]])
    assert.equal(allowsLocalEditorContext(model,url), false);
});
test("open XML questions use a bounded live buffer rather than another file search", () => {
  assert.equal(refersToEditor("What's in this xml that I opened?"), true);
  assert.equal(refersToEditor("Что в открытом файле?"), true);
  assert.equal(refersToEditor("What is the weather today?"), false);
  const text = '<resources><string name="unsaved">editor text</string></resources>';
  const message = formatEditorContext({path:"/home/user/project/compose_strings.xml",text,truncated:false});
  const data = JSON.parse(message.slice(message.indexOf("{")));
  assert.equal(data.text, text);
  assert.equal(data.source, "editor_buffer");
  assert.equal(data.truncated, false);
  const bounded = formatEditorContext({path:"large.xml",text:"x".repeat(100000),truncated:false});
  assert.ok(bounded.length < EDITOR_CONTEXT_CHAR_LIMIT + 500);
  assert.equal(JSON.parse(bounded.slice(bounded.indexOf("{"))).truncated, true);
});

test("local models use the existing compact instruction preset", async () => {
  const {selectSystemPrompt} = await import("../../apps/desktop/workbench/src/modules/ai/config.ts");
  assert.ok(selectSystemPrompt("lmstudio-local").length < selectSystemPrompt("gpt-5.6-sol").length * 0.6);
  assert.match(selectSystemPrompt("lmstudio-local"), /approval card/);
});

test("automatic editor attachment refuses unresolved or sensitive canonical targets", async () => {
  const { checkReadableCanonical } = await import("./security_fixture.mjs");
  assert.equal((await checkReadableCanonical("/home/user/normal.xml", async () => { throw new Error("cross-distro target"); })).ok, false);
  assert.equal((await checkReadableCanonical("/home/user/normal.xml", async () => "/home/user/.ssh/id_rsa")).ok, false);
});
