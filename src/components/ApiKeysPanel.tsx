import { useI18n } from "../i18n";
import { useEffect, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { KeyRound, ShieldCheck } from "lucide-react";

const providers = [
  {
    name: "OpenRouter",
    key: "OPENROUTER_API_KEY",
    usage: "Writing and visual agents",
  },
  {
    name: "DeepSeek",
    key: "DEEPSEEK_API_KEY",
    usage: "Writing and visual agents",
  },
  {
    name: "Xiaomi MiMo",
    key: "MIMO_API_KEY",
    usage: "Writing and visual agents",
  },
  {
    name: "Kimi / Moonshot",
    key: "KIMI_API_KEY",
    usage: "Writing and visual agents",
  },
  {
    name: "SiliconFlow",
    key: "SILICONFLOW_KEY",
    usage: "Writing and visual agents",
  },
  {
    name: "Volcengine / Doubao",
    key: "VOLCENGINE_API_KEY",
    usage: "Text, Seedream images, and covers",
  },
  {
    name: "Google",
    key: "GOOGLE_CLOUD_API_KEY",
    usage: "Images and covers with API key authentication",
  },
  {
    name: "ByteDance TTS",
    key: "BYTEDANCE_TTS_API_KEY",
    usage: "Voice generation",
  },
];
type KeyStatus = { saved: Record<string, boolean>; applied: boolean };

export default function ApiKeysPanel() {
  const { t } = useI18n();
  const native = isTauri();
  const [status, setStatus] = useState<KeyStatus | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const load = async () => {
    setError("");
    try {
      setStatus(await invoke<KeyStatus>("credential_status"));
    } catch (e) {
      setError(String(e));
    }
  };
  useEffect(() => {
    if (native) void load();
  }, [native]);

  const save = async (key: string, value: string | null) => {
    setBusy(key);
    setError("");
    setMessage("");
    try {
      const result = await invoke<KeyStatus>("credential_save", {
        name: key,
        value,
      });
      setStatus(result);
      setDrafts((d) => ({ ...d, [key]: "" }));
      const name = providers.find((p) => p.key === key)!.name;
      setMessage(
        `${name} key ${value === null ? "removed" : "saved"}. ${result.applied ? "New jobs and new Claude Code sessions use the updated keys." : "Keys will apply when you connect or reconnect the runtime."}`,
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="api-keys-panel">
      <div className="credential-intro">
        <ShieldCheck size={22} />
        <div>
          <strong>{t("API keys for this computer")}</strong>
          <p>
            {t(
              "Shared across projects. Stored securely in macOS Keychain or Windows Credential Manager. Saved keys stay hidden; enter a new key to replace one.",
            )}
          </p>
        </div>
      </div>
      <p className="small-muted">
        {t(
          "New jobs and new Claude Code sessions use the saved keys. Running jobs and terminal sessions keep their current keys.",
        )}
      </p>
      {!native && (
        <p className="credential-notice">
          {t("Open the Tauri desktop app to save API keys.")}
        </p>
      )}
      {error && (
        <div className="credential-error" role="alert">
          {t(error)}{" "}
          <button
            className="button"
            disabled={!!busy}
            onClick={() => void load()}
          >
            {t("Retry")}
          </button>
        </div>
      )}
      <p className="credential-message" role="status">
        {t(message) ||
          (native && !status && !error ? t("Checking saved keys…") : "")}
      </p>
      <div className="credential-list">
        {providers.map((provider) => (
          <form
            className="credential-row"
            key={provider.key}
            onSubmit={(e) => {
              e.preventDefault();
              void save(provider.key, drafts[provider.key] || "");
            }}
          >
            <div className="credential-heading">
              <label htmlFor={`key-${provider.key}`}>
                <KeyRound size={13} />
                {t(provider.name)}
              </label>
              <span
                className={
                  status?.saved[provider.key] ? "key-saved" : "small-muted"
                }
              >
                {!status
                  ? ""
                  : status.saved[provider.key]
                    ? t("Saved securely")
                    : t("Not saved in app")}
              </span>
            </div>
            <p className="small-muted">{t(provider.usage)}</p>
            <div className="credential-input">
              <input
                id={`key-${provider.key}`}
                aria-label={t("{provider} API key", {
                  provider: provider.name,
                })}
                type="password"
                autoComplete="new-password"
                spellCheck={false}
                autoCapitalize="none"
                disabled={!native || !status || !!busy}
                value={drafts[provider.key] || ""}
                placeholder={
                  status?.saved[provider.key]
                    ? t("Saved • enter a replacement key")
                    : t("Enter API key")
                }
                onChange={(e) =>
                  setDrafts((d) => ({ ...d, [provider.key]: e.target.value }))
                }
              />
              <button
                className="button primary"
                type="submit"
                disabled={
                  !native || !status || !!busy || !drafts[provider.key]?.trim()
                }
              >
                {busy === provider.key ? t("Saving…") : t("Save key")}
              </button>
              <button
                className="button"
                type="button"
                aria-label={t("Remove {provider} API key", {
                  provider: provider.name,
                })}
                disabled={!status?.saved[provider.key] || !!busy}
                onClick={() => void save(provider.key, null)}
              >
                {t("Remove")}
              </button>
            </div>
          </form>
        ))}
      </div>
      <p className="small-muted credential-footnote">
        {t(
          "Only enter keys for providers you use. Google ADC uses your existing Google Cloud login and does not require an API key.",
        )}
      </p>
    </div>
  );
}
