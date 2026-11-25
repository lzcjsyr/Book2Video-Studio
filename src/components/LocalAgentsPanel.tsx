import { useState } from "react";
import { TerminalSquare } from "lucide-react";
import { api } from "../api";
import { agentNames, type AgentId } from "../agents";
import { useI18n } from "../i18n";

type AgentStatus = {
  installed: boolean;
  path: string | null;
  version: string | null;
  logged_in: boolean | null;
};

function AgentStatusRow({ agent }: { agent: AgentId }) {
  const { t } = useI18n();
  const [status, setStatus] = useState<AgentStatus>();
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState(false);
  async function check() {
    setError(false);
    setChecking(true);
    try {
      setStatus(await api<AgentStatus>(`/local-agents/${agent}`));
    } catch {
      setStatus(undefined);
      setError(true);
    } finally {
      setChecking(false);
    }
  }

  const label = checking ? "Checking…" : error ? "Could not check the CLI." : !status ? "Not checked"
    : !status.installed ? "CLI not found" : status.logged_in === null ? "Login could not be verified"
    : status.logged_in ? "Signed in" : "Signed out";
  const tone = checking || !status ? (error ? "warning" : "neutral")
    : status.logged_in ? "success" : "warning";
  return <li className="local-agent-row">
    <span className="local-agent-icon" aria-hidden="true"><TerminalSquare size={17} /></span>
    <div className="local-agent-details">
      <div className="local-agent-name">
        <strong>{agentNames[agent]}</strong>
        {!checking && status?.version && <span className="small-muted">{status.version}</span>}
      </div>
      <div className={`local-agent-status ${tone}`} role="status">{t(label)}</div>
      {!checking && status?.path && <div className="local-agent-path" title={status.path}>{status.path}</div>}
    </div>
    <button type="button" className="button small" onClick={check} disabled={checking}
      aria-label={t("Check {agent}", { agent: agentNames[agent] })}>
      {t("Check")}
    </button>
  </li>;
}

export default function LocalAgentsPanel() {
  const { t } = useI18n();
  return <div className="local-agents">
    <p className="small-muted">{t("Install and sign in to the CLI on this computer. Grist uses your local configuration.")}</p>
    <ul className="local-agent-list">
      {(Object.keys(agentNames) as AgentId[]).map((id) => <AgentStatusRow key={id} agent={id} />)}
    </ul>
  </div>;
}
