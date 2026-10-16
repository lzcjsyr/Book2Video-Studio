import { useId, useEffect, useState } from "react";
import { BookOpen, FolderOpen, History, Plus, RefreshCw, Save, Trash2, Upload, X } from "lucide-react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { api } from "../api";
import { useI18n } from "../i18n";
import { librarySettings, orderedLibraryGroups } from "../library";
import type { LibraryCatalog, LibraryItem, LibraryRevision } from "../types";

type Value = LibraryItem["value"];

export default function LibraryPanel({
  initialGroup = "writing_skills", initialId, embedded = false,
  onClose, onChanged, onUse, onDirtyChange,
}: {
  initialGroup?: string;
  initialId?: string;
  embedded?: boolean;
  onClose: () => void;
  onChanged: () => Promise<void>;
  onUse?: (group: string, id: string) => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const uid = useId();
  const { t, locale } = useI18n();
  const [catalog, setCatalog] = useState<LibraryCatalog>({ groups: [] });
  const [groupId, setGroupId] = useState(initialGroup);
  const [templateStep, setTemplateStep] = useState(1);
  const [resource, setResource] = useState<LibraryItem | null>(null);
  const [draft, setDraft] = useState<Value>("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [newId, setNewId] = useState("");
  const [source, setSource] = useState("");
  const [history, setHistory] = useState<LibraryRevision[] | null>(null);
  const [lastRevision, setLastRevision] = useState("");
  const groups = orderedLibraryGroups(catalog);
  const group = groups.find((g) => g.id === groupId && (g.id !== "templates" || g.step === templateStep));
  const groupLabel = (g: typeof group) => g ? `${g.step} · ${t(g.label)}` : t("Library");

  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await api<LibraryCatalog>("/library");
        if (cancelled) return;
        setCatalog(list);
        const ordered = orderedLibraryGroups(list);
        const chosen = ordered.find((g) => g.id === initialGroup && g.items.some((item) => item.id === initialId))
          || ordered.find((g) => g.id === initialGroup) || ordered[0];
        if (!chosen) return;
        setGroupId(chosen.id);
        if (chosen.id === "templates") setTemplateStep(chosen.step);
        const id = chosen.items.find((i) => i.id === initialId)?.id || chosen.items[0]?.id || (chosen.error ? "__source__" : "");
        if (id) {
          const item = await readItem(chosen.id, id);
          if (!cancelled) { setResource(item); setDraft(structuredClone(item.value)); }
        }
      } catch (error) { if (!cancelled) setMessage(String(error)); }
    })();
    return () => { cancelled = true; };
  }, []);

  function readItem(group: string, id: string, file?: string) {
    const query = new URLSearchParams({ group, id, ...(file ? { file } : {}) });
    return api<LibraryItem>("/library/item?" + query);
  }
  function guard() {
    if (!dirty) return true;
    setMessage("Save or discard your library edits before switching.");
    return false;
  }
  async function perform(task: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await task(); } catch (error) { setMessage(String(error)); }
    finally { setBusy(false); }
  }
  function accept(item: LibraryItem | null) {
    setResource(item); setDraft(item ? structuredClone(item.value) : "");
    setDirty(false); setCreating(false); setImporting(false);
  }
  async function choose(nextGroup: string, id?: string, file?: string) {
    if (!guard()) return;
    await perform(async () => {
      setGroupId(nextGroup); setHistory(null);
      if (nextGroup === "templates") setTemplateStep(groups.find((g) => g.id === nextGroup && g.items.some((item) => item.id === id))?.step || templateStep);
      accept(id ? await readItem(nextGroup, id, file) : null);
    });
  }
  function edit(value: Value) { setDraft(value); setDirty(true); }
  function property(key: string, value: unknown) {
    edit({ ...(typeof draft === "object" ? draft : {}), [key]: value });
  }
  async function refresh() {
    if (!guard()) return;
    await perform(async () => {
      const list = await api<LibraryCatalog>("/library"); setCatalog(list);
      if (resource && (resource.kind === "source" || list.groups.find((g) => g.id === groupId)?.items.some((i) => i.id === resource.id)))
        accept(await readItem(groupId, resource.id, resource.file));
      else accept(null);
      await onChanged();
      setMessage("Library refreshed.");
    });
  }
  async function save() {
    await perform(async () => {
      const result = await api<{ item: LibraryItem; revision: string }>(
        "/library/" + (importing ? "import" : creating ? "create" : "save"),
        importing
          ? { group: groupId, id: newId, source }
          : { group: groupId, id: creating ? newId : resource?.id, file: resource?.file,
              value: draft, expected_hash: creating ? group?.hash : resource?.hash },
      );
      setLastRevision(result.revision); accept(result.item);
      setCatalog(await api<LibraryCatalog>("/library"));
      await onChanged();
      setMessage("Saved to the library. New generation jobs use these changes.");
    });
  }
  async function remove() {
    if (!resource || !guard()) return;
    await perform(async () => {
      const result = await api<{ revision: string }>("/library/remove", {
        group: groupId, id: resource.id, expected_hash: resource.package_hash || resource.hash,
      });
      setLastRevision(result.revision);
      const list = await api<LibraryCatalog>("/library"); setCatalog(list);
      const next = list.groups.find((g) => g.id === groupId)?.items[0]?.id;
      accept(next ? await readItem(groupId, next) : null);
      await onChanged();
      setMessage("Removed from the library. Restore it from library history if needed.");
    });
  }
  async function restore(revision: string) {
    if (!guard()) return;
    await perform(async () => {
      await api("/library/restore", { revision });
      setCatalog(await api<LibraryCatalog>("/library"));
      accept(null); setLastRevision("");
      setHistory(await api<LibraryRevision[]>("/library/history"));
      await onChanged(); setMessage("Library revision restored.");
    });
  }
  async function reveal(path: string) {
    await perform(async () => {
      if (!isTauri()) throw new Error("Open the desktop app to show the source folder.");
      await invoke("open_library_folder", { path });
    });
  }
  async function browse(folder: boolean) {
    await perform(async () => {
      if (!isTauri()) throw new Error("Use the desktop file picker, or enter a local source path.");
      const picked = await open({ directory: folder, multiple: false,
        ...(folder ? {} : { filters: [{ name: "Skill", extensions: ["md", "zip"] }] }) });
      if (typeof picked !== "string") return;
      setSource(picked);
      if (!newId) {
        const name = picked.split(/[\\/]/).at(-1)?.replace(/\.(md|zip)$/i, "") || "custom_skill";
        setNewId(name.toLowerCase() === "skill" ? "custom_skill" : name);
      }
    });
  }
  function startCreate(importSkill: boolean) {
    if (!guard() || !group) return;
    setHistory(null); setResource(null); setCreating(!importSkill); setImporting(importSkill);
    setNewId(""); setSource(""); setMessage(""); setDirty(true);
    setDraft(group.kind === "image_template"
      ? { label: "", description: "", template: "{summary}\n\n{segment}\n\n{style_block}" }
      : group.kind === "motion_style"
        ? { label: "", official_style: "Swiss Pulse", palette: "dark-premium", best_for: "", references: [] }
        : "");
  }
  function textField(key: string, label: string, multiline = false) {
    const value = typeof draft === "object" ? draft[key] : "";
    return <label className="field" key={key}>
      <span>{t(label)}</span>
      {multiline
        ? <textarea value={String(value ?? "")} rows={key === "template" ? 14 : 4}
            onChange={(e) => property(key, e.target.value)} disabled={busy} />
        : <input value={String(value ?? "")} onChange={(e) => property(key, e.target.value)} disabled={busy} />}
    </label>;
  }
  const kind = resource?.kind || group?.kind;
  const body = <>
    <div className="library-intro">
      <BookOpen size={17} />
      <p>{t("Skills and templates are shared across projects. Saved changes apply to new generation jobs.")}</p>
      <button className="icon-button" aria-label={t("Refresh library")} disabled={busy} onClick={refresh}><RefreshCw size={14} /></button>
      <button className="button small" disabled={busy} onClick={() => {
        if (!guard()) return;
        perform(async () => setHistory(history ? null : await api<LibraryRevision[]>("/library/history")));
      }}><History size={13} />{t("Library history")}</button>
    </div>
    {message && <div className="library-message" role="status">
      <span>{t(message)}</span>
      {lastRevision && <button className="button small" disabled={busy || dirty} onClick={() => restore(lastRevision)}>{t("Undo last change")}</button>}
    </div>}
    <div className="library-layout">
      <nav className="library-groups" aria-label={t("Library groups")}>
        {groups.map((g) => <button key={`${g.step}-${g.id}`} className={group === g ? "active" : ""} disabled={busy}
          onClick={() => choose(g.id, g.items[0]?.id || (g.error ? "__source__" : undefined))}><span>{groupLabel(g)}</span><span className="mono">{g.items.length}</span></button>)}
      </nav>
      <div className="library-items">
        <div className="library-list-heading"><span>{groupLabel(group)}</span>
          {group && !["prompt", "skill"].includes(group.kind) && <button className="icon-button" aria-label={t("Add style or template")} disabled={busy || Boolean(group.error)} onClick={() => startCreate(false)}><Plus size={15} /></button>}
          {kind === "skill" && <button className="icon-button" aria-label={t("Import skill")} disabled={busy} onClick={() => startCreate(true)}><Upload size={15} /></button>}
        </div>
        {group?.error && <p className="library-message">{t(group.error)}</p>}
        {group?.items.map((item) => <button key={item.id} className={resource?.id === item.id ? "active" : ""} disabled={busy}
          onClick={() => choose(groupId, item.id)}><span>{t(item.label)}</span>
          {item.label !== item.id && <small className="mono">{item.id}</small>}
          {item.description && <small>{item.description}</small>}</button>)}
        {!group?.items.length && <p className="small-muted">{t("No library items yet.")}</p>}
        {group && !["prompt", "skill"].includes(group.kind) && <button className={resource?.kind === "source" ? "active" : ""}
          disabled={busy} onClick={() => choose(groupId, "__source__")}><span>{t("Edit source file")}</span><small>{t("Advanced · YAML")}</small></button>}
      </div>
      <div className="library-editor">
        {history ? <div className="library-history">
          <h3>{t("Library history")}</h3>
          <p className="small-muted">{t("Restore newer revisions first to preserve later edits.")}</p>
          {!history.length && <p className="small-muted">{t("No library revisions yet.")}</p>}
          {history.map((event) => <div key={event.id} className="library-history-row">
            <div><strong>{event.item}</strong><span>{groupLabel(groups.find((g) => g.id === event.group && g.items.some((item) => item.id === event.item))
              || groups.find((g) => g.id === event.group))}</span>
              <small>{new Date(event.time).toLocaleString(locale)}</small></div>
            <button className="button small" disabled={busy || !event.can_restore} onClick={() => restore(event.id)}>{t("Restore")}</button>
          </div>)}
        </div> : importing ? <div className="library-import">
          <h3>{t("Import skill")}</h3>
          <p className="small-muted">{t("Import SKILL.md, a ZIP package, or a skill folder. Folder imports preserve references, scripts, and assets.")}</p>
          <label className="field"><span>{t("Skill folder name")}</span><input value={newId} onChange={(e) => setNewId(e.target.value)} disabled={busy} /></label>
          <label className="field"><span>{t("Skill source path")}</span><input value={source} onChange={(e) => setSource(e.target.value)} disabled={busy} /></label>
          <div className="library-import-actions">
            <button className="button" disabled={busy} onClick={() => browse(false)}><Upload size={14} />{t("Choose skill file")}</button>
            <button className="button" disabled={busy} onClick={() => browse(true)}><FolderOpen size={14} />{t("Choose skill folder")}</button>
          </div>
        </div> : resource || creating ? <>
          <div className="library-editor-heading">
            <div><h3>{creating ? t("New style or template") : t(resource?.label || "")}</h3>
              {resource && <p className="library-source mono">{resource.path}</p>}</div>
            {resource && <button className="icon-button" aria-label={t("Open source folder")} title={t("Open source folder")} disabled={busy} onClick={() => reveal(resource.path)}><FolderOpen size={17} /></button>}
          </div>
          {creating && <label className="field"><span>{t("Item ID")}</span><input placeholder="my_style" value={newId} onChange={(e) => setNewId(e.target.value)} disabled={busy} /></label>}
          {resource?.files.length ? <label className="field"><span>{t("Skill file")}</span>
            <select value={resource.file} disabled={busy} onChange={(e) => choose(groupId, resource.id, e.target.value)}>
              {resource.files.map((file) => <option key={file}>{file}</option>)}</select></label> : null}
          {resource?.variables.length ? <p className="library-variables">{t("Keep these template variables:")}{" "}
            {resource.variables.map((variable) => <code key={variable}>{"{" + variable + "}"}</code>)}</p> : null}
          {kind === "image_template" ? <>{textField("label", "Display name")}{textField("description", "Description")}{textField("template", "Template content", true)}</>
            : kind === "motion_style" ? <>{textField("label", "Display name")}{textField("official_style", "Motion design style")}{textField("palette", "Palette")}{textField("best_for", "Best suited for", true)}
              <label className="field"><span>{t("Reference files · one path per line")}</span>
                <textarea rows={5} value={typeof draft === "object" && Array.isArray(draft.references) ? draft.references.join("\n") : ""}
                  disabled={busy} onChange={(e) => property("references", e.target.value.split("\n").filter((line) => line.trim()))} /></label></>
              : <label className="field library-text"><span>{t(kind === "style" ? "Style description" : "Skill or template content")}</span>
                <textarea aria-label={t("Library content editor")} value={typeof draft === "string" ? draft : ""} disabled={busy} onChange={(e) => edit(e.target.value)} spellCheck={false} /></label>}
        </> : <div className="library-empty"><BookOpen size={30} /><p>{t("Select a skill, style, or template to edit.")}</p>
          {group && <button className="button" onClick={() => reveal(group.path)}>{t("Open source folder")}</button>}</div>}
      </div>
    </div>
    <div className="library-footer">
      <span className="small-muted">{dirty ? t("Unsaved library changes") : t("Source files remain editable outside the app.")}</span>
      {resource?.removable && !creating && !importing && !history && <button className="button library-delete" disabled={busy || dirty} onClick={remove}><Trash2 size={13} />{t("Remove")}</button>}
      {dirty && <button className="button" disabled={busy} onClick={() => {
        accept(resource); setMessage("");
      }}>{t("Discard changes")}</button>}
      {onUse && resource && resource.kind !== "source" && librarySettings[groupId] && !history && <button className="button" disabled={busy || dirty} onClick={() => onUse(groupId, resource.id)}>{t("Use in this project")}</button>}
      {!history && (resource || creating || importing) && <button className="button primary" disabled={busy || !dirty || ((creating || importing) && !newId) || (importing && !source)} onClick={save}>
        {importing ? <Upload size={14} /> : <Save size={14} />}{t(importing ? "Import skill" : "Save to library")}</button>}
    </div>
  </>;
  if (embedded) return <section className="library-panel" aria-label={t("Skills & templates")}>{body}</section>;
  return <div className="modal-backdrop"><section className="modal library-modal" role="dialog" aria-modal="true" aria-labelledby={`${uid}-library-title`}>
    <div className="modal-heading"><h2 id={`${uid}-library-title`}>{t("Skills & templates")}</h2>
      <button className="icon-button" aria-label={t("Close library")} disabled={busy} onClick={() => { if (guard()) onClose(); }}><X size={18} /></button></div>{body}
  </section></div>;
}
