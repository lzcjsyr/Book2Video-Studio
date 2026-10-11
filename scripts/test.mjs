import { spawnSync } from "node:child_process";
import { stagedRuntime } from "./python.mjs";
const language = spawnSync(
  process.execPath,
  ["--experimental-strip-types", "--test", "scripts/test-language.mjs"],
  { stdio: "inherit" },
);
if (language.status !== 0) process.exit(language.status ?? 1);
const { python, env } = stagedRuntime(process.cwd());
const result = spawnSync(
  python,
  ["-m", "unittest", "discover", "-s", "backend/tests", "-v"],
  { stdio: "inherit", env },
);
process.exitCode = result.status ?? 1;
