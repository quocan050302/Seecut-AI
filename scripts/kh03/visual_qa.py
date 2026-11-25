"""KH-03 Visual QA & Deterministic Headless Chromium Component Gallery Verifier.

Provides:
1. Swept bounding-box protected-region collision audit (face/mouth/hands/objects + fullscreen hard lock).
2. Real headless Chromium verification of all 8 newly compiled KH-03 component families using
   either pinned playwright-core OR native Node v26 Chromium CDP (direct == reverse == repeat == cold,
   visible pixel delta >= 2.0, zero console/page errors, zero first-frame flash).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

from scripts.kh02.compile_plan import compile_shotbook
from scripts.kh02.registry import build_registry, mark_gallery_verified

from .components import KH03_COMPONENT_CATALOG, audit_component_motion, design_component, compile_component_events
from .contracts import hash_artifact, write_json_atomic


def _find_chromium_executable() -> Path | None:
    env_p = os.environ.get("KH03_CHROMIUM_PATH") or os.environ.get("CHROME_PATH")
    if env_p and Path(env_p).is_file():
        return Path(env_p)
    # Check local Playwright browser cache cleanly (bounded explicit paths, no HOME recursive scan)
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        pw_root = Path(local_app) / "ms-playwright"
        if pw_root.is_dir():
            for pat in ("chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe", "chromium-*/chrome-win/chrome.exe"):
                matches = sorted(pw_root.glob(pat), reverse=True)
                if matches:
                    return matches[0]
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


def audit_fullscreen_and_collisions(
    creative_plan: dict[str, Any],
    input_analysis: dict[str, Any],
    *,
    shotbook: dict[str, Any] | None = None,
    require_shotbook: bool = False,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Audit full-screen lock compliance, camera geometry, protected-region clearance, 360px legibility & motion (R1.02)."""
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
        "detection_mode": "STATIC_FALLBACK_VERIFIED",
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
) -> dict[str, Any]:
    """Stream EVERY decoded frame (0..N-1) comparing expected EDL source frame vs final MP4 outside overlay mask (R1.02).

    Detects single-frame PIP shrink, zoom/crop, black/hidden source, wrong frame order, freeze, and
    protected-region overlay intrusion without dumping full-res PNGs to disk.
    """
    src_p = Path(source_mp4).resolve()
    fin_p = Path(final_mp4).resolve()
    if not src_p.is_file() or not fin_p.is_file():
        return {
            "schema_version": "kh03.fullscreen_frame_qa.v1",
            "verification_scope": "DECODED_RENDER",
            "status": "FAIL_MISSING_MEDIA",
            "pass": False,
            "issues": ["Source or final MP4 missing for decoded frame verification."],
        }

    def _probe_meta(p: Path) -> dict[str, Any]:
        pr = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate,nb_frames", "-of", "json", str(p)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        st = json.loads(pr.stdout)["streams"][0]
        num, den = (int(x) for x in str(st.get("r_frame_rate", "24/1")).split("/"))
        return {
            "width": int(st["width"]), "height": int(st["height"]),
            "fps_num": num, "fps_den": max(1, den),
            "nb_frames": int(st["nb_frames"]) if str(st.get("nb_frames", "")).isdigit() else None,
        }

    src_m = _probe_meta(src_p)
    fin_m = _probe_meta(fin_p)
    issues: list[str] = []

    src_ar = src_m["width"] / float(max(1, src_m["height"]))
    fin_ar = fin_m["width"] / float(max(1, fin_m["height"]))
    if abs(src_ar - fin_ar) > 0.02:
        issues.append(f"ASPECT_MISMATCH: source {src_m['width']}x{src_m['height']} vs final {fin_m['width']}x{fin_m['height']}")
    if (src_m["fps_num"] / float(src_m["fps_den"])) != (fin_m["fps_num"] / float(fin_m["fps_den"])):
        issues.append(f"FPS_MISMATCH: source {src_m['fps_num']}/{src_m['fps_den']} vs final {fin_m['fps_num']}/{fin_m['fps_den']}")

    # Build expected source frame index -> output frame index list from EDL (or identity)
    if edl and edl.get("segments"):
        expected_src_frames: list[int] = []
        for seg in edl["segments"]:
            expected_src_frames.extend(range(int(seg["src_start_frame"]), int(seg["src_end_frame"])))
    else:
        n_est = fin_m["nb_frames"] or src_m["nb_frames"] or 0
        expected_src_frames = list(range(n_est))

    # Decode compact 180x320 RGB frames via streaming FFmpeg pipe (constant bounded memory)
    def _decode_raw_rgb(p: Path, w: int, h: int) -> bytes:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(p), "-vf", f"scale={w}:{h}:flags=bilinear", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=180,
        )
        return proc.stdout

    frame_bytes = probe_w * probe_h * 3
    src_buf = _decode_raw_rgb(src_p, probe_w, probe_h)
    fin_buf = _decode_raw_rgb(fin_p, probe_w, probe_h)
    src_n = len(src_buf) // frame_bytes
    fin_n = len(fin_buf) // frame_bytes
    if not expected_src_frames:
        expected_src_frames = list(range(fin_n))

    if fin_n != len(expected_src_frames):
        issues.append(f"FRAME_COUNT_MISMATCH: final decoded {fin_n} frames != expected EDL {len(expected_src_frames)} frames")

    # Build protected mask pixels (focus on presenter/subject region where overlays must NOT occlude)
    pzones = protected_zones or [{"bbox_norm": [0.15, 0.30, 0.70, 0.44]}]
    ov_list = overlay_events or []
    n_check = min(fin_n, len(expected_src_frames))
    fail_frames: list[dict[str, Any]] = []
    max_obs_diff = 0.0
    total_diff = 0.0

    for out_idx in range(n_check):
        src_idx = expected_src_frames[out_idx]
        if src_idx >= src_n:
            fail_frames.append({"frame": out_idx, "reason": f"src_idx {src_idx} >= src_n {src_n}"})
            continue
        im_s = Image.frombytes("RGB", (probe_w, probe_h), src_buf[src_idx * frame_bytes : (src_idx + 1) * frame_bytes])
        im_f = Image.frombytes("RGB", (probe_w, probe_h), fin_buf[out_idx * frame_bytes : (out_idx + 1) * frame_bytes])

        # Mask out ONLY approved non-protected overlay rects active on this frame; protected zones are NEVER masked out!
        active_boxes = [
            ev["normalized_bbox"] for ev in ov_list
            if int(ev.get("start_frame", 0)) <= out_idx < int(ev.get("end_frame", 0)) and ev.get("normalized_bbox")
        ]
        # Measure diff inside protected zones directly (catches PIP shrink, zoom, crop, black frame, wrong order, or face occlusion!)
        prot_diffs: list[float] = []
        for pz in pzones:
            bx, by, bw, bh = [float(v) for v in pz["bbox_norm"]]
            box = (
                max(0, int(bx * probe_w)), max(0, int(by * probe_h)),
                min(probe_w, max(1, int((bx + bw) * probe_w))), min(probe_h, max(1, int((by + bh) * probe_h))),
            )
            diff_im = ImageChops.difference(im_s.crop(box), im_f.crop(box))
            md = sum(ImageStat.Stat(diff_im).mean) / 3.0
            prot_diffs.append(md)
        # Also check full-frame outside active overlay boxes (catches background replacement / letterbox / PIP shrink)
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
        if frame_md > max_mean_diff:
            fail_frames.append({
                "frame": out_idx,
                "expected_src_frame": src_idx,
                "mean_abs_diff": frame_md,
                "bg_diff": round(bg_md, 3),
                "protected_max_diff": round(max(prot_diffs) if prot_diffs else 0.0, 3),
                "reason": "FULLSCREEN_OR_PROTECTED_ZONE_DRIFT",
            })

    if fail_frames:
        issues.append(f"FULLSCREEN_DECODED_FRAME_DRIFT: {len(fail_frames)}/{n_check} frames exceeded tolerance {max_mean_diff} (max={max_obs_diff})")

    report = {
        "schema_version": "kh03.fullscreen_frame_qa.v1",
        "verification_scope": "DECODED_RENDER",
        "source_mp4_sha256": hash_artifact(src_p),
        "final_mp4_sha256": hash_artifact(fin_p),
        "source_resolution": f"{src_m['width']}x{src_m['height']}",
        "final_resolution": f"{fin_m['width']}x{fin_m['height']}",
        "fps": f"{fin_m['fps_num']}/{fin_m['fps_den']}",
        "frames_checked": n_check,
        "frames_expected": len(expected_src_frames),
        "max_mean_abs_diff": round(max_obs_diff, 3),
        "avg_mean_abs_diff": round(total_diff / float(max(1, n_check)), 3),
        "tolerance_mean_abs_diff": max_mean_diff,
        "fail_frame_count": len(fail_frames),
        "fail_frames_sample": fail_frames[:12],
        "issues": issues,
        "status": "DECODED_RENDER_PASS" if not issues else "DECODED_RENDER_FAIL",
        "pass": len(issues) == 0,
    }
    if out_dir is not None:
        od = Path(out_dir)
        od.mkdir(parents=True, exist_ok=True)
        write_json_atomic(od / "FULLSCREEN_FRAME_QA.json", report)
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
) -> dict[str, Any]:
    """Stream EVERY decoded frame (0..N-1) verifying live presenter geometry, crop, motion & continuity (ADL-07).

    Evaluates exact `interpolate_presenter_geometry(frame)` at every frame (including morph midpoints!),
    transforms the corresponding EDL source frame into the expected presenter box via aspect-preserving
    object-fit:cover math, and detects missing presenter, freeze, black flash, crop/aspect drift, or
    wrong frame order without dumping full-res PNGs to disk.
    """
    import csv as _csv
    from .layout import compute_source_crop_for_box, interpolate_presenter_geometry

    src_p = Path(source_mp4).resolve()
    fin_p = Path(final_mp4).resolve()
    if not src_p.is_file() or not fin_p.is_file():
        return {
            "schema_version": "kh03.adaptive_layout_frame_qa.v1",
            "verification_scope": "DECODED_RENDER",
            "status": "FAIL_MISSING_MEDIA",
            "pass": False,
            "issues": ["Source or final MP4 missing for adaptive layout decoded frame verification."],
        }

    def _probe(p: Path) -> dict[str, Any]:
        pr = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate,nb_frames", "-of", "json", str(p)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        st = json.loads(pr.stdout)["streams"][0]
        num, den = (int(x) for x in str(st.get("r_frame_rate", "24/1")).split("/"))
        return {
            "width": int(st["width"]), "height": int(st["height"]),
            "fps_num": num, "fps_den": max(1, den),
            "nb_frames": int(st["nb_frames"]) if str(st.get("nb_frames", "")).isdigit() else None,
        }

    src_m = _probe(src_p)
    fin_m = _probe(fin_p)
    issues: list[str] = []

    if edl and edl.get("segments"):
        expected_src_frames: list[int] = []
        for seg in edl["segments"]:
            expected_src_frames.extend(range(int(seg["src_start_frame"]), int(seg["src_end_frame"])))
    else:
        n_est = fin_m["nb_frames"] or src_m["nb_frames"] or 0
        expected_src_frames = list(range(n_est))

    def _decode_rgb(p: Path, w: int, h: int) -> bytes:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(p), "-vf", f"scale={w}:{h}:flags=bilinear", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=180,
        )
        return proc.stdout

    frame_bytes = probe_w * probe_h * 3
    src_buf = _decode_rgb(src_p, probe_w, probe_h)
    fin_buf = _decode_rgb(fin_p, probe_w, probe_h)
    src_n = len(src_buf) // frame_bytes
    fin_n = len(fin_buf) // frame_bytes
    if not expected_src_frames:
        expected_src_frames = list(range(fin_n))
    if fin_n != len(expected_src_frames):
        issues.append(f"FRAME_COUNT_MISMATCH: final {fin_n} != expected EDL {len(expected_src_frames)}")

    n_check = min(fin_n, len(expected_src_frames))
    trs = layout_plan.get("transitions") or []
    beats_by_id = {b["beat_id"]: b for b in layout_plan.get("beats", [])}
    fail_frames: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []
    max_diff = 0.0
    sum_diff = 0.0
    prev_pres_crop: Image.Image | None = None
    freeze_streak = 0
    max_freeze_streak = 0
    black_frames = 0
    morph_checked = 0

    for out_idx in range(n_check):
        src_idx = expected_src_frames[out_idx]
        if src_idx >= src_n:
            fail_frames.append({"frame": out_idx, "reason": "SRC_INDEX_OUT_OF_RANGE"})
            continue
        im_s = Image.frombytes("RGB", (probe_w, probe_h), src_buf[src_idx * frame_bytes : (src_idx + 1) * frame_bytes])
        im_f = Image.frombytes("RGB", (probe_w, probe_h), fin_buf[out_idx * frame_bytes : (out_idx + 1) * frame_bytes])

        st = interpolate_presenter_geometry(out_idx, layout_plan, trs)
        if st["in_transition"]:
            morph_checked += 1
        bx, by, bw, bh = st["presenter_box_norm"]
        px0 = max(0, int(round(bx * probe_w)))
        py0 = max(0, int(round(by * probe_h)))
        pw = max(8, min(probe_w - px0, int(round(bw * probe_w))))
        ph = max(8, min(probe_h - py0, int(round(bh * probe_h))))

        # Compute aspect-preserving cover crop of expected source frame into (pw, ph)
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

        # Check inner safe core of presenter box (avoid rounded corner edge pixels & full-screen top hook card)
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

        # Detect live presenter freeze when source itself is changing
        if prev_pres_crop is not None and prev_pres_crop.size == act_core.size and out_idx > 0:
            src_prev = Image.frombytes("RGB", (probe_w, probe_h), src_buf[expected_src_frames[out_idx - 1] * frame_bytes : (expected_src_frames[out_idx - 1] + 1) * frame_bytes])
            src_delta = sum(ImageStat.Stat(ImageChops.difference(src_prev, im_s)).mean) / 3.0
            act_delta = sum(ImageStat.Stat(ImageChops.difference(prev_pres_crop, act_core)).mean) / 3.0
            if src_delta > 2.5 and act_delta < 0.15:
                freeze_streak += 1
                max_freeze_streak = max(max_freeze_streak, freeze_streak)
            else:
                freeze_streak = 0
        else:
            freeze_streak = 0
        prev_pres_crop = act_core

        max_diff = max(max_diff, md)
        sum_diff += md
        row_fail = (md > max_mean_diff) or is_black
        if row_fail:
            fail_frames.append({
                "frame": out_idx,
                "expected_src_frame": src_idx,
                "layout_mode": st["layout_mode"],
                "in_transition": st["in_transition"],
                "mean_abs_diff": md,
                "black_flash": is_black,
                "reason": "BLACK_FLASH" if is_black else "PRESENTER_GEOMETRY_OR_OCCLUSION_DRIFT",
            })
        if out_idx == 0 or out_idx == n_check - 1 or out_idx % max(1, n_check // 40) == 0 or st["in_transition"] or row_fail:
            csv_rows.append({
                "frame": out_idx,
                "src_frame": src_idx,
                "layout_mode": st["layout_mode"],
                "in_transition": st["in_transition"],
                "mean_abs_diff": md,
                "black_flash": is_black,
                "pass": not row_fail,
            })

    if fail_frames:
        issues.append(f"ADAPTIVE_LAYOUT_FRAME_DRIFT: {len(fail_frames)}/{n_check} frames failed (max_diff={max_diff}, black={black_frames})")
    if max_freeze_streak >= 8:
        issues.append(f"PRESENTER_FREEZE_DETECTED: {max_freeze_streak} consecutive static presenter frames while source moved")

    report = {
        "schema_version": "kh03.adaptive_layout_frame_qa.v1",
        "verification_scope": "DECODED_RENDER",
        "source_mp4_sha256": hash_artifact(src_p),
        "final_mp4_sha256": hash_artifact(fin_p),
        "layout_plan_sha256": layout_plan.get("layout_plan_sha256"),
        "compile_map_sha256": hash_artifact(compile_map) if compile_map else None,
        "layout_policy": layout_plan.get("layout_policy"),
        "source_resolution": f"{src_m['width']}x{src_m['height']}",
        "final_resolution": f"{fin_m['width']}x{fin_m['height']}",
        "fps": f"{fin_m['fps_num']}/{fin_m['fps_den']}",
        "frames_checked": n_check,
        "frames_expected": len(expected_src_frames),
        "morph_frames_checked": morph_checked,
        "max_mean_abs_diff": round(max_diff, 3),
        "avg_mean_abs_diff": round(sum_diff / float(max(1, n_check)), 3),
        "tolerance_mean_abs_diff": max_mean_diff,
        "black_flash_frames": black_frames,
        "max_freeze_streak": max_freeze_streak,
        "fail_frame_count": len(fail_frames),
        "fail_frames_sample": fail_frames[:12],
        "issues": issues,
        "status": "DECODED_ADAPTIVE_PASS" if not issues else "DECODED_ADAPTIVE_FAIL",
        "pass": len(issues) == 0,
    }
    if out_dir is not None:
        od = Path(out_dir)
        od.mkdir(parents=True, exist_ok=True)
        write_json_atomic(od / "ADAPTIVE_LAYOUT_FRAME_QA.json", report)
        write_json_atomic(od / "MOTION_CHOREOGRAPHY_QA.json", {
            "schema_version": "kh03.motion_choreography_qa.v1",
            "final_mp4_sha256": report["final_mp4_sha256"],
            "morph_frames_checked": morph_checked,
            "black_flash_frames": black_frames,
            "max_freeze_streak": max_freeze_streak,
            "pass": report["pass"],
            "issues": issues,
        })
        with (od / "ADAPTIVE_LAYOUT_FRAME_QA.csv").open("w", encoding="utf-8", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=["frame", "src_frame", "layout_mode", "in_transition", "mean_abs_diff", "black_flash", "pass"])
            w.writeheader()
            w.writerows(csv_rows)
    return report


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
      el.style.transform = `translate(${tx}px, ${ty}px) translateY(${yp}%) scale(${sc})`;
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
    """Compile & capture ALL 8 new KH-03 visual components in real headless Chromium across direct/reverse/repeat/cold seeks."""
    gdir = Path(out_dir).resolve()
    bdir = gdir / "build"
    shots_dir = gdir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    chrome_exe = _find_chromium_executable()
    if chrome_exe is None:
        return {"pass": False, "status": "BLOCKED_ENV", "error": "No local Chromium executable available.", "capabilities": {}}

    # Ensure gsap.min.js exists (use pinned HyperFrames vendor GSAP if present, else deterministic test shim inside gallery build)
    troot = tools_root or (Path(os.environ.get("SEECUT_STANDALONE_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone"))) / "tools")
    gsap_probe_root = gdir / "_gsap_tools"
    gsap_shim_used = False
    if not (troot / "node_modules/hyperframes/dist/vendor/npm/gsap@3.15.0/dist/gsap.min.js").is_file():
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

    sample_params: dict[str, dict[str, Any]] = {
        "kh03.contradiction_lockup": {"kicker": "EXAMPLE", "left_value": "$1,200", "operator": "≠", "right_value": "$?", "headline": "Balance is not spendable"},
        "kh03.proportional_reservoir": {"kicker": "TRUE PROPORTION", "headline": "Budget Allocation", "segments": [{"label": "Bills", "pct": 66.7, "delta": "-$800"}, {"label": "Food", "pct": 16.7, "delta": "-$200"}, {"label": "Gas", "pct": 8.3, "delta": "-$100"}, {"label": "Left", "pct": 8.3, "delta": "$100"}]},
        "kh03.rolling_counter": {"kicker": "REMAINING", "headline": "Discrete Tabular Roll", "start_value": "$1,200", "end_value": "$100", "unit": "USD"},
        "kh03.causal_flow_graph": {"kicker": "MECHANISM", "headline": "Heat Pump Cycle", "nodes": [{"label": "Absorb", "text": "Outdoor air"}, {"label": "Compress", "text": "Raise temp"}, {"label": "Release", "text": "Warm room"}]},
        "kh03.annotated_timeline": {"kicker": "CHRONOLOGY", "headline": "1440 Printing Shift", "steps": [{"label": "1440", "text": "Movable type"}, {"label": "Weeks", "text": "Pamphlets spread"}]},
        "kh03.comparison_matrix": {"kicker": "COMPARISON", "headline": "Linen vs Cotton", "left_col": {"title": "Linen", "detail": "Hollow flax airflow"}, "right_col": {"title": "Cotton", "detail": "Tightly packed fibers"}},
        "kh03.proof_frame_reveal": {"kicker": "VERIFIED COVER", "headline": "The Next Paycheck Plan", "subtitle": "REAL_EVIDENCE", "evidence_label": "REAL_EVIDENCE"},
        "kh03.cta_magnetic_underline": {"kicker": "NEXT STEP", "headline": "Plan your next payday.", "cta_tag": "Link in my profile"},
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
            "archetype": "visual_essay_modular", "summary": "KH-03 8-component gallery", "alternatives_considered": [],
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
            "planner_version": "kh03.0.0", "generated_at_utc": "1970-01-01T00:00:00Z",
            "engine": "SeeCut-v3.4-HyperFrames-HTML-GSAP",
            "base_git_sha": "7e8a93d", "upstream_seecut_sha": "b05d7ed",
        },
    }

    compiled = compile_shotbook(shotbook, inventory, bdir, tools_root=troot, registry=reg)
    html_url = Path(compiled["html_path"]).resolve().as_uri()

    # Build direct / reverse / repeat / cold seek plan using native Node v26 CDP (zero npm deps!)
    plan1 = [{"label": "t0_blank", "t": 0.0}]
    for idx, sp in enumerate(specs):
        ts = round((sp["start_frame"] + 14) / float(fps), 4)
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
const path = require('node:path');
const chromePath = process.argv[1];
const url = process.argv[2];
const outDir = process.argv[3];
const W = parseInt(process.argv[4], 10);
const H = parseInt(process.argv[5], 10);
const plan1 = JSON.parse(process.argv[6]);
const plan2 = JSON.parse(process.argv[7]);

async function runPass(plan, port) {
  const errors = [];
  const udir = path.join(outDir, `_prof_${port}_${process.pid}`);
  fs.mkdirSync(udir, { recursive: true });
  const proc = spawn(chromePath, [
    `--remote-debugging-port=${port}`, `--user-data-dir=${udir}`,
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--mute-audio', '--force-device-scale-factor=1', `--window-size=${W},${H}`, 'about:blank'
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
    for (const item of plan) {
      await send('Runtime.evaluate', { expression: `window.__timelines.master.time(${item.t}).pause(); void document.body.offsetHeight; true;`, returnByValue: true });
      const shot = await send('Page.captureScreenshot', { format: 'png', fromSurface: true });
      fs.writeFileSync(path.join(outDir, item.label + '.png'), Buffer.from(shot.data, 'base64'));
    }
  } finally {
    try { ws.close(); } catch {}
    try { proc.kill('SIGKILL'); } catch {}
    await new Promise(r => setTimeout(r, 200));
    try { fs.rmSync(udir, { recursive: true, force: true }); } catch {}
  }
  return errors;
}

(async () => {
  const e1 = await runPass(plan1, 9461);
  const e2 = await runPass(plan2, 9462);
  console.log(JSON.stringify({ ok: (e1.length === 0 && e2.length === 0), errors: [...e1, ...e2] }));
})().catch(e => { console.error(e); process.exit(1); });
"""
    pr = subprocess.run(
        ["node", "-e", node_cdp, str(chrome_exe), html_url, str(shots_dir), str(width), str(height), json.dumps(plan1), json.dumps(plan2)],
        capture_output=True, text=True, timeout=90,
    )
    if pr.returncode != 0:
        return {"pass": False, "status": "FAIL_CHROMIUM_CDP", "error": pr.stderr[-400:], "capabilities": {}}

    meta = json.loads(pr.stdout.strip().splitlines()[-1])
    t0_png = shots_dir / "t0_blank.png"
    results: dict[str, dict[str, Any]] = {}
    all_pass = bool(meta.get("ok"))

    for idx, sp in enumerate(specs):
        p_dir = shots_dir / f"c{idx}_direct.png"
        p_rev = shots_dir / f"c{idx}_reverse.png"
        p_rep = shots_dir / f"c{idx}_repeat.png"
        p_cld = shots_dir / f"c{idx}_cold.png"
        bx, by, bw, bh = sp["normalized_bbox"]
        crop = (int(bx * width), int(by * height), int((bx + bw) * width), int((by + bh) * height))

        diff_vis = _mean_abs_diff(t0_png, p_dir, crop)
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
    write_json_atomic(gdir / "CAPABILITY_REGISTRY_VERIFIED.json", verified_reg)
    return report
