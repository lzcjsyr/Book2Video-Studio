import { ChevronDown, ChevronRight, FileText, RefreshCw, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { useCollapsed } from "../collapse";
import { useI18n } from "../i18n";

// The shared summary every visual follows. Visuals create it when it is missing or
// outdated; here it can be reviewed, edited, or regenerated on its own.
export default function VisualDirection({ summary, hash, outdated, disabled, onSave, onGenerate, onEditInstructions, onDirtyChange }: {
  summary: string;
  hash: string;
  outdated: boolean;
  disabled: boolean;
  onSave: (summary: string, expectedHash: string) => Promise<boolean>;
  onGenerate: () => void;
  onEditInstructions: () => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const { t } = useI18n();
  const [collapsed, toggle] = useCollapsed("visual-direction");
  // An edit remembers the saved version it started from, so a conflicting save is rejected.
  const [draft, setDraft] = useState<{ text: string; hash: string } | null>(null);
  const text = draft?.text ?? summary;
  const dirty = draft !== null && draft.text !== summary;
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  const status = !summary.trim() ? "Not generated" : outdated ? "Outdated" : "";
  return <section className={"direction-panel" + (collapsed ? " collapsed" : "")} aria-label={t("Visual direction")}>
    <div className="direction-heading">
      <button type="button" className="collapse-toggle" aria-expanded={!collapsed} aria-controls="visual-direction-body"
        title={t(collapsed ? "Expand" : "Collapse")} onClick={toggle}>
        {collapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}
        <span>{t("Visual direction")}</span>
      </button>
      {collapsed && <span className="collapse-summary" title={text}>{text}</span>}
      {status && <span className="direction-status">{t(status)}</span>}
    </div>
    <div id="visual-direction-body" hidden={collapsed}>
      <p className="small-muted">{t("Every image and motion clip follows this summary of the story. Visuals create it when it is missing and refresh it after the manuscript changes.")}</p>
      <textarea aria-label={t("Visual direction editor")} value={text} disabled={disabled}
        placeholder={t("Generate a direction, or describe the story's look and mood.")}
        onChange={(e) => setDraft({ text: e.target.value, hash: draft?.hash ?? hash })} />
      <div className="direction-actions">
        <button className="button small" disabled={disabled} onClick={onEditInstructions}>
          <FileText size={13} /> {t("Edit instructions")}</button>
        <button className="button small" disabled={disabled || dirty} onClick={onGenerate}>
          <RefreshCw size={13} /> {t(summary.trim() ? "Regenerate" : "Generate")}</button>
        <button className="button small primary" disabled={disabled || !dirty}
          onClick={async () => { if (draft && await onSave(draft.text, draft.hash)) setDraft(null); }}>
          <Save size={13} /> {t("Save direction")}</button>
      </div>
    </div>
  </section>;
}
