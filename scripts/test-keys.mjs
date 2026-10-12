import assert from "node:assert/strict";
import { test } from "node:test";
import { adcModels, distinctGaps, imageModelFits, imageModelFor, keyNeeds, unmet } from "../src/keys.ts";

const settings = {
  step1: { llm_server: "deepseek" },
  step2: { split_mode: "auto", llm_server: "kimi" },
  step4_summary: { llm_server: "deepseek" },
  step4: { llm_server: "volcengine", image_server: "doubao" },
  step6: { cover_image_server: "google_adc" },
};
const keys = (needs) => Object.fromEntries(needs.map((n) => [n.id, n.key]));

test("each step needs the key of the provider chosen in its settings", () => {
  assert.deepEqual(keys(keyNeeds(settings)), {
    writing: "DEEPSEEK_API_KEY",
    voice: "BYTEDANCE_TTS_API_KEY",
    direction: "DEEPSEEK_API_KEY",
    images: "VOLCENGINE_API_KEY",
    covers: null,
  });
});
test("segmentation needs a model key only in agent mode", () => {
  const agent = { ...settings, step2: { split_mode: "agent", llm_server: "kimi" } };
  assert.equal(keys(keyNeeds(agent)).segments, "KIMI_API_KEY");
});
test("missing keys and unsupported providers are reported; Google ADC needs no key", () => {
  const saved = { DEEPSEEK_API_KEY: true, VOLCENGINE_API_KEY: false, BYTEDANCE_TTS_API_KEY: true };
  assert.deepEqual(unmet(keyNeeds(settings, { motion: true }), saved).map((n) => n.id), ["motion_agent", "images"]);
  assert.deepEqual(unmet(keyNeeds(settings), saved, ["voice", "covers"]), []);
  // Two steps share the missing Volcengine key: one key to add.
  assert.equal(distinctGaps(unmet(keyNeeds(settings, { motion: true }), saved)), 1);
  const typo = { ...settings, step4_summary: { llm_server: "deepsek" } };
  assert.deepEqual(unmet(keyNeeds(typo), { ...saved, VOLCENGINE_API_KEY: true }).map((n) => n.id), ["direction"]);
  // Before the saved keys are known, nothing is reported as missing.
  assert.deepEqual(unmet(keyNeeds(settings), undefined), []);
});
test("static images with Google ADC need no key; only motion segments need the step 4 model", () => {
  const adc = { ...settings, step4: { llm_server: "volcengine", image_server: "google_adc" } };
  const saved = { DEEPSEEK_API_KEY: true, BYTEDANCE_TTS_API_KEY: true };
  assert.deepEqual(unmet(keyNeeds(adc), saved), []);
  assert.deepEqual(unmet(keyNeeds(adc, { motion: true }), saved).map((n) => n.id), ["motion_agent"]);
});
test("switching image service keeps a fitting model and replaces another service's model", () => {
  assert.equal(imageModelFor("google_adc", "doubao-seedream-4-0-250828"), "gemini-3.1-flash-image");
  assert.equal(imageModelFor("doubao", "gemini-3.1-flash-image"), "doubao-seedream-4-0-250828");
  assert.equal(imageModelFor("google", "gemini-3-pro-image"), "gemini-3-pro-image");
  assert.equal(imageModelFits("google_adc", "doubao-seedream-4-0-250828"), false);
});
test("the connection check covers the models sent through Google ADC", () => {
  assert.deepEqual(adcModels(settings), []);
  const adc = { step4: { image_server: "google_adc", image_model: "gemini-3.1-flash-image" }, step6: { cover_image_model: "gemini-3-pro-image" } };
  assert.deepEqual(adcModels(adc), ["gemini-3.1-flash-image", "gemini-3-pro-image"]);
  assert.deepEqual(adcModels({ ...adc, step6: { cover_image_server: "doubao", cover_image_model: "doubao-seedream-4-0-250828" } }), ["gemini-3.1-flash-image"]);
});
test("with a Claude subscription the writing and motion agents need no key; other steps still do", () => {
  const needs = keyNeeds(settings, { motion: true, subscription: true });
  const writing = needs.find((n) => n.id === "writing");
  assert.equal(writing.key, null);
  assert.equal(writing.login, "Your Claude Code login (no key)");
  assert.equal(needs.find((n) => n.id === "motion_agent").key, null);
  const saved = { BYTEDANCE_TTS_API_KEY: true };
  assert.deepEqual(unmet(needs, saved).map((n) => n.id), ["direction", "images"]);
  assert.equal(needs.find((n) => n.id === "covers").login, "Google Cloud login (no key)");
});
