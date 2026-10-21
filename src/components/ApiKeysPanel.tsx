import { useI18n } from "../i18n";
import { useId, useEffect, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { AlertTriangle, CheckCircle2, Cloud, Image, KeyRound, MessageSquareText, ShieldCheck, TerminalSquare, Volume2, XCircle } from "lucide-react";
import { api } from "../api";
import type { ClaudeCodeMode, ClaudeCodeStatus, Settings } from "../types";
import { adcModels, distinctGaps, keyNeeds, keyProviders, providerName, unmet, type KeyGroup, type KeyNeed } from "../keys";

export type KeyStatus = { saved: Record<string, boolean>; applied: boolean };
type AdcCheck = { ok?: boolean; checked?: string; project?: string; location?: string; message?: string; models?: { model: string; ok: boolean; message?: string }[] };

const claudeModes: { id: ClaudeCodeMode; title: string; description: string }[] = [
  { id: "api_key", title: "Built-in Claude Code · API key",
    description: "Grist's own Claude Code, connected to the language-model providers chosen for Writing, Motion, Assembly and HTML covers. Uses the keys below." },
  { id: "subscription", title: "Your Claude Code · subscription",
    description: "The Claude Code installed on this computer, signed in with your Claude account. Your Claude plan covers Writing, Motion, Assembly and HTML cover sessions; they need no key." },
];

/** Which Claude Code runs the Writing and Motion sessions, and whether the installed one is signed in. */
function ClaudeCodeSection({ mode, onMode }: { mode: ClaudeCodeMode; onMode?: (mode: ClaudeCodeMode) => void }) {
  const uid = useId();
  const { t } = useI18n();
  const native = isTauri();
  const [local, setLocal] = useState<ClaudeCodeStatus["local"]>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const check = async () => {
    setChecking(true);
    setError("");
    try {
      setLocal((await invoke<ClaudeCodeStatus>("claude_code_status", { check: true })).local);
    } catch (e) {
      setError(String(e));
    } finally {
      setChecking(false);
    }
  };
  useEffect(() => {
    if (native && mode === "subscription" && !local) void check();
  }, [native, mode]);
  const choose = async (next: ClaudeCodeMode) => {
    setError("");
    try {
      await invoke<ClaudeCodeStatus>("claude_code_set_mode", { mode: next });
      onMode?.(next);
    } catch (e) {
      setError(String(e));
    }
  };
  return (
    <section className="credential-group claude-code-modes" aria-labelledby={`${uid}-claude-code-title`}>
      <h4 id={`${uid}-claude-code-title`}><TerminalSquare size={15} /> {t("Claude Code")}</h4>
      <p className="small-muted">{t("Runs the interactive Writing, Motion, Assembly and HTML cover sessions.")}</p>
      <div className="mode-options" role="radiogroup" aria-labelledby={`${uid}-claude-code-title`}>
        {claudeModes.map((option) => (
          <label key={option.id} className={`mode-option ${mode === option.id ? "selected" : ""}`}>
            <input type="radio" name={`${uid}-claude-code-mode`} checked={mode === option.id} disabled={!native}
              onChange={() => void choose(option.id)} />
            <span>
              <strong>{t(option.title)}</strong>
              <span className="small-muted">{t(option.description)}</span>
            </span>
          </label>
        ))}
      </div>
      {mode === "subscription" && (
        <div className="claude-login" aria-live="polite">
          {checking ? (
            <span className="small-muted">{t("Looking for Claude Code on this computer…")}</span>
          ) : local?.error ? (
            <span className="key-missing"><XCircle size={13} /> {t(local.error)}</span>
          ) : local?.logged_in ? (
            <span className="key-ok">
              <CheckCircle2 size={13} />{" "}
              {t("Signed in{account}{plan}", {
                account: local.email ? " · " + local.email : "",
                plan: local.subscription ? " · " + t("{plan} plan", { plan: local.subscription.replace(/^\w/, (c) => c.toUpperCase()) }) : "",
              })}
            </span>
          ) : local ? (
            <span className="key-missing"><AlertTriangle size={13} /> {t("Not signed in. Run `claude` in Terminal and sign in, then check again.")}</span>
          ) : null}
          {local?.path && !checking && <span className="small-muted context-path">{local.version} · {local.path}</span>}
          <div className="adc-check">
            <button className="button" type="button" disabled={!native || checking} onClick={() => void check()}>
              {checking ? t("Checking…") : t("Check again")}
            </button>
            <span className="small-muted">{t("Agent segmentation still uses an API key.")}</span>
          </div>
        </div>
      )}
      {error && <p className="credential-error" role="alert">{t(error)}</p>}
    </section>
  );
}

/** Google ADC has no key to save; its login, project and models are checked against Google instead. */
function GoogleLoginCheck({ settings, needs }: { settings?: Settings | null; needs: KeyNeed[] }) {
  const { t, locale } = useI18n();
  const [last, setLast] = useState<AdcCheck>({});
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api<AdcCheck>("/google-adc").then(setLast).catch(() => {});
  }, []);
  const uses = needs.filter((n) => n.provider === "google_adc").map((n) => t(n.use));
  const models = settings ? adcModels(settings) : [];
  const check = async () => {
    setChecking(true);
    setError("");
    try {
      setLast(await api<AdcCheck>("/google-adc", { models }));
    } catch (e) {
      setError(String(e));
    } finally {
      setChecking(false);
    }
  };
  const state = !last.checked ? "" : last.ok ? t("Connected") : t("Not connected");
  return (
    <div className={`credential-row adc-row ${uses.length && last.checked && !last.ok ? "needed" : ""}`}>
      <div className="credential-heading">
        <label><Cloud size={13} /> {t("Google Cloud login (ADC)")}</label>
        <span className={last.ok ? "key-saved" : last.checked ? "key-missing" : "small-muted"}>{state || t("Not checked")}</span>
      </div>
      <p className="small-muted">
        {t("No key needed: uses the Google Cloud login on this computer.")}{" "}
        {settings && (uses.length ? t("This project uses it for: {uses}", { uses: uses.join(t(", ")) }) : t("Not used by this project's current settings."))}
      </p>
      <div className="adc-check">
        <button className="button" type="button" disabled={checking} onClick={() => void check()}>
          {checking ? t("Checking…") : t("Check connection")}
        </button>
        <span className="small-muted">
          {last.checked ? t("Last checked {time}", { time: new Date(last.checked).toLocaleString(locale) }) : t("Never checked")}
        </span>
      </div>
      {error && <p className="credential-error" role="alert">{t(error)}</p>}
      {last.checked && (
        <ul className="adc-result" aria-live="polite">
          {last.project && <li><CheckCircle2 size={13} /> {t("Signed in · project {project} ({location})", { project: last.project, location: last.location || "global" })}</li>}
          {last.models?.map((m) => (
            <li key={m.model} className={m.ok ? "" : "failed"}>
              {m.ok ? <CheckCircle2 size={13} /> : <XCircle size={13} />} {m.model}: {m.ok ? t("available") : t(m.message || "")}
            </li>
          ))}
          {!last.ok && last.message && !last.models?.length && <li className="failed"><XCircle size={13} /> {t(last.message)}</li>}
        </ul>
      )}
    </div>
  );
}

const groups: { id: KeyGroup; title: string; description: string; icon: typeof Image }[] = [
  { id: "text", title: "Language models", icon: MessageSquareText,
    description: "Write the manuscript, plan the visuals and drive Claude Code for motion segments. Each step uses the provider chosen in its settings; one provider can serve them all." },
  { id: "image", title: "Image generation", icon: Image,
    description: "Segment images, the opening background and covers. Google ADC uses your Google Cloud login instead of a key." },
  { id: "voice", title: "Voice", icon: Volume2, description: "Narration for every segment and the opening quote." },
];

/** Saved provider keys, grouped by what they do, against what the project's settings need. */
export default function ApiKeysPanel({ settings, motion = false, claudeMode = "api_key", onClaudeMode, onStatus }: {
  settings?: Settings | null;
  motion?: boolean;
  claudeMode?: ClaudeCodeMode;
  onClaudeMode?: (mode: ClaudeCodeMode) => void;
  onStatus?: (status: KeyStatus) => void;
}) {
  const uid = useId();
  const { t } = useI18n();
  const native = isTauri();
  const [status, setStatus] = useState<KeyStatus | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const update = (next: KeyStatus) => {
    setStatus(next);
    onStatus?.(next);
  };
  const load = async () => {
    setError("");
    try {
      update(await invoke<KeyStatus>("credential_status"));
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
      const result = await invoke<KeyStatus>("credential_save", { name: key, value });
      update(result);
      setDrafts((d) => ({ ...d, [key]: "" }));
      setMessage(t(value === null ? "{provider} key removed." : "{provider} key saved.", { provider: providerName(key) }) + " " +
        t(result.applied ? "New jobs and new Claude Code sessions use the updated keys." : "Keys will apply when the production service starts."));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const needs = settings ? keyNeeds(settings, { motion, subscription: claudeMode === "subscription" }) : [];
  const missing = unmet(needs, status?.saved);
  const usesOf = (key: string) => needs.filter((n) => n.key === key).map((n) => t(n.use));

  return (
    <div className="api-keys-panel">
      <div className="credential-intro">
        <ShieldCheck size={22} />
        <div>
          <strong>{t("API keys for this computer")}</strong>
          <p>
            {t("Shared across projects. Stored securely in macOS Keychain or Windows Credential Manager. Saved keys stay hidden; enter a new key to replace one.")}
          </p>
        </div>
      </div>
      {!native && <p className="credential-notice">{t("Open the Tauri desktop app to save API keys.")}</p>}
      {error && (
        <div className="credential-error" role="alert">
          {t(error)}{" "}
          <button className="button" disabled={!!busy} onClick={() => void load()}>{t("Retry")}</button>
        </div>
      )}
      <ClaudeCodeSection mode={claudeMode} onMode={onClaudeMode} />
      {settings && status && (
        <section className={`key-needs ${missing.length ? "incomplete" : "complete"}`} aria-labelledby={`${uid}-key-needs-title`}>
          <h4 id={`${uid}-key-needs-title`}>
            {missing.length ? <AlertTriangle size={15} /> : <CheckCircle2 size={15} />}
            {missing.length
              ? distinctGaps(missing) === 1 ? t("1 key missing for this project") : t("{count} keys missing for this project", { count: distinctGaps(missing) })
              : t("This project has every key it needs")}
          </h4>
          <p className="small-muted">{t("Based on the providers chosen in this project's settings. Change providers in Production settings or at the top of Writing.")}</p>
          <ul>
            {needs.map((n) => {
              const ok = n.key === null || (n.key && status.saved[n.key]);
              return (
                <li key={n.id} className={ok ? "ok" : "missing"}>
                  <span>{t(n.use)}</span>
                  <span>{n.key === undefined ? t("Unsupported provider: {provider}", { provider: n.provider || "—" })
                    : n.key === null ? t(n.login || "") : t(providerName(n.key))}</span>
                  <span className="key-need-state">{ok ? t("Ready") : n.key === undefined ? t("Change provider") : t("Missing key")}</span>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      <p className="credential-message" role="status">
        {message || (native && !status && !error ? t("Checking saved keys…") : "")}
      </p>
      {groups.map((group) => (
        <section className="credential-group" key={group.id} aria-labelledby={`${uid}-key-group-${group.id}`}>
          <h4 id={`${uid}-key-group-${group.id}`}><group.icon size={15} /> {t(group.title)}</h4>
          <p className="small-muted">{t(group.description)}</p>
          <div className="credential-list">
            {keyProviders.filter((p) => p.group === group.id).map((provider) => {
              const uses = usesOf(provider.key);
              const saved = status?.saved[provider.key];
              return (
                <form className={`credential-row ${uses.length && status && !saved ? "needed" : ""}`} key={provider.key}
                  onSubmit={(e) => { e.preventDefault(); void save(provider.key, drafts[provider.key] || ""); }}>
                  <div className="credential-heading">
                    <label htmlFor={`${uid}-key-${provider.key}`}>
                      <KeyRound size={13} />
                      {t(provider.name)}
                      {provider.alsoText && <span className="key-tag">{t("Also a language model")}</span>}
                    </label>
                    <span className={saved ? "key-saved" : uses.length ? "key-missing" : "small-muted"}>
                      {!status ? "" : saved ? t("Saved securely") : uses.length ? t("Needed · not saved") : t("Not saved")}
                    </span>
                  </div>
                  <p className="small-muted">
                    {settings
                      ? uses.length ? t("This project uses it for: {uses}", { uses: uses.join(t(", ")) }) : t("Not used by this project's current settings.")
                      : t("Choose providers in a project's settings to see which keys it needs.")}
                  </p>
                  <div className="credential-input">
                    <input id={`${uid}-key-${provider.key}`} aria-label={t("{provider} API key", { provider: provider.name })}
                      type="password" autoComplete="new-password" spellCheck={false} autoCapitalize="none"
                      disabled={!native || !status || !!busy} value={drafts[provider.key] || ""}
                      placeholder={saved ? t("Saved • enter a replacement key") : t("Enter API key")}
                      onChange={(e) => setDrafts((d) => ({ ...d, [provider.key]: e.target.value }))} />
                    <button className="button primary" type="submit" disabled={!native || !status || !!busy || !drafts[provider.key]?.trim()}>
                      {busy === provider.key ? t("Saving…") : t("Save key")}
                    </button>
                    <button className="button" type="button" aria-label={t("Remove {provider} API key", { provider: provider.name })}
                      disabled={!saved || !!busy} onClick={() => void save(provider.key, null)}>
                      {t("Remove")}
                    </button>
                  </div>
                </form>
              );
            })}
            {group.id === "image" && <GoogleLoginCheck settings={settings} needs={needs} />}
          </div>
        </section>
      ))}
      <p className="small-muted credential-footnote">
        {t("New jobs and new Claude Code sessions use the saved keys. Running jobs and terminal sessions keep their current keys.")}
      </p>
    </div>
  );
}
