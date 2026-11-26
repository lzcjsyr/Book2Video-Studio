import { useState } from "react";
import { useI18n } from "../i18n";
import { api } from "../api";
import type { SavedVoice } from "../types";

export default function VoicePicker({ value, voices, onChange }: {
  value: string;
  voices: SavedVoice[];
  onChange: (id: string) => void;
}) {
  const { t } = useI18n();
  const [nameDraft, setNameDraft] = useState<{ id: string; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const selected = voices.find((voice) => voice.id === value);
  const name = nameDraft?.id === value ? nameDraft.name : selected?.name ?? "";
  async function save(remove = false) {
    setBusy(true); setNotice("");
    try {
      const result = await api<{ voices: SavedVoice[]; fallback?: string }>(remove ? "/voices/delete" : "/voices", { id: value, name });
      if (remove && result.fallback) onChange(result.fallback);
      setNameDraft(null);
      window.dispatchEvent(new CustomEvent("grist-voices-changed", { detail: result.voices }));
      setNotice(remove ? "Voice deleted." : "Voice saved. It is available in every project on this computer.");
    } catch (error) { setNotice(String(error)); }
    finally { setBusy(false); }
  }
  return <section className="settings-part saved-voices" aria-label={t("Voice library")}>
    <div className="form-grid">
      <label className="field">
        <span>{t("Saved voices")}</span>
        <select aria-label={t("Saved voices")} value={selected?.id ?? ""} disabled={busy} onChange={(event) => {
          if (event.target.value) { onChange(event.target.value); setNameDraft(null); setNotice(""); }
        }}>
          <option value="">{t("Enter a voice ID")}</option>
          {voices.map((voice) => <option key={voice.id} value={voice.id}>{`${voice.name} · ${voice.id}`}</option>)}
        </select>
      </label>
      <label className="field">
        <span>{t("Voice ID")}</span>
        <input aria-label={t("Voice ID")} value={value} disabled={busy} onChange={(event) => {
          onChange(event.target.value); setNameDraft(null); setNotice("");
        }} />
      </label>
      <label className="field">
        <span>{t("Voice name")}</span>
        <input aria-label={t("Voice name")} value={name} disabled={busy} maxLength={80}
          placeholder={t("For example: narrator or speaker name")} onChange={(event) => {
            setNameDraft({ id: value, name: event.target.value }); setNotice("");
          }} />
      </label>
      <div className="voice-save-action">
        <button type="button" className="button" disabled={busy || !value.trim() || !name.trim()} onClick={() => save()}>
          {t(busy ? "Saving…" : "Save voice")}
        </button>
        {selected && <button type="button" className="button danger" disabled={busy} onClick={() => save(true)}>
          {t("Delete voice")}
        </button>}
      </div>
    </div>
    <p className="small-muted">{t("Save settings to use the selected voice across projects.")}</p>
    {notice && <p className="library-message" role="status">{t(notice)}</p>}
  </section>;
}
