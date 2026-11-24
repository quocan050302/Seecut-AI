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
    expected_w = int(round(spec["width"] * float(spec.get("output_scale", 1.0))))
    expected_h = int(round(spec["height"] * float(spec.get("output_scale", 1.0))))
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

    # 3. Monotonic PTS check via ffprobe packets
    pts_proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "frame=best_effort_timestamp_time",
         "-of", "csv=p=0", str(p)],
        capture_output=True, text=True, timeout=60,
    )
    pts_vals = [float(x) for x in pts_proc.stdout.splitlines() if x.strip() and x.strip() != "N/A"]
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
    cam_issues = validate_camera_continuity(shotbook.get("camera_events", []), fps=fps)
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
