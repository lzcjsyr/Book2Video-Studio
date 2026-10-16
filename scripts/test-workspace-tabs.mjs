import assert from "node:assert/strict";
import { test } from "node:test";
import { closeProjectTabs, MAX_PROJECT_TABS, newProjectTab, openProjectTab, restoreTabs } from "../src/workspace-tabs.ts";

test("one workspace per project, with a hard five-tab limit", () => {
  let state = restoreTabs(null);
  for (let i = 1; i <= MAX_PROJECT_TABS; i++) state = openProjectTab(state, `p${i}`, state.active);
  assert.equal(state.tabs.length, 5);
  assert.equal(openProjectTab(state, "sixth", state.active), null);
  const existing = openProjectTab(state, "p2", state.active);
  assert.equal(existing.tabs.length, 5);
  assert.equal(existing.active, state.tabs[1].key);
});
test("closing an inactive tab keeps the active project; closing the last opens a start page", () => {
  let state = restoreTabs(null);
  state = openProjectTab(state, "one", state.active);
  state = openProjectTab(state, "two", state.active);
  const active = state.active;
  state = closeProjectTabs(state, [state.tabs[0].key]);
  assert.equal(state.active, active);
  state = closeProjectTabs(state, [active]);
  assert.equal(state.tabs.length, 1);
  assert.equal(state.tabs[0].blank, true);
});
test("new projects reuse their reserved empty tab", () => {
  const tab = newProjectTab(true);
  const state = openProjectTab({ tabs: [tab], active: tab.key }, "created", tab.key, "1");
  assert.equal(state.tabs.length, 1);
  assert.equal(state.tabs[0].key, tab.key);
  assert.equal(state.tabs[0].initialStage, "1");
  assert.equal(state.tabs[0].create, false);
});
test("restoring a new-project tab keeps the start screen active", () => {
  const state = restoreTabs(JSON.stringify({ tabs: [{ projectId: "one" }, { blank: true }], activeIndex: 1 }));
  assert.equal(state.tabs.length, 2);
  assert.equal(state.tabs[1].key, state.active);
  assert.equal(state.tabs[1].blank, true);
});
test("native WebViews without randomUUID can still open independent tabs", () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: undefined });
  try {
    assert.notEqual(newProjectTab().key, newProjectTab().key);
    assert.equal(restoreTabs(null).tabs.length, 1);
  } finally { Object.defineProperty(globalThis, "crypto", descriptor); }
});
test("restoration validates, deduplicates and caps stored projects", () => {
  const saved = { tabs: [null, {}, { projectId: 1 }, ...Array.from({ length: 8 }, (_, i) => ({ projectId: `p${i}`, draft: "never restore drafts" })), { projectId: "p1" }], activeProject: "p2" };
  const state = restoreTabs(JSON.stringify(saved));
  assert.equal(state.tabs.length, 5);
  assert.equal(state.tabs.find(t => t.key === state.active).projectId, "p2");
  assert.ok(state.tabs.every(t => !("draft" in t)));
  for (const invalid of ["broken", "null", "[]", '{}', '{"tabs":{}}']) assert.equal(restoreTabs(invalid).tabs.length, 1);
});
