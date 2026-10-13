// Assemble everything Book2Video Studio ships besides its own binary:
//   build/runtime  Python with the engine's packages (including the Claude Code
//                  CLI bundled by claude-agent-sdk), Node.js, the pinned
//                  HyperFrames CLI and its Chrome build, FFmpeg and FFprobe
//   build/engine   the CLI engine's code, prompts, skills, music and defaults
//   build/backend  the desktop's project service
// Downloads are pinned, checksum-verified and cached in build/downloads. The
// runtime is rebuilt only when a pin or lock file changes.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const build = path.join(root, "build");
const downloads = path.join(build, "downloads");
const engineSource = path.resolve(
  process.env.STUDIO_ENGINE_ROOT || path.join(root, "../AIGC_Video_CLI"),
);

const PYTHON = { version: "3.13.15", release: "20260924" };
const NODE = "22.23.3";
const FFMPEG = { version: "9.0.2", build: "1789931890_9.0.2" };
const PLATFORMS = {
  "darwin-arm64": {
    python: `cpython-${PYTHON.version}+${PYTHON.release}-aarch64-apple-darwin-install_only_stripped.tar.gz`,
    node: `node-v${NODE}-darwin-arm64`,
    ffmpeg: `https://ffmpeg.martin-riedl.de/download/macos/arm64/${FFMPEG.build}`,
  },
};
const platform = PLATFORMS[`${process.platform}-${process.arch}`];
if (!platform) {
  console.error(`Packaging is not set up for ${process.platform}-${process.arch} yet.`);
  process.exit(1);
}
const pythonBin = "python/bin/python3";
const sitePackages = `python/lib/python${PYTHON.version.split(".").slice(0, 2).join(".")}/site-packages`;

function run(command, args, options = {}) {
  execFileSync(command, args, { stdio: "inherit", ...options });
}
function sha256(file) {
  return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}
async function fetchText(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.text();
}
// Download once into the cache and verify it against the publisher's checksum.
async function download(url, expected) {
  const file = path.join(downloads, path.basename(new URL(url).pathname));
  if (!fs.existsSync(file) || sha256(file) !== expected) {
    console.log(`Downloading ${url}`);
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    fs.mkdirSync(downloads, { recursive: true });
    fs.writeFileSync(file + ".part", Buffer.from(await response.arrayBuffer()));
    fs.renameSync(file + ".part", file);
  }
  const actual = sha256(file);
  if (actual !== expected) throw new Error(`Checksum mismatch for ${url}`);
  return file;
}
function checksumFrom(list, name) {
  const line = list.split("\n").find((l) => l.trim().endsWith(name));
  if (!line) throw new Error(`No published checksum for ${name}`);
  return line.trim().split(/\s+/)[0];
}
function copyTree(source, target, skip = () => false) {
  fs.cpSync(source, target, {
    recursive: true,
    filter: (p) => !skip(path.relative(source, p).split(path.sep), p),
  });
}
const cleanEnv = (extra = {}) => {
  const env = { ...process.env, PYTHONNOUSERSITE: "1", ...extra };
  for (const name of ["PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "PIP_REQUIRE_VIRTUALENV"]) delete env[name];
  return env;
};

// Tauri copies resources file by file, so replace symlinks with what they
// point to; npm's launcher scripts need a wrapper to keep relative imports.
function flattenSymlinks(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) {
      const target = fs.realpathSync(full);
      fs.unlinkSync(full);
      if (fs.statSync(target).isDirectory()) {
        fs.cpSync(target, full, { recursive: true, dereference: true });
      } else if (target.endsWith(".js")) {
        const relative = path.relative(path.dirname(full), target);
        fs.writeFileSync(full, `#!/bin/sh\nexec "$(dirname "$0")/node" "$(dirname "$0")/${relative}" "$@"\n`, { mode: 0o755 });
      } else {
        fs.copyFileSync(target, full);
        fs.chmodSync(full, fs.statSync(target).mode);
      }
    } else if (entry.isDirectory()) {
      flattenSymlinks(full);
    }
  }
}

async function stageRuntime() {
  const lockDir = path.join(root, "runtime");
  const pins = {
    platform, PYTHON, NODE, FFMPEG,
    script: sha256(fileURLToPath(import.meta.url)),
    requirements: sha256(path.join(lockDir, "requirements.lock")),
    hyperframes: ["package.json", "package-lock.json"].map((f) => {
      const file = path.join(lockDir, "hyperframes", f);
      return fs.existsSync(file) ? sha256(file) : "";
    }),
  };
  const stamp = JSON.stringify(pins);
  const target = path.join(build, "runtime");
  if (fs.existsSync(path.join(target, ".stamp")) && fs.readFileSync(path.join(target, ".stamp"), "utf8") === stamp) {
    console.log("Runtime is current.");
    return;
  }
  const work = path.join(build, "runtime.partial");
  fs.rmSync(work, { recursive: true, force: true });
  fs.mkdirSync(path.join(work, "bin"), { recursive: true });

  // Python: a relocatable CPython build with the engine's locked packages.
  const pbs = `https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON.release}`;
  const pythonArchive = await download(`${pbs}/${platform.python}`, checksumFrom(await fetchText(`${pbs}/SHA256SUMS`), platform.python));
  run("tar", ["-xzf", pythonArchive, "-C", work]);
  const python = path.join(work, pythonBin);
  run(python, ["-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", "--no-compile",
    "-r", path.join(lockDir, "requirements.lock")], { env: cleanEnv() });
  run(python, ["-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "unchecked-hash", path.join(work, "python/lib")], { env: cleanEnv() });

  // Node.js with npm: runs HyperFrames, and the motion agent's own checks.
  const nodeArchive = await download(`https://nodejs.org/dist/v${NODE}/${platform.node}.tar.gz`,
    checksumFrom(await fetchText(`https://nodejs.org/dist/v${NODE}/SHASUMS256.txt`), `${platform.node}.tar.gz`));
  run("tar", ["-xzf", nodeArchive, "-C", work]);
  fs.renameSync(path.join(work, platform.node), path.join(work, "node"));
  for (const extra of ["include", "share", "lib/node_modules/corepack", "bin/corepack"]) {
    fs.rmSync(path.join(work, "node", extra), { recursive: true, force: true });
  }
  const node = path.join(work, "node/bin/node");
  const npm = path.join(work, "node/lib/node_modules/npm/bin/npm-cli.js");
  const nodeEnv = cleanEnv({ PATH: [path.join(work, "node/bin"), process.env.PATH].join(path.delimiter) });

  // HyperFrames, pinned by its lock file, and the Chrome build it expects.
  const hyperframes = path.join(work, "hyperframes");
  fs.mkdirSync(hyperframes);
  const lock = path.join(lockDir, "hyperframes/package-lock.json");
  fs.copyFileSync(path.join(lockDir, "hyperframes/package.json"), path.join(hyperframes, "package.json"));
  if (fs.existsSync(lock)) fs.copyFileSync(lock, path.join(hyperframes, "package-lock.json"));
  run(node, [npm, fs.existsSync(lock) ? "ci" : "install", "--omit=dev", "--no-audit", "--no-fund"], { cwd: hyperframes, env: nodeEnv });
  if (!fs.existsSync(lock)) fs.copyFileSync(path.join(hyperframes, "package-lock.json"), lock);
  const dist = path.join(hyperframes, "node_modules/hyperframes/dist");
  const cli = path.join(dist, "cli.js");
  // Newer CLIs code-split the constant into a chunk; \b skips MACOS_12_CHROME_VERSION.
  const chromeVersion = fs.readdirSync(dist).filter((f) => f.endsWith(".js"))
    .map((f) => fs.readFileSync(path.join(dist, f), "utf8").match(/\bCHROME_VERSION = "([\d.]+)"/)?.[1])
    .find(Boolean);
  if (!chromeVersion) throw new Error("Cannot find the Chrome version HyperFrames expects.");
  // v2 exposes an ESM build under exports; v3 is ESM-only at main.
  const browsersDir = path.join(hyperframes, "node_modules/@puppeteer/browsers");
  const browsersPkg = JSON.parse(fs.readFileSync(path.join(browsersDir, "package.json"), "utf8"));
  const browsers = path.join(browsersDir, browsersPkg.exports?.["."]?.import ?? browsersPkg.main);
  const chrome = execFileSync(node, ["--input-type=module", "-e", `
    const { install, detectBrowserPlatform, Browser } = await import(${JSON.stringify(browsers)});
    const result = await install({ cacheDir: ${JSON.stringify(path.join(work, "chrome"))},
      browser: Browser.CHROMEHEADLESSSHELL, buildId: ${JSON.stringify(chromeVersion)}, platform: detectBrowserPlatform() });
    process.stdout.write(result.executablePath);`], { env: nodeEnv }).toString().trim();

  // FFmpeg and FFprobe: static builds used by the engine and by HyperFrames.
  for (const tool of ["ffmpeg", "ffprobe"]) {
    const sums = await fetchText(`${platform.ffmpeg}/${tool}.zip.sha256`);
    const archive = await download(`${platform.ffmpeg}/${tool}.zip`, sums.trim().split(/\s+/)[0]);
    run("unzip", ["-o", "-q", archive, "-d", path.join(work, "bin")]);
    fs.chmodSync(path.join(work, "bin", tool), 0o755);
  }
  fs.writeFileSync(path.join(work, "bin/hyperframes"),
    `#!/bin/sh\nhere="$(cd "$(dirname "$0")/.." && pwd)"\nexec "$here/node/bin/node" "$here/hyperframes/node_modules/hyperframes/dist/cli.js" "$@"\n`, { mode: 0o755 });

  // Skills and the Claude Code agent run `python`; macOS has no such command and its
  // python3 is a stub that asks to install developer tools. Point both at the bundled one.
  for (const name of ["python", "python3"]) {
    fs.writeFileSync(path.join(work, "bin", name),
      `#!/bin/sh\nhere="$(cd "$(dirname "$0")/.." && pwd)"\nexec "$here/${pythonBin}" "$@"\n`, { mode: 0o755 });
  }

  flattenSymlinks(work);
  const relative = (p) => path.relative(work, p).split(path.sep).join("/");
  const manifest = {
    python: pythonBin,
    claude: `${sitePackages}/claude_agent_sdk/_bundled/claude`,
    node: "node/bin/node",
    hyperframes: relative(cli),
    chrome: relative(chrome),
    ffmpeg: "bin/ffmpeg",
    ffprobe: "bin/ffprobe",
    path: ["bin", "node/bin"],
  };
  for (const [name, file] of Object.entries(manifest)) {
    if (name !== "path" && !fs.existsSync(path.join(work, file))) throw new Error(`Missing ${name}: ${file}`);
  }
  fs.writeFileSync(path.join(work, "manifest.json"), JSON.stringify(manifest, null, 2));
  fs.copyFileSync(path.join(lockDir, "THIRD_PARTY_NOTICES.md"), path.join(work, "THIRD_PARTY_NOTICES.md"));
  fs.writeFileSync(path.join(work, ".stamp"), stamp);
  fs.rmSync(target, { recursive: true, force: true });
  fs.renameSync(work, target);
}

// Only the engine's shipped parts: never .env, config.yaml, input or output.
function stageEngine() {
  const target = path.join(build, "engine");
  fs.rmSync(target, { recursive: true, force: true });
  const skip = (parts) => parts.some((p) => p === "__pycache__" || p === ".DS_Store" || p === "node_modules" || p.endsWith(".pyc"));
  copyTree(path.join(engineSource, "core"), path.join(target, "core"), (parts) => skip(parts) || parts[0] === "tests");
  for (const folder of ["prompts", "skills", "music"]) copyTree(path.join(engineSource, folder), path.join(target, folder), skip);
  copyTree(path.join(root, "skills/step5"), path.join(target, "skills/step5"), skip);
  fs.copyFileSync(path.join(engineSource, "config.example.yaml"), path.join(target, "config.example.yaml"));
}

function stageBackend() {
  const target = path.join(build, "backend");
  fs.rmSync(target, { recursive: true, force: true });
  fs.mkdirSync(target, { recursive: true });
  for (const file of fs.readdirSync(path.join(root, "backend"))) {
    if (file.endsWith(".py")) fs.copyFileSync(path.join(root, "backend", file), path.join(target, file));
  }
}

// Fail the build, not the user's first job, when a bundled part is broken.
function smokeTest() {
  const runtime = path.join(build, "runtime");
  const manifest = JSON.parse(fs.readFileSync(path.join(runtime, "manifest.json"), "utf8"));
  const tool = (name) => path.join(runtime, manifest[name]);
  const env = cleanEnv({ PATH: manifest.path.map((p) => path.join(runtime, p)).join(path.delimiter) + path.delimiter + "/usr/bin:/bin" });
  for (const folder of ["engine", "backend"]) {
    run(tool("python"), ["-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "unchecked-hash", path.join(build, folder)], { env });
  }
  run(tool("python"), ["-c", "import sys; sys.path.insert(0, sys.argv[1]); import core.pipeline.steps, claude_agent_sdk, fitz, moviepy, docx, yaml; print('Python engine imports: ok')",
    path.join(build, "engine")], { env });
  // The agent's shell finds the bundled Python by name, and it can read every input format.
  console.log("Agent shell:", execFileSync("sh", ["-c",
    'command -v python | grep -qF "$RUNTIME/bin/python" && python -c "import fitz, pdfplumber, ebooklib, mobi, docx, yaml; print(\'python on PATH with extractors: ok\')"'],
  { env: { ...env, RUNTIME: runtime } }).toString().trim());
  const version = (name, args) => execFileSync(tool(name), args, { env }).toString().split("\n")[0].trim();
  console.log("Claude Code:", version("claude", ["--version"]));
  console.log("Node.js:", version("node", ["--version"]));
  console.log("HyperFrames:", execFileSync(tool("node"), [tool("hyperframes"), "--version"], { env }).toString().trim());
  console.log("Chrome:", version("chrome", ["--version"]));
  console.log("FFmpeg:", version("ffmpeg", ["-version"]));
  console.log("FFprobe:", version("ffprobe", ["-version"]));
}

await stageRuntime();
stageEngine();
stageBackend();
smokeTest();
