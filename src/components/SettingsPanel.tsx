import { useI18n } from "../i18n";
import { useState } from "react";
import { X } from "lucide-react";
import type { Bootstrap, Detail, Settings, LibraryChoice } from "../types";
import ApiKeysPanel from "./ApiKeysPanel";
import LibraryPanel from "./LibraryPanel";
import { libraryChoices, librarySettings } from "../library";

const imageProviders = ["doubao", "google", "google_adc"];

export default function SettingsPanel({
  detail,
  bootstrap,
  onSave,
  onClose,
  onLibraryChanged,
}: {
  detail: Detail | null;
  bootstrap: Bootstrap | null;
  onSave: (settings: Settings) => void;
  onClose: () => void;
  onLibraryChanged: () => Promise<void>;
}) {
  const { t, locale, setLocale } = useI18n();
  const [settings, setSettings] = useState<Settings>(() => {
    const m = structuredClone(bootstrap?.defaults || {});
    Object.entries(detail?.state.settings || {}).forEach(
      ([k, v]) => (m[k] = { ...m[k], ...v }),
    );
    return m;
  });
  const [tab, setTab] = useState<"production" | "keys" | "library">(
    detail && bootstrap ? "production" : "keys",
  );
  const [libraryTarget, setLibraryTarget] = useState({ group: "writing_skills", id: "" });
  const [libraryDirty, setLibraryDirty] = useState(false);
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
  const set = (section: string, key: string, value: unknown) =>
    setSettings((s) => ({ ...s, [section]: { ...s[section], [key]: value } }));
  const field = (
    section: string,
    key: string,
    label: string,
    options?: (string | LibraryChoice)[],
  ) => (
    <label className="field" key={`${section}.${key}`}>
      <span>{t(label)}</span>
      {options ? (
        <select
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
          value={String(settings[section]?.[key] ?? "")}
          type={
            typeof settings[section]?.[key] === "number" ? "number" : "text"
          }
          onChange={(e) =>
            set(
              section,
              key,
              e.target.type === "number"
                ? Number(e.target.value)
                : e.target.value,
            )
          }
        />
      )}
    </label>
  );
  return (
    <div className="modal-backdrop">
      <section
        className={"modal settings-modal" + (tab === "library" ? " with-library" : "")}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
      >
        <div className="modal-heading">
          <h2 id="settings-title">{t("Settings")}</h2>
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
            <span id="interface-language-label">{t("Language")}</span>
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
            id="production-settings-tab"
            role="tab"
            aria-selected={tab === "production"}
            aria-controls="production-settings-panel"
            disabled={!detail || !bootstrap}
            onClick={() => leaveLibrary(() => setTab("production"))}
          >
            {t("Production")}
          </button>
          <button
            id="api-keys-tab"
            role="tab"
            aria-selected={tab === "keys"}
            aria-controls="api-keys-panel"
            onClick={() => leaveLibrary(() => setTab("keys"))}
          >
            {t("API keys")}
          </button>
          <button role="tab" id="library-settings-tab" aria-selected={tab === "library"}
            aria-controls="library-settings-panel" disabled={!bootstrap}
            onClick={() => setTab("library")}>{t("Skills & templates")}</button>
        </div>
        {libraryNotice && <p className="library-message" role="status">{t(libraryNotice)}</p>}
        {tab === "library" && <div id="library-settings-panel" role="tabpanel" aria-labelledby="library-settings-tab">
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
            id="api-keys-panel"
            className="modal-content"
            role="tabpanel"
            aria-labelledby="api-keys-tab"
          >
            <ApiKeysPanel />
          </div>
        )}
        {tab === "production" && detail && bootstrap && (
          <>
            <div
              id="production-settings-panel"
              className="modal-content"
              role="tabpanel"
              aria-labelledby="production-settings-tab"
            >
              <h3>{t("Segmentation")}</h3>
              <div className="form-grid">
                {field("step1_5", "num_segments", t("Target segments"))}
                {field("step1_5", "split_mode", t("Segmentation"), [
                  "auto",
                  "manual",
                  "agent",
                ])}
              </div>
              <h3>{t("Voice")}</h3>
              <div className="form-grid">
                {field("step3", "voice", t("Voice ID"))}
                {field("step3", "tts_model", t("TTS model"))}
                {field("step3", "speech_rate", t("Speaking rate"))}
                {field("step3", "emotion", t("Emotion"), [
                  "neutral",
                  "happy",
                  "sad",
                ])}
              </div>
              <h3>{t("Visuals")}</h3>
              <div className="form-grid">
                {field("step4", "image_size", t("Image dimensions"))}
                {field("step4", "image_model", t("Image model"))}
                {field(
                  "step4",
                  "image_server",
                  t("Image provider"),
                  imageProviders,
                )}
                {field("step4", "image_style_preset", t("Image style"), libraryChoices(bootstrap.library, "image_styles"))}
                {field("step4", "image_prompt_template", t("Image prompt"), libraryChoices(bootstrap.library, "image_templates"))}
                {field(
                  "step4",
                  "hyperframes_style_preset",
                  t("Animation style"),
                  libraryChoices(bootstrap.library, "motion_styles"),
                )}
                {field("step4", "llm_server", t("Visual agent provider"))}
                {field("step4", "llm_model", t("Visual agent model"))}
                {field("step2", "llm_server", t("Visual direction provider"))}
                {field("step2", "llm_model", t("Visual direction model"))}
              </div>
              <div className="settings-manage-row">
                {manage("summary", "Edit visual direction instructions")}
                {manage("image_styles", "Edit image styles")}
                {manage("image_templates", "Edit image templates")}
                {manage("motion_styles", "Edit motion styles")}
              </div>
              <h3>{t("Assembly")}</h3>
              <div className="form-grid">
                {field("step5", "video_size", t("Export dimensions"))}
                {field("step5", "video_codec", t("Codec"), ["h264", "hevc"])}
                {field("step5", "bgm_filename", t("Background music"), [
                  "",
                  ...bootstrap.music,
                ])}
                {field("step5", "bgm_default_volume", t("Music volume"))}
                {field(
                  "step5",
                  "narration_speed_factor",
                  t("Narration playback speed"),
                )}
              </div>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={Boolean(settings.step5?.enable_subtitles)}
                  onChange={(e) =>
                    set("step5", "enable_subtitles", e.target.checked)
                  }
                />{" "}
                {t("Burn subtitles into exports")}
              </label>
              <h3>{t("Covers")}</h3>
              <div className="form-grid">
                {field(
                  "step6",
                  "default_cover_image_size_key",
                  t("Cover aspect ratio"),
                  ["16:9", "9:16", "4:3", "3:4", "1:1"],
                )}
                {field("step6", "cover_image_style", t("Cover style"), libraryChoices(bootstrap.library, "cover_styles"))}
                {field("step6", "cover_image_count", t("Cover candidates"))}
                {field(
                  "step6",
                  "cover_image_server",
                  t("Cover provider"),
                  imageProviders,
                )}
                {field("step6", "cover_image_model", t("Cover model"))}
              </div>
              {manage("cover_styles", "Edit cover styles")}
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
