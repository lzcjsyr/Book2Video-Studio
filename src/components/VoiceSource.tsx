import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, projectRoute } from "../api";
import { useI18n } from "../i18n";
import type { Detail, SavedVoice } from "../types";

type InstallStatus = {
  state: "unsupported" | "available" | "downloading" | "installing" | "checking" | "ready" | "failed";
  model_bytes: number;
  percent?: number;
  error?: string;
};

const installLabels = {
  downloading: "Downloading VoxCPM2…",
  installing: "Installing voice components…",
  checking: "Checking VoxCPM2…",
} as const;

function LocalVoice({ detail, disabled, onReference }: {
  detail: Detail;
  disabled: boolean;
  onReference: (detail: Detail) => void;
}) {
  const { t } = useI18n();
  const fileInput = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<InstallStatus>();
  const [installing, setInstalling] = useState(false);
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const next = await api<InstallStatus>("/local-voice");
        if (active) setStatus(next);
      } catch (error) {
        if (active) setNotice((error as Error).message);
      } finally {
        if (active) timer = setTimeout(refresh, 1500);
      }
    }
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, []);

  async function install() {
    setInstalling(true);
    setNotice("");
    try {
      setStatus(await api<InstallStatus>("/local-voice", {}));
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setInstalling(false);
    }
  }

  async function importReference(file: File) {
    setImporting(true);
    setNotice("");
    try {
      const updated = await api<Detail>(projectRoute(detail.id) + "/voice-reference?name=" + encodeURIComponent(file.name), file);
      onReference(updated);
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setImporting(false);
    }
  }

  const phase = status?.state;
  const phaseLabel = phase === "downloading" || phase === "installing" || phase === "checking" ? installLabels[phase] : "";
  const message = notice || status?.error;
  const percent = status?.percent ?? 0;
  return <div className="voice-source-fields">
    <div className="field">
      <span>{t("Reference recording")}</span>
      <input type="file" ref={fileInput} hidden aria-label={t("Reference recording")}
        accept=".wav,.mp3,.m4a,.aac,.flac,.aif,.aiff,.ogg,.opus,.webm"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          event.currentTarget.value = "";
          if (file) void importReference(file);
        }} />
      <button type="button" className="button" disabled={disabled || importing} onClick={() => fileInput.current?.click()}>
        {t(importing ? "Importing recording…" : detail.voice_reference ? "Change reference recording" : "Choose reference recording")}
      </button>
      {detail.voice_reference && <span className="field-hint voice-reference-name" title={detail.voice_reference}>{detail.voice_reference}</span>}
      <small className="field-hint">{t("Choose a short recording of one speaker. It is stored with this project.")}</small>
    </div>
    <div className="field" aria-live="polite">
      {!status && <small className="field-hint">{t("Checking VoxCPM2…")}</small>}
      {phase === "ready" && <small className="field-hint">{t("VoxCPM2 is installed and ready for offline generation.")}</small>}
      {phaseLabel && <small className="field-hint">{t(phaseLabel)} {phase === "downloading" && `${percent}%`}</small>}
      {phase === "downloading" && <progress aria-label={t("Downloading VoxCPM2…")} max={100} value={percent} />}
      {status && (phase === "available" || phase === "failed") && <>
        <button type="button" className="button" disabled={disabled || installing} onClick={install}>
          {t(phase === "failed" ? "Retry VoxCPM2 installation" : "Install VoxCPM2")}
        </button>
        <small className="field-hint">{t("Downloads {size} GB of model files plus the runtime.", { size: (status.model_bytes / 1e9).toFixed(2) })}</small>
      </>}
      {message && <p className="library-message" role="status">{t(message)}</p>}
    </div>
  </div>;
}

export function VoiceSource({ settings, detail, disabled = false, onChange, onReference, children }: {
  settings: Record<string, unknown>;
  detail: Detail;
  disabled?: boolean;
  onChange: (provider: string) => void;
  onReference: (detail: Detail) => void;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const local = settings.tts_provider === "voxcpm2";
  return <div className="voice-source-fields">
    <label className="field">
      <span>{t("Voice source")}</span>
      <select value={local ? "voxcpm2" : "bytedance"} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
        <option value="bytedance">{t("Volcengine TTS")}</option>
        <option value="voxcpm2">{t("Local clone · VoxCPM2")}</option>
      </select>
    </label>
    {local ? <LocalVoice key={detail.id} detail={detail} disabled={disabled} onReference={onReference} /> : children}
  </div>;
}

export function SavedVoiceSelect({ value, voices, disabled, onChange }: {
  value: string;
  voices?: SavedVoice[];
  disabled: boolean;
  onChange: (voice: string) => void;
}) {
  const { t } = useI18n();
  return <label className="field">
    <span>{t("Voice ID")}</span>
    <select value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
      {!voices?.some((voice) => voice.id === value) && <option value={value}>{value || t("Default voice")}</option>}
      {voices?.map((voice) => <option key={voice.id} value={voice.id}>{voice.name} · {voice.id}</option>)}
    </select>
    <small className="field-hint">{t("Add voices in Settings → Production.")}</small>
  </label>;
}
