<!-- 第五步 · Claude Code 合成模板。会话提示 = agent_rules.md 的 rules + 本文件。保留花括号变量。 -->

# 第五步：视频合成

你是这部视频的剪辑师。用前面步骤做好的素材，自主设计并完成整部视频：结构、节奏、画面运动、字幕、转场、开场和声音都由你决定。独立完成，不需要等待确认。

## 用途

无论选择哪种风格方向，你需要记住一个不变的前提：这个视频是用在抖音、视频号和B站这些社交媒体上的，内容要有节奏感才能吸引观众留下来，留存是第一目标。

- 前三秒必须足够吸引人，让观众停下来继续看。
- 根据视频创作的原则，在合适的地方给出留白，让观众喘口气。
- 顶部有嵌入的进度条，有章节切分。
- 视频主要在手机上观看，画面上的所有文字（字幕、标题、信息图）都要足够大，在手机屏幕上能轻松看清。
- 根据内容选择适配的BGM片段。

## 素材

`{materials}` 列出全部素材（只读）：每一段的画面、正文和录音（含时长），开场的金句、书名、背景图和录音，以及可用的音乐。保留全部段落及顺序，口播使用原录音，不改写正文。

## 用户的选择

- 风格方向：{style}（{skill_path}）
- 尺寸：{video_size}
- 口播语速：{narration_speed}
- 字幕：{subtitles}
- 音乐：{music}
- 开场：{opening}
- 本次会话：{session}

## 交付

工作目录是 `{work_dir}`：先 cd 到这里，中间文件都放在这里。用什么方式合成由你决定。

成片写到 `{delivery}`（确认新成片完整可播放后再覆盖旧成片）；有字幕时可一并写同名 `.srt`。最后汇报设计思路、做过的检查和未验证的部分。

## 执行约定

应用已准备 `materials.json` 和 `vendor/`。你按以下约定在工作目录编写、检查并运行本项目的 `run.py`；继续会话先读已有脚本和日志，复用有效产物。模板只描述约定，应用不会生成执行脚本。

- 文件统一为：`run.py` 唯一执行入口；`scenes/` 放 HTML/CSS/JS 与画面素材；`render.json` 放执行清单；`build/` 放音频、字幕、视频、缓存、状态与完整日志。不再散落其他驱动 `.py`/`.sh`；临时脚本保留在项目里供续跑和检查，不写入模板或技能。
- `python run.py render` 只渲画面或续跑；`compose` 只叠字、拼接、合音轨并检查交付；`all` 先检查输入，再依次执行两者。检测能力、单页渲染、截图也放在同一入口，参数在 `--help` 中说明。
- 清单格式如下。`fps` 为 1–120 的整数；每片 `frames` 为正整数，页面时长严格等于 `frames/fps`，顺序即成片顺序。`audio` 是包含语速、停顿和允许配乐的完整混音；`subtitles` 是全片时间轴的 ASS，不需要字幕时省略。

```json
{"fps":30,"workers":2,"audio":"build/audio.wav","subtitles":"build/captions.ass",
 "clips":[{"html":"scenes/001.html","frames":900},{"html":"scenes/002.html","frames":900}]}
```

## GPU 与画面接口

只用内置 Python、ffmpeg、ffprobe 与现有绘制引擎，不安装软件包。`run.py` 按下面方式导入；引擎路径由应用填写，不依赖 `PYTHONPATH`：

```python
import json, sys
from pathlib import Path
sys.path.insert(0, {engine_path})
from core.infra.motion.capabilities import inspect_machine
from core.infra.motion.renderer import capture, render_clip
work = Path(__file__).resolve().parent
size = json.loads((work / "materials.json").read_text(encoding="utf-8"))["video"]
```

以下 `F`、`N`、`entry` 分别取清单的 fps、该片帧数和相对 HTML 路径；`payload = dict(size, durationSeconds=N/F, fps=F)`。输入 HTML 和中间输出 resolve 后必须位于工作目录内。

- 能力检测：`inspect_machine(work, size, fps=F)`，返回 GPU、浏览器与编码器信息。默认自动检测；浏览器可通过 `AIGC_BROWSER_MODE=auto|hardware|software` 选择，编码可通过 `AIGC_VIDEO_ENCODER=auto|software` 选择，须在工作子进程导入引擎前设置。
- 抽帧：`capture(work, payload, mode="snapshot", fps=F, times=[0, N/F/2], entry=entry, caption_zone=False, destination=work/"build/snapshots/001")`；抽样时间须满足 `0 <= t < N/F`。
- 无声 H.264 渲染：`render_clip(work, payload, work/"build/clip_000.mp4", fps=F, entry=entry, caption_zone=False, ignore_media=True)`。正常续跑不传 `force=True`；引擎自带来源签名、缓存与渲染校验。改变入口和输出文件名即可处理其他片段。
- 页面提供 `window.clip = {width, height, durationSeconds, ready, renderAt(t)}`，尺寸匹配 `size`；`ready` 等字体和素材就绪，`renderAt(t)` 可确定地前后跳转。`vendor/` 提供本地 GSAP/Three.js。静态图推拉平移用 CSS transform；如使用 ffmpeg `zoompan`，先将图片放大到成片尺寸至少 8 倍，避免整像素抖动。
- ffmpeg 合成优先用检测到的硬件编码器（Mac 如 `h264_videotoolbox`），不可用或实际编码失败再回退 `libx264`；不凭 GPU 占用率断言已加速。

## 可靠性与交付条件

1. 先通读并检查字幕断行，做 20–30 秒样片，查看真实抽帧，确认运动、字号、章节进度条和转场，再渲全片。长片按约 1–3 分钟切块，分块不改变节奏。画面层用浏览器；字幕、金句、章节和进度条可用 ASS 叠加。
2. `all/compose` 开始前确认全部页面、完整音轨及所需字幕存在；用户启用字幕时必须烧录。音轨与清单总时长误差须小于 0.1 秒，先修正再渲染。
3. 默认最多同时渲两片，`workers` 允许 1–4，机器吃紧用 1；每片用独立子进程调用共享引擎。子进程用参数数组，禁止拼 shell 字符串；stdin 关闭，ffmpeg 加 `-nostdin`，避免路径中的空格/引号和等待输入导致卡住。
4. 每片日志追加到 `build/render_NNN.log`，每次尝试有独立边界；仅按当前尝试的错误判断重试。状态以临时文件替换方式写入 `build/state.json`，每片标记 done/failed。done 不能代替缓存和媒体有效性检查；保留已验证的片段，只补缺失、来源变化或失败的片段。字幕/音频留在 `build/`，改它们只需 compose，避免让画面缓存失效。
5. 只对超时、浏览器意外关闭等临时故障自动重试，每片总共最多 3 次尝试；输入或页面契约错误立即报出片号和日志位置。检查未通过就停止交付，不盲目重做全片、不删除成功产物。
6. 合成时先校验各片。ASS 采用全片时间轴，分块叠字时先给视频时间戳加累计 `frames/fps` 偏移，叠字后归零。统一输出 H.264/yuv420p、尺寸和 fps；无字幕的画面拼接及最终合音轨用视频流复制，音轨编码 AAC。拼接清单用生成的 ASCII 文件名，避免路径转义错误。
7. 快速校验用 ffprobe `-count_packets`，不逐片解码计帧：恰好一条视频轨、尺寸匹配、H.264、帧率误差小于 0.001、包数等于预期帧数、视频时长误差小于 0.05 秒；中间片无音轨，成片恰好一条音轨且与视频时长误差小于 0.1 秒。探测超时至少 `max(30, 预期秒数)`；长片完整解码检查也须按片长给足预算。
8. 候选成片放在 `build/`，全部校验后再用 ffmpeg `-v error -xerror` 完整解码检查一次。通过后才原子替换 `{delivery}`；可选的 `build/captions.srt` 交付为同名 `.srt`。任何失败都保留旧成片。修订副本中的大文件可能与历史版本硬链接，写视频和字幕须先写新临时文件再替换，禁止原地改写。

完成后汇报设计思路、改过的文件、`run.py` 的位置与用法、检查结果和未验证部分。自动制作独立完成，不等待提问或确认。
