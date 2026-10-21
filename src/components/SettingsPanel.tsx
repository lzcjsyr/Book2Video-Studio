import { useI18n } from "../i18n";
import { useEffect, useId, useRef, useState } from "react";
import { X } from "lucide-react";
import type { Bootstrap, ClaudeCodeMode, Detail, Settings, LibraryChoice } from "../types";
import ApiKeysPanel, { type KeyStatus } from "./ApiKeysPanel";
import LibraryPanel from "./LibraryPanel";
import { libraryChoices, librarySettings } from "../library";
import { imageModelChoices, imageModelFits, imageModelFor, imageServiceChoices, languageModelChoices } from "../keys";
import ModelSelect from "./ModelSelect";
import VoicePicker from "./VoicePicker";
import AgentConnection from "./AgentConnection";

export default function SettingsPanel({
  detail,
  bootstrap,
  onSave,
  onClose,
  onLibraryChanged,
  onKeyStatus,
  initialTab,
  claudeMode,
  onClaudeMode,
  onDirtyChange,
}: {
  detail: Detail | null;
  bootstrap: Bootstrap | null;
  onSave: (settings: Settings) => void;
  onClose: () => void;
  onLibraryChanged: () => Promise<void>;
  onKeyStatus?: (status: KeyStatus) => void;
  initialTab?: "production" | "keys";
  claudeMode?: ClaudeCodeMode;
  onClaudeMode?: (mode: ClaudeCodeMode) => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const uid = useId();
  const { t, locale, setLocale } = useI18n();
  const [settings, setSettings] = useState<Settings>(() => {
    const m = structuredClone(bootstrap?.defaults || {});
    Object.entries(detail?.state.settings || {}).forEach(
      ([k, v]) => (m[k] = { ...m[k], ...v }),
    );
    return m;
  });
  const [tab, setTab] = useState<"production" | "keys" | "library">(
    initialTab ?? (detail && bootstrap ? "production" : "keys"),
  );
  const [libraryTarget, setLibraryTarget] = useState({ group: "writing_skills", id: "" });
  const [libraryDirty, setLibraryDirty] = useState(false);
  const savedSettings = useRef(JSON.stringify(settings));
  useEffect(() => {
    onDirtyChange?.(JSON.stringify(settings) !== savedSettings.current || libraryDirty);
    return () => onDirtyChange?.(false);
  }, [settings, libraryDirty, onDirtyChange]);
  const [libraryNotice, setLibraryNotice] = useState("");
  function leaveLibrary(next: () => void) {
    if (libraryDirty) setLibraryNotice("Save or discard your library edits before switching.");
    else { setLibraryNotice(""); next(); }
  }
  function manage(group: string, label: string) {
    const setting = librarySettings[group];
    return <button className="button small settings-manage" onClick={() => {
      setLibraryTarget({ group, id: setting ? String(settings[setting[0]]?.[setting[1]] || "") : "" });
      setTab("library");
    }}>{t(label)}</button>;
  }
  // Choosing another image service also swaps a model that only the previous service accepts.
  const modelFor: Record<string, string> = { image_server: "image_model", cover_image_server: "cover_image_model" };
  const set = (section: string, key: string, value: unknown) =>
    setSettings((s) => {
      const next = { ...s[section], [key]: value };
      const model = modelFor[key];
      if (model) next[model] = imageModelFor(String(value), String(next[model] ?? ""));
      return { ...s, [section]: next };
    });
  const modelWarning = (section: string, serverKey: string, modelKey: string) => {
    const server = String(settings[section]?.[serverKey] ?? "");
    const model = String(settings[section]?.[modelKey] ?? "");
    const choice = imageServiceChoices.find((c) => c.id === server);
    return choice && model && !imageModelFits(server, model) ? (
      <p className="settings-warning" role="alert">
        {t("{model} does not work with {provider}. Choose a {provider} model, such as {example}.", { model, provider: t(choice.label), example: choice.model })}
      </p>
    ) : null;
  };
  // Headings carry the step number shown in the sidebar.
  const step = (number: number, name: string) => <h3>{t("Step {number} · {name}", { number, name })}</h3>;
  const imageModel = (section: string, serverKey: string, modelKey: string, label: string) => <ModelSelect
    key={`${section}.${modelKey}:${settings[section]?.[serverKey]}`}
    label={t(label)} value={String(settings[section]?.[modelKey] ?? "")}
    options={imageModelChoices(String(settings[section]?.[serverKey] ?? ""))}
    onChange={(value) => set(section, modelKey, value)} />;
  // On a Claude subscription, Claude Code runs on the user's own login: agent providers are not used.
  const subscription = claudeMode === "subscription";
  const field = (
    section: string,
    key: string,
    label: string,
    options?: (string | LibraryChoice)[],
    disabled = false,
    hint?: string,
  ) => (
    <label className="field" key={`${section}.${key}`}>
      <span>{t(label)}</span>
      {options ? (
        <select
          disabled={disabled}
          value={String(settings[section]?.[key] ?? "")}
          onChange={(e) => set(section, key, e.target.value)}
        >
          {!options.some((o) => (typeof o === "string" ? o : o.id) === String(settings[section]?.[key] ?? "")) && Boolean(settings[section]?.[key]) && (
            <option value={String(settings[section][key])}>{t("Unavailable: {id}", { id: String(settings[section][key]) })}</option>
          )}
          {options.map((o) => (
            <option key={typeof o === "string" ? o : o.id} value={typeof o === "string" ? o : o.id}>
              {t(typeof o === "string" ? o || "None" : o.label)}
            </option>
          ))}
        </select>
      ) : (
        <input
          disabled={disabled}
          min={key === "seedream_max_concurrent_image_generation" ? 1 : undefined}
          max={key === "seedream_max_concurrent_image_generation" ? 5 : undefined}
          step={key === "seedream_max_concurrent_image_generation" ? 1 : undefined}
          value={String(settings[section]?.[key] ?? "")}
          type={
            typeof settings[section]?.[key] === "number" ? "number" : "text"
          }
          onChange={(e) =>
            set(
              section,
              key,
              key === "seedream_max_concurrent_image_generation"
                ? Math.min(5, Math.max(1, Math.trunc(Number(e.target.value)) || 1))
                : e.target.type === "number"
                ? Number(e.target.value)
                : e.target.value,
            )
          }
        />
      )}
      {hint && <small className="field-hint">{t(hint)}</small>}
    </label>
  );
  return (
    <div className="modal-backdrop">
      <section
        className={"modal settings-modal" + (tab === "library" ? " with-library" : "")}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${uid}-settings-title`}
      >
        <div className="modal-heading">
          <h2 id={`${uid}-settings-title`}>{t("Settings")}</h2>
          <button
            className="icon-button"
            aria-label={t("Close settings")}
            onClick={() => leaveLibrary(onClose)}
          >
            <X size={18} />
          </button>
        </div>
        <div className="settings-language">
          <div>
            <span id={`${uid}-interface-language-label`}>{t("Language")}</span>
            <p className="small-muted">
              {t(
                "Changes apply immediately and are remembered on this computer.",
              )}
            </p>
          </div>
          <div
            className="language-options"
            role="group"
            aria-label={t("Interface language")}
          >
            <button
              type="button"
              lang="en"
              aria-pressed={locale === "en"}
              onClick={() => setLocale("en")}
            >
              English
            </button>
            <button
              type="button"
              lang="zh-CN"
              aria-pressed={locale === "zh-CN"}
              onClick={() => setLocale("zh-CN")}
            >
              简体中文
            </button>
          </div>
        </div>
        <div
          className="settings-tabs"
          role="tablist"
          aria-label={t("Settings sections")}
        >
          <button
            id={`${uid}-production-settings-tab`}
            role="tab"
            aria-selected={tab === "production"}
            aria-controls={`${uid}-production-settings-panel`}
            disabled={!detail || !bootstrap}
            onClick={() => leaveLibrary(() => setTab("production"))}
          >
            {t("Production")}
          </button>
          <button
            id={`${uid}-api-keys-tab`}
            role="tab"
            aria-selected={tab === "keys"}
            aria-controls={`${uid}-api-keys-panel`}
            onClick={() => leaveLibrary(() => setTab("keys"))}
          >
            {t("API keys")}
          </button>
          <button role="tab" id={`${uid}-library-settings-tab`} aria-selected={tab === "library"}
            aria-controls={`${uid}-library-settings-panel`} disabled={!bootstrap}
            onClick={() => setTab("library")}>{t("Skills & templates")}</button>
        </div>
        {libraryNotice && <p className="library-message" role="status">{t(libraryNotice)}</p>}
        {tab === "library" && <div id={`${uid}-library-settings-panel`} role="tabpanel" aria-labelledby={`${uid}-library-settings-tab`}>
          <LibraryPanel embedded initialGroup={libraryTarget.group} initialId={libraryTarget.id}
            onDirtyChange={setLibraryDirty} onChanged={onLibraryChanged}
            onClose={() => leaveLibrary(() => setTab(detail ? "production" : "keys"))}
            onUse={detail ? (group, id) => {
              const [section, key] = librarySettings[group];
              set(section, key, id); setTab("production"); setLibraryNotice("");
            } : undefined} />
        </div>}
        {tab === "keys" && (
          <div
            id={`${uid}-api-keys-panel`}
            className="modal-content"
            role="tabpanel"
            aria-labelledby={`${uid}-api-keys-tab`}
          >
            {/* The keys this project needs follow the provider choices in this unsaved draft. */}
            <ApiKeysPanel settings={detail ? settings : null} motion={Boolean(detail?.segments.some((s) => s.visualizer === "hf"))} claudeMode={claudeMode} onClaudeMode={onClaudeMode} onStatus={onKeyStatus} />
          </div>
        )}
        {tab === "production" && detail && bootstrap && (
          <>
            <div
              id={`${uid}-production-settings-panel`}
              className="modal-content"
              role="tabpanel"
              aria-labelledby={`${uid}-production-settings-tab`}
            >
              <AgentConnection mode={claudeMode ?? "api_key"} onMode={onClaudeMode} disabled={Boolean(detail.state.agent_active)} />
              {step(1, "Writing")}
              <p className="small-muted settings-note">{t(subscription
                ? "Not used now: Writing runs in your own Claude Code on your Claude subscription. Change this in Settings → API keys."
                : "Claude Code writes the manuscript with this provider's API key. Changes apply to the next writing session.")}</p>
              <div className="form-grid">
                {field("step1", "llm_server", t("Writing agent provider"), languageModelChoices, subscription)}
                {field("step1", "llm_model", t("Writing agent model"), undefined, subscription)}
              </div>
              {step(2, "Segments")}
              <div className="form-grid">
                {field("step2", "llm_server", t("Agent provider"), languageModelChoices, settings.step2?.split_mode !== "agent",
                  "Used only by Agent (split by meaning).")}
                {field("step2", "llm_model", t("Agent model"), undefined, settings.step2?.split_mode !== "agent")}
              </div>
              {step(3, "Voice")}
              <VoicePicker value={String(settings.step3?.voice ?? "S_MKj17k802")} voices={bootstrap.voices ?? []}
                onChange={(voice) => set("step3", "voice", voice)} />
              {step(4, "Visuals")}
              {/* Visuals has three parts: the summary every visual draws on, still images, and motion clips. */}
              <section className="settings-part" aria-labelledby={`${uid}-settings-summary`}>
                <h4 id={`${uid}-settings-summary`}>{t("Content summary")}</h4>
                <p className="small-muted">{t("Written from the manuscript; every image and motion clip draws on it.")}</p>
                <div className="form-grid">
                  {field("step4_summary", "llm_server", t("Content summary provider"), languageModelChoices)}
                  {field("step4_summary", "llm_model", t("Content summary model"))}
                </div>
                <div className="settings-manage-row">
                  {manage("summary", "Edit content summary instructions")}
                </div>
              </section>
              <section className="settings-part" aria-labelledby={`${uid}-settings-images`}>
                <h4 id={`${uid}-settings-images`}>{t("Image generation")}</h4>
                <p className="small-muted">{t("Still images for segments, and the background of the opening.")}</p>
                <div className="form-grid">
                  {field("step4", "image_size", t("Frame size (images and clips)"))}
                  {field(
                    "step4",
                    "image_server",
                    t("Image provider"),
                    imageServiceChoices,
                  )}
                  {imageModel("step4", "image_server", "image_model", "Image model")}
                  {settings.step4?.image_server === "doubao" &&
                    field("step4", "seedream_max_concurrent_image_generation", t("Seedream images at once"), undefined, false, "Up to 5 concurrent image requests. Google always uses 1.")}
                  {field("motion_opening", "ip_name", t("Opening masthead"))}
                </div>
                {modelWarning("step4", "image_server", "image_model")}
              </section>
              <section className="settings-part" aria-labelledby={`${uid}-settings-motion`}>
                <h4 id={`${uid}-settings-motion`}>{t("Motion clips")}</h4>
                <p className="small-muted">{t(subscription
                  ? "Designed by your own Claude Code on your Claude subscription, then rendered by Grist. The agent provider below is not used now."
                  : "Designed by Claude Code with HTML / Three.js, then rendered by Grist.")}</p>
                <div className="form-grid">
                  {field(
                    "step4",
                    "motion_style_preset",
                    t("Animation style"),
                    libraryChoices(bootstrap.library, "motion_styles"),
                  )}
                  {field("step4", "llm_server", t("Motion agent provider"), languageModelChoices, subscription)}
                  {field("step4", "llm_model", t("Motion agent model"), undefined, subscription)}
                </div>
                <div className="settings-manage-row">
                  {manage("motion_styles", "Edit motion styles")}
                </div>
              </section>
              {step(5, "Assembly")}
              <p className="small-muted settings-note">{t("Music, export size, subtitles, narration speed and the 3:4 copy are set on the Assembly page.")}</p>
              <div className="form-grid">
                {field("step5", "video_codec", t("Codec"), ["h264", "hevc"], false,
                  "H.264 plays everywhere; HEVC makes smaller files.")}
              </div>
              {step(6, "Covers")}
              <p className="small-muted settings-note">{t("Choose Image API or Claude Code + HTML / Three.js on the Covers page. The cover agent uses Writing's provider and the same Claude Code login.")}</p>
              {settings.step6?.cover_method !== "agent" && <>
              <div className="form-grid">
                {field(
                  "step6",
                  "cover_image_server",
                  t("Cover provider"),
                  imageServiceChoices,
                )}
                {imageModel("step6", "cover_image_server", "cover_image_model", "Cover model ID")}
              </div>
              {modelWarning("step6", "cover_image_server", "cover_image_model")}
              </>}
              <p className="small-muted">
                {t(
                  "Manage provider credentials in the API keys tab. Each job uses a saved copy of these production settings.",
                )}
              </p>
            </div>
          </>
        )}
        {tab === "keys" && (
          <div className="modal-actions">
            <button className="button" onClick={onClose}>
              {t("Done")}
            </button>
          </div>
        )}
        {tab === "production" && detail && bootstrap && (
          <div className="modal-actions">
            <button className="button" onClick={onClose}>
              {t("Cancel")}
            </button>
            <button
              className="button primary"
              onClick={() => onSave(settings)}
            >
              {t("Save settings")}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
