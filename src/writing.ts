import type { Bootstrap, Detail } from "./types";

export interface WritingOptions {
  mode: "source" | "direct";
  source: string;
  agent_skill: string;
  llm_server: string;
  llm_model: string;
}

export function writingOptions(detail: Detail, bootstrap: Bootstrap | null): WritingOptions {
  const settings = { ...bootstrap?.defaults.step1, ...detail.state.settings.step1 };
  return {
    mode: settings.writing_mode === "direct" ? "direct" : settings.writing_mode === "source" || detail.state.source ? "source" : "direct",
    source: detail.state.source || "",
    agent_skill: String(settings.agent_skill ?? "sample_writer"),
    llm_server: String(settings.llm_server ?? ""),
    llm_model: String(settings.llm_model ?? ""),
  };
}

export function sameWriting(a: WritingOptions, b: WritingOptions) {
  return (Object.keys(a) as (keyof WritingOptions)[]).every((key) => a[key] === b[key]);
}
