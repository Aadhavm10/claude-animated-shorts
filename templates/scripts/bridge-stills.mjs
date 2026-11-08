// usage: [COMP=<CompositionId>] node scripts/bridge-stills.mjs <outPrefix> <frame,frame,...> [scale]  (bundles once, GPU via ANGLE, jpeg)
// BRIDGE_ENTRY=src/sandboxes/<name>/index.tsx renders a sandbox copy instead of the main project entry.
import fs from "node:fs";
// Remotion bundles fill the temp folder (~1.3 GB each): set REMOTION_TMP to a folder on a big drive
if (process.env.REMOTION_TMP) { fs.mkdirSync(process.env.REMOTION_TMP, { recursive: true }); process.env.TEMP = process.env.TMP = process.env.TMPDIR = process.env.REMOTION_TMP; } // TMPDIR: macOS/Linux, TEMP/TMP: Windows
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import path from "node:path";
const [prefix, frames, scale = "0.5"] = process.argv.slice(2);
// Browser: Remotion's own Chrome Headless Shell (downloaded once on first use) unless REMOTION_BROWSER points to a
// Chrome binary. GL: "angle" (GPU) by default; REMOTION_GL=swangle forces the CPU fallback if the GPU path fails.
const browserExecutable = process.env.REMOTION_BROWSER || null;
const chromiumOptions = { gl: process.env.REMOTION_GL || "angle" };
const serveUrl = await bundle({ entryPoint: path.resolve(process.env.BRIDGE_ENTRY || "src/index.ts"), publicDir: path.resolve("public") });
// COMP=<CompositionId> picks the short to render (default: the bridge template)
const composition = await selectComposition({ serveUrl, id: process.env.COMP || "BridgeShort", browserExecutable, chromiumOptions });
const onBrowserLog = (l) => { if (l.type === "error" || l.type === "warning") console.log(`[${l.type}]`, l.text.slice(0, 300)); };
for (const f of frames.split(",").map(Number)) {
  const t0 = Date.now();
  await renderStill({ composition, serveUrl, frame: f, output: `${prefix}${f}.jpg`, imageFormat: "jpeg", jpegQuality: 88, scale: Number(scale),
    browserExecutable, chromiumOptions, timeoutInMilliseconds: 180000, onBrowserLog });
  console.log("still", f, ((Date.now() - t0) / 1000).toFixed(1) + "s");
}
