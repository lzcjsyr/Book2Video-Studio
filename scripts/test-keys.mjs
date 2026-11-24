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
    voice: "BYTEDANCE_TTS_API_KEY",
    direction: "DEEPSEEK_API_KEY",
    images: "VOLCENGINE_API_KEY",
    covers: null,
  });
});
test("old intelligent segmentation settings need no key", () => {
  const agent = { ...settings, step2: { split_mode: "agent", llm_server: "kimi" } };
  assert.deepEqual(keyNeeds(agent), keyNeeds(settings));
});
test("local VoxCPM2 does not require a cloud TTS key", () => {
  assert.equal(keyNeeds({ ...settings, step3: { tts_provider: "voxcpm2" } }).some((n) => n.id === "voice"), false);
  assert.equal(keys(keyNeeds({ ...settings, step3: { tts_provider: "bytedance" } })).voice, "BYTEDANCE_TTS_API_KEY");
});
test("missing keys and unsupported providers are reported; Google ADC needs no key", () => {
  const saved = { DEEPSEEK_API_KEY: true, VOLCENGINE_API_KEY: false, BYTEDANCE_TTS_API_KEY: true };
  assert.deepEqual(unmet(keyNeeds(settings), saved).map((n) => n.id), ["images"]);
  assert.deepEqual(unmet(keyNeeds(settings), saved, ["voice", "covers"]), []);
  assert.equal(distinctGaps(unmet(keyNeeds(settings), saved)), 1);
  const typo = { ...settings, step4_summary: { llm_server: "deepsek" } };
  assert.deepEqual(unmet(keyNeeds(typo), { ...saved, VOLCENGINE_API_KEY: true }).map((n) => n.id), ["direction"]);
  // Before the saved keys are known, nothing is reported as missing.
  assert.deepEqual(unmet(keyNeeds(settings), undefined), []);
});
test("Google ADC needs no image key", () => {
  const adc = { ...settings, step4: { llm_server: "volcengine", image_server: "google_adc" } };
  const saved = { DEEPSEEK_API_KEY: true, BYTEDANCE_TTS_API_KEY: true };
  assert.deepEqual(unmet(keyNeeds(adc), saved), []);
});
test("switching image service keeps a fitting model and replaces another service's model", () => {
  assert.equal(imageModelFor("google_adc", "doubao-seedream-4-0-250828"), "gemini-3.1-flash-image");
  assert.equal(imageModelFor("doubao", "gemini-3.1-flash-image"), "doubao-seedream-5-0-pro-260628");
  assert.equal(imageModelFor("google", "gemini-3-pro-image"), "gemini-3-pro-image");
  assert.equal(imageModelFits("google_adc", "doubao-seedream-4-0-250828"), false);
});
test("the connection check covers the models sent through Google ADC", () => {
  assert.deepEqual(adcModels(settings), []);
  const adc = { step4: { image_server: "google_adc", image_model: "gemini-3.1-flash-image" }, step6: { cover_image_model: "gemini-3-pro-image" } };
  assert.deepEqual(adcModels(adc), ["gemini-3.1-flash-image", "gemini-3-pro-image"]);
  assert.deepEqual(adcModels({ ...adc, step6: { cover_image_server: "doubao", cover_image_model: "doubao-seedream-4-0-250828" } }), ["gemini-3.1-flash-image"]);
});
test("local agents do not use app API keys and HTML covers omit ADC checks", () => {
  for (const cli of ["claude", "agy"]) {
    const agent = { ...settings, agents: { default: cli }, step5: { assembly_method: "agent" },
      step6: { cover_method: "agent", cover_image_server: "google_adc", cover_image_model: "gemini-3-pro-image" } };
    assert.deepEqual(keyNeeds(agent).map(n => n.id), ["voice", "direction", "images"]);
    assert.deepEqual(adcModels(agent), []);
  }
});
