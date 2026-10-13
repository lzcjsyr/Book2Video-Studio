import type { Settings } from "./types";

// Which saved API key each provider uses, and which keys a project's settings need.
export type KeyGroup = "text" | "image" | "voice";
export interface KeyProvider {
  key: string;
  name: string;
  group: KeyGroup;
  // Volcengine serves both Doubao language models and Seedream images.
  alsoText?: boolean;
}
export const keyProviders: KeyProvider[] = [
  { key: "DEEPSEEK_API_KEY", name: "DeepSeek", group: "text" },
  { key: "KIMI_API_KEY", name: "Kimi / Moonshot", group: "text" },
  { key: "MIMO_API_KEY", name: "Xiaomi MiMo", group: "text" },
  { key: "OPENROUTER_API_KEY", name: "OpenRouter", group: "text" },
  { key: "SILICONFLOW_KEY", name: "SiliconFlow", group: "text" },
  { key: "VOLCENGINE_API_KEY", name: "Volcengine / Doubao", group: "image", alsoText: true },
  { key: "GOOGLE_CLOUD_API_KEY", name: "Google", group: "image" },
  { key: "BYTEDANCE_TTS_API_KEY", name: "ByteDance TTS", group: "voice" },
];

// Engine provider ids (settings values) → saved key. Google ADC signs in with the
// local Google Cloud login instead of a key.
const languageModels: Record<string, string> = {
  deepseek: "DEEPSEEK_API_KEY",
  kimi: "KIMI_API_KEY",
  mimo: "MIMO_API_KEY",
  openrouter: "OPENROUTER_API_KEY",
  siliconflow: "SILICONFLOW_KEY",
  volcengine: "VOLCENGINE_API_KEY",
};
const imageServices: Record<string, string | null> = {
  doubao: "VOLCENGINE_API_KEY",
  google: "GOOGLE_CLOUD_API_KEY",
  google_adc: null,
};
export const languageModelChoices = Object.keys(languageModels).map((id) => ({
  id,
  label: keyProviders.find((p) => p.key === languageModels[id])!.name,
}));
// Each image service only accepts its own models; `model` is the one to switch to.
const doubao = { model: "doubao-seedream-4-0-250828", fits: /doubao|seedream/i };
const google = { model: "gemini-3.1-flash-image", fits: /gemini|imagen/i };
export const imageServiceChoices = [
  { id: "doubao", label: "Volcengine Seedream (Doubao)", ...doubao },
  { id: "google", label: "Google (API key)", ...google },
  { id: "google_adc", label: "Google Cloud login (ADC)", ...google },
];
/** Whether an image model belongs to the service (unknown services are not judged). */
export const imageModelFits = (server: string, model: string) =>
  imageServiceChoices.find((c) => c.id === server)?.fits.test(model) ?? true;
/** The model to keep after choosing a service: the current one if it fits, otherwise the service's default. */
export const imageModelFor = (server: string, model: string) =>
  imageModelFits(server, model) ? model : imageServiceChoices.find((c) => c.id === server)!.model;
export const providerName = (key: string) => keyProviders.find((p) => p.key === key)?.name || key;

export type NeedId = "writing" | "segments" | "voice" | "direction" | "motion_agent" | "images" | "covers";
export interface KeyNeed {
  id: NeedId;
  use: string;
  provider: string;
  // null: no key needed (Google ADC, a Claude subscription). undefined: the setting names an unsupported provider.
  key: string | null | undefined;
  // What signs in instead of a key.
  login?: string;
}
const googleLogin = "Google Cloud login (no key)";
const claudeLogin = "Your Claude Code login (no key)";

/** Every key the whole production needs with these (effective) project settings.
 * Static images are written from a template and need no language model; only motion segments use the step 4 agent.
 * With `subscription`, the writing and motion agents run in the user's installed Claude Code on their own login. */
export function keyNeeds(settings: Settings, { motion = false, subscription = false } = {}): KeyNeed[] {
  const value = (section: string, key: string) => String(settings[section]?.[key] ?? "").trim().toLowerCase();
  const model = (id: NeedId, use: string, section: string): KeyNeed => {
    const provider = value(section, "llm_server");
    return { id, use, provider, key: languageModels[provider] };
  };
  const agent = (id: NeedId, use: string, section: string): KeyNeed =>
    subscription ? { id, use, provider: "claude_code", key: null, login: claudeLogin } : model(id, use, section);
  const image = (id: NeedId, use: string, provider: string): KeyNeed =>
    ({ id, use, provider, key: imageServices[provider], ...(imageServices[provider] === null && { login: googleLogin }) });
  const imageServer = value("step4", "image_server");
  return [
    agent("writing", "Writing agent", "step1"),
    // Segmentation only calls a model when its split mode is "agent".
    ...(value("step2", "split_mode") === "agent" ? [model("segments", "Agent segmentation", "step2")] : []),
    { id: "voice", use: "Voice", provider: "bytedance", key: "BYTEDANCE_TTS_API_KEY" },
    model("direction", "Content summary", "step4_summary"),
    ...(motion ? [agent("motion_agent", "Motion agent (Claude Code)", "step4")] : []),
    image("images", "Segment and opening images", imageServer),
    value("step6", "cover_method") === "agent"
      ? agent("covers", "Cover agent (Claude Code)", "step1")
      : image("covers", "Covers", value("step6", "cover_image_server") || imageServer),
  ];
}

/** How many different keys (or provider choices) must be fixed; several steps can share one. */
export const distinctGaps = (gaps: KeyNeed[]) => new Set(gaps.map((n) => n.key ?? `provider:${n.id}`)).size;

/** Needs that cannot run: a missing saved key, or an unsupported provider. */
export function unmet(needs: KeyNeed[], saved: Record<string, boolean> | undefined, only?: NeedId[]) {
  return needs.filter((n) => (!only || only.includes(n.id)) && (n.key === undefined || (n.key !== null && saved && !saved[n.key])));
}

/** The image models this project would send through the Google Cloud login (ADC). */
export function adcModels(settings: Settings): string[] {
  const value = (section: string, key: string) => String(settings[section]?.[key] ?? "").trim();
  const imageServer = value("step4", "image_server");
  const models = [
    imageServer === "google_adc" ? value("step4", "image_model") : "",
    value("step6", "cover_method") !== "agent" && (value("step6", "cover_image_server") || imageServer) === "google_adc" ? value("step6", "cover_image_model") : "",
  ];
  return [...new Set(models.filter(Boolean))];
}
