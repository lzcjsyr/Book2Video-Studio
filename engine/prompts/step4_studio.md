保留命名的 `<!-- section: name -->` 分节标记及必需的模板变量；可以编辑各节内的说明。

<!-- section: segment -->
{agent_prompt}

### Studio 工作目录（段落 {segment_index}）

- 本段所有文件只放在 `{work_dir}` 内：`index.html`、`snapshots/`，以及所需辅助文件。
- 每条 render.py 命令（check、snapshot）都必须在该目录内运行：先 `cd "{work_dir}"`。这样 `python render.py check --snapshots` 和 `python render.py snapshot` 的关键帧会写入 `{snapshots_dir}`，Studio 会将其显示为本段预览。
- 在本段目录运行 `python render.py capabilities`、`python render.py preview`、`python render.py render` 完成制作循环。只写本段 video.mp4，应用会自动识别当前片段。
- 不要在 images/ 根目录写入 segment_*.mp4，不要改动其他图片或片段版本。
<!-- section: new -->
- 这是本段新建的动态片段：从头设计 `index.html`。本段之前的版本会保留，供用户比较和选择。
<!-- section: single -->
当前任务是所选段落。在完成修改前保留现有构图。由你检查并渲染保存的 HTML。

{segments}
<!-- section: multi -->
需要依次制作 {segment_count} 个段落。每段是独立任务，分别列在下方；某节中的“当前段落”仅指该节对应的段落。完成一段的编写、检查和修复后，再开始下一段。以下全部文件均存在且通过检查，才算完成：

{tasks}

完成修改前保留现有构图。由你检查并渲染保存的 HTML。

{segments}
<!-- section: target -->
{task_number}. 段落 {segment_index} → `{target_path}`
<!-- section: task -->
# 段落 {segment_index}（第 {task_number} / {segment_count} 个任务）

{agent_prompt}

# 段落 {segment_index} 结束
<!-- section: finish -->
<!-- AGENT_RENDER_LOOP_VERSION: 2 -->
你必须在本会话完成每个段落的预览、视觉检查、必要修复和最终渲染。确认每段 video.mp4 与当前 HTML 一致且 render-report.json 的 verified 为 true。
完成后简要说明已生成的视频、检查结果、硬件或回退路径及尚未验证的质量限制。用户可以继续提出修改或结束会话；结束会话不会重新渲染。
应用已内置 Chrome、FFmpeg、GSAP 和 Three.js。使用本段 render.py 工具，不安装包，不硬编码其他项目路径。把能力报告和截图留在文件中，只将关键诊断纳入上下文。
