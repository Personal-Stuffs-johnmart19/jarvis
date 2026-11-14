import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { projectInterruptedHistory } from "../../apps/desktop/workbench/src/modules/ai/lib/interruptedHistory.ts";
const { convertToModelMessages, generateText } = createRequire(new URL("../../apps/desktop/package.json", import.meta.url))("ai");
const pending = {type:"tool-run_subagent",toolCallId:"child",state:"input-available",input:{prompt:"inspect"}};
const approvedPending = {type:"tool-jarvis_action",toolCallId:"proc",state:"approval-responded",input:{name:"bash_run",args:{command:"python parent.py"}},approval:{id:"approve-proc",approved:true}};
test("interrupted calls become explicit unknown outcomes in model context only", async () => {
  const messages = [{role:"user",parts:[{type:"text",text:"first"}]},
    {role:"assistant",parts:[pending]}, {role:"user",parts:[{type:"text",text:"next"}]}];
  const saved = JSON.stringify(messages);
  const model = {specificationVersion:"v3",provider:"offline",modelId:"fixture",supportedUrls:{},
    doGenerate:async()=>{throw new Error("MODEL_REACHED");}};
  await assert.rejects(generateText({model,messages:await convertToModelMessages(messages),maxRetries:0}), /Tool result is missing/);
  const projected = projectInterruptedHistory(messages);
  const converted = await convertToModelMessages(projected);
  await assert.rejects(generateText({model,messages:converted,maxRetries:0}), /MODEL_REACHED/);
  assert.equal(JSON.stringify(messages), saved);
  assert.equal(projected[1].parts[0].toolCallId,"child");
  assert.deepEqual(projected[1].parts[0].input,pending.input);
  assert.ok(converted.some(m=>m.role==="tool" && m.content.some(p=>p.toolCallId==="child")));
  assert.match(projected[1].parts[0].errorText,/Outcome unknown/);
});
test("interrupted approved calls preserve approval linkage and never replay side effects", async () => {
  const messages = [{role:"user",parts:[{type:"text",text:"run process"}]},
    {role:"assistant",parts:[approvedPending]}, {role:"user",parts:[{type:"text",text:"after stop"}]}];
  const saved = JSON.stringify(messages);
  const model = {specificationVersion:"v3",provider:"offline",modelId:"fixture",supportedUrls:{},
    doGenerate:async()=>{throw new Error("MODEL_REACHED");}};
  await assert.rejects(generateText({model,messages:await convertToModelMessages(messages),maxRetries:0}), /Tool result is missing/);
  const projected = projectInterruptedHistory(messages);
  const converted = await convertToModelMessages(projected);
  await assert.rejects(generateText({model,messages:converted,maxRetries:0}), /MODEL_REACHED/);
  assert.equal(JSON.stringify(messages), saved);
  assert.equal(projected[1].parts[0].toolCallId, "proc");
  assert.deepEqual(projected[1].parts[0].input, approvedPending.input);
  assert.deepEqual(projected[1].parts[0].approval, {id:"approve-proc",approved:true});
  assert.match(projected[1].parts[0].errorText, /Outcome unknown/);
  const assistantMsg = converted.find(m=>m.role==="assistant");
  const toolMsg = converted.find(m=>m.role==="tool");
  assert.ok(assistantMsg?.content.some(p=>p.type==="tool-call" && p.toolCallId==="proc"));
  assert.ok(assistantMsg?.content.some(p=>p.type==="tool-approval-request" && p.approvalId==="approve-proc" && p.toolCallId==="proc"));
  assert.ok(toolMsg?.content.some(p=>p.type==="tool-approval-response" && p.approvalId==="approve-proc" && p.approved===true));
  assert.ok(toolMsg?.content.some(p=>p.type==="tool-result" && p.toolCallId==="proc"));
});
test("active calls, approvals and completed results remain unchanged", () => {
  const approval = {...pending,state:"approval-requested",approval:{id:"approve-child"}};
  const done = {...pending,state:"output-available",output:{ok:true}};
  const messages = [{role:"assistant",parts:[approval,done]},
    {role:"user",parts:[{type:"text",text:"current"}]}, {role:"assistant",parts:[pending,approvedPending]}];
  assert.deepEqual(projectInterruptedHistory(messages),messages);
  assert.equal(projectInterruptedHistory(messages)[0],messages[0]);
  assert.equal(projectInterruptedHistory(messages)[2],messages[2]);
});
