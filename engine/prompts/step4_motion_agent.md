<!-- 第四步 · Claude Code 段落动画模板。会话提示 = agent_rules.md 的 rules + 本文件的 main。 -->
<!-- {segments} 由每个所选段落的 segment 节拼成；新片段的 {new} 填入 new 节。保留 section 标记和花括号变量。 -->

<!-- section: main -->
# 第四步：段落动画

为下面 {segment_count} 个段落各做一段 HTML 动画，并渲染成静音视频。一段做完（编写、检查、修复、渲染）再做下一段；每段只用自己的正文和时长。用户没要求时，保留已有构图。

{segments}

## 内容与设计

- 从本段正文提炼关键词：主词 2–8 个汉字，辅助短语最多 1 个（4–12 字），对比词可选、最多 2 个。
- 画面中文总共不超过 28 字，不照搬旁白，连续引用原文不超过 12 字。标题和摘要只作语境，不添加新事实。
- 先规划阅读路径、焦点和动作关系，再写 HTML。用对比、因果、数量或空间关系表达观点，不只摆文字卡片；不留孤立数字或互不相关的信息块。
- 每段一个主要动作，内容随叙述逐步出现，结尾留出阅读时间。可用 HTML/CSS/SVG、GSAP、Canvas 或 Three.js，不强制 3D。
- 根元素 #segment-root 铺满画布、无滚动条；前景内容放在 #content-wrapper，占画面上方 80%，底部 20% 留给字幕（纯背景装饰可以延伸进去）。
- 中文字体用 "PingFang SC", "Noto Sans SC", sans-serif，只用本机字体或本地文件。

## 手机可读

视频主要在手机上看。以 2560×1440 为基准：主关键词至少 200px，辅助短语至少 72px，核心数字至少 320px，其他尺寸按比例缩放；主字至少是辅助字的 1.8 倍。信息文字不透明度至少 0.78，标签至少 0.62，低于 0.4 只用于装饰。每段至少一个清晰的焦点色。

## 片段契约

- index.html 读取 `window.clipPayload`；直接用浏览器打开时，用内嵌的本段 payload 作默认值。
- 定义 `window.clip = {{ width, height, durationSeconds, ready, renderAt }}`：
  - width、height、durationSeconds 来自 payload，尺寸变化时同步更新布局和 WebGL 渲染器。
  - ready 是 Promise：字体、纹理、模型和 shader 全部就绪后 resolve，加载失败就 reject。ready 后调用 renderAt(0)。
  - renderAt(seconds) 能画出任意时刻的完整画面，包括往回跳；可以返回 Promise，但要等绘制完成。
- 动画只由 renderAt 的时间驱动：不用真实时间、随机数、setTimeout、自由运行的 requestAnimationFrame 或 autoplay；粒子用固定种子；不用无限循环或依赖前一帧的特效。
- GSAP：时间线设为 paused，所有 tween 放进同一条时间线，renderAt 里调用 `tl.totalTime(seconds, false)`。
- Three.js：renderAt 里设置物体和相机状态，`mixer.setTime(seconds)`，再 `renderer.render(scene, camera)`；像素比固定为 1。
- 不放配音、音乐或字幕，应用之后统一混音。
- 只用本段目录里的本地文件，不用 CDN、网络请求或 npm。本段已有：
  - GSAP：`<script src="vendor/gsap.min.js"></script>`
  - Three.js import map："three" → "./vendor/three/build/three.module.js"，"three/addons/" → "./vendor/three/examples/jsm/"

## 检查与渲染

在本段目录里依次运行：

1. `python render.py capabilities`：查看本机实测的浏览器 GPU 和编码器，以它为准，不凭机型猜测。
2. `python render.py check --snapshots`：检查报错、资源加载、clip 契约、画面溢出、字幕区和往回跳的一致性，截图存到 snapshots/。它看不到 Canvas / Three.js 里的文字是否压住字幕区，所以要亲自看截图：可读性、对比度、中文裁切、主题和字幕区。指定时刻用 `python render.py snapshot --at 0.5,1.5` 或 `--frames 5`。
3. `python render.py preview`：生成完整预览；看 snapshots/，并用 ffmpeg 抽转场前后的帧检查。
4. `python render.py render`：生成本段 video.mp4（未变的已验证预览会直接复用）。核对 render-report.json 的 verified、帧数、尺寸、时长和实际用到的硬件。

有问题就改 HTML 再从第 2 步检查；改过 HTML 或素材就必须重新渲染。默认用硬件加速；只有硬件不可用、失败或用户要求时，才加 `--browser-mode software` 或 `--encoder software`，并说明原因。`--force` 可以强制重渲。

不要改动或删除 vendor/、studio-payload.json、render.py，不要在 images/ 根目录写 segment_*.mp4，不要碰其他段落或本段的其他版本。

## 风格：{style_label}

适用于：{style_best_for}。上面的字数、字号、字体、透明度和字幕规则优先于风格里的写法。

{style_definition}

调色板 {palette}（每行一组可选配色）：

{palette_definition}

可选延伸参考（不必读）：
{style_references}

## 可选参考

先直接写 index.html，确实需要时再读，最多 {reference_limit} 个文件。

动作配方：
{recipe_paths}

技术参考：
{reference_paths}

技能入口：
{skill_entries}

## 完成

每段 video.mp4 都与当前 HTML 一致、render-report.json 的 verified 为 true 才算完成。最后简要说明生成的视频、检查结果、用到的硬件或回退原因，以及没能验证的地方，然后等待修改要求；结束会话不会重新渲染。
<!-- section: segment -->
### 段落 {segment_index}

- 工作目录：`{work_dir}`，先 `cd` 进去，所有文件只写在这里。
- 时长：{duration} 秒。
- 输入：`{work_dir}/studio-payload.json`（本段正文、时长、尺寸和全片摘要）。
- 产出：`index.html` 和渲染出的 `video.mp4`。
{new}
<!-- section: new -->
- 这是新片段，从头设计；本段之前的版本会保留，供用户比较。
