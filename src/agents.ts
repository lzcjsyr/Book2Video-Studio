import type { Settings, Stage } from "./types";

export const agentNames = { claude: "Claude Code", agy: "AGY", codebuddy: "CodeBuddy Code" } as const;
export type AgentId = keyof typeof agentNames;

function isAgentId(value: unknown): value is AgentId {
  return typeof value === "string" && Object.hasOwn(agentNames, value);
}

export function selectedAgent(settings: Settings, step: Stage): AgentId {
  const override = settings[`step${step}`]?.agent_cli;
  if (isAgentId(override)) return override;
  const defaultAgent = settings.agents?.default;
  return isAgentId(defaultAgent) ? defaultAgent : "claude";
}
