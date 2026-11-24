import type { Settings, Stage } from "./types";

export const agentNames = { claude: "Claude Code", agy: "AGY" } as const;
export type AgentId = keyof typeof agentNames;

export function selectedAgent(settings: Settings, step: Stage): AgentId {
  const override = settings[`step${step}`]?.agent_cli;
  if (override === "agy" || override === "claude") return override;
  return settings.agents?.default === "agy" ? "agy" : "claude";
}
