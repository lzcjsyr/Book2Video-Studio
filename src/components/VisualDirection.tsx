import { ChevronDown, ChevronRight, FileText, Loader2, RefreshCw, Save } from "lucide-react";
import { useId, useEffect, useImperativeHandle, useState } from "react";
import { useCollapsed } from "../collapse";
import { useI18n } from "../i18n";

/** Saves an unsaved edit from outside, as a closing question does. */
export interface DirectionHandle { save: () => Promise<boolean> }

// The shared summary every visual follows. Visuals create it when it is missing or
// outdated; here it can be reviewed, edited, or generated on its own.
export default function VisualDirection({ handle, summary, hash, outdated, disabled, generating, generationBlocked, onGenerate, onSave, onEditInstructions, onDirtyChange }: {
  handle?: React.Ref<DirectionHandle>;
  summary: string;
  hash: string;
  outdated: boolean;
  disabled: boolean;
  generating: boolean;
  generationBlocked: boolean;
  onGenerate: () => void;
  onSave: (summary: string, expectedHash: string) => Promise<boolean>;
  onEditInstructions: () => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const uid = useId();
  const { t } = useI18n();
  const [collapsed, toggle] = useCollapsed("visual-direction", true);
  // An edit remembers the saved version it started from, so a conflicting save is rejected.
  const [draft, setDraft] = useState<{ text: string; hash: string } | null>(null);
  const text = draft?.text ?? summary;
  const dirty = draft !== null && draft.text !== summary;
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  async function save() {
    if (!draft || !dirty) return true;
    const saved = await onSave(draft.text, draft.hash);
    if (saved) setDraft(null);
    return saved;
  }
  useImperativeHandle(handle, () => ({ save }));
  const status = !summary.trim() ? "Not generated" : outdated ? "Outdated" : "";
  return <section className={"direction-panel" + (collapsed ? " collapsed" : "")} aria-label={t("Content summary")}>
    <div className="direction-heading">
      <button type="button" className="collapse-toggle" aria-expanded={!collapsed} aria-controls={`${uid}-visual-direction-body`}
        title={t(collapsed ? "Expand" : "Collapse")} onClick={toggle}>
        {collapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}
        <span>{t("Content summary")}</span>
      </button>
      {collapsed && <span className="collapse-summary" title={text}>{text}</span>}
      {status && <span className="direction-status">{t(status)}</span>}
      <button className="button small direction-generate" disabled={disabled || dirty || generationBlocked}
        title={t(dirty || generationBlocked ? "Save changes before generating." : "Generate only the content summary using its saved provider and model.")}
        onClick={onGenerate}>
        {generating ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />}
        {t(generating ? "Generating…" : summary.trim() ? "Regenerate summary" : "Generate summary")}
      </button>
    </div>
    <div id={`${uid}-visual-direction-body`} hidden={collapsed}>
      <p className="small-muted">{t("Every image and motion clip draws on this summary. Generate it separately, or let visual generation create it when missing. Regenerate it here after the manuscript changes.")}</p>
      <textarea aria-label={t("Content summary editor")} value={text} disabled={disabled}
        placeholder={t("Generate a summary, or write what the video covers.")}
        onChange={(e) => setDraft({ text: e.target.value, hash: draft?.hash ?? hash })} />
      <div className="direction-actions">
        <button className="button small" disabled={disabled} onClick={onEditInstructions}>
          <FileText size={13} /> {t("Edit instructions")}</button>
        <button className="button small primary" disabled={disabled || !dirty}
          onClick={save}>
          <Save size={13} /> {t("Save summary")}</button>
      </div>
    </div>
  </section>;
}
