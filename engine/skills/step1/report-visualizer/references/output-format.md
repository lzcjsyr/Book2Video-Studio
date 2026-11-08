# 输出格式

`_draft_final.txt` 定稿、数据台账里没有 `unchecked` 后，生成标题、封面文案和最终 raw JSON。

标题和封面按 [titles.md](titles.md) 写。

## raw.json

保存到用户指定的路径：

```json
{
  "source_name": "报告本身标题（不带后缀，如：腾讯2025年第四季度财报）",
  "video_titles": ["...", "...", "..."],
  "cover_titles": ["...", "...", "..."],
  "cover_subtitles": ["...", "...", "..."],
  "golden_quotes": ["3条解说金句，直击商业本质或经济因果的真话"],
  "comment_hook_options": ["3条评论引导，抛出利益分歧、行业争议或民生对照，促使观众争论"],
  "share_hook_options": ["3条转发引导，提供社交货币，让人想发给同行、老板或家人"],
  "content": "完整口播终稿，按 SKILL.md 的 final_target_chars 控制字数，无 Markdown，所有数字与数据对照表完全吻合",
  "total_length": 0
}
```

- 每个候选数组各 3 条，互不重复。
- `content` 来自 `_draft_final.txt`，保留自然段落换行，可清理行首行尾空白和多余连续空行。
- `total_length` 等于 `content` 的实际字符数。
- 用 Python `json.dump(..., ensure_ascii=False, indent=2)` 写入，不要手动拼 JSON；再用 `json.load` 读回验证。
