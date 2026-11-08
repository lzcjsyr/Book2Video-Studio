# 输出格式

标题和封面按 [titles.md](titles.md) 写。

## 开场金句

3 条，和同一位置的标题是一套。用正文里的原句，或者不改意思的缩短版。

## raw.json

写入指定的 `raw.json`，只包含这 7 个字段：

```json
{
  "source_name": "《书名》",
  "video_titles": ["发布标题1", "发布标题2", "发布标题3"],
  "cover_titles": ["封面主标题1", "...", "..."],
  "cover_subtitles": ["封面副标题1", "...", "..."],
  "golden_quotes": ["开场金句1", "...", "..."],
  "content": "口播正文，自然分段",
  "total_length": 0
}
```

- `source_name` 带书名号，不带文件后缀。
- 四个数组各 3 条、不重复。
- `content` 950-1150 字；`total_length` 等于 Python `len(content)`。
- 用 `json.dump(..., ensure_ascii=False, indent=2)` 写入，然后运行校验：

```bash
python <本技能目录>/scripts/validate_raw.py <raw.json 的绝对路径>
```

校验通过后，告诉用户文件路径，并列出稿子里需要用户确认的事实。
