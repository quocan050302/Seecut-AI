"""KH-02 Transition Direction: semantic transitions, match-anchor solving & seam continuity validation."""

from __future__ import annotations

from typing import Any


def solve_match_anchor(from_beat: dict[str, Any], to_beat: dict[str, Any]) -> str | None:
    """Resolve a shared visual anchor ID linking adjacent beats when continuity tags align."""
    c_out = str(from_beat.get("continuity_out") or "")
    c_in = str(to_beat.get("continuity_in") or "")
    if c_out and c_in and c_out == c_in and c_out not in ("none", "open_frame", "clean_settle"):
        return c_out
    if from_beat.get("semantic_function") == "hook" and to_beat.get("semantic_function") in ("evidence", "reveal", "step"):
        return "editorial_spine"
    if from_beat.get("semantic_function") in ("step", "mechanism") and to_beat.get("semantic_function") in ("step", "payoff"):
        return "step_progress_rail"
    return None


def choose_transition(
    from_beat: dict[str, Any],
    to_beat: dict[str, Any],
    *,
    fps: float = 24.0,
    profile: str = "editorial_pro",
    allow_intentional_cut: bool = False,
) -> dict[str, Any]:
    """Choose motivated transition technique & overlap window respecting upstream 00-总纲 no-hard-cut rule."""
    boundary = int(to_beat["start_frame"])
    anchor = solve_match_anchor(from_beat, to_beat)
    to_fn = to_beat.get("semantic_function", "")
    from_fn = from_beat.get("semantic_function", "")

    if anchor:
        technique = "match_anchor"
        dur_frames = max(4, int(round(0.28 * fps)))
        direction = "spine_handoff"
        reason = f"Shared match anchor '{anchor}' bridges {from_fn} -> {to_fn} smoothly without hard cut."
    elif from_beat.get("visual_mode") != to_beat.get("visual_mode"):
        technique = "reframe"
        dur_frames = max(5, int(round(0.32 * fps)))
        direction = "layout_reframe"
        reason = f"Smooth reframing shifts visual focus from {from_beat.get('visual_mode')} to {to_beat.get('visual_mode')}."
    elif profile == "minimal_documentary" and allow_intentional_cut:
        technique = "intentional_cut"
        dur_frames = 0
        direction = "cut"
        reason = "Approved documentary direct cut (logged exception to 00-总纲 no-hard-cut rule)."
    else:
        technique = "motion_bridge"
        dur_frames = max(4, int(round(0.22 * fps)))
        direction = "forward_flow"
        reason = f"Eased motion bridge preserves continuity between {from_fn} and {to_fn}."

    half = dur_frames // 2
    ov0 = max(int(from_beat["start_frame"]), boundary - half)
    ov1 = min(int(to_beat["end_frame"]), boundary + (dur_frames - half))
    return {
        "transition_id": f"tr_{from_beat['beat_id']}_to_{to_beat['beat_id']}",
        "from_beat": from_beat["beat_id"],
        "to_beat": to_beat["beat_id"],
        "boundary_frame": boundary,
        "overlap_range": [ov0, ov1],
        "technique": technique,
        "match_anchor": anchor,
        "direction": direction,
        "easing": "power2.inOut",
        "reason": reason,
        "audio_bridge": "continuous_voice",
        "cue_id": None,
    }


def validate_seam(
    transitions: list[dict[str, Any]],
    beats: list[dict[str, Any]],
    *,
    fps: float = 24.0,
    profile: str = "editorial_pro",
    approved_exceptions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Validate transition seam timing, overlap bounds, no frame gap, and upstream no-hard-cut compliance."""
    beat_map = {b["beat_id"]: b for b in beats}
    exc_rules = {e.get("rule_id") for e in (approved_exceptions or [])}
    issues: list[dict[str, Any]] = []

    for tr in transitions:
        tid = tr["transition_id"]
        fb = beat_map.get(tr["from_beat"])
        tb = beat_map.get(tr["to_beat"])
        if not fb or not tb:
            issues.append({"severity": "P0", "transition_id": tid, "code": "SEAM_DANGLING_BEAT", "message": "Missing beat reference"})
            continue
        if int(fb["end_frame"]) != int(tb["start_frame"]):
            issues.append({
                "severity": "P0",
                "transition_id": tid,
                "code": "SEAM_GAP_OR_OVERLAP",
                "message": f"Beat boundary mismatch: {fb['beat_id']}.end={fb['end_frame']} != {tb['beat_id']}.start={tb['start_frame']}",
            })
        bframe = int(tr["boundary_frame"])
        if bframe != int(tb["start_frame"]):
            issues.append({
                "severity": "P1",
                "transition_id": tid,
                "code": "SEAM_BOUNDARY_MISMATCH",
                "message": f"boundary_frame {bframe} != {tb['beat_id']}.start_frame {tb['start_frame']}",
            })
        ov0, ov1 = int(tr["overlap_range"][0]), int(tr["overlap_range"][1])
        if ov0 > ov1 or ov0 < int(fb["start_frame"]) or ov1 > int(tb["end_frame"]):
            issues.append({
                "severity": "P0",
                "transition_id": tid,
                "code": "SEAM_OVERLAP_OOB",
                "message": f"overlap_range [{ov0},{ov1}] outside adjacent beats",
            })
        dur_sec = (ov1 - ov0) / fps
        if dur_sec > 1.2:
            issues.append({
                "severity": "P1",
                "transition_id": tid,
                "code": "SEAM_TOO_LONG",
                "message": f"Transition duration {dur_sec:.2f}s steals reading time (>1.2s)",
            })
        if tr["technique"] == "intentional_cut" and profile in ("seecut_core", "editorial_pro") and "ALLOW_INTENTIONAL_CUT" not in exc_rules:
            issues.append({
                "severity": "P1",
                "transition_id": tid,
                "code": "SEAM_UNAPPROVED_HARD_CUT",
                "message": "Hard cut requires approved_exceptions entry ALLOW_INTENTIONAL_CUT per 00-总纲 rule 9",
            })
    return issues
