import type { Agent, EffortLevel } from "@valuz/core";
import type { AgentModelSelection } from "./AgentModelPicker";

export interface AgentCopyDefaults {
  name: string;
  tagline: string;
  avatar: string | null;
  model: AgentModelSelection;
  effort: EffortLevel;
  instructions: string;
  skills: string[];
  connectors: string[];
  knowledgeScope: string[];
  inheritValurionInstructions: boolean;
}

const DEFAULT_EFFORT: EffortLevel = "high";

/**
 * Valurion copy is equivalent to a new Agent that inherits Valurion and only
 * carries over the portable brain settings. Other agents are deep-copied.
 */
export function getAgentCopyDefaults(seed: Agent): AgentCopyDefaults {
  const isValurion = seed.kind === "system";
  return {
    name: `${seed.name} (copy)`,
    tagline: isValurion ? "" : seed.description,
    avatar: isValurion ? null : seed.avatar,
    model: {
      runtime: seed.runtime,
      providerId: isValurion ? null : seed.provider_id,
      model: seed.model,
    },
    effort: seed.effort ?? DEFAULT_EFFORT,
    instructions: isValurion ? "" : seed.instructions,
    skills: isValurion ? [] : [...seed.skills],
    connectors: isValurion ? [] : [...seed.connector_types],
    knowledgeScope: isValurion ? [] : [...seed.knowledge_scope],
    inheritValurionInstructions: isValurion
      ? true
      : seed.inherit_global_instructions,
  };
}
