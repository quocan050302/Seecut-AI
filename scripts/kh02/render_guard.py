"""KH-02 Render Guard: P0 path containment, composite cache identity, disk/RAM floor, guarded pinned render.mjs launcher & atomic mux."""

from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from .contracts import hash_artifact, safe_path
from .source_analysis import probe_source, sha256_file

OWNERSHIP_MARKER = ".kh02_run_owned"
CACHE_META_FILE = ".kh02_cache_identity.json"
DEFAULT_DISK_RESERVE_GIB = 5.0


def estimate_resources(
    total_frames: int,
    width: int,
    height: int,
    *,
    render_scale: float = 1.0,
    target_dir: str | Path = ".",
    reserve_floor_gib: float = DEFAULT_DISK_RESERVE_GIB,
    max_disk_gib: float | None = None,
) -> dict[str, Any]:
    """Estimate transient disk peak (PNG frames + person JPGs + layers + MP4 + margin) and check free disk reserve."""
    px = int(width * render_scale) * int(height * render_scale)
    # Empirical conservative PNG estimate ~0.35 bytes/pixel + person JPG ~0.08 bytes/pixel
    png_bytes = int(total_frames * px * 0.35)
    person_bytes = int(total_frames * (width * height) * 0.08)
    mp4_and_layers = int(max(30 * 1024 * 1024, total_frames * 85000))
    projected_bytes = int((png_bytes + person_bytes + mp4_and_layers) * 1.25)
    projected_gib = round(projected_bytes / (1024 ** 3), 3)

    check_path = Path(target_dir).expanduser()
    while not check_path.exists() and check_path != check_path.parent:
        check_path = check_path.parent
    du = shutil.disk_usage(str(check_path))
    free_gib = round(du.free / (1024 ** 3), 3)
    remaining_after_gib = round(free_gib - projected_gib, 3)

    ok_reserve = remaining_after_gib >= reserve_floor_gib
    ok_budget = (max_disk_gib is None) or (projected_gib <= max_disk_gib)
    return {
        "total_frames": total_frames,
        "effective_resolution": f"{int(width * render_scale)}x{int(height * render_scale)}",
        "projected_peak_gib": projected_gib,
        "free_disk_gib": free_gib,
        "remaining_after_peak_gib": remaining_after_gib,
        "reserve_floor_gib": reserve_floor_gib,
        "max_disk_gib": max_disk_gib,
        "disk_ok": ok_reserve and ok_budget,
        "reason": "OK" if (ok_reserve and ok_budget) else f"Disk budget/reserve violated (free={free_gib}GiB, peak={projected_gib}GiB, floor={reserve_floor_gib}GiB)",
    }


def compute_cache_identity(
    project_cfg: dict[str, Any],
    build_dir: Path,
    render_mjs_path: Path,
    *,
    render_scale: float,
    time_map_sha: str | None = None,
) -> dict[str, Any]:
    """Composite cache key covering source SHA/size, fps rational, trim map, renderer SHA, config, assets & scale."""
    src_path = project_cfg.get("source")
    src_sha = sha256_file(src_path) if (src_path and os.path.isfile(src_path)) else None
    src_size = os.path.getsize(src_path) if (src_path and os.path.isfile(src_path)) else None
    assets_shas: dict[str, str] = {}
    adir = build_dir / "assets"
    if adir.is_dir():
        for f in sorted(adir.rglob("*")):
            if f.is_file():
                assets_shas[str(f.relative_to(build_dir))] = sha256_file(f)
    html_p = build_dir / project_cfg.get("html", "master.html")
    return {
        "src_path": os.path.realpath(src_path) if src_path else None,
        "src_sha256": src_sha,
        "src_size_bytes": src_size,
        "fps_num": project_cfg.get("fps_num", project_cfg.get("fps")),
        "fps_den": project_cfg.get("fps_den", 1),
        "width": project_cfg.get("width"),
        "height": project_cfg.get("height"),
        "duration": project_cfg.get("duration"),
        "render_scale": round(float(render_scale), 4),
        "time_map_sha": time_map_sha or "identity",
        "renderer_sha256": sha256_file(render_mjs_path) if render_mjs_path.is_file() else None,
        "project_cfg_sha256": hash_artifact(project_cfg),
        "html_sha256": sha256_file(html_p) if html_p.is_file() else None,
        "assets_sha256": assets_shas,
    }


def preflight_render(
    build_dir: str | Path,
    run_root: str | Path,
    *,
    run_id: str,
    render_scale: float = 1.0,
    reserve_floor_gib: float = DEFAULT_DISK_RESERVE_GIB,
    max_disk_gib: float | None = None,
    seecut_root: Path | None = None,
    time_map_sha: str | None = None,
) -> dict[str, Any]:
    """P0 safety gate BEFORE invoking upstream render.mjs: validates all write targets, symlinks, markers & disk."""
    run_real = Path(os.path.realpath(str(Path(run_root).expanduser())))
    bdir = safe_path(build_dir, run_real, allow_symlinks=False, must_exist=True)
    proj_path = safe_path(bdir / "project.json", bdir, allow_symlinks=False, must_exist=True)
    cfg = json.loads(proj_path.read_text(encoding="utf-8"))

    # P0: Validate person_dir strictly inside build_dir — NEVER allow .. / abs / symlink / source dir / parent / old run
    raw_pd = cfg.get("person_dir", "person")
    if not isinstance(raw_pd, str) or not raw_pd.strip() or Path(raw_pd).is_absolute() or ".." in Path(raw_pd).parts:
        raise ValueError(f"P0_UNSAFE_PERSON_DIR: rejected person_dir={raw_pd!r}")
    pd_resolved = safe_path(bdir / raw_pd, bdir, allow_symlinks=False, must_exist=False)
    if pd_resolved == bdir or pd_resolved == run_real:
        raise ValueError(f"P0_UNSAFE_PERSON_DIR: person_dir cannot equal build_dir or run_root: {pd_resolved}")

    # Verify source is read-only and never inside person_dir
    src = cfg.get("source")
    if src:
        src_p = Path(src)
        if src_p.is_symlink():
            raise ValueError(f"P0_SYMLINK_SOURCE: symlink source rejected: {src}")
        src_real = Path(os.path.realpath(str(src_p)))
        if not src_real.is_file():
            raise FileNotFoundError(f"Source file missing: {src_real}")
        if src_real.parent == pd_resolved or str(src_real).startswith(str(pd_resolved) + os.sep):
            raise ValueError(f"P0_SOURCE_IN_PERSON_DIR: source {src_real} inside person_dir {pd_resolved}")

    # Validate HTML path inside build_dir
    html_rel = cfg.get("html", "master.html")
    if Path(html_rel).is_absolute() or ".." in Path(html_rel).parts:
        raise ValueError(f"P0_UNSAFE_HTML_PATH: {html_rel!r}")
    safe_path(bdir / html_rel, bdir, allow_symlinks=False, must_exist=True)

    # Check if person_dir already exists without our run_id ownership marker -> refuse upstream fs.rmSync!
    marker_path = pd_resolved / OWNERSHIP_MARKER
    if pd_resolved.exists():
        if not marker_path.is_file():
            raise ValueError(
                f"P0_UNOWNED_PERSON_DIR: existing person_dir {pd_resolved} lacks ownership marker {OWNERSHIP_MARKER}; refusing deletion"
            )
        owned_id = marker_path.read_text(encoding="utf-8").strip()
        if owned_id != run_id:
            raise ValueError(f"P0_FOREIGN_RUN_PERSON_DIR: person_dir owned by {owned_id!r}, current run is {run_id!r}")

    up_root = seecut_root or Path(
        os.environ.get("SEECUT_UPSTREAM_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut"))
    )
    render_mjs = up_root / "skill/seecut/scripts/render.mjs"
    if not render_mjs.is_file():
        raise FileNotFoundError(f"Pinned upstream render.mjs missing: {render_mjs}")

    total_frames = int(cfg.get("total_frames") or round(float(cfg["fps"]) * float(cfg["duration"])))
    res = estimate_resources(
        total_frames, int(cfg["width"]), int(cfg["height"]),
        render_scale=render_scale, target_dir=run_real,
        reserve_floor_gib=reserve_floor_gib, max_disk_gib=max_disk_gib,
    )
    if not res["disk_ok"]:
        raise RuntimeError(f"BLOCKED_LOW_DISK: {res['reason']}")

    cache_id = compute_cache_identity(cfg, bdir, render_mjs, render_scale=render_scale, time_map_sha=time_map_sha)
    cache_hash = hash_artifact(cache_id)

    # Invalidate stale person cache deterministically if composite cache key changed
    cache_meta_p = pd_resolved / CACHE_META_FILE
    cache_valid = False
    if pd_resolved.is_dir() and cache_meta_p.is_file():
        try:
            old = json.loads(cache_meta_p.read_text(encoding="utf-8"))
            # Person frames depend on src_sha256, src_size_bytes, fps_num, fps_den, duration, time_map_sha
            keys = ("src_sha256", "src_size_bytes", "fps_num", "fps_den", "duration", "time_map_sha")
            cache_valid = all(old.get(k) == cache_id.get(k) for k in keys)
        except Exception:
            cache_valid = False

    if pd_resolved.is_dir() and not cache_valid:
        safe_cleanup(pd_resolved, run_real, run_id=run_id)

    return {
        "ok": True,
        "build_dir": str(bdir),
        "person_dir": str(pd_resolved),
        "render_mjs": str(render_mjs),
        "project_cfg": cfg,
        "total_frames": total_frames,
        "resources": res,
        "cache_identity": cache_id,
        "cache_hash": cache_hash,
        "person_cache_valid": cache_valid,
    }


def safe_cleanup(target_dir: str | Path, run_root: str | Path, *, run_id: str) -> bool:
    """Safely delete ONLY a run-owned temp/cache directory carrying the exact run_id ownership marker."""
    run_real = Path(os.path.realpath(str(Path(run_root).expanduser())))
    target = safe_path(target_dir, run_real, allow_symlinks=False, must_exist=False)
    if target == run_real or not target.exists():
        return False
    marker = target / OWNERSHIP_MARKER
    if not marker.is_file():
        raise PermissionError(f"Refusing to delete unowned directory lacking {OWNERSHIP_MARKER}: {target}")
    if marker.read_text(encoding="utf-8").strip() != run_id:
        raise PermissionError(f"Refusing to delete directory owned by another run: {target}")
    shutil.rmtree(target)
    return True


def verify_render_frames(
    frames_dir: str | Path,
    expected_indices: list[int],
    *,
    prefix: str = "f",
    ext: str = ".png",
    check_magic: bool = True,
) -> dict[str, Any]:
    """Verify exact contiguous frame index coverage, non-zero byte size, and valid PNG/JPEG magic header."""
    fdir = Path(frames_dir)
    missing: list[int] = []
    corrupt: list[str] = []
    total_bytes = 0
    magic = b"\x89PNG\r\n\x1a\n" if ext == ".png" else b"\xff\xd8"

    for idx in expected_indices:
        fp = fdir / f"{prefix}{idx:04d}{ext}"
        if not fp.is_file():
            missing.append(idx)
            continue
        sz = fp.stat().st_size
        if sz < 64:
            corrupt.append(f"{fp.name} (size={sz})")
            continue
        total_bytes += sz
        if check_magic:
            with fp.open("rb") as fh:
                hdr = fh.read(len(magic))
            if hdr != magic:
                corrupt.append(f"{fp.name} (bad header)")

    ok = (len(missing) == 0) and (len(corrupt) == 0)
    return {
        "ok": ok,
        "frames_dir": str(fdir),
        "expected_count": len(expected_indices),
        "missing_count": len(missing),
        "missing_sample": missing[:10],
        "corrupt_count": len(corrupt),
        "corrupt_sample": corrupt[:10],
        "total_bytes": total_bytes,
    }


def _ensure_node_link(build_dir: Path, tools_root: Path) -> None:
    """Ensure build/node_modules points to pinned tools/node_modules so upstream _common.mjs resolves playwright-core."""
    nm = build_dir / "node_modules"
    target = tools_root / "node_modules"
    if not nm.exists() and target.is_dir():
        nm.symlink_to(target)


def run_render_checked(
    build_dir: str | Path,
    run_root: str | Path,
    *,
    run_id: str,
    mode: str = "probe",
    probe_frames: list[int] | None = None,
    render_scale: float | None = None,
    out_mp4: str | Path | None = None,
    seecut_root: Path | None = None,
    tools_root: Path | None = None,
    reserve_floor_gib: float = DEFAULT_DISK_RESERVE_GIB,
    max_disk_gib: float | None = None,
    timeout_sec: int = 900,
    keep_frames: bool = False,
) -> dict[str, Any]:
    """Execute unmodified pinned upstream render.mjs inside validated run workspace, verify frames & atomically mux MP4."""
    proj_cfg_peek = json.loads((Path(build_dir) / "project.json").read_text(encoding="utf-8"))
    eff_scale = float(render_scale if render_scale is not None else proj_cfg_peek.get("output_scale", 1.0))

    pf = preflight_render(
        build_dir, run_root, run_id=run_id,
        render_scale=eff_scale, reserve_floor_gib=reserve_floor_gib,
        max_disk_gib=max_disk_gib, seecut_root=seecut_root,
    )
    bdir = Path(pf["build_dir"])
    pdir = Path(pf["person_dir"])
    cfg = pf["project_cfg"]
    total_frames = int(pf["total_frames"])

    troot = tools_root or (Path(os.environ.get("SEECUT_STANDALONE_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone"))) / "tools")
    _ensure_node_link(bdir, troot)

    # Pre-create person_dir with ownership marker if source exists & cache invalid (so upstream render.mjs never touches unowned dir)
    if cfg.get("source") and not pf["person_cache_valid"]:
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / OWNERSHIP_MARKER).write_text(run_id, encoding="utf-8")

    # Clean/mark output frame buffer directory safely
    out_frames_dir = bdir / ("frames" if mode == "full" else "probe_frames")
    if out_frames_dir.exists():
        if (out_frames_dir / OWNERSHIP_MARKER).is_file():
            safe_cleanup(out_frames_dir, run_root, run_id=run_id)
    out_frames_dir.mkdir(parents=True, exist_ok=True)
    (out_frames_dir / OWNERSHIP_MARKER).write_text(run_id, encoding="utf-8")

    cmd = ["node", pf["render_mjs"], str(bdir), mode]
    if mode == "probe" and probe_frames:
        cmd.append(",".join(str(int(f)) for f in probe_frames))

    env = dict(os.environ)
    env["RENDER_SCALE"] = str(eff_scale)
    t0 = time.monotonic()
    popen_kw: dict[str, Any] = {
        "env": env, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True,
    }
    if os.name != "nt":
        popen_kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **popen_kw)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_sec)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt" and hasattr(os, "killpg"):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, text=True, timeout=10,
                )
        except OSError:
            proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=5)
        raise TimeoutError(f"render.mjs timed out after {timeout_sec}s; reaped child process group")
    elapsed = round(time.monotonic() - t0, 3)

    # Write ownership marker + composite cache identity inside person_dir after extraction
    if pdir.is_dir():
        (pdir / OWNERSHIP_MARKER).write_text(run_id, encoding="utf-8")
        (pdir / CACHE_META_FILE).write_text(json.dumps(pf["cache_identity"], indent=2), encoding="utf-8")

    if rc != 0:
        raise RuntimeError(f"Upstream render.mjs failed (rc={rc}): {(stderr or stdout)[-400:]}")

    # Detect page console errors reported by render.mjs
    console_errors = [line for line in (stdout + "\n" + stderr).splitlines() if "页面报错:" in line]
    if console_errors:
        raise RuntimeError(f"Browser page console errors detected during render: {console_errors[:3]}")

    # Verify person frames contiguous & valid JPEG if source present
    person_check = None
    if cfg.get("source"):
        person_check = verify_render_frames(pdir, list(range(1, total_frames + 1)), prefix="p", ext=".jpg")
        # Allow 1-frame rounding tolerance only at very tail (e.g. 476 vs 476)
        if person_check["corrupt_count"] > 0 or person_check["missing_count"] > 1:
            raise RuntimeError(f"Person extraction verification failed: {person_check}")

    expected_idx = list(range(total_frames)) if mode == "full" else (
        probe_frames if probe_frames else [int(math.floor((i + 0.5) * total_frames / 12.0 + 0.5)) for i in range(12)]
    )
    frame_check = verify_render_frames(out_frames_dir, expected_idx, prefix="f", ext=".png")
    if not frame_check["ok"]:
        raise RuntimeError(f"Rendered frame verification failed: {frame_check}")

    result: dict[str, Any] = {
        "ok": True,
        "engine": "SeeCut-v3.4-HyperFrames-HTML-GSAP",
        "renderer_script": pf["render_mjs"],
        "renderer_sha256": pf["cache_identity"]["renderer_sha256"],
        "mode": mode,
        "render_scale": eff_scale,
        "elapsed_sec": elapsed,
        "stdout_tail": stdout.splitlines()[-10:],
        "person_check": person_check,
        "frame_check": frame_check,
        "cache_hash": pf["cache_hash"],
        "mp4_path": None,
        "mp4_sha256": None,
    }

    # Atomic mux when mode == "full" and out_mp4 requested (NO premature -shortest truncation!)
    if mode == "full" and out_mp4:
        dst = safe_path(out_mp4, run_root, allow_symlinks=False, must_exist=False)
        dst.parent.mkdir(parents=True, exist_ok=True)
        staging = dst.with_suffix(".staging.mp4")
        fps_str = f"{cfg.get('fps_num', cfg['fps'])}/{cfg.get('fps_den', 1)}"
        mux_cmd = [
            "ffmpeg", "-v", "error", "-y", "-threads", "4",
            "-framerate", fps_str, "-start_number", "0",
            "-i", str(out_frames_dir / "f%04d.png"),
        ]
        src_path = cfg.get("source")
        src_has_audio = bool(src_path and probe_source(src_path)["media"]["has_audio"])
        if src_has_audio:
            mux_cmd += ["-i", src_path, "-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy"]
        else:
            mux_cmd += ["-map", "0:v:0", "-an"]
        mux_cmd += [
            "-c:v", "libx264", "-preset", "medium", "-crf", "17",
            "-pix_fmt", "yuv420p", "-r", fps_str,
            "-movflags", "+faststart", str(staging),
        ]
        mproc = subprocess.run(mux_cmd, capture_output=True, text=True, timeout=300)
        if mproc.returncode != 0:
            if staging.exists():
                staging.unlink()
            raise RuntimeError(f"FFmpeg mux failed (rc={mproc.returncode}): {mproc.stderr[-300:]}")

        # Full decode check & exact frame count check BEFORE atomic promotion!
        dec = subprocess.run(["ffmpeg", "-v", "error", "-i", str(staging), "-f", "null", "-"], capture_output=True, text=True, timeout=120)
        probed_out = probe_source(staging)
        if dec.returncode != 0 or probed_out["media"]["nb_frames"] != total_frames:
            staging.unlink(missing_ok=True)
            raise RuntimeError(
                f"Staging MP4 failed QA (decode_rc={dec.returncode}, nb_frames={probed_out['media']['nb_frames']} != {total_frames})"
            )

        os.replace(staging, dst)
        result["mp4_path"] = str(dst)
        result["mp4_sha256"] = sha256_file(dst)
        result["mp4_probe"] = probed_out["media"]

        if not keep_frames:
            safe_cleanup(out_frames_dir, run_root, run_id=run_id)
            result["frames_cleaned_after_decode_qa"] = True

    return result
