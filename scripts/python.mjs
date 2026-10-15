import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

// The staged runtime (npm run stage) that the app bundles; development and
// tests use it so they exercise what ships.
export function stagedRuntime(root) {
  const runtime = path.join(root, "build/runtime");
  const file = path.join(runtime, "manifest.json");
  if (!existsSync(file))
    throw new Error("The bundled runtime is not staged yet. Run `npm run stage` first.");
  const manifest = JSON.parse(readFileSync(file, "utf8"));
  const tool = (name) => path.join(runtime, manifest[name]);
  const env = { ...process.env };
  for (const name of ["PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"]) delete env[name];
  Object.assign(env, {
    PATH: [...manifest.path.map((p) => path.join(runtime, p)), process.env.PATH].join(path.delimiter),
    AIGC_MOTION_MODULES: tool("motion_modules"),
    AIGC_BROWSER_PATH: tool("chrome"),
    AIGC_FFMPEG_PATH: tool("ffmpeg"),
    AIGC_FFPROBE_PATH: tool("ffprobe"),
    PRODUCER_HEADLESS_SHELL_PATH: tool("chrome"),
    IMAGEIO_FFMPEG_EXE: tool("ffmpeg"),
    STUDIO_CLAUDE: tool("claude"),
    PYTHONPATH: path.join(root,"build/engine"),
    PYTHONNOUSERSITE: "1",
    PYTHONDONTWRITEBYTECODE: "1",
    PYTHONUTF8: "1",
  });
  return { python: tool("python"), engine: path.join(root, "build/engine"), env };
}
