import assert from "node:assert/strict";
import { test } from "node:test";
import { orderedLibraryGroups } from "../src/library.ts";

const catalog = { groups: [
  ...["writing_skills", "summary", "image_styles", "image_templates", "motion_styles", "motion_skills", "assembly_skills", "cover_styles"]
    .map((id) => ({ id, label: id, kind: "skill", items: [{ id: "example", label: "Example" }] })),
  { id: "templates", label: "Production templates", kind: "prompt", items: [
    "step1_agent.md", "step1_5_segment.md", "step4_hyperframes_agent.md", "step4_opening_bg.md",
    "step4_safety.md", "step6_cover.md", "step1_subagents/title-quote-writer.md", "step1_subagents/fact-style-reviewer.md",
  ].map((id) => ({ id, label: id })) },
] };

test("library categories follow production step order, including the Step 4 summary", () => {
  const groups = orderedLibraryGroups(catalog);
  assert.deepEqual(groups.map((g) => g.step), [1, 1, 2, 4, 4, 4, 4, 4, 4, 5, 6, 6]);
  assert.equal(groups.find((g) => g.id === "summary").step, 4);
});

test("templates are shown under their own steps without changing file IDs or API groups", () => {
  const groups = orderedLibraryGroups(catalog).filter((g) => g.id === "templates");
  assert.deepEqual(groups.map((g) => [g.step, g.items.length]), [[1, 3], [2, 1], [4, 3], [6, 1]]);
  assert.deepEqual(groups.flatMap((g) => g.items.map((i) => i.id)).sort(), catalog.groups.at(-1).items.map((i) => i.id).sort());
  assert.equal(groups.find((g) => g.step === 2).items[0].id, "step1_5_segment.md");
});

test("ordering does not mutate the catalog used by production settings", () => {
  const before = structuredClone(catalog);
  orderedLibraryGroups(catalog);
  assert.deepEqual(catalog, before);
  assert.deepEqual(orderedLibraryGroups({ groups: [] }), []);
});
