# 修订流程

只做一次正式修订：先写完整初稿，再从初稿修到终稿，并在这次修订中完成 L1-L4 质检。本文只规定文件、步骤和审计格式；写作标准见 `writing-standard.md`，检查项见 `script-quality-checklist.md`。

所有文件都放在 `<text目录>`，按顺序生成：

```text
_angle_brief.json
_draft_v1.txt
_draft_final.txt
_revision_audit.json
```

## 1. `_draft_v1.txt`

按 `_angle_brief.json` 和 `writing-standard.md` 写完整初稿，忠于资料，覆盖核心人物、事件、概念、机制和结论边界，长度达到 `draft_min_chars`。初稿不要求表达打磨到位，但主线和证据要成立。

## 2. `_draft_final.txt`

1. 先完整读一遍 `_draft_v1.txt`，不凭记忆改。
2. 把 `_draft_v1.txt` 复制为 `_draft_final.txt`，在副本上用 Edit 局部修改，每次替换一个清楚的语义块。
3. 对照 `writing-standard.md` 修改，用 `script-quality-checklist.md` 逐层检查，一直改到 L1、L2 通过。
4. 关键修改后复查字数，不能低于 `draft_min_chars`。

只有初稿主线明显错误、局部修改救不回来时，才允许整篇重写；重写后重新检查字数、忠实性和 L1-L4。

## 3. `_revision_audit.json`

只记录初稿到终稿之间的真实差异。没有实质差异就不算完成，继续改。

```json
{
  "revision_basis": {
    "meaningful_difference": true,
    "draft_paths": {
      "angle_brief": "_angle_brief.json",
      "v1": "_draft_v1.txt",
      "final": "_draft_final.txt"
    },
    "diff_summary": "初稿到终稿的核心变化"
  },
  "changes": [
    {"before": "原句", "after": "新句", "reason": "修改原因"}
  ],
  "quality_check_summary": {
    "l1_hard_rules_passed": true,
    "l2_oral_structure_passed": true,
    "l3_content_quality_passed": true,
    "l4_human_feel_passed": true,
    "remaining_issues": []
  },
  "final_checks": {
    "content_length": 0,
    "forbidden_expressions_found": []
  }
}
```

- `changes` 至少 5 条，每条的 `before` 和 `after` 都能在初稿和终稿中原样找到。
- 这些修改合起来要涉及：开头、结构调整、主线或节奏、口语化、传播点（场景、评论点、金句或“被说中”），以及忠实性、禁用表达、长度或格式中的至少一项。
- 不要只写“已优化”“已检查”“符合要求”。
