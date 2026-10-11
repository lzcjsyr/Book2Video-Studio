export type Stage = "1" | "1.5" | "3" | "4" | "5" | "6";
export type Settings = Record<string, Record<string, unknown>>;
export interface Project {
  id: string;
  name: string;
  source_name: string;
  segments: number;
  modified: number;
  path: string;
  linked: boolean;
  problem?: string;
}
export interface Segment {
  index: number;
  content: string;
  length: number;
  estimated_duration: number;
  visualizer: "image" | "hf";
  audio: string | null;
  visual: string | null;
  duration: number | null;
  composition: string | null;
}
export interface TextData {
  content?: string;
  source_name?: string;
  video_titles?: string[];
  cover_titles?: string[];
  cover_subtitles?: string[];
  golden_quotes?: string[];
  summary?: string;
  segments?: Segment[];
  [key: string]: unknown;
}
export type MetadataField =
  | "video_titles"
  | "cover_titles"
  | "cover_subtitles"
  | "golden_quotes";
export type MetadataSelections = Record<MetadataField, number | null>;
export interface Detail {
  id: string;
  revision: string;
  warning?: string;
  path: string;
  raw: TextData;
  script: TextData;
  summary: TextData;
  hashes: Record<string, string>;
  segments: Segment[];
  ready: Record<Stage, boolean>;
  opening: {
    visual: string | null;
    audio: string | null;
    duration: number | null;
  };
  covers: string[];
  exports: string[];
  state: {
    selected_cover?: string;
    source: string;
    settings: Settings;
    stale: Record<string, { reason: string; segments: number[] }>;
    reviewed: Record<string, string>;
    summary_stale?: boolean;
    agent_active?: { step: Stage; indices: number[]; launch: string };
    events: Revision[];
  };
}
export interface Revision {
  id: string;
  label: string;
  time: string;
  files: string[];
}
export interface Job {
  id: string;
  project: string;
  step: Stage;
  status: "running" | "succeeded" | "failed" | "cancelled" | "cancelling";
  log: string[];
  result?: { message?: string; success?: boolean };
  /** Seconds since the epoch, from the project service. */
  started: number;
}
export interface Bootstrap {
  default_location: string;
  library_folder: string;
  defaults: Settings;
  skills: string[];
  library: LibraryCatalog;
  music: string[];
  components: { name: string; path: string | null; bundled: boolean }[];
}
export interface LibraryChoice {
  id: string;
  label: string;
  description?: string;
}
export interface LibraryGroup {
  id: string;
  label: string;
  kind: string;
  hash: string;
  path: string;
  items: LibraryChoice[];
  error?: string | null;
}
export interface LibraryCatalog {
  groups: LibraryGroup[];
}
export interface LibraryItem {
  group: string;
  id: string;
  kind: string;
  label: string;
  value: string | Record<string, unknown>;
  hash: string;
  package_hash?: string;
  path: string;
  folder: string;
  files: string[];
  file?: string;
  variables: string[];
  removable: boolean;
}
export interface LibraryRevision {
  id: string;
  group: string;
  item: string;
  time: string;
  kind: string;
  can_restore: boolean;
}
export interface Launch {
  id: string;
  project: string;
  step: Stage;
  cwd: string;
  context: string;
  scope: string;
  args: string[];
}

/** How Claude Code runs: the bundled copy with a provider API key, or the user's installed copy on their Claude login. */
export type ClaudeCodeMode = "api_key" | "subscription";
export interface ClaudeCodeStatus {
  mode: ClaudeCodeMode;
  local: null | {
    path: string | null;
    version: string | null;
    logged_in: boolean;
    auth_method: string | null;
    subscription: string | null;
    email: string | null;
    error: string | null;
  };
}
