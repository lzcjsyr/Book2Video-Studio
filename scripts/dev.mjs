import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { stagedRuntime } from "./python.mjs";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const token = randomBytes(32).toString("hex");
const { python, engine, env } = stagedRuntime(root);
// Browser development keeps its own library and projects beside the build.
const data = path.join(root, "build/dev-data");
const service = spawn(
  python,
  [
    "-u",
    path.join(root, "backend/server.py"),
    "--engine",
    engine,
    "--data",
    data,
    "--workspace",
    path.join(data, "projects"),
  ],
  {
    env: { ...env, AIGC_VIDEO_LIBRARY_DIR: path.join(data, "library"), STUDIO_TOKEN: token },
    stdio: ["ignore", "pipe", "inherit"],
  },
);
let frontend;
createInterface({ input: service.stdout }).once("line", (line) => {
  const { port } = JSON.parse(line);
  frontend = spawn(
    process.execPath,
    [path.join(root, "node_modules/vite/bin/vite.js"), "--configLoader", "runner"],
    {
      cwd: root,
      env: {
        ...process.env,
        STUDIO_BACKEND_PORT: String(port),
        STUDIO_TOKEN: token,
      },
      stdio: "inherit",
    },
  );
  frontend.on("exit", (code) => {
    service.kill();
    process.exitCode = code || 0;
  });
});
service.on("exit", (code) => {
  if (code) process.exitCode = code;
  frontend?.kill();
});
for (const signal of ["SIGINT", "SIGTERM"])
  process.on(signal, () => {
    frontend?.kill();
    service.kill();
  });
