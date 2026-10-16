export const MAX_PROJECT_TABS = 5;
export const TABS_STORAGE_KEY = "studio-project-tabs";

export interface ProjectTab {
  key: string;
  projectId?: string;
  title?: string;
  blank?: boolean;
  create?: boolean;
  initialStage?: "1";
}
export interface WorkspaceTabs { tabs: ProjectTab[]; active: string }
export interface WorkspaceStatus {
  projectId?: string;
  title: string;
  dirty: boolean;
  running: boolean;
  closeReason: string;
}

let nextKey = 0;
// Some native WebView origins expose getRandomValues but not randomUUID.
const tabKey = () => globalThis.crypto?.randomUUID?.() || `workspace-${Date.now()}-${++nextKey}`;
export const newProjectTab = (create = false): ProjectTab => ({ key: tabKey(), blank: true, create });

/** Restore only project identities; drafts and running work are never serialized. */
export function restoreTabs(value: string | null): WorkspaceTabs {
  try {
    const saved = JSON.parse(value || "null");
    const seen = new Set<string>();
    const tabs: ProjectTab[] = [];
    for (const item of Array.isArray(saved?.tabs) ? saved.tabs : []) {
      if (item?.blank === true && !item.projectId) tabs.push(newProjectTab());
      else {
        if (typeof item?.projectId !== "string" || !item.projectId || seen.has(item.projectId)) continue;
        seen.add(item.projectId);
        tabs.push({ key: tabKey(), projectId: item.projectId, title: typeof item.title === "string" ? item.title : undefined });
      }
      if (tabs.length === MAX_PROJECT_TABS) break;
    }
    if (tabs.length) return { tabs, active: (tabs.find(t => t.projectId && t.projectId === saved.activeProject)
      || tabs[Number.isInteger(saved.activeIndex) ? saved.activeIndex : 0] || tabs[0]).key };
  } catch { /* An old or invalid preference falls back to the previous start screen. */ }
  const tab = { key: tabKey() };
  return { tabs: [tab], active: tab.key };
}

/** Open once, reusing an empty tab. null means the five-workspace limit was reached. */
export function openProjectTab(state: WorkspaceTabs, projectId: string, from: string, initialStage?: "1"): WorkspaceTabs | null {
  const existing = state.tabs.find(t => t.projectId === projectId);
  if (existing) return { ...state, active: existing.key };
  const empty = state.tabs.find(t => t.key === from && !t.projectId);
  if (empty) return { tabs: state.tabs.map(t => t.key === empty.key ? { ...t, projectId, blank: false, create: false, initialStage } : t), active: empty.key };
  if (state.tabs.length >= MAX_PROJECT_TABS) return null;
  const tab = { ...newProjectTab(), projectId, blank: false, initialStage };
  return { tabs: [...state.tabs, tab], active: tab.key };
}

export function closeProjectTabs(state: WorkspaceTabs, keys: string[]): WorkspaceTabs {
  const position = state.tabs.findIndex(t => t.key === state.active);
  const tabs = state.tabs.filter(t => !keys.includes(t.key));
  if (!tabs.length) tabs.push(newProjectTab());
  return { tabs, active: tabs.some(t => t.key === state.active) ? state.active : tabs[Math.min(Math.max(0, position - 1), tabs.length - 1)].key };
}
