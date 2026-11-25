"""KH-02 Independent QA, Objective Diagnostics & Filmstrip/Waveform Renderer.

Selective adaptation of filmstrip + PCM waveform + silence + output-clock word label rendering from:
  browser-use/video-use @ 43cfc566833548093455c9d4a46bd69913b7e552
  helpers/timeline_view.py (MIT License, Copyright (c) 2026 Browser Use).
"""

from __future__ import annotations

import csv
import json
import math
import os
import struct
import subprocess
from pathlib import Path
from typing import Any

from .camera import validate_camera_continuity
from .contracts import hash_artifact, validate_references
from .motion import validate_motion_curves
from .sound import measure_final_audio, validate_cues
from .source_analysis import probe_source, sha256_file
from .transition import validate_seam
from .typography import validate_readability


def validate_structural(
    mp4_path: str | Path,
    shotbook: dict[str, Any],
    *,
    expect_audio: bool = True,
    provenance_log: dict[str, Any] | None = None,
    render_scale_override: float | None = None,
) -> dict[str, Any]:
    """Decode every frame + audio packet of final MP4 and verify stream specs, PTS monotonicity, EOF freeze/black & provenance."""
    p = Path(mp4_path)
    issues: list[dict[str, Any]] = []
    if not p.is_file() or p.stat().st_size < 1024:
        return {"pass": False, "issues": [{"severity": "P0", "code": "MP4_MISSING_OR_EMPTY", "message": f"Invalid MP4: {p}"}]}

    # 1. Full decode check
    dec = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-f", "null", "-"], capture_output=True, text=True, timeout=180)
    if dec.returncode != 0 or dec.stderr.strip():
        issues.append({"severity": "P0", "code": "MP4_DECODE_ERROR", "message": f"ffmpeg decode error rc={dec.returncode}: {dec.stderr.strip()[:240]}"})

    # 2. Stream probe
    try:
        probed = probe_source(p)
    except Exception as e:
        return {"pass": False, "issues": [{"severity": "P0", "code": "MP4_PROBE_FAIL", "message": str(e)}]}

    media = probed["media"]
    spec = shotbook["output_spec"]
    eff_scale = float(render_scale_override if render_scale_override is not None else spec.get("output_scale", 1.0))
    expected_w = int(round(spec["width"] * eff_scale))
    expected_h = int(round(spec["height"] * eff_scale))
    expected_frames = int(spec["total_frames"])
    fps = spec["fps_num"] / spec["fps_den"]
    expected_dur = expected_frames / fps

    if media["width"] != expected_w or media["height"] != expected_h:
        issues.append({
            "severity": "P0", "code": "MP4_RESOLUTION_MISMATCH",
            "message": f"Expected {expected_w}x{expected_h}, got {media['width']}x{media['height']}",
        })
    if media["nb_frames"] != expected_frames:
        issues.append({
            "severity": "P0", "code": "MP4_FRAMECOUNT_MISMATCH",
            "message": f"Expected {expected_frames} frames, got {media['nb_frames']}",
        })
    if media["duration_sec"] is None or abs(media["duration_sec"] - expected_dur) > 0.12:
        issues.append({
            "severity": "P0", "code": "MP4_DURATION_DRIFT",
            "message": f"Duration {media['duration_sec']}s drifts >0.12s from expected {expected_dur:.3f}s",
        })
    if expect_audio and not media["has_audio"]:
        issues.append({"severity": "P0", "code": "MP4_MISSING_AUDIO", "message": "Required audio stream missing"})

    # 3. Monotonic PTS check via ffprobe JSON (cross-version FFmpeg 6/7/8/9 safe; rejects corrupt values)
    pts_proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "frame=best_effort_timestamp_time",
         "-of", "json", str(p)],
        capture_output=True, text=True, timeout=60,
    )
    pts_vals: list[float] = []
    if pts_proc.returncode == 0 and pts_proc.stdout.strip():
        pts_doc = json.loads(pts_proc.stdout)
        for fr in pts_doc.get("frames", []):
            raw_ts = fr.get("best_effort_timestamp_time")
            if raw_ts is not None and str(raw_ts).strip() not in ("", "N/A"):
                pts_vals.append(float(raw_ts))
    non_mono = sum(1 for i in range(1, len(pts_vals)) if pts_vals[i] <= pts_vals[i - 1])
    if non_mono > 0:
        issues.append({"severity": "P0", "code": "MP4_NON_MONOTONIC_PTS", "message": f"{non_mono} non-monotonic video PTS entries"})

    # 4. Black flash / EOF black detection (d >= 0.08s, pixel thresh 0.98)
    bd = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(p), "-vf", "blackdetect=d=0.08:pix_th=0.10", "-an", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    black_spans = [line.strip() for line in bd.stderr.splitlines() if "black_start:" in line]
    if black_spans:
        issues.append({"severity": "P1", "code": "MP4_BLACK_DETECTED", "message": f"Unexpected black spans: {black_spans[:3]}"})

    # 5. Native engine provenance check (reject mock FFmpeg-only file masquerading as SeeCut render)
    provenance_verified = False
    if provenance_log:
        provenance_verified = (
            provenance_log.get("engine") == "SeeCut-v3.4-HyperFrames-HTML-GSAP"
            and bool(provenance_log.get("renderer_sha256"))
            and provenance_log.get("mp4_sha256") == probed["sha256"]
        )
        if not provenance_verified:
            issues.append({
                "severity": "P0", "code": "PROVENANCE_MISMATCH",
                "message": "Render provenance does not prove native SeeCut HTML/GSAP render for this MP4 SHA",
            })

    return {
        "pass": len([i for i in issues if i["severity"] == "P0"]) == 0,
        "strict_pass": len(issues) == 0,
        "mp4_sha256": probed["sha256"],
        "size_bytes": probed["size_bytes"],
        "media": media,
        "pts_count": len(pts_vals),
        "non_monotonic_pts": non_mono,
        "black_spans": black_spans,
        "provenance_verified": provenance_verified,
        "issues": issues,
    }


def inspect_motion(shotbook: dict[str, Any]) -> dict[str, Any]:
    spec = shotbook["output_spec"]
    fps = spec["fps_num"] / spec["fps_den"]
    motion_issues = validate_motion_curves(shotbook.get("visual_events", []), fps=fps, profile=shotbook["profile"])
    cam_issues = validate_camera_continuity(
        shotbook.get("camera_events", []),
        fps=fps,
        presenter_policy=shotbook.get("presenter_policy"),
        total_frames=int(spec["total_frames"]),
    )
    all_issues = motion_issues + cam_issues
    return {"pass": len(all_issues) == 0, "motion_issues": motion_issues, "camera_issues": cam_issues}


def inspect_seams(shotbook: dict[str, Any]) -> dict[str, Any]:
    spec = shotbook["output_spec"]
    fps = spec["fps_num"] / spec["fps_den"]
    issues = validate_seam(
        shotbook.get("transitions", []),
        shotbook.get("beats", []),
        fps=fps,
        profile=shotbook["profile"],
        approved_exceptions=shotbook.get("approved_exceptions"),
    )
    return {"pass": len(issues) == 0, "issues": issues}


def inspect_audio(
    mp4_path: str | Path,
    shotbook: dict[str, Any],
    *,
    raw_source_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run real EBU R128 loudness/true peak QA + optional dry-vs-mixed PCM correlation check."""
    voice_policy = shotbook.get("edit_decision_map", {}).get("voice_policy", "LOCK_VOICE")
    if voice_policy == "SILENT_BY_DESIGN":
        return {"pass": True, "status": "SILENT_BY_DESIGN", "metrics": {"has_audio": False}, "issues": []}

    metrics = measure_final_audio(mp4_path)
    issues: list[dict[str, Any]] = []
    if not metrics.get("measured"):
        issues.append({"severity": "P0", "code": "AUDIO_UNMEASURED", "message": metrics.get("error", "EBU R128 measurement failed")})
    elif metrics.get("clipping_detected"):
        issues.append({"severity": "P1", "code": "AUDIO_CLIPPING", "message": f"True peak {metrics['true_peak_dbtp']} dBTP > -0.1"})

    cue_errs, cue_warns = validate_cues(
        shotbook.get("audio_cues", []),
        total_frames=int(shotbook["output_spec"]["total_frames"]),
        fps=shotbook["output_spec"]["fps_num"] / shotbook["output_spec"]["fps_den"],
    )
    issues.extend(cue_errs)

    correlation = None
    if raw_source_path and Path(raw_source_path).is_file() and voice_policy == "LOCK_VOICE":
        try:
            def read_pcm(fp: str | Path) -> list[int]:
                p = subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", str(fp), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                    capture_output=True, check=True, timeout=60,
                )
                n = len(p.stdout) // 2
                return list(struct.unpack(f"<{n}h", p.stdout[: n * 2]))
            a = read_pcm(raw_source_path)
            b = read_pcm(mp4_path)
            n = min(len(a), len(b))
            if n > 100:
                a, b = a[:n], b[:n]
                ma = sum(a) / n
                mb = sum(b) / n
                num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
                da = math.sqrt(sum((x - ma) ** 2 for x in a))
                db = math.sqrt(sum((y - mb) ** 2 for y in b))
                correlation = round(num / (da * db), 6) if (da > 0 and db > 0) else 1.0
                if correlation < 0.995:
                    issues.append({
                        "severity": "P0", "code": "LOCK_VOICE_AUDIO_CORRUPTION",
                        "message": f"Voice PCM correlation {correlation} < 0.995 under LOCK_VOICE",
                    })
        except Exception as e:
            issues.append({"severity": "P1", "code": "AUDIO_CORR_ERROR", "message": str(e)})

    return {
        "pass": len([i for i in issues if i["severity"] == "P0"]) == 0,
        "metrics": metrics,
        "voice_pcm_correlation": correlation,
        "warnings": cue_warns,
        "issues": issues,
        "owner_audio_status": "PENDING",
        "auto_perceptual_status": "PENDING",
    }


def _compute_pcm_envelope(video_path: Path, start_s: float, end_s: float, bins: int) -> list[float]:
    """Compute normalized RMS waveform envelope (adapted from video-use timeline_view.py, MIT)."""
    dur = max(0.05, end_s - start_s)
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start_s:.3f}", "-t", f"{dur:.3f}",
         "-i", str(video_path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True, timeout=30,
    )
    raw = proc.stdout
    n = len(raw) // 2
    if n < 2 or bins <= 0:
        return [0.0] * max(1, bins)
    samples = struct.unpack(f"<{n}h", raw[: n * 2])
    step = max(1, n // bins)
    env: list[float] = []
    for i in range(bins):
        chunk = samples[i * step : min(n, (i + 1) * step)]
        if not chunk:
            env.append(0.0)
        else:
            rms = math.sqrt(sum(s * s for s in chunk) / len(chunk)) / 32768.0
            env.append(rms)
    peak = max(env) if env else 0.0
    return [v / peak if peak > 1e-6 else 0.0 for v in env]


def render_timeline_diagnostics(
    video_path: str | Path,
    out_dir: str | Path,
    *,
    windows: list[dict[str, Any]],
    mapped_words: list[dict[str, Any]] | None = None,
    shotbook: dict[str, Any] | None = None,
    frames_per_window: int = 6,
) -> dict[str, Any]:
    """Render bounded boundary filmstrips + real PCM waveform + output-clock word labels (adapted from video-use MIT).

    Also emits a deterministic per-window frame index CSV distinguishing planned events from pixel stats.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return {"status": "BLOCKED_MISSING_DIAGNOSTIC", "reason": "Pillow (PIL) not installed in current Python interpreter", "files": []}

    vpath = Path(video_path)
    odir = Path(out_dir)
    odir.mkdir(parents=True, exist_ok=True)
    probed = probe_source(vpath)
    media = probed["media"]
    dur = float(media.get("duration_sec") or 0.0)
    if media.get("nb_frames") and media.get("fps_num") and media.get("fps_den"):
        vid_dur = max(0.0, (int(media["nb_frames"]) - 2) * float(media["fps_den"]) / float(media["fps_num"]))
        max_t = max(0.0, min(dur - 0.08, vid_dur))
    else:
        max_t = max(0.0, dur - 0.06)
    if dur <= 0:
        return {"status": "FAIL", "reason": "Zero/invalid video duration", "files": []}

    created: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []
    words = mapped_words or []

    thumb_w, thumb_h = 180, 320
    if (media.get("width") or 1080) > (media.get("height") or 1920):
        thumb_w, thumb_h = 280, 158
    wave_h, label_h, head_h = 90, 56, 32

    for w_idx, win in enumerate(windows):
        tag = str(win.get("name") or f"win_{w_idx:02d}")
        s0 = max(0.0, float(win["start_sec"]))
        s1 = min(dur, float(win["end_sec"]))
        if s1 <= s0 + 0.05:
            s1 = min(dur, s0 + 0.5)
        n_fr = max(2, min(10, int(frames_per_window)))
        # Ensure exact end-of-stream frame lands strictly on or before last valid video frame PTS
        sample_ts = [min(max_t, s0 + (s1 - s0) * (k / max(1, n_fr - 1))) for k in range(n_fr)]

        canvas_w = thumb_w * n_fr
        canvas_h = head_h + thumb_h + wave_h + label_h
        img = Image.new("RGB", (canvas_w, canvas_h), (20, 18, 16))
        draw = ImageDraw.Draw(img)
        draw.text((10, 8), f"{tag} | {s0:.2f}s -> {s1:.2f}s | {vpath.name}", fill=(245, 240, 230))

        prev_luma = None
        for k, ts in enumerate(sample_ts):
            tmp_jpg = odir / f"_tmp_{tag}_{k}.jpg"
            proc = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", f"{ts:.3f}", "-i", str(vpath),
                 "-frames:v", "1", "-vf", f"scale={thumb_w}:{thumb_h}:force_original_aspect_ratio=decrease,pad={thumb_w}:{thumb_h}:(ow-iw)/2:(oh-ih)/2",
                 str(tmp_jpg)],
                capture_output=True, timeout=30,
            )
            luma = 0.0
            motion_delta = 0.0
            if proc.returncode == 0 and tmp_jpg.is_file():
                with Image.open(tmp_jpg) as fr_im:
                    fr_rgb = fr_im.convert("RGB")
                    img.paste(fr_rgb, (k * thumb_w, head_h))
                    gray = fr_rgb.convert("L").resize((32, 32))
                    vals = list(gray.get_flattened_data() if hasattr(gray, "get_flattened_data") else gray.getdata())
                    luma = round(sum(vals) / len(vals), 2)
                    if prev_luma is not None:
                        motion_delta = round(abs(luma - prev_luma), 2)
                    prev_luma = luma
                tmp_jpg.unlink(missing_ok=True)
            draw.rectangle([k * thumb_w, head_h, k * thumb_w + 76, head_h + 20], fill=(0, 0, 0))
            draw.text((k * thumb_w + 4, head_h + 3), f"{ts:.2f}s", fill=(255, 220, 120))

            # Record CSV row distinguishing planned events from observed luma/motion
            planned_ids = []
            beat_id = ""
            if shotbook:
                fps = shotbook["output_spec"]["fps_num"] / shotbook["output_spec"]["fps_den"]
                f_idx = int(round(ts * fps))
                b = next((x for x in shotbook["beats"] if x["start_frame"] <= f_idx < x["end_frame"]), None)
                beat_id = b["beat_id"] if b else ""
                planned_ids = [ev["event_id"] for ev in shotbook["visual_events"] if ev["start_frame"] <= f_idx < ev["end_frame"]]
            csv_rows.append({
                "window": tag,
                "pts_sec": round(ts, 3),
                "beat_id": beat_id,
                "planned_visible_events": ";".join(planned_ids),
                "observed_luma": luma,
                "observed_luma_delta": motion_delta,
            })

        # Draw PCM waveform
        wy0 = head_h + thumb_h
        draw.rectangle([0, wy0, canvas_w, wy0 + wave_h], fill=(28, 26, 24))
        bins = max(40, canvas_w // 4)
        env = _compute_pcm_envelope(vpath, s0, s1, bins)
        mid_y = wy0 + wave_h // 2
        for bi, amp in enumerate(env):
            x = int(bi * (canvas_w / bins))
            h_bar = int(amp * (wave_h * 0.44))
            color = (212, 82, 51) if amp < 0.06 else (120, 210, 175)
            draw.line([(x, mid_y - h_bar), (x, mid_y + h_bar)], fill=color, width=2)

        # Draw output-clock word labels along bottom strip
        ly0 = wy0 + wave_h
        draw.rectangle([0, ly0, canvas_w, canvas_h], fill=(16, 15, 14))
        win_words = [w for w in words if float(w.get("out_end_sec", 0)) >= s0 and float(w.get("out_start_sec", 0)) <= s1]
        for idx_w, w in enumerate(win_words[:20]):
            wt = float(w["out_start_sec"])
            wx = int(((wt - s0) / max(0.05, s1 - s0)) * (canvas_w - 40))
            wx = max(4, min(canvas_w - 60, wx))
            row_y = ly0 + 6 + (idx_w % 2) * 22
            draw.text((wx, row_y), str(w["text"])[:14], fill=(245, 240, 230))

        out_png = odir / f"diag_{w_idx:02d}_{tag}.png"
        img.save(out_png)
        created.append({"window": tag, "start_sec": round(s0, 3), "end_sec": round(s1, 3), "path": str(out_png), "sha256": sha256_file(out_png)})

    csv_path = odir / "frame_diagnostics.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["window", "pts_sec", "beat_id", "planned_visible_events", "observed_luma", "observed_luma_delta"])
        writer.writeheader()
        writer.writerows(csv_rows)

    return {"status": "PASS", "files": created, "csv_path": str(csv_path)}


def evaluate_editorial(shotbook: dict[str, Any], inventory: dict[str, Any] | None = None, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deterministic objective editorial evaluation (contract + motion + camera + seams + typography)."""
    ref_errs = validate_references(shotbook, inventory=inventory, registry=registry)
    spec = shotbook["output_spec"]
    fps = spec["fps_num"] / spec["fps_den"]
    mot = inspect_motion(shotbook)
    seams = inspect_seams(shotbook)
    max_z = max([int(e.get("z_order", 0)) for e in shotbook.get("visual_events", [])] + [50])
    caption_pol = "ON_REQUESTED" if any(t.get("role") == "caption" for t in shotbook.get("text_events", [])) else "OFF"
    typo_errs = validate_readability(
        shotbook.get("text_events", []),
        canvas_width=int(spec["width"]),
        canvas_height=int(spec["height"]),
        fps=fps,
        caption_policy=caption_pol,
        platform_safe_zone=spec.get("platform_safe_zone"),
        max_visual_z_order=max_z,
    )
    ok = (not ref_errs) and mot["pass"] and seams["pass"] and (not typo_errs)
    return {
        "pass": ok,
        "contract_errors": ref_errs,
        "motion_issues": mot["motion_issues"],
        "camera_issues": mot["camera_issues"],
        "seam_issues": seams["issues"],
        "typography_issues": typo_errs,
    }


def decide_adoption(
    *,
    h1_pass: bool,
    h2_pass: bool,
    objective_qa_pass: bool,
    pairwise_consensus: str | None,
    candidate_label: str = "A",
) -> dict[str, Any]:
    """Strict adoption rule: requires H1 + H2 + objective QA PASS AND dual-order pairwise consensus."""
    if not (h1_pass and h2_pass and objective_qa_pass):
        return {
            "adopted": False,
            "decision": "REJECTED_HARD_GATE",
            "reason": f"Hard gates incomplete (h1={h1_pass}, h2={h2_pass}, objective_qa={objective_qa_pass})",
        }
    if pairwise_consensus is None:
        return {"adopted": False, "decision": "PENDING_REVIEW", "reason": "No valid dual-order pairwise model review yet."}
    if pairwise_consensus == candidate_label:
        return {"adopted": True, "decision": "ADOPTED", "reason": f"Candidate {candidate_label} passed H1/H2/objective QA and won dual-order consensus."}
    if pairwise_consensus == "tie":
        return {"adopted": False, "decision": "CANDIDATE_NOT_ADOPTED_TIE", "reason": "Order-swapped pairwise comparison ended in tie."}
    if pairwise_consensus == "invalid":
        return {"adopted": False, "decision": "BLOCKED_INVALID_JUDGE", "reason": "Pairwise judge returned invalid output."}
    return {"adopted": False, "decision": "CANDIDATE_NOT_ADOPTED_LOSS", "reason": f"Pairwise consensus selected {pairwise_consensus}."}


def build_review_report(
    *,
    run_id: str,
    evidence_records: list[dict[str, Any]],
    original_19_gates: dict[str, str],
    kh02_subgates: dict[str, str],
    statuses: dict[str, str],
) -> dict[str, Any]:
    """Assemble honest QA_REPORT JSON separating code QA, Olivia render, auto editorial QA, human approvals & commercial gate."""
    # Never claim READY_FOR_PRODUCTION while owner approvals are PENDING or commercial release is BLOCKED
    if any(r["status"] == "FAIL" for r in evidence_records):
        overall = "FAIL"
    elif statuses.get("COMMERCIAL_RELEASE") == "BLOCKED" or statuses.get("OWNER_VISUAL") == "PENDING" or statuses.get("OWNER_AUDIO") == "PENDING":
        overall = "PREVIEW_PENDING_APPROVAL" if statuses.get("OLIVIA_RENDER_VERIFIED") == "PASS" else "CODE_READY_ONLY"
    else:
        overall = "READY_FOR_PRODUCTION"

    return {
        "schema_version": "kh02.qa_report.v1",
        "run_id": run_id,
        "overall_status": overall,
        "statuses": statuses,
        "evidence_records": evidence_records,
        "original_19_gates": original_19_gates,
        "kh02_subgates": kh02_subgates,
    }


# ==============================================================================
# KH-02.R2 — Decoded Per-Frame Presenter QA, H1 Runner, Effect Ledger, Seam/Hold & Layer Fidelity
# ==============================================================================


def _stream_cropped_rgb_frames(
    video_path: Path,
    crop_px: tuple[int, int, int, int],
    *,
    grid: int = 48,
    max_frames: int | None = None,
) -> list[bytes]:
    """Stream exact decoded RGB24 thumbnails of a cropped rectangle from a video in constant O(grid^2) per frame."""
    cx, cy, cw, ch = crop_px
    vf = f"crop={max(2, cw)}:{max(2, ch)}:{max(0, cx)}:{max(0, cy)},scale={grid}:{grid}:flags=area"
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video_path),
        "-vf", vf, "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frame_bytes = grid * grid * 3
    frames: list[bytes] = []
    assert proc.stdout is not None
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frames.append(buf)
            if max_frames is not None and len(frames) >= max_frames:
                break
    finally:
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()
        proc.wait(timeout=60)
    return frames


def _rgb_stats_and_match(a: bytes, b: bytes | None) -> dict[str, float]:
    """Pure-stdlib RGB24 luma/std + normalized MAE & Pearson correlation between two cropped patches."""
    n = len(a)
    if n == 0:
        return {"luma_mean": 0.0, "luma_std": 0.0, "mae_norm": 1.0, "corr": 0.0}
    # Sample every 3rd byte stride cleanly (R,G,B triplets)
    px_count = n // 3
    lumas = [(0.299 * a[i] + 0.587 * a[i + 1] + 0.114 * a[i + 2]) for i in range(0, px_count * 3, 3)]
    l_mean = sum(lumas) / px_count
    l_var = sum((v - l_mean) ** 2 for v in lumas) / px_count
    l_std = math.sqrt(max(0.0, l_var))
    if b is None or len(b) != n:
        return {"luma_mean": round(l_mean, 2), "luma_std": round(l_std, 2), "mae_norm": 1.0, "corr": 0.0}

    # Normalized MAE & Pearson correlation across RGB channels
    diff_sum = 0
    sum_a = 0
    sum_b = 0
    for i in range(n):
        va = a[i]
        vb = b[i]
        diff_sum += abs(va - vb)
        sum_a += va
        sum_b += vb
    mae = diff_sum / (255.0 * n)
    ma = sum_a / n
    mb = sum_b / n
    num = 0.0
    da = 0.0
    db = 0.0
    for i in range(n):
        xa = a[i] - ma
        xb = b[i] - mb
        num += xa * xb
        da += xa * xa
        db += xb * xb
    corr = (num / math.sqrt(da * db)) if (da > 1e-6 and db > 1e-6) else (1.0 if mae < 0.03 else 0.0)
    return {
        "luma_mean": round(l_mean, 2),
        "luma_std": round(l_std, 2),
        "mae_norm": round(mae, 4),
        "corr": round(corr, 4),
    }


def _patch_motion(prev_b: bytes | None, cur_b: bytes) -> float:
    if prev_b is None or len(prev_b) != len(cur_b) or not cur_b:
        return 0.0
    return round(sum(abs(cur_b[i] - prev_b[i]) for i in range(len(cur_b))) / (255.0 * len(cur_b)), 5)


def verify_presenter_frames(
    mp4_path: str | Path,
    derived_source_path: str | Path,
    shotbook: dict[str, Any],
    out_dir: str | Path,
    *,
    corr_threshold: float = 0.62,
    mae_threshold: float = 0.24,
    emit_crop_evidence: bool = True,
) -> dict[str, Any]:
    """Decode EVERY frame 0..N-1 of rendered MP4 and verify presenter PIP visibility, crop match, motion & non-occlusion."""
    mp4 = Path(mp4_path).resolve()
    src = Path(derived_source_path).resolve()
    odir = Path(out_dir).resolve()
    odir.mkdir(parents=True, exist_ok=True)

    pres_pol = shotbook.get("presenter_policy") or {}
    pres_mode = str(pres_pol.get("mode", "LEGACY_DYNAMIC"))
    pres_vis = str(pres_pol.get("visibility", "EDITORIAL_ALLOWED"))
    if pres_mode == "FACELESS":
        return {"pass": True, "status": "NOT_APPLICABLE_FACELESS", "total_frames": 0}

    pres_lay = shotbook.get("presenter_layout")
    if not pres_lay and shotbook.get("camera_events"):
        c0 = shotbook["camera_events"][0]
        pres_lay = {"region": {"normalized_box": c0["target_region"]}, "crop": c0.get("presenter_crop") or {}}
    if not pres_lay or not mp4.is_file() or not src.is_file():
        return {"pass": False, "status": "FAIL_MISSING_INPUTS", "issues": ["Missing presenter_layout, rendered MP4, or derived source"]}

    mp4_probe = probe_source(mp4)["media"]
    src_probe = probe_source(src)["media"]
    rw, rh = int(mp4_probe["width"]), int(mp4_probe["height"])
    sw, sh = int(src_probe["width"]), int(src_probe["height"])
    spec = shotbook["output_spec"]
    expected_frames = int(spec["total_frames"])
    fps = spec["fps_num"] / spec["fps_den"]

    # Inset inner crop 10% inside PIP border/radius so rounded corners & border stroke never distort pixel match
    nx, ny, nw, nh = [float(v) for v in pres_lay["region"]["normalized_box"]]
    inset = 0.10
    rx = int(round((nx + nw * inset) * rw))
    ry = int(round((ny + nh * inset) * rh))
    rw_c = max(4, int(round((nw * (1.0 - 2.0 * inset)) * rw)))
    rh_c = max(4, int(round((nh * (1.0 - 2.0 * inset)) * rh)))

    # Expected matching inner source crop from presenter_crop metadata
    sc_norm = (pres_lay.get("crop") or {}).get("source_crop_norm") or [0.0, 0.0, 1.0, 1.0]
    sx0, sy0, sx1, sy1 = [float(v) for v in sc_norm]
    sc_w, sc_h = max(0.05, sx1 - sx0), max(0.05, sy1 - sy0)
    src_x = int(round((sx0 + sc_w * inset) * sw))
    src_y = int(round((sy0 + sc_h * inset) * sh))
    src_cw = max(4, int(round((sc_w * (1.0 - 2.0 * inset)) * sw)))
    src_ch = max(4, int(round((sc_h * (1.0 - 2.0 * inset)) * sh)))

    ren_patches = _stream_cropped_rgb_frames(mp4, (rx, ry, rw_c, rh_c), grid=40)
    src_patches = _stream_cropped_rgb_frames(src, (src_x, src_y, src_cw, src_ch), grid=40)

    n_ren = len(ren_patches)
    n_src = len(src_patches)
    rows: list[dict[str, Any]] = []
    occluded_frames: list[int] = []
    frozen_mismatch_frames: list[int] = []
    low_match_frames: list[int] = []
    corrs: list[float] = []
    maes: list[float] = []

    for i in range(n_ren):
        rp = ren_patches[i]
        sp = src_patches[min(i, n_src - 1)] if n_src > 0 else None
        st = _rgb_stats_and_match(rp, sp)
        r_mot = _patch_motion(ren_patches[i - 1] if i > 0 else None, rp)
        s_mot = _patch_motion(src_patches[min(i - 1, n_src - 1)] if (i > 0 and n_src > 0) else None, sp) if sp else 0.0

        # Detect solid-color/black occlusion or mismatched overlay painted over PIP
        is_flat_black = (st["luma_mean"] < 8.0 and st["luma_std"] < 4.0)
        is_mismatch = (st["corr"] < corr_threshold and st["mae_norm"] > mae_threshold)
        occluded = is_flat_black or is_mismatch
        # Detect frozen presenter frame while derived source is actively moving
        frozen_mismatch = (i > 0 and s_mot >= 0.008 and r_mot < 0.0015 and st["mae_norm"] > 0.05)

        if occluded:
            occluded_frames.append(i)
        if is_mismatch:
            low_match_frames.append(i)
        if frozen_mismatch:
            frozen_mismatch_frames.append(i)
        corrs.append(st["corr"])
        maes.append(st["mae_norm"])

        rows.append({
            "frame": i,
            "pts_sec": round(i / fps, 4),
            "bbox_px": f"{int(round(nx*rw))},{int(round(ny*rh))},{int(round(nw*rw))},{int(round(nh*rh))}",
            "luma_mean": st["luma_mean"],
            "luma_std": st["luma_std"],
            "crop_mae_norm": st["mae_norm"],
            "crop_corr": st["corr"],
            "source_motion": s_mot,
            "render_motion": r_mot,
            "occluded": occluded,
            "frozen_mismatch": frozen_mismatch,
            "status": "FAIL_OCCLUDED" if occluded else ("FAIL_FROZEN" if frozen_mismatch else "VISIBLE_MATCHED"),
        })

    # Write PRESENTER_FRAME_QA.csv
    csv_path = odir / "PRESENTER_FRAME_QA.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "frame", "pts_sec", "bbox_px", "luma_mean", "luma_std",
            "crop_mae_norm", "crop_corr", "source_motion", "render_motion",
            "occluded", "frozen_mismatch", "status",
        ])
        w.writeheader()
        w.writerows(rows)

    frame_count_ok = (n_ren == expected_frames)
    frame_0_ok = (n_ren > 0 and not rows[0]["occluded"])
    eof_ok = (n_ren > 0 and not rows[-1]["occluded"])
    visible_count = sum(1 for r in rows if not r["occluded"])
    coverage = round(visible_count / float(max(1, expected_frames)), 4)

    issues: list[str] = []
    if not frame_count_ok:
        issues.append(f"Decoded frame count {n_ren} != expected {expected_frames}")
    if pres_vis == "ALWAYS_VISIBLE":
        if not frame_0_ok:
            issues.append("Frame 0 presenter PIP occluded, empty, or mismatched")
        if not eof_ok:
            issues.append(f"Final frame {n_ren - 1} presenter PIP occluded, empty, or faded")
        if occluded_frames:
            issues.append(f"{len(occluded_frames)} decoded frame(s) failed PIP presence/non-occlusion check (sample: {occluded_frames[:8]})")
        if frozen_mismatch_frames:
            issues.append(f"{len(frozen_mismatch_frames)} decoded frame(s) frozen vs moving source (sample: {frozen_mismatch_frames[:8]})")

    # Emit visual crop comparison evidence + 360px mobile preview when Pillow available
    evidence_files: list[str] = []
    evid_dir = odir / "PIP_CROP_EVIDENCE"
    if emit_crop_evidence and n_ren > 0:
        try:
            from PIL import Image, ImageDraw
            evid_dir.mkdir(parents=True, exist_ok=True)
            sample_indices = sorted({
                0,
                max(0, n_ren // 4),
                max(0, n_ren // 2),
                max(0, (3 * n_ren) // 4),
                max(0, n_ren - 1),
            })
            # Include beat boundaries
            for b in shotbook.get("beats", [])[1:]:
                bf = max(0, min(n_ren - 1, int(b["start_frame"])))
                if bf not in sample_indices and len(sample_indices) < 9:
                    sample_indices.append(bf)
            sample_indices = sorted(sample_indices)

            full_box = (int(round(nx * rw)), int(round(ny * rh)), max(4, int(round(nw * rw))), max(4, int(round(nh * rh))))
            src_full_box = (int(round(sx0 * sw)), int(round(sy0 * sh)), max(4, int(round(sc_w * sw))), max(4, int(round(sc_h * sh))))

            def _extract_png(vid: Path, fidx: int, out_p: Path, vf_extra: str | None = None) -> bool:
                vf = f"select=eq(n\\,{fidx})" + (f",{vf_extra}" if vf_extra else "")
                pr = subprocess.run(
                    ["ffmpeg", "-v", "error", "-y", "-i", str(vid), "-vf", vf, "-vsync", "0", "-frames:v", "1", str(out_p)],
                    capture_output=True, timeout=30,
                )
                return pr.returncode == 0 and out_p.is_file()

            # Build composite comparison sheet: [Full 360w Mobile | Rendered PIP | Expected Source Crop]
            mobile_thumbs: list[Any] = []
            for fidx in sample_indices:
                tmp_full = evid_dir / f"_tmp_full_{fidx}.png"
                tmp_pip = evid_dir / f"_tmp_pip_{fidx}.png"
                tmp_src = evid_dir / f"_tmp_src_{fidx}.png"
                ok1 = _extract_png(mp4, fidx, tmp_full, "scale=360:-1")
                ok2 = _extract_png(mp4, fidx, tmp_pip, f"crop={full_box[2]}:{full_box[3]}:{full_box[0]}:{full_box[1]},scale=240:320:force_original_aspect_ratio=decrease,pad=240:320:(ow-iw)/2:(oh-ih)/2")
                ok3 = _extract_png(src, min(fidx, max(0, n_src - 1)), tmp_src, f"crop={src_full_box[2]}:{src_full_box[3]}:{src_full_box[0]}:{src_full_box[1]},scale=240:320:force_original_aspect_ratio=decrease,pad=240:320:(ow-iw)/2:(oh-ih)/2")
                if ok1 and ok2 and ok3:
                    with Image.open(tmp_full) as im_f, Image.open(tmp_pip) as im_p, Image.open(tmp_src) as im_s:
                        im_f = im_f.convert("RGB")
                        im_p = im_p.convert("RGB")
                        im_s = im_s.convert("RGB")
                        if fidx in (0, n_ren // 2, n_ren - 1):
                            mobile_thumbs.append((fidx, im_f.copy()))
                        cw_all = im_f.width + im_p.width + im_s.width + 40
                        ch_all = max(im_f.height, 360) + 42
                        sheet = Image.new("RGB", (cw_all, ch_all), (18, 16, 15))
                        dr = ImageDraw.Draw(sheet)
                        row_i = rows[fidx]
                        dr.text((12, 10), f"f{fidx:04d} ({fidx/fps:.2f}s) | corr={row_i['crop_corr']} mae={row_i['crop_mae_norm']} | {row_i['status']}", fill=(245, 220, 120))
                        sheet.paste(im_f, (10, 36))
                        sheet.paste(im_p, (im_f.width + 20, 36))
                        sheet.paste(im_s, (im_f.width + im_p.width + 30, 36))
                        dr.text((im_f.width + 24, 362), "Rendered PIP", fill=(180, 240, 200))
                        dr.text((im_f.width + im_p.width + 34, 362), "Derived Source Crop", fill=(180, 220, 255))
                        out_cmp = evid_dir / f"pip_compare_f{fidx:04d}.png"
                        sheet.save(out_cmp)
                        evidence_files.append(str(out_cmp))
                for t in (tmp_full, tmp_pip, tmp_src):
                    t.unlink(missing_ok=True)

            if mobile_thumbs:
                mw = sum(im.width for _, im in mobile_thumbs) + 16 * (len(mobile_thumbs) + 1)
                mh = max(im.height for _, im in mobile_thumbs) + 48
                m_img = Image.new("RGB", (mw, mh), (15, 14, 13))
                mdr = ImageDraw.Draw(m_img)
                mdr.text((14, 10), f"360px Mobile Readability & Fixed-PIP Preview (Start / Mid / EOF) — {mp4.name}", fill=(245, 240, 230))
                x_cur = 16
                for fidx, im in mobile_thumbs:
                    m_img.paste(im, (x_cur, 38))
                    mdr.rectangle([x_cur, 38, x_cur + 96, 58], fill=(0, 0, 0))
                    mdr.text((x_cur + 6, 41), f"f{fidx:04d} ({fidx/fps:.2f}s)", fill=(255, 220, 110))
                    x_cur += im.width + 16
                mob_out = evid_dir / "mobile_360px_preview.png"
                m_img.save(mob_out)
                evidence_files.append(str(mob_out))
        except ImportError:
            pass

    summary = {
        "schema_version": "kh02r2.presenter_frame_qa.v1",
        "pass": len(issues) == 0,
        "status": "PASS" if not issues else "FAIL",
        "mp4_sha256": sha256_file(mp4),
        "derived_source_sha256": sha256_file(src),
        "presenter_mode": pres_mode,
        "visibility_policy": pres_vis,
        "expected_frames": expected_frames,
        "decoded_frames": n_ren,
        "visible_matched_frames": visible_count,
        "pip_presence_coverage_ratio": coverage,
        "frame_0_ok": frame_0_ok,
        "eof_frame_ok": eof_ok,
        "occluded_frame_count": len(occluded_frames),
        "occluded_frames_sample": occluded_frames[:12],
        "frozen_mismatch_count": len(frozen_mismatch_frames),
        "frozen_mismatch_sample": frozen_mismatch_frames[:12],
        "mean_crop_corr": round(sum(corrs) / max(1, len(corrs)), 4),
        "min_crop_corr": round(min(corrs), 4) if corrs else 0.0,
        "mean_crop_mae": round(sum(maes) / max(1, len(maes)), 4),
        "pip_box_norm": [nx, ny, nw, nh],
        "face_tracking_status": (pres_lay.get("crop") or {}).get("tracking_status", "FALLBACK_STATIC_VERIFIED"),
        "face_visibility_note": "Per-frame pixel matching verifies PIP presence/non-occlusion/motion against derived source crop; human visual face/expression signoff remains OWNER_VISUAL=PENDING.",
        "csv_path": str(csv_path),
        "evidence_files": evidence_files,
        "issues": issues,
    }
    json_path = odir / "PRESENTER_FRAME_QA.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary["json_path"] = str(json_path)
    return summary


def build_effect_application_ledger(
    shotbook: dict[str, Any],
    compile_map: dict[str, Any],
    *,
    registry: dict[str, Any] | None = None,
    rendered_mp4_sha: str | None = None,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    """Emit EFFECT_APPLICATION_LEDGER.json distinguishing eligible vs selected vs compiled vs rendered effects."""
    compiled_by_id = {e["event_id"]: e for e in compile_map.get("compiled_events", [])}
    by_cap: dict[str, list[str]] = {}
    for ev in shotbook.get("visual_events", []):
        by_cap.setdefault(ev["capability_id"], []).append(ev["event_id"])

    caps = (registry or {}).get("capabilities", [])
    rows: list[dict[str, Any]] = []
    seen_caps: set[str] = set()
    for c in caps:
        cid = c["capability_id"]
        seen_caps.add(cid)
        ev_ids = by_cap.get(cid, [])
        compiled_ids = [eid for eid in ev_ids if eid in compiled_by_id]
        rows.append({
            "capability_id": cid,
            "availability": c.get("availability", "VERIFIED"),
            "eligible": c.get("availability") != "UNAVAILABLE",
            "selected_event_ids": ev_ids,
            "compiled_event_ids": compiled_ids,
            "rendered_verified": bool(compiled_ids and rendered_mp4_sha),
            "not_used_reason": None if ev_ids else "Not required by semantic story beats for this script.",
        })
    for cid, ev_ids in sorted(by_cap.items()):
        if cid not in seen_caps:
            compiled_ids = [eid for eid in ev_ids if eid in compiled_by_id]
            rows.append({
                "capability_id": cid, "availability": "VERIFIED", "eligible": True,
                "selected_event_ids": ev_ids, "compiled_event_ids": compiled_ids,
                "rendered_verified": bool(compiled_ids and rendered_mp4_sha), "not_used_reason": None,
            })

    doc = {
        "schema_version": "kh02r2.effect_application_ledger.v1",
        "plan_sha256": compile_map.get("plan_sha256"),
        "rendered_mp4_sha256": rendered_mp4_sha,
        "selected_capabilities_count": sum(1 for r in rows if r["selected_event_ids"]),
        "compiled_events_count": len(compiled_by_id),
        "capabilities": rows,
    }
    if out_path is not None:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return doc


def audit_transition_seams(
    shotbook: dict[str, Any],
    mp4_path: str | Path,
    *,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    """Inspect decoded frames around every beat transition boundary for accidental black/empty flashes."""
    mp4 = Path(mp4_path).resolve()
    media = probe_source(mp4)["media"]
    w, h = int(media["width"]), int(media["height"])
    patches = _stream_cropped_rgb_frames(mp4, (0, 0, w, h), grid=32)
    fps = shotbook["output_spec"]["fps_num"] / shotbook["output_spec"]["fps_den"]
    seams: list[dict[str, Any]] = []
    issues: list[str] = []

    for tr in shotbook.get("transitions", []):
        bf = int(tr["boundary_frame"])
        window_luma = []
        for f in range(max(0, bf - 2), min(len(patches), bf + 3)):
            st = _rgb_stats_and_match(patches[f], None)
            window_luma.append({"frame": f, "luma_mean": st["luma_mean"], "luma_std": st["luma_std"]})
        flash_black = any(x["luma_mean"] < 6.0 and x["luma_std"] < 4.0 for x in window_luma)
        if flash_black:
            issues.append(f"Accidental black/empty flash detected around transition {tr['transition_id']} (f{bf})")
        seams.append({
            "transition_id": tr["transition_id"],
            "from_beat": tr["from_beat"],
            "to_beat": tr["to_beat"],
            "boundary_frame": bf,
            "boundary_sec": round(bf / fps, 4),
            "technique": tr["technique"],
            "reason": tr["reason"],
            "black_flash_detected": flash_black,
            "window_samples": window_luma,
        })

    doc = {
        "schema_version": "kh02r2.transition_seam_report.v1",
        "pass": len(issues) == 0,
        "mp4_sha256": sha256_file(mp4),
        "seams": seams,
        "issues": issues,
    }
    if out_path is not None:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return doc


def audit_visual_holds(
    shotbook: dict[str, Any],
    mp4_path: str | Path,
    *,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    """Measure decoded visual change across every beat, distinguishing intentional holds from stagnant holds."""
    mp4 = Path(mp4_path).resolve()
    media = probe_source(mp4)["media"]
    w, h = int(media["width"]), int(media["height"])
    patches = _stream_cropped_rgb_frames(mp4, (0, 0, w, h), grid=32)
    fps = shotbook["output_spec"]["fps_num"] / shotbook["output_spec"]["fps_den"]
    beats_out: list[dict[str, Any]] = []
    max_low_change_sec = 0.0

    for b in shotbook.get("beats", []):
        sf = max(0, int(b["start_frame"]))
        ef = min(len(patches), int(b["end_frame"]))
        deltas = [_patch_motion(patches[i - 1], patches[i]) for i in range(max(1, sf + 1), ef)]
        avg_d = round(sum(deltas) / max(1, len(deltas)), 5)
        max_d = round(max(deltas), 5) if deltas else 0.0
        # Longest consecutive low-change run (< 0.002)
        run_cur = 0
        run_max = 0
        for d in deltas:
            if d < 0.002:
                run_cur += 1
                run_max = max(run_max, run_cur)
            else:
                run_cur = 0
        low_sec = round(run_max / fps, 3)
        max_low_change_sec = max(max_low_change_sec, low_sec)
        beats_out.append({
            "beat_id": b["beat_id"],
            "semantic_function": b["semantic_function"],
            "duration_sec": round((ef - sf) / fps, 3),
            "intentional_hold": bool(b.get("intentional_hold")),
            "mean_frame_delta": avg_d,
            "peak_frame_delta": max_d,
            "longest_low_change_sec": low_sec,
        })

    doc = {
        "schema_version": "kh02r2.visual_hold_audit.v1",
        "mp4_sha256": sha256_file(mp4),
        "longest_low_change_sec": max_low_change_sec,
        "beats": beats_out,
        "note": "Low pixel delta is diagnostic only and does not automatically indicate an error when intentional_hold=true or reading hold is needed.",
    }
    if out_path is not None:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return doc


def run_h1_geom_check(
    build_dir: str | Path,
    *,
    seecut_root: Path | None = None,
    tools_root: Path | None = None,
    out_log: str | Path | None = None,
    timeout_sec: int = 120,
) -> dict[str, Any]:
    """Execute unmodified pinned upstream geom-check.mjs (H1) and parse exact FAIL/WARN counts."""
    bdir = Path(build_dir).resolve()
    up_root = seecut_root or Path(
        os.environ.get("SEECUT_UPSTREAM_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut"))
    )
    geom_mjs = up_root / "skill/seecut/scripts/geom-check.mjs"
    if not geom_mjs.is_file():
        return {"pass": False, "status": "BLOCKED_ENV", "exit_code": 2, "error": f"Missing pinned geom-check.mjs: {geom_mjs}"}

    troot = tools_root or (Path(os.environ.get("SEECUT_STANDALONE_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone"))) / "tools")
    nm = bdir / "node_modules"
    if not nm.exists() and (troot / "node_modules").is_dir():
        nm.symlink_to(troot / "node_modules")

    proc = subprocess.run(
        ["node", str(geom_mjs), str(bdir)],
        capture_output=True, text=True, timeout=timeout_sec,
    )
    combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if out_log is not None:
        lp = Path(out_log)
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text(combined, encoding="utf-8")

    fail_lines = [ln.strip() for ln in combined.splitlines() if ln.strip().startswith("✗")]
    warn_lines = [ln.strip() for ln in combined.splitlines() if ln.strip().startswith("⚠")]
    passed = (proc.returncode == 0 and len(fail_lines) == 0)
    return {
        "pass": passed,
        "status": "PASS" if passed else ("BLOCKED_ENV" if proc.returncode >= 2 else "FAIL"),
        "exit_code": proc.returncode,
        "fail_count": len(fail_lines),
        "warn_count": len(warn_lines),
        "fails": fail_lines,
        "warns": warn_lines,
        "command": f"node {geom_mjs} {bdir}",
        "log_path": str(out_log) if out_log else None,
        "stdout_tail": combined.splitlines()[-20:],
    }


def export_and_verify_jianying_layers(
    build_dir: str | Path,
    jianying_dir: str | Path,
    *,
    project_name: str,
    shotbook: dict[str, Any] | None = None,
    seecut_root: Path | None = None,
    tools_root: Path | None = None,
) -> dict[str, Any]:
    """Run pinned upstream jianying_layers.mjs + jianying_plan.py with honest fidelity audit (no hidden flattening claims)."""
    bdir = Path(build_dir).resolve()
    jdir = Path(jianying_dir).resolve()
    layers_dir = jdir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    up_root = seecut_root or Path(
        os.environ.get("SEECUT_UPSTREAM_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut"))
    )
    layers_mjs = up_root / "skill/seecut/scripts/jianying_layers.mjs"
    plan_py = up_root / "skill/seecut/scripts/jianying_plan.py"
    if not layers_mjs.is_file() or not plan_py.is_file():
        return {"pass": False, "status": "BLOCKED_ENV", "error": "Pinned upstream JianYing scripts missing"}

    troot = tools_root or (Path(os.environ.get("SEECUT_STANDALONE_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone"))) / "tools")
    nm = bdir / "node_modules"
    if not nm.exists() and (troot / "node_modules").is_dir():
        nm.symlink_to(troot / "node_modules")

    p1 = subprocess.run(
        ["node", str(layers_mjs), str(bdir), str(layers_dir)],
        capture_output=True, text=True, timeout=180,
    )
    manifest_p = layers_dir / "manifest.json"
    if p1.returncode != 0 or not manifest_p.is_file():
        return {"pass": False, "status": "FAIL_LAYERS", "exit_code": p1.returncode, "stderr": (p1.stderr or p1.stdout)[-400:]}

    plan_out = jdir / "jianying_plan.json"
    p2 = subprocess.run(
        ["python3", str(plan_py), str(manifest_p), "--name", project_name, "--out", str(plan_out)],
        capture_output=True, text=True, timeout=60,
    )
    if p2.returncode != 0 or not plan_out.is_file():
        return {"pass": False, "status": "FAIL_PLAN", "exit_code": p2.returncode, "stderr": (p2.stderr or p2.stdout)[-400:]}

    # Re-open and audit manifest + PNG files + person_states + fidelity limitations honestly
    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    plan_data = json.loads(plan_out.read_text(encoding="utf-8"))
    layer_items = manifest.get("segs") or manifest.get("layers") or manifest.get("items") or []
    person_states = manifest.get("person_states") or []
    png_files = sorted(layers_dir.rglob("*.png"))
    missing_pngs = []
    for it in layer_items:
        fn = it.get("file") or it.get("png")
        if fn:
            fp = Path(fn) if Path(fn).is_absolute() else (layers_dir / fn)
            if not fp.is_file():
                missing_pngs.append(str(fn))

    pres_lay = (shotbook or {}).get("presenter_layout")
    pres_box_matched = True
    if pres_lay and person_states:
        want_box = list((pres_lay.get("region") or {}).get("pixel_box") or [])
        got_box = list(person_states[0].get("box") or [])
        if len(want_box) == 4 and len(got_box) == 4:
            pres_box_matched = all(abs(int(a) - int(b)) <= 3 for a, b in zip(want_box, got_box))
    fidelity_limitations = [
        "Upstream jianying_layers.mjs rasterizes complex CSS/GSAP motion effects (clip-path wipes, SVG dash draw, brush sweeps, light sheen) to static PNG snapshots + transform keyframes.",
        "JianYing/CapCut profile injection is intentionally disabled (no automatic modification of external editor profiles).",
    ]
    fidelity_report = {
        "schema_version": "kh02r2.layer_fidelity_report.v1",
        "pass": (len(missing_pngs) == 0 and len(png_files) > 0 and pres_box_matched),
        "fidelity_status": "LAYER_FIDELITY_PARTIAL",
        "manifest_path": str(manifest_p),
        "manifest_sha256": sha256_file(manifest_p),
        "plan_path": str(plan_out),
        "plan_sha256": sha256_file(plan_out),
        "exported_png_count": len(png_files),
        "manifest_layer_count": len(layer_items),
        "person_state_count": len(person_states),
        "presenter_box_matched": pres_box_matched,
        "missing_pngs": missing_pngs,
        "presenter_layout_preserved_in_metadata": pres_lay is not None,
        "plan_track_count": len(plan_data.get("tracks", [])) if isinstance(plan_data, dict) else 0,
        "limitations": fidelity_limitations,
    }
    fid_path = jdir / "LAYER_FIDELITY_REPORT.json"
    fid_path.write_text(json.dumps(fidelity_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fidelity_report["report_path"] = str(fid_path)
    return fidelity_report
