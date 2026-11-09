// Run after `tauri build`:
// - Recompress the .dmg with LZMA (ULMO). Tauri writes zlib (UDZO); LZMA makes the
//   installer about a fifth smaller and opens on macOS 10.15+, below our 11.0 minimum.
//   Any signing or notarization of the .dmg has to happen after this step.
// - Move the intermediate src-tauri/target/.../Grist.app out of the bundle folder
//   once it is inside the .dmg. Left there with its .app name, Spotlight registers a
//   second Grist in Launchpad. A rename is also safer than a bulk delete; the parked
//   copy stays in gitignored build/.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dmgDir = path.join(root, "src-tauri/target/release/bundle/dmg");
const bundle = path.join(root, "src-tauri/target/release/bundle/macos/Grist.app");

const megabytes = (file) => (fs.statSync(file).size / 1024 / 1024).toFixed(1);
if (process.platform === "darwin" && fs.existsSync(dmgDir)) {
  for (const name of fs.readdirSync(dmgDir).filter((n) => n.endsWith(".dmg") && !n.endsWith(".lzma.dmg"))) {
    const dmg = path.join(dmgDir, name);
    const info = execFileSync("hdiutil", ["imageinfo", dmg], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
    if (/^Format: ULMO$/m.test(info)) continue;
    // Convert beside the original and replace it only once the new image verifies.
    const lzma = dmg.replace(/\.dmg$/, ".lzma.dmg");
    fs.rmSync(lzma, { force: true });
    const before = megabytes(dmg);
    execFileSync("hdiutil", ["convert", dmg, "-format", "ULMO", "-o", lzma, "-quiet"], { stdio: "inherit" });
    execFileSync("hdiutil", ["verify", lzma, "-quiet"], { stdio: "inherit" });
    fs.renameSync(lzma, dmg);
    console.log(`Recompressed ${name} with LZMA: ${before} MB -> ${megabytes(dmg)} MB.`);
  }
}
const parkedRoot = path.join(root, "build", ".intermediate-apps");
const lsregister =
  "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister";

if (process.platform === "darwin" && fs.existsSync(bundle)) {
  try {
    execFileSync(lsregister, ["-u", bundle], { stdio: "inherit" });
  } catch (error) {
    // Moving the bundle below is what matters; a stale record is cleaned up later.
    console.warn(`Could not unregister ${bundle}: ${error.message}`);
  }
  fs.mkdirSync(parkedRoot, { recursive: true });
  let parked = path.join(parkedRoot, `Grist-${Date.now()}.app.disabled`);
  for (let suffix = 1; fs.existsSync(parked); suffix++) {
    parked = path.join(parkedRoot, `Grist-${Date.now()}-${suffix}.app.disabled`);
  }
  fs.renameSync(bundle, parked);
  console.log(`Moved ${path.relative(root, bundle)} to ${path.relative(root, parked)}; the installer is in bundle/dmg/.`);
}
