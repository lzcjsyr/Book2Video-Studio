# 输出格式

标题和封面按 [titles.md](titles.md) 写。

## 开场金句

3 条，每条不超过 SKILL.md 长度配置里的 `golden_quote_max_chars` 字，约 3 秒念完，用在开场卡片上。三条换三种角度，比如反常识、说中处境、清晰提问，不要和首句重复。可以用一处 `【】` 标出最关键的词，卡片会高亮它。最好的一条放第一位，默认会选它。

## raw.json

写入第一步指定的 `raw.json`，只包含这 7 个字段：

```json
{
  "source_name": "《话题短标题》",
  "video_titles": ["...", "...", "..."],
  "cover_titles": ["...", "...", "..."],
  "cover_subtitles": ["...", "...", "..."],
  "golden_quotes": ["...", "...", "..."],
  "content": "口播正文，自然段之间一个换行",
  "total_length": 0
}
```

- `source_name` 会显示在开场卡片上，书名号内最好不超过 12 字。
- `total_length` 等于 Python `len(content)`。
- 金句只放在 `golden_quotes` 里，不要拼到 `content` 开头。

用 `json.dump(..., ensure_ascii=False, indent=2)` 写入，然后运行：

```bash
python <本技能目录>/scripts/validate_raw.py <raw.json 路径>
```

报错就改完再跑；带“提示”的输出能改就改。通过后告诉用户文件路径，并列出没能核实、需要用户确认的事实。
