// Run after `tauri build`: delete the intermediate src-tauri/target/.../Grist.app
// once it is inside the .dmg. Left on disk, Spotlight keeps registering it, so
// Launchpad shows a second Grist next to the installed copy in /Applications.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundle = path.join(root, "src-tauri/target/release/bundle/macos/Grist.app");
const lsregister =
  "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister";

if (process.platform === "darwin" && fs.existsSync(bundle)) {
  try {
    execFileSync(lsregister, ["-u", bundle], { stdio: "inherit" });
  } catch (error) {
    // Deleting the bundle below is what matters; a stale record is cleaned up later.
    console.warn(`Could not unregister ${bundle}: ${error.message}`);
  }
  fs.rmSync(bundle, { recursive: true, force: true });
  console.log(`Removed ${path.relative(root, bundle)}; the installer is in bundle/dmg/.`);
}
