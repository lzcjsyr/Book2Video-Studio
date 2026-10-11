import { ChevronDown, ChevronRight, FileText, FolderOpen, Save, TerminalSquare } from "lucide-react";
import { isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { useCollapsed } from "../collapse";
import { useI18n } from "../i18n";
import { libraryChoices } from "../library";
import type { Bootstrap } from "../types";
import type { WritingOptions } from "../writing";

export default function WritingSetup({ value, bootstrap, disabled, pending, onChange, onSave, onEditSkill }: {
  value: WritingOptions;
  bootstrap: Bootstrap | null;
  disabled: boolean;
  pending: boolean;
  onChange: (value: WritingOptions) => void;
  onSave: () => void;
  onEditSkill: () => void;
}) {
  const { t } = useI18n();
  const skills = libraryChoices(bootstrap?.library, "writing_skills");
  const change = <K extends keyof WritingOptions>(key: K, next: WritingOptions[K]) => onChange({ ...value, [key]: next });
  const [collapsed, toggle] = useCollapsed("writing-setup");
  const skill = skills.find((s) => s.id === value.agent_skill);
  const summary = [
    t(value.mode === "source" ? "Write from a document" : "Write directly with Claude Code"),
    skill ? t(skill.label) : value.agent_skill,
  ].join(" · ");
  return <section className={"writing-setup" + (collapsed ? " collapsed" : "")} aria-label={t("Writing setup")}>
    <div className="writing-setup-heading">
      <button type="button" className="collapse-toggle" aria-expanded={!collapsed} aria-controls="writing-setup-body"
        title={t(collapsed ? "Expand" : "Collapse")} onClick={toggle}>
        {collapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}
        <span>{t("Writing mode")}</span>
      </button>
      {collapsed && <span className="collapse-summary" title={summary}>{summary}</span>}
      {(!collapsed || pending) && <button className="button small" disabled={disabled || !pending} onClick={onSave}>
        <Save size={13} /> {t(pending ? "Save writing setup" : "Writing setup saved")}
      </button>}
    </div>
    <div id="writing-setup-body" hidden={collapsed}>
    <div className="writing-mode-options" role="group" aria-label={t("Writing mode")}>
      <button type="button" aria-pressed={value.mode === "source"} disabled={disabled} onClick={() => change("mode", "source")}>
        <FileText size={17} /><span><strong>{t("Write from a document")}</strong><small>{t("Use a manuscript, book, or source folder")}</small></span>
      </button>
      <button type="button" aria-pressed={value.mode === "direct"} disabled={disabled} onClick={() => change("mode", "direct")}>
        <TerminalSquare size={17} /><span><strong>{t("Write directly with Claude Code")}</strong><small>{t("Give your topic and instructions in the terminal")}</small></span>
      </button>
    </div>
    {value.mode === "source" ? <label className="field writing-source">
      <span>{t("Source document or folder")}</span>
      <div className="input-action">
        <input value={value.source} disabled={disabled} onChange={(e) => change("source", e.target.value)} placeholder={t("Source document path")} />
        {isTauri() && <>
          <button className="button small" disabled={disabled} onClick={async () => {
            const path = await open({ multiple: false });
            if (typeof path === "string") change("source", path);
          }}><FileText size={14} />{t("Choose file")}</button>
          <button className="button small" disabled={disabled} onClick={async () => {
            const path = await open({ directory: true, multiple: false });
            if (typeof path === "string") change("source", path);
          }}><FolderOpen size={14} />{t("Choose folder")}</button>
        </>}
      </div>
    </label> : <p className="small-muted writing-mode-description">{t("No source document is required. Claude Code will wait for your writing instructions in the terminal.")}</p>}
    <div className="writing-skill-row">
      <label className="field">
        <span>{t("Writing skill")}</span>
        <select value={value.agent_skill} disabled={disabled} onChange={(e) => change("agent_skill", e.target.value)}>
          {!skills.some((s) => s.id === value.agent_skill) && <option value={value.agent_skill}>{t("Unavailable: {id}", { id: value.agent_skill })}</option>}
          {skills.map((s) => <option key={s.id} value={s.id}>{t(s.label)}</option>)}
        </select>
      </label>
      <button className="button small" disabled={disabled} onClick={onEditSkill}><FileText size={14} />{t("Edit or import writing skills")}</button>
    </div>
    <details className="writing-agent-settings">
      <summary>{t("Writing agent provider & model")}</summary>
      <div className="form-grid">
        <label className="field"><span>{t("Writing agent provider")}</span><input value={value.llm_server} disabled={disabled} onChange={(e) => change("llm_server", e.target.value)} /></label>
        <label className="field"><span>{t("Writing agent model")}</span><input value={value.llm_model} disabled={disabled} onChange={(e) => change("llm_model", e.target.value)} /></label>
      </div>
    </details>
    {pending && <p className="small-muted" role="status">{t("Your selections will also be saved when you activate Claude Code.")}</p>}
    </div>
  </section>;
}
