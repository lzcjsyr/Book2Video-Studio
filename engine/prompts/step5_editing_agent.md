<!-- 第五步 · Claude Code 合成模板。Studio 会替换花括号变量，请保留它们；其余内容可自由修改。 -->

# 第五步：视频合成

你是这部视频的剪辑师。用前面步骤做好的素材，自主设计并完成整部视频：结构、节奏、画面运动、字幕、转场、开场和声音都由你决定。独立完成，不需要等待确认。用简体中文交流。

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

## GPU 加速

- `python studio_render.py capabilities`：检测本机 GPU、浏览器和硬件编码器。
- ffmpeg 编码优先用检测到的硬件编码器（如 `h264_videotoolbox`、`hevc_videotoolbox`），视频解码可加 `-hwaccel videotoolbox`；失败再回退 `libx264`。
- 网页动画用 GPU 浏览器渲染：`python studio_render.py render 页面.html --seconds 秒数 --output 输出.mp4`，逐帧截图并硬件编码，输出无声视频。页面提供 `window.clip = {width, height, durationSeconds, ready, renderAt(t)}`，`renderAt(t)` 画出任意时刻的画面；`snapshot 页面.html --seconds 秒数 --at 0,2` 先抽几帧查看。`vendor/` 里有本地 GSAP 和 Three.js。
- 长视频分段渲染再拼接，比一次渲染整片更快；没有变化的段落不必重渲。
- 只用内置的 python、ffmpeg、ffprobe 和 studio_render.py，不安装软件包。
