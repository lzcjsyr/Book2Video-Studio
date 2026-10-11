import { useI18n } from "./i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Download,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileText,
  Film,
  FolderOpen,
  History,
  Image,
  ListVideo,
  Loader2,
  Maximize2,
  MoreHorizontal,
  Music2,
  PanelRightOpen,
  Plus,
  RefreshCw,
  Save,
  KeyRound,
  Settings2,
  ShieldCheck,
  TerminalSquare,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { open, save as chooseSave } from "@tauri-apps/plugin-dialog";
import {
  api,
  connect,
  media,
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
  Segment,
  Settings,
  Stage,
  TextData,
} from "./types";
import AgentPane from "./components/AgentPane";
import Timeline, { formatTime } from "./components/Timeline";
import SettingsPanel from "./components/SettingsPanel";
import MetadataCandidates, {
  primarySelections,
} from "./components/MetadataCandidates";
import LibraryPanel from "./components/LibraryPanel";
import BrandMark from "./components/BrandMark";
import WritingSetup from "./components/WritingSetup";
import VisualDirection from "./components/VisualDirection";
import SegmentEditor from "./components/SegmentEditor";
import { writingOptions, sameWriting, type WritingOptions } from "./writing";
import { useCollapsed } from "./collapse";
import { librarySettings } from "./library";
import { distinctGaps, keyNeeds, providerName, unmet, type NeedId } from "./keys";
import type { KeyStatus } from "./components/ApiKeysPanel";

// Stage ids follow the engine (Segments is its step 1.5); `number` is what people see.
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
    id: "1.5",
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
const stageNumber = (id: Stage) => stages.find((s) => s.id === id)?.number || id;
const clone = <T,>(data: T): T => structuredClone(data);
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
      | "regenerate"
      | "restart"
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
    [exportIndex, setExportIndex] = useState(0),
    [coverIndex, setCoverIndex] = useState(0);
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
  // Saved API keys (names only) and the Settings tab to open, for key reminders.
  const [keyStatus, setKeyStatus] = useState<KeyStatus | null>(null);
  const [claudeMode, setClaudeMode] = useState<ClaudeCodeMode>("api_key");
  const [settingsTab, setSettingsTab] = useState<"production" | "keys" | undefined>();
  function openSettings(tab?: "production" | "keys") {
    setSettingsTab(tab);
    setModal("settings");
  }
  const activeJob = jobs.find(
    (j) =>
      j.project === detail?.id &&
      (j.status === "running" || j.status === "cancelling"),
  );
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
    if (d.state.selected_cover)
      setCoverIndex(Math.max(0, d.covers.indexOf(d.state.selected_cover)));
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
                  `Next rebuild: ${stages.find((s) => s.id === next)?.name}. Review the outputs, then regenerate to continue.`,
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
    if (!["3", "4", "5"].includes(s) && selected === 0) setSelected(1);
    setSearch("");
    if (detail) initializeDraft(detail, s);
  }
  function changeSegment(n: number) {
    // Segments edits every row in place; other stages edit only the selected one.
    if (dirty && step !== "1.5") {
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
      notice("Saved. Affected outputs are marked for regeneration.");
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
  // Only Writing and Visuals work with Claude Code. The pane stays mounted elsewhere so a running session survives.
  const agentStep = step === "1" || step === "4";
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
  // Static images also write their shared direction first when it is missing or outdated.
  const visualNeeds = (): NeedId[] =>
    detail && (detail.state.summary_stale || !String(detail.summary.summary || "").trim())
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
      notice(enabled ? "Opening quote enabled. Regenerate the video to apply this change." : "Opening quote disabled. Regenerate the video to apply this change.");
    });
  }
  const totalDuration =
    detail?.segments.reduce(
      (n, s) => n + (s.duration ?? s.estimated_duration),
      openingEnabled ? detail.opening.duration || 0 : 0,
    ) || 0;
  const reviewAudio = selected === 0 ? detail?.opening : segment;
  const current = stages.find((s) => s.id === step)!;
  const status = detail?.state.stale[step]
    ? t("Needs rebuild")
    : detail?.state.reviewed[step]
      ? t("Reviewed")
      : detail?.ready[step]
        ? t("Ready to review")
        : t("Not generated");

  async function activateFor(indices?: number[]) {
    if (!detail || unsaved) {
      notice("Save changes before activating the agent.");
      return;
    }
    if (!keysReady([step === "1" ? "writing" : "motion_agent"])) return;
    if (step === "4" && indices?.includes(0)) {
      notice(
        "Regenerate the opening through Visuals; the motion agent works on narration segments.",
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
              `Next rebuild: ${stages.find((s) => s.id === next)?.name}. Review and regenerate when ready.`,
            );
          }
        })
        .catch((e) => notice(String(e)));
  }, []);
  async function regenerate() {
    if (!detail) return;
    await action(async () => {
      if (unsaved) throw new Error(t("Save changes before regenerating."));
      if (rebuild)
        queue.current = order
          .slice(order.indexOf(step) + 1)
          .filter((s) => !(s === "6" && ["3", "4", "5"].includes(step)));
      if (step === "1") {
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
            : undefined;
      let mode = step === "4" ? visualAction : undefined;
      const opening = openingEnabled && (withOpening || (selected === 0 && scope === "selected"));
      if (selected === 0 && scope === "selected") {
        indices = [];
        if (step === "4") mode = "opening";
      }
      if (step === "4" && mode === "agent") {
        setModal(null);
        await activateFor(indices);
        return;
      }
      const required: Partial<Record<Stage, NeedId[]>> = { "1.5": ["segments"], "3": ["voice"], "4": mode === "render" ? [] : visualNeeds(), "6": ["covers"] };
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
        `Regenerating ${current.name.toLowerCase()}. You can keep reviewing.`,
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
  async function startVisualJob(body: { mode: string; batch?: string }, message: string) {
    if (!detail) return;
    if (!keysReady(body.mode === "summary" ? ["direction"] : visualNeeds())) return;
    await action(async () => {
      if (unsaved) throw new Error(t("Save changes before regenerating."));
      const job = await api<Job>(projectRoute(detail.id) + "/job", { step: "4", ...body });
      setJobs((jobs) => [...jobs, job]);
      notice(message);
    });
  }
  async function saveDirection(summary: string, expected: string) {
    if (!detail) return false;
    let saved = false;
    await action(async () => {
      setDetail(await api<Detail>(projectRoute(detail.id) + "/save", {
        kind: "summary", value: { ...detail.summary, summary }, expected_hash: expected }));
      saved = true;
      notice("Visual direction saved. Visuals are marked for regeneration.");
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
  async function newProject(restart = false) {
    await action(async () => {
      if (writingPending) throw new Error(t("Save your writing setup before changing projects."));
      if (!restart && !newName.trim()) throw new Error(t("Enter a project name."));
      const created = await api<{ id: string }>(
        restart ? projectRoute(detail!.id) + "/restart" : "/projects",
        restart ? { name: newName } : { source: "", name: newName, location: newLocation, settings: { step1: { writing_mode: "direct" } } },
      );
      setProjects(await api<Project[]>("/projects"));
      setDirty(false);
      setWritingDraft(null);
      dirtyRef.current = false;
      setStep("1");
      setSelected(1);
      await load(created.id, "1");
      localStorage.setItem("studio-project", created.id);
      if (!restart) localStorage.setItem("studio-location", newLocation.trim());
      setModal(null);
      setLaunch(null);
      notice(
        restart
          ? "A new production revision is ready. Activate Claude Code to restart writing."
          : "Project created. Activate Claude Code to begin writing.",
      );
    });
  }
  function resizeAgent(e: React.PointerEvent) {
    e.preventDefault();
    const initial = e.clientX,
      width = agentWidth;
    const move = (m: PointerEvent) =>
      setAgentWidth(Math.max(285, Math.min(650, width + initial - m.clientX)));
    const up = () => {
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
  const selectedMedia =
    step === "3"
      ? reviewAudio?.audio
      : step === "5"
        ? detail?.exports[exportIndex]
        : step === "6"
          ? detail?.covers[coverIndex]
          : selected === 0
            ? detail?.opening.visual
            : segment?.visual;
  async function exportCopy() {
    if (!detail || !selectedMedia) return;
    await action(async () => {
      const destination = await chooseSave({
        title: t("Save output copy"),
        defaultPath: selectedMedia.split(/[\\/]/).at(-1),
      });
      if (destination) {
        await invoke("export_artifact", {
          project: detail.id,
          path: selectedMedia,
          destination,
        });
        notice("Production output saved.");
      }
    });
  }
  const previewMedia =
    selectedMedia && detail ? media(detail.id, selectedMedia, version) : null;

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
        <span className="header-save-state">
          {dirty
            ? t("Unsaved changes")
            : activeJob
              ? t("Production running")
              : t("Local workspace")}
        </span>
        {detail && projectGaps.length > 0 && (
          <button className="key-reminder" onClick={() => openSettings("keys")}
            title={projectGaps.map((n) => `${t(n.use)}: ${n.key ? t(providerName(n.key)) : t("unsupported provider")}`).join("\n")}>
            <KeyRound size={13} /> {distinctGaps(projectGaps) === 1 ? t("1 API key missing") : t("{count} API keys missing", { count: distinctGaps(projectGaps) })}
          </button>
        )}
        <button
          className="icon-button"
          title={t("Settings")}
          aria-label={t("Settings")}
          onClick={() => openSettings()}
        >
          <Settings2 size={17} />
        </button>
        <button
          className="icon-button"
          title={t("Revision history")}
          aria-label={t("Revision history")}
          disabled={!detail}
          onClick={() => setModal("history")}
        >
          <History size={17} />
        </button>
        {agentStep && !agentVisible && (
          <button
            className="icon-button"
            aria-label={t("Show Claude Code pane")}
            onClick={() => setAgentVisible(true)}
          >
            <PanelRightOpen size={17} />
          </button>
        )}
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
                  className={`stage-item ${step === s.id ? "active" : ""}`}
                  onClick={() => changeStage(s.id)}
                >
                  <span className="stage-number">{s.number}</span>
                  <span className="stage-text">
                    <strong>{t(s.name)}</strong>
                    <small>{t(s.description)}</small>
                  </span>
                  {stale ? (
                    <span className="stale-dot" title={t(stale.reason)} />
                  ) : ready ? (
                    <Check size={13} className="stage-check" />
                  ) : (
                    <span className="stage-empty-dot" />
                  )}
                </button>
              );
            })}
          </div>
          <div className="sidebar-bottom">
            <button
              disabled={!detail || Boolean(activeJob || activeAgent)}
              onClick={() => {
                if (writingPending) { notice("Save your writing setup before changing projects."); return; }
                setNewName("");
                setModal("restart");
              }}
            >
              <RefreshCw size={14} /> {t("Restart production")}
            </button>
            <button disabled={!bootstrap} onClick={() => setModal("system")}>
              <ShieldCheck size={14} /> {t("System check")}
            </button>
            <span className="small-muted">
              {t("Local production workspace")}
            </span>
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
                    className={`stage-status ${detail.state.stale[step] ? "needs-rebuild" : ""}`}
                  >
                    <span className="status-dot" />
                    {status}
                  </span>
                </div>
                <div className="production-actions">
                  {["1", "4", "6"].includes(step) && <button className="icon-button"
                    aria-label={t("Edit skills & templates")} title={t("Edit skills & templates")}
                    onClick={() => openLibrary()}><FileText size={15} /></button>}
                  <button
                    className="icon-button"
                    aria-label={t("Refresh project outputs")}
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
                      setScope(["3", "4"].includes(step) ? "selected" : "all");
                      setRebuild(false);
                      setWithOpening(selected === 0);
                      setVisualAction(
                        segment?.visualizer === "hf" ? "render" : "static",
                      );
                      setModal("regenerate");
                    }}
                  >
                    <RefreshCw size={13} /> {t("Regenerate")}
                  </button>
                  <button
                    className="button small continue"
                    disabled={
                      unsaved || writingPending ||
                      (step === "1" && openingEnabled && !String(draft.golden_quotes?.[draftSelections.golden_quotes ?? 0] || "").trim()) ||
                      activeAgent ||
                      Boolean(activeJob) ||
                      !detail.ready[step] ||
                      Boolean(detail.state.stale[step])
                    }
                    onClick={() => {
                      const next = order[order.indexOf(step) + 1];
                      if (next) changeStage(next);
                      else
                        action(async () => {
                          setDetail(
                            await api<Detail>(
                              projectRoute(detail.id) + "/review",
                              { step },
                            ),
                          );
                          notice("Cover review saved.");
                        });
                    }}
                  >
                    {step === "6" ? t("Finish review") : t("Continue")}
                    <ArrowRight size={13} />
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
                          fields={["golden_quotes"]} showHeading={false} hideLegend active={openingEnabled}
                          disabled={activeAgent || busy || Boolean(activeJob)}
                          onChange={(value, selections) => { edit(value); setDraftSelections(selections); }} />
                        {openingEnabled && !openingQuote &&
                          <p className="opening-quote-hint" role="status">{t("Enter and save an opening quote, or turn it off to continue without one.")}</p>}
                      </div>
                    </section>
                    <div className="editor-heading">
                      <span>{t("Manuscript")}</span>
                      <span className="mono muted">
                        {String(draft.content || "").length.toLocaleString(
                          locale,
                        )}{" "}
                        {t("characters")}
                      </span>
                    </div>
                    <textarea
                      className="manuscript-editor"
                      aria-label={t("Manuscript editor")}
                      value={String(draft.content || "")}
                      placeholder={t(
                        "Choose a writing mode above, then activate Claude Code or enter your manuscript here.",
                      )}
                      onChange={(e) =>
                        edit({ ...draft, content: e.target.value })
                      }
                      disabled={activeAgent}
                    />
                    <MetadataCandidates
                      value={draft}
                      fields={["video_titles", "cover_titles", "cover_subtitles"]}
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
                ) : step === "1.5" ? (
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
                  <div className="media-workspace">
                    {step === "4" && <>
                      <VisualDirection key={detail.id} summary={String(detail.summary.summary || "")}
                        hash={detail.hashes.summary} outdated={Boolean(detail.state.summary_stale)}
                        disabled={busy || activeAgent || Boolean(activeJob)} onSave={saveDirection}
                        onGenerate={() => startVisualJob({ mode: "summary" }, "Generating the visual direction.")}
                        onEditInstructions={() => openLibrary("summary")} onDirtyChange={setDirectionDirty} />
                      <div className="visual-batch-actions">
                        <div><span>{t("Batch images")}</span><small>{t("Uses static-image segments and the enabled opening.")}</small></div>
                        <button className="button small" disabled={busy || unsaved || activeAgent || Boolean(activeJob)}
                          onClick={() => startVisualJob({ mode: "static", batch: "missing" }, "Generating missing static images.")}>
                          <Plus size={13} />{t("Fill missing images")}</button>
                        <button className="button small" disabled={busy || unsaved || activeAgent || Boolean(activeJob)}
                          onClick={() => startVisualJob({ mode: "static", batch: "all" }, "Generating all static images.")}>
                          <RefreshCw size={13} />{t("Generate all images")}</button>
                      </div>
                    </>}
                    <div className="preview-toolbar">
                      <div>
                        <span className="mono">
                          {step === "5"
                            ? t("EXPORT")
                            : step === "6"
                              ? t("COVER")
                              : selected === 0
                                ? t("OPENING")
                                : t("SEGMENT {index}", {
                                    index: String(selected).padStart(3, "0"),
                                  })}
                        </span>
                        <span className="muted">
                          {step === "5"
                            ? exportIndex === 0
                              ? t("Landscape master")
                              : t("3:4 distribution")
                            : step === "6"
                              ? t("{count} candidates", {
                                  count: detail.covers.length,
                                })
                              : selected === 0
                                ? t("Opening quote")
                                : segment?.visualizer === "hf"
                                  ? t("Motion composition")
                                  : t("Static image")}
                        </span>
                      </div>
                      <div>
                        {step === "5" && (
                          <select
                            aria-label={t("Export variant")}
                            value={exportIndex}
                            onChange={(e) =>
                              setExportIndex(Number(e.target.value))
                            }
                          >
                            {detail.exports.map((f, i) => (
                              <option key={f} value={i}>
                                {f.includes("3x4")
                                  ? t("3:4 distribution")
                                  : t("Landscape master")}
                              </option>
                            ))}
                          </select>
                        )}
                        {step === "6" && (
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
                                {t("Cover")}
                                {i + 1}
                              </option>
                            ))}
                          </select>
                        )}
                        <button
                          className="icon-button"
                          aria-label={t("Save output copy")}
                          disabled={!selectedMedia || !isTauri()}
                          onClick={exportCopy}
                        >
                          <Download size={14} />
                        </button>
                        <button
                          className="icon-button"
                          aria-label={t("Fullscreen preview")}
                          onClick={() =>
                            video.current?.requestFullscreen() ||
                            document
                              .querySelector(".preview-image")
                              ?.requestFullscreen()
                          }
                        >
                          <Maximize2 size={14} />
                        </button>
                      </div>
                    </div>
                    <div
                      className={`preview-canvas ${step === "3" ? "audio-preview" : ""}`}
                    >
                      {step === "3" ? (
                        <div className="voice-preview">
                          <Music2 size={35} />
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
                          <Film size={34} />
                          <h2>
                            {step === "5"
                              ? t("Your assembled story appears here.")
                              : step === "6"
                                ? t("Your cover appears here.")
                                : t("A frame for this part of the story.")}
                          </h2>
                          <p>
                            {step === "5"
                              ? t(
                                  "Regenerate assembly after recordings and visuals are ready.",
                                )
                              : step === "6"
                                ? t(
                                    "Generate cover candidates from your chosen title.",
                                  )
                                : t(
                                    "Generate an image or activate Claude Code for motion.",
                                  )}
                          </p>
                        </div>
                      )}
                    </div>
                    <div className="preview-caption">
                      <span>
                        {selectedMedia ||
                          reviewAudio?.audio ||
                          t("Awaiting production")}
                      </span>
                      <span className="mono">
                        {step === "5"
                          ? formatTime(totalDuration)
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
                          {selected === 0 ? (
                            <span className="small-muted">{t("Edit the opening quote in Writing.")}</span>
                          ) : (
                          <div>
                            <select
                              aria-label={t("Segment visual type")}
                              value={scriptSegment?.visualizer || "image"}
                              disabled={
                                selected === 0 ||
                                Boolean(activeJob) ||
                                activeAgent
                              }
                              onChange={(e) =>
                                editSegment("visualizer", e.target.value)
                              }
                            >
                              <option value="image">{t("Static image")}</option>
                              <option value="hf">
                                {t("Motion / HyperFrames")}
                              </option>
                            </select>
                            <button
                              className="icon-button"
                              aria-label={t("More visual settings")}
                              onClick={() => openSettings()}
                            >
                              <MoreHorizontal size={16} />
                            </button>
                          </div>
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
                    {step === "5" && (
                      <div className="assembly-inspector">
                        <span>
                          <Music2 size={14} />{" "}
                          {t("Music, captions, and delivery")}
                        </span>
                        <button
                          className="button small"
                          onClick={() => openSettings()}
                        >
                          <Settings2 size={13} /> {t("Assembly settings")}
                        </button>
                        <p className="small-muted">
                          {t(
                            "Review the landscape master and 3:4 distribution copy. Select a segment in the sequence to seek to its position in the assembled video.",
                          )}
                        </p>
                      </div>
                    )}
                    {step === "6" && detail.covers.length > 0 && (
                      <div className="cover-grid">
                        {detail.covers.map((f, i) => (
                          <button
                            key={f}
                            className={coverIndex === i ? "selected" : ""}
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
                              src={media(detail.id, f, version)}
                              alt={t("Cover candidate {index}", {
                                index: i + 1,
                              })}
                            />
                            <span>
                              {t("Cover")}
                              {i + 1}
                            </span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
              {detail.segments.length > 0 && (
                <Timeline
                  openingEnabled={
                    openingEnabled && ["3", "4", "5"].includes(step)
                  }
                  detail={detail}
                  selected={selected}
                  onSelect={changeSegment}
                  version={version}
                  focus={step === "3" ? "voice" : step === "4" ? "visual" : "both"}
                />
              )}
              {activeJob && (
                <div className="job-strip">
                  <Loader2 size={14} className="spin" />
                  <span>
                    {t("Step")} {stageNumber(activeJob.step)} ·{" "}
                    {t(activeJob.log.at(-1) || "Starting production…")}
                  </span>
                  <button
                    onClick={() =>
                      api("/jobs/" + activeJob.id + "/cancel", {}).catch((e) =>
                        notice(String(e)),
                      )
                    }
                  >
                    {t("Stop job")}
                  </button>
                </div>
              )}
            </>
          )}
        </main>
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
          <span className="status-dot live" />
          {bootstrap ? t("Production service ready") : t("Starting production service")}
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
                  : modal === "restart"
                    ? t("Restart production")
                    : modal === "regenerate"
                      ? t("Regenerate {stage}", {
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
              {(modal === "new" || modal === "restart") && (
                <>
                  <p className="muted">
                    {modal === "restart"
                      ? t(
                          "Create a new production revision. Your existing scripts, recordings, and visuals are preserved.",
                        )
                      : t(
                          "Choose a name and where to store this project, then pick document-based or direct writing at the top of Step 1.",
                        )}
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
                  {modal === "restart" && detail && (
                    <p className="small-muted project-destination">
                      {t("The new project is saved beside this one, in")}
                      <code title={parentFolder(detail.path)}>
                        {parentFolder(detail.path)}
                      </code>
                    </p>
                  )}
                  {modal === "restart" && writing && <p className="small-muted">{t("The new project keeps your saved writing mode and skill.")}</p>}
                </>
              )}
              {modal === "regenerate" && (
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
                        aria-label={t("Regeneration scope")}
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
                        <option value="all">{t("Entire step")}</option>
                      </select>
                    </label>
                  )}
                  {step === "4" && selected !== 0 && (
                    <label className="field">
                      <span>{t("Production action")}</span>
                      <select
                        aria-label={t("Visual regeneration action")}
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
                      {t("Also regenerate the opening")}
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
                        "Your revisions will appear here when you save edits or regenerate a step.",
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
              {modal === "restart" && (
                <button
                  className="button primary"
                  disabled={busy || activeAgent || Boolean(activeJob)}
                  onClick={() => newProject(true)}
                >
                  {t("Restart production")}
                  <RefreshCw size={14} />
                </button>
              )}
              {modal === "regenerate" && (
                <button
                  className="button primary"
                  disabled={busy}
                  onClick={regenerate}
                >
                  {step === "1" ? t("Activate Claude Code") : t("Regenerate")}
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
