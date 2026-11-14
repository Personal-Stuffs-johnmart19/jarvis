import { createRoot } from "react-dom/client";
import { useState } from "react";
import { RenderedMessage } from "../workbench/src/modules/ai/components/AiChat";
import { setLocale } from "../src/localization";
import { useLocale } from "../src/i18n";
import type { UIMessage } from "ai";
import "../workbench/src/styles/globals.css";

const responses: [string, boolean][] = [];
const qa = {
  locale: setLocale, responses,
  surface: (_visible: boolean) => {},
  deferred: (_deferred: boolean) => {},
  remount: () => {},
};
Object.assign(window, { __chatActivityQA: qa });
const message: UIMessage = { id: "fixture", role: "assistant", parts: [
  { type: "reasoning", text: "Private reasoning fixture" },
  { type: "tool-mcp_catalog", toolCallId: "catalog1", state: "output-available", input: {}, output: { catalog: "raw catalog fixture" } },
  { type: "step-start" },
  { type: "dynamic-tool", toolName: "jarvis_catalog", toolCallId: "catalog2", state: "output-available", input: {}, output: { catalog: "raw second fixture" } },
  { type: "text", text: "The weather answer remains visible." },
  { type: "tool-write_file", toolCallId: "approve", state: "approval-requested", input: { path: "fixture.txt", content: "fixture" }, approval: { id: "approval-fixture" } },
  { type: "tool-jarvis_action", toolCallId: "failure", state: "output-available", input: {}, output: { ok: false, error: "Synthetic visible failure" } },
] };
function Fixture() {
  useLocale();
  const [visible, setVisible] = useState(true);
  const [deferred, setDeferred] = useState(false);
  const [revision, setRevision] = useState(0);
  qa.surface = setVisible;
  qa.deferred = setDeferred;
  qa.remount = () => setRevision(value => value + 1);
  return <main className="max-w-xl p-4" style={{ display: visible ? undefined : "none" }}>
    <RenderedMessage key={revision} message={message} streaming={false} deferOffscreen={deferred}
      onApproval={(id, approved) => responses.push([id, approved])} />
    <span className="animate-spin" data-loop-fixture aria-hidden="true">⟳</span>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
