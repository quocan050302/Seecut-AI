"""KH-02 Sound Director: real EBU R128 loudness/peak analysis, cue validation, mix profile & guarded SFX adapter.

Selective loudness JSON parsing helper adapted from:
  browser-use/video-use @ 43cfc566833548093455c9d4a46bd69913b7e552
  helpers/render.py::measure_loudness (MIT License, Copyright (c) 2026 Browser Use).
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .contracts import is_strict_number

SFX_OFFSETS_SEC = {
    "sfx_impact": 0.12,
    "sfx_whoosh": -0.08,
    "sfx_pop": 0.0,
    "sfx_click": 0.0,
    "sfx_ding": 0.0,
    "sfx_hook": 0.0,
}

SFX_RELATIVE_TARGET_DB = {
    "sfx_hook": -12.0,
    "sfx_impact": -12.0,
    "sfx_whoosh": -15.0,
    "sfx_pop": -16.0,
    "sfx_ding": -16.0,
    "sfx_click": -15.0,
}


def measure_final_audio(media_path: str | Path) -> dict[str, Any]:
    """Measure real integrated LUFS, LRA, true peak (dBTP), max volume, and duration via ffmpeg ebur128 + volumedetect."""
    p = Path(media_path)
    if not p.is_file():
        return {"measured": False, "error": f"File not found: {p}"}

    # Check if audio stream exists first
    pr = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,sample_rate,channels,duration",
         "-of", "json", str(p)],
        capture_output=True, text=True, timeout=30,
    )
    if pr.returncode != 0:
        return {"measured": False, "error": f"ffprobe failed rc={pr.returncode}"}
    info = json.loads(pr.stdout or "{}")
    streams = info.get("streams") or []
    if not streams:
        return {"measured": False, "has_audio": False, "error": "No audio stream present"}

    # Real EBU R128 loudness & true peak
    eb = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(p),
         "-af", "ebur128=peak=true", "-vn", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    if eb.returncode != 0:
        return {"measured": False, "has_audio": True, "error": f"ffmpeg ebur128 failed rc={eb.returncode}"}

    # Parse final Summary block from ebur128 stderr
    summary_idx = eb.stderr.rfind("Summary:")
    summary = eb.stderr[summary_idx:] if summary_idx != -1 else eb.stderr
    m_i = re.search(r"I:\s*(-?[\d.]+)\s*LUFS", summary)
    m_lra = re.search(r"LRA:\s*(-?[\d.]+)\s*LU", summary)
    m_tp = re.search(r"Peak:\s*(-?[\d.]+)\s*dBFS", summary)

    # Also capture volumedetect mean/max
    vd = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(p),
         "-af", "volumedetect", "-vn", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    m_mean = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", vd.stderr)
    m_max = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", vd.stderr)

    # Guard against unmeasured / -inf silence tracks
    if not m_i or not m_tp:
        return {
            "measured": False,
            "has_audio": True,
            "error": "Could not parse EBU R128 I/Peak from ffmpeg output",
        }

    integrated_lufs = float(m_i.group(1))
    true_peak_dbtp = float(m_tp.group(1))
    lra_lu = float(m_lra.group(1)) if m_lra else None
    mean_db = float(m_mean.group(1)) if m_mean else None
    max_db = float(m_max.group(1)) if m_max else None

    return {
        "measured": True,
        "has_audio": True,
        "codec": streams[0].get("codec_name"),
        "sample_rate": int(streams[0]["sample_rate"]) if streams[0].get("sample_rate") else None,
        "channels": int(streams[0]["channels"]) if streams[0].get("channels") else None,
        "integrated_lufs": integrated_lufs,
        "lra_lu": lra_lu,
        "true_peak_dbtp": true_peak_dbtp,
        "mean_volume_db": mean_db,
        "max_volume_db": max_db,
        "clipping_detected": true_peak_dbtp > -0.1,
    }


def analyze_audio(
    media_path: str | Path,
    *,
    silences: list[dict[str, float]] | None = None,
    transcript: dict[str, Any] | None = None,
    target_lufs: float = -16.0,
) -> dict[str, Any]:
    """Analyze voice track loudness, peaks, silence classification, and discrepancy vs profile target."""
    metrics = measure_final_audio(media_path)
    classified_gaps: list[dict[str, Any]] = []
    words = (transcript or {}).get("words", [])
    for s in (silences or []):
        st, en, dur = float(s["start_sec"]), float(s["end_sec"]), float(s["duration_sec"])
        # Differentiate lead-in, inter-phrase showcase, and short natural pause
        if st < 0.1 and dur < 1.0:
            kind = "lead_in_roomtone"
        elif dur >= 2.0:
            kind = "visual_only_or_long_pause"
        else:
            kind = "natural_phrase_pause"
        # Check if any words fall inside this silence (accidental dropout signal)
        overlapping = [w["word_id"] for w in words if float(w["end_sec"]) > st + 0.1 and float(w["start_sec"]) < en - 0.1]
        if overlapping:
            kind = "possible_dropout_check_source"
        classified_gaps.append({
            "start_sec": st,
            "end_sec": en,
            "duration_sec": dur,
            "classification": kind,
            "overlapping_word_ids": overlapping,
        })

    discrepancy_lu = None
    recommendation = "No loudness adjustment applied under LOCK_VOICE."
    if metrics.get("measured") and is_strict_number(metrics.get("integrated_lufs")):
        discrepancy_lu = round(metrics["integrated_lufs"] - target_lufs, 2)
        if abs(discrepancy_lu) > 2.0:
            recommendation = (
                f"Measured {metrics['integrated_lufs']:.1f} LUFS differs by {discrepancy_lu:+.1f} LU from {target_lufs:.1f} LUFS "
                "target; preserved untouched under LOCK_VOICE — recommend owner-approved gain normalization if publishing."
            )

    return {
        "metrics": metrics,
        "target_lufs": target_lufs,
        "discrepancy_lu": discrepancy_lu,
        "recommendation": recommendation,
        "classified_gaps": classified_gaps,
    }


def plan_audio_cues(
    *,
    voice_asset_id: str | None,
    total_frames: int,
    fps_num: int,
    fps_den: int = 1,
    audio_sample_rate: int = 48000,
    voice_policy: str = "LOCK_VOICE",
    sfx_policy: str = "OPTIONAL_LICENSED",
    bgm_policy: str = "OFF",
    sfx_ledger: list[dict[str, Any]] | None = None,
    sfx_events: list[dict[str, Any]] | None = None,
    voice_license_ref: str = "OWNER_SUPPLIED_NONCOMMERCIAL",
    voice_peak_db: float | None = None,
) -> dict[str, Any]:
    """Plan voice backbone + optional verified-licensed SFX/BGM cues (never fabricates unlicensed audio)."""
    fps = fps_num / fps_den
    cues: list[dict[str, Any]] = []
    warnings: list[str] = []

    if voice_policy != "SILENT_BY_DESIGN" and voice_asset_id:
        cues.append({
            "cue_id": "aud_voice_backbone",
            "kind": "voice",
            "asset_ref": voice_asset_id,
            "frame_start": 0,
            "frame_end": total_frames,
            "sample_start": 0,
            "anchor_event": None,
            "trim_start_sec": 0.0,
            "gain_db": 0.0,
            "fade_in_ms": 0.0,
            "fade_out_ms": 0.0,
            "ducking_db": 0.0,
            "license_ref": voice_license_ref,
            "role": "primary_voice_backbone",
            "reason": f"{voice_policy} continuous original narration",
            "expected_peak_db": voice_peak_db,
            "optional_flag": False,
        })

    sfx_status = "OFF_BY_BRIEF" if sfx_policy == "OFF" else "SFX_NOT_AVAILABLE"
    bgm_status = "OFF_BY_BRIEF" if bgm_policy == "OFF" else "BGM_NOT_AVAILABLE"

    if sfx_policy != "OFF" and sfx_ledger and sfx_events:
        led_map = {r["asset_id"]: r for r in sfx_ledger if r.get("rights") not in ("UNLICENSED", "ANALYSIS_ONLY", None)}
        for idx, ev in enumerate(sfx_events):
            aid = ev.get("asset_ref")
            rec = led_map.get(aid)
            if not rec:
                warnings.append(f"SFX cue {idx} skipped: asset {aid!r} not found in verified licensed SFX ledger")
                continue
            kind = ev.get("kind", "sfx_pop")
            offset_sec = SFX_OFFSETS_SEC.get(kind, 0.0)
            raw_t = float(ev["event_sec"]) + offset_sec
            if raw_t < 0.0:
                warnings.append(f"Negative onset {raw_t:.3f}s for {kind} clamped to 0.0s")
                raw_t = 0.0
            f_start = min(total_frames - 1, max(0, int(math.ceil(round(raw_t * fps, 6)))))
            dur_sec = min(float(rec.get("duration_sec", 0.5)), 1.5 if kind == "sfx_hook" else 0.8)
            f_end = min(total_frames, max(f_start + 1, f_start + int(round(dur_sec * fps))))
            sample_start = int(round((f_start / fps) * audio_sample_rate))
            rel_gain = float(ev.get("gain_db", SFX_RELATIVE_TARGET_DB.get(kind, -15.0)))
            cues.append({
                "cue_id": f"sfx_{idx + 1:02d}_{kind}",
                "kind": kind,
                "asset_ref": aid,
                "frame_start": f_start,
                "frame_end": f_end,
                "sample_start": sample_start,
                "anchor_event": ev.get("anchor_event"),
                "trim_start_sec": float(rec.get("trim_start_sec", 0.0)),
                "gain_db": rel_gain,
                "fade_in_ms": 10.0,
                "fade_out_ms": 20.0,
                "ducking_db": 0.0,
                "license_ref": str(rec["rights"]),
                "role": f"accent_{kind}",
                "reason": ev.get("reason", f"Synced to {ev.get('anchor_event')}"),
                "expected_peak_db": (voice_peak_db + rel_gain) if is_strict_number(voice_peak_db) else None,
                "optional_flag": sfx_policy == "OPTIONAL_LICENSED",
            })
        if any(c["kind"].startswith("sfx_") for c in cues):
            sfx_status = "PLANNED_LICENSED"
    elif sfx_policy == "REQUIRED_LICENSED":
        sfx_status = "BLOCKED_UNLICENSED"
        warnings.append("REQUIRED_LICENSED SFX policy cannot PASS without verified licensed SFX assets.")

    if bgm_policy == "REQUIRED_LICENSED":
        bgm_status = "BLOCKED_UNLICENSED"
        warnings.append("REQUIRED_LICENSED BGM policy cannot PASS without verified licensed BGM assets.")

    return {
        "schema_version": "kh02.sfx_cues.v1",
        "voice_policy": voice_policy,
        "sfx_status": sfx_status,
        "bgm_status": bgm_status,
        "voice_peak_db": voice_peak_db,
        "voice_lufs": None,
        "cues": cues,
        "warnings": warnings,
    }


def validate_cues(
    cues: list[dict[str, Any]],
    *,
    total_frames: int,
    fps: float = 24.0,
    asset_paths: dict[str, Path] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate audio cues for bounds, finite gain, negative onset, rights, asset presence, and density warnings."""
    errors: list[dict[str, Any]] = []
    warnings: list[str] = []
    sfx_times: list[float] = []

    for c in cues:
        cid = c["cue_id"]
        fs, fe = c["frame_start"], c["frame_end"]
        if fs < 0 or fe <= fs or fe > total_frames:
            errors.append({"severity": "P0", "cue_id": cid, "code": "AUDIO_OUT_OF_BOUNDS", "message": f"[{fs},{fe}) outside [0,{total_frames})"})
        gain = c.get("gain_db", 0.0)
        if not is_strict_number(gain) or gain < -60.0 or gain > 12.0:
            errors.append({"severity": "P0", "cue_id": cid, "code": "AUDIO_INVALID_GAIN", "message": f"Non-finite or out-of-range gain_db={gain!r}"})
        if c.get("license_ref") in ("UNLICENSED", "ANALYSIS_ONLY", "", None):
            errors.append({"severity": "P0", "cue_id": cid, "code": "AUDIO_UNLICENSED", "message": f"Invalid license_ref={c.get('license_ref')!r}"})
        if asset_paths is not None and c.get("asset_ref"):
            ap = asset_paths.get(c["asset_ref"])
            if not ap or not ap.is_file() or ap.stat().st_size == 0:
                errors.append({"severity": "P0", "cue_id": cid, "code": "AUDIO_MISSING_OR_EMPTY_FILE", "message": f"Asset {c['asset_ref']!r} missing or empty"})
        if c["kind"].startswith("sfx_") and c["kind"] != "sfx_hook":
            sfx_times.append(fs / fps)

    sfx_times.sort()
    for t in sfx_times:
        win = [x for x in sfx_times if t <= x < t + 3.0]
        if len(win) > 2:
            warnings.append(f"SFX density warning: {len(win)} events in [{t:.2f}s, {t+3.0:.2f}s] (>2 per 3s guideline)")
            break
    dur_sec = max(1.0, total_frames / fps)
    if sfx_times and (len(sfx_times) / dur_sec) > 0.7:
        warnings.append(f"SFX average density {len(sfx_times)/dur_sec:.2f}/s exceeds 0.7/s guideline")
    return errors, warnings


def resolve_mix_profile(
    *,
    profile: str = "editorial_pro",
    voice_policy: str = "LOCK_VOICE",
    sfx_status: str = "SFX_NOT_AVAILABLE",
    bgm_status: str = "OFF_BY_BRIEF",
) -> dict[str, Any]:
    """Resolve authoritative mix instructions honoring LOCK_VOICE and non-blocking optional SFX."""
    targets = {
        "seecut_core": {"target_lufs": -16.0, "tolerance_lu": 2.0, "max_true_peak_dbtp": -1.0},
        "editorial_pro": {"target_lufs": -16.0, "tolerance_lu": 2.0, "max_true_peak_dbtp": -1.0},
        "minimal_documentary": {"target_lufs": -18.0, "tolerance_lu": 2.0, "max_true_peak_dbtp": -1.5},
        "silent_visual": {"target_lufs": None, "tolerance_lu": None, "max_true_peak_dbtp": None},
    }
    t = targets.get(profile, targets["editorial_pro"])
    blocked = (sfx_status == "BLOCKED_UNLICENSED") or (bgm_status == "BLOCKED_UNLICENSED")
    return {
        "profile": profile,
        "voice_policy": voice_policy,
        "copy_original_audio_untouched": (voice_policy == "LOCK_VOICE" and sfx_status != "PLANNED_LICENSED" and bgm_status != "PLANNED_LICENSED"),
        "sfx_status": sfx_status,
        "bgm_status": bgm_status,
        "core_blocked": blocked,
        **t,
    }
