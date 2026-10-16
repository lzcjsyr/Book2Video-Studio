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
  function close(key: string) {
    const reason = statuses.current[key]?.closeReason;
    if (reason) { update({ ...current.current, active: key }); setNotice(reason); return; }
    update(closeProjectTabs(current.current, [key]));
    setStatus(s => { const next = { ...s }; delete next[key]; return next; });
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
    import("@tauri-apps/api/window").then(({ getCurrentWindow }) => getCurrentWindow().onCloseRequested(event => {
      if (!Object.values(statuses.current).some(s => s.dirty)) return;
      event.preventDefault();
      setNotice("Save edits in all project tabs before closing Studio.");
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
      <ProjectWorkspace tab={tab} active={workspace.active === tab.key} onStatus={report}
        onOpenProject={openProject} onNewTab={addTab} mayRemove={mayRemove} onRemoved={removed} />
    </div>)}
    {notice && <div className="toast workspace-notice" role="status"><span>{t(notice)}</span>
      <button className="icon-button" aria-label={t("Dismiss notification")} onClick={() => setNotice("")}><X size={14} /></button></div>}
  </div>;
}
