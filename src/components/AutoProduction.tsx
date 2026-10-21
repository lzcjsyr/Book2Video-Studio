import { ArrowRight, Check, ChevronDown, ChevronRight, CircleAlert, Film, Image, KeyRound, ListVideo, Loader2, Minus, Music2,
  Square, TerminalSquare, X, Zap } from "lucide-react";
import { useEffect, useId, useState, type ReactNode } from "react";
import { useI18n } from "../i18n";
import { api, projectRoute } from "../api";
import { useCollapsed } from "../collapse";
import { libraryChoices } from "../library";
import { imageModelChoices, imageModelFor, imageServiceChoices, languageModelChoices, providerName, type KeyNeed } from "../keys";
import { autoStatus, planText } from "../autorun";
import type { AutoPlan, AutoRun, AutoRunOptions, Bootstrap, Detail, Job, Settings, Stage } from "../types";
import { SegmentationControls, VoiceFields } from "./StepControls";
import VisualImageControls from "./VisualImageControls";
import AssemblySetup from "./AssemblySetup";
import CoverSetup from "./CoverSetup";
import { styleLabels } from "./AssemblyEditing";

type Save = (section: string, patch: Record<string, unknown>) => Promise<boolean>;
const steps: { id: Stage; name: string; icon: typeof Zap }[] = [
  { id: "2", name: "Segments", icon: ListVideo },
  { id: "3", name: "Voice", icon: Music2 },
  { id: "4", name: "Visuals", icon: Image },
  { id: "5", name: "Assembly", icon: Film },
  { id: "6", name: "Covers", icon: Image },
];
const splitNames: Record<string, string> = { punctuation: "Split by punctuation", manual: "One segment per line", agent: "Agent (split by meaning)" };

/** A text setting that saves when it loses focus. */
function DraftField({ label, value, disabled, onCommit }: { label: string; value: string; disabled: boolean; onCommit: (value: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return <label className="field"><span>{label}</span>
    <input value={draft} disabled={disabled} spellCheck={false} onChange={(e) => setDraft(e.target.value)}
      onBlur={() => { if (draft.trim() !== value) onCommit(draft.trim()); }}
      onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />
  </label>;
}

/** The language model a step's agent uses. */
function ProviderFields({ section, settings, labels, disabled, onChange }: {
  section: string; settings: Settings; labels: [string, string]; disabled: boolean; onChange: Save;
}) {
  const { t } = useI18n();
  const server = String(settings[section]?.llm_server ?? "");
  return <div className="form-grid">
    <label className="field"><span>{t(labels[0])}</span>
      <select value={server} disabled={disabled} onChange={(e) => onChange(section, { llm_server: e.target.value })}>
        {!languageModelChoices.some((c) => c.id === server) && <option value={server}>{server ? t("Unavailable: {id}", { id: server }) : t("Choose an option")}</option>}
        {languageModelChoices.map((c) => <option key={c.id} value={c.id}>{t(c.label)}</option>)}
      </select>
    </label>
    <DraftField label={t(labels[1])} value={String(settings[section]?.llm_model ?? "")} disabled={disabled}
      onCommit={(llm_model) => onChange(section, { llm_model })} />
  </div>;
}

/** One step of the run: its plan or progress, and the settings it will use. */
function StepCard({ number, name, icon: Icon, state, plan, note, error, summary, onOpen, children, action }: {
  number: Stage; name: string; icon: typeof Zap; state: string; plan: string; note?: string; error?: string;
  summary: string; onOpen: () => void; children: ReactNode; action?: ReactNode;
}) {
  const uid = useId();
  const { t } = useI18n();
  const [collapsed, toggle] = useCollapsed(`auto-step-${number}`);
  const badge = state === "running" ? <Loader2 size={13} className="spin" /> : state === "done" ? <Check size={13} />
    : state === "failed" ? <X size={13} /> : state === "skipped" ? <Minus size={13} /> : number;
  return <section className={`auto-step ${state}`} aria-labelledby={`${uid}-name`}>
    <header className="auto-step-header">
      <button type="button" className="auto-step-toggle" aria-expanded={!collapsed} aria-controls={`${uid}-body`}
        title={t(collapsed ? "Expand" : "Collapse")} onClick={toggle}>
        <span className="auto-step-badge" aria-hidden="true">{badge}</span>
        <span className="auto-step-title">
          <strong id={`${uid}-name`}><Icon size={14} /> {t(name)}</strong>
          <span className="auto-step-plan">{plan}</span>
        </span>
        {collapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}
      </button>
      {action}
      <button type="button" className="icon-button" aria-label={t("Open {step}", { step: t(name) })} title={t("Open {step}", { step: t(name) })} onClick={onOpen}>
        <ArrowRight size={14} />
      </button>
    </header>
    {error && <p className="auto-step-alert" role="alert"><CircleAlert size={13} /> {t(error)}</p>}
    {note && <p className="auto-step-note"><CircleAlert size={13} /> {t("Still marked outdated: {reason}", { reason: t(note) })}</p>}
    {collapsed ? <p className="auto-step-summary" title={summary}>{summary}</p>
      : <div id={`${uid}-body`} className="auto-step-body">{children}</div>}
  </section>;
}

/**
 * Auto production: every choice Segments through Covers need, on one page, then one run that makes
 * them in order in the background. Choices save at once, exactly as on each step's own page.
 */
export default function AutoProduction({ detail, bootstrap, settings, run, jobs, locked, lockReason, subscription, gaps, onSettings,
  onOpenStep, onLibrary, onSettingsModal, onAddMusic, onToggleOpening, onStart, onStop, musicUrl, onError }: {
  detail: Detail;
  bootstrap: Bootstrap | null;
  /** The project's effective settings: defaults, then its saved choices. */
  settings: Settings;
  /** The project's latest automatic run, if any. */
  run: AutoRun | null;
  jobs: Job[];
  locked: boolean;
  lockReason: string;
  subscription: boolean;
  /** Keys this run needs that are not saved, for the plan it would follow. */
  gaps: (plan: AutoPlan | null) => KeyNeed[];
  onSettings: Save;
  onOpenStep: (step: Stage) => void;
  onLibrary: (group: string) => void;
  onSettingsModal: (tab: "production" | "keys") => void;
  onAddMusic: () => void;
  onToggleOpening: (enabled: boolean) => void;
  onStart: (options: AutoRunOptions, plan: AutoPlan) => void;
  onStop: () => void;
  musicUrl: (name: string) => string;
  onError: (message: string) => void;
}) {
  const uid = useId();
  const { t, locale } = useI18n();
  const running = Boolean(run && ["running", "stopping"].includes(run.status));
  const [options, setOptions] = useState<AutoRunOptions>({ scope: "missing", refresh_summary: false, covers: true });
  const [plan, setPlan] = useState<AutoPlan | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [running]);
  // What a run would do follows every saved choice and output, so it is asked again when they change.
  useEffect(() => {
    if (running) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      api<AutoPlan>(projectRoute(detail.id) + "/autorun-plan", options)
        .then((next) => { if (!cancelled) setPlan(next); })
        .catch((error) => { if (!cancelled) onError(String(error)); });
    }, 150);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [detail.id, detail.revision, options.scope, options.refresh_summary, options.covers, running]);

  const set = (section: string) => (patch: Record<string, unknown>) => onSettings(section, patch);
  const value = (section: string, key: string, fallback = "") => String(settings[section]?.[key] ?? fallback);
  const planned = (step: Stage) => plan?.steps.find((s) => s.step === step);
  const entry = (step: Stage) => run?.steps.find((s) => s.step === step);
  // During a run each card shows its progress; afterwards, the plan for the next run and how the last one ended there.
  const stateOf = (step: Stage) => {
    const live = running ? entry(step) : undefined;
    if (live) return live.status;
    const next = planned(step);
    return !next ? "pending" : next.off ? "off" : next.run ? "planned" : "current";
  };
  const textOf = (step: Stage) => {
    const live = running ? entry(step) : undefined;
    if (live?.status === "running") {
      const job = jobs.find((j) => j.id === run?.job);
      return job?.log.at(-1) ? t(job.log.at(-1)!) : t("Starting…");
    }
    if (live && live.status !== "pending") return t(autoStatus[live.status]);
    if (live) return t("Waiting for the steps before it");
    return planText(t, planned(step), plan?.segments ?? detail.segments.length, settings, options.scope === "all");
  };
  // How the last run left a step, while that still applies: the step still has work, or is still marked outdated.
  const failure = (step: Stage) => {
    const last = entry(step);
    return last && ["failed", "stopped"].includes(last.status) && last.message && (running || planned(step)?.run !== false) ? last.message : undefined;
  };
  const noteOf = (step: Stage) => (detail.state.stale[step] && entry(step)?.note) || undefined;

  const total = run?.steps.length || 0;
  const finished = run?.steps.filter((s) => ["done", "skipped"].includes(s.status)).length || 0;
  const current = run?.steps.find((s) => s.status === "running");
  const minutes = (seconds: number) => Math.max(1, Math.round(seconds / 60));
  const missing = gaps(plan);
  const nothingToDo = Boolean(plan && plan.steps.every((s) => !s.run));
  const opening = settings.global?.opening_quote !== false;
  const quote = String(detail.raw.golden_quotes?.[0] || "").trim();
  const motionCount = detail.segments.filter((s) => s.visualizer === "hf").length;
  const agentSplit = value("step2", "split_mode", "punctuation") === "agent";
  const assemblyAgent = value("step5", "assembly_method") === "agent";
  const coverAgent = value("step6", "cover_method") === "agent";
  const claudeSteps = [motionCount || agentSplit ? t("the motion clips") : "", assemblyAgent ? t("the video edit") : "", coverAgent && options.covers ? t("the HTML covers") : ""].filter(Boolean);
  const startProblem = locked ? lockReason : plan?.blocked || (missing.length ? "Add the missing API keys first." : nothingToDo ? "Everything is up to date. Choose Everything again to remake it." : "");
  const summaryText = String(detail.summary.summary || "").trim();

  const step3Summary = [bootstrap?.voices?.find((v) => v.id === value("step3", "voice"))?.name || value("step3", "voice"),
    value("step3", "tts_model").replace(/^seed-tts-2\.0-/, ""), t("Speaking rate {rate}", { rate: value("step3", "speech_rate", "0") })].filter(Boolean).join(" · ");
  const styleLabel = libraryChoices(bootstrap?.library, "image_styles").find((c) => c.id === value("step4", "image_style_preset"))?.label;
  const coverSizes = Array.isArray(settings.step6?.cover_image_size_keys) ? (settings.step6.cover_image_size_keys as string[]).join(", ") : "";

  return <div className="auto-production">
    <div className="auto-steps">
      <p className="auto-intro">{t("Set up how each remaining step works, then start. Grist makes them one after another in the background with these choices; they save at once and are the same ones each step's page uses.")}</p>

      <StepCard number="2" name="Segments" icon={ListVideo} state={stateOf("2")} plan={textOf("2")} error={failure("2")} note={noteOf("2")}
        summary={t(splitNames[value("step2", "split_mode", "punctuation")] || "Split by punctuation")} onOpen={() => onOpenStep("2")}>
        <SegmentationControls settings={settings.step2 || {}} disabled={locked} onChange={set("step2")} />
        <p className="small-muted">{t("Each segment becomes one recording and one picture or motion clip. Agent segmentation also chooses which segments get motion.")}</p>
        {agentSplit && <ProviderFields section="step2" settings={settings} labels={["Agent provider", "Agent model"]} disabled={locked} onChange={onSettings} />}
      </StepCard>

      <StepCard number="3" name="Voice" icon={Music2} state={stateOf("3")} plan={textOf("3")} error={failure("3")} note={noteOf("3")}
        summary={step3Summary} onOpen={() => onOpenStep("3")}>
        <div className="form-grid">
          <label className="field"><span>{t("Voice")}</span>
            <select value={value("step3", "voice")} disabled={locked} onChange={(e) => onSettings("step3", { voice: e.target.value })}>
              {!bootstrap?.voices?.some((v) => v.id === value("step3", "voice")) && <option value={value("step3", "voice")}>{value("step3", "voice") || t("Default voice")}</option>}
              {(bootstrap?.voices || []).map((v) => <option key={v.id} value={v.id}>{`${v.name} · ${v.id}`}</option>)}
            </select>
            <small className="field-hint">{t("Add voices in Settings → Production.")}</small>
          </label>
        </div>
        <VoiceFields settings={settings.step3 || {}} disabled={locked} onChange={set("step3")} />
      </StepCard>

      <StepCard number="4" name="Visuals" icon={Image} state={stateOf("4")} plan={textOf("4")} error={failure("4")} note={noteOf("4")}
        summary={[styleLabel ? t(styleLabel) : "", value("step4", "image_model")].filter(Boolean).join(" · ")} onOpen={() => onOpenStep("4")}>
        <div className="auto-subhead">{t("Content summary")}
          <span className={`auto-pill${detail.state.summary_stale ? " warn" : summaryText ? " ok" : ""}`}>
            {t(!summaryText ? "Written first" : detail.state.summary_stale ? "Outdated" : "Current")}</span></div>
        <p className="small-muted">{t("Written from the manuscript; every image and motion clip draws on it.")}</p>
        <ProviderFields section="step4_summary" settings={settings} labels={["Content summary provider", "Content summary model"]} disabled={locked} onChange={onSettings} />
        <div className="auto-subhead">{t("Image generation")}</div>
        <div className="form-grid">
          <label className="field"><span>{t("Image provider")}</span>
            <select value={value("step4", "image_server")} disabled={locked}
              onChange={(e) => onSettings("step4", { image_server: e.target.value, image_model: imageModelFor(e.target.value, value("step4", "image_model")) })}>
              {imageServiceChoices.map((c) => <option key={c.id} value={c.id}>{t(c.label)}</option>)}
            </select>
          </label>
          <label className="field"><span>{t("Image model")}</span>
            <select value={value("step4", "image_model")} disabled={locked} onChange={(e) => onSettings("step4", { image_model: e.target.value })}>
              {!imageModelChoices(value("step4", "image_server")).includes(value("step4", "image_model")) && <option value={value("step4", "image_model")}>{value("step4", "image_model")}</option>}
              {imageModelChoices(value("step4", "image_server")).map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
        </div>
        <VisualImageControls settings={settings.step4 || {}} library={bootstrap?.library} disabled={locked} onChange={set("step4")} onEdit={onLibrary} />
        {(motionCount > 0 || agentSplit) && <>
          <div className="auto-subhead">{t("Motion clips")}</div>
          <p className="small-muted">{motionCount ? t("{count} segments use motion. Claude Code designs each clip without a terminal, then Grist renders it.", { count: motionCount })
            : t("Agent segmentation may choose motion for some segments. Claude Code designs each clip without a terminal, then Grist renders it.")}</p>
          <div className="form-grid">
            <label className="field"><span>{t("Animation style")}</span>
              <select value={value("step4", "motion_style_preset")} disabled={locked} onChange={(e) => onSettings("step4", { motion_style_preset: e.target.value })}>
                {libraryChoices(bootstrap?.library, "motion_styles").map((c) => <option key={c.id} value={c.id}>{t(c.label)}</option>)}
              </select>
            </label>
          </div>
          {!subscription && <ProviderFields section="step4" settings={settings} labels={["Motion agent provider", "Motion agent model"]} disabled={locked} onChange={onSettings} />}
        </>}
      </StepCard>

      <StepCard number="5" name="Assembly" icon={Film} state={stateOf("5")} plan={textOf("5")} error={failure("5")} note={noteOf("5")}
        summary={[t(assemblyAgent ? "Claude Code editing" : "Standard assembly"), value("step5", "video_size", "1280x720").replace(/x/i, " × "),
          `${Number(settings.step5?.narration_speed_factor ?? 1).toFixed(2)}×`].join(" · ")} onOpen={() => onOpenStep("5")}>
        <div className="cover-methods assembly-methods" role="group" aria-label={t("Assembly method")}>
          {(["standard", "agent"] as const).map((method) => <button type="button" key={method} disabled={locked}
            className={`cover-method${assemblyAgent === (method === "agent") ? " selected" : ""}`} aria-pressed={assemblyAgent === (method === "agent")}
            onClick={() => onSettings("step5", { assembly_method: method })}>
            {method === "agent" ? <TerminalSquare size={18} /> : <Film size={18} />}
            <span><strong>{t(method === "agent" ? "Claude Code editing" : "Standard assembly")}</strong>
              <small>{t(method === "agent" ? "Claude Code designs and renders the whole video on its own, in the chosen style." : "Assemble selected visuals and recordings with your saved settings.")}</small></span>
          </button>)}
        </div>
        {assemblyAgent && <div className="form-grid">
          <label className="field"><span>{t("Style direction")}</span>
            <select value={value("step5", "agent_skill", "editorial-explainer")} disabled={locked} onChange={(e) => onSettings("step5", { agent_skill: e.target.value })}>
              {libraryChoices(bootstrap?.library, "assembly_skills").map((skill) => <option key={skill.id} value={skill.id}>{t(styleLabels[skill.id] || skill.label)}</option>)}
            </select>
          </label>
        </div>}
        <AssemblySetup variant="auto" step5={settings.step5 || {}} panels={settings.three_by_four || {}} music={bootstrap?.music || []}
          userMusic={bootstrap?.user_music || []} copy={{ title: "", subtitle: "", quote }} opening={{ enabled: opening, rendered: Boolean(detail.opening.clip) }}
          disabled={locked} musicUrl={musicUrl} onChange={onSettings} onAddMusic={onAddMusic} />
      </StepCard>

      <StepCard number="6" name="Covers" icon={Image} state={options.covers ? stateOf("6") : "off"} plan={textOf("6")} error={failure("6")} note={noteOf("6")}
        summary={[t(coverAgent ? "Claude Code + HTML / Three.js" : "Image API"), coverSizes].filter(Boolean).join(" · ")} onOpen={() => onOpenStep("6")}
        action={<label className="switch" title={t(options.covers ? "Leave covers out of this run" : "Make covers in this run")}>
          <input type="checkbox" role="switch" checked={options.covers} disabled={running} aria-label={t("Make covers in this run")}
            onChange={(e) => setOptions({ ...options, covers: e.target.checked })} />
          <span aria-hidden="true" />
        </label>}>
        <CoverSetup heading={false} settings={settings.step6 || {}} library={bootstrap?.library} disabled={locked}
          copy={{ title: String(detail.raw.cover_titles?.[0] || detail.raw.video_titles?.[0] || ""), subtitle: String(detail.raw.cover_subtitles?.[0] || "") }}
          agentMode={subscription ? "Your Claude Code · subscription" : "Built-in Claude Code · API key"}
          onChange={(key, v) => onSettings("step6", { [key]: v })} onEditStyles={() => onLibrary("cover_styles")} />
        {!coverAgent && <div className="form-grid">
          <label className="field"><span>{t("Cover provider")}</span>
            <select value={value("step6", "cover_image_server")} disabled={locked}
              onChange={(e) => onSettings("step6", { cover_image_server: e.target.value, cover_image_model: imageModelFor(e.target.value, value("step6", "cover_image_model")) })}>
              {imageServiceChoices.map((c) => <option key={c.id} value={c.id}>{t(c.label)}</option>)}
            </select>
          </label>
          <label className="field"><span>{t("Cover model ID")}</span>
            <select value={value("step6", "cover_image_model")} disabled={locked} onChange={(e) => onSettings("step6", { cover_image_model: e.target.value })}>
              {!imageModelChoices(value("step6", "cover_image_server")).includes(value("step6", "cover_image_model")) && <option value={value("step6", "cover_image_model")}>{value("step6", "cover_image_model")}</option>}
              {imageModelChoices(value("step6", "cover_image_server")).map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
        </div>}
      </StepCard>
      <p className="auto-more small-muted">
        {t("Frame size, codec, the opening masthead and other rarely changed choices are in Settings.")}{" "}
        <button type="button" className="link-button" onClick={() => onSettingsModal("production")}>{t("Open Settings")}</button>
      </p>
    </div>

    <aside className="auto-run" aria-label={t("Run")}>
      {running && run ? <section className="auto-progress" aria-live="polite">
        <div className="auto-progress-head">
          <Loader2 size={15} className="spin" />
          <strong>{run.status === "stopping" ? t("Stopping…") : current ? t("Step {number} · {name}", { number: current.step, name: t(steps.find((s) => s.id === current.step)!.name) }) : t("Starting…")}</strong>
          <span>{t("{done} of {total} steps", { done: finished, total })}</span>
        </div>
        <div className="auto-progress-bar" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={finished}>
          <span style={{ width: `${total ? (finished / total) * 100 : 0}%` }} />
        </div>
        <ol className="auto-progress-steps">
          {run.steps.map((s) => <li key={s.step} className={s.status}>
            <span className="auto-step-badge" aria-hidden="true">{s.status === "running" ? <Loader2 size={11} className="spin" /> : s.status === "done" ? <Check size={11} /> : s.status === "skipped" ? <Minus size={11} /> : s.step}</span>
            <span>{t(steps.find((x) => x.id === s.step)!.name)}</span>
            <small>{t(autoStatus[s.status])}</small>
          </li>)}
        </ol>
        <p className="small-muted">{t("Started {time} · {minutes} min", { time: new Date(run.started * 1000).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" }), minutes: minutes(now - run.started) })}</p>
        <button type="button" className="button danger" disabled={run.status === "stopping"} onClick={onStop}>
          <Square size={13} /> {t("Stop auto production")}
        </button>
        <p className="small-muted">{t("Stopping ends the current job. Finished steps keep their outputs.")}</p>
      </section> : <>
        <section className="auto-options">
          <h2>{t("Run")}</h2>
          <fieldset className="auto-scope">
            <legend>{t("What to make")}</legend>
            {(["missing", "all"] as const).map((scope) => <label key={scope} className={`auto-scope-choice${options.scope === scope ? " selected" : ""}`}>
              <input type="radio" name={`${uid}-scope`} checked={options.scope === scope} onChange={() => setOptions({ ...options, scope })} />
              <span><strong>{t(scope === "missing" ? "Missing and outdated" : "Everything again")}</strong>
                <small>{t(scope === "missing" ? "Keeps finished recordings, pictures, videos and covers." : "Splits the manuscript again and remakes every step. Revision history keeps the current outputs.")}</small></span>
            </label>)}
          </fieldset>
          {options.scope === "missing" && summaryText && detail.state.summary_stale && <label className="checkbox checkbox-hinted">
            <input type="checkbox" checked={options.refresh_summary} onChange={(e) => setOptions({ ...options, refresh_summary: e.target.checked })} />
            <span>{t("Rewrite the outdated content summary")}<small>{t("Every visual is then made again to follow it.")}</small></span>
          </label>}
        </section>
        <section className="auto-checks" aria-label={t("Before you start")}>
          <h3>{t("Before you start")}</h3>
          <ul>
            <li className="ok"><Check size={13} /><span>{t("Manuscript · {count} characters", { count: String(detail.raw.content || "").length.toLocaleString(locale) })}</span></li>
            <li className={opening && !quote ? "problem" : "ok"}>
              {opening && !quote ? <CircleAlert size={13} /> : <Check size={13} />}
              <span>{opening ? (quote ? t("Opening quote: {quote}", { quote }) : t("The opening is on, but no quote is chosen.")) : t("No opening: the video starts with the manuscript.")}</span>
              <label className="switch" title={t("Use opening quote")}>
                <input type="checkbox" role="switch" checked={opening} disabled={locked} aria-label={t("Use opening quote")} onChange={(e) => onToggleOpening(e.target.checked)} />
                <span aria-hidden="true" />
              </label>
            </li>
            <li className={missing.length ? "problem" : "ok"}>
              {missing.length ? <KeyRound size={13} /> : <Check size={13} />}
              <span>{missing.length ? t("Missing API keys: {keys}", { keys: [...new Set(missing.map((n) => n.key ? t(providerName(n.key)) : t(n.use)))].join(", ") }) : t("Every API key this run needs is saved.")}</span>
              {missing.length > 0 && <button type="button" className="button small" onClick={() => onSettingsModal("keys")}>{t("Add keys")}</button>}
            </li>
            {claudeSteps.length > 0 && <li className="info"><TerminalSquare size={13} />
              <span>{t("Claude Code makes {work} on its own, without a terminal · {mode}", { work: claudeSteps.length > 1 ? t("{list} and {last}", { list: claudeSteps.slice(0, -1).join(t(", ")), last: claudeSteps.at(-1)! }) : claudeSteps[0], mode: t(subscription ? "Your Claude Code · subscription" : "Built-in Claude Code · API key") })}</span></li>}
          </ul>
        </section>
        <button type="button" className="button primary auto-start" disabled={Boolean(startProblem) || !plan}
          onClick={() => plan && onStart(options, plan)}>
          <Zap size={15} /> {t("Start auto production")}
        </button>
        {startProblem ? <p className="auto-start-hint" role="status">{t(startProblem)}</p>
          : <p className="small-muted">{t("Runs in the background. You can open other projects meanwhile; this project's pages stay locked until it ends.")}</p>}
        {run && <div className={`auto-last ${run.status}`} role="status">
          <strong>{t(run.status === "succeeded" ? "Last run finished" : run.status === "stopped" ? "Last run was stopped" : "Last run failed")}</strong>
          <span>{run.ended ? t("{time} · {minutes} min", { time: new Date(run.ended * 1000).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" }), minutes: minutes(run.ended - run.started) }) : ""}</span>
          {run.status !== "succeeded" && (() => {
            const stopped = run.steps.find((s) => ["failed", "stopped"].includes(s.status));
            return stopped ? <p>{t("At {step}", { step: t(steps.find((x) => x.id === stopped.step)!.name) })}{stopped.message ? ` · ${t(stopped.message)}` : ""}</p> : null;
          })()}
        </div>}
      </>}
    </aside>
  </div>;
}

