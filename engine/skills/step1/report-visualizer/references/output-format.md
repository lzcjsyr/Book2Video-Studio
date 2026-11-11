# 输出格式

本文只规定交给主程序的 `raw.json` 结构，所有写作技能共用同一份。内容怎么写，见各技能自己的说明。

## raw.json

保存到会话指定的路径（通常是 `<text目录>/raw.json`），只包含这 7 个字段：

```json
{
  "source_name": "来源标题或话题名",
  "content": "口播正文",
  "total_length": 0,
  "video_titles": ["...", "...", "..."],
  "cover_titles": ["...", "...", "..."],
  "cover_subtitles": ["...", "...", "..."],
  "golden_quotes": ["...", "...", "..."]
}
```

## 字段说明

| 字段 | 程序怎么用 | 要求 |
|---|---|---|
| `source_name` | 显示在开场金句下方和项目列表里 | 非空，不带文件后缀；书名号、长度等要求见本技能说明 |
| `content` | 配音和分段的正文 | 非空；自然段之间换行；不写 Markdown、小标题或列表；不包含金句 |
| `total_length` | 正文字数 | 等于 Python `len(content)` |
| `video_titles` | 发布标题 | 候选数组 |
| `cover_titles` | 封面主标题 | 候选数组 |
| `cover_subtitles` | 封面副标题 | 候选数组 |
| `golden_quotes` | 开场金句：启用时在正文前单独播放并配字幕卡片；用户可以关掉 | 候选数组 |

候选数组：

- 每个数组 3 条，互不重复。
- 每条是一个独立的单行字符串，不要把多条候选用换行拼在一个字符串里。
- **第一条默认被选用**，最好的一条放第一位。用户可以在界面里改选。

开场金句卡片：

- 每行约 20 字，最多 4 行。
- 可以用一处 `【】` 标出最关键的词，卡片会高亮它，配音时去掉括号。不标时程序自动挑选高亮词。
- 金句只放在 `golden_quotes` 里，不要拼到 `content` 开头。

## 写入和校验

用 Python `json.dump(..., ensure_ascii=False, indent=2)` 写入，不要手动拼 JSON。写完用 `json.load` 读回，确认：

- 7 个字段齐全，没有多余字段。
- `source_name`、`content` 非空。
- 四个候选数组都是非空字符串组成的列表。
- `total_length == len(content)`。

本技能带有 `scripts/validate_raw.py` 时，再运行：

```bash
python <本技能目录>/scripts/validate_raw.py <raw.json 的绝对路径>
```

报错就改完再跑。
