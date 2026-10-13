import { FileText, Film, Play, TerminalSquare } from "lucide-react";
import { useI18n } from "../i18n";
import { libraryChoices } from "../library";
import type { Detail, LibraryCatalog, Settings } from "../types";

const styleLabels: Record<string, string> = { "editorial-explainer": "Editorial explainer", "cinematic-essay": "Cinematic essay", "kinetic-social": "Kinetic social" };

/** Assembly's agent workflow; preview files are separate from published exports. */
export default function AssemblyEditing({ settings, edit, library, active, locked, onChange, onDesign, onFinish, onPreview, onRender, onLibrary, mediaUrl }: {
  settings: Settings[string]; edit: Detail["assembly_edit"]; library?: LibraryCatalog;
  active: boolean; locked: boolean;
  onChange: (patch: Record<string, unknown>) => Promise<boolean>;
  onDesign: (revise: boolean) => void; onFinish: () => void;
  onPreview: () => void; onRender: () => void; onLibrary: () => void;
  mediaUrl: (path: string) => string;
}) {
  const { t } = useI18n();
  const agent = settings.assembly_method === "agent";
  return <section className="assembly-editing" aria-label={t("Assembly method")}>
    <div className="cover-methods" role="group" aria-label={t("Assembly method")}>
      {(["standard", "agent"] as const).map((method) => <button type="button" key={method} disabled={locked || active}
        className={`cover-method${agent === (method === "agent") ? " selected" : ""}`} aria-pressed={agent === (method === "agent")}
        onClick={() => onChange({ assembly_method: method })}>
        {method === "agent" ? <TerminalSquare size={18} /> : <Film size={18} />}
        <span><strong>{t(method === "agent" ? "Claude Code editing" : "Standard assembly")}</strong>
          <small>{t(method === "agent" ? "Polish timing, captions, emphasis and sound with an editing skill." : "Assemble selected visuals and recordings with your saved settings.")}</small></span>
      </button>)}
    </div>
    {agent && <>
      <div className="editing-skill-row">
        <label className="field"><span>{t("Editing skill")}</span>
          <select aria-label={t("Editing skill")} value={String(settings.agent_skill || "editorial-explainer")} disabled={locked || active}
            onChange={(e) => onChange({ agent_skill: e.target.value })}>
            {libraryChoices(library, "assembly_skills").map((skill) => <option key={skill.id} value={skill.id}>{t(styleLabels[skill.id] || skill.label)}</option>)}
          </select></label>
        <button className="button small" disabled={locked || active} onClick={onLibrary}><FileText size={13} />{t("Edit editing skills")}</button>
      </div>
      <p className="small-muted">{t("Uses Writing's Claude Code login, provider and model. Published videos stay available while you edit.")}</p>
      {active ? <div className="agent-banner"><span>{t("Claude Code is polishing the video in the right pane.")}</span>
        <button className="button small primary" disabled={!edit?.valid || !edit.changed} onClick={onFinish}><Play size={13} />{t("Finish & render video")}</button></div>
        : <div className="cover-design-buttons">
          <button className="button small primary" disabled={locked} onClick={() => onDesign(false)}><TerminalSquare size={13} />{t("New video edit")}</button>
          <button className="button small" disabled={locked || !edit} onClick={() => onDesign(true)}>{t("Revise saved edit")}</button>
          <button className="button small" disabled={locked || !edit?.valid || !edit.current} onClick={onPreview}>{t("Render editing preview")}</button>
          <button className="button small" disabled={locked || !edit?.valid || !edit.current} onClick={onRender}>{t("Render saved edit")}</button>
        </div>}
      {edit && <p className="small-muted">{t("Saved edit: {style}", { style: t(styleLabels[edit.style] || edit.style) })} · {t("Skill changes apply to a new or revised edit.")}</p>}
      {edit?.problem && <p className="credential-error" role="alert">{edit.problem}</p>}
      {edit && !edit.current && <p className="small-muted">{t("Assembly sources or settings changed. Revise the edit before rendering.")}</p>}
      {!!edit?.warnings.length && <p className="small-muted">{t("Some captions still use estimated timing. Review them with the recordings before delivery.")}</p>}
      {edit?.preview && <details className="editing-preview"><summary>{t("Editing preview · separate from final exports")}</summary>
        <video controls src={mediaUrl(edit.preview)} aria-label={t("Editing preview")} />
        {edit.contact_sheet && <img src={mediaUrl(edit.contact_sheet)} alt={t("Editing preview contact sheet")} />}
      </details>}
    </>}
  </section>;
}
