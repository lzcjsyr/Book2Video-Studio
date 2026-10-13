import { useI18n } from "./i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileText,
  Film,
  FolderOpen,
  FolderSearch,
  History,
  Image,
  Layers,
  ListVideo,
  Loader2,
  Maximize2,
  Music2,
  PanelRightOpen,
  Play,
  Plus,
  RefreshCw,
  Save,
  KeyRound,
  Settings2,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import {
  api,
  connect,
  media,
  musicUrl,
  projectRoute,
} from "./api";
import type {
  Bootstrap,
  ClaudeCodeMode,
  ClaudeCodeStatus,
  Detail,
  Job,
  Launch,
  Project,
  Settings,
  Stage,
  Take,
  TextData,
} from "./types";
import AgentPane, { type AgentHandle } from "./components/AgentPane";
import TakePicker, { type PickerAnchor } from "./components/TakePicker";
import ClipPreview from "./components/ClipPreview";
import NumberedText, { textCounts } from "./components/NumberedText";
import DeleteControl from "./components/DeleteControl";
import { clipState, clipStateLabel, numbers } from "./takes";
import Timeline, { formatTime } from "./components/Timeline";
import SettingsPanel from "./components/SettingsPanel";
import MetadataCandidates, {
  primarySelections,
} from "./components/MetadataCandidates";
import LibraryPanel from "./components/LibraryPanel";
import BrandMark from "./components/BrandMark";
import WritingSetup from "./components/WritingSetup";
import VisualDirection from "./components/VisualDirection";
import CoverSetup from "./components/CoverSetup";
import AssemblySetup from "./components/AssemblySetup";
import SegmentEditor from "./components/SegmentEditor";
import { writingOptions, sameWriting, type WritingOptions } from "./writing";
import { useCollapsed } from "./collapse";
import { librarySettings } from "./library";
import { distinctGaps, keyNeeds, providerName, unmet, type NeedId } from "./keys";
import type { KeyStatus } from "./components/ApiKeysPanel";

// Stage ids are the step numbers people see; the service translates them for the CLI engine.
const stages: {
  id: Stage;
  number: string;
  name: string;
  description: string;
  icon: typeof FileText;
}[] = [
  {
    id: "1",
    number: "1",
    name: "Writing",
    description: "Manuscript & story",
    icon: FileText,
  },
  {
    id: "2",
    number: "2",
    name: "Segments",
    description: "Structure & narration",
    icon: ListVideo,
  },
  {
    id: "3",
    number: "3",
    name: "Voice",
    description: "Recordings & delivery",
    icon: Music2,
  },
  { id: "4", number: "4", name: "Visuals", description: "Images & motion", icon: Image },
  { id: "5", number: "5", name: "Assembly", description: "Video & sound mix", icon: Film },
  { id: "6", number: "6", name: "Covers", description: "The final impression", icon: Image },
];
const order = stages.map((s) => s.id);
// Recordings, visuals and assembly leave the text as it is, so covers are made beside them.
const besideCovers: Stage[] = ["3", "4", "5"];
const stageNumber = (id: Stage) => stages.find((s) => s.id === id)?.number || id;
const clone = <T,>(data: T): T => structuredClone(data);
// The cover in use: the saved choice, else the first candidate.
const chosenCover = (d: Detail) =>
  d.state.selected_cover && d.covers.includes(d.state.selected_cover) ? d.state.selected_cover : d.covers[0];
const pathParts = (path?: string) => (path || "").split(/[\\/]/).filter(Boolean);
const folderName = (path?: string) => pathParts(path).at(-1) || path || "";
// The folders above the last one, for a compact "…/a / b" hint under a name.
const folderTail = (path: string | undefined, above: number) =>
  pathParts(path).slice(-(above + 1), -1).join(" / ");
const parentFolder = (path: string) => path.replace(/[\\/][^\\/]*[\\/]?$/, "") || path;
// Where a new project folder will be created; mirrors the service's name cleanup.
const projectDestination = (location: string, name: string) => {
  const base = location.trim().replace(/[\\/]+$/, "");
  const separator = base.includes("\\") && !base.includes("/") ? "\\" : "/";
  const title = name.trim().replace(/[\\/:*?"<>|]/g, "_").replace(/^\.+|\.+$/g, "").trim();
  return base + separator + (title || "Untitled");
};

export default function App() {
  const { t, locale } = useI18n();
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null),
    [projects, setProjects] = useState<Project[]>([]),
    [detail, setDetail] = useState<Detail | null>(null),
    [step, setStep] = useState<Stage>("4"),
    [selected, setSelected] = useState(1);
  const [draft, setDraft] = useState<TextData>({}),
    [draftSelections, setDraftSelections] = useState(() => primarySelections({})),
    [draftKind, setDraftKind] = useState<"raw" | "script">("script"),
    [draftHash, setDraftHash] = useState(""),
    [dirty, setDirty] = useState(false),
    [directionDirty, setDirectionDirty] = useState(false);
  // Visuals can hold an unsaved direction beside the narration draft.
  const unsaved = dirty || directionDirty;
  const [search, setSearch] = useState(""),
    [jobs, setJobs] = useState<Job[]>([]),
    [launch, setLaunch] = useState<Launch | null>(null),
    [agentVisible, setAgentVisible] = useState(true),
    [agentWidth, setAgentWidth] = useState(352),
    [version, setVersion] = useState(0);
  const [modal, setModal] = useState<
      | "new"
      | "settings"
      | "history"
      | "generate"
      | "system"
      | "manage"
      | "library"
      | null
    >(null),
    // Projects waiting for the remove/delete confirmation in "Manage projects".
    [removeTargets, setRemoveTargets] = useState<string[] | null>(null),
    [removeFiles, setRemoveFiles] = useState(false),
    [toast, setToast] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [newName, setNewName] = useState(""),
    [newLocation, setNewLocation] = useState(""),
    [scope, setScope] = useState("selected"),
    [visualAction, setVisualAction] = useState("static"),
    [rebuild, setRebuild] = useState(false),
    [withOpening, setWithOpening] = useState(false),
    [coverIndex, setCoverIndex] = useState(0);
  // Assembly always makes the 16:9 video; the 3:4 copy is optional and has a layout preview of its panels.
  const [exportVariant, setExportVariant] = useState<"landscape" | "portrait">("landscape"),
    [portraitView, setPortraitView] = useState<"video" | "layout" | null>(null),
    [layoutPreview, setLayoutPreview] = useState<{ project: string; path: string; stamp: number; source?: string } | null>(null),
    [layoutBusy, setLayoutBusy] = useState(false);
  const [libraryTarget, setLibraryTarget] = useState({ group: "writing_skills", id: "" });
  const [writingDraft, setWritingDraft] = useState<{ project: string; options: WritingOptions } | null>(null);
  const projectRef = useRef(""),
    dirtyRef = useRef(false),
    jobsSeen = useRef(new Set<string>()),
    // Jobs that ended before this window opened (a reload, a reconnect) are not news.
    openedAt = useRef(Date.now() / 1000),
    queue = useRef<Stage[]>([]),
    video = useRef<HTMLVideoElement>(null),
    revisionRef = useRef(""),
    stepRef = useRef<Stage>("4");
  dirtyRef.current = dirty;
  projectRef.current = detail?.id || "";
  stepRef.current = step;
  const notice = useCallback((message: string) => setToast(message), []);
  const agentHandle = useRef<AgentHandle>(null);
  // Visuals: the list of a part's images and clips, and the motion clip open in its preview.
  const [picker, setPicker] = useState<PickerAnchor | null>(null);
  const [clipView, setClipView] = useState<{ index: number; take: string } | null>(null);
  useEffect(() => {
    setPicker(null);
    setClipView(null);
  }, [step, detail?.id]);
  // Saved API keys (names only) and the Settings tab to open, for key reminders.
  const [keyStatus, setKeyStatus] = useState<KeyStatus | null>(null);
  const [claudeMode, setClaudeMode] = useState<ClaudeCodeMode>("api_key");
  const [settingsTab, setSettingsTab] = useState<"production" | "keys" | undefined>();
  function openSettings(tab?: "production" | "keys") {
    setSettingsTab(tab);
    setModal("settings");
  }
  const runningJobs = jobs.filter(
    (j) =>
      j.project === detail?.id &&
      (j.status === "running" || j.status === "cancelling"),
  );
  // The job this page waits for. Covers are drawn from the text alone, so a recording,
  // visual or assembly job leaves the Covers page free (and the reverse, in the service).
  const activeJob = runningJobs.find((j) => step !== "6" ||
    (detail?.state.settings.step6?.cover_method ?? bootstrap?.defaults.step6?.cover_method) === "agent" || !besideCovers.includes(j.step));
  const activeAgent = Boolean(detail?.state.agent_active);
  const savedWriting = detail ? writingOptions(detail, bootstrap) : null;
  const writing = writingDraft && writingDraft.project === detail?.id ? writingDraft.options : savedWriting;
  const writingPending = Boolean(writing && savedWriting && !sameWriting(writing, savedWriting));
  function editWriting(options: WritingOptions) {
    if (detail) setWritingDraft({ project: detail.id, options });
  }
  async function saveWriting(options = writing) {
    if (!detail || !options || !savedWriting) return;
    if (sameWriting(options, savedWriting)) return;
    const updated = await api<Detail>(projectRoute(detail.id) + "/writing", { options, expected: savedWriting });
    if (projectRef.current === detail.id) {
      setDetail(updated);
      setWritingDraft(null);
    }
  }
  const kindFor = (s: Stage) => (s === "1" ? "raw" : "script");
  function initializeDraft(d: Detail, s: Stage) {
    const kind = kindFor(s);
    setDraftKind(kind);
    setDraft(
      clone(
        kind === "raw"
          ? {
              source_name: d.raw.source_name || d.id,
              video_titles: [],
              cover_titles: [],
              cover_subtitles: [],
              golden_quotes: [],
              ...d.raw,
            }
          : d[kind],
      ),
    );
    setDraftHash(d.hashes[kind]);
    setDraftSelections(primarySelections(d.raw));
    setDirty(false);
  }
  async function load(id: string, s?: Stage) {
    const d = await api<Detail>(projectRoute(id));
    setDetail(d);
    setCoverIndex(Math.max(0, d.covers.indexOf(chosenCover(d))));
    if (revisionRef.current !== d.revision) {
      revisionRef.current = d.revision;
      setVersion((v) => v + 1);
    }
    setSelected((n) =>
      Math.min(Math.max(n, 0), Math.max(1, d.segments.length)),
    );
    if (!dirtyRef.current) initializeDraft(d, s || stepRef.current);
    return d;
  }
  async function initialize() {
    try {
      await connect();
      const [b, p] = await Promise.all([
        api<Bootstrap>("/bootstrap"),
        api<Project[]>("/projects"),
      ]);
      setBootstrap(b);
      setProjects(p);
      setError("");
      if (isTauri()) {
        invoke<KeyStatus>("credential_status").then(setKeyStatus).catch(() => setKeyStatus(null));
        invoke<ClaudeCodeStatus>("claude_code_status", { check: false }).then((s) => setClaudeMode(s.mode)).catch(() => {});
      }
      const remembered = localStorage.getItem("studio-project");
      const choice =
        p.find((x) => x.id === remembered) ||
        p.find((x) => x.segments > 0) ||
        p[0];
      if (choice) await load(choice.id);
    } catch (e) {
      setError(String(e));
    }
  }
  useEffect(() => {
    initialize();
  }, []);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 7000);
    return () => clearTimeout(t);
  }, [toast]);
  useEffect(() => {
    if (!bootstrap) return;
    const timer = setInterval(async () => {
      try {
        const list = await api<Job[]>("/jobs");
        setJobs(list);
        for (const j of list) {
          if (
            !["running", "cancelling"].includes(j.status) &&
            !jobsSeen.current.has(j.id)
          ) {
            jobsSeen.current.add(j.id);
            if (j.project === projectRef.current && j.started >= openedAt.current) {
              const d = await load(j.project);
              notice(j.result?.message || `Step ${stageNumber(j.step)} ${j.status}`);
              // Covers, the last step, can run beside a rebuild without moving it along.
              if (j.step === "6") continue;
              if (
                j.status === "succeeded" &&
                queue.current.length &&
                d.ready[j.step] &&
                !d.state.stale[j.step]
              ) {
                const next = queue.current.shift()!;
                setStep(next);
                initializeDraft(d, next);
                notice(
                  `Next rebuild: ${stages.find((s) => s.id === next)?.name}. Review the outputs, then generate to continue.`,
                );
              } else if (j.status !== "succeeded") queue.current = [];
            }
          }
        }
        if (
          projectRef.current &&
          !dirtyRef.current &&
          !list.some(
            (j) => j.project === projectRef.current && j.status === "running",
          )
        )
          await load(projectRef.current);
      } catch {
        /* Preserve the loaded project during a transient connection loss. */
      }
    }, 4000);
    return () => clearInterval(timer);
  }, [bootstrap]);
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      notice(String(e));
    } finally {
      setBusy(false);
    }
  }
  function changeStage(s: Stage) {
    if (unsaved || writingPending) {
      if (writingPending) { notice("Save your writing setup before switching stages."); return; }
      notice("Save your text changes before switching stages.");
      return;
    }
    setStep(s);
    if (!["2", "3", "4", "5"].includes(s) && selected === 0) setSelected(1);
    setSearch("");
    if (detail) initializeDraft(detail, s);
  }
  function changeSegment(n: number) {
    // Segments edits every row in place; other stages edit only the selected one.
    if (dirty && step !== "2") {
      notice("Save your narration edit before selecting another segment.");
      return;
    }
    setSelected(n);
    if (step === "5" && video.current && detail) {
      const speed = Number(
        detail.state.settings.step5?.narration_speed_factor ??
          bootstrap?.defaults.step5?.narration_speed_factor ??
          1,
      );
      video.current.currentTime =
        ((openingEnabled ? detail.opening.duration || 0 : 0) +
          detail.segments
            .filter((s) => s.index < n)
            .reduce(
              (sum, s) => sum + (s.duration ?? s.estimated_duration),
              0,
            )) /
        speed;
    }
  }
  function edit(value: TextData) {
    setDraft(value);
    setDirty(true);
  }
  // Saving only marks the segments whose narration or visual type changed.
  function changedSegments() {
    const before = new Map(detail?.script.segments?.map((s) => [s.index, s]));
    return (draft.segments || [])
      .filter((s) => {
        const b = before.get(s.index);
        return !b || b.content !== s.content || (b.visualizer || "image") !== (s.visualizer || "image");
      })
      .map((s) => s.index);
  }
  async function save() {
    if (!detail) return;
    await action(async () => {
      const updated = await api<Detail>(projectRoute(detail.id) + "/save", {
        kind: draftKind,
        value: draft,
        expected_hash: draftHash,
        selections: draftKind === "raw" ? draftSelections : undefined,
        segments: draftKind === "script" ? changedSegments() : undefined,
      });
      setDetail(updated);
      initializeDraft(updated, step);
      notice("Saved. Affected outputs are marked for generation.");
    });
  }
  const segment = detail?.segments.find((s) => s.index === selected),
    scriptSegment = (
      draftKind === "script" ? draft.segments : detail?.script.segments
    )?.find((s) => s.index === selected);
  // What the narration box shows: the opening quote, or the segment's (possibly unsaved) text.
  const narration = String((selected === 0 ? detail?.raw.golden_quotes?.[0] : scriptSegment?.content) || "");
  // Keys the whole production needs with this project's providers (the Writing
  // setup draft decides the writing agent), and the ones not saved yet.
  const keySettings: Settings = { ...bootstrap?.defaults };
  Object.entries(detail?.state.settings || {}).forEach(([k, v]) => (keySettings[k] = { ...keySettings[k], ...v }));
  if (writing) keySettings.step1 = { ...keySettings.step1, llm_server: writing.llm_server };
  const coverAgent = keySettings.step6?.cover_method === "agent";
  // The pane stays mounted elsewhere so a running session survives navigation.
  const agentStep = step === "1" || step === "4" || (step === "6" && coverAgent);
  const agentShown = agentStep && agentVisible;
  const hasMotion = Boolean(detail?.segments.some((s) => s.visualizer === "hf"));
  const missingKeys = detail ? unmet(keyNeeds(keySettings, { motion: true, subscription: claudeMode === "subscription" }), keyStatus?.saved) : [];
  // The header and the API keys tab count the motion agent only when a segment uses motion.
  const projectGaps = missingKeys.filter((n) => hasMotion || n.id !== "motion_agent");
  /** Remind the user (and open the API keys tab) when a step's key is not saved. */
  function keysReady(ids: NeedId[]) {
    const gap = missingKeys.find((n) => ids.includes(n.id));
    if (!gap) return true;
    notice(gap.key
      ? t("{use} needs a {provider} API key. Save it in Settings → API keys.", { use: t(gap.use), provider: t(providerName(gap.key)) })
      : t("{use} uses an unsupported provider. Choose another one in Settings.", { use: t(gap.use) }));
    openSettings(gap.key ? "keys" : "production");
    return false;
  }
  // Static images also write their shared direction first when it is missing.
  const visualNeeds = (): NeedId[] =>
    detail && !String(detail.summary.summary || "").trim()
      ? ["direction", "images"] : ["images"];
  const openingEnabled = Boolean(
    detail?.state.settings.global?.opening_quote ??
      bootstrap?.defaults.global?.opening_quote ?? true,
  );
  const [openingCollapsed, toggleOpeningPanel] = useCollapsed("opening-quote");
  const openingQuote = String(
    draft.golden_quotes?.[draftSelections.golden_quotes ?? 0] || "",
  ).trim();
  const openingSummary = !openingEnabled
    ? t("Opening quote is off")
    : openingQuote || t("No opening quote yet");
  async function toggleOpening(enabled: boolean) {
    if (!detail) return;
    await action(async () => {
      const updated = await api<Detail>(projectRoute(detail.id) + "/opening", { enabled, expected: openingEnabled });
      setDetail(updated);
      if (!enabled) { setWithOpening(false); if (selected === 0) setSelected(1); }
      notice(enabled ? "Opening quote enabled. Generate the video to apply this change." : "Opening quote disabled. Generate the video to apply this change.");
    });
  }
  const totalDuration =
    detail?.segments.reduce(
      (n, s) => n + (s.duration ?? s.estimated_duration),
      openingEnabled ? detail.opening.duration || 0 : 0,
    ) || 0;
  const reviewAudio = selected === 0 ? detail?.opening : segment;
  const current = stages.find((s) => s.id === step)!;
  const effectiveSettings = (section: string) => ({ ...(bootstrap?.defaults[section] || {}), ...(detail?.state.settings[section] || {}) });
  const step5Settings = effectiveSettings("step5"),
    panelSettings = effectiveSettings("three_by_four");
  const portraitEnabled = panelSettings.enabled !== false;
  const landscapeFile = detail?.exports.includes("final_video.mp4") ? "final_video.mp4" : null,
    portraitFile = detail?.exports.includes("final_video_3x4.mp4") ? "final_video_3x4.mp4" : null;
  const assemblyStale = Boolean(detail?.state.stale["5"]),
    portraitStale = Boolean(detail?.state.three_by_four_stale);
  // The 3:4 copy shows the layout of its panels until a copy matching them exists.
  const portraitShows = portraitView ?? (portraitFile && portraitEnabled && !portraitStale ? "video" : "layout");
  const showingLayout = step === "5" && exportVariant === "portrait" && portraitShows === "layout";
  const currentLayout = layoutPreview && layoutPreview.project === detail?.id ? layoutPreview : null;
  const variantStatus = (variant: "landscape" | "portrait"): [string, string] => {
    const file = variant === "landscape" ? landscapeFile : portraitFile;
    if (variant === "portrait" && !portraitEnabled) return ["Off", "off"];
    if (!file) return [variant === "landscape" ? "Not assembled" : "Not made yet", "empty"];
    if (assemblyStale) return ["Out of date", "stale"];
    if (variant === "portrait" && portraitStale) return ["Panels changed", "stale"];
    return ["Ready", "ready"];
  };
  const firstText = (...lists: unknown[]) => {
    for (const list of lists)
      for (const item of Array.isArray(list) ? list : [list]) {
        const text = String(item ?? "").trim();
        if (text) return text;
      }
    return "";
  };
  const coverCopy = {
    title: firstText(detail?.raw.cover_titles, detail?.script.cover_titles, detail?.raw.video_titles, detail?.script.video_titles),
    subtitle: firstText(detail?.raw.cover_subtitles, detail?.script.cover_subtitles),
    quote: firstText(detail?.script.golden_quotes, detail?.raw.golden_quotes),
  };
  // Redraw the layout preview when the panels, the export size or the project's outputs change.
  const layoutKey = JSON.stringify([panelSettings, step5Settings.video_size, coverCopy]);
  useEffect(() => {
    if (!detail || !showingLayout) return;
    const project = detail.id;
    let cancelled = false;
    const timer = setTimeout(() => {
      setLayoutBusy(true);
      api<{ path: string; source?: string }>(projectRoute(project) + "/panel-preview", {})
        .then((r) => { if (!cancelled) setLayoutPreview({ ...r, project, stamp: Date.now() }); })
        .catch((e) => { if (!cancelled) notice(String(e)); })
        .finally(() => setLayoutBusy(false));
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [detail?.id, showingLayout, layoutKey, version]);
  const statusKind = detail?.state.stale[step]
    ? "needs-rebuild"
    : detail?.state.reviewed[step]
      ? "reviewed"
      : detail?.ready[step]
        ? "ready"
        : "empty";
  const outdatedCount = detail?.state.stale[step]?.segments.length ?? 0;
  const status = statusKind === "needs-rebuild" && outdatedCount
    ? t(outdatedCount === 1 ? "1 segment needs rebuild" : "{count} segments need rebuild", { count: outdatedCount })
    : t(
        {
          "needs-rebuild": "Needs rebuild",
          reviewed: "Reviewed",
          ready: "Ready to review",
          empty: "Not generated",
        }[statusKind],
      );

  /** Activate Claude Code; Visuals can name the motion clip to revise, or "new" for a new clip. */
  async function activateFor(indices?: number[], take?: string, revise = false) {
    if (!detail || unsaved) {
      notice("Save changes before activating the agent.");
      return;
    }
    if (!keysReady([step === "1" ? "writing" : step === "6" ? "covers" : "motion_agent"])) return;
    if (step === "4" && indices?.includes(0)) {
      notice(
        "Generate the opening through Visuals; the motion agent works on narration segments.",
      );
      return;
    }
    await action(async () => {
      if (!isTauri())
        throw new Error(
          t("Open the Tauri desktop app to activate Claude Code."),
        );
      if (step === "1") await saveWriting();
      const prepared = await api<Launch>(projectRoute(detail.id) + "/agent", {
        step,
        indices,
        take,
        revise,
      });
      setLaunch(prepared);
      setAgentVisible(true);
      await load(detail.id);
    });
  }
  async function activate() {
    await activateFor(step === "4" ? [selected] : undefined);
  }
  const finished = useCallback(() => {
    const id = projectRef.current;
    if (id)
      api<Detail>(projectRoute(id) + "/finish-agent", {})
        .then((d) => {
          setDetail(d);
          setVersion((v) => v + 1);
          setLaunch(null);
          if (!dirtyRef.current) initializeDraft(d, stepRef.current);
          notice(
            d.warning || "Agent session ended. Production outputs refreshed.",
          );
          // What a Visuals session composed is rendered right away, so its video shows in the preview.
          const composed = d.finished?.step === "4"
            ? d.segments.filter((s) => d.finished!.indices.includes(s.index) && s.motion?.composition && !s.motion.video_current).map((s) => s.index)
            : [];
          if (composed.length)
            api<Job>(projectRoute(d.id) + "/job", { step: "4", mode: "render", indices: composed })
              .then((job) => {
                setJobs((jobs) => [...jobs, job]);
                notice(composed.length === 1
                  ? `Rendering segment ${composed[0]} from Claude Code's composition…`
                  : `Rendering ${composed.length} segments from Claude Code's compositions…`);
              })
              .catch((e) => notice(String(e)));
          if (d.finished?.step === "6" && !d.warning && d.cover_design?.complete)
            api<Job>(projectRoute(d.id) + "/job", { step: "6", mode: "cover_render" })
              .then((job) => {
                setJobs((jobs) => [...jobs, job]);
                notice("Rendering covers from Claude Code's HTML designs…");
              })
              .catch((e) => notice(String(e)));
          if (
            !d.warning &&
            queue.current.length &&
            d.ready[stepRef.current] &&
            !d.state.stale[stepRef.current]
          ) {
            const next = queue.current.shift()!;
            setStep(next);
            initializeDraft(d, next);
            notice(
              `Next rebuild: ${stages.find((s) => s.id === next)?.name}. Review and generate when ready.`,
            );
          }
        })
        .catch((e) => notice(String(e)));
  }, []);
  async function generate() {
    if (!detail) return;
    await action(async () => {
      if (unsaved) throw new Error(t("Save changes before generating."));
      if (rebuild)
        queue.current = order
          .slice(order.indexOf(step) + 1)
          .filter((s) => !(s === "6" && ["3", "4", "5"].includes(step)));
      if (step === "1" || (step === "6" && coverAgent)) {
        setModal(null);
        await activate();
        return;
      }
      let indices =
        scope === "selected" && detail.segments.length
          ? [selected]
          : scope === "missing"
            ? detail.segments
                .filter((s) => (step === "3" ? !s.audio : !s.visual))
                .map((s) => s.index)
            : scope === "outdated"
              ? detail.state.stale[step]?.segments.filter((i) => i !== 0) ?? []
              : undefined;
      let mode = step === "4" ? visualAction : undefined;
      const opening = openingEnabled && (withOpening || (selected === 0 && scope === "selected") ||
        (scope === "outdated" && Boolean(detail.state.stale[step]?.segments.includes(0))));
      if (selected === 0 && scope === "selected") {
        indices = [];
        if (step === "4") mode = "opening";
      }
      if (step === "4" && mode === "agent") {
        setModal(null);
        await activateFor(indices);
        return;
      }
      const required: Partial<Record<Stage, NeedId[]>> = { "2": ["segments"], "3": ["voice"], "4": mode === "render" ? [] : visualNeeds(), "6": ["covers"] };
      if (!keysReady(required[step] || [])) return;
      if (step === "4" && mode === "render") {
        const selectedMotion = detail.segments.filter(
          (s) =>
            s.visualizer === "hf" && (!indices || indices.includes(s.index)),
        );
        if (!selectedMotion.length)
          throw new Error(t("Select motion segments to render."));
        indices = selectedMotion.map((s) => s.index);
        if (selectedMotion.some((s) => !s.composition))
          throw new Error(
            t(
              "Activate Claude Code to create the missing motion compositions first.",
            ),
          );
      }
      if (step === "4" && mode === "static" && indices) {
        indices = indices.filter(
          (n) =>
            detail.segments.find((s) => s.index === n)?.visualizer !== "hf",
        );
        if (!indices.length && !opening)
          throw new Error(
            t(
              "Choose static image segments, or change the action to motion rendering.",
            ),
          );
      }
      const job = await api<Job>(projectRoute(detail.id) + "/job", {
        step,
        indices,
        mode,
        opening,
      });
      setJobs((j) => [...j, job]);
      setModal(null);
      notice(
        `Generating ${current.name.toLowerCase()}. You can keep reviewing.`,
      );
    });
  }
  function openLibrary(group = step === "4" ? "image_styles" : step === "6" ? "cover_styles" : "writing_skills") {
    const setting = librarySettings[group];
    const id = group === "writing_skills" ? writing?.agent_skill || "" : setting ? String(detail?.state.settings[setting[0]]?.[setting[1]] ?? bootstrap?.defaults[setting[0]]?.[setting[1]] ?? "") : "summary";
    setLibraryTarget({ group, id });
    setModal("library");
  }
  async function refreshLibrary() {
    setBootstrap(await api<Bootstrap>("/bootstrap"));
  }
  /** Save some of a step's settings from its page; the service marks what they outdate. */
  async function saveSettings(section: string, patch: Record<string, unknown>) {
    if (!detail || activeJob || activeAgent) return false;
    let saved = false;
    await action(async () => {
      const updated = await api<Detail>(projectRoute(detail.id) + "/settings", { settings: { [section]: patch } });
      if (projectRef.current === detail.id) setDetail(updated);
      saved = true;
    });
    return saved;
  }
  const saveCoverSetting = (key: string, value: string | number | string[]) => saveSettings("step6", { [key]: value });
  /** Redraw only the 3:4 copy around the current 16:9 video (Generate makes both). */
  async function buildPortrait() {
    if (!detail) return;
    await action(async () => {
      if (unsaved) throw new Error(t("Save changes before generating."));
      const job = await api<Job>(projectRoute(detail.id) + "/job", { step: "5", mode: "three_by_four" });
      setJobs((jobs) => [...jobs, job]);
      setExportVariant("portrait");
      setPortraitView(null);
      notice("Building the 3:4 copy from the landscape video…");
    });
  }
  /** Copy a music file into Studio's music folder and use it for this video. */
  async function addMusic() {
    if (!detail) return;
    if (!isTauri()) {
      notice("Open the desktop app to add music.");
      return;
    }
    const picked = await open({
      multiple: false,
      directory: false,
      title: t("Add background music"),
      filters: [{ name: t("Audio"), extensions: ["mp3", "wav", "m4a", "aac", "flac"] }],
    }).catch((e) => {
      notice(String(e));
      return null;
    });
    if (typeof picked !== "string") return;
    let name = "";
    await action(async () => {
      const added = await api<{ name: string; music: string[]; user_music: string[] }>("/music/import", { path: picked });
      setBootstrap((b) => (b ? { ...b, music: added.music, user_music: added.user_music } : b));
      name = added.name;
    });
    if (name && (await saveSettings("step5", { bgm_filename: name }))) notice("Music added and chosen for this video.");
  }
  async function startVisualJob(body: { mode: string; batch?: string; indices?: number[]; opening?: boolean; take?: string }, message: string) {
    if (!detail) return;
    // Rendering (a saved composition, or the opening over a kept background) runs locally and needs no provider key.
    if (!["render", "opening_render"].includes(body.mode) && !keysReady(body.mode === "summary" ? ["direction"] : visualNeeds())) return;
    await action(async () => {
      if (unsaved) throw new Error(t("Save changes before generating."));
      const job = await api<Job>(projectRoute(detail.id) + "/job", { step: "4", ...body });
      setJobs((jobs) => [...jobs, job]);
      notice(message);
    });
  }
  /** Use one of a part's images or motion clips; every other version is kept. */
  async function selectTake(index: number, take: Take) {
    if (!detail) return;
    if (dirty) {
      notice("Save your narration edit before changing the visual.");
      return;
    }
    const current = index === 0 ? detail.opening.take : detail.segments.find((s) => s.index === index)?.take;
    if (current === take.id) return;
    await action(async () => {
      const updated = await api<Detail>(projectRoute(detail.id) + "/take", { index, take: take.id });
      setDetail(updated);
      initializeDraft(updated, step);
      if (take.kind === "motion" && !take.motion?.video)
        notice("This clip is not rendered yet, so the segment has no video until you render it.");
    });
  }
  /** Delete a version a part no longer needs; Revision history keeps a copy. */
  async function deleteTake(index: number, take: Take) {
    if (!detail) return;
    await action(async () => {
      setDetail(await api<Detail>(projectRoute(detail.id) + "/delete-take", { index, take: take.id }));
      notice("Version deleted. Revision history keeps a copy.");
    });
  }
  async function deleteCover(path: string) {
    if (!detail) return;
    await action(async () => {
      const updated = await api<Detail>(projectRoute(detail.id) + "/delete-cover", { path });
      setDetail(updated);
      setCoverIndex(Math.max(0, updated.covers.indexOf(chosenCover(updated))));
      notice("Cover deleted. Revision history keeps a copy.");
    });
  }
  /** Why a part's visual cannot change right now, or "" when it can. */
  function takeLock(index: number) {
    if (activeJob) return "Wait for the running job to finish.";
    if (agentSegments.includes(index) || (activeAgent && detail?.state.agent_active?.step !== "4"))
      return "Finish the Claude Code session first.";
    if (dirty) return "Save your narration edit first.";
    // Not locked while a choice is being saved: a double-click's second click must still land.
    return "";
  }
  /** Why a new image or clip cannot be made right now, or "" when it can. */
  function makeLock() {
    if (activeJob) return "Wait for the running job to finish.";
    if (activeAgent) return "Finish the Claude Code session first.";
    if (unsaved) return "Save your changes first.";
    return busy ? "Working…" : "";
  }
  const closePicker = useCallback(() => setPicker(null), []);
  function newImage(index: number) {
    setPicker(null);
    if (index === 0) startVisualJob({ mode: "opening", opening: true, indices: [] }, "Generating a new opening.");
    else startVisualJob({ mode: "static", indices: [index] }, `Generating a new image for segment ${index}.`);
  }
  function newClip(index: number) {
    setPicker(null);
    activateFor([index], "new");
  }
  /** Open a clip's preview. It does not switch the segment to it: the preview has "Use this clip". */
  function openClip(index: number, take: Take) {
    setPicker(null);
    setClipView({ index, take: take.id });
  }
  /** Show a project file selected in Finder or Explorer, or a folder in the project ("" is the project folder). */
  async function showFiles(path: string) {
    if (!detail) return;
    if (!isTauri()) {
      notice("Open the desktop app to show files in Finder or Explorer.");
      return;
    }
    await invoke("reveal_project_path", { project: detail.id, path }).catch((e) => notice(String(e)));
  }
  async function saveDirection(summary: string, expected: string) {
    if (!detail) return false;
    let saved = false;
    await action(async () => {
      setDetail(await api<Detail>(projectRoute(detail.id) + "/save", {
        kind: "summary", value: { ...detail.summary, summary }, expected_hash: expected }));
      saved = true;
      notice("Content summary saved. Visuals are marked for generation.");
    });
    return saved;
  }
  async function pickFolder(title: string, current: string) {
    const picked = await open({
      directory: true,
      multiple: false,
      title,
      defaultPath: current.trim() || undefined,
    });
    return typeof picked === "string" ? picked : null;
  }
  // A production folder from a backup, another computer, or the CLI.
  async function openExisting() {
    if (writingPending || unsaved || activeAgent) {
      notice("Save edits and finish the active agent before changing projects.");
      return;
    }
    const path = await pickFolder(t("Open project folder"), bootstrap?.default_location || "");
    if (!path) return;
    await action(async () => {
      const opened = await api<{ id: string }>("/projects/open", { path });
      setProjects(await api<Project[]>("/projects"));
      setLaunch(null);
      await load(opened.id);
      localStorage.setItem("studio-project", opened.id);
    });
  }
  function openManage() {
    setRemoveTargets(null);
    setRemoveFiles(false);
    setModal("manage");
  }
  // Takes projects off the list; with deleteFiles it also deletes their folders.
  async function removeProjects(ids: string[], deleteFiles: boolean) {
    await action(async () => {
      const removesCurrent = Boolean(detail && ids.includes(detail.id));
      if (removesCurrent && (writingPending || unsaved))
        throw new Error(t("Save your edits before removing this project."));
      if (removesCurrent && (activeAgent || activeJob))
        throw new Error(t("Finish the active job or agent before removing this project."));
      await api("/projects/remove", { ids, delete_files: deleteFiles });
      const list = await api<Project[]>("/projects");
      setProjects(list);
      setRemoveTargets(null);
      setRemoveFiles(false);
      if (removesCurrent) {
        // Leave the removed project: open another one, or show the empty start page.
        setLaunch(null);
        setWritingDraft(null);
        setDirty(false);
        dirtyRef.current = false;
        revisionRef.current = "";
        const next = list.find((p) => p.segments > 0) || list[0];
        if (next) {
          await load(next.id);
          localStorage.setItem("studio-project", next.id);
        } else {
          setDetail(null);
          localStorage.removeItem("studio-project");
        }
      }
      notice(
        deleteFiles
          ? t("Deleted {count} with their files.", { count: ids.length })
          : t("Removed {count} from the list.", { count: ids.length }),
      );
    });
  }
  function openNewProject() {
    setNewName("");
    // Start from the last location used, else the Book2Video Studio projects folder.
    setNewLocation(localStorage.getItem("studio-location") || bootstrap?.default_location || "");
    setModal("new");
  }
  async function newProject() {
    await action(async () => {
      if (writingPending) throw new Error(t("Save your writing setup before changing projects."));
      if (!newName.trim()) throw new Error(t("Enter a project name."));
      const created = await api<{ id: string }>("/projects",
        { source: "", name: newName, location: newLocation, settings: { step1: { writing_mode: "direct" } } });
      setProjects(await api<Project[]>("/projects"));
      setDirty(false);
      setWritingDraft(null);
      dirtyRef.current = false;
      setStep("1");
      setSelected(1);
      await load(created.id, "1");
      localStorage.setItem("studio-project", created.id);
      localStorage.setItem("studio-location", newLocation.trim());
      setModal(null);
      setLaunch(null);
      notice("Project created. Activate Claude Code to begin writing.");
    });
  }
  function resizeAgent(e: React.PointerEvent) {
    e.preventDefault();
    const initial = e.clientX,
      // Start from the width on screen, which the stylesheet may have capped.
      width = (e.currentTarget.nextElementSibling as HTMLElement | null)?.offsetWidth || agentWidth;
    // The terminal would otherwise take pointer events (and text selection) mid-drag.
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    const move = (m: PointerEvent) =>
      setAgentWidth(
        Math.max(285, Math.min(650, window.innerWidth * 0.46, width + initial - m.clientX)),
      );
    const up = () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }
  const editSegment = (key: string, value: unknown, index = selected) =>
    edit({
      ...draft,
      segments: draft.segments?.map((s) =>
        s.index === index ? { ...s, [key]: value } : s,
      ),
    });
  // A segment using a motion clip previews its video, else (labelled) one of its keyframes.
  const motion = step === "4" && selected !== 0 ? segment?.motion ?? undefined : undefined;
  const motionPreview = (() => {
    if (!motion) return null;
    const frames = motion.keyframes;
    if (motion.video)
      return { path: motion.video, note: motion.video_current ? "" : t("Rendered from an earlier composition") };
    if (frames.length)
      return { path: frames[Math.floor(frames.length / 2)], note: t("Keyframe preview · render the clip to see the motion") };
    return { path: null, note: "" };
  })();
  // The selected part's versions, and how the one it uses is named ("Image 2 of 3", "Clip 1 of 2").
  const takesOf = (index: number) => (index === 0 ? detail?.opening.takes : detail?.segments.find((s) => s.index === index)?.takes) || [];
  const takeOf = (index: number) => (index === 0 ? detail?.opening.take : detail?.segments.find((s) => s.index === index)?.take) ?? null;
  const partName = (index: number) => (index === 0 ? t("Opening") : t("Segment {index}", { index }));
  const selectedTakes = takesOf(selected);
  const selectedTake = selectedTakes.find((take) => take.id === takeOf(selected));
  const takeName = (takes: Take[], take: Take) =>
    take.kind === "still" ? t("Image {index}", { index: numbers(takes).get(take.id)! }) : t("Clip {index}", { index: numbers(takes).get(take.id)! });
  const visualName = selectedTake ? takeName(selectedTakes, selectedTake) : "";
  const visualLabel = !selectedTake
    ? segment?.visualizer === "hf" && selected !== 0 ? t("Motion · no clip yet") : t("No image yet")
    : selectedTake.kind === "still"
      ? t("Image {index} of {count}", { index: numbers(selectedTakes).get(selectedTake.id)!, count: selectedTakes.filter((x) => x.kind === "still").length })
      : `${t("Clip {index} of {count}", { index: numbers(selectedTakes).get(selectedTake.id)!, count: selectedTakes.filter((x) => x.kind === "motion").length })} · ${t(clipStateLabel[clipState(selectedTake.motion)])}`;
  const selectedMedia =
    step === "3"
      ? reviewAudio?.audio
      : step === "5"
        ? exportVariant === "landscape"
          ? landscapeFile
          : portraitShows === "video"
            ? portraitFile
            : currentLayout?.path
        : step === "6"
          ? detail?.covers[coverIndex]
          : selected === 0
            ? detail?.opening.visual
            : motionPreview
              ? motionPreview.path
              : segment?.visual;
  const previewMedia =
    selectedMedia && detail ? media(detail.id, selectedMedia, showingLayout && currentLayout ? currentLayout.stamp : version) : null;
  // The selected segment's place in Voice or Visuals work: why it is outdated, and what is running on it.
  const stepStale = ["3", "4"].includes(step) ? detail?.state.stale[step] : undefined;
  const selectedStale =
    stepStale && (!stepStale.segments.length || stepStale.segments.includes(selected)) ? stepStale.reason : null;
  const agentSegments = detail?.state.agent_active?.step === "4" ? detail.state.agent_active.indices || [] : [];
  const jobCovers = (mode: string) =>
    Boolean(
      activeJob && activeJob.step === "4" && activeJob.mode === mode &&
        (selected === 0 ? activeJob.opening || mode === "opening" : !activeJob.scope || activeJob.scope.includes(selected)),
    );
  // Voice review sits over a dimmed copy of the part's picture: its still, or a frame of its clip.
  const voiceBackdrop = (selected === 0 ? detail?.opening.poster : segment?.poster) || null;

  return (
    <div
      className="studio-shell"
      style={{ "--agent-width": `${agentWidth}px` } as React.CSSProperties}
    >
      <header className="app-header">
        <div className="brand">
          <BrandMark size={26} />
          <span>
            Book2Video<span className="brand-studio">Studio</span>
          </span>
        </div>
        <span className="header-divider" />
        <div className="project-title" title={detail?.path}>
          <span>{detail?.raw.source_name || projects.find((p) => p.id === detail?.id)?.source_name || t("Production workspace")}</span>
        </div>
        <div className="header-spacer" />
        {(unsaved || writingPending) ? (
          <span className="header-save-state unsaved">
            <span className="status-dot" />
            {t("Unsaved changes")}
          </span>
        ) : runningJobs.length ? (
          <span className="header-save-state running">
            <Loader2 size={12} className="spin" />
            {t("Production running")}
          </span>
        ) : null}
        {detail && projectGaps.length > 0 && (
          <button className="key-reminder" onClick={() => openSettings("keys")}
            title={projectGaps.map((n) => `${t(n.use)}: ${n.key ? t(providerName(n.key)) : t("unsupported provider")}`).join("\n")}>
            <KeyRound size={13} /> {distinctGaps(projectGaps) === 1 ? t("1 API key missing") : t("{count} API keys missing", { count: distinctGaps(projectGaps) })}
          </button>
        )}
        <div className="header-actions">
          <button
            className="icon-button"
            title={t("Revision history")}
            aria-label={t("Revision history")}
            disabled={!detail}
            onClick={() => setModal("history")}
          >
            <History size={17} />
          </button>
          <button
            className="icon-button"
            title={t("Settings")}
            aria-label={t("Settings")}
            onClick={() => openSettings()}
          >
            <Settings2 size={17} />
          </button>
          {agentStep && !agentVisible && (
            <button
              className="icon-button"
              title={t("Show Claude Code pane")}
              aria-label={t("Show Claude Code pane")}
              onClick={() => setAgentVisible(true)}
            >
              <PanelRightOpen size={17} />
            </button>
          )}
        </div>
      </header>
      <div className="workspace">
        <nav className="sidebar">
          <div className="sidebar-section-label project-label">
            {t("PROJECTS")}
            <button
              className="icon-button"
              aria-label={t("New project")}
              title={t("New project")}
              disabled={Boolean(activeJob || activeAgent) || !bootstrap}
              onClick={openNewProject}
            >
              <Plus size={15} />
            </button>
            <button
              className="icon-button"
              aria-label={t("Open project folder")}
              title={t("Open project folder")}
              disabled={Boolean(activeJob || activeAgent) || !bootstrap || !isTauri()}
              onClick={openExisting}
            >
              <FolderOpen size={15} />
            </button>
            <button
              className="icon-button"
              aria-label={t("Manage projects")}
              title={t("Manage projects")}
              disabled={!bootstrap || !projects.length}
              onClick={openManage}
            >
              <Trash2 size={15} />
            </button>
          </div>
          <select
            className="project-select"
            aria-label={t("Project")}
            value={detail?.id || ""}
            onChange={(e) => {
              if (writingPending) {
                notice("Save your writing setup before changing projects.");
                return;
              }
              if (unsaved || activeAgent) {
                notice(
                  "Save edits and finish the active agent before changing projects.",
                );
                return;
              }
              setLaunch(null);
              load(e.target.value).catch((e) => notice(String(e)));
              localStorage.setItem("studio-project", e.target.value);
            }}
          >
            <option value="" disabled>
              {t("Select project")}
            </option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {(p.linked
                  ? `${p.source_name} · ${folderName(parentFolder(p.path))}`
                  : p.source_name) + (p.problem ? ` · ${t(p.problem)}` : "")}
              </option>
            ))}
          </select>
          {detail && (
            <div className="project-path" title={detail.path}>
              <FolderOpen size={11} />
              <span>{folderTail(detail.path, 2)}</span>
            </div>
          )}
          <div className="sidebar-section-label production-label">
            {t("PRODUCTION")}
          </div>
          <div className="stage-list">
            {stages.map((s) => {
              const ready = detail?.ready[s.id],
                stale = detail?.state.stale[s.id];
              return (
                <button
                  key={s.id}
                  className={`stage-item${step === s.id ? " active" : ""}${stale ? " stale" : ""}${ready ? " done" : ""}`}
                  aria-current={step === s.id ? "step" : undefined}
                  title={stale ? t(stale.reason) : undefined}
                  onClick={() => changeStage(s.id)}
                >
                  <span className="stage-number">{s.number}</span>
                  <span className="stage-text">
                    <strong>{t(s.name)}</strong>
                    <small>{t(s.description)}</small>
                  </span>
                  {/* The check means the step has what the next one needs; outdated is shown beside it. */}
                  {stale && <span className="stale-dot" title={t(stale.reason)} />}
                  {ready ? (
                    <Check size={13} className="stage-check" />
                  ) : !stale && (
                    <span className="stage-empty-dot" />
                  )}
                </button>
              );
            })}
          </div>
          <div className="sidebar-bottom">
            <button disabled={!detail} title={detail?.path} onClick={() => showFiles("")}>
              <FolderOpen size={14} /> {t("Show project folder")}
            </button>
            <button disabled={!bootstrap} onClick={() => setModal("system")}>
              <ShieldCheck size={14} /> {t("System check")}
            </button>
          </div>
        </nav>
        <main className="production-main">
          {error ? (
            <div className="connection-error">
              <ShieldCheck size={32} />
              <h1>{t("The production service could not start")}</h1>
              <p>{t(error)}</p>
              <button className="button primary" onClick={() => action(initialize)}>
                <RefreshCw size={14} /> {t("Try again")}
              </button>
            </div>
          ) : !detail ? (
            <div className="connection-error">
              <BrandMark size={42} />
              <h1>{t("Start with a story.")}</h1>
              <p>
                {t(
                  "Open an existing production or create a project to start writing.",
                )}
              </p>
              <div className="editor-actions">
                <button className="button" disabled={!bootstrap || !isTauri()} onClick={openExisting}>
                  <FolderOpen size={15} /> {t("Open project folder")}
                </button>
                <button
                  className="button primary"
                  disabled={!bootstrap}
                  onClick={openNewProject}
                >
                  <Plus size={15} /> {t("New project")}
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="production-heading">
                <div className="production-name">
                  <current.icon size={18} />
                  <h1>{t(current.name)}</h1>
                  <span
                    className={`stage-status ${statusKind}`}
                    title={detail.state.stale[step] ? t(detail.state.stale[step].reason) : undefined}
                  >
                    <span className="status-dot" />
                    {status}
                  </span>
                  {(detail.state.stale[step] || (step === "4" && detail.state.summary_stale) || (step === "5" && detail.state.three_by_four_stale)) && (
                    <button
                      className="button small"
                      disabled={busy || Boolean(activeJob) || activeAgent}
                      title={t("Keep these outputs as they are. Nothing is regenerated.")}
                      onClick={() =>
                        action(async () => {
                          setDetail(await api<Detail>(projectRoute(detail.id) + "/keep-current", { step, summary: true, three_by_four: true }));
                          notice("Kept the current outputs. This step no longer needs a rebuild.");
                        })
                      }
                    >
                      <CheckCircle2 size={13} /> {t("Keep current")}
                    </button>
                  )}
                </div>
                <div className="production-actions">
                  {["1", "4", "6"].includes(step) && <button className="icon-button"
                    aria-label={t("Edit skills & templates")} title={t("Edit skills & templates")}
                    onClick={() => openLibrary()}><FileText size={15} /></button>}
                  <button
                    className="icon-button"
                    aria-label={t("Refresh project outputs")}
                    title={t("Refresh project outputs")}
                    onClick={() =>
                      action(async () => {
                        await load(detail.id);
                        notice(
                          dirty
                            ? "Outputs refreshed. Your draft is preserved."
                            : "Outputs refreshed.",
                        );
                      })
                    }
                  >
                    <RefreshCw size={15} />
                  </button>
                  <span className="action-divider" />
                  {dirty && (
                    <button
                      className="button small primary"
                      onClick={save}
                      disabled={busy}
                    >
                      <Save size={13} /> {t("Save changes")}
                    </button>
                  )}
                  <button
                    className="button small"
                    disabled={busy || Boolean(activeJob) || activeAgent}
                    onClick={() => {
                      if (step === "6" && coverAgent) {
                        activate();
                        return;
                      }
                      // Outdated segments are the usual next work when there are some.
                      setScope(!["3", "4"].includes(step) ? "all" : detail.state.stale[step]?.segments.length ? "outdated" : "selected");
                      setRebuild(false);
                      setWithOpening(selected === 0);
                      setVisualAction(
                        segment?.visualizer === "hf" ? "render" : "static",
                      );
                      setModal("generate");
                    }}
                  >
                    <RefreshCw size={13} /> {t(step === "6" && coverAgent ? "New cover design" : "Generate")}
                  </button>
                </div>
              </div>
              <div className="stage-content">
                {step === "1" ? (
                  <div className="manuscript-workspace">
                    {writing && <WritingSetup value={writing} bootstrap={bootstrap}
                      disabled={busy || activeAgent || Boolean(activeJob)} pending={writingPending} subscription={claudeMode === "subscription"}
                      onChange={editWriting} onEditSkill={() => openLibrary()}
                      onSave={() => action(async () => { await saveWriting(); notice("Writing setup saved."); })} />}
                    {/* The title comes first, then the opening quote; cover text stays with the manuscript below. */}
                    <MetadataCandidates value={draft} selections={draftSelections}
                      fields={["video_titles"]} showHeading={false} className="title-candidates"
                      disabled={activeAgent || busy || Boolean(activeJob)}
                      onChange={(value, selections) => { edit(value); setDraftSelections(selections); }} />
                    <section className={"opening-quote-panel" + (openingEnabled ? "" : " excluded") + (openingCollapsed ? " collapsed" : "")} aria-label={t("Opening quote")}>
                      <div className="opening-quote-heading">
                        <button type="button" className="collapse-toggle" aria-expanded={!openingCollapsed}
                          aria-controls="opening-quote-body" title={t(openingCollapsed ? "Expand" : "Collapse")}
                          onClick={toggleOpeningPanel}>
                          {openingCollapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}
                          <span>{t("Opening quote")}</span>
                        </button>
                        {openingCollapsed && <span className="collapse-summary" title={openingSummary}>{openingSummary}</span>}
                        <label className="checkbox">
                          <input type="checkbox" checked={openingEnabled}
                            disabled={activeAgent || busy || Boolean(activeJob)}
                            onChange={(e) => toggleOpening(e.target.checked)} />
                          {t("Use opening quote")}
                        </label>
                      </div>
                      <div id="opening-quote-body" className="opening-quote-body" hidden={openingCollapsed}>
                        <p className="small-muted">{t(openingEnabled
                          ? "Plays before the manuscript. Choose a quote and save the manuscript; the switch is saved automatically."
                          : "The video will start with the manuscript. Your quote candidates and existing opening media are kept.")}</p>
                        <MetadataCandidates value={draft} selections={draftSelections}
                          fields={["golden_quotes"]} showHeading={false} hideLegend
                          disabled={activeAgent || busy || Boolean(activeJob)}
                          onChange={(value, selections) => { edit(value); setDraftSelections(selections); }} />
                        {openingEnabled && !openingQuote &&
                          <p className="opening-quote-hint" role="status">{t("Enter and save an opening quote, or turn it off to continue without one.")}</p>}
                      </div>
                    </section>
                    <div className="editor-heading">
                      <span>{t("Manuscript")}</span>
                      <span className="mono muted">
                        {t("{lines} lines · {sentences} sentences · {characters} characters", {
                          ...textCounts(String(draft.content || "")),
                          characters: String(draft.content || "").length.toLocaleString(locale),
                        })}
                      </span>
                    </div>
                    <NumberedText
                      className="manuscript-editor"
                      ariaLabel={t("Manuscript editor")}
                      value={String(draft.content || "")}
                      placeholder={t(
                        "Choose a writing mode above, then activate Claude Code or enter your manuscript here.",
                      )}
                      onChange={(content) => edit({ ...draft, content })}
                      disabled={activeAgent}
                    />
                    <MetadataCandidates
                      value={draft}
                      fields={["cover_titles", "cover_subtitles"]}
                      selections={draftSelections}
                      disabled={activeAgent || busy || Boolean(activeJob)}
                      onChange={(value, selections) => {
                        edit(value);
                        setDraftSelections(selections);
                      }}
                    />
                    <div className="editor-actions">
                      <button
                        className="button primary"
                        disabled={dirty || busy || activeAgent || Boolean(activeJob)}
                        onClick={activate}
                      >
                        <TerminalSquare size={14} />{" "}
                        {t("Activate writing agent")}
                      </button>
                      <button
                        className="button"
                        disabled={!dirty || busy || activeAgent || Boolean(activeJob)}
                        onClick={save}
                      >
                        <Save size={14} /> {t("Save manuscript")}
                      </button>
                    </div>
                  </div>
                ) : step === "2" ? (
                  <SegmentEditor
                    segments={detail.segments}
                    draft={draft.segments || []}
                    selected={selected}
                    search={search}
                    locked={busy || activeAgent || Boolean(activeJob)}
                    onSearch={setSearch}
                    onSelect={changeSegment}
                    onEdit={(index, key, value) => editSegment(key, value, index)}
                  />
                ) : (
                  <div className={`media-workspace${step === "4" ? " visuals-workspace" : step === "6" ? " covers-workspace" : ""}`}>
                    {step === "6" && <>
                      <CoverSetup key={detail.id} settings={keySettings.step6 || {}} library={bootstrap?.library}
                        disabled={busy || activeAgent || Boolean(activeJob)} copy={coverCopy}
                        agentMode={claudeMode === "subscription" ? "Your Claude Code · subscription" : "Built-in Claude Code · API key"}
                        onChange={saveCoverSetting} onEditStyles={() => openLibrary("cover_styles")} />
                      {coverAgent && <section className="cover-design-actions" aria-label={t("HTML cover design")}>
                        <div><strong>{t("HTML cover design")}</strong>
                          <p className="small-muted">{t(activeAgent && detail.state.agent_active?.step === "6"
                            ? "Claude Code is designing covers in the right pane. Review its snapshots below, then finish to render candidates."
                            : "Design, review snapshots, then render. Your existing cover candidates stay available.")}</p></div>
                        <div className="cover-design-buttons">
                          {activeAgent && detail.state.agent_active?.step === "6" ? <button className="button small primary"
                            disabled={busy || !detail.cover_design?.complete} onClick={() => agentHandle.current?.stop()}>
                            <Play size={13} />{t("Finish & render covers")}</button> : <>
                            <button className="button small primary" disabled={busy || unsaved || activeAgent || Boolean(activeJob)} onClick={activate}>
                              <TerminalSquare size={13} />{t("New cover design")}</button>
                            <button className="button small" disabled={busy || unsaved || activeAgent || Boolean(activeJob) || !detail.cover_design?.files.length}
                              onClick={() => activateFor(undefined, undefined, true)}>{t("Revise HTML design")}</button>
                            <button className="button small" disabled={busy || unsaved || activeAgent || Boolean(activeJob) || !detail.cover_design?.complete || !detail.cover_design.current}
                              onClick={() => action(async () => {
                                const job = await api<Job>(projectRoute(detail.id) + "/job", { step: "6", mode: "cover_render" });
                                setJobs((jobs) => [...jobs, job]);
                                notice("Rendering covers from Claude Code's HTML designs…");
                              })}>{t("Render saved HTML")}</button>
                          </>}
                        </div>
                        {detail.cover_design && !detail.cover_design.current && <p className="small-muted">{t("Cover copy or setup changed. Revise the HTML design before rendering.")}</p>}
                        {!!detail.cover_design?.previews.length && <div className="cover-design-previews">
                          {detail.cover_design.previews.map((file) => <img key={file} src={media(detail.id, file, version)}
                            alt={t("HTML cover snapshot")} loading="lazy" />)}
                        </div>}
                      </section>}
                    </>}
                    {step === "4" && <>
                      <VisualDirection key={detail.id} summary={String(detail.summary.summary || "")}
                        hash={detail.hashes.summary} outdated={Boolean(detail.state.summary_stale)}
                        disabled={busy || activeAgent || Boolean(activeJob)} onSave={saveDirection}
                        onGenerate={() => startVisualJob({ mode: "summary" }, "Generating the content summary.")}
                        onEditInstructions={() => openLibrary("summary")} onDirtyChange={setDirectionDirty}
                        actions={<>
                          <button className="button small" disabled={busy || unsaved || activeAgent || Boolean(activeJob)}
                            title={t("Uses static-image segments and the enabled opening.")}
                            onClick={() => startVisualJob({ mode: "static", batch: "missing" }, "Generating missing static images.")}>
                            <Plus size={13} />{t("Fill missing images")}</button>
                          <button className="button small" disabled={busy || unsaved || activeAgent || Boolean(activeJob)}
                            title={t("Adds a new image for every static-image segment and the enabled opening; earlier images are kept.")}
                            onClick={() => startVisualJob({ mode: "static", batch: "all" }, "Generating all static images.")}>
                            <RefreshCw size={13} />{t("Generate all images")}</button>
                        </>} />
                    </>}
                    <div className={`preview-toolbar${step === "5" ? " output-toolbar" : ""}`}>
                      {step === "5" ? (
                        <div className="output-variants" role="group" aria-label={t("Outputs")}>
                          {(["landscape", "portrait"] as const).map((variant) => {
                            const [label, kind] = variantStatus(variant);
                            const portrait = variant === "portrait";
                            return (
                              <div key={variant} className={`output-variant${exportVariant === variant ? " selected" : ""}${portrait && !portraitEnabled ? " off" : ""}`}>
                                <button type="button" className="variant-select" aria-pressed={exportVariant === variant}
                                  onClick={() => { setExportVariant(variant); setPortraitView(null); }}>
                                  <span className={`variant-frame ${variant}`} aria-hidden="true" />
                                  <span className="variant-text">
                                    <strong>{portrait ? t("3:4 copy") : t("16:9 video")}</strong>
                                    <small>
                                      {!portrait && <>{t("Always made")} · </>}
                                      <span className={`variant-status ${kind}`}>{t(label)}</span>
                                    </small>
                                  </span>
                                </button>
                                {portrait && (
                                  <label className="switch" title={t(portraitEnabled ? "Stop making the 3:4 copy" : "Also make the 3:4 copy")}>
                                    <input type="checkbox" role="switch" checked={portraitEnabled} aria-label={t("Make the 3:4 copy")}
                                      disabled={Boolean(activeJob) || activeAgent}
                                      onChange={(e) => saveSettings("three_by_four", { enabled: e.target.checked })} />
                                    <span aria-hidden="true" />
                                  </label>
                                )}
                              </div>
                            );
                          })}
                          {portraitEnabled && (() => {
                            // The 3:4 copy is drawn around the 16:9 video, so that video must exist and be current.
                            const missing = !landscapeFile || assemblyStale;
                            const needed = !missing && (portraitStale || !portraitFile);
                            return (
                              <button type="button" className={`button small build-portrait${needed ? " primary" : ""}`}
                                disabled={missing || busy || Boolean(activeJob) || activeAgent}
                                title={t(missing
                                  ? "Generate the 16:9 video first. The 3:4 copy is made from it."
                                  : "Redraws only the 3:4 copy around the current 16:9 video, much faster than generating the video.")}
                                onClick={buildPortrait}>
                                <RefreshCw size={13} /> {portraitFile && !portraitStale ? t("Rebuild 3:4 copy") : t("Build 3:4 copy")}
                              </button>
                            );
                          })()}
                        </div>
                      ) : (
                      <div>
                        <span className="mono">
                          {step === "6"
                              ? t("COVER")
                              : selected === 0
                                ? t("OPENING")
                                : t("SEGMENT {index}", {
                                    index: String(selected).padStart(3, "0"),
                                  })}
                        </span>
                        <span className="muted">
                          {step === "6"
                              ? t("{count} candidates", {
                                  count: detail.covers.length,
                                })
                              : selected === 0
                                ? t("Opening quote")
                                : step === "3"
                                  ? t("Recording")
                                  : visualLabel}
                        </span>
                        {selectedStale && (
                          <span className="toolbar-flag" title={t(selectedStale)}>{t("Out of date")}</span>
                        )}
                      </div>
                      )}
                      <div>
                        {step === "4" && (
                          <>
                            {selectedTake?.kind === "motion" && (
                              <button className="button small" onClick={() => setClipView({ index: selected, take: selectedTake.id })}>
                                <Film size={13} /> {t("Open clip")}
                              </button>
                            )}
                            <button className="button small takes-button" data-picker-toggle aria-haspopup="dialog" aria-expanded={Boolean(picker)}
                              onClick={(e) => {
                                const rect = e.currentTarget.getBoundingClientRect();
                                setPicker(picker ? null : { index: selected, rect, placement: "below" });
                              }}>
                              <Layers size={13} /> {t("Versions")} <span className="takes-count">{selectedTakes.length}</span>
                              <ChevronDown size={12} />
                            </button>
                            <span className="action-divider" />
                          </>
                        )}
                        {step === "5" && exportVariant === "portrait" && portraitFile && (
                          <div className="view-toggle" role="group" aria-label={t("3:4 view")}>
                            {(["video", "layout"] as const).map((view) => (
                              <button key={view} type="button" aria-pressed={portraitShows === view} onClick={() => setPortraitView(view)}>
                                {view === "video" ? t("Video") : t("Layout")}
                              </button>
                            ))}
                          </div>
                        )}
                        {step === "6" && detail.covers.length > 0 && (
                          <select
                            aria-label={t("Cover candidate")}
                            value={coverIndex}
                            onChange={(e) => {
                              const i = Number(e.target.value);
                              setCoverIndex(i);
                              action(async () => {
                                setDetail(
                                  await api<Detail>(
                                    projectRoute(detail.id) + "/select-cover",
                                    { path: detail.covers[i] },
                                  ),
                                );
                              });
                            }}
                          >
                            {detail.covers.map((f, i) => (
                              <option key={f} value={i}>
                                {t("Cover {index}", { index: i + 1 })}
                              </option>
                            ))}
                          </select>
                        )}
                        <button
                          className="icon-button"
                          aria-label={t("Show in Finder or Explorer")}
                          title={t("Show in Finder or Explorer")}
                          // The 3:4 layout preview is a working file, not a project output.
                          disabled={!selectedMedia || showingLayout}
                          onClick={() => selectedMedia && showFiles(selectedMedia)}
                        >
                          <FolderSearch size={15} />
                        </button>
                        {step !== "3" && (
                          <button
                            className="icon-button"
                            aria-label={t("Fullscreen preview")}
                            title={t("Fullscreen preview")}
                            disabled={!previewMedia}
                            onClick={() =>
                              (video.current || document.querySelector(".preview-image"))
                                ?.requestFullscreen()
                                .catch(() => {})
                            }
                          >
                            <Maximize2 size={15} />
                          </button>
                        )}
                      </div>
                    </div>
                    {step === "4" && agentSegments.includes(selected) && (
                      <div className="agent-banner" role="status">
                        <Loader2 size={14} className="spin" />
                        <span>
                          {selectedTake
                            ? t("Claude Code is working on {clip} in the right pane. Keyframes appear as it saves them.", { clip: visualName })
                            : t("Claude Code is designing a new motion clip in the right pane.")}
                        </span>
                        <button className="button small primary" disabled={!segment?.motion?.composition} onClick={() => agentHandle.current?.stop()}
                          title={segment?.motion?.composition ? undefined : t("Available once Claude Code saves its composition.")}>
                          <Play size={13} /> {t("Finish & render")}
                        </button>
                      </div>
                    )}
                    <div
                      className={`preview-canvas ${step === "3" ? "audio-preview" : ""}`}
                    >
                      {step === "3" ? (
                        <>
                        {voiceBackdrop && (
                          <img className="voice-backdrop" src={media(detail.id, voiceBackdrop, version)} alt="" />
                        )}
                        <div className="voice-preview">
                          <Music2 size={28} />
                          <h2>
                            {reviewAudio?.audio
                              ? t("Listen to the delivery.")
                              : t("No recording yet.")}
                          </h2>
                          <p>
                            {selected === 0
                              ? t("Opening")
                              : t("Segment {index}", { index: selected })}{" "}
                            ·{" "}
                            {reviewAudio?.duration
                              ? t("{seconds} seconds", {
                                  seconds: reviewAudio.duration!.toFixed(1),
                                })
                              : t("Generate a recording to review.")}
                          </p>
                          {reviewAudio?.audio && (
                            <audio
                              controls
                              src={media(detail.id, reviewAudio.audio, version)}
                              aria-label={t("Segment recording")}
                            />
                          )}
                        </div>
                        </>
                      ) : previewMedia ? (
                        /\.(mp4|mov|webm)$/i.test(selectedMedia!) ? (
                          <video
                            key={`${selectedMedia}-${version}`}
                            ref={video}
                            controls
                            src={previewMedia}
                            onError={() =>
                              notice(
                                "This export codec may not play in the embedded viewer. Use H.264 for a compatible preview.",
                              )
                            }
                            aria-label={t("Production video preview")}
                          />
                        ) : (
                          <img
                            className="preview-image"
                            src={previewMedia}
                            alt={
                              step === "6"
                                ? t("Generated cover")
                                : t("Visual for segment {index}", {
                                    index: selected,
                                  })
                            }
                          />
                        )
                      ) : (
                        <div className="preview-empty">
                          {showingLayout && layoutBusy ? <Loader2 size={30} className="spin" /> : <Film size={30} />}
                          <h2>
                            {showingLayout
                              ? t(layoutBusy ? "Drawing the 3:4 layout…" : "The 3:4 layout appears here.")
                              : step === "5"
                              ? t("Your assembled story appears here.")
                              : step === "6"
                                ? t("Your cover appears here.")
                                : motion
                                  ? t("This motion clip is not designed yet.")
                                  : step === "4" && selectedTakes.length
                                    ? t("No version is in use.")
                                    : t("A frame for this part of the story.")}
                          </h2>
                          <p>
                            {showingLayout
                              ? t("Its panels follow the 3:4 settings on the right.")
                              : step === "5"
                              ? t(
                                  "Generate assembly after recordings and visuals are ready.",
                                )
                              : step === "6"
                                ? t(
                                    "Generate cover candidates from your chosen title.",
                                  )
                                : motion
                                  ? t("Open the clip to revise it with Claude Code.")
                                  : step === "4" && selectedTakes.length
                                    ? t("Choose one of this part's versions, or make a new one.")
                                    : t(
                                        "Generate an image or activate Claude Code for motion.",
                                      )}
                          </p>
                          {step === "4" && (
                            <div className="preview-empty-actions">
                              {selectedTakes.length > 0 && (
                                <button className="button small" data-picker-toggle onClick={(e) =>
                                  setPicker({ index: selected, rect: e.currentTarget.getBoundingClientRect(), placement: "below" })}>
                                  <Layers size={13} /> {t("Versions")}
                                </button>
                              )}
                              <button className="button small primary" disabled={Boolean(makeLock())} title={t(makeLock()) || undefined}
                                onClick={() => newImage(selected)}>
                                <Sparkles size={13} /> {t(selected === 0 ? "New opening" : "New image")}
                              </button>
                              {selected !== 0 && (
                                <button className="button small" disabled={Boolean(makeLock())} title={t(makeLock()) || undefined}
                                  onClick={() => newClip(selected)}>
                                  <TerminalSquare size={13} /> {t("New motion clip")}
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                      {motionPreview?.note && previewMedia && (
                        <span className="preview-note">{motionPreview.note}</span>
                      )}
                      {showingLayout && previewMedia && (
                        <span className="preview-note">
                          {layoutBusy && <Loader2 size={11} className="spin" />}
                          {t(currentLayout?.source === "video"
                            ? "Layout preview · a frame of the 16:9 video"
                            : currentLayout?.source === "segment"
                              ? "Layout preview · the first segment's picture"
                              : "Layout preview")}
                          {(!portraitFile || portraitStale || !portraitEnabled) && <> · {t(portraitEnabled ? "build the 3:4 copy to see it move" : "the 3:4 copy is off")}</>}
                        </span>
                      )}
                    </div>
                    <div className="preview-caption">
                      <span title={selectedMedia || undefined}>
                        {selectedMedia || t("Awaiting production")}
                      </span>
                      <span className="mono">
                        {step === "5"
                          ? formatTime(totalDuration)
                          : step === "6"
                            ? ""
                            : reviewAudio?.duration
                              ? t("{seconds}s", {
                                  seconds: reviewAudio.duration!.toFixed(1),
                                })
                              : t("Estimated")}
                      </span>
                    </div>
                    {["3", "4"].includes(step) && (
                      <div className="narration-inspector">
                        <div className="narration-heading">
                          <span>
                            <FileText size={14} /> {t("Narration")}
                          </span>
                          {selected === 0 && (
                            <span className="small-muted">{t("Edit the opening quote in Writing.")}</span>
                          )}
                        </div>
                        <textarea
                          aria-label={t("Narration editor")}
                          value={narration}
                          onChange={(e) =>
                            editSegment("content", e.target.value)
                          }
                          disabled={
                            selected === 0 || Boolean(activeJob) || activeAgent
                          }
                        />
                        <div className="narration-footer">
                          <span>
                            {narration.length} {t("characters ·")}{" "}
                            {reviewAudio?.duration
                              ? t("Recorded duration")
                              : t("Estimated duration")}
                          </span>
                          {reviewAudio?.audio && step === "4" && (
                            <audio
                              controls
                              src={media(detail.id, reviewAudio.audio, version)}
                              aria-label={t("Preview narration")}
                            />
                          )}
                          <button
                            className="text-button"
                            disabled={
                              Boolean(detail.state.stale[step]) ||
                              !detail.ready[step]
                            }
                            onClick={() =>
                              action(async () => {
                                setDetail(
                                  await api<Detail>(
                                    projectRoute(detail.id) + "/review",
                                    { step },
                                  ),
                                );
                                notice("This stage is marked as reviewed.");
                              })
                            }
                          >
                            <CheckCircle2 size={13} /> {t("Mark reviewed")}
                          </button>
                        </div>
                      </div>
                    )}
                    {step === "6" && detail.covers.length > 0 && (
                      <div className="cover-grid">
                        {detail.covers.map((f, i) => (
                          <div className="cover-item" key={f}>
                          <button
                            className={`cover-tile${coverIndex === i ? " selected" : ""}`}
                            onClick={() =>
                              action(async () => {
                                setCoverIndex(i);
                                setDetail(
                                  await api<Detail>(
                                    projectRoute(detail.id) + "/select-cover",
                                    { path: f },
                                  ),
                                );
                              })
                            }
                          >
                            <img
                              loading="lazy"
                              src={media(detail.id, f, version)}
                              alt={t("Cover candidate {index}", {
                                index: i + 1,
                              })}
                            />
                            <span>{t("Cover {index}", { index: i + 1 })}</span>
                          </button>
                          <DeleteControl item={t("Cover {index}", { index: i + 1 })}
                            blocked={activeJob ? "Wait for the running job to finish." : activeAgent ? "Finish the Claude Code session first." : busy ? "Working…" : ""}
                            onDelete={() => deleteCover(f)} />
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
              {/* Writing and Covers do not work per segment, so the sequence stays out of their way. */}
              {detail.segments.length > 0 && !["1", "6"].includes(step) && (
                <Timeline
                  openingEnabled={
                    openingEnabled && ["2", "3", "4", "5"].includes(step)
                  }
                  detail={detail}
                  selected={selected}
                  onSelect={changeSegment}
                  version={version}
                  focus={step === "3" ? "voice" : step === "4" ? "visual" : "both"}
                  onPick={step === "4" ? (index, rect) => {
                    // A segment with an unsaved narration edit cannot be left, so its list does not open either.
                    if (dirty && index !== selected) return;
                    setPicker({ index, rect, placement: "above" });
                  } : undefined}
                />
              )}
              {runningJobs.map((job) => (
                <div className="job-strip" key={job.id}>
                  <Loader2 size={14} className="spin" />
                  <span>
                    {t("Step")} {stageNumber(job.step)} ·{" "}
                    {t(job.log.at(-1) || "Starting production…")}
                  </span>
                  <button
                    disabled={job.status === "cancelling"}
                    onClick={() =>
                      api("/jobs/" + job.id + "/cancel", {}).catch((e) =>
                        notice(String(e)),
                      )
                    }
                  >
                    {t("Stop job")}
                  </button>
                </div>
              ))}
            </>
          )}
        </main>
        {detail && step === "5" && <aside className="cover-inspector" aria-label={t("Assembly setup")}>
          <AssemblySetup key={detail.id} step5={step5Settings} panels={panelSettings}
            music={bootstrap?.music || []} userMusic={bootstrap?.user_music || []} copy={coverCopy}
            disabled={Boolean(activeJob) || activeAgent} musicUrl={musicUrl}
            onChange={saveSettings} onAddMusic={addMusic} />
        </aside>}
        {detail && (
          <>
            <div
              className={`pane-resizer ${agentShown ? "" : "hidden"}`}
              onPointerDown={resizeAgent}
            />
            <div
              className={
                agentShown ? "agent-container" : "agent-container hidden"
              }
            >
              <AgentPane
                handle={agentHandle}
                target={step === "4" && selected !== 0 ? t("Segment {index}", { index: selected }) : undefined}
                step={step}
                launch={launch}
                onActivate={activate}
                onFinished={finished}
                onClose={() => setAgentVisible(false)}
                onError={notice}
                directWriting={step === "1" && writing?.mode === "direct"}
                claudeMode={claudeMode}
                disabled={busy || unsaved || activeAgent || Boolean(activeJob)}
              />
            </div>
          </>
        )}
      </div>
      <footer className="app-statusbar">
        <span>
          <span className={`status-dot ${error ? "error" : bootstrap ? "live" : ""}`} />
          {error
            ? t("The production service could not start")
            : bootstrap
              ? t("Production service ready")
              : t("Starting production service")}
        </span>
        <span>
          {detail?.segments.length
            ? t("{count} segments · {duration}", {
                count: detail.segments.length,
                duration: formatTime(totalDuration),
              })
            : t("Document → story → video")}
        </span>
        <span>
          {activeAgent
            ? t("Claude Code · interactive")
            : busy
              ? t("Working…")
              : t("All files stay on your computer")}
        </span>
      </footer>
      {toast && (
        <div className="toast" role="status">
          <span>{t(toast)}</span>
          <button
            className="icon-button"
            aria-label={t("Dismiss notification")}
            onClick={() => setToast("")}
          >
            <X size={14} />
          </button>
        </div>
      )}
      {modal === "settings" && (
        <SettingsPanel
          detail={detail}
          bootstrap={bootstrap}
          initialTab={settingsTab}
          claudeMode={claudeMode}
          onClaudeMode={setClaudeMode}
          onKeyStatus={setKeyStatus}
          onLibraryChanged={refreshLibrary}
          onClose={() => setModal(null)}
          onSave={(settings: Settings) =>
            action(async () => {
              if (!detail) return;
              const d = await api<Detail>(
                projectRoute(detail.id) + "/settings",
                { settings },
              );
              setDetail(d);
              setModal(null);
              notice("Project settings saved.");
            })
          }
        />
      )}
      {modal === "library" && <LibraryPanel initialGroup={libraryTarget.group} initialId={libraryTarget.id}
        onClose={() => setModal(null)} onChanged={refreshLibrary}
        onUse={detail && !unsaved && !activeJob && !activeAgent ? (group, id) => {
          action(async () => {
            if (group === "writing_skills" && writing) {
              await saveWriting({ ...writing, agent_skill: id });
              setModal(null); notice("Writing setup saved."); return;
            }
            const [section, key] = librarySettings[group];
            const settings = structuredClone(detail.state.settings);
            settings[section] = { ...(bootstrap?.defaults[section] || {}), ...(settings[section] || {}), [key]: id };
            setDetail(await api<Detail>(projectRoute(detail.id) + "/settings", { settings }));
            setModal(null); notice("Project settings saved.");
          });
        } : undefined} />}
      {detail && step === "4" && picker && (
        <TakePicker key={picker.index} project={detail.id} anchor={picker} label={partName(picker.index)}
          takes={takesOf(picker.index)} selected={takeOf(picker.index)} version={version}
          opening={picker.index === 0} locked={Boolean(takeLock(picker.index))} lockReason={takeLock(picker.index)}
          renderLock={makeLock()}
          generating={jobCovers("static") || jobCovers("opening") || jobCovers("opening_render")}
          newImageLabel={picker.index === 0 ? "New opening" : "New image"}
          newImageHint={picker.index === 0
            ? "Renders a new opening from a new background image; earlier openings are kept"
            : "Generated by the image service, guided by the content summary"}
          onSelect={(take) => selectTake(picker.index, take)}
          onOpenClip={(take) => openClip(picker.index, take)}
          onNewImage={() => (makeLock() ? notice(makeLock()) : newImage(picker.index))}
          onNewClip={() => (makeLock() ? notice(makeLock()) : newClip(picker.index))}
          deleteLock={takeLock(picker.index) === "Save your narration edit first." ? "" : takeLock(picker.index)}
          onDelete={(take) => deleteTake(picker.index, take)}
          onUseBackground={(take) => {
            setPicker(null);
            startVisualJob({ mode: "opening_render", take: take.id },
              `Rendering a new opening with background ${numbers(detail.opening.takes).get(take.id)}…`);
          }}
          onClose={closePicker} />
      )}
      {detail && step === "4" && clipView && (() => {
        const takes = takesOf(clipView.index);
        const take = takes.find((x) => x.id === clipView.take);
        if (!take) return null;
        const elsewhere = agentSegments.length && !agentSegments.includes(clipView.index) ? agentSegments[0] : null;
        const lock = makeLock();
        return (
          <ClipPreview project={detail.id} take={take} version={version}
            title={`${partName(clipView.index)} · ${takeName(takes, take)}`}
            inUse={takeOf(clipView.index) === take.id}
            narration={clipView.index === 0 ? "" : detail.segments.find((s) => s.index === clipView.index)?.content || ""}
            revisable={clipView.index !== 0}
            agentHere={agentSegments.includes(clipView.index) && takeOf(clipView.index) === take.id}
            agentElsewhere={elsewhere}
            locked={Boolean(lock)} lockReason={lock}
            rendering={Boolean(activeJob && activeJob.step === "4" && activeJob.mode === "render" && activeJob.scope?.includes(clipView.index))}
            onUse={() => selectTake(clipView.index, take)}
            onRevise={() => {
              setClipView(null);
              if (clipView.index !== selected) changeSegment(clipView.index);
              activateFor([clipView.index], take.id);
            }}
            onRender={() => startVisualJob({ mode: "render", indices: [clipView.index], take: take.id }, `Rendering segment ${clipView.index}…`)}
            onFinish={() => agentHandle.current?.stop()}
            onShowFiles={showFiles}
            onClose={() => setClipView(null)} />
        );
      })()}
      {modal && modal !== "settings" && modal !== "library" && (
        <div className="modal-backdrop">
          <section
            className={`modal ${modal === "history" ? "history-modal" : ""}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby="modal-title"
          >
            <div className="modal-heading">
              <h2 id="modal-title">
                {modal === "new"
                  ? t("New production")
                  : modal === "generate"
                      ? t("Generate {stage}", {
                          stage: current.name.toLowerCase(),
                        })
                      : modal === "history"
                        ? t("Revision history")
                        : modal === "manage"
                          ? t("Manage projects")
                          : t("System check")}
              </h2>
              <button
                className="icon-button"
                aria-label={t("Close dialog")}
                onClick={() => setModal(null)}
              >
                <X size={18} />
              </button>
            </div>
            <div className="modal-content">
              {modal === "new" && (
                <>
                  <p className="muted">
                    {t("Choose a name and where to store this project, then pick document-based or direct writing at the top of Step 1.")}
                  </p>
                  <label className="field">
                    <span>{t("Project name")}</span>
                    <input
                      autoFocus
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      placeholder={t("Name your production")}
                    />
                  </label>
                  {modal === "new" && (
                    <>
                      <label className="field">
                        <span>{t("Project location")}</span>
                        <div className="input-action">
                          <input
                            value={newLocation}
                            spellCheck={false}
                            onChange={(e) => setNewLocation(e.target.value)}
                            placeholder={t("Choose a folder for this project")}
                          />
                          {isTauri() && (
                            <button
                              type="button"
                              className="button"
                              onClick={async () => {
                                try {
                                  const folder = await pickFolder(
                                    t("Choose project location"),
                                    newLocation,
                                  );
                                  if (folder) setNewLocation(folder);
                                } catch (e) {
                                  notice(String(e));
                                }
                              }}
                            >
                              <FolderOpen size={14} /> {t("Browse")}
                            </button>
                          )}
                        </div>
                      </label>
                      {newLocation.trim() && (
                        <p className="small-muted project-destination">
                          {t("The project folder will be created at")}
                          <code title={projectDestination(newLocation, newName)}>
                            {projectDestination(newLocation, newName)}
                          </code>
                        </p>
                      )}
                    </>
                  )}
                </>
              )}
              {modal === "generate" && (
                <>
                  <p className="muted">
                    {t(
                      "Keep a revision of the current outputs, then generate new work using your saved project settings.",
                    )}
                  </p>
                  {["3", "4"].includes(step) && (
                    <label className="field">
                      <span>{t("Scope")}</span>
                      <select
                        aria-label={t("Generation scope")}
                        value={scope}
                        onChange={(e) => setScope(e.target.value)}
                      >
                        <option value="selected">
                          {selected === 0
                            ? t("Opening only")
                            : t("Selected segment · {index}", {
                                index: selected,
                              })}
                        </option>
                        <option value="missing">{t("Missing segments")}</option>
                        {Boolean(detail?.state.stale[step]?.segments.length) && (
                          <option value="outdated">
                            {t("Outdated segments · {count}", { count: detail!.state.stale[step].segments.length })}
                          </option>
                        )}
                        <option value="all">{t("Entire step")}</option>
                      </select>
                    </label>
                  )}
                  {step === "4" && selected !== 0 && (
                    <label className="field">
                      <span>{t("Production action")}</span>
                      <select
                        aria-label={t("Visual generation action")}
                        value={visualAction}
                        onChange={(e) => setVisualAction(e.target.value)}
                      >
                        <option value="static">
                          {t("Generate static images")}
                        </option>
                        <option value="render">
                          {t("Render saved motion compositions")}
                        </option>
                        <option value="agent">
                          {t("Revise motion with Claude Code")}
                        </option>
                      </select>
                    </label>
                  )}
                  {["3", "4"].includes(step) && openingEnabled && (
                    <label className="checkbox">
                      <input
                        type="checkbox"
                        checked={withOpening}
                        onChange={(e) => setWithOpening(e.target.checked)}
                      />{" "}
                      {t("Also generate the opening")}
                    </label>
                  )}
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={rebuild}
                      onChange={(e) => setRebuild(e.target.checked)}
                    />{" "}
                    {t("Queue affected later steps for rebuilding")}
                  </label>
                  {rebuild && (
                    <p className="small-muted">
                      {t(
                        "Studio guides you through later steps, pausing for your review and interactive agents.",
                      )}
                    </p>
                  )}
                  {step === "4" && segment?.visualizer === "hf" && (
                    <p className="small-muted">
                      {t(
                        "A saved motion composition will be rendered as-is. To revise its design, activate Claude Code in the right pane.",
                      )}
                    </p>
                  )}
                </>
              )}
              {modal === "history" && (
                <>
                  {detail?.state.events.length ? (
                    detail.state.events.map((e) => (
                      <div className="history-row" key={e.id}>
                        <div>
                          <strong>{t(e.label)}</strong>
                          <span>
                            {new Date(e.time).toLocaleString(locale)} ·{" "}
                            {e.files.length} {t("files")}
                          </span>
                        </div>
                        <button
                          className="button small"
                          disabled={
                            busy ||
                            activeAgent ||
                            Boolean(activeJob) ||
                            e.files.length === 0
                          }
                          onClick={() =>
                            action(async () => {
                              const d = await api<Detail>(
                                projectRoute(detail!.id) + "/restore",
                                { revision: e.id },
                              );
                              setDetail(d);
                              initializeDraft(d, step);
                              setVersion((v) => v + 1);
                              notice(
                                "Revision restored. Affected later outputs need rebuilding.",
                              );
                            })
                          }
                        >
                          <Undo2 size={12} /> {t("Restore")}
                        </button>
                      </div>
                    ))
                  ) : (
                    <p className="muted">
                      {t(
                        "Your revisions will appear here when you save edits or generate a step.",
                      )}
                    </p>
                  )}
                </>
              )}
              {modal === "manage" && (
                <>
                  {removeTargets ? (
                    <div className="remove-confirm" role="alertdialog" aria-labelledby="remove-question">
                      <p id="remove-question" className="muted">
                        {removeTargets.length === 1
                          ? t("Remove this project from the list?")
                          : removeTargets.length === projects.length
                            ? t("Remove all projects from the list?")
                            : t("Remove {count} projects from the list?", { count: removeTargets.length })}
                      </p>
                      <ul className="manage-names">
                        {removeTargets.slice(0, 5).map((id) => (
                          <li key={id}>{projects.find((p) => p.id === id)?.source_name || id}</li>
                        ))}
                        {removeTargets.length > 5 && (
                          <li className="small-muted">{t("and {count} more", { count: removeTargets.length - 5 })}</li>
                        )}
                      </ul>
                      <label className="checkbox">
                        <input
                          type="checkbox"
                          checked={removeFiles}
                          onChange={(e) => setRemoveFiles(e.target.checked)}
                        />
                        {t("Also permanently delete the project files from this computer")}
                      </label>
                      <p className={"small-muted" + (removeFiles ? " remove-warning" : "")}>
                        {removeFiles
                          ? t("The folders and everything in them (manuscripts, recordings, images, videos) will be deleted. This cannot be undone.")
                          : t("The files stay on this computer. Use Open project folder to add one back.")}
                      </p>
                    </div>
                  ) : (
                    <>
                      <p className="muted">
                        {t("Remove projects you no longer need from the list. Their files stay on this computer unless you choose to delete them.")}
                      </p>
                      {projects.length ? (
                        <ul className="manage-list">
                          {projects.map((p) => (
                            <li key={p.id} className="manage-row">
                              <div className="manage-row-text" title={p.path}>
                                <strong>{p.source_name}</strong>
                                <small>
                                  {t("{count} segments", { count: p.segments })} · {folderTail(p.path, 2) || folderName(p.path)}
                                </small>
                              </div>
                              {p.id === detail?.id && <span className="manage-current">{t("Current")}</span>}
                              <button
                                className="icon-button danger"
                                aria-label={t("Remove {name}", { name: p.source_name })}
                                title={t("Remove {name}", { name: p.source_name })}
                                disabled={busy}
                                onClick={() => {
                                  setRemoveFiles(false);
                                  setRemoveTargets([p.id]);
                                }}
                              >
                                <Trash2 size={14} />
                              </button>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="muted">{t("No projects yet.")}</p>
                      )}
                    </>
                  )}
                </>
              )}
              {modal === "system" && bootstrap && (
                <>
                  <p className="muted">
                    {t("Everything production needs is included with Book2Video Studio. Nothing else has to be installed.")}
                  </p>
                  <div className="runtime-tools">
                    {bootstrap.components.map((c) => (
                      <div key={c.name} title={c.path || undefined}>
                        <span>{c.name}</span>
                        <span className={c.path ? "tool-ready" : "tool-missing"}>
                          {c.bundled ? t("Included") : c.path ? t("Found on this computer") : t("Missing")}
                        </span>
                      </div>
                    ))}
                  </div>
                  {[
                    { folder: "workspace", label: t("Projects folder"), path: bootstrap.default_location },
                    { folder: "library", label: t("Skills and templates folder"), path: bootstrap.library_folder },
                  ].map((f) => (
                    <label className="field" key={f.folder}>
                      <span>{f.label}</span>
                      <div className="input-action">
                        <input value={f.path} readOnly />
                        {isTauri() && (
                          <button
                            className="icon-button"
                            aria-label={t("Show in Finder or Explorer")}
                            title={t("Show in Finder or Explorer")}
                            onClick={() => invoke("open_folder", { folder: f.folder }).catch((e) => notice(String(e)))}
                          >
                            <FolderOpen size={16} />
                          </button>
                        )}
                      </div>
                    </label>
                  ))}
                </>
              )}
            </div>
            <div className="modal-actions">
              <button
                className="button"
                disabled={busy && Boolean(removeTargets)}
                onClick={() => (modal === "manage" && removeTargets ? setRemoveTargets(null) : setModal(null))}
              >
                {modal === "manage" && removeTargets
                  ? t("Back")
                  : modal === "history" || modal === "system" || modal === "manage"
                    ? t("Close")
                    : t("Cancel")}
              </button>
              {modal === "manage" && !removeTargets && projects.length > 0 && (
                <button
                  className="button danger"
                  disabled={busy}
                  onClick={() => {
                    setRemoveFiles(false);
                    setRemoveTargets(projects.map((p) => p.id));
                  }}
                >
                  <Trash2 size={14} />
                  {t("Remove all")}
                </button>
              )}
              {modal === "manage" && removeTargets && (
                <button
                  className="button danger solid"
                  disabled={busy}
                  onClick={() => removeProjects(removeTargets, removeFiles)}
                >
                  <Trash2 size={14} />
                  {removeFiles ? t("Delete permanently") : t("Remove from list")}
                </button>
              )}
              {modal === "new" && (
                <button
                  className="button primary"
                  disabled={busy || !newName.trim() || !newLocation.trim()}
                  onClick={() => newProject()}
                >
                  {t("Create project")}
                  <ArrowRight size={14} />
                </button>
              )}
              {modal === "generate" && (
                <button
                  className="button primary"
                  disabled={busy}
                  onClick={generate}
                >
                  {step === "1" ? t("Activate Claude Code") : t("Generate")}
                  <RefreshCw size={14} />
                </button>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
