import { useId, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { useI18n } from "../i18n";
import type { ClaudeCodeMode } from "../types";

/** The same connection choice controls every interactive and background agent. */
export default function AgentConnection({ mode, disabled, onMode }: {
  mode: ClaudeCodeMode;
  disabled?: boolean;
  onMode?: (mode: ClaudeCodeMode) => void;
}) {
  const { t } = useI18n();
  const id = useId();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  return <div>
    <label className="field" htmlFor={id}>
      <span>{t("Claude Code connection")}</span>
      <select id={id} value={mode} disabled={disabled || saving || !isTauri()} onChange={async (event) => {
        const next = event.target.value as ClaudeCodeMode;
        setSaving(true); setError("");
        try {
          await invoke("claude_code_set_mode", { mode: next });
          onMode?.(next);
        } catch (error) { setError(String(error)); }
        finally { setSaving(false); }
      }}>
        <option value="api_key">{t("Built-in Claude Code · API key")}</option>
        <option value="subscription">{t("Your Claude Code · subscription")}</option>
      </select>
    </label>
    <p className="small-muted">{t("Applies to new Writing, Motion, Assembly and HTML cover sessions. Running sessions keep their current connection.")}</p>
    {error && <p role="alert" className="field-error">{error}</p>}
  </div>;
}
