import { useState } from "react";
import { createRoot } from "react-dom/client";
import { SettingsPanel } from "../src/SettingsPanel";
import { CustomEndpointCard } from "../workbench/src/settings/sections/ModelsSection";
import type { CustomEndpoint } from "../workbench/src/modules/ai/config";
import { PROVIDERS } from "../workbench/src/modules/ai/config";
import { ProviderKeyCard } from "../workbench/src/settings/components/ProviderKeyCard";

const qa = (window as any).__settingsControlsQA = {
  calls: [] as any[], finishPreview: null as any, removed: 0,
};
// Every host call stays inside this source fixture. Never connect to a real service.
(window as any).__TAURI_INTERNALS__ = {
  invoke: async (command: string) => {
    if (command === "lm_ping") return 500;
    throw new Error(`Unexpected native call: ${command}`);
  },
};
const run = async (method: string, params?: unknown): Promise<any> => {
  qa.calls.push({ method, params });
  if (method === "audio.devices") return [];
  if (method === "voices") return [{ id: "silero:xenia", name: "Silero — Xenia" }];
  if (method === "services.read") return undefined;
  if (method === "settings.voice") return new Promise(resolve => { qa.finishPreview = resolve; });
  if (method === "control") return { ok: true };
  throw new Error(`Unexpected backend call: ${method}`);
};
function Fixture() {
  const [endpoint, setEndpoint] = useState<CustomEndpoint>({
    id: "fixture", name: "Offline fixture", baseURL: "http://127.0.0.1:1/v1", modelId: "synthetic", contextLimit: 8192,
  });
  return <main>
    <section aria-label="Speech fixture">
      <SettingsPanel tab="Speech & Voice" snapshot={null} hints={[]} setHints={() => {}}
        run={run} refresh={async () => {}} setTab={() => {}} reduced={false} setReduced={() => {}} />
    </section>
    <section aria-label="Endpoint fixture">
      <CustomEndpointCard endpoint={endpoint} endpointKey={null} onSaveKey={async () => {}}
        onClearKey={async () => {}} onUpdate={async patch => setEndpoint(current => ({ ...current, ...patch }))}
        onRemove={() => { qa.removed++; }} />
    </section>
    <section aria-label="Provider key fixture">
      <ProviderKeyCard provider={PROVIDERS.find(provider => provider.id === "openai")!}
        currentKey="sk-fixture-unverified" onSave={async () => {}} onClear={async () => {}} />
    </section>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
