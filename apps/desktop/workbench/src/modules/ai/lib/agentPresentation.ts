import { t } from "../../../../../src/localization";
import type { Agent } from "./agents";

/** Translate app-owned personas only; user text stays literal. */
export function agentPresentation(agent: Agent) {
  return {
    name: agent.builtIn ? t(agent.name) : agent.name,
    description: agent.builtIn ? t(agent.description) : agent.description,
  };
}
