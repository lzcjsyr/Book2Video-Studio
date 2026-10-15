import { useEffect, useState } from "react";
import { useI18n } from "../i18n";
import type { Settings } from "../types";

type Props = {
  settings: Settings[string];
  disabled: boolean;
  onChange: (patch: Record<string, unknown>) => Promise<boolean>;
};

/** Keep intermediate numeric input local; persist a valid value on blur or Enter. */
function NumberSetting({ label, value, min, max, disabled, onSave }: {
  label: string; value: number; min: number; max?: number; disabled: boolean;
  onSave: (value: number) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  return <label className="field"><span>{label}</span>
    <input type="number" aria-label={label} value={draft} min={min} max={max} step={1} disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
      onBlur={async (e) => {
        const next = e.currentTarget.valueAsNumber;
        if (!e.currentTarget.validity.valid || !Number.isInteger(next)) { setDraft(String(value)); return; }
        if (next !== value && !await onSave(next)) setDraft(String(value));
      }} />
  </label>;
}

export function SegmentationControls({ settings, disabled, onChange }: Props) {
  const { t } = useI18n();
  const mode = String(settings.split_mode || "punctuation") === "auto" ? "punctuation" : String(settings.split_mode || "punctuation");
  const hints: Record<string, string> = {
    punctuation: "Each period, question mark, exclamation mark or semicolon ends a segment, in English or Chinese. Decimal numbers stay together.",
    manual: "Each manuscript line becomes a segment. Lines ending in 【motion】 become motion segments.",
    agent: "An agent groups sentences by meaning and chooses image or motion for each segment.",
  };
  return <section className="step-controls segmentation-controls" aria-label={t("Segmentation controls")}>
    <div className="step-controls-fields">
      <label className="field"><span>{t("Split method")}</span>
        <select aria-label={t("Split method")} value={mode} disabled={disabled}
          onChange={(e) => onChange({ split_mode: e.target.value })}>
          <option value="punctuation">{t("Split by punctuation")}</option>
          <option value="manual">{t("One segment per line")}</option>
          <option value="agent">{t("Agent (split by meaning)")}</option>
        </select>
      </label>
      {mode === "agent" && <NumberSetting label={t("Target segments")} value={Number(settings.num_segments ?? 50)} min={1}
        disabled={disabled} onSave={(num_segments) => onChange({ num_segments })} />}
    </div>
    <p className="small-muted">{t(hints[mode] || hints.punctuation)}</p>
    <p className="small-muted">{t("Saved automatically. Changes apply the next time you generate segments.")}</p>
  </section>;
}

export function VoiceControls({ settings, disabled, onChange }: Props) {
  const { t } = useI18n();
  const emotion = String(settings.emotion || "neutral");
  const model = String(settings.tts_model || "seed-tts-2.0-standard");
  return <section className="step-controls voice-controls" aria-label={t("Voice delivery")}>
    <h3>{t("Voice delivery")}</h3>
    <div className="step-controls-fields">
      <label className="field"><span>{t("TTS model")}</span>
        <select aria-label={t("TTS model")} value={model} title={model} disabled={disabled}
          onChange={(e) => onChange({ tts_model: e.target.value })}>
          {!["seed-tts-2.0-standard", "seed-tts-2.0-expressive"].includes(model) && <option value={model}>{model}</option>}
          <option value="seed-tts-2.0-standard">{t("Standard · seed-tts-2.0-standard")}</option>
          <option value="seed-tts-2.0-expressive">{t("Expressive · seed-tts-2.0-expressive")}</option>
        </select>
      </label>
      <NumberSetting label={t("Speaking rate")} value={Number(settings.speech_rate ?? 20)} min={-50} max={100}
        disabled={disabled} onSave={(speech_rate) => onChange({ speech_rate })} />
      <label className="field"><span>{t("Emotion")}</span>
        <select aria-label={t("Emotion")} value={emotion} disabled={disabled}
          onChange={(e) => onChange({ emotion: e.target.value })}>
          {!["neutral", "happy", "sad"].includes(emotion) && <option value={emotion}>{emotion}</option>}
          <option value="neutral">{t("neutral")}</option>
          <option value="happy">{t("happy")}</option>
          <option value="sad">{t("sad")}</option>
        </select>
      </label>
    </div>
    <p className="small-muted">{t("Speaking rate: −50 to 100; 0 is normal speed.")}</p>
    <p className="small-muted">{t("Saved automatically. Changes apply to new recordings.")}</p>
  </section>;
}
