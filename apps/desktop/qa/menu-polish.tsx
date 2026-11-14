import { useState } from "react";
import { createRoot } from "react-dom/client";
import { CustomInstructionsEditor } from "../workbench/src/settings/components/CustomInstructionsEditor";
import { ThemeCard } from "../workbench/src/settings/components/ThemeCard";
import { AgentSwitcher } from "../workbench/src/modules/ai/components/AgentSwitcher";
import { AgentCard } from "../workbench/src/settings/sections/AgentsSection";
import { useAgentsStore } from "../workbench/src/modules/ai/store/agentsStore";
import { agentPresentation } from "../workbench/src/modules/ai/lib/agentPresentation";
import { BUILTIN_AGENTS } from "../workbench/src/modules/ai/lib/agents";
import { setLocale } from "../src/localization";
import { useLocale } from "../src/i18n";
import "../workbench/src/styles/globals.css";

const qa = {
  update: (_value: string) => {}, locale: setLocale, fail: false,
  saves: [] as string[], actions: [] as string[], selections: [] as string[],
  importThemes: async () => typeof (await import("../workbench/src/settings/sections/ThemesSection")).ThemesSection === "function",
};
Object.assign(window, { __menuPolishQA: qa });
const custom = { ...BUILTIN_AGENTS.find(agent => agent.id === "builtin:coder")!, id: "fixture:custom", builtIn: false };
useAgentsStore.setState({ customAgents: [custom], setActiveId: id => {
  qa.selections.push(id); useAgentsStore.setState({ activeId: id });
} });

function Fixture() {
  useLocale();
  const [value, setValue] = useState("");
  qa.update = setValue;
  return <main className="mx-auto flex max-w-xl flex-col gap-6 p-4">
    <CustomInstructionsEditor value={value} save={async submitted => {
      qa.saves.push(submitted);
      if (qa.fail) throw new Error("Synthetic save failure");
      setValue(submitted);
    }} />
    <section aria-label="themes">
      <ThemeCard theme={{ id: "fixture:theme", name: "Fixture theme", description: "Clean neutral greys with a full-color terminal.", variants: { dark: { colors: { background: "#111111" } } } }}
        mode="dark" selected={true} custom={true} onSelect={() => qa.actions.push("select")}
        onEdit={() => qa.actions.push("edit")} onRemove={() => qa.actions.push("remove")} />
      <ThemeCard theme={{ id: "builtin:theme", name: "Builtin fixture", description: "Clean neutral greys with a full-color terminal.", variants: { dark: {} } }}
        mode="dark" selected={false} custom={false} onSelect={() => qa.actions.push("builtin-select")}
        onEdit={() => { throw new Error("Builtin edit exposed"); }} onRemove={() => { throw new Error("Builtin remove exposed"); }} />
    </section>
    <section aria-label="agent switcher"><AgentSwitcher /></section>
    <section aria-label="custom agent card"><AgentCard agent={custom} active={true}
      onActivate={() => qa.actions.push("agent-select")} onEdit={() => qa.actions.push("agent-edit")}
      onDelete={() => qa.actions.push("agent-remove")} /></section>
    <section aria-label="builtin descriptors">{BUILTIN_AGENTS.map(agent => {
      const presentation = agentPresentation(agent);
      return <p key={agent.id} data-agent={agent.id}>{presentation.name}: {presentation.description}</p>;
    })}</section>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
