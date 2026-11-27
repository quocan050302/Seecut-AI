/**
 * DX-01 Deterministic Offline Remotion Bridge CLI (`render_span.mjs`).
 *
 * Reuses the host's existing pinned Remotion workspace WITHOUT modifying it,
 * WITHOUT duplicating `node_modules`, and WITHOUT downloading browsers/fonts at render time.
 *
 * Actions:
 *   --action probe   --remotion-root <dir> [--browser <exe>]
 *   --action bundle  --remotion-root <dir> --bundle-out <dir>
 *   --action render  --remotion-root <dir> --bundle-dir <dir> --props <json> --out <file>
 *                    [--browser <exe>] [--codec prores|h264|vp9|png_still] [--frame <idx>]
 *                    [--concurrency <n>] [--receipt <json>]
 */

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { pathToFileURL } from "node:url";

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const k = a.slice(2);
      const nxt = argv[i + 1];
      if (!nxt || nxt.startsWith("--")) {
        out[k] = true;
      } else {
        out[k] = nxt;
        i += 1;
      }
    }
  }
  return out;
}

function sha256File(filePath) {
  const h = crypto.createHash("sha256");
  h.update(fs.readFileSync(filePath));
  return h.digest("hex");
}

function resolvePackageEntry(remotionRoot, pkgName) {
  const nmDir = path.join(remotionRoot, "node_modules", ...pkgName.split("/"));
  const pkgJsonPath = path.join(nmDir, "package.json");
  if (!fs.existsSync(pkgJsonPath)) {
    throw new Error(`MISSING_PACKAGE_IN_REMOTION_ROOT: ${pkgName} in ${remotionRoot}`);
  }
  const pkg = JSON.parse(fs.readFileSync(pkgJsonPath, "utf8"));
  let rel = pkg.main || "dist/index.js";
  if (pkg.exports && pkg.exports["."]) {
    const dot = pkg.exports["."];
    if (typeof dot === "string") {
      rel = dot;
    } else if (dot.import) {
      rel = typeof dot.import === "string" ? dot.import : dot.import.default || rel;
    } else if (dot.default) {
      rel = dot.default;
    }
  }
  return {
    version: pkg.version,
    license: pkg.license || "UNKNOWN",
    entryPath: path.join(nmDir, rel),
    nmRoot: path.join(remotionRoot, "node_modules"),
  };
}

async function main() {
  const t0 = Date.now();
  const args = parseArgs(process.argv.slice(2));
  const action = String(args.action || "probe");
  const remotionRoot = path.resolve(String(args["remotion-root"] || ""));
  if (!remotionRoot || !fs.existsSync(path.join(remotionRoot, "node_modules", "remotion", "package.json"))) {
    throw new Error(`INVALID_REMOTION_ROOT: ${remotionRoot}`);
  }

  const remPkg = resolvePackageEntry(remotionRoot, "remotion");
  const bndPkg = resolvePackageEntry(remotionRoot, "@remotion/bundler");
  const rndPkg = resolvePackageEntry(remotionRoot, "@remotion/renderer");
  const pathsPkg = resolvePackageEntry(remotionRoot, "@remotion/paths");
  const nmRoot = remPkg.nmRoot;
  const browserExecutable = args.browser ? path.resolve(String(args.browser)) : undefined;

  if (action === "probe") {
    const result = {
      ok: true,
      remotion_root: remotionRoot,
      remotion_version: remPkg.version,
      bundler_version: bndPkg.version,
      renderer_version: rndPkg.version,
      paths_version: pathsPkg.version,
      browser_executable: browserExecutable || null,
      browser_exists: browserExecutable ? fs.existsSync(browserExecutable) : false,
      node_version: process.version,
      elapsed_ms: Date.now() - t0,
    };
    process.stdout.write(JSON.stringify(result) + "\n");
    return;
  }

  const bridgeDir = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
  const entryPoint = path.join(bridgeDir, "src", "index.ts");

  if (action === "bundle") {
    const bundleOut = path.resolve(String(args["bundle-out"] || ""));
    if (!bundleOut) throw new Error("MISSING_BUNDLE_OUT");
    fs.mkdirSync(bundleOut, { recursive: true });
    const bundlerMod = await import(pathToFileURL(bndPkg.entryPath).href);
    const serveUrl = await bundlerMod.bundle({
      entryPoint,
      outDir: bundleOut,
      enableCaching: false,
      webpackOverride: (cfg) => {
        const prevModules = (cfg.resolve && cfg.resolve.modules) || ["node_modules"];
        const prevLoaderModules = (cfg.resolveLoader && cfg.resolveLoader.modules) || ["node_modules"];
        return {
          ...cfg,
          resolve: {
            ...(cfg.resolve || {}),
            modules: [nmRoot, ...prevModules],
          },
          resolveLoader: {
            ...(cfg.resolveLoader || {}),
            modules: [nmRoot, ...prevLoaderModules],
          },
        };
      },
    });
    const res = {
      ok: true,
      action: "bundle",
      serve_url: serveUrl,
      bundle_dir: bundleOut,
      remotion_version: remPkg.version,
      elapsed_ms: Date.now() - t0,
    };
    process.stdout.write(JSON.stringify(res) + "\n");
    return;
  }

  if (action === "render") {
    const bundleDir = path.resolve(String(args["bundle-dir"] || ""));
    const propsPath = path.resolve(String(args.props || ""));
    const outPath = path.resolve(String(args.out || ""));
    const codec = String(args.codec || "prores");
    const concurrency = Math.max(1, Math.min(2, Number(args.concurrency) || 1));
    const receiptPath = args.receipt ? path.resolve(String(args.receipt)) : null;

    if (!fs.existsSync(bundleDir)) throw new Error(`MISSING_BUNDLE_DIR: ${bundleDir}`);
    if (!fs.existsSync(propsPath)) throw new Error(`MISSING_PROPS_FILE: ${propsPath}`);
    if (browserExecutable && !fs.existsSync(browserExecutable)) {
      throw new Error(`MISSING_BROWSER_EXECUTABLE: ${browserExecutable}`);
    }

    const inputProps = JSON.parse(fs.readFileSync(propsPath, "utf8"));
    const propsSha = sha256File(propsPath);
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    const stagingOut = `${outPath}.staging${path.extname(outPath)}`;
    if (fs.existsSync(stagingOut)) fs.unlinkSync(stagingOut);

    const rendererMod = await import(pathToFileURL(rndPkg.entryPath).href);
    const browserOpts = browserExecutable ? { browserExecutable } : {};

    const composition = await rendererMod.selectComposition({
      serveUrl: bundleDir,
      id: "Dx01EffectSpan",
      inputProps,
      ...browserOpts,
      logLevel: "error",
    });

    if (codec === "png_still") {
      const targetFrame = Math.max(0, Math.min(composition.durationInFrames - 1, Number(args.frame) || 0));
      await rendererMod.renderStill({
        composition,
        serveUrl: bundleDir,
        output: stagingOut,
        inputProps,
        frame: targetFrame,
        imageFormat: "png",
        ...browserOpts,
        logLevel: "error",
      });
    } else if (codec === "prores") {
      await rendererMod.renderMedia({
        composition,
        serveUrl: bundleDir,
        outputLocation: stagingOut,
        inputProps,
        codec: "prores",
        proResProfile: "4444",
        imageFormat: "png",
        pixelFormat: "yuva444p10le",
        concurrency,
        muted: true,
        ...browserOpts,
        logLevel: "error",
      });
    } else if (codec === "vp9") {
      await rendererMod.renderMedia({
        composition,
        serveUrl: bundleDir,
        outputLocation: stagingOut,
        inputProps,
        codec: "vp9",
        imageFormat: "png",
        pixelFormat: "yuva420p",
        concurrency,
        muted: true,
        ...browserOpts,
        logLevel: "error",
      });
    } else {
      await rendererMod.renderMedia({
        composition,
        serveUrl: bundleDir,
        outputLocation: stagingOut,
        inputProps,
        codec: "h264",
        crf: 17,
        pixelFormat: "yuv420p",
        imageFormat: "jpeg",
        jpegQuality: 92,
        concurrency,
        muted: true,
        ...browserOpts,
        logLevel: "error",
      });
    }

    fs.renameSync(stagingOut, outPath);
    const outStat = fs.statSync(outPath);
    const receipt = {
      schema_version: "kh03.dx01.remotion_render_receipt.v1",
      ok: true,
      backend: "REMOTION_LOCAL_BRIDGE",
      remotion_version: remPkg.version,
      renderer_version: rndPkg.version,
      browser_executable: browserExecutable || null,
      codec,
      transparent: Boolean(inputProps.transparent),
      composition_id: composition.id,
      width: composition.width,
      height: composition.height,
      fps: composition.fps,
      duration_in_frames: composition.durationInFrames,
      span_id: inputProps.span_id,
      effect_ids: Array.from(new Set((inputProps.events || []).map((e) => e.effect_id))),
      props_sha256: propsSha,
      output_path: outPath,
      output_bytes: outStat.size,
      output_sha256: sha256File(outPath),
      concurrency,
      elapsed_ms: Date.now() - t0,
    };
    if (receiptPath) {
      fs.mkdirSync(path.dirname(receiptPath), { recursive: true });
      fs.writeFileSync(receiptPath, JSON.stringify(receipt, null, 2) + "\n", "utf8");
    }
    process.stdout.write(JSON.stringify(receipt) + "\n");
    return;
  }

  throw new Error(`UNKNOWN_ACTION: ${action}`);
}

main().catch((err) => {
  process.stderr.write(String(err && err.stack ? err.stack : err) + "\n");
  process.exit(1);
});
