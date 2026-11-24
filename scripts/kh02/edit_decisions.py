"""KH-02 Edit Decisions & Rational Time-Map (Audio-First Cuts + LOCK_VOICE Default)."""

from __future__ import annotations

import math
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from .contracts import is_strict_int, seconds_to_frame


def propose_cut_candidates(
    transcript: dict[str, Any],
    *,
    silences: list[dict[str, float]] | None = None,
    voice_policy: str = "LOCK_VOICE",
    min_cut_candidate_sec: float = 0.40,
    caution_min_sec: float = 0.15,
    pad_sec: float = 0.08,
    intentional_windows: list[tuple[float, float]] | None = None,
) -> list[dict[str, Any]]:
    """Identify candidate silence/retake cuts without ever cutting inside a spoken word.

    Under LOCK_VOICE (default), all candidates are marked KEEP/CAUTION_KEEP with explicit reasons.
    """
    words = transcript.get("words", [])
    phrases = transcript.get("phrases", [])
    src_id = transcript["source_asset_id"]
    candidates: list[dict[str, Any]] = []
    intent_wins = intentional_windows or []

    def is_intentional(a: float, b: float) -> bool:
        for ia, ib in intent_wins:
            if min(b, ib) - max(a, ia) > 0.15:
                return True
        return False

    # 1. Analyze inter-word / inter-phrase pauses
    for idx in range(len(words) - 1):
        w_cur = words[idx]
        w_nxt = words[idx + 1]
        gap_s = float(w_cur["end_sec"])
        gap_e = float(w_nxt["start_sec"])
        gap_dur = gap_e - gap_s
        if gap_dur < caution_min_sec:
            continue
        # Adaptive safe padding between 30ms and 200ms
        eff_pad = min(0.20, max(0.03, min(pad_sec, gap_dur * 0.25)))
        trim_s = round(gap_s + eff_pad, 4)
        trim_e = round(gap_e - eff_pad, 4)
        if trim_e <= trim_s + 0.05:
            continue
        if is_intentional(gap_s, gap_e):
            kind = "intentional_showcase"
            decision = "KEEP"
            reason = "Intentional visual showcase / dramatic pause preserved."
        elif gap_dur >= min_cut_candidate_sec:
            kind = "silence_long"
            decision = "CUT_APPROVED" if voice_policy == "TRIM_APPROVED" else "KEEP"
            reason = (
                f"Pause {gap_dur:.2f}s >= {min_cut_candidate_sec:.2f}s; "
                + ("approved trim with safe padding" if decision == "CUT_APPROVED" else "preserved under LOCK_VOICE")
            )
        else:
            kind = "silence_caution"
            decision = "CAUTION_KEEP"
            reason = f"Short natural breath/pause ({gap_dur:.2f}s in 150-400ms caution band); kept by default."
        candidates.append({
            "candidate_id": f"cand_{len(candidates) + 1:03d}",
            "source_asset_id": src_id,
            "start_sec": trim_s,
            "end_sec": trim_e,
            "raw_gap_sec": round(gap_dur, 4),
            "kind": kind,
            "decision": decision,
            "reason": reason,
        })

    # 2. Detect verbatim repeated phrase retake candidates (only flagged as evidence, never blindly cut)
    for idx in range(len(phrases) - 1):
        p1 = phrases[idx]
        p2 = phrases[idx + 1]
        norm1 = " ".join(p1["text"].lower().split())
        norm2 = " ".join(p2["text"].lower().split())
        if len(norm1) >= 8 and (norm1 == norm2 or norm2.startswith(norm1[: max(8, int(len(norm1) * 0.7))])):
            candidates.append({
                "candidate_id": f"cand_{len(candidates) + 1:03d}",
                "source_asset_id": src_id,
                "start_sec": round(float(p1["start_sec"]), 4),
                "end_sec": round(float(p1["end_sec"]), 4),
                "raw_gap_sec": round(float(p1["end_sec"]) - float(p1["start_sec"]), 4),
                "kind": "retake",
                "decision": "CUT_APPROVED" if voice_policy == "TRIM_APPROVED" else "CAUTION_KEEP",
                "reason": f"Repeated opening phrase detected ({p1['phrase_id']} -> {p2['phrase_id']}).",
            })

    candidates.sort(key=lambda c: (c["start_sec"], c["candidate_id"]))
    return candidates


def validate_edl(
    edl: dict[str, Any],
    *,
    words: list[dict[str, Any]] | None = None,
    source_total_frames: dict[str, int] | None = None,
) -> list[dict[str, str]]:
    """Validate EDL segments for frame-clock contiguity, bounds, LOCK_VOICE identity, and zero mid-word cuts."""
    errors: list[dict[str, str]] = []
    fps_num = edl.get("fps_num")
    fps_den = edl.get("fps_den")
    if not is_strict_int(fps_num) or fps_num <= 0 or not is_strict_int(fps_den) or fps_den <= 0:
        return [{"code": "EDL_FPS", "location": "$.fps", "message": f"Invalid rational fps {fps_num}/{fps_den}"}]
    fps = Fraction(fps_num, fps_den)
    segs = edl.get("segments") or []
    if not segs:
        return [{"code": "EDL_EMPTY", "location": "$.segments", "message": "EDL has zero segments"}]

    voice_policy = edl.get("voice_policy", "LOCK_VOICE")
    expected_out = 0
    for idx, seg in enumerate(segs):
        loc = f"$.segments[{idx}]"
        s0, s1 = seg["src_start_frame"], seg["src_end_frame"]
        o0, o1 = seg["out_start_frame"], seg["out_end_frame"]
        if not all(is_strict_int(x) for x in (s0, s1, o0, o1)):
            errors.append({"code": "EDL_NONINT", "location": loc, "message": "Frame bounds must be strict ints"})
            continue
        if s0 < 0 or s1 <= s0 or o0 < 0 or o1 <= o0:
            errors.append({"code": "EDL_RANGE", "location": loc, "message": f"Invalid segment range src=[{s0},{s1}) out=[{o0},{o1})"})
            continue
        if (s1 - s0) != (o1 - o0):
            errors.append({"code": "EDL_SPEED_MISMATCH", "location": loc, "message": f"Unapproved time-stretch: src len {s1-s0} != out len {o1-o0}"})
        if o0 != expected_out:
            errors.append({"code": "EDL_OUT_GAP", "location": loc, "message": f"Output segment starts at {o0}, expected contiguous {expected_out}"})
        expected_out = o1

        if source_total_frames and seg["source_asset_id"] in source_total_frames:
            max_f = source_total_frames[seg["source_asset_id"]]
            if s1 > max_f:
                errors.append({"code": "EDL_SRC_OOB", "location": loc, "message": f"src_end_frame {s1} > source frames {max_f}"})

        if voice_policy == "LOCK_VOICE" and (s0 != o0 or s1 != o1):
            errors.append({"code": "LOCK_VOICE_VIOLATION", "location": loc, "message": "LOCK_VOICE requires identity src==out mapping"})

        # Check mid-word cuts at segment boundaries (only when segment boundary is interior)
        if words:
            b_start_sec = float(Fraction(s0) / fps)
            b_end_sec = float(Fraction(s1) / fps)
            for w in words:
                ws, we = float(w["start_sec"]), float(w["end_sec"])
                # Cut strictly inside [ws + 0.02, we - 0.02]
                if s0 > 0 and ws + 0.02 < b_start_sec < we - 0.02:
                    errors.append({
                        "code": "EDL_MID_WORD_CUT",
                        "location": loc,
                        "message": f"src_start_frame {s0} ({b_start_sec:.3f}s) cuts inside word {w['word_id']} ({w['text']!r} [{ws:.3f},{we:.3f}])",
                    })
                if source_total_frames and s1 < source_total_frames.get(seg["source_asset_id"], s1) and ws + 0.02 < b_end_sec < we - 0.02:
                    errors.append({
                        "code": "EDL_MID_WORD_CUT",
                        "location": loc,
                        "message": f"src_end_frame {s1} ({b_end_sec:.3f}s) cuts inside word {w['word_id']} ({w['text']!r} [{ws:.3f},{we:.3f}])",
                    })

    if edl.get("total_output_frames") != expected_out:
        errors.append({
            "code": "EDL_TOTAL_MISMATCH",
            "location": "$.total_output_frames",
            "message": f"total_output_frames={edl.get('total_output_frames')} != summed {expected_out}",
        })
    return errors


def build_time_map(
    source_asset_id: str,
    total_source_frames: int,
    fps_num: int,
    fps_den: int = 1,
    *,
    audio_sample_rate: int = 48000,
    voice_policy: str = "LOCK_VOICE",
    approved_cuts: list[dict[str, Any]] | None = None,
    cut_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build exact rational piecewise frame map. Under LOCK_VOICE returns identity map."""
    if total_source_frames <= 0:
        raise ValueError(f"total_source_frames must be positive (got {total_source_frames})")

    cuts_to_apply: list[tuple[int, int, str]] = []
    if voice_policy == "TRIM_APPROVED" and approved_cuts:
        for c in approved_cuts:
            if c.get("decision") != "CUT_APPROVED":
                continue
            cf0 = max(0, seconds_to_frame(c["start_sec"], fps_num, fps_den, mode="ceil"))
            cf1 = min(total_source_frames, seconds_to_frame(c["end_sec"], fps_num, fps_den, mode="floor"))
            if cf1 > cf0:
                cuts_to_apply.append((cf0, cf1, c.get("reason", "approved_cut")))
        cuts_to_apply.sort()
        # Merge overlapping cuts
        merged: list[tuple[int, int, str]] = []
        for a, b, r in cuts_to_apply:
            if merged and a <= merged[-1][1]:
                prev_a, prev_b, prev_r = merged[-1]
                merged[-1] = (prev_a, max(prev_b, b), prev_r)
            else:
                merged.append((a, b, r))
        cuts_to_apply = merged

    segments: list[dict[str, Any]] = []
    cursor = 0
    out_cursor = 0
    fps_float = fps_num / fps_den

    def add_seg(s0: int, s1: int, reason: str, has_left_cut: bool, has_right_cut: bool) -> None:
        nonlocal out_cursor
        if s1 <= s0:
            return
        dur_ms = ((s1 - s0) / fps_float) * 1000.0
        # 30ms safe fade capped at 25% of clip length so very short clips never double-fade
        safe_fade = round(min(30.0, dur_ms * 0.25), 2)
        fade_in = safe_fade if has_left_cut else 0.0
        fade_out = safe_fade if has_right_cut else 0.0
        length = s1 - s0
        segments.append({
            "segment_id": f"seg_{len(segments) + 1:03d}",
            "source_asset_id": source_asset_id,
            "src_start_frame": s0,
            "src_end_frame": s1,
            "out_start_frame": out_cursor,
            "out_end_frame": out_cursor + length,
            "audio_fade_in_ms": fade_in,
            "audio_fade_out_ms": fade_out,
            "reason": reason,
        })
        out_cursor += length

    if not cuts_to_apply:
        add_seg(0, total_source_frames, f"{voice_policy} continuous identity mapping", False, False)
    else:
        for idx, (c0, c1, _reason) in enumerate(cuts_to_apply):
            if c0 > cursor:
                add_seg(cursor, c0, "Approved keep span before cut", idx > 0, True)
            cursor = max(cursor, c1)
        if cursor < total_source_frames:
            add_seg(cursor, total_source_frames, "Approved keep span after final cut", True, False)

    return {
        "schema_version": "kh02.edl.v1",
        "voice_policy": voice_policy,
        "fps_num": fps_num,
        "fps_den": fps_den,
        "audio_sample_rate": audio_sample_rate,
        "total_output_frames": out_cursor,
        "cut_candidates": cut_candidates or [],
        "segments": segments,
    }


def remap_events(
    events: list[dict[str, Any]],
    time_map: dict[str, Any],
    *,
    start_key: str = "start_frame",
    end_key: str = "end_frame",
) -> list[dict[str, Any]]:
    """Remap source-frame events onto output-frame clock using exact rational piecewise segment offsets."""
    segments = time_map.get("segments", [])
    remapped: list[dict[str, Any]] = []
    for ev in events:
        es = int(ev[start_key])
        ee = int(ev[end_key])
        for seg in segments:
            s0, s1 = seg["src_start_frame"], seg["src_end_frame"]
            ov0 = max(es, s0)
            ov1 = min(ee, s1)
            if ov1 <= ov0:
                continue
            offset = seg["out_start_frame"] - s0
            new_ev = dict(ev)
            new_ev[start_key] = ov0 + offset
            new_ev[end_key] = ov1 + offset
            new_ev["mapped_segment_id"] = seg["segment_id"]
            remapped.append(new_ev)
            break
    remapped.sort(key=lambda x: (x[start_key], x[end_key]))
    return remapped


def apply_approved_cut_map(
    source_path: str | Path,
    time_map: dict[str, Any],
    out_path: str | Path,
) -> dict[str, Any]:
    """Materialize a derived A/V trimmed intermediate ONLY when TRIM_APPROVED; never modifies source."""
    src = Path(source_path).resolve()
    dst = Path(out_path).resolve()
    if src == dst:
        raise ValueError("Refusing to overwrite source media with derived cut output")
    if time_map.get("voice_policy") != "TRIM_APPROVED":
        return {"applied": False, "reason": f"voice_policy={time_map.get('voice_policy')} (identity; no derived trim needed)", "path": str(src)}

    segs = time_map.get("segments", [])
    if not segs:
        raise ValueError("Cannot apply empty EDL")
    fps = Fraction(int(time_map["fps_num"]), int(time_map["fps_den"]))
    dst.parent.mkdir(parents=True, exist_ok=True)

    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    for idx, seg in enumerate(segs):
        t0 = float(Fraction(seg["src_start_frame"]) / fps)
        t1 = float(Fraction(seg["src_end_frame"]) / fps)
        dur = max(0.01, t1 - t0)
        fi = float(seg.get("audio_fade_in_ms", 0.0)) / 1000.0
        fo = float(seg.get("audio_fade_out_ms", 0.0)) / 1000.0
        filter_parts.append(f"[0:v]trim=start={t0:.6f}:end={t1:.6f},setpts=PTS-STARTPTS[v{idx}]")
        af = f"[0:a]atrim=start={t0:.6f}:end={t1:.6f},asetpts=PTS-STARTPTS"
        if fi > 0.001:
            af += f",afade=t=in:st=0:d={min(fi, dur * 0.4):.4f}"
        if fo > 0.001:
            af += f",afade=t=out:st={max(0.0, dur - min(fo, dur * 0.4)):.4f}:d={min(fo, dur * 0.4):.4f}"
        af += f"[a{idx}]"
        filter_parts.append(af)
        concat_inputs.append(f"[v{idx}][a{idx}]")

    filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(segs)}:v=1:a=1[vout][aout]")
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-i", str(src),
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(time_map.get("audio_sample_rate", 48000)),
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"apply_approved_cut_map failed (rc={proc.returncode}): {proc.stderr[-300:]}")
    return {"applied": True, "path": str(dst), "segments": len(segs), "total_output_frames": time_map["total_output_frames"]}


# Re-export KH-02.R1 Intelligent Rough Cut contract symbols from rough_cut.py
from .rough_cut import (  # noqa: E402,F401
    CANDIDATE_TYPES,
    apply_r1_cut_map,
    build_r1_time_map,
    classify_pause,
    detect_take_candidates,
    emit_rough_cut_artifacts,
    evaluate_cut_safety,
    propose_r1_cut_candidates,
    rank_cut_candidates,
    resolve_cut_policy,
    verify_cut_result,
)
