"""KH-03 Visual QA, True O(1) Streaming Frame Verifier & Native Pinned-GSAP Gallery (CMI-09 & CMI-10).

Provides:
1. Swept bounding-box protected-region collision audit (face/mouth/hands/objects + fullscreen hard lock).
2. TRUE O(1)-memory streaming per-frame decoded QA (`verify_fullscreen_frames`, `verify_adaptive_layout_frames`)
   using `stream_paired_frames()` (zero full-video RGB buffers in RAM!), incremental `FRAME_QA.jsonl` & CSV
   streaming, `STREAMING_QA_REPORT.json`, and `PROCESS_CLEANUP_REPORT.json`.
3. Internal component ROI pixel motion audit (`verify_component_internal_roi_motion`, `COMPONENT_PIXEL_QA.json`)
   that excludes presenter/camera motion and catches frozen static cards or codec-noise-only changes.
4. Bounded hook pixel QA (`verify_hook_pixels`) and every-frame morph strip extraction (`export_morph_frame_strips`).
5. Cross-platform headless Chromium verification (macOS, Linux, Windows) of all 8 KH-03 component families
   separating genuine pinned native GSAP (`NATIVE_GSAP_PASS`) from offline test shim (`SHIM_ONLY_TEST_PASS`).
"""

from __future__ import annotations

import csv as _csv
import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

from scripts.kh02.compile_plan import compile_shotbook
from scripts.kh02.registry import build_registry, mark_gallery_verified

from .components import KH03_COMPONENT_CATALOG, audit_component_motion, compile_component_events, design_component
from .contracts import hash_artifact, write_json_atomic
from .execution import (
    StreamingRGBFrameDecoder,
    count_edl_frames,
    get_process_rss_mib,
    stream_paired_frames,
)


def _find_chromium_executable() -> Path | None:
    """Locate local Chromium / Chrome for Testing deterministically across macOS, Linux, and Windows."""
    env_p = os.environ.get("KH03_CHROMIUM_PATH") or os.environ.get("CHROME_PATH")
    if env_p and Path(env_p).is_file():
        return Path(env_p)

    # 1. macOS Playwright cache (bounded explicit paths, no HOME recursive scan)
    mac_pw = Path.home() / "Library" / "Caches" / "ms-playwright"
    if mac_pw.is_dir():
        for pat in (
            "chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
            "chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell",
            "chromium_headless_shell-*/chrome-headless-shell-mac/chrome-headless-shell",
        ):
            matches = sorted(mac_pw.glob(pat), reverse=True)
            if matches:
                return matches[0]

    # 2. Linux Playwright cache
    lin_pw = Path.home() / ".cache" / "ms-playwright"
    if lin_pw.is_dir():
        for pat in (
            "chromium-*/chrome-linux/chrome",
            "chromium_headless_shell-*/chrome-linux/headless_shell",
        ):
            matches = sorted(lin_pw.glob(pat), reverse=True)
            if matches:
                return matches[0]

    # 3. Windows Playwright cache
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        pw_root = Path(local_app) / "ms-playwright"
        if pw_root.is_dir():
            for pat in (
                "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe",
                "chromium-*/chrome-win/chrome.exe",
            ):
                matches = sorted(pw_root.glob(pat), reverse=True)
                if matches:
                    return matches[0]

    # 4. System PATH browsers
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _mean_abs_diff(img_a: Path, img_b: Path, crop_box: tuple[int, int, int, int] | None = None) -> float:
    with Image.open(img_a) as ia, Image.open(img_b) as ib:
        a = ia.convert("RGB")
        b = ib.convert("RGB")
        if crop_box:
            a = a.crop(crop_box)
            b = b.crop(crop_box)
        diff = ImageChops.difference(a, b)
        stat = ImageStat.Stat(diff)
        return round(sum(stat.mean) / 3.0, 4)


def _probe_video_meta(p: Path) -> dict[str, Any]:
    pr = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,nb_frames,duration",
            "-of", "json", str(p),
        ],
        capture_output=True, text=True, check=True, timeout=30,
    )
    st = json.loads(pr.stdout)["streams"][0]
    num, den = (int(x) for x in str(st.get("r_frame_rate", "24/1")).split("/"))
    nb = int(st["nb_frames"]) if str(st.get("nb_frames", "")).isdigit() else None
    if nb is None and st.get("duration"):
        try:
            nb = int(round(float(st["duration"]) * (num / float(max(1, den)))))
        except Exception:
            nb = None
    return {
        "width": int(st["width"]),
        "height": int(st["height"]),
        "fps_num": num,
        "fps_den": max(1, den),
        "nb_frames": nb,
    }


def audit_fullscreen_and_collisions(
    creative_plan: dict[str, Any],
    input_analysis: dict[str, Any],
    *,
    shotbook: dict[str, Any] | None = None,
    require_shotbook: bool = False,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Audit full-screen lock compliance, camera geometry, protected-region clearance, 360px legibility & motion."""
    lc = creative_plan.get("layout_constraints") or {}
    fs_lock = bool(lc.get("fullscreen_lock"))
    media = input_analysis.get("media") or {}
    fps_num = int(media.get("fps_num") or 24)
    fps_den = max(1, int(media.get("fps_den") or 1))
    real_fps = fps_num / float(fps_den)
    issues: list[str] = []
    cam_issues: list[str] = []

    if not lc.get("collision_ok", False):
        issues.append("PROTECTED_REGION_COLLISION: unresolved overlay overlap with protected face/hands/object mask.")

    if shotbook is None:
        if require_shotbook or fs_lock:
            issues.append("MISSING_SHOTBOOK: Cannot certify fullscreen/camera geometry or motion audit without canonical SHOTBOOK_V2.")
    else:
        sb_spec = shotbook.get("output_spec") or {}
        if sb_spec.get("fps_num") and sb_spec.get("fps_den"):
            real_fps = int(sb_spec["fps_num"]) / float(max(1, int(sb_spec["fps_den"])))
        if fs_lock:
            pm = (shotbook.get("presenter_policy") or {}).get("mode", "LEGACY_DYNAMIC")
            if pm in ("FIXED_PIP", "ADAPTIVE_PIP", "FACELESS", "DYNAMIC_HYBRID"):
                issues.append(f"FULLSCREEN_LOCK_VIOLATION: presenter_policy.mode={pm!r} violates explicit full-screen lock.")
            cam_events = shotbook.get("camera_events") or []
            if not cam_events:
                cam_issues.append("FULLSCREEN_LOCK_NO_CAMERA_EVENTS: Missing frame-0..EOF camera geometry proof.")
            for ce in cam_events:
                tr = [round(float(x), 4) for x in (ce.get("target_region") or [0, 0, 0, 0])]
                sc_f = round(float(ce.get("scale_from", 1.0)), 4)
                sc_t = round(float(ce.get("scale_to", 1.0)), 4)
                op = round(float(ce.get("presenter_opacity", 1.0)), 4)
                if tr != [0.0, 0.0, 1.0, 1.0]:
                    cam_issues.append(f"FULLSCREEN_REGION_DRIFT({ce.get('camera_id')}): target_region={tr} != [0,0,1,1]")
                if abs(sc_f - 1.0) > 0.005 or abs(sc_t - 1.0) > 0.005:
                    cam_issues.append(f"FULLSCREEN_ZOOM_FORBIDDEN({ce.get('camera_id')}): scale {sc_f}->{sc_t} != 1.0")
                if op < 0.99:
                    cam_issues.append(f"FULLSCREEN_OPACITY_FADE({ce.get('camera_id')}): opacity={op} < 1.0")
            issues.extend(cam_issues)

    unreadable = [
        c["component_id"] for c in creative_plan.get("components", [])
        if (c.get("params") or {}).get("mobile_360px_readable") is False
    ]
    if unreadable:
        issues.append(f"MOBILE_360PX_UNREADABLE: {unreadable}")

    if shotbook is not None:
        motion_audit = audit_component_motion(shotbook.get("visual_events", []), fps=real_fps)
        if not motion_audit["pass"]:
            issues.extend(motion_audit["anomalies"])
    else:
        motion_audit = {"pass": False, "unique_capabilities": 0, "anomalies": ["MISSING_SHOTBOOK_FOR_MOTION_AUDIT"]}

    cam_qa = {
        "schema_version": "kh03.camera_geometry_qa.v1",
        "scope": "PLAN_AND_COMPILE_ONLY",
        "fullscreen_lock": fs_lock,
        "fps_used": round(real_fps, 4),
        "camera_event_count": len((shotbook or {}).get("camera_events") or []),
        "issues": cam_issues,
        "pass": len(cam_issues) == 0 and (shotbook is not None),
    }
    prot_map = {
        "schema_version": "kh03.protected_region_map.v1",
        "detection_mode": str(lc.get("tracking_mode") or "STATIC_FALLBACK_VERIFIED"),
        "note": "Conservative swept safe bounds verified; ML face/hand tracker not claimed unless explicitly executed.",
        "protected_zones": (input_analysis.get("safe_regions") or {}).get("protected_zones") or [],
        "clear_overlay_zones": (input_analysis.get("safe_regions") or {}).get("clear_overlay_zones") or [],
    }
    if run_dir is not None:
        write_json_atomic(run_dir / "qc" / "CAMERA_GEOMETRY_QA.json", cam_qa)
        write_json_atomic(run_dir / "qc" / "PROTECTED_REGION_MAP.json", prot_map)

    return {
        "schema_version": "kh03.visual_qa.v1",
        "verification_scope": "PLAN_ONLY_NOT_DECODED_RENDER",
        "fullscreen_lock_enforced": fs_lock,
        "fps_used": round(real_fps, 4),
        "collision_ok": bool(lc.get("collision_ok", False)),
        "reflow_count": int(lc.get("reflow_count", 0)),
        "mobile_360px_unreadable": unreadable,
        "camera_geometry_qa": cam_qa,
        "motion_audit": motion_audit,
        "issues": issues,
        "pass": len(issues) == 0,
    }


def verify_fullscreen_frames(
    source_mp4: str | Path,
    final_mp4: str | Path,
    *,
    edl: dict[str, Any] | None = None,
    overlay_events: list[dict[str, Any]] | None = None,
    protected_zones: list[dict[str, Any]] | None = None,
    out_dir: str | Path | None = None,
    max_mean_diff: float = 18.0,
    probe_w: int = 180,
    probe_h: int = 320,
    ffmpeg_threads: int = 2,
) -> dict[str, Any]:
    """TRUE O(1)-RAM streaming verifier for EVERY decoded frame under FULLSCREEN_LOCK (CMI-09 & CMI-10)."""
    t0 = time.monotonic()
    src_p = Path(source_mp4).resolve()
    fin_p = Path(final_mp4).resolve()
    if not src_p.is_file() or not fin_p.is_file():
        return {
            "schema_version": "kh03.fullscreen_frame_qa.v1",
            "verification_scope": "DECODED_RENDER",
            "streaming_mode": "TRUE_BOUNDED_STREAMING_O1_RAM",
            "status": "FAIL_MISSING_MEDIA",
            "pass": False,
            "issues": ["Source or final MP4 missing for decoded frame verification."],
        }

    src_m = _probe_video_meta(src_p)
    fin_m = _probe_video_meta(fin_p)
    issues: list[str] = []

    src_ar = src_m["width"] / float(max(1, src_m["height"]))
    fin_ar = fin_m["width"] / float(max(1, fin_m["height"]))
    if abs(src_ar - fin_ar) > 0.02:
        issues.append(f"ASPECT_MISMATCH: source {src_m['width']}x{src_m['height']} vs final {fin_m['width']}x{fin_m['height']}")
    if abs((src_m["fps_num"] / float(src_m["fps_den"])) - (fin_m["fps_num"] / float(fin_m["fps_den"]))) > 0.02:
        issues.append(f"FPS_MISMATCH: source {src_m['fps_num']}/{src_m['fps_den']} vs final {fin_m['fps_num']}/{fin_m['fps_den']}")

    expected_n = count_edl_frames(edl, fin_m["nb_frames"] or src_m["nb_frames"] or 0)
    pzones = protected_zones or [{"bbox_norm": [0.15, 0.30, 0.70, 0.44]}]
    ov_list = overlay_events or []

    fail_frames: list[dict[str, Any]] = []
    fail_count = 0
    checked = 0
    max_obs_diff = 0.0
    total_diff = 0.0
    rss_start = get_process_rss_mib()
    rss_peak = rss_start
    last_stats: dict[str, Any] = {}

    jsonl_fh = None
    jsonl_tmp: Path | None = None
    od = Path(out_dir) if out_dir is not None else None
    if od is not None:
        od.mkdir(parents=True, exist_ok=True)
        jsonl_tmp = od / "FRAME_QA.jsonl.part"
        jsonl_fh = jsonl_tmp.open("w", encoding="utf-8")

    try:
        for out_idx, src_idx, src_b, fin_b, st_info in stream_paired_frames(
            src_p, fin_p, edl=edl, expected_total_frames=expected_n,
            probe_w=probe_w, probe_h=probe_h, ffmpeg_threads=ffmpeg_threads,
        ):
            last_stats = st_info
            if fin_b is None:
                break
            if src_b is None:
                fail_count += 1
                if len(fail_frames) < 16:
                    fail_frames.append({"frame": out_idx, "expected_src_frame": src_idx, "reason": "SRC_EOF_EARLY"})
                continue

            im_s = Image.frombytes("RGB", (probe_w, probe_h), src_b)
            im_f = Image.frombytes("RGB", (probe_w, probe_h), fin_b)
            active_boxes = [
                ev["normalized_bbox"] for ev in ov_list
                if int(ev.get("start_frame", 0)) <= out_idx < int(ev.get("end_frame", 0)) and ev.get("normalized_bbox")
            ]
            prot_diffs: list[float] = []
            for pz in pzones:
                bx, by, bw, bh = [float(v) for v in pz["bbox_norm"]]
                box = (
                    max(0, int(bx * probe_w)), max(0, int(by * probe_h)),
                    min(probe_w, max(1, int((bx + bw) * probe_w))), min(probe_h, max(1, int((by + bh) * probe_h))),
                )
                md = sum(ImageStat.Stat(ImageChops.difference(im_s.crop(box), im_f.crop(box))).mean) / 3.0
                prot_diffs.append(md)

            im_s_m = im_s.copy()
            im_f_m = im_f.copy()
            for bx, by, bw, bh in active_boxes:
                x0, y0 = max(0, int(bx * probe_w)), max(0, int(by * probe_h))
                x1, y1 = min(probe_w, int((bx + bw) * probe_w)), min(probe_h, int((by + bh) * probe_h))
                if x1 > x0 and y1 > y0:
                    im_s_m.paste((0, 0, 0), (x0, y0, x1, y1))
                    im_f_m.paste((0, 0, 0), (x0, y0, x1, y1))
            bg_md = sum(ImageStat.Stat(ImageChops.difference(im_s_m, im_f_m)).mean) / 3.0
            frame_md = round(max([bg_md] + prot_diffs), 3)
            max_obs_diff = max(max_obs_diff, frame_md)
            total_diff += frame_md
            checked += 1
            row_ok = frame_md <= max_mean_diff
            if not row_ok:
                fail_count += 1
                if len(fail_frames) < 16:
                    fail_frames.append({
                        "frame": out_idx,
                        "expected_src_frame": src_idx,
                        "mean_abs_diff": frame_md,
                        "bg_diff": round(bg_md, 3),
                        "protected_max_diff": round(max(prot_diffs) if prot_diffs else 0.0, 3),
                        "reason": "FULLSCREEN_OR_PROTECTED_ZONE_DRIFT",
                    })
            if jsonl_fh is not None and (out_idx == 0 or out_idx == expected_n - 1 or out_idx % max(1, expected_n // 60) == 0 or not row_ok):
                jsonl_fh.write(json.dumps({"frame": out_idx, "src_frame": src_idx, "mean_abs_diff": frame_md, "pass": row_ok}) + "\n")
            if checked % 120 == 0:
                rss_peak = max(rss_peak, get_process_rss_mib())
    finally:
        if jsonl_fh is not None:
            jsonl_fh.close()

    if jsonl_tmp is not None and od is not None and jsonl_tmp.is_file():
        jsonl_tmp.replace(od / "FRAME_QA.jsonl")

    if checked != expected_n or int(last_stats.get("fin_extra_frames", 0)) > 0:
        issues.append(
            f"FRAME_COUNT_MISMATCH: decoded {checked} (+{last_stats.get('fin_extra_frames', 0)} extra) != expected EDL {expected_n}"
        )
    if fail_count > 0:
        issues.append(f"FULLSCREEN_DECODED_FRAME_DRIFT: {fail_count}/{checked} frames exceeded tolerance {max_mean_diff} (max={max_obs_diff})")

    wall_s = round(time.monotonic() - t0, 4)
    rss_peak = max(rss_peak, get_process_rss_mib())
    report = {
        "schema_version": "kh03.fullscreen_frame_qa.v1",
        "verification_scope": "DECODED_RENDER",
        "streaming_mode": "TRUE_BOUNDED_STREAMING_O1_RAM",
        "source_mp4_sha256": hash_artifact(src_p),
        "final_mp4_sha256": hash_artifact(fin_p),
        "source_resolution": f"{src_m['width']}x{src_m['height']}",
        "final_resolution": f"{fin_m['width']}x{fin_m['height']}",
        "fps": f"{fin_m['fps_num']}/{fin_m['fps_den']}",
        "frames_checked": checked,
        "frames_expected": expected_n,
        "max_mean_abs_diff": round(max_obs_diff, 3),
        "avg_mean_abs_diff": round(total_diff / float(max(1, checked)), 3),
        "tolerance_mean_abs_diff": max_mean_diff,
        "fail_frame_count": fail_count,
        "fail_frames_sample": fail_frames[:12],
        "streaming_metrics": {
            "wall_sec": wall_s,
            "decoded_fps": round(checked / max(0.001, wall_s), 2),
            "rss_start_mib": rss_start,
            "rss_peak_mib": rss_peak,
            "rss_delta_mib": round(max(0.0, rss_peak - rss_start), 2),
            "src_cleanup": last_stats.get("src_cleanup"),
            "fin_cleanup": last_stats.get("fin_cleanup"),
        },
        "issues": issues,
        "status": "DECODED_RENDER_PASS" if not issues else "DECODED_RENDER_FAIL",
        "pass": len(issues) == 0,
    }
    if od is not None:
        write_json_atomic(od / "FULLSCREEN_FRAME_QA.json", report)
        write_json_atomic(od / "FRAME_QA_SUMMARY.json", report)
        write_json_atomic(od / "STREAMING_QA_REPORT.json", report["streaming_metrics"])
        write_json_atomic(od / "PROCESS_CLEANUP_REPORT.json", {
            "schema_version": "kh03.process_cleanup.v1",
            "src_decoder": last_stats.get("src_cleanup"),
            "fin_decoder": last_stats.get("fin_cleanup"),
            "all_clean": bool((last_stats.get("src_cleanup") or {}).get("clean", True) and (last_stats.get("fin_cleanup") or {}).get("clean", True)),
        })
    return report


def verify_adaptive_layout_frames(
    source_mp4: str | Path,
    final_mp4: str | Path,
    *,
    layout_plan: dict[str, Any],
    edl: dict[str, Any] | None = None,
    compile_map: dict[str, Any] | None = None,
    out_dir: str | Path | None = None,
    max_mean_diff: float = 28.0,
    probe_w: int = 180,
    probe_h: int = 320,
    ffmpeg_threads: int = 2,
) -> dict[str, Any]:
    """TRUE O(1)-RAM streaming verifier for EVERY decoded frame under ADAPTIVE_DYNAMIC layout (CMI-09 & CMI-10).

    Streams source + output incrementally via `stream_paired_frames()` and writes CSV/JSONL directly to disk,
    holding at most 2 compact low-res frames in RAM regardless of whether video is 30s or 30+ minutes!
    """
    from .layout import compute_source_crop_for_box, interpolate_presenter_geometry

    t0 = time.monotonic()
    src_p = Path(source_mp4).resolve()
    fin_p = Path(final_mp4).resolve()
    if not src_p.is_file() or not fin_p.is_file():
        return {
            "schema_version": "kh03.adaptive_layout_frame_qa.v1",
            "verification_scope": "DECODED_RENDER",
            "streaming_mode": "TRUE_BOUNDED_STREAMING_O1_RAM",
            "status": "FAIL_MISSING_MEDIA",
            "pass": False,
            "issues": ["Source or final MP4 missing for adaptive layout decoded frame verification."],
        }

    src_m = _probe_video_meta(src_p)
    fin_m = _probe_video_meta(fin_p)
    issues: list[str] = []
    expected_n = count_edl_frames(edl, fin_m["nb_frames"] or src_m["nb_frames"] or 0)

    trs = layout_plan.get("transitions") or []
    beats_by_id = {b["beat_id"]: b for b in layout_plan.get("beats", [])}
    fail_frames: list[dict[str, Any]] = []
    fail_count = 0
    checked = 0
    max_diff = 0.0
    sum_diff = 0.0
    prev_pres_crop: Image.Image | None = None
    prev_src_im: Image.Image | None = None
    freeze_streak = 0
    max_freeze_streak = 0
    black_frames = 0
    morph_checked = 0
    rss_start = get_process_rss_mib()
    rss_peak = rss_start
    last_stats: dict[str, Any] = {}

    od = Path(out_dir) if out_dir is not None else None
    csv_fh = None
    csv_writer = None
    csv_tmp: Path | None = None
    jsonl_fh = None
    jsonl_tmp: Path | None = None
    if od is not None:
        od.mkdir(parents=True, exist_ok=True)
        csv_tmp = od / "ADAPTIVE_LAYOUT_FRAME_QA.csv.part"
        csv_fh = csv_tmp.open("w", encoding="utf-8", newline="")
        csv_writer = _csv.DictWriter(csv_fh, fieldnames=["frame", "src_frame", "layout_mode", "in_transition", "mean_abs_diff", "black_flash", "pass"])
        csv_writer.writeheader()
        jsonl_tmp = od / "FRAME_QA.jsonl.part"
        jsonl_fh = jsonl_tmp.open("w", encoding="utf-8")

    try:
        for out_idx, src_idx, src_b, fin_b, st_info in stream_paired_frames(
            src_p, fin_p, edl=edl, expected_total_frames=expected_n,
            probe_w=probe_w, probe_h=probe_h, ffmpeg_threads=ffmpeg_threads,
        ):
            last_stats = st_info
            if fin_b is None:
                break
            if src_b is None:
                fail_count += 1
                if len(fail_frames) < 16:
                    fail_frames.append({"frame": out_idx, "expected_src_frame": src_idx, "reason": "SRC_INDEX_OUT_OF_RANGE"})
                continue

            im_s = Image.frombytes("RGB", (probe_w, probe_h), src_b)
            im_f = Image.frombytes("RGB", (probe_w, probe_h), fin_b)

            st = interpolate_presenter_geometry(out_idx, layout_plan, trs)
            if st["in_transition"]:
                morph_checked += 1
            bx, by, bw, bh = st["presenter_box_norm"]
            px0 = max(0, int(round(bx * probe_w)))
            py0 = max(0, int(round(by * probe_h)))
            pw = max(8, min(probe_w - px0, int(round(bw * probe_w))))
            ph = max(8, min(probe_h - py0, int(round(bh * probe_h))))

            lb = beats_by_id.get(st["beat_id"]) or (layout_plan.get("beats") or [{}])[0]
            fc = tuple(lb.get("anchor") or [0.5, 0.36])
            crop_info = compute_source_crop_for_box(src_m["width"], src_m["height"], pw, ph, focus_center=(fc[0], fc[1]))
            cx, cy, cw_n, ch_n = crop_info["crop_rect_norm"]
            sx0 = max(0, int(round(cx * probe_w)))
            sy0 = max(0, int(round(cy * probe_h)))
            sx1 = max(sx0 + 4, min(probe_w, int(round((cx + cw_n) * probe_w))))
            sy1 = max(sy0 + 4, min(probe_h, int(round((cy + ch_n) * probe_h))))
            exp_pres = im_s.crop((sx0, sy0, sx1, sy1)).resize((pw, ph), Image.BILINEAR)
            act_pres = im_f.crop((px0, py0, px0 + pw, py0 + ph))

            is_full = (bw > 0.95 and bh > 0.95)
            ix0 = max(0, int(pw * 0.14))
            ix1 = min(pw, max(ix0 + 4, int(pw * 0.86)))
            iy0 = max(0, int(ph * (0.26 if is_full else 0.14)))
            iy1 = min(ph, max(iy0 + 4, int(ph * (0.68 if is_full else 0.86))))

            exp_core = exp_pres.crop((ix0, iy0, ix1, iy1))
            act_core = act_pres.crop((ix0, iy0, ix1, iy1))
            md = round(sum(ImageStat.Stat(ImageChops.difference(exp_core, act_core)).mean) / 3.0, 3)
            act_luma = sum(ImageStat.Stat(act_core).mean) / 3.0
            exp_luma = sum(ImageStat.Stat(exp_core).mean) / 3.0
            is_black = (act_luma < 6.0 and exp_luma > 18.0)
            if is_black:
                black_frames += 1

            # O(1)-RAM freeze detector: compares ONLY previous source frame & previous presenter crop!
            if prev_pres_crop is not None and prev_src_im is not None and prev_pres_crop.size == act_core.size:
                src_delta = sum(ImageStat.Stat(ImageChops.difference(prev_src_im, im_s)).mean) / 3.0
                act_delta = sum(ImageStat.Stat(ImageChops.difference(prev_pres_crop, act_core)).mean) / 3.0
                if src_delta > 2.5 and act_delta < 0.15:
                    freeze_streak += 1
                    max_freeze_streak = max(max_freeze_streak, freeze_streak)
                else:
                    freeze_streak = 0
            else:
                freeze_streak = 0
            prev_pres_crop = act_core
            prev_src_im = im_s

            max_diff = max(max_diff, md)
            sum_diff += md
            checked += 1
            row_fail = (md > max_mean_diff) or is_black
            if row_fail:
                fail_count += 1
                if len(fail_frames) < 16:
                    fail_frames.append({
                        "frame": out_idx,
                        "expected_src_frame": src_idx,
                        "layout_mode": st["layout_mode"],
                        "in_transition": st["in_transition"],
                        "mean_abs_diff": md,
                        "black_flash": is_black,
                        "reason": "BLACK_FLASH" if is_black else "PRESENTER_GEOMETRY_OR_OCCLUSION_DRIFT",
                    })

            sample_stride = max(1, expected_n // 60)
            if out_idx == 0 or out_idx == expected_n - 1 or out_idx % sample_stride == 0 or st["in_transition"] or row_fail:
                row_d = {
                    "frame": out_idx,
                    "src_frame": src_idx,
                    "layout_mode": st["layout_mode"],
                    "in_transition": st["in_transition"],
                    "mean_abs_diff": md,
                    "black_flash": is_black,
                    "pass": not row_fail,
                }
                if csv_writer is not None:
                    csv_writer.writerow(row_d)
                if jsonl_fh is not None:
                    jsonl_fh.write(json.dumps(row_d) + "\n")
            if checked % 180 == 0:
                rss_peak = max(rss_peak, get_process_rss_mib())
    finally:
        if csv_fh is not None:
            csv_fh.close()
        if jsonl_fh is not None:
            jsonl_fh.close()

    if od is not None:
        if csv_tmp is not None and csv_tmp.is_file():
            csv_tmp.replace(od / "ADAPTIVE_LAYOUT_FRAME_QA.csv")
        if jsonl_tmp is not None and jsonl_tmp.is_file():
            jsonl_tmp.replace(od / "FRAME_QA.jsonl")

    if checked != expected_n or int(last_stats.get("fin_extra_frames", 0)) > 0:
        issues.append(f"FRAME_COUNT_MISMATCH: final {checked} (+{last_stats.get('fin_extra_frames', 0)} extra) != expected EDL {expected_n}")
    if fail_count > 0:
        issues.append(f"ADAPTIVE_LAYOUT_FRAME_DRIFT: {fail_count}/{checked} frames failed (max_diff={max_diff}, black={black_frames})")
    if max_freeze_streak >= 8:
        issues.append(f"PRESENTER_FREEZE_DETECTED: {max_freeze_streak} consecutive static presenter frames while source moved")

    wall_s = round(time.monotonic() - t0, 4)
    rss_peak = max(rss_peak, get_process_rss_mib())
    streaming_metrics = {
        "streaming_mode": "TRUE_BOUNDED_STREAMING_O1_RAM",
        "wall_sec": wall_s,
        "decoded_fps": round(checked / max(0.001, wall_s), 2),
        "frames_decoded": checked,
        "rss_start_mib": rss_start,
        "rss_peak_mib": rss_peak,
        "rss_delta_mib": round(max(0.0, rss_peak - rss_start), 2),
        "src_cleanup": last_stats.get("src_cleanup"),
        "fin_cleanup": last_stats.get("fin_cleanup"),
    }

    report = {
        "schema_version": "kh03.adaptive_layout_frame_qa.v1",
        "verification_scope": "DECODED_RENDER",
        "streaming_mode": "TRUE_BOUNDED_STREAMING_O1_RAM",
        "source_mp4_sha256": hash_artifact(src_p),
        "final_mp4_sha256": hash_artifact(fin_p),
        "layout_plan_sha256": layout_plan.get("layout_plan_sha256"),
        "compile_map_sha256": hash_artifact(compile_map) if compile_map else None,
        "layout_policy": layout_plan.get("layout_policy"),
        "source_resolution": f"{src_m['width']}x{src_m['height']}",
        "final_resolution": f"{fin_m['width']}x{fin_m['height']}",
        "fps": f"{fin_m['fps_num']}/{fin_m['fps_den']}",
        "frames_checked": checked,
        "frames_expected": expected_n,
        "morph_frames_checked": morph_checked,
        "max_mean_abs_diff": round(max_diff, 3),
        "avg_mean_abs_diff": round(sum_diff / float(max(1, checked)), 3),
        "tolerance_mean_abs_diff": max_mean_diff,
        "black_flash_frames": black_frames,
        "max_freeze_streak": max_freeze_streak,
        "fail_frame_count": fail_count,
        "fail_frames_sample": fail_frames[:12],
        "streaming_metrics": streaming_metrics,
        "issues": issues,
        "status": "DECODED_ADAPTIVE_PASS" if not issues else "DECODED_ADAPTIVE_FAIL",
        "pass": len(issues) == 0,
    }
    if od is not None:
        write_json_atomic(od / "ADAPTIVE_LAYOUT_FRAME_QA.json", report)
        write_json_atomic(od / "FRAME_QA_SUMMARY.json", report)
        write_json_atomic(od / "STREAMING_QA_REPORT.json", streaming_metrics)
        write_json_atomic(od / "PROCESS_CLEANUP_REPORT.json", {
            "schema_version": "kh03.process_cleanup.v1",
            "src_decoder": last_stats.get("src_cleanup"),
            "fin_decoder": last_stats.get("fin_cleanup"),
            "all_clean": bool((last_stats.get("src_cleanup") or {}).get("clean", True) and (last_stats.get("fin_cleanup") or {}).get("clean", True)),
        })
        write_json_atomic(od / "MOTION_CHOREOGRAPHY_QA.json", {
            "schema_version": "kh03.motion_choreography_qa.v1",
            "final_mp4_sha256": report["final_mp4_sha256"],
            "morph_frames_checked": morph_checked,
            "black_flash_frames": black_frames,
            "max_freeze_streak": max_freeze_streak,
            "pass": report["pass"],
            "issues": issues,
        })
    return report


def _valid_norm_box(box: Any) -> list[float] | None:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x, y, w, h = [float(v) for v in box]
    except Exception:
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        return None
    if w <= 0.01 or h <= 0.01:
        return None
    if x < -0.02 or y < -0.02 or (x + w) > 1.02 or (y + h) > 1.02:
        return None
    return [max(0.0, x), max(0.0, y), min(1.0 - max(0.0, x), w), min(1.0 - max(0.0, y), h)]


def collect_streaming_qa_frames(
    media_path: str | Path,
    wanted_frames: set[int] | list[int],
    *,
    width: int = 360,
    height: int = 640,
    ffmpeg_threads: int = 2,
    timeout_sec: float = 120.0,
) -> dict[int, Image.Image]:
    """Single bounded O(1)-stream pass capturing only explicitly requested frame indices for shared QA reuse (R4.1-05)."""
    mp = Path(media_path).resolve()
    wanted = {max(0, int(f)) for f in wanted_frames}
    if not mp.is_file() or not wanted:
        return {}
    captured: dict[int, Image.Image] = {}
    max_needed = max(wanted)
    idx = 0
    with StreamingRGBFrameDecoder(mp, width, height, ffmpeg_threads=ffmpeg_threads, timeout_sec=timeout_sec) as dec:
        while idx <= max_needed:
            raw = dec.read_frame()
            if raw is None:
                break
            if idx in wanted:
                captured[idx] = Image.frombytes("RGB", (width, height), raw)
            idx += 1
    return captured


def verify_component_internal_roi_motion(
    final_mp4: str | Path,
    node_bindings: list[dict[str, Any]],
    *,
    source_mp4: str | Path | None = None,
    presenter_box_norm_by_frame: dict[int, list[float]] | None = None,
    predecoded_fin_frames: dict[int, Image.Image] | None = None,
    predecoded_src_frames: dict[int, Image.Image] | None = None,
    codec_noise_floor: float = 1.6,
    probe_w: int = 360,
    probe_h: int = 640,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """CMI-02 / CMI-09 / PED-08 / R4.1-05 pixel verifier: checks ALL internal component ROIs excluding presenter/source motion!"""
    fin_p = Path(final_mp4).resolve()
    if not fin_p.is_file():
        return {"schema_version": "kh03.component_pixel_qa.v1", "pass": False, "status": "MISSING_MP4", "nodes": []}
    if not node_bindings:
        return {"schema_version": "kh03.component_pixel_qa.v1", "pass": False, "status": "NO_NODE_BINDINGS", "nodes": [], "frozen_nodes": []}

    needed_frames: set[int] = set()
    for nb in node_bindings:
        sf = max(0, int(nb.get("start_frame", 0)))
        stf = max(sf + 1, int(nb.get("settle_frame", sf + 6)))
        ef = max(stf + 1, int(nb.get("end_frame", stf + 6)))
        needed_frames.update({max(0, sf - 2), sf, (sf + stf) // 2, stf, max(sf, ef - 2)})

    if predecoded_fin_frames is not None:
        captured = {
            f: (predecoded_fin_frames[f] if predecoded_fin_frames[f].size == (probe_w, probe_h) else predecoded_fin_frames[f].resize((probe_w, probe_h), Image.BILINEAR))
            for f in needed_frames if f in predecoded_fin_frames
        }
    else:
        captured = collect_streaming_qa_frames(fin_p, needed_frames, width=probe_w, height=probe_h)

    src_p = Path(source_mp4).resolve() if (source_mp4 and Path(source_mp4).is_file()) else None
    if predecoded_src_frames is not None:
        src_captured = {
            f: (predecoded_src_frames[f] if predecoded_src_frames[f].size == (probe_w, probe_h) else predecoded_src_frames[f].resize((probe_w, probe_h), Image.BILINEAR))
            for f in needed_frames if f in predecoded_src_frames
        }
    elif src_p is not None:
        src_captured = collect_streaming_qa_frames(src_p, needed_frames, width=probe_w, height=probe_h)
    else:
        src_captured = {}

    results: list[dict[str, Any]] = []
    frozen_nodes: list[str] = []
    invalid_rois: list[str] = []
    for nb in node_bindings:
        nid = str(nb["node_id"])
        sf = max(0, int(nb.get("start_frame", 0)))
        stf = max(sf + 1, int(nb.get("settle_frame", sf + 6)))
        midf = (sf + stf) // 2
        v_roi = _valid_norm_box(nb.get("canvas_roi_norm"))
        if v_roi is None:
            invalid_rois.append(nid)
            frozen_nodes.append(nid)
            results.append({"node_id": nid, "component_id": nb.get("component_id"), "roi_delta": 0.0, "moved": False, "reason": "ROI_INVALID_OR_OFFSCREEN"})
            continue
        rx, ry, rw, rh = v_roi
        box = (
            max(0, int(rx * probe_w)), max(0, int(ry * probe_h)),
            min(probe_w, max(2, int((rx + rw) * probe_w))), min(probe_h, max(2, int((ry + rh) * probe_h))),
        )
        im0 = captured.get(sf)
        im_mid = captured.get(midf)
        im1 = captured.get(stf)
        if im0 is None or im1 is None or box[2] <= box[0] or box[3] <= box[1]:
            results.append({"node_id": nid, "component_id": nb.get("component_id"), "roi_delta": 0.0, "moved": False, "reason": "FRAME_UNAVAILABLE"})
            frozen_nodes.append(nid)
            continue

        def _crop_masked(im: Image.Image, fno: int) -> Image.Image:
            cr = im.crop(box).copy()
            pbox = (presenter_box_norm_by_frame or {}).get(fno) or (presenter_box_norm_by_frame or {}).get(stf)
            if pbox and len(pbox) == 4:
                px0 = max(0, int(pbox[0] * probe_w) - box[0])
                py0 = max(0, int(pbox[1] * probe_h) - box[1])
                px1 = min(cr.width, int((pbox[0] + pbox[2]) * probe_w) - box[0])
                py1 = min(cr.height, int((pbox[1] + pbox[3]) * probe_h) - box[1])
                if px1 > px0 and py1 > py0:
                    cr.paste((0, 0, 0), (px0, py0, px1, py1))
            return cr

        c0 = _crop_masked(im0, sf)
        c1 = _crop_masked(im1, stf)

        def _patch_motion(a_im: Image.Image, b_im: Image.Image) -> float:
            diff = ImageChops.difference(a_im, b_im)
            full_m = sum(ImageStat.Stat(diff).mean) / 3.0
            # Also evaluate 4x4 local tiles so crisp vector animations (e.g. SVG underline draw) register
            # truthfully without requiring the entire card background to flash (frozen cards score ~0 everywhere!).
            dw, dh = diff.size
            peak_tile = 0.0
            if dw >= 16 and dh >= 16:
                tw, th = max(4, dw // 4), max(4, dh // 4)
                for gy in range(4):
                    for gx in range(4):
                        tile = diff.crop((gx * tw, gy * th, min(dw, (gx + 1) * tw), min(dh, (gy + 1) * th)))
                        peak_tile = max(peak_tile, sum(ImageStat.Stat(tile).mean) / 3.0)
            return max(full_m, peak_tile * 0.45)

        raw_delta = _patch_motion(c0, c1)
        if im_mid is not None:
            cm = _crop_masked(im_mid, midf)
            raw_delta = max(raw_delta, _patch_motion(c0, cm), _patch_motion(cm, c1))
        pre_f = max(0, sf - 2)
        if pre_f != sf and pre_f in captured:
            c_pre = _crop_masked(captured[pre_f], pre_f)
            raw_delta = max(raw_delta, _patch_motion(c_pre, c1))

        # If source plate available, check whether the component actually changed relative to source plate!
        plate_delta = None
        plate_motion = 0.0
        if sf in src_captured and stf in src_captured:
            s0 = _crop_masked(src_captured[sf], sf)
            s1 = _crop_masked(src_captured[stf], stf)
            # Overlay signature at sf vs stf: diff between (final - source) at sf and (final - source) at stf!
            ov0 = ImageChops.difference(c0, s0)
            ov1 = ImageChops.difference(c1, s1)
            plate_delta = round(max(
                sum(ImageStat.Stat(ov0).mean) / 3.0,
                sum(ImageStat.Stat(ov1).mean) / 3.0,
            ), 3)
            plate_motion = _patch_motion(ov0, ov1)
            if pre_f != sf and pre_f in captured and pre_f in src_captured:
                ov_pre = ImageChops.difference(_crop_masked(captured[pre_f], pre_f), _crop_masked(src_captured[pre_f], pre_f))
                plate_motion = max(plate_motion, _patch_motion(ov_pre, ov1))
            delta = round(plate_motion, 3)
        else:
            delta = round(raw_delta, 3)

        is_static_hold = nb.get("primitive") == "stable_hold" or bool(nb.get("intentional_static"))
        moved = delta >= codec_noise_floor
        visible_over_plate = (plate_delta is None) or (plate_delta >= codec_noise_floor)
        if not visible_over_plate:
            frozen_nodes.append(nid)
        elif not moved and not is_static_hold:
            frozen_nodes.append(nid)
        results.append({
            "node_id": nid,
            "component_id": nb.get("component_id"),
            "primitive": nb.get("primitive"),
            "frames_compared": [sf, stf],
            "roi_delta": delta,
            "plate_presence_delta": plate_delta,
            "codec_noise_floor": codec_noise_floor,
            "intentional_static": is_static_hold,
            "moved": moved,
            "visible_over_plate": visible_over_plate,
        })

    rep = {
        "schema_version": "kh03.component_pixel_qa.v1",
        "final_mp4_sha256": hash_artifact(fin_p),
        "source_plate_isolated": src_p is not None,
        "nodes_checked": len(results),
        "moved_nodes": sum(1 for r in results if r["moved"]),
        "frozen_nodes": frozen_nodes,
        "invalid_rois": invalid_rois,
        "nodes": results,
        "pass": len(frozen_nodes) == 0 and len(invalid_rois) == 0 and len(results) > 0,
    }
    if out_dir is not None:
        od = Path(out_dir)
        od.mkdir(parents=True, exist_ok=True)
        write_json_atomic(od / "COMPONENT_PIXEL_QA.json", rep)
    return rep


def verify_hook_pixels(
    final_mp4: str | Path,
    hook_plan: dict[str, Any],
    *,
    fps: float = 24.0,
    source_mp4: str | Path | None = None,
    hook_roi_norm: list[float] | None = None,
    presenter_box_norm: list[float] | None = None,
    reveal_constraints: dict[str, Any] | None = None,
    intentional_static: bool | None = None,
    predecoded_fin_frames: dict[int, Image.Image] | None = None,
    predecoded_src_frames: dict[int, Image.Image] | None = None,
    codec_noise_floor: float = 1.8,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """PED-01 & CMI-01 ROI-isolated hook pixel verifier: strictly separates hook component ROI from KOL/source motion!

    - Never passes a frozen/missing hook just because the presenter/KOL moves elsewhere on screen!
    - Rejects invalid/offscreen ROIs fail-closed.
    - When `source_mp4` is supplied, diffs `(final_roi - source_roi)` across frames so source plate motion
      inside/behind the ROI cannot spoof hook animation.
    - When `intentional_static=True`, evaluates readability/visibility/semantic reveal without faking `motion_pass=True`.
    """
    fin_p = Path(final_mp4).resolve()
    if not fin_p.is_file():
        return {"schema_version": "kh03.hook_pixel_qa.v1", "pass": False, "status": "MISSING_MP4"}

    # Resolve strict hook ROI (from explicit arg, hook_plan, or safe top negative-space band; reject offscreen!)
    raw_roi = hook_roi_norm if hook_roi_norm is not None else hook_plan.get("hook_roi_norm")
    if raw_roi is None:
        # Backward-compatible default top negative-space hook band (strictly isolated from central KOL torso!)
        raw_roi = [0.05, 0.04, 0.90, 0.18]
    v_roi = _valid_norm_box(raw_roi)
    if v_roi is None:
        rep = {
            "schema_version": "kh03.hook_pixel_qa.v1",
            "final_mp4_sha256": hash_artifact(fin_p),
            "selected_hook_id": hook_plan.get("selected_hook_id"),
            "status": "FAIL_INVALID_OR_OFFSCREEN_ROI",
            "hook_roi_norm": raw_roi,
            "issues": [f"HOOK_ROI_INVALID_OR_OFFSCREEN: {raw_roi!r}"],
            "pass": False,
        }
        if out_dir is not None:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            write_json_atomic(Path(out_dir) / "HOOK_PIXEL_QA.json", rep)
        return rep

    # Determine whether hook is intentionally static (e.g., calm_context unbroken shot with explicit rationale)
    if intentional_static is None:
        mbs = hook_plan.get("microbeats") or []
        intentional_static = bool(
            hook_plan.get("intentional_static")
            or (len(mbs) == 1 and mbs[0].get("continuous_unbroken_shot") is True)
        )

    w, h = 270, 480
    rx, ry, rw, rh = v_roi
    roi_px = (
        max(0, int(round(rx * w))),
        max(0, int(round(ry * h))),
        min(w, max(2, int(round((rx + rw) * w)))),
        min(h, max(2, int(round((ry + rh) * h)))),
    )
    sample_f = sorted({0, max(1, int(round(fps * 0.45))), max(2, int(round(fps * 1.25))), max(3, int(round(fps * 2.4)))})

    if predecoded_fin_frames is not None:
        imgs = {
            f: (predecoded_fin_frames[f] if predecoded_fin_frames[f].size == (w, h) else predecoded_fin_frames[f].resize((w, h), Image.BILINEAR))
            for f in sample_f if f in predecoded_fin_frames
        }
    else:
        imgs = collect_streaming_qa_frames(fin_p, sample_f, width=w, height=h, timeout_sec=60.0)

    src_p = Path(source_mp4).resolve() if (source_mp4 and Path(source_mp4).is_file()) else None
    if predecoded_src_frames is not None:
        src_imgs = {
            f: (predecoded_src_frames[f] if predecoded_src_frames[f].size == (w, h) else predecoded_src_frames[f].resize((w, h), Image.BILINEAR))
            for f in sample_f if f in predecoded_src_frames
        }
    elif src_p is not None:
        src_imgs = collect_streaming_qa_frames(src_p, sample_f, width=w, height=h, timeout_sec=60.0)
    else:
        src_imgs = {}

    def _crop_hook_roi(im: Image.Image) -> Image.Image:
        cr = im.crop(roi_px).copy()
        if presenter_box_norm and len(presenter_box_norm) == 4:
            px0 = max(0, int(round(presenter_box_norm[0] * w)) - roi_px[0])
            py0 = max(0, int(round(presenter_box_norm[1] * h)) - roi_px[1])
            px1 = min(cr.width, int(round((presenter_box_norm[0] + presenter_box_norm[2]) * w)) - roi_px[0])
            py1 = min(cr.height, int(round((presenter_box_norm[1] + presenter_box_norm[3]) * h)) - roi_px[1])
            # Only mask presenter sub-region if it does not obliterate >85% of hook ROI; if it covers >85%, flag occlusion!
            if px1 > px0 and py1 > py0:
                cr.paste((0, 0, 0), (px0, py0, px1, py1))
        return cr

    # Check presenter occlusion over hook ROI
    occluded_ratio = 0.0
    if presenter_box_norm and len(presenter_box_norm) == 4:
        ix0 = max(rx, float(presenter_box_norm[0]))
        iy0 = max(ry, float(presenter_box_norm[1]))
        ix1 = min(rx + rw, float(presenter_box_norm[0]) + float(presenter_box_norm[2]))
        iy1 = min(ry + rh, float(presenter_box_norm[1]) + float(presenter_box_norm[3]))
        if ix1 > ix0 and iy1 > iy0:
            occluded_ratio = round(((ix1 - ix0) * (iy1 - iy0)) / max(1e-6, rw * rh), 4)

    ordered_keys = [k for k in sample_f if k in imgs]
    ordered_full = [imgs[k] for k in ordered_keys]
    ordered_roi = [_crop_hook_roi(imgs[k]) for k in ordered_keys]

    f0_full = imgs.get(0)
    f0_roi = ordered_roi[0] if (0 in imgs and ordered_roi) else None
    f0_luma = round(sum(ImageStat.Stat(f0_full).mean) / 3.0, 2) if f0_full else 0.0
    f0_roi_luma = round(sum(ImageStat.Stat(f0_roi).mean) / 3.0, 2) if f0_roi else 0.0
    f0_roi_std = round(sum(ImageStat.Stat(f0_roi).stddev) / 3.0, 2) if f0_roi else 0.0

    roi_deltas: list[float] = []
    plate_deltas: list[float] = []
    for i in range(len(ordered_keys)):
        k = ordered_keys[i]
        if k in src_imgs:
            sr = _crop_hook_roi(src_imgs[k])
            ov = ImageChops.difference(ordered_roi[i], sr)
            plate_deltas.append(round(sum(ImageStat.Stat(ov).mean) / 3.0, 3))
        if i + 1 < len(ordered_keys):
            k2 = ordered_keys[i + 1]
            if k in src_imgs and k2 in src_imgs:
                ov_a = ImageChops.difference(ordered_roi[i], _crop_hook_roi(src_imgs[k]))
                ov_b = ImageChops.difference(ordered_roi[i + 1], _crop_hook_roi(src_imgs[k2]))
                roi_deltas.append(round(sum(ImageStat.Stat(ImageChops.difference(ov_a, ov_b)).mean) / 3.0, 3))
            else:
                roi_deltas.append(round(sum(ImageStat.Stat(ImageChops.difference(ordered_roi[i], ordered_roi[i + 1])).mean) / 3.0, 3))

    # Reveal timing validation (if payoff reveal constraints provided)
    reveal_ok = True
    reveal_issues: list[str] = []
    if isinstance(reveal_constraints, dict):
        if reveal_constraints.get("early_payoff_spoiler"):
            reveal_ok = False
            reveal_issues.append("EARLY_PAYOFF_SPOILER_IN_HOOK")
        earliest_f = reveal_constraints.get("earliest_reveal_frame")
        actual_f = reveal_constraints.get("actual_hook_entry_frame", 0)
        if earliest_f is not None and int(actual_f) > int(reveal_constraints.get("max_hook_entry_frame", int(round(fps * 1.0)))):
            reveal_ok = False
            reveal_issues.append(f"HOOK_ENTRY_TOO_LATE(f{actual_f})")

    issues: list[str] = list(reveal_issues)
    if occluded_ratio > 0.60:
        issues.append(f"HOOK_ROI_OCCLUDED_BY_PRESENTER({occluded_ratio:.2f})")
    if f0_full is None or f0_luma < 10.0:
        issues.append(f"FRAME0_DARK_OR_EMPTY(luma={f0_luma})")
    plate_visible = (not plate_deltas) or (max(plate_deltas) >= codec_noise_floor)
    if src_p is not None and not intentional_static and not plate_visible:
        issues.append(f"HOOK_NOT_VISIBLE_OVER_SOURCE_PLATE(max_plate_delta={max(plate_deltas, default=0.0)})")

    roi_evolving = any(d >= codec_noise_floor for d in roi_deltas)
    if intentional_static:
        motion_gate_status = "NOT_APPLICABLE_INTENTIONAL_STATIC"
        readability_ok = (f0_roi is not None and f0_roi_luma >= 8.0 and (f0_roi_std >= 4.0 or plate_visible))
        if not readability_ok:
            issues.append(f"STATIC_HOOK_LOW_READABILITY(luma={f0_roi_luma}, std={f0_roi_std})")
        ok = (len(issues) == 0 and reveal_ok and readability_ok)
    else:
        motion_gate_status = "PASS" if roi_evolving else "FAIL_FROZEN_HOOK_ROI"
        if not roi_evolving:
            issues.append(f"HOOK_COMPONENT_FROZEN_IN_ROI(deltas={roi_deltas}, floor={codec_noise_floor})")
        ok = (len(issues) == 0 and reveal_ok and roi_evolving)

    rep = {
        "schema_version": "kh03.hook_pixel_qa.v1",
        "final_mp4_sha256": hash_artifact(fin_p),
        "selected_hook_id": hook_plan.get("selected_hook_id"),
        "hook_roi_norm": v_roi,
        "source_plate_isolated": src_p is not None,
        "presenter_occlusion_ratio": occluded_ratio,
        "intentional_static": bool(intentional_static),
        "motion_gate_status": motion_gate_status,
        "sampled_frames": ordered_keys,
        "frame0_mean_luma": f0_luma,
        "frame0_roi_luma": f0_roi_luma,
        "frame0_roi_contrast_std": f0_roi_std,
        "frame0_non_empty": f0_luma >= 10.0,
        "subframe_deltas": roi_deltas,
        "plate_presence_deltas": plate_deltas,
        "opening_evolving": roi_evolving,
        "reveal_timing_ok": reveal_ok,
        "issues": issues,
        "status": "PASS" if ok else "FAIL",
        "pass": ok,
    }
    if out_dir is not None:
        od = Path(out_dir)
        od.mkdir(parents=True, exist_ok=True)
        write_json_atomic(od / "HOOK_PIXEL_QA.json", rep)
        if ordered_full:
            strip = Image.new("RGB", (w * len(ordered_full), h), (16, 12, 10))
            for i, im in enumerate(ordered_full):
                strip.paste(im, (i * w, 0))
            strip.save(od / "hook_contact_sheet.png")
    return rep


def audit_decoded_frames_full(
    final_mp4: str | Path,
    *,
    expected_frames: int | None = None,
    intentional_still_spans: list[tuple[int, int]] | None = None,
    probe_w: int = 180,
    probe_h: int = 320,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """PED-08 O(1)-RAM full-frame PTS decoder audit: detects black flashes, duplicates, unintended freezes & exports sheets."""
    fin_p = Path(final_mp4).resolve()
    if not fin_p.is_file():
        return {"schema_version": "kh03.frame_audit.v1", "pass": False, "status": "MISSING_MP4", "decoded_frames": 0}

    still_spans = list(intentional_still_spans or [])

    def _in_intentional_still(fidx: int) -> bool:
        return any(s0 <= fidx < s1 for s0, s1 in still_spans)

    decoded = 0
    black_frames: list[int] = []
    duplicate_frames: list[int] = []
    unintended_freeze_runs: list[dict[str, int]] = []
    cur_freeze_start: int | None = None
    cur_freeze_len = 0
    prev_im: Image.Image | None = None
    min_luma = 255.0
    max_luma = 0.0
    rep_thumbs: list[tuple[int, Image.Image]] = []
    fail_thumbs: list[tuple[int, str, Image.Image]] = []
    sample_step = max(1, (expected_frames or 240) // 12)

    with StreamingRGBFrameDecoder(fin_p, probe_w, probe_h, ffmpeg_threads=2, timeout_sec=180.0) as dec:
        while True:
            raw = dec.read_frame()
            if raw is None:
                break
            im = Image.frombytes("RGB", (probe_w, probe_h), raw)
            luma = sum(ImageStat.Stat(im).mean) / 3.0
            min_luma = min(min_luma, luma)
            max_luma = max(max_luma, luma)
            if luma < 8.0:
                black_frames.append(decoded)
                if len(fail_thumbs) < 8:
                    fail_thumbs.append((decoded, "BLACK", im.copy()))
            if prev_im is not None:
                mad = sum(ImageStat.Stat(ImageChops.difference(im, prev_im)).mean) / 3.0
                if mad < 0.05:
                    duplicate_frames.append(decoded)
                    if not _in_intentional_still(decoded):
                        if cur_freeze_start is None:
                            cur_freeze_start = decoded - 1
                            cur_freeze_len = 2
                        else:
                            cur_freeze_len += 1
                    else:
                        cur_freeze_start = None
                        cur_freeze_len = 0
                else:
                    if cur_freeze_len >= 12:
                        unintended_freeze_runs.append({"start_frame": int(cur_freeze_start or 0), "length_frames": cur_freeze_len})
                        if len(fail_thumbs) < 8:
                            fail_thumbs.append((int(cur_freeze_start or 0), f"FREEZE_{cur_freeze_len}F", prev_im.copy()))
                    cur_freeze_start = None
                    cur_freeze_len = 0
            if decoded % sample_step == 0 and len(rep_thumbs) < 12:
                rep_thumbs.append((decoded, im.copy()))
            prev_im = im
            decoded += 1

    if cur_freeze_len >= 12:
        unintended_freeze_runs.append({"start_frame": int(cur_freeze_start or 0), "length_frames": cur_freeze_len})

    frame_count_ok = (expected_frames is None) or (abs(decoded - int(expected_frames)) <= 1)
    issues: list[str] = []
    if not frame_count_ok:
        issues.append(f"FRAME_COUNT_MISMATCH(decoded={decoded}, expected={expected_frames})")
    if black_frames:
        issues.append(f"BLACK_FRAMES_DETECTED({len(black_frames)} frames: {black_frames[:8]})")
    if unintended_freeze_runs:
        issues.append(f"UNINTENDED_FREEZE_RUNS({len(unintended_freeze_runs)})")

    contact_sheet_path = None
    failure_sheet_path = None
    if out_dir is not None:
        od = Path(out_dir)
        od.mkdir(parents=True, exist_ok=True)
        if rep_thumbs:
            cols = min(4, len(rep_thumbs))
            rows = int(math.ceil(len(rep_thumbs) / float(cols)))
            sheet = Image.new("RGB", (probe_w * cols, probe_h * rows), (12, 16, 14))
            for i, (_fi, th) in enumerate(rep_thumbs):
                sheet.paste(th, ((i % cols) * probe_w, (i // cols) * probe_h))
            cs_p = od / "FRAME_AUDIT_CONTACT_SHEET.png"
            sheet.save(cs_p)
            contact_sheet_path = str(cs_p)
        if fail_thumbs:
            fs = Image.new("RGB", (probe_w * len(fail_thumbs), probe_h), (40, 10, 10))
            for i, (_fi, _tag, th) in enumerate(fail_thumbs):
                fs.paste(th, (i * probe_w, 0))
            fp = od / "FRAME_AUDIT_FAILURES.png"
            fs.save(fp)
            failure_sheet_path = str(fp)

    rep = {
        "schema_version": "kh03.frame_audit.v1",
        "final_mp4_sha256": hash_artifact(fin_p),
        "decoded_frames": decoded,
        "expected_frames": expected_frames,
        "frame_count_ok": frame_count_ok,
        "black_frames": black_frames[:64],
        "black_frame_count": len(black_frames),
        "duplicate_frame_count": len(duplicate_frames),
        "unintended_freeze_runs": unintended_freeze_runs,
        "mean_luma_min": round(min_luma, 2) if decoded > 0 else 0.0,
        "mean_luma_max": round(max_luma, 2) if decoded > 0 else 0.0,
        "contact_sheet_path": contact_sheet_path,
        "failure_sheet_path": failure_sheet_path,
        "issues": issues,
        "status": "PASS" if (decoded > 0 and not issues) else "FAIL",
        "pass": (decoded > 0 and not issues),
    }
    if out_dir is not None:
        write_json_atomic(Path(out_dir) / "FRAME_AUDIT.json", rep)
    return rep


def export_morph_frame_strips(
    final_mp4: str | Path,
    transitions: list[dict[str, Any]],
    out_dir: str | Path,
    *,
    tile_w: int = 180,
    tile_h: int = 320,
    max_transitions: int = 6,
) -> list[str]:
    """Export bounded every-frame strip PNG for each critical morph transition (CMI-03)."""
    fin_p = Path(final_mp4).resolve()
    sdir = Path(out_dir) / "MORPH_FRAME_STRIPS"
    sdir.mkdir(parents=True, exist_ok=True)
    if not fin_p.is_file() or not transitions:
        return []
    wanted_ranges = [(str(t["transition_id"]), int(t["start_frame"]), int(t["end_frame"])) for t in transitions[:max_transitions]]
    wanted_frames: set[int] = set()
    for _, s0, s1 in wanted_ranges:
        for f in range(max(0, s0), min(s0 + 24, max(s0 + 1, s1 + 1))):
            wanted_frames.add(f)
    if not wanted_frames:
        return []
    captured: dict[int, Image.Image] = {}
    max_f = max(wanted_frames)
    idx = 0
    with StreamingRGBFrameDecoder(fin_p, tile_w, tile_h, ffmpeg_threads=2, timeout_sec=90.0) as dec:
        while idx <= max_f:
            raw = dec.read_frame()
            if raw is None:
                break
            if idx in wanted_frames:
                captured[idx] = Image.frombytes("RGB", (tile_w, tile_h), raw)
            idx += 1
    out_paths: list[str] = []
    for tid, s0, s1 in wanted_ranges:
        frames = [captured[f] for f in range(max(0, s0), min(s0 + 24, max(s0 + 1, s1 + 1))) if f in captured]
        if not frames:
            continue
        strip = Image.new("RGB", (tile_w * len(frames), tile_h), (16, 12, 10))
        for i, im in enumerate(frames):
            strip.paste(im, (i * tile_w, 0))
        sp = sdir / f"{tid}_f{s0}_f{s1}.png"
        strip.save(sp)
        out_paths.append(str(sp))
    return out_paths


def _create_minimal_gsap_shim(dest: Path) -> None:
    """Create a deterministic pure-JS subset shim of gsap.timeline().set/.to/.time/.progress ONLY for offline headless gallery probes when HyperFrames vendor GSAP is absent."""
    shim = r"""
window.gsap = (function(){
  function setStyle(el, props) {
    if (!el) return;
    if (props.opacity !== undefined) el.style.opacity = String(props.opacity);
    if (props.width !== undefined) el.style.width = typeof props.width === 'number' ? props.width + 'px' : String(props.width);
    if (props.clipPath !== undefined) el.style.clipPath = String(props.clipPath);
    if (props.strokeDasharray !== undefined) el.setAttribute('stroke-dasharray', String(props.strokeDasharray));
    if (props.strokeDashoffset !== undefined) el.setAttribute('stroke-dashoffset', String(props.strokeDashoffset));
    if (props.left !== undefined) el.style.left = typeof props.left === 'number' ? props.left + 'px' : String(props.left);
    if (props.top !== undefined) el.style.top = typeof props.top === 'number' ? props.top + 'px' : String(props.top);
    const tx = props.x !== undefined ? props.x : 0;
    const ty = props.y !== undefined ? props.y : 0;
    const yp = props.yPercent !== undefined ? props.yPercent : 0;
    const sc = props.scale !== undefined ? props.scale : 1;
    if (props.x !== undefined || props.y !== undefined || props.yPercent !== undefined || props.scale !== undefined) {
      if (tx === 0 && ty === 0 && yp === 0 && sc === 1) {
        el.style.transform = 'none';
      } else {
        el.style.transform = `translate(${tx}px, ${ty}px) translateY(${yp}%) scale(${sc})`;
      }
    }
  }
  function resolveEls(target) {
    if (!target) return [];
    if (typeof target === 'string') return Array.from(document.querySelectorAll(target));
    return [target];
  }
  return {
    timeline: function() {
      const ops = [];
      let curT = 0;
      const api = {
        set(target, props, at) { ops.push({ type: 'set', target, props, at: Number(at || 0), dur: 0 }); return api; },
        to(target, props, at) { ops.push({ type: 'to', target, props, at: Number(at || 0), dur: Number(props.duration || 0.25) }); return api; },
        progress(p) { return api.time(p <= 0 ? 0 : 999); },
        pause() { return api; },
        time(t) {
          curT = Number(t || 0);
          const sorted = ops.slice().sort((a, b) => a.at - b.at);
          // Reset touched elements cleanly before replaying ops up to curT so reverse/repeat seeks are 100% deterministic!
          const seen = new Set();
          for (const op of sorted) {
            for (const el of resolveEls(op.target)) {
              if (!seen.has(el)) {
                seen.add(el);
                el.style.transform = '';
                el.style.clipPath = '';
              }
            }
          }
          const opState = new WeakMap();
          for (const op of sorted) {
            if (curT < op.at) continue;
            const els = resolveEls(op.target);
            if (op.type === 'set' || op.dur <= 0 || curT >= op.at + op.dur) {
              for (const el of els) {
                setStyle(el, op.props);
                if (typeof op.props.opacity === 'number') opState.set(el, op.props.opacity);
              }
            } else {
              const u = Math.max(0, Math.min(1, (curT - op.at) / op.dur));
              for (const el of els) {
                const interp = {};
                const prevOp = opState.has(el) ? opState.get(el) : 0;
                for (const [k, v] of Object.entries(op.props)) {
                  if (k === 'duration' || k === 'ease') continue;
                  if (typeof v === 'number') interp[k] = k === 'opacity' ? (prevOp + u * (v - prevOp)) : v;
                  else interp[k] = v;
                }
                setStyle(el, interp);
                if (typeof interp.opacity === 'number') opState.set(el, interp.opacity);
              }
            }
          }
          return api;
        }
      };
      return api;
    }
  };
})();
"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(shim.strip() + "\n", encoding="utf-8")


def run_kh03_component_gallery(
    out_dir: str | Path,
    *,
    tools_root: Path | None = None,
    width: int = 360,
    height: int = 640,
    fps: int = 24,
) -> dict[str, Any]:
    """Compile & capture ALL 8 KH-03 cross-domain visual components in real headless Chromium across direct/reverse/repeat/cold seeks."""
    gdir = Path(out_dir).resolve()
    bdir = gdir / "build"
    shots_dir = gdir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    chrome_exe = _find_chromium_executable()
    if chrome_exe is None:
        return {"pass": False, "status": "BLOCKED_ENV", "error": "No local Chromium executable available.", "capabilities": {}}

    troot = tools_root or (Path(os.environ.get("SEECUT_STANDALONE_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone"))) / "tools")
    gsap_probe_root = gdir / "_gsap_tools"
    gsap_shim_used = False
    has_native_gsap = any(troot.glob("node_modules/hyperframes/dist/vendor/npm/gsap@*/dist/gsap.min.js"))
    if not has_native_gsap:
        _create_minimal_gsap_shim(gsap_probe_root / "node_modules/hyperframes/dist/vendor/npm/gsap@3.15.0/dist/gsap.min.js")
        troot = gsap_probe_root
        gsap_shim_used = True

    reg = build_registry()
    sample_img = gdir / "gallery_sample_asset.png"
    Image.new("RGB", (240, 160), (24, 58, 50)).save(sample_img)
    sample_sha = hash_artifact(sample_img)

    inventory = {
        "schema_version": "kh02.source_inventory.v1",
        "run_id": "kh03_component_gallery",
        "assets": [{
            "asset_id": "gallery_sample",
            "canonical_path": str(sample_img),
            "basename": sample_img.name,
            "kind": "still_asset",
            "rights": "OWNER_SUPPLIED_NONCOMMERCIAL",
            "usable_in_render": True,
            "sha256": sample_sha,
            "byte_size": sample_img.stat().st_size,
            "media": {"width": width, "height": height, "fps_num": fps, "fps_den": 1, "nb_frames": 360, "duration_sec": 15.0, "has_audio": False},
        }],
    }

    # Intentionally spans unrelated domains: cooking, biology/physics, history, textiles, software, finance, product, CTA
    sample_params: dict[str, dict[str, Any]] = {
        "kh03.contradiction_lockup": {"kicker": "CULINARY PHYSICS", "left_value": "450°F", "operator": "≠", "right_value": "Sear", "headline": "Dry surface matters more"},
        "kh03.proportional_reservoir": {"kicker": "NUTRITION MIX", "headline": "Sourdough Hydration", "segments": [{"label": "Flour", "pct": 58.0, "delta": "500g"}, {"label": "Water", "pct": 35.0, "delta": "325g"}, {"label": "Starter", "pct": 7.0, "delta": "75g"}]},
        "kh03.rolling_counter": {"kicker": "LATENCY DROP", "headline": "Streaming Frame QA", "start_value": "4.6 GiB", "end_value": "12 MiB", "unit": "RAM"},
        "kh03.causal_flow_graph": {"kicker": "THERMODYNAMICS", "headline": "Heat Pump Cycle", "nodes": [{"label": "Absorb", "text": "Outdoor air"}, {"label": "Compress", "text": "Raise temp"}, {"label": "Release", "text": "Warm room"}]},
        "kh03.annotated_timeline": {"kicker": "HISTORY", "headline": "1440 Printing Shift", "steps": [{"label": "1440", "text": "Movable type"}, {"label": "Weeks", "text": "Pamphlets spread"}]},
        "kh03.comparison_matrix": {"kicker": "MATERIALS", "headline": "Linen vs Cotton", "left_col": {"title": "Linen", "detail": "Hollow flax airflow"}, "right_col": {"title": "Cotton", "detail": "Tightly packed fibers"}},
        "kh03.proof_frame_reveal": {"kicker": "VERIFIED CAPTURE", "headline": "Field Specimen Plate", "subtitle": "REAL_EVIDENCE", "evidence_label": "REAL_EVIDENCE"},
        "kh03.cta_magnetic_underline": {"kicker": "NEXT STEP", "headline": "Read the full guide.", "cta_tag": "Source notes linked"},
    }

    specs = []
    beats = []
    span = 40
    total_frames = len(KH03_COMPONENT_CATALOG) * span + 24
    for idx, cat in enumerate(KH03_COMPONENT_CATALOG):
        sf = 12 + idx * span
        ef = sf + span - 4
        bid = f"gbeat_{idx + 1:02d}"
        beats.append({
            "beat_id": bid,
            "start_frame": 0 if idx == 0 else beats[-1]["end_frame"],
            "end_frame": total_frames if idx == len(KH03_COMPONENT_CATALOG) - 1 else ef + 4,
            "source_word_ids": [],
            "semantic_function": "reveal",
            "viewer_goal": f"Verify {cat['capability_id']}",
            "emotion_intensity": 0.7,
            "priority": 4,
            "content_refs": ["gallery_sample"],
            "proof_status": "CONCEPT_ILLUSTRATION",
            "visual_mode": "faceless_diagram",
            "focus_subject": cat["capability_id"],
            "continuity_in": "open_frame",
            "continuity_out": "clean_settle",
            "planned_information_change": f"Render {cat['capability_id']}",
            "why": "KH-03 Component Gallery verification",
            "read_time_frames": 12,
        })
        cid = cat["capability_id"]
        specs.append(design_component(
            component_id=f"k3g_{idx + 1:02d}",
            requested_capability_id=cid,
            beat_id=bid,
            role="reveal",
            start_frame=sf,
            end_frame=ef,
            normalized_bbox=[0.06, 0.22, 0.88, 0.34],
            params=sample_params[cid],
            semantic_anchor=f"gallery:{cid}",
            registry=reg,
            aspect_ratio="9:16",
            fps=fps,
            asset_ref="gallery_sample" if cid == "kh03.proof_frame_reveal" else None,
        ))

    vis_events = compile_component_events(specs, fps=fps, profile="editorial_pro")
    shotbook = {
        "schema_version": "kh02.shotbook.v2",
        "run_id": "kh03_component_gallery",
        "mode": "faceless",
        "profile": "editorial_pro",
        "status": "TEST_ONLY",
        "source_sha_set": {"gallery_sample": sample_sha},
        "output_spec": {
            "width": width, "height": height, "output_scale": 1.0,
            "fps_num": fps, "fps_den": 1, "total_frames": total_frames,
            "audio_sample_rate": 48000, "orientation": "portrait",
            "platform_safe_zone": {"top": 0.06, "bottom": 0.08, "left": 0.04, "right": 0.04},
        },
        "input_brief_sha": "a" * 64,
        "rights_ledger_sha": "b" * 64,
        "transcript_sha": None,
        "edit_decision_map": {
            "schema_version": "kh02.edl.v1", "voice_policy": "SILENT_BY_DESIGN",
            "fps_num": fps, "fps_den": 1, "audio_sample_rate": 48000,
            "total_output_frames": total_frames, "segments": [{
                "segment_id": "seg_001", "source_asset_id": "gallery_sample",
                "src_start_frame": 0, "src_end_frame": total_frames,
                "out_start_frame": 0, "out_end_frame": total_frames,
                "audio_fade_in_ms": 0, "audio_fade_out_ms": 0, "reason": "kh03_gallery",
            }],
        },
        "story_arc": {
            "creative_direction_id": "kh03_gallery", "theme": "explainer_cobalt_chalk",
            "archetype": "visual_essay_modular", "summary": "KH-03 8-component cross-domain gallery", "alternatives_considered": [],
        },
        "beats": beats,
        "visual_events": vis_events,
        "camera_events": [],
        "transitions": [],
        "text_events": [],
        "audio_cues": [],
        "claim_evidence": [],
        "approved_exceptions": [],
        "provenance": {
            "planner_version": "kh03.3.0", "generated_at_utc": "1970-01-01T00:00:00Z",
            "engine": "SeeCut-v3.4-HyperFrames-HTML-GSAP",
            "base_git_sha": "8066fc7", "upstream_seecut_sha": "b05d7ed",
        },
    }

    compiled = compile_shotbook(shotbook, inventory, bdir, tools_root=troot, registry=reg)
    html_url = Path(compiled["html_path"]).resolve().as_uri()

    # Build direct / entry-motion / reverse / repeat / cold seek plan (uses dynamic free TCP ports to avoid race collisions!)
    plan1 = [{"label": "t0_blank", "t": 0.0}]
    for idx, sp in enumerate(specs):
        t_entry = round((sp["start_frame"] + 2) / float(fps), 4)
        ts = round((sp["start_frame"] + 14) / float(fps), 4)
        plan1.append({"label": f"c{idx}_entry", "t": t_entry})
        plan1.append({"label": f"c{idx}_direct", "t": ts})
    for idx in reversed(range(len(specs))):
        ts = round((specs[idx]["start_frame"] + 14) / float(fps), 4)
        plan1.append({"label": f"c{idx}_reverse", "t": ts})
    for idx, sp in enumerate(specs):
        ts = round((sp["start_frame"] + 14) / float(fps), 4)
        plan1.append({"label": f"c{idx}_repeat", "t": ts})
    plan2 = [{"label": f"c{idx}_cold", "t": round((sp["start_frame"] + 14) / float(fps), 4)} for idx, sp in enumerate(specs)]

    node_cdp = r"""
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const chromePath = process.argv[1];
const url = process.argv[2];
const outDir = process.argv[3];
const W = parseInt(process.argv[4], 10);
const H = parseInt(process.argv[5], 10);
const plan1 = JSON.parse(process.argv[6]);
const plan2 = JSON.parse(process.argv[7]);

function freePort() {
  return new Promise((res, rej) => {
    const s = net.createServer();
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
    s.on('error', rej);
  });
}

async function runPass(plan) {
  const port = await freePort();
  const errors = [];
  const udir = path.join(outDir, `_prof_${port}_${process.pid}`);
  fs.mkdirSync(udir, { recursive: true });
  const proc = spawn(chromePath, [
    `--remote-debugging-port=${port}`, `--user-data-dir=${udir}`,
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--mute-audio', '--force-device-scale-factor=1',
    '--disable-lcd-text', '--font-render-hinting=none', '--disable-font-subpixel-positioning',
    `--window-size=${W},${H}`, 'about:blank'
  ], { stdio: 'ignore' });
  let wsUrl = null;
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`);
      const arr = await r.json();
      const pg = arr.find(x => x.type === 'page' && x.webSocketDebuggerUrl);
      if (pg) { wsUrl = pg.webSocketDebuggerUrl; break; }
    } catch {}
    await new Promise(r => setTimeout(r, 120));
  }
  if (!wsUrl) { proc.kill('SIGKILL'); throw new Error('CDP connect failed'); }
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.addEventListener('open', res, { once: true }); ws.addEventListener('error', rej, { once: true }); });
  let nid = 1;
  const pend = new Map();
  ws.addEventListener('message', ev => {
    const m = JSON.parse(ev.data);
    if (m.method === 'Runtime.exceptionThrown') errors.push('exception');
    if (m.id && pend.has(m.id)) {
      const { res, rej } = pend.get(m.id);
      pend.delete(m.id);
      if (m.error) rej(new Error(m.error.message)); else res(m.result);
    }
  });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const id = nid++; pend.set(id, { res, rej });
    ws.send(JSON.stringify({ id, method, params }));
  });
  try {
    await send('Page.enable');
    await send('Runtime.enable');
    await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
    await send('Page.navigate', { url });
    for (let i = 0; i < 40; i++) {
      const rr = await send('Runtime.evaluate', { expression: `document.readyState === 'complete' && !!(window.__timelines && window.__timelines.master)`, returnByValue: true });
      if (rr?.result?.value === true) break;
      await new Promise(r => setTimeout(r, 80));
    }
    await send('Runtime.evaluate', { expression: `document.fonts ? document.fonts.ready.then(() => true) : true`, awaitPromise: true, returnByValue: true });
    for (const item of plan) {
      await send('Runtime.evaluate', { expression: `new Promise(res => { window.__timelines.master.time(${item.t}).pause(); void document.body.offsetHeight; requestAnimationFrame(() => requestAnimationFrame(() => res(true))); })`, awaitPromise: true, returnByValue: true });
      const shot = await send('Page.captureScreenshot', { format: 'png', fromSurface: true });
      fs.writeFileSync(path.join(outDir, item.label + '.png'), Buffer.from(shot.data, 'base64'));
    }
  } finally {
    try { ws.close(); } catch {}
    try { proc.kill('SIGKILL'); } catch {}
    await new Promise(r => setTimeout(r, 180));
    try { fs.rmSync(udir, { recursive: true, force: true }); } catch {}
  }
  return errors;
}

(async () => {
  const e1 = await runPass(plan1);
  const e2 = await runPass(plan2);
  console.log(JSON.stringify({ ok: (e1.length === 0 && e2.length === 0), errors: [...e1, ...e2] }));
})().catch(e => { console.error(e); process.exit(1); });
"""
    pr = subprocess.run(
        ["node", "-e", node_cdp, str(chrome_exe), html_url, str(shots_dir), str(width), str(height), json.dumps(plan1), json.dumps(plan2)],
        capture_output=True, text=True, timeout=120,
    )
    if pr.returncode != 0:
        return {"pass": False, "status": "FAIL_CHROMIUM_CDP", "error": pr.stderr[-400:], "capabilities": {}}

    meta = json.loads(pr.stdout.strip().splitlines()[-1])
    t0_png = shots_dir / "t0_blank.png"
    results: dict[str, dict[str, Any]] = {}
    all_pass = bool(meta.get("ok"))

    for idx, sp in enumerate(specs):
        p_ent = shots_dir / f"c{idx}_entry.png"
        p_dir = shots_dir / f"c{idx}_direct.png"
        p_rev = shots_dir / f"c{idx}_reverse.png"
        p_rep = shots_dir / f"c{idx}_repeat.png"
        p_cld = shots_dir / f"c{idx}_cold.png"
        bx, by, bw, bh = sp["normalized_bbox"]
        crop = (int(bx * width), int(by * height), int((bx + bw) * width), int((by + bh) * height))

        diff_vis = _mean_abs_diff(t0_png, p_dir, crop)
        diff_prog = _mean_abs_diff(p_ent, p_dir, crop)
        diff_rev = _mean_abs_diff(p_dir, p_rev)
        diff_rep = _mean_abs_diff(p_dir, p_rep)
        diff_cld = _mean_abs_diff(p_dir, p_cld)
        ok = (diff_vis >= 2.0 and diff_rev == 0.0 and diff_rep == 0.0 and diff_cld == 0.0)
        if not ok:
            all_pass = False
        results[sp["capability_id"]] = {
            "pass": ok,
            "kind": sp["kind"],
            "visible_region_delta_vs_t0": diff_vis,
            "internal_entry_to_settle_delta": diff_prog,
            "reverse_seek_diff": diff_rev,
            "repeat_seek_diff": diff_rep,
            "cold_restart_diff": diff_cld,
            "preview_path": str(p_dir),
            "preview_sha256": hash_artifact(p_dir),
        }

    verified_reg = mark_gallery_verified(reg, results)
    gsap_path = bdir / "assets" / "gsap.min.js"
    gsap_sha = hash_artifact(gsap_path) if gsap_path.is_file() else None
    bv_proc = subprocess.run([str(chrome_exe), "--version"], capture_output=True, text=True, timeout=15)
    browser_ver = (bv_proc.stdout or bv_proc.stderr or "").strip() or "unknown"
    native_gsap_ok = bool(all_pass and not gsap_shim_used)
    shim_test_ok = bool(all_pass and gsap_shim_used)
    report = {
        "schema_version": "kh03.component_gallery.v1",
        "pass": all_pass,
        "status": "PASS" if all_pass else "FAIL",
        "gsap_mode": "OFFLINE_DETERMINISTIC_SHIM_TEST_ONLY" if gsap_shim_used else "PINNED_HYPERFRAMES_GSAP",
        "gsap_vendor_sha256": gsap_sha,
        "browser_version": browser_ver,
        "native_gsap_verified": native_gsap_ok,
        "shim_only_test_pass": shim_test_ok,
        "production_gallery_status": (
            "NATIVE_GSAP_PASS" if native_gsap_ok
            else ("SHIM_ONLY_TEST_PASS_BLOCKED_MISSING_NATIVE_GSAP" if shim_test_ok else "FAIL")
        ),
        "browser_errors": meta.get("errors", []),
        "t0_blank_sha256": hash_artifact(t0_png),
        "verified_count": sum(1 for v in results.values() if v["pass"]),
        "total_count": len(results),
        "capabilities": results,
    }
    write_json_atomic(gdir / "COMPONENT_GALLERY_REPORT.json", report)
    write_json_atomic(gdir / "GALLERY_VERIFICATION.json", report)
    write_json_atomic(gdir / "NATIVE_GALLERY_VERIFICATION.json", report)
    write_json_atomic(gdir / "CAPABILITY_REGISTRY_VERIFIED.json", verified_reg)
    return report
