import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { translate, initialLocale } from "../src/i18n/messages.ts";
import { zh } from "../src/locales/zh-CN.ts";

const records = new Map([["studio-language", "en"]]);
globalThis.localStorage = {
  getItem: (key) => records.get(key) ?? null,
  setItem: (key, value) => records.set(key, value),
};
globalThis.document = { documentElement: { lang: "" } };
const language = await import("../src/i18n/index.ts");

test("saved language wins over system preference; invalid preferences fall back safely", () => {
  assert.equal(initialLocale("en", "zh-CN"), "en");
  assert.equal(initialLocale("zh-CN", "en-US"), "zh-CN");
  assert.equal(initialLocale(null, "zh-TW"), "zh-CN");
  assert.equal(initialLocale("invalid", "en-US"), "en");
});
test("language switches update the document and persist for the next launch", () => {
  language.setLocale("zh-CN");
  assert.equal(language.getLocale(), "zh-CN");
  assert.equal(records.get("studio-language"), "zh-CN");
  assert.equal(document.documentElement.lang, "zh-CN");
  assert.equal(initialLocale(records.get("studio-language"), "en-US"), "zh-CN");
  language.setLocale("en");
  assert.equal(language.getLocale(), "en");
  assert.equal(records.get("studio-language"), "en");
});
test("switching remains available if preference storage is unavailable", () => {
  const original = localStorage.setItem;
  localStorage.setItem = () => {
    throw new Error("unavailable");
  };
  try {
    language.setLocale("zh-CN");
    assert.equal(language.getLocale(), "zh-CN");
  } finally {
    localStorage.setItem = original;
    language.setLocale("en");
  }
});
test("both languages interpolate counts and stage names", () => {
  assert.equal(
    translate("zh-CN", "{count} segments", { count: 151 }),
    "151 段",
  );
  assert.equal(
    translate("en", "{count} segments", { count: 151 }),
    "151 segments",
  );
  assert.equal(
    translate("zh-CN", "Generate {stage}", { stage: "visuals" }),
    "生成画面",
  );
  assert.equal(
    translate("zh-CN", "Visual for segment {index}", { index: 2 }),
    "分段 2 的画面",
  );
  assert.equal(
    translate("zh-CN", "Generate {stage}", { stage: "segments" }),
    "生成分段",
  );
});
test("persisted notices, revision labels, and known service errors render in Chinese", () => {
  assert.equal(translate("zh-CN", "Step 3 failed"), "步骤 3：失败");
  assert.equal(translate("zh-CN", "Before editing raw"), "编辑文稿前");
  assert.equal(
    translate("zh-CN", "Error: The manuscript cannot be empty."),
    "文稿不能为空。",
  );
  assert.equal(
    translate("zh-CN", "Segment 12 needs its recording before rendering."),
    "分段 12 需要先生成录音，再进行渲染。",
  );
  assert.equal(
    translate(
      "zh-CN",
      "DeepSeek key saved. New jobs and new Claude Code sessions use the updated keys.",
    ),
    "DeepSeek 密钥已保存。新任务和新的 Claude Code 会话将使用更新后的密钥。",
  );
  assert.equal(translate("zh-CN", "5.5s"), "5.5秒");
});
test("unknown technical output and paths are preserved", () => {
  for (const text of [
    "Windows",
    "assets",
    "progress",
    "config.params",
    "deepseek-v4-pro",
    "C:\\clips\\videos",
    "Claude Code",
    "未知用户文稿",
  ]) {
    assert.equal(translate("zh-CN", text), text);
    assert.equal(translate("en", text), text);
  }
});
test("all static interface messages have Chinese translations", () => {
  const missing = new Set();
  for (const file of [
    "App.tsx",
    "ProjectWorkspace.tsx",
    ...fs
      .readdirSync("src/components")
      .filter((f) => f.endsWith(".tsx"))
      .map((f) => "components/" + f),
  ]) {
    const source = ts.createSourceFile(
      file,
      fs.readFileSync(path.join("src", file), "utf8"),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    function visit(node) {
      if (
        ts.isCallExpression(node) &&
        node.expression.getText(source) === "t" &&
        ts.isStringLiteral(node.arguments[0]) &&
        !zh[node.arguments[0].text]
      )
        missing.add(node.arguments[0].text);
      if (ts.isJsxText(node)) {
        const text = node.text.replace(/\s+/g, " ").trim();
        if (
          /[a-zA-Z]/.test(text) &&
          !["Book2Video", "Studio", "English"].includes(text)
        )
          missing.add("Unlocalized text: " + text);
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
  assert.deepEqual([...missing], []);
});
