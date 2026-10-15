# Studio 精剪计划与渲染契约

`studio-payload.json` 是只读来源上下文，包含当前选定的图片 / 动态片段、录音、权威正文、音乐目录、导出设置和素材尺寸。只在此精剪工作目录内编辑；保留源文件、早期设计和已发布导出。Studio 仅发布验证成功的成片。

`plan.json` 使用 version 1 和三个内置风格之一。`scenes` 必须按原顺序包含每个来源 index 恰好一次；启用开场时也必须包含 index 0。每个场景的字段如下：

- `trim: [start, end]`：原始录音秒数，默认保留全部录音。用户未明确要求删减口播时，只能裁剪已核实的静音。
- `pause_before`：额外成片秒数，范围 0–10。暂停时场景仍可见，口播同步延迟相同时间。
- `transition`：可选 cut / crossfade / fade_black / fade_white / slide_left / slide_right / wipe_left / wipe_right / zoom_in / zoom_out；`transition_duration` 可选，范围 0.01–3 秒，且不超过本场景时长的三分之一。默认遵循图形界面设置。新场景入场时保持上一背景帧，口播时钟持续前进。
- `zoom`：静态图片重构图缩放比例，范围 1–1.4。动态片段保留选定内容并适配录音时长。
- `captions`：`{text, start, end}` 字幕条目，时间为原始录音秒数。必须完整保留口播用词，不得重叠或乱序。`caption_timing` 为 `estimated`（估算）、`aligned`（语音对齐）或 `reviewed`（已人工审阅）；初始按比例分配的时间属于估算。
- `overlays`：`{kind, text, start, end, position}`。kind 支持 `keyword`、`quote`、`chapter`、`compare`、`bars`；position 支持 `center`、`top`、`left`、`right`。可以用 `at: "精确关键词"` 代替 start，并依据字幕时间定位；跨字幕条目的关键词需要填写审阅过的数字时间。compare / bars 增加 `items: [{label, value}]`；bars 数值范围 0–100，单位写入 text。章节卡可用 `during_pause: true` 在延迟的口播开始前入场。卡片最多 80 字，章节卡最多 36 字，侧边卡最多 40 字，均不超过三行。对比和图表位于 center / top，含 2–6 个标签，每个最多 20 字。不得编造事实。
- 可选 `html`：当前工作目录内自定义 HTML 的相对路径。声明 window.clip={width,height,durationSeconds,ready,renderAt}，ready 为 Promise，renderAt(t) 在任意时间点重建画面并可返回 Promise。宽高和时长必须匹配 check 解析结果。使用 vendor/gsap.min.js、vendor/three/ 本地依赖；不加入配音。视频素材通过 window.clipTools.seekVideo(video,seconds) 精确 seek 并等待解码，不能用自由运行播放作为时间源。Canvas/WebGL 中的文字避让需抽帧检查。

`music: [{track, start, end?, offset?, volume}]` 按最终视频秒数安排目录内的音乐，volume 范围 0–0.6；可选 offset 是音乐源偏移，便于衔接。图形界面的闪避和响度控制仍生效。`sfx: [{sound, at, volume}]` 也使用最终视频秒数，volume 范围 0–0.5；内置程序化音效为 click / whoosh / impact / chime。`notes` 记录剪辑选择、新编写的标签和字幕时间限制。

## 检查、预览与输出

- `python studio_edit.py check`：验证计划并写出解析后的时间线；start 为成片秒数，start_frame 为帧索引。
- `python studio_edit.py align`：使用本地可用的 Whisper CLI / 模型，将权威正文对齐录音并保留正文。会报告转写相似度，低匹配率需审阅。
- `python studio_edit.py preview`：实渲染开头短片到 `previews/preview.mp4`，并生成联系表。`preview --start N --seconds S` 可选择其他部分（成片秒数）。查看真实帧、听取声音，修复后再次验证。
- `python studio_edit.py render`：在精剪目录内写出候选主视频。完成全片检查后运行 publish；界面显示已生成的主片及可选 3:4 副本。

长视频按不超过两分钟的块渲染。边界按帧对齐，包含一个预滚帧，再按帧数裁剪。跨边界的强调层应出现在两侧局部时间线中。画面、口播、字幕和音效采用同一次速度变换。确认帧数、尺寸、音视频时长、完整解码和接缝帧后才说明可交付；FFmpeg 创建了文件不代表通过验证。

## Agent 渲染与质量循环
<!-- AGENT_RENDER_LOOP_VERSION: 2 -->

`python studio_edit.py capabilities` 实测 OS、架构、Chrome GPU、视频解码支持，以及目标尺寸/帧率下的编码器。先读取 capabilities.json，优先使用硬件；软件回退应有实测失败原因或用户指示。
`python studio_edit.py render` 在 build/ 写入已验证 candidate.mp4、candidate.qa.json、captions.srt 及启用时的 candidate_3x4.mp4。
完成全片的抽帧与质量检查后运行 `python studio_edit.py publish`。它会拒绝源码、计划、录音、素材、设置或产物已改变的候选，不重新渲染。
未改动的已验证完整渲染会复用。修改后重新预览/渲染；`--force` 可重建。用户无需在会话结束后另行启动渲染。
预览只写 previews/，不会覆盖最终视频。保留正文和素材，只通过计划调整时间、强调、字幕和声音。不要声称自动帧数/时长验证能证明审美质量；必须读抽帧，并如实报告未听审或估算字幕等限制。
共享工具支持 `--browser-mode auto/hardware/software` 和 `--encoder auto/software`。机器判断以实测为准，不靠型号名单。
