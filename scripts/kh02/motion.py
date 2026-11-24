"""KH-02 Motion Direction: grammar (entry/hold/exit), adaptive easing, deterministic pose evaluation & anomaly detection."""

from __future__ import annotations

import math
from typing import Any

PROFILE_EASING = {
    "seecut_core": {
        "card": "back.out(2.1)",
        "badge": "back.out(2.2)",
        "callout": "power2.out",
        "camera": "power2.inOut",
        "evidence": "power2.inOut",
        "allow_spring": True,
    },
    "editorial_pro": {
        "card": "back.out(2.1)",
        "badge": "back.out(2.2)",
        "callout": "power2.out",
        "camera": "power2.inOut",
        "evidence": "power2.inOut",
        "allow_spring": True,
    },
    "minimal_documentary": {
        "card": "power2.out",
        "badge": "power2.out",
        "callout": "power2.out",
        "camera": "power2.inOut",
        "evidence": "power2.inOut",
        "allow_spring": False,
    },
    "silent_visual": {
        "card": "power2.inOut",
        "badge": "power2.out",
        "callout": "power2.out",
        "camera": "power2.inOut",
        "evidence": "power2.inOut",
        "allow_spring": False,
    },
}


def _ease_power2_out(u: float) -> float:
    return 1.0 - (1.0 - u) ** 2


def _ease_power2_inout(u: float) -> float:
    return 2 * u * u if u < 0.5 else 1.0 - ((-2 * u + 2) ** 2) / 2.0


def _ease_back_out(u: float, overshoot: float = 2.1) -> float:
    c1 = overshoot
    c3 = c1 + 1.0
    return 1.0 + c3 * ((u - 1.0) ** 3) + c1 * ((u - 1.0) ** 2)


def _eval_curve(easing: str, u: float) -> float:
    u = min(1.0, max(0.0, u))
    if easing.startswith("back.out"):
        ov = 2.1
        if "(" in easing and ")" in easing:
            try:
                ov = float(easing.split("(", 1)[1].split(")", 1)[0])
            except ValueError:
                ov = 2.1
        return _ease_back_out(u, ov)
    if "inOut" in easing or "inout" in easing:
        return _ease_power2_inout(u)
    if "out" in easing:
        return _ease_power2_out(u)
    return u


def plan_motion(
    events: list[dict[str, Any]],
    *,
    fps: float = 24.0,
    profile: str = "editorial_pro",
    stagger_ms: int = 95,
) -> list[dict[str, Any]]:
    """Assign entry/hold/exit frame counts, easing, and optional semantic stagger per beat."""
    cfg = PROFILE_EASING.get(profile, PROFILE_EASING["editorial_pro"])
    planned: list[dict[str, Any]] = []
    beat_stagger_counter: dict[str, int] = {}

    for ev in events:
        out = dict(ev)
        total = max(1, int(ev["end_frame"]) - int(ev["start_frame"]))
        cat = ev.get("layer_cat", "card")
        cap = ev.get("capability_id", "")

        if "spring" in cap or "badge" in cap:
            role_key = "badge" if "badge" in cap else "card"
        elif cat == "evbg":
            role_key = "evidence"
        elif cat == "hand":
            role_key = "callout"
        else:
            role_key = "card"

        easing = ev.get("easing") or cfg[role_key]
        if not cfg["allow_spring"] and easing.startswith("back.out"):
            easing = "power2.out"

        entry = min(max(3, int(round(0.28 * fps))), max(1, total // 3))
        exit_f = min(max(2, int(round(0.18 * fps))), max(1, total // 4))
        hold = max(0, total - entry - exit_f)

        # Apply semantic stagger only when capability is hf.stagger_cascade
        stagger_frames = 0
        if cap == "hf.stagger_cascade":
            bid = ev["beat_id"]
            idx = beat_stagger_counter.get(bid, 0)
            beat_stagger_counter[bid] = idx + 1
            stagger_frames = int(round((idx * stagger_ms / 1000.0) * fps))

        out["easing"] = easing
        out["entry_frames"] = int(ev.get("entry_frames") if ev.get("entry_frames") is not None else entry)
        out["exit_frames"] = int(ev.get("exit_frames") if ev.get("exit_frames") is not None else exit_f)
        out["hold_frames"] = max(0, total - out["entry_frames"] - out["exit_frames"])
        out["stagger_offset_frames"] = stagger_frames
        planned.append(out)
    return planned


def evaluate_pose(event: dict[str, Any], frame: int) -> dict[str, float]:
    """Deterministically evaluate opacity, scale, and y_offset at integer frame index."""
    s = int(event["start_frame"])
    e = int(event["end_frame"])
    if frame < s or frame >= e:
        return {"visible": 0.0, "opacity": 0.0, "scale": 1.0, "y_offset_px": 0.0}

    entry = max(1, int(event.get("entry_frames", 6)))
    exit_f = max(0, int(event.get("exit_frames", 4)))
    tr = event.get("transform") or {}
    sc_from = float(tr.get("scale_from", 0.94))
    sc_to = float(tr.get("scale_to", 1.0))
    y_from = float(tr.get("y_offset_px", 14.0))
    target_op = float(event.get("opacity", 1.0))
    easing = str(event.get("easing", "power2.out"))

    rel = frame - s
    rem = (e - 1) - frame
    if rel < entry:
        u = (rel + 1) / float(entry)
        k = _eval_curve(easing, u)
        op = target_op * min(1.0, u * 1.35)
        sc = sc_from + (sc_to - sc_from) * k
        y = y_from * (1.0 - min(1.0, _ease_power2_out(u)))
    elif exit_f > 0 and rem < exit_f:
        u = (rem + 1) / float(exit_f)
        op = target_op * max(0.0, u)
        sc = sc_to
        y = 0.0
    else:
        op = target_op
        sc = sc_to
        y = 0.0

    return {
        "visible": 1.0 if op > 0.05 else 0.0,
        "opacity": round(op, 4),
        "scale": round(sc, 4),
        "y_offset_px": round(y, 2),
    }


def validate_motion_curves(
    events: list[dict[str, Any]],
    *,
    fps: float = 24.0,
    profile: str = "editorial_pro",
) -> list[dict[str, Any]]:
    """Detect unsupported spring on serious profile, zero settle hold, or unplanned one-frame jumps."""
    cfg = PROFILE_EASING.get(profile, PROFILE_EASING["editorial_pro"])
    issues: list[dict[str, Any]] = []
    for ev in events:
        eid = ev["event_id"]
        s, e = int(ev["start_frame"]), int(ev["end_frame"])
        easing = str(ev.get("easing", ""))
        if not cfg["allow_spring"] and easing.startswith("back.out"):
            issues.append({
                "severity": "P1",
                "event_id": eid,
                "code": "MOTION_UNSUPPORTED_SPRING",
                "message": f"Spring overshoot {easing!r} disallowed in profile {profile!r}",
            })
        dur_frames = e - s
        if dur_frames < max(3, int(round(0.2 * fps))) and not ev.get("intentional_flash"):
            issues.append({
                "severity": "P1",
                "event_id": eid,
                "code": "MOTION_TOO_BRIEF",
                "message": f"Event {eid} lasts only {dur_frames} frames (< 0.2s readable minimum)",
            })
        # Check for severe scale discontinuity inside hold span (frames s+entry .. e-exit-1)
        entry = max(1, int(ev.get("entry_frames", 6)))
        exit_f = max(0, int(ev.get("exit_frames", 4)))
        prev_pose = None
        for f in range(s + entry, max(s + entry, e - exit_f)):
            pose = evaluate_pose(ev, f)
            if prev_pose is not None:
                if abs(pose["scale"] - prev_pose["scale"]) > 0.25 or abs(pose["y_offset_px"] - prev_pose["y_offset_px"]) > 60:
                    issues.append({
                        "severity": "P1",
                        "event_id": eid,
                        "code": "MOTION_HOLD_JUMP",
                        "message": f"Unplanned one-frame jump during hold at frame {f}",
                    })
                    break
            prev_pose = pose
    return issues


# KH-02.R1 (R1.05) contract helpers
def emit_effect_event(
    *,
    event_id: str,
    beat_id: str,
    capability_id: str,
    kind: str,
    start_frame: int,
    end_frame: int,
    normalized_bbox: list[float],
    layer_id: str | None = None,
    layer_cat: str = "card",
    track_id: str = "track_info_primary",
    z_order: int = 30,
    asset_ref: str | None = None,
    anchor_word_id: str | None = None,
    anchor_mode: str = "beat_start",
    easing: str = "power2.out",
    transform: dict[str, Any] | None = None,
    mask: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    semantic_reason: str = "Purposeful visual emphasis",
    expected_visual_evidence: str = "Visible settled graphic in safe zone",
    fps: float = 24.0,
    profile: str = "editorial_pro",
) -> dict[str, Any]:
    """Emit a validated, planned R1 visual effect event bound to the single frame clock."""
    raw = {
        "event_id": event_id,
        "beat_id": beat_id,
        "track_id": track_id,
        "layer_id": layer_id or f"layer_{event_id}",
        "layer_cat": layer_cat,
        "z_order": int(z_order),
        "asset_ref": asset_ref,
        "capability_id": capability_id,
        "start_frame": int(start_frame),
        "end_frame": int(end_frame),
        "anchor_word_id": anchor_word_id,
        "anchor_mode": anchor_mode,
        "normalized_bbox": list(normalized_bbox),
        "opacity": 1.0,
        "transform": dict(transform or {"scale_from": 0.94, "scale_to": 1.0, "y_offset_px": 14}),
        "mask": mask,
        "blend": "normal",
        "easing": easing,
        "params": {"kind": kind, **(params or {})},
        "safe_zone": True,
        "face_exclusion": True,
        "semantic_reason": semantic_reason,
        "expected_visual_evidence": expected_visual_evidence,
    }
    return plan_motion([raw], fps=fps, profile=profile)[0]


def sample_effect_pose(event: dict[str, Any], frame: int) -> dict[str, float]:
    """Deterministic R1 alias for evaluating exact effect pose (opacity, scale, y_offset, clip_progress) at integer frame."""
    pose = evaluate_pose(event, frame)
    s, e = int(event["start_frame"]), int(event["end_frame"])
    entry = max(1, int(event.get("entry_frames", 6)))
    if frame < s or frame >= e:
        clip_prog = 0.0
    elif frame - s < entry:
        clip_prog = round(min(1.0, (frame - s + 1) / float(entry)), 4)
    else:
        clip_prog = 1.0
    pose["clip_progress"] = clip_prog
    return pose
