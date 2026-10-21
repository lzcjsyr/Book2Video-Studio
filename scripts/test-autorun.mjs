import assert from "node:assert/strict";
import { test } from "node:test";
import { autoNeeds, planText } from "../src/autorun.ts";
import { translate } from "../src/i18n/messages.ts";

const en = (key, values) => translate("en", key, values);
const zh = (key, values) => translate("zh-CN", key, values);
const plan = (steps) => ({ blocked: null, segments: 4, steps });

test("each step's plan reads as what the next run will make", () => {
  const settings = { three_by_four: { enabled: true }, step5: { assembly_method: "standard" }, step6: { cover_method: "api" } };
  assert.equal(planText(en, { step: "2", run: true }, 0, settings), "Split the manuscript into segments");
  assert.equal(planText(en, { step: "2", run: false }, 1, settings), "Up to date · 1 segment");
  assert.equal(planText(en, { step: "3", run: true, after: ["2"] }, 0, settings), "Record every segment");
  assert.equal(planText(en, { step: "3", run: true, after: ["2"] }, 4, settings), "Record new and changed segments");
  // Everything again remakes every segment, whatever exists now.
  assert.equal(planText(en, { step: "3", run: true, after: ["2"] }, 4, settings, true), "Record every segment");
  assert.equal(planText(en, { step: "3", run: true, segments: 2, opening: true }, 4, settings), "Record 2 segments and the opening");
  assert.equal(planText(en, { step: "3", run: true, segments: 0, opening: true }, 4, settings), "Record the opening");
  assert.equal(planText(en, { step: "4", run: true, images: 3, motion: 1, opening: true, summary: "write" }, 4, settings),
    "Content summary first, then make 3 images, 1 motion clip, the opening background");
  assert.equal(planText(en, { step: "4", run: true, images: 1 }, 4, settings), "Make 1 image");
  assert.equal(planText(en, { step: "5", run: true, jobs: ["three_by_four"] }, 4, settings), "Build the 3:4 copy");
  assert.equal(planText(en, { step: "5", run: true, jobs: ["assembly"] }, 4, { ...settings, step5: { assembly_method: "agent" } }),
    "Claude Code edits the video; Grist draws the 3:4 copy");
  assert.equal(planText(en, { step: "6", run: true, jobs: ["covers"] }, 4, { step6: { cover_method: "agent" } }), "Render the saved HTML covers");
  assert.equal(planText(en, { step: "6", run: false, off: true }, 4, settings), "Left out of this run");
  assert.equal(planText(zh, { step: "4", run: true, images: 2, opening: true }, 4, settings), "制作 2 张图片、开场背景");
});

test("a run needs the keys of the steps it makes, and only those", () => {
  const settings = { step2: { split_mode: "punctuation" }, step5: { assembly_method: "standard" } };
  const fresh = plan([{ step: "2", run: true }, { step: "3", run: true, after: ["2"] }, { step: "4", run: true, after: ["2"] },
    { step: "5", run: true, after: ["2", "3", "4"] }, { step: "6", run: true }]);
  assert.deepEqual(autoNeeds(fresh, settings), ["voice", "images", "direction", "covers"]);
  // Motion segments (or agent segmentation, which may choose motion) need the motion agent after new segments.
  assert.ok(autoNeeds(fresh, settings, { motion: true }).includes("motion_agent"));
  assert.deepEqual(autoNeeds(fresh, { ...settings, step2: { split_mode: "agent" } }).slice(0, 1), ["segments"]);
  // An existing summary is not written again unless the plan says so.
  assert.ok(!autoNeeds(fresh, settings, { summary: true }).includes("direction"));
  const onlyVideo = plan([{ step: "2", run: false }, { step: "3", run: false }, { step: "4", run: false }, { step: "5", run: true, jobs: ["assembly"] }, { step: "6", run: false }]);
  assert.deepEqual(autoNeeds(onlyVideo, { step5: { assembly_method: "agent" } }), ["assembly_agent"]);
  assert.deepEqual(autoNeeds(onlyVideo, settings), []);
  assert.deepEqual(autoNeeds(null, settings), []);
});
