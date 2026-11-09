// The staged runtime ships the full Volcengine wheel and a second FFmpeg binary
// from imageio-ffmpeg. Keep only the Ark packages used by Seedream; MoviePy and
// imageio use the bundled FFmpeg through IMAGEIO_FFMPEG_EXE instead.
// Removed files are moved outside the bundled runtime into a gitignored backup,
// so a failed verification can restore them without relying on bulk deletion.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const build = path.join(root, "build");
const runtime = path.join(build, "runtime");
const backup = path.join(build, ".runtime-prune-backup");
const manifestFile = path.join(runtime, "manifest.json");
const KEEP_SDK_PACKAGES = new Set([
  "volcenginesdkarkruntime",
  "volcenginesdkark",
  "volcenginesdkcore",
]);
const SDK_PACKAGE = /^volcenginesdk[a-z0-9_]+$/;
const FFMPEG_BINARY = /^ffmpeg-[A-Za-z0-9._-]+$/;

if (!fs.existsSync(manifestFile))
  throw new Error("The bundled runtime is not staged yet. Run `npm run stage` first.");
const manifest = JSON.parse(fs.readFileSync(manifestFile, "utf8"));
const tool = (name) => path.join(runtime, manifest[name]);
const python = tool("python");
const ffmpeg = tool("ffmpeg");
const ffprobe = tool("ffprobe");
for (const [name, file] of Object.entries({ python, ffmpeg, ffprobe })) {
  if (!fs.existsSync(file)) throw new Error(`Runtime manifest points to a missing ${name}: ${file}`);
}

// Deleting imageio-ffmpeg's copy is only safe while both launchers keep this
// variable pointed at the one FFmpeg the app already bundles.
for (const source of ["src-tauri/src/main.rs", "scripts/python.mjs"]) {
  if (!fs.readFileSync(path.join(root, source), "utf8").includes("IMAGEIO_FFMPEG_EXE"))
    throw new Error(`${source} no longer sets IMAGEIO_FFMPEG_EXE; the shared FFmpeg cannot be pruned.`);
}
const env = {
  ...process.env,
  PATH: [...manifest.path.map((folder) => path.join(runtime, folder)), process.env.PATH].join(path.delimiter),
  AIGC_FFMPEG_PATH: ffmpeg,
  AIGC_FFPROBE_PATH: ffprobe,
  IMAGEIO_FFMPEG_EXE: ffmpeg,
  FFMPEG_BINARY: "ffmpeg-imageio",
  PYTHONNOUSERSITE: "1",
  PYTHONDONTWRITEBYTECODE: "1",
  PYTHONUTF8: "1",
};
for (const name of ["PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"]) delete env[name];

const sitePackages = execFileSync(
  python,
  ["-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
  { env, encoding: "utf8" },
).trim();
const binaries = path.join(sitePackages, "imageio_ffmpeg", "binaries");
if (!fs.existsSync(sitePackages) || !fs.existsSync(binaries))
  throw new Error(`The staged Python packages are incomplete: ${sitePackages}`);
for (const name of KEEP_SDK_PACKAGES) {
  if (!fs.existsSync(path.join(sitePackages, name, "__init__.py")))
    throw new Error(`The staged Ark runtime is missing ${name}. Rebuild with \`npm run stage\`.`);
}

// Tauri copies resources without removing stale files, so release/debug target
// runtimes must be pruned too or an earlier build's copies would still ship.
const sitePackagesRelative = path.relative(runtime, sitePackages);
const targets = ["debug", "release"].map((profile) => {
  const targetRuntime = path.join(root, "src-tauri", "target", profile, "runtime");
  return {
    profile,
    sitePackages: path.join(targetRuntime, sitePackagesRelative),
  };
}).filter((target) => fs.existsSync(target.sitePackages));

const moves = [];
function moveToBackup(source, base, bucket) {
  const relative = path.relative(base, source);
  let target = path.join(backup, bucket, relative);
  for (let suffix = 1; fs.existsSync(target); suffix++) {
    target = path.join(backup, bucket, `${relative}.${suffix}`);
  }
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.renameSync(source, target);
  moves.push({ source, target, bucket });
}
function restore() {
  for (const { source, target } of moves.reverse()) {
    fs.mkdirSync(path.dirname(source), { recursive: true });
    fs.renameSync(target, source);
  }
}
function sizeOf(target) {
  const stat = fs.lstatSync(target);
  if (!stat.isDirectory()) return stat.size;
  return fs.readdirSync(target).reduce((total, name) => total + sizeOf(path.join(target, name)), 0);
}
function pruneSitePackages(packagesDir, base, bucket) {
  let sdk = 0;
  let ffmpegCopies = 0;
  for (const entry of fs.readdirSync(packagesDir, { withFileTypes: true })) {
    if (!entry.isDirectory() || !SDK_PACKAGE.test(entry.name) || KEEP_SDK_PACKAGES.has(entry.name)) continue;
    moveToBackup(path.join(packagesDir, entry.name), base, bucket);
    sdk++;
  }
  const targetBinaries = path.join(packagesDir, "imageio_ffmpeg", "binaries");
  if (fs.existsSync(targetBinaries)) {
    for (const entry of fs.readdirSync(targetBinaries, { withFileTypes: true })) {
      if (!entry.isFile() || !FFMPEG_BINARY.test(entry.name)) continue;
      moveToBackup(path.join(targetBinaries, entry.name), base, bucket);
      ffmpegCopies++;
    }
  }
  return { sdk, ffmpegCopies };
}
function checkSitePackages(label, packagesDir) {
  const remainingSdk = fs.readdirSync(packagesDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && SDK_PACKAGE.test(entry.name) && !KEEP_SDK_PACKAGES.has(entry.name));
  if (remainingSdk.length)
    throw new Error(`${label}: unused Volcengine packages remain: ${remainingSdk.map((e) => e.name).join(", ")}`);
  const targetBinaries = path.join(packagesDir, "imageio_ffmpeg", "binaries");
  if (!fs.existsSync(targetBinaries)) return;
  const remainingBinaries = fs.readdirSync(targetBinaries, { withFileTypes: true })
    .filter((entry) => entry.isFile() && FFMPEG_BINARY.test(entry.name));
  if (remainingBinaries.length)
    throw new Error(`${label}: duplicate FFmpeg binaries remain: ${remainingBinaries.map((e) => e.name).join(", ")}`);
}

fs.mkdirSync(backup, { recursive: true });
const staged = pruneSitePackages(sitePackages, sitePackages, "staged");
let targetSdk = 0;
let targetFfmpegCopies = 0;
for (const target of targets) {
  const pruned = pruneSitePackages(target.sitePackages, target.sitePackages, `tauri-${target.profile}`);
  targetSdk += pruned.sdk;
  targetFfmpegCopies += pruned.ffmpegCopies;
}

function verify() {
  checkSitePackages("Staged runtime", sitePackages);
  for (const target of targets) checkSitePackages(`Tauri ${target.profile} runtime`, target.sitePackages);
  execFileSync(python, ["-c", `
import os
import imageio_ffmpeg
from moviepy.config import FFMPEG_BINARY
from volcenginesdkarkruntime import Ark
assert imageio_ffmpeg.get_ffmpeg_exe() == os.environ["IMAGEIO_FFMPEG_EXE"]
assert FFMPEG_BINARY == os.environ["IMAGEIO_FFMPEG_EXE"]
assert hasattr(Ark(api_key="runtime-prune-check"), "images")
print("Ark client and shared FFmpeg: ok")
`], { env, stdio: "inherit" });
  console.log("FFmpeg:", execFileSync(ffmpeg, ["-version"], { env, encoding: "utf8" }).split("\n")[0]);
}

function movedBytes(bucket) {
  return moves.filter((move) => move.bucket === bucket)
    .reduce((total, move) => total + sizeOf(move.target), 0);
}
const stagedBytes = movedBytes("staged");
const targetBytes = targets.reduce((total, target) => total + movedBytes(`tauri-${target.profile}`), 0);
try {
  verify();
} catch (error) {
  restore();
  throw new Error(`Runtime pruning failed and the original files were restored: ${error.message}`);
}
console.log(
  `Pruned ${staged.sdk} unused Volcengine packages and ${staged.ffmpegCopies} duplicate FFmpeg binaries ` +
  `from the staged runtime (${(stagedBytes / 1024 / 1024).toFixed(1)} MB).`,
);
console.log(
  `Pruned ${targetSdk} stale packages and ${targetFfmpegCopies} stale FFmpeg copies ` +
  `from Tauri target runtimes (${(targetBytes / 1024 / 1024).toFixed(1)} MB).`,
);
console.log(`Rollback copies stay outside the bundle in ${path.relative(root, backup)}; delete them after the build is verified.`);
