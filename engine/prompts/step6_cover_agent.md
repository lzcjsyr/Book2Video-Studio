<!-- 第六步 · Claude Code 封面模板。会话提示 = agent_rules.md 的 rules + 本文件的 main。 -->
<!-- 修改上一版时，{revise} 填入 revise 节，否则为空。保留 section 标记和花括号变量。 -->

<!-- section: main -->
# 第六步：封面设计

用 HTML、CSS 和 Three.js 与用户一起设计静态视频封面。文字用 HTML；需要光照、几何、纵深时用 Three.js。

{revise}## 文案

- 主标题：{title}
- 副标题：{subtitle}

原样使用，用户没要求就不改写，也不加事实。`{design_dir}/studio-payload.json` 里还有全片摘要和所选风格；风格是方向，由你转化为视觉。构图需要更多语境时，才读其中 `manuscript_path` 指向的正文。

## 要做的封面

每项是一个独立候选，每种宽高比单独构图，不裁切横版凑数。

{covers}

## 手机优先

封面主要在手机信息流里以缩略图出现，常常只有几厘米宽。

- 主标题字高不小于画面短边的 12%，副标题不小于 6%，粗体、高对比。
- 画面上只放标题和副标题，不加多余文字。
- 文字离边缘至少留出短边的 6%，避开右下角（平台会在那里叠加时长）。
- 检查预览时，把 PNG 缩到约 360 像素宽来看，仍要一眼看清。

## 技术要求

- 页面恰好填满视口（devicePixelRatio 1），不滚动、不溢出。
- 画面静态、可复现：开头设 `window.__ready = false`；字体、图片、纹理加载完、Three.js 画完最后一帧、动画循环停掉后，再设为 `true`。
- 渲染器用 `preserveDrawingBuffer: true` 和 `renderer.setPixelRatio(1)`。
- 用内置 Three.js 0.160.0，不用 CDN 或 npm：
  <script type="importmap">{{"imports":{{"three":"./vendor/three/build/three.module.js","three/addons/":"./vendor/three/examples/jsm/"}}}}</script>
- 字体用系统已装的，或存进设计目录用相对路径引用。

## 流程

1. 在 `{design_dir}` 存好全部 HTML，运行 `python render.py`，在 `previews/` 生成预览 PNG。
2. 逐张检查字号、裁切、缺字、空白画布，修好后重渲。
3. 告诉用户点击「结束并渲染封面」（Finish & render covers）或输入 `/exit` 生成最终封面；结束前继续接受修改。

## 边界

- 只在 `{design_dir}` 内工作，不动项目已有文件。
- 不改 `design.json`、`render.py`，不装软件包，不调用图像 API，不往项目根目录写 PNG。
<!-- section: revise -->

这次是修改：你在上一版 HTML 的副本上工作。用户没提新方向就保留原概念；之前的候选仍可用。
