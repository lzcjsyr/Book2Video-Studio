"""Claude Code cover designs and deterministic HTML → PNG rendering."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

ROOT = ".studio/cover-designs"
DEFAULT_SIZES = {"16:9": "3200x1800", "9:16": "1800x3200", "4:3": "3000x2250", "3:4": "2250x3000", "1:1": "2500x2500"}


def dimensions(value):
    match = re.fullmatch(r"(\d+)[x×](\d+)", str(value).lower())
    if not match or any(not 1 <= int(n) <= 8192 for n in match.groups()):
        raise ValueError("Cover dimensions must be between 1 and 8192 pixels.")
    return tuple(map(int, match.groups()))


def contained(project, relative):
    path = (project / relative).resolve()
    if not path.is_relative_to(project.resolve() / ROOT):
        raise ValueError("Invalid cover design path.")
    return path


def specification(settings):
    cover = settings.get("step6", {})
    presets = cover.get("cover_image_size_presets") or DEFAULT_SIZES
    keys = cover.get("cover_image_size_keys") or [cover.get("default_cover_image_size_key")]
    sizes = list(dict.fromkeys(str(presets[key]) for key in keys if key in presets))
    if not sizes:
        sizes = [str(settings.get("step4", {}).get("image_size") or "1280x720")]
    count = cover.get("cover_image_count", 1)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 20:
        raise ValueError("Choose between 1 and 20 cover candidates per size.")
    entries = []
    for size in sizes:
        width, height = dimensions(size)
        for candidate in range(1, count + 1):
            entries.append({"html": f"cover_{width}x{height}_{candidate}.html", "width": width, "height": height})
    return entries


def prepare(project, settings, session, previous=None, copy=("", "")):
    entries = specification(settings)
    work = project / ROOT / session
    if previous:
        source = contained(project, previous["folder"])
        if source.is_dir():
            shutil.copytree(source, work, ignore=shutil.ignore_patterns("previews", "__pycache__"))
    work.mkdir(parents=True, exist_ok=True)
    # Use the same pinned Three.js for every design; offline imports need no CDN or npm install.
    from core.infra.motion.runtime import modules_dir
    three = modules_dir() / 'three'
    shutil.copytree(three / "build", work / "vendor/three/build", dirs_exist_ok=True)
    shutil.copytree(three / "examples/jsm", work / "vendor/three/examples/jsm", dirs_exist_ok=True)
    design = {"folder": str(work.relative_to(project)), "entries": entries,
              "brief": {"title": copy[0], "subtitle": copy[1], "style": settings.get("step6", {}).get("cover_image_style", "cover01")}}
    (work / "design.json").write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    # Agents can inspect snapshots using the same renderer as the final production job.
    (work / "render.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
        "from covers import render\n"
        f"render(Path({str(project)!r}), {design!r}, preview=True)\n", encoding="utf-8")
    return design


def detail(project, design, settings=None, copy=None):
    if not design:
        return None
    work = contained(project, design["folder"])
    files, previews = [], []
    for entry in design["entries"]:
        html = work / entry["html"]
        if html.is_file():
            files.append(str(html.relative_to(project)))
        preview = work / "previews" / (Path(entry["html"]).stem + ".png")
        if preview.is_file():
            previews.append(str(preview.relative_to(project)))
    current = True
    if settings is not None and copy is not None:
        try:
            current = (design["entries"] == specification(settings) and design.get("brief") ==
                       {"title": copy[0], "subtitle": copy[1], "style": settings.get("step6", {}).get("cover_image_style", "cover01")})
        except ValueError:
            current = False
    revisions = {file: f"{(project / file).stat().st_mtime_ns}:{(project / file).stat().st_size}" for file in files + previews}
    return {**design, "files": files, "previews": previews, "complete": len(files) == len(design["entries"]), "current": current, "revisions": revisions}


RENDER_JS = r"""
const { createRequire } = require('node:module');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const spec = JSON.parse(process.argv[1]);
const puppeteer = createRequire(path.join(spec.modules,'package.json'))('puppeteer-core');
(async () => {
  // Serve ES modules on loopback: file:// module imports differ across Chrome platforms.
  const root = fs.realpathSync(spec.work);
  const server = http.createServer((req, res) => {
    try {
      const file = path.resolve(root, '.' + decodeURIComponent(new URL(req.url, 'http://localhost').pathname));
      if (!file.startsWith(root + path.sep) || !fs.realpathSync(file).startsWith(root + path.sep)) {
        res.writeHead(403).end(); return;
      }
      const types = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2'};
      res.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream');
      fs.createReadStream(file).pipe(res);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    const hardwareArgs = ['--hide-scrollbars', '--enable-gpu-rasterization'];
    if (process.platform === 'darwin') hardwareArgs.push('--use-gl=angle', '--use-angle=metal');
    if (process.platform === 'win32') hardwareArgs.push('--use-gl=angle', '--use-angle=d3d11');
    try {
      browser = await puppeteer.launch({executablePath: spec.chrome, headless: true, args: hardwareArgs});
      const probe = await browser.newPage();
      try {
        const renderer = await probe.evaluate(() => {
          const canvas = document.createElement('canvas');
          const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
          if (!gl) return null;
          const debug = gl.getExtension('WEBGL_debug_renderer_info');
          return String(gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
        });
        if (!renderer || /swiftshader|llvmpipe|software/i.test(renderer)) {
          throw new Error('硬件 WebGL 不可用：' + (renderer || '无法创建 WebGL 上下文'));
        }
        console.log('封面使用 GPU：' + renderer);
      } finally { await probe.close(); }
    } catch (error) {
      if (browser) await browser.close();
      browser = undefined;
      console.warn('封面 GPU 初始化失败，回退软件 WebGL：' + error.message);
      browser = await puppeteer.launch({executablePath: spec.chrome, headless: true,
        args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--hide-scrollbars']});
    }
    for (const entry of spec.entries) {
      const page = await browser.newPage();
      try {
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('requestfailed', request => errors.push('Asset failed: ' + request.url()));
        page.on('response', response => { if (response.status() >= 400) errors.push('Asset HTTP ' + response.status() + ': ' + response.url()); });
        await page.setViewport({width: entry.width, height: entry.height, deviceScaleFactor: 1});
        await page.goto(`http://127.0.0.1:${server.address().port}/${encodeURIComponent(entry.html)}`, {waitUntil: 'networkidle0', timeout: 60000});
        await page.waitForFunction(() => window.__ready === true, {timeout: 60000});
        await page.evaluate(async () => {
          await document.fonts.ready;
          await Promise.all(Array.from(document.images, img => img.decode()));
          document.getAnimations().forEach(animation => animation.pause());
        });
        if (errors.length) throw new Error(errors.join('\n'));
        await page.screenshot({path: entry.output, type: 'png', clip: {x: 0, y: 0, width: entry.width, height: entry.height}});
        console.log('Rendered ' + entry.html);
      } finally { await page.close(); }
    }
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


def render(project, design, preview=False):
    work = contained(project, design["folder"])
    from core.infra.motion.runtime import modules_dir, browser_path
    modules = str(modules_dir())
    chrome = browser_path()
    node = shutil.which("node")
    if not chrome or not node:
        raise ValueError("The bundled cover renderer is unavailable. Reinstall Book2Video Studio.")
    info = detail(project, design)
    if not info["complete"]:
        raise ValueError("Save every cover HTML design with Claude Code before rendering.")
    run_id = uuid.uuid4().hex[:12]
    entries = []
    for entry in design["entries"]:
        dimensions(f"{entry['width']}x{entry['height']}")
        source = (work / entry["html"]).resolve()
        if source.parent != work.resolve() or source.suffix != ".html":
            raise ValueError("Invalid cover HTML file.")
        name = Path(entry["html"]).stem
        output = work / "previews" / f"{name}.png" if preview else work / f"{name}_{run_id}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        entries.append({**entry, "output": str(output)})
    try:
        result = subprocess.run([node, "-e", RENDER_JS, json.dumps({"work": str(work), "modules": modules, "chrome": chrome, "entries": entries})],
                                capture_output=True, text=True, encoding="utf-8", timeout=150 * len(entries) + 30)
        if result.returncode:
            raise ValueError("Cover rendering failed: " + (result.stderr or result.stdout)[-3000:])
        outputs = []
        for entry in entries:
            output = Path(entry["output"])
            if not preview:
                output = output.replace(project / output.name)
            outputs.append(str(output.relative_to(project)))
        print(result.stdout, end="", flush=True)
        return {"success": True, "covers": outputs}
    finally:
        if not preview:
            for entry in entries:
                Path(entry["output"]).unlink(missing_ok=True)
