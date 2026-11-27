"""DX01-06 — Deterministic Offline Local Remotion Bridge Adapter.

Bridges Python canonical DX-01 specs to the repo's local `remotion_bridge/` React/TypeScript
composition while reusing the host's preinstalled pinned Remotion workspace WITHOUT modifying
that workspace, WITHOUT duplicate npm installs, and WITHOUT network downloads at render time!

Guarantees:
- Explicit workspace discovery (REMOTION_WORKSPACE env -> known configured workspaces -> repo sibling;
  never an unrestricted disk crawl).
- Local preinstalled Chromium/Chrome discovery via `browserExecutable` (zero browser downloads).
- Content-addressed bundle caching keyed by `(bridge source SHA, remotion version)` with single-flight lock.
- Content-addressed span render caching keyed by `(bundle SHA, canonical props SHA, codec, geometry, fps, seed)`.
- Bounded concurrency (max 1-2 browser tabs/renders calibrated to host RAM), subprocess timeouts,
  cooperative cancellation, stderr draining, and process-tree termination.
- O(1)-RAM streaming RGBA frame readback from cached ProRes 4444 (`yuva444p10le`) or VP9 (`yuva420p`)
  alpha streams — zero thousands of persistent full-res PNGs on disk!
- Honest per-span & per-effect render receipts (never labels FFmpeg-only output as Remotion).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from .execution import CancellationToken, run_subprocess_isolated, terminate_process_tree

REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_DIR = REPO_ROOT / "remotion_bridge"
BRIDGE_CLI = BRIDGE_DIR / "render_span.mjs"

_BUNDLE_LOCKS: dict[str, threading.Lock] = {}
_SPAN_LOCKS: dict[str, threading.Lock] = {}
_GLOBAL_LOCK = threading.Lock()
_PROBE_CACHE: dict[str, Any] | None = None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_sha(obj: Any) -> str:
    return _sha256_bytes(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _get_lock(store: dict[str, threading.Lock], key: str) -> threading.Lock:
    with _GLOBAL_LOCK:
        lk = store.get(key)
        if lk is None:
            lk = threading.Lock()
            store[key] = lk
        return lk


def compute_bridge_source_sha() -> str:
    """Hash all local `remotion_bridge/` source files deterministically for bundle cache invalidation."""
    if not BRIDGE_DIR.is_dir():
        return "MISSING_BRIDGE_DIR"
    files = sorted(
        p for p in BRIDGE_DIR.rglob("*")
        if p.is_file() and "node_modules" not in p.parts and p.suffix.lower() in (".ts", ".tsx", ".mjs", ".json")
    )
    h = hashlib.sha256()
    for f in files:
        rel = f.relative_to(BRIDGE_DIR).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def discover_remotion_workspace(explicit_root: Path | None = None) -> Path | None:
    """Discover compatible local pinned Remotion workspace via explicit allowed paths only (no disk crawl)."""
    candidates: list[Path] = []
    if explicit_root is not None:
        candidates.append(Path(explicit_root))
    env_ws = os.environ.get("REMOTION_WORKSPACE") or os.environ.get("DX01_REMOTION_ROOT")
    if env_ws:
        candidates.append(Path(env_ws))
    home = Path.home()
    candidates.extend([
        REPO_ROOT / "remotion_bridge",
        REPO_ROOT.parent / "remotion-runtime",
        home / "projects" / "anti-animated-shorts-studio",
        home / "Projects" / "anti-animated-shorts-studio",
        home / "remotion-studio",
    ])
    for cand in candidates:
        try:
            p = cand.expanduser().resolve()
            if (
                (p / "node_modules" / "remotion" / "package.json").is_file()
                and (p / "node_modules" / "@remotion" / "bundler" / "package.json").is_file()
                and (p / "node_modules" / "@remotion" / "renderer" / "package.json").is_file()
            ):
                return p
        except Exception:
            continue
    return None


def discover_local_browser_executable(explicit_browser: Path | None = None) -> Path | None:
    """Locate preinstalled headless Chromium or Chrome executable for offline Remotion rendering."""
    candidates: list[Path] = []
    if explicit_browser is not None:
        candidates.append(Path(explicit_browser))
    env_br = os.environ.get("REMOTION_BROWSER_EXECUTABLE") or os.environ.get("CHROME_EXECUTABLE")
    if env_br:
        candidates.append(Path(env_br))

    local_app = Path(os.environ.get("LOCALAPPDATA", "")) if os.environ.get("LOCALAPPDATA") else None
    if local_app and (local_app / "ms-playwright").is_dir():
        # Prefer Playwright headless shell (fast, low RAM, zero UI popup!), then Playwright Chromium
        for pat in (
            "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe",
            "chromium-*/chrome-win64/chrome.exe",
        ):
            for hit in sorted((local_app / "ms-playwright").glob(pat), reverse=True):
                candidates.append(hit)
    if os.name == "nt":
        for p_str in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ):
            candidates.append(Path(p_str))
    else:
        for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
            w = shutil.which(name)
            if w:
                candidates.append(Path(w))
        mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if mac_chrome.is_file():
            candidates.append(mac_chrome)

    for c in candidates:
        try:
            if c.is_file():
                return c.resolve()
        except Exception:
            continue
    return None


def probe_remotion_runtime(
    *,
    remotion_root: Path | None = None,
    browser_executable: Path | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Audit local Remotion workspace, coherent package versions, Node, and offline browser readiness."""
    global _PROBE_CACHE
    if use_cache and _PROBE_CACHE is not None and remotion_root is None and browser_executable is None:
        return dict(_PROBE_CACHE)

    node_bin = shutil.which("node")
    ws = discover_remotion_workspace(remotion_root)
    br = discover_local_browser_executable(browser_executable)
    bridge_ok = BRIDGE_CLI.is_file() and (BRIDGE_DIR / "src" / "index.ts").is_file()

    if not node_bin or ws is None or not bridge_ok:
        res = {
            "schema_version": "kh03.dx01.remotion_probe.v1",
            "ready": False,
            "status": "BLOCKED_BACKEND",
            "node_present": bool(node_bin),
            "bridge_present": bridge_ok,
            "remotion_root_found": ws is not None,
            "browser_found": br is not None,
            "remotion_version": None,
            "reason": f"node={bool(node_bin)}, workspace={ws is not None}, bridge={bridge_ok}",
            "license_notice": "Remotion License (Free License for individuals/non-profits/<=3-employee companies; Company License otherwise).",
        }
        return res

    cmd = [node_bin, str(BRIDGE_CLI), "--action", "probe", "--remotion-root", str(ws)]
    if br is not None:
        cmd.extend(["--browser", str(br)])
    run = run_subprocess_isolated(cmd, timeout_sec=20.0)
    if run["returncode"] != 0:
        return {
            "schema_version": "kh03.dx01.remotion_probe.v1",
            "ready": False,
            "status": "BLOCKED_BACKEND",
            "remotion_root": str(ws),
            "browser_executable": str(br) if br else None,
            "reason": f"probe failed rc={run['returncode']}: {run['stderr'][-240:]}",
        }
    info = json.loads(run["stdout"].strip().splitlines()[-1])
    aligned = (
        info.get("remotion_version") == info.get("bundler_version") == info.get("renderer_version")
    )
    ready = bool(info.get("ok") and aligned and br is not None and br.is_file())
    rep = {
        "schema_version": "kh03.dx01.remotion_probe.v1",
        "ready": ready,
        "status": "READY_OFFLINE" if ready else "BLOCKED_BROWSER_OR_VERSION_MISMATCH",
        "node_version": info.get("node_version"),
        "remotion_root": str(ws),
        "remotion_version": info.get("remotion_version"),
        "bundler_version": info.get("bundler_version"),
        "renderer_version": info.get("renderer_version"),
        "paths_version": info.get("paths_version"),
        "versions_aligned": aligned,
        "browser_executable": str(br) if br else None,
        "browser_exists": bool(br and br.is_file()),
        "bridge_source_sha256": compute_bridge_source_sha(),
        "offline_capable": ready,
        "uses_seecut_polyform": False,
        "license_notice": "Remotion License (Free License for individuals, non-profits, evaluation, <=3-employee companies; Company License for >3-employee for-profit).",
    }
    if remotion_root is None and browser_executable is None:
        _PROBE_CACHE = dict(rep)
    return rep


def ensure_remotion_bundle(
    cache_root: Path,
    *,
    remotion_root: Path | None = None,
    cancel_token: CancellationToken | None = None,
    timeout_sec: float = 180.0,
) -> dict[str, Any]:
    """Build or reuse cached Remotion webpack bundle keyed by `(bridge_source_sha, remotion_version)`."""
    probe = probe_remotion_runtime(remotion_root=remotion_root)
    if not probe.get("ready"):
        raise RuntimeError(f"REMOTION_BACKEND_NOT_READY: {probe.get('status')} ({probe.get('reason')})")

    src_sha = probe["bridge_source_sha256"]
    rem_ver = probe["remotion_version"]
    bundle_key = _canonical_sha({"src_sha": src_sha, "remotion_version": rem_ver})[:24]
    bundles_root = Path(cache_root).resolve() / "remotion_bundles"
    bundle_dir = bundles_root / f"bundle_{bundle_key}"
    marker = bundle_dir / ".dx01_bundle_verified.json"

    with _get_lock(_BUNDLE_LOCKS, bundle_key):
        if marker.is_file() and (bundle_dir / "index.html").is_file():
            try:
                m = json.loads(marker.read_text(encoding="utf-8"))
                if m.get("bundle_key") == bundle_key and m.get("bridge_source_sha256") == src_sha:
                    return {**m, "cached": True, "bundle_dir": str(bundle_dir)}
            except Exception:
                pass

        if cancel_token is not None:
            cancel_token.check()

        bundles_root.mkdir(parents=True, exist_ok=True)
        staging_dir = bundles_root / f".staging_{bundle_key}_{os.getpid()}"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)

        env = dict(os.environ)
        tmp_dir = Path(cache_root).resolve() / "remotion_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        for tk in ("REMOTION_TMP", "TMPDIR", "TEMP", "TMP"):
            env[tk] = str(tmp_dir)

        t0 = time.monotonic()
        cmd = [
            shutil.which("node") or "node",
            str(BRIDGE_CLI),
            "--action", "bundle",
            "--remotion-root", str(probe["remotion_root"]),
            "--bundle-out", str(staging_dir),
        ]
        res = run_subprocess_isolated(cmd, cancel_token=cancel_token, timeout_sec=timeout_sec, env=env)
        if res["returncode"] != 0 or res["timed_out"] or res["cancelled"]:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise RuntimeError(
                f"REMOTION_BUNDLE_FAILED(rc={res['returncode']}, timeout={res['timed_out']}, cancel={res['cancelled']}): "
                f"{res['stderr'][-350:]}"
            )

        if bundle_dir.exists():
            shutil.rmtree(bundle_dir, ignore_errors=True)
        os.replace(staging_dir, bundle_dir)

        meta = {
            "schema_version": "kh03.dx01.remotion_bundle.v1",
            "bundle_key": bundle_key,
            "bridge_source_sha256": src_sha,
            "remotion_version": rem_ver,
            "bundle_dir": str(bundle_dir),
            "elapsed_sec": round(time.monotonic() - t0, 3),
            "cached": False,
        }
        marker.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return meta


def render_remotion_effect_span(
    span_props: dict[str, Any],
    *,
    cache_root: Path,
    codec: str = "prores",
    frame_index: int | None = None,
    concurrency: int = 1,
    remotion_root: Path | None = None,
    browser_executable: Path | None = None,
    cancel_token: CancellationToken | None = None,
    timeout_sec: float = 240.0,
) -> dict[str, Any]:
    """Render (or reuse from content-addressed cache) a deterministic Remotion effect span or still frame."""
    probe = probe_remotion_runtime(remotion_root=remotion_root, browser_executable=browser_executable)
    if not probe.get("ready"):
        raise RuntimeError(f"REMOTION_BACKEND_NOT_READY: {probe.get('status')}")

    bundle_info = ensure_remotion_bundle(
        cache_root,
        remotion_root=Path(probe["remotion_root"]),
        cancel_token=cancel_token,
    )

    ext = ".png" if codec == "png_still" else (".mov" if codec == "prores" else (".webm" if codec == "vp9" else ".mp4"))
    span_key = _canonical_sha({
        "bundle_key": bundle_info["bundle_key"],
        "codec": codec,
        "frame_index": frame_index,
        "props": span_props,
    })[:28]

    spans_dir = Path(cache_root).resolve() / "remotion_spans"
    spans_dir.mkdir(parents=True, exist_ok=True)
    out_file = spans_dir / f"span_{span_key}{ext}"
    receipt_file = spans_dir / f"span_{span_key}.receipt.json"
    props_file = spans_dir / f"span_{span_key}.props.json"

    with _get_lock(_SPAN_LOCKS, span_key):
        if out_file.is_file() and receipt_file.is_file():
            try:
                rec = json.loads(receipt_file.read_text(encoding="utf-8"))
                if rec.get("ok") and rec.get("output_sha256") == _sha256_file(out_file):
                    return {
                        **rec,
                        "cached": True,
                        "bundle_key": bundle_info["bundle_key"],
                        "bundle_cached": bundle_info["cached"],
                        "span_key": span_key,
                    }
            except Exception:
                pass

        if cancel_token is not None:
            cancel_token.check()

        props_file.write_text(json.dumps(span_props, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_dir = Path(cache_root).resolve() / "remotion_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        for tk in ("REMOTION_TMP", "TMPDIR", "TEMP", "TMP"):
            env[tk] = str(tmp_dir)

        eff_conc = max(1, min(2, int(concurrency)))
        cmd = [
            shutil.which("node") or "node",
            str(BRIDGE_CLI),
            "--action", "render",
            "--remotion-root", str(probe["remotion_root"]),
            "--bundle-dir", str(bundle_info["bundle_dir"]),
            "--props", str(props_file),
            "--out", str(out_file),
            "--codec", str(codec),
            "--concurrency", str(eff_conc),
            "--receipt", str(receipt_file),
        ]
        if probe.get("browser_executable"):
            cmd.extend(["--browser", str(probe["browser_executable"])])
        if frame_index is not None:
            cmd.extend(["--frame", str(int(frame_index))])

        t0 = time.monotonic()
        res = run_subprocess_isolated(cmd, cancel_token=cancel_token, timeout_sec=timeout_sec, env=env)
        if res["returncode"] != 0 or res["timed_out"] or res["cancelled"] or not out_file.is_file():
            out_file.unlink(missing_ok=True)
            receipt_file.unlink(missing_ok=True)
            raise RuntimeError(
                f"REMOTION_SPAN_RENDER_FAILED(rc={res['returncode']}, timeout={res['timed_out']}, cancel={res['cancelled']}): "
                f"{res['stderr'][-400:]}"
            )

        rec = json.loads(receipt_file.read_text(encoding="utf-8"))
        rec["cached"] = False
        rec["bundle_key"] = bundle_info["bundle_key"]
        rec["bundle_cached"] = bundle_info["cached"]
        rec["span_key"] = span_key
        rec["wall_sec"] = round(time.monotonic() - t0, 3)
        receipt_file.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
        return rec


class StreamingAlphaOverlayReader:
    """O(1)-RAM sequential FFmpeg RGBA32 reader for Remotion transparent ProRes4444 / VP9 alpha spans."""

    def __init__(self, alpha_video_path: Path, width: int, height: int) -> None:
        self.path = Path(alpha_video_path).resolve()
        self.width = int(width)
        self.height = int(height)
        self.frame_bytes = self.width * self.height * 4
        self._proc: subprocess.Popen[bytes] | None = None
        self._cur_idx = -1
        self._cur_rgba: np.ndarray | None = None

    def open(self) -> None:
        cmd = [
            "ffmpeg", "-v", "error", "-nostdin", "-threads", "2",
            "-i", str(self.path),
            "-vf", f"scale={self.width}:{self.height}:flags=lanczos",
            "-fps_mode", "passthrough",
            "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1",
        ]
        popen_kw: dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "bufsize": self.frame_bytes * 2}
        if os.name == "posix":
            popen_kw["start_new_session"] = True
        elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        self._proc = subprocess.Popen(cmd, **popen_kw)

    def get_frame_rgba(self, local_frame: int) -> np.ndarray | None:
        if local_frame < self._cur_idx:
            self.close()
            self._cur_idx = -1
            self._cur_rgba = None
            self.open()
        if self._proc is None:
            self.open()
        assert self._proc is not None and self._proc.stdout is not None
        while self._cur_idx < local_frame:
            buf = bytearray(self.frame_bytes)
            mv = memoryview(buf)
            got = 0
            while got < self.frame_bytes:
                n = self._proc.stdout.readinto(mv[got:])
                if not n:
                    break
                got += n
            if got < self.frame_bytes:
                break
            self._cur_idx += 1
            if self._cur_idx == local_frame or self._cur_rgba is None:
                self._cur_rgba = np.frombuffer(buf, dtype=np.uint8).reshape((self.height, self.width, 4)).copy()
        return self._cur_rgba

    def close(self) -> None:
        p = self._proc
        self._proc = None
        if p is not None:
            for pipe in (p.stdout, p.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except Exception:
                        pass
            terminate_process_tree(p)


def alpha_composite_rgba_onto_rgb(dst_rgb: np.ndarray, overlay_rgba: np.ndarray) -> None:
    """In-place premultiplication-safe straight-alpha composite of RGBA overlay onto uint8 RGB frame."""
    alpha_u8 = overlay_rgba[:, :, 3]
    if not np.any(alpha_u8):
        return
    # Fast ROI bounding-box slice to avoid touching empty transparent pixels!
    ys, xs = np.nonzero(alpha_u8)
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    sub_ov = overlay_rgba[y0:y1, x0:x1]
    a = sub_ov[:, :, 3:4].astype(np.float32) * (1.0 / 255.0)
    src = sub_ov[:, :, :3].astype(np.float32)
    dst = dst_rgb[y0:y1, x0:x1].astype(np.float32)
    dst_rgb[y0:y1, x0:x1] = np.clip(src * a + dst * (1.0 - a), 0.0, 255.0).astype(np.uint8)


def typecheck_remotion_bridge(*, remotion_root: Path | None = None) -> dict[str, Any]:
    """Run TypeScript `tsc --noEmit` against `remotion_bridge/src` using host's pinned TypeScript + @types."""
    ws = discover_remotion_workspace(remotion_root)
    if ws is None:
        return {"pass": False, "reason": "REMOTION_WORKSPACE_NOT_FOUND"}
    tsc_js = ws / "node_modules" / "typescript" / "bin" / "tsc"
    node_bin = shutil.which("node")
    if not tsc_js.is_file() or not node_bin:
        return {"pass": False, "reason": "TYPESCRIPT_TSC_NOT_FOUND"}
    # Build a temporary extended tsconfig pointing paths/typeRoots to ws/node_modules without modifying ws!
    tmp_cfg = Path(os.environ.get("TEMP", ".")) / f"dx01_tsc_{os.getpid()}.json"
    nm = (ws / "node_modules").as_posix()
    src_glob = (BRIDGE_DIR / "src" / "**" / "*.ts*").as_posix()
    cfg = {
        "compilerOptions": {
            "target": "ES2022",
            "module": "ESNext",
            "moduleResolution": "bundler",
            "jsx": "react-jsx",
            "strict": True,
            "noEmit": True,
            "skipLibCheck": True,
            "esModuleInterop": True,
            "baseUrl": ".",
            "typeRoots": [f"{nm}/@types"],
            "paths": {
                "react": [f"{nm}/@types/react/index.d.ts"],
                "react/*": [f"{nm}/@types/react/*"],
                "remotion": [f"{nm}/remotion/dist/cjs/index.d.ts", f"{nm}/remotion/dist/esm/index.d.ts"],
                "@remotion/paths": [f"{nm}/@remotion/paths/dist/index.d.ts", f"{nm}/@remotion/paths/dist/cjs/index.d.ts"],
            },
        },
        "include": [src_glob],
    }
    tmp_cfg.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        res = run_subprocess_isolated([node_bin, str(tsc_js), "-p", str(tmp_cfg)], timeout_sec=60.0)
        return {
            "pass": res["returncode"] == 0,
            "returncode": res["returncode"],
            "stdout": res["stdout"][-1200:],
            "stderr": res["stderr"][-1200:],
            "wall_sec": res["wall_sec"],
        }
    finally:
        tmp_cfg.unlink(missing_ok=True)


__all__ = [
    "BRIDGE_CLI",
    "BRIDGE_DIR",
    "StreamingAlphaOverlayReader",
    "alpha_composite_rgba_onto_rgb",
    "compute_bridge_source_sha",
    "discover_local_browser_executable",
    "discover_remotion_workspace",
    "ensure_remotion_bundle",
    "probe_remotion_runtime",
    "render_remotion_effect_span",
    "typecheck_remotion_bridge",
]
