import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Plus, X } from "lucide-react";
import { useI18n } from "./i18n";
import { isTauri } from "@tauri-apps/api/core";
import ProjectWorkspace from "./ProjectWorkspace";
import { closeProjectTabs, MAX_PROJECT_TABS, newProjectTab, openProjectTab, restoreTabs, TABS_STORAGE_KEY,
  type WorkspaceStatus, type WorkspaceTabs } from "./workspace-tabs";

export default function App() {
  const { t } = useI18n();
  const [workspace, setWorkspace] = useState(() => {
    try { return restoreTabs(localStorage.getItem(TABS_STORAGE_KEY)); }
    catch { return restoreTabs(null); }
  });
  const [status, setStatus] = useState<Record<string, WorkspaceStatus>>({});
  const [notice, setNotice] = useState("");
  // The question asked before closing Studio (or one tab) with unsaved edits or running work.
  const [closing, setClosing] = useState<{ tab?: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const savers = useRef<Record<string, () => Promise<boolean>>>({});
  const registerSaver = useCallback((key: string, save: (() => Promise<boolean>) | null) => {
    if (save) savers.current[key] = save; else delete savers.current[key];
  }, []);
  const current = useRef(workspace), statuses = useRef(status);
  statuses.current = status;
  const update = useCallback((next: WorkspaceTabs) => { current.current = next; setWorkspace(next); }, []);
  const openProject = useCallback((from: string, id: string, stage?: "1") => {
    const next = openProjectTab(current.current, id, from, stage);
    if (!next) { setNotice("Up to five projects can be open. Close a tab to open another."); return; }
    update(next);
  }, [update]);
  const addTab = useCallback((create = false) => {
    const state = current.current;
    if (state.tabs.length === MAX_PROJECT_TABS) { setNotice("Up to five projects can be open. Close a tab to open another."); return; }
    const tab = newProjectTab(create);
    update({ tabs: [...state.tabs, tab], active: tab.key });
  }, [update]);
  const report = useCallback((key: string, value: WorkspaceStatus) => {
    setStatus(previous => {
      const old = previous[key];
      if (old && Object.keys(value).every(k => old[k as keyof WorkspaceStatus] === value[k as keyof WorkspaceStatus])) return previous;
      return { ...previous, [key]: value };
    });
    const tab = current.current.tabs.find(t => t.key === key);
    if (tab && value.projectId && (tab.projectId !== value.projectId || tab.title !== value.title)) {
      update({ ...current.current, tabs: current.current.tabs.map(t => t.key === key ? { ...t, projectId: value.projectId, title: value.title } : t) });
    }
  }, [update]);
  const mayRemove = useCallback((ids: string[]) => {
    for (const tab of current.current.tabs) {
      if (tab.projectId && ids.includes(tab.projectId) && (statuses.current[tab.key]?.closeReason || statuses.current[tab.key]?.running))
        return "Save edits and finish running work in every open project before removing it.";
    }
    return "";
  }, []);
  const removed = useCallback((ids: string[]) => {
    update(closeProjectTabs(current.current, current.current.tabs.filter(t => t.projectId && ids.includes(t.projectId)).map(t => t.key)));
  }, [update]);
  const unsavedIn = (key: string) => Boolean(statuses.current[key]?.unsaved || statuses.current[key]?.manual);
  function closeTab(key: string) {
    update(closeProjectTabs(current.current, [key]));
    setStatus(s => { const next = { ...s }; delete next[key]; return next; });
  }
  function close(key: string) {
    // Unsaved edits get the usual question; a running session or action still keeps the tab open.
    const state = statuses.current[key];
    if (unsavedIn(key) && !state?.running) { update({ ...current.current, active: key }); setClosing({ tab: key }); return; }
    const reason = state?.closeReason;
    if (reason) { update({ ...current.current, active: key }); setNotice(reason); return; }
    closeTab(key);
  }
  async function closeWindow() {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().destroy();
  }
  /** Save every listed tab's edits, then close. A tab that cannot save is shown and nothing closes. */
  async function saveAndClose() {
    if (!closing) return;
    const keys = closing.tab ? [closing.tab] : current.current.tabs.map(t => t.key).filter(unsavedIn);
    setSaving(true);
    try {
      for (const key of keys) {
        if (!(await savers.current[key]?.())) {
          update({ ...current.current, active: key });
          setClosing(null);
          return;
        }
      }
    } finally {
      setSaving(false);
    }
    discardAndClose();
  }
  function discardAndClose() {
    const target = closing;
    setClosing(null);
    if (target?.tab) closeTab(target.tab);
    else closeWindow().catch(error => setNotice(String(error)));
  }
  useEffect(() => {
    try {
      localStorage.setItem(TABS_STORAGE_KEY, JSON.stringify({ tabs: workspace.tabs.map(({ projectId, title }) => ({ projectId, title, blank: !projectId })),
        activeIndex: workspace.tabs.findIndex(t => t.key === workspace.active),
        activeProject: workspace.tabs.find(t => t.key === workspace.active)?.projectId }));
    } catch { /* Workspaces still work when storage is unavailable. */ }
  }, [workspace]);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (Object.values(statuses.current).some(s => s.dirty)) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, []);
  useEffect(() => {
    if (!isTauri()) return;
    let disposed = false, unlisten: (() => void) | undefined;
    // With a listener, closing is left to this page: the API destroys the window unless it is
    // prevented, which needs core:window:allow-destroy in src-tauri/capabilities/main.json.
    import("@tauri-apps/api/window").then(({ getCurrentWindow }) => getCurrentWindow().onCloseRequested(event => {
      const all = Object.values(statuses.current);
      if (!all.some(s => s.unsaved || s.manual || s.running)) return;
      event.preventDefault();
      setClosing({});
    })).then(stop => { if (disposed) stop(); else unlisten = stop; }).catch(() => {});
    return () => { disposed = true; unlisten?.(); };
  }, []);
  useEffect(() => {
    if (!notice) return;
    const timeout = setTimeout(() => setNotice(""), 6000);
    return () => clearTimeout(timeout);
  }, [notice]);

  return <div className="project-workspaces">
    <div className="project-tab-strip">
      <div className="project-tabs" role="tablist" aria-label={t("Open projects")}>
        {workspace.tabs.map((tab, index) => {
          const selected = workspace.active === tab.key, state = status[tab.key];
          const title = state?.title || tab.title || t("New project");
          return <div className={`project-tab${selected ? " active" : ""}`} key={tab.key}>
            <button role="tab" id={`tab-${tab.key}`} aria-selected={selected} aria-controls={`workspace-${tab.key}`}
              tabIndex={selected ? 0 : -1} title={title} onClick={() => update({ ...current.current, active: tab.key })}
              onKeyDown={event => {
                const offset = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
                const target = event.key === "Home" ? 0 : event.key === "End" ? workspace.tabs.length - 1
                  : offset ? (index + offset + workspace.tabs.length) % workspace.tabs.length : -1;
                if (target < 0) return;
                event.preventDefault();
                const next = workspace.tabs[target]; update({ ...current.current, active: next.key });
                document.getElementById(`tab-${next.key}`)?.focus();
              }}>
              {state?.running ? <Loader2 size={13} className="spin" aria-label={t("Production running")} />
                : <span className={`project-tab-dot${state?.dirty ? " unsaved" : ""}`} />}
              <span className="project-tab-title">{title}</span>
              {state?.dirty && <span className="sr-only">{t("Unsaved changes")}</span>}
            </button>
            <button className="project-tab-close" aria-label={t("Close project tab · {name}", { name: title })}
              title={t(state?.closeReason || "Close tab")} onClick={() => close(tab.key)}><X size={12} /></button>
          </div>;
        })}
      </div>
      <button className="icon-button new-project-tab" aria-label={t("New project tab")}
        title={t(workspace.tabs.length === MAX_PROJECT_TABS ? "Up to five projects can be open. Close a tab to open another." : "New project tab")}
        disabled={workspace.tabs.length === MAX_PROJECT_TABS} onClick={() => addTab()}><Plus size={16} /></button>
      <span className="project-tab-count">{workspace.tabs.length} / {MAX_PROJECT_TABS}</span>
    </div>
    {workspace.tabs.map(tab => <div className="project-workspace" role="tabpanel" id={`workspace-${tab.key}`}
      aria-labelledby={`tab-${tab.key}`} hidden={workspace.active !== tab.key} key={tab.key}>
      <ProjectWorkspace tab={tab} active={workspace.active === tab.key} onStatus={report} onSaver={registerSaver}
        onOpenProject={openProject} onNewTab={addTab} mayRemove={mayRemove} onRemoved={removed} />
    </div>)}
    {closing && (() => {
      const tabs = closing.tab ? workspace.tabs.filter(t => t.key === closing.tab) : workspace.tabs;
      const name = (key: string, fallback?: string) => status[key]?.title || fallback || t("New project");
      const edited = tabs.filter(tab => status[tab.key]?.unsaved || status[tab.key]?.manual);
      const running = closing.tab ? [] : tabs.filter(tab => status[tab.key]?.running);
      return <div className="modal-backdrop" onKeyDown={event => { if (event.key === "Escape" && !saving) setClosing(null); }}>
        <section className="modal closing-question" role="alertdialog" aria-modal="true" aria-labelledby="closing-title" aria-describedby="closing-body">
          <div className="modal-heading"><h2 id="closing-title">{edited.length
            ? closing.tab ? t("Save changes to {name} before closing it?", { name: name(edited[0].key, edited[0].title) }) : t("Save changes before closing Studio?")
            : t("Close Studio while work is running?")}</h2></div>
          <div className="modal-content" id="closing-body">
            {edited.length > 0 && <>
              <p className="muted">{t("Your changes will be lost if you don't save them.")}</p>
              <ul className="closing-list">
                {edited.map(tab => <li key={tab.key}><strong>{name(tab.key, tab.title)}</strong>
                  <span>{[...(status[tab.key]?.unsaved || "").split("\n").filter(Boolean).map(item => t(item)),
                    ...(status[tab.key]?.manual ? [t("{editor} (save it in its window)", { editor: t(status[tab.key]!.manual!) })] : [])].join(" · ")}</span></li>)}
              </ul>
            </>}
            {running.length > 0 && <p className="closing-running">{t("Closing Studio stops the running work in: {names}.", { names: running.map(tab => name(tab.key, tab.title)).join(", ") })}</p>}
          </div>
          <div className="modal-actions">
            {edited.length > 0 && <button className="button closing-discard" disabled={saving} onClick={discardAndClose}>{t("Don't save")}</button>}
            <button className="button" disabled={saving} autoFocus={!edited.length} onClick={() => setClosing(null)}>{t("Cancel")}</button>
            {edited.length > 0
              ? <button className="button primary" disabled={saving} autoFocus onClick={saveAndClose}>
                  {saving && <Loader2 size={13} className="spin" />}{t("Save")}</button>
              : <button className="button primary" onClick={discardAndClose}>{t("Close Studio")}</button>}
          </div>
        </section>
      </div>;
    })()}
    {notice && <div className="toast workspace-notice" role="status"><span>{t(notice)}</span>
      <button className="icon-button" aria-label={t("Dismiss notification")} onClick={() => setNotice("")}><X size={14} /></button></div>}
  </div>;
}
