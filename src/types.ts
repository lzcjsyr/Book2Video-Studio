export type Stage = "1" | "2" | "3" | "4" | "5" | "6";
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
  /** Every still image and motion clip the segment has had, oldest first. */
  takes: Take[];
  /** The take the segment uses (the only one in the sequence and the assembly), or none yet. */
  take: string | null;
  /** A picture that stands for the selected take in the sequence (the still, or a clip's keyframe). */
  poster: string | null;
  /** The selected motion clip, when the segment uses one. */
  motion: Motion | null;
}
/** One still image or motion clip of a segment (or of the opening). */
export interface Take {
  id: string;
  /** A background is the opening's visual; Assembly draws the opening quote over it. */
  kind: "still" | "motion" | "background";
  /** When it was made (seconds since the epoch). */
  created: number;
  /** The image, or a clip's rendered video (null until rendered). */
  path: string | null;
  poster: string | null;
  motion?: Motion;
}
/** Where a segment's motion stands: Claude Code's composition, its keyframes, the rendered video. */
export interface Motion {
  progress?: { phase: string; completed: number; total: number } | null;
  composition: string | null;
  /** When the composition was last saved (seconds since the epoch). */
  composed: number | null;
  folder: string;
  keyframes: string[];
  keyframes_current: boolean;
  video: string | null;
  video_current: boolean;
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
  /** Set by finish-agent: the session that just ended. */
  finished?: { step: Stage; indices: number[] } | null;
  path: string;
  raw: TextData;
  script: TextData;
  summary: TextData;
  hashes: Record<string, string>;
  segments: Segment[];
  ready: Record<Stage, boolean>;
  opening: {
    visual: string | null;
    takes: Take[];
    take: string | null;
    poster: string | null;
    audio: string | null;
    duration: number | null;
    /** The opening clip Assembly rendered, while it still matches the quote and background. */
    clip: string | null;
  };
  covers: string[];
  /** The last Claude Code edit: its folder and style, whether its materials are current and whether it delivered a video. */
  assembly_edit?: { folder: string; skill: string; current: boolean; delivered: boolean } | null;
  cover_design?: { folder: string; entries: { html: string; width: number; height: number }[]; files: string[]; previews: string[]; complete: boolean; current: boolean } | null;
  exports: string[];
  state: {
    selected_cover?: string;
    source: string;
    settings: Settings;
    stale: Record<string, { reason: string; segments: number[] }>;
    reviewed: Record<string, string>;
    summary_stale?: boolean;
    /** The 3:4 copy's panel settings changed since it was made; the landscape video is unaffected. */
    three_by_four_stale?: boolean;
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
  /** "queued": new images waiting for the running job; they start together when it ends. */
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "cancelling";
  log: string[];
  result?: { message?: string; success?: boolean };
  /** Seconds since the epoch, from the project service. */
  started: number;
  /** The segments it works on (null: the whole step), its Visuals mode, and whether it includes the opening. */
  scope?: number[] | null;
  mode?: string | null;
  opening?: boolean;
}
export interface Bootstrap {
  default_location: string;
  library_folder: string;
  defaults: Settings;
  voices?: SavedVoice[];
  skills: string[];
  library: LibraryCatalog;
  /** Background music: the bundled tracks, then the ones the user added (also listed in user_music). */
  music: string[];
  user_music?: string[];
  components: { name: string; path: string | null; bundled: boolean }[];
}
export interface SavedVoice { id: string; name: string; }
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
