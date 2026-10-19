import { Check, FileText, Film, TerminalSquare } from "lucide-react";
import type { ReactNode } from "react";
import { useI18n } from "../i18n";
import { libraryChoices } from "../library";
import type { Detail, LibraryCatalog, Settings } from "../types";

const styleLabels: Record<string, string> = { "content-led": "Free design", "editorial-explainer": "Editorial explainer", "cinematic-essay": "Cinematic essay", "kinetic-social": "Kinetic social" };

/**
 * How Assembly makes the video. With Claude Code editing, the panel holds the brief: the style direction,
 * the person's choices (children) and the session's actions. Claude Code designs, renders and delivers
 * the video itself. A new edit starts from the page header.
 */
export default function AssemblyEditing({ settings, edit, library, active, locked, children, onChange, onDesign, onFinish, onLibrary }: {
  settings: Settings[string]; edit: Detail["assembly_edit"]; library?: LibraryCatalog;
  active: boolean; locked: boolean;
  /** The basic choices Claude Code starts from. */
  children?: ReactNode;
  onChange: (patch: Record<string, unknown>) => Promise<boolean>;
  onDesign: (revise: boolean) => void; onFinish: () => void; onLibrary: () => void;
}) {
  const { t } = useI18n();
  const agent = settings.assembly_method === "agent";
  return <section className={`assembly-editing${agent ? " agent" : ""}`} aria-label={t("Assembly method")}>
    <div className="cover-methods assembly-methods" role="group" aria-label={t("Assembly method")}>
      {(["standard", "agent"] as const).map((method) => <button type="button" key={method} disabled={locked || active}
        className={`cover-method${agent === (method === "agent") ? " selected" : ""}`} aria-pressed={agent === (method === "agent")}
        onClick={() => onChange({ assembly_method: method })}>
        {method === "agent" ? <TerminalSquare size={18} /> : <Film size={18} />}
        <span><strong>{t(method === "agent" ? "Claude Code editing" : "Standard assembly")}</strong>
          <small>{t(method === "agent" ? "Polish timing, captions, emphasis and sound with an editing skill." : "Assemble selected visuals and recordings with your saved settings.")}</small></span>
      </button>)}
    </div>
    {agent && <div className="editing-brief">
      <div className="editing-brief-heading">
        <div>
          <h2>{t("Editing brief")}</h2>
          <p className="small-muted">{t("Claude Code designs the whole video from the recordings, visuals and text, following these choices. Changes apply to a new or revised edit.")}</p>
        </div>
        <div className="editing-skill-row">
          <label className="field"><span>{t("Style direction")}</span>
            <select aria-label={t("Style direction")} value={String(settings.agent_skill || "editorial-explainer")} disabled={locked || active}
              onChange={(e) => onChange({ agent_skill: e.target.value })}>
              {libraryChoices(library, "assembly_skills").map((skill) => <option key={skill.id} value={skill.id}>{t(styleLabels[skill.id] || skill.label)}</option>)}
            </select></label>
          <button className="icon-button" disabled={locked || active} onClick={onLibrary}
            aria-label={t("Edit style directions")} title={t("Edit style directions")}><FileText size={15} /></button>
        </div>
      </div>
      {children}
      <div className="editing-session">
        {active ? <div className="agent-banner"><span>{t("Claude Code is making the video in the right pane.")}</span>
          <button className="button small primary" onClick={onFinish}><Check size={13} />{t("Finish session")}</button></div>
          : <div className="cover-design-buttons">
            <button className="button small" disabled={locked || !edit} onClick={() => onDesign(true)}>{t("Revise last edit")}</button>
          </div>}
        {edit ? <p className="small-muted">{t(edit.delivered ? "Last edit: {style} · delivered the final video" : "Last edit: {style} · no final video delivered", { style: t(styleLabels[edit.skill] || edit.skill) })}</p>
          : !active && <p className="small-muted">{t("No edit yet. Start one with New video edit at the top of the page.")}</p>}
        {edit && !edit.current && <p className="small-muted">{t("Recordings, visuals or choices changed since the last edit.")}</p>}
      </div>
    </div>}
  </section>;
}
