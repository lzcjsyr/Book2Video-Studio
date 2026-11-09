---
name: book-explainer
description: |
  将长书、长文档、PDF、EPUB、读书资料、思想经典、社科著作、商业书或研究型长文转成更有传播感的中文视频号/抖音读书口播稿，并输出项目可用的 raw JSON。只要用户提到“读书解说”“拆书稿”“视频号口播”“把这本书做成短视频”“长文档转视频脚本”“生成标题/封面/金句/raw JSON”，都应使用此 skill。
---
# 长书读书视频脚本增强版

目标：让读书口播稿像一个读透资料、懂普通人处境的人，在认真讲一件值得知道的事，而不是 AI 总结或读书报告。

所有中间文件和最终 `raw.json` 都放在 `<text目录>`。

## 正文长度配置

这是正文长度的唯一真相来源，其他文件只引用本节。

draft_min_chars: 3000
final_target_chars: 3200

- `_draft_v1.txt` 和之后的所有正文稿不低于 `draft_min_chars`。
- 最终 `content` 目标长度为 `final_target_chars`。

## 主流程

不要一轮直接输出。每一步只读该步需要的 reference：

1. **读资料**：按 `references/reading-strategy.md` 读到“够写”，边读边记 `_reading_notes.json`。不要求读完全书。
2. **定角度**：按 `references/writing-standard.md` 的「角度判断」和「读书视频原型」保存 `_angle_brief.json`。它和阅读交替进行，通过 reading-strategy 的「够写了吗」检查后再写稿。
3. **写初稿**：按 `writing-standard.md` 写 `_draft_v1.txt`。
4. **终稿修订**：按 `references/revision-workflow.md` 修出 `_draft_final.txt`，用 `references/script-quality-checklist.md` 做 L1-L4 检查，写 `_revision_audit.json`。L1 或 L2 不通过，不进入下一步。
5. **包装输出**：按 `references/titles.md` 写标题和封面，按 `references/writing-standard.md` 的「开场金句」写金句，再按 `references/output-format.md` 写出并校验 `raw.json`。`source_name` 写作品标题加书名号，如《置身事内》；`content` 来自 `_draft_final.txt`，只清理首尾空白和多余空行，不改措辞。

## 交付前确认

- 读书笔记、角度 brief、修订审计三个文件都已落盘，且与事实一致。
- `_revision_audit.json` 中 L1/L2 为 `true`。
- `raw.json` 通过 `output-format.md` 的校验。
