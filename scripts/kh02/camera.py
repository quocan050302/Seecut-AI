"""KH-02 Virtual 2D Camera Direction: framing, face-safe crop caps, continuous paths & cut exceptions."""

from __future__ import annotations

import math
from typing import Any


def compute_max_safe_zoom(
    source_width: int,
    source_height: int,
    output_width: int,
    output_height: int,
    *,
    hard_cap: float = 1.35,
) -> float:
    """Cap 2D virtual zoom by source-to-output pixel headroom so footage never turns blurry."""
    if source_width <= 0 or source_height <= 0 or output_width <= 0 or output_height <= 0:
        return 1.0
    ratio = min(source_width / float(output_width), source_height / float(output_height))
    # Allow up to max(1.12, ratio * 1.15) bounded by hard_cap to prevent excessive digital zoom
    return round(min(hard_cap, max(1.08, ratio * 1.15)), 3)


def choose_framing(
    beat: dict[str, Any],
    *,
    mode: str = "talking_head",
    face_rect: list[float] | str | None = None,
    has_real_evidence: bool = False,
) -> dict[str, Any]:
    """Choose semantic 2D framing (full_medium, full_push_in, pip_top_left, pip_top_right, split_focus, evidence_full)."""
    if mode == "faceless":
        return {
            "framing": "object_focus_full",
            "target_region": [0.0, 0.0, 1.0, 1.0],
            "anchor": [0.5, 0.5],
            "scale_from": 1.0,
            "scale_to": 1.04,
            "reason": "Faceless mode focuses on focal visual subject without mandatory presenter frame.",
        }

    fn = beat.get("semantic_function", "hook")
    vm = beat.get("visual_mode", "")
    # Anchor vertically on measured face center when available
    if isinstance(face_rect, list) and len(face_rect) == 4:
        ax = round((face_rect[0] + face_rect[2]) / 2.0, 3)
        ay = round((face_rect[1] + face_rect[3]) / 2.0, 3)
    else:
        ax, ay = 0.5, 0.36

    if "pip_top_right" in vm:
        return {
            "framing": "pip_top_right",
            "target_region": [0.62, 0.06, 0.32, 0.24],
            "anchor": [ax, ay],
            "scale_from": 1.0,
            "scale_to": 1.02,
            "reason": "Yield center/left frame to visual showcase while keeping presenter visible.",
        }
    if "pip" in vm or (has_real_evidence and fn in ("evidence", "step", "comparison", "mechanism")):
        return {
            "framing": "pip_top_left",
            "target_region": [0.06, 0.06, 0.32, 0.24],
            "anchor": [ax, ay],
            "scale_from": 1.0,
            "scale_to": 1.02,
            "reason": f"Compact PIP preserves presenter presence while spotlighting {fn} proof.",
        }
    if fn in ("hook", "payoff", "cta"):
        return {
            "framing": "full_push_in",
            "target_region": [0.0, 0.0, 1.0, 1.0],
            "anchor": [ax, ay],
            "scale_from": 1.0,
            "scale_to": 1.06,
            "reason": f"Deliberate 6% push-in emphasizes direct-to-camera {fn}.",
        }
    return {
        "framing": "full_medium",
        "target_region": [0.0, 0.0, 1.0, 1.0],
        "anchor": [ax, ay],
        "scale_from": 1.0,
        "scale_to": 1.03,
        "reason": f"Stable medium framing maintains visual clarity during {fn}.",
    }


def plan_camera_path(
    beats: list[dict[str, Any]],
    *,
    mode: str = "talking_head",
    source_width: int = 1080,
    source_height: int = 1920,
    output_width: int = 1080,
    output_height: int = 1920,
    face_rect: list[float] | str | None = None,
) -> list[dict[str, Any]]:
    """Build continuous, resolution-capped 2D virtual camera keyframes across beats."""
    max_zoom = compute_max_safe_zoom(source_width, source_height, output_width, output_height)
    events: list[dict[str, Any]] = []
    prev_scale = 1.0
    prev_framing = "full_medium"
    prev_pos = [0.0, 0.0]

    for idx, b in enumerate(beats):
        has_ev = b.get("proof_status") == "VERIFIED_REAL_EVIDENCE"
        fr = choose_framing(b, mode=mode, face_rect=face_rect, has_real_evidence=has_ev)
        sc_from = min(max_zoom, max(1.0, prev_scale if idx > 0 else fr["scale_from"]))
        sc_to = min(max_zoom, max(1.0, fr["scale_to"]))
        # Keep translation strictly inside overscan margin so zero black edges can appear
        max_dx = max(0.0, (sc_to - 1.0) / (2.0 * sc_to))
        max_dy = max(0.0, (sc_to - 1.0) / (2.0 * sc_to))
        target_pos = [0.0, round(-min(0.015, max_dy * 0.8), 4)] if sc_to > 1.01 else [0.0, 0.0]

        events.append({
            "camera_id": f"cam_{idx + 1:02d}",
            "beat_id": b["beat_id"],
            "start_frame": int(b["start_frame"]),
            "end_frame": int(b["end_frame"]),
            "target_region": fr["target_region"],
            "framing_from": prev_framing if idx > 0 else fr["framing"],
            "framing_to": fr["framing"],
            "scale_from": round(sc_from, 3),
            "scale_to": round(sc_to, 3),
            "pos_from": prev_pos if idx > 0 else [0.0, 0.0],
            "pos_to": [max(-max_dx, min(max_dx, target_pos[0])), max(-max_dy, min(max_dy, target_pos[1]))],
            "anchor": fr["anchor"],
            "easing": "power2.inOut",
            "velocity_limit": 0.35,
            "acceleration_limit": 0.85,
            "continuity_group": "cg_main",
            "reason": fr["reason"],
            "source_resolution_limit": max_zoom,
        })
        prev_scale = sc_to
        prev_framing = fr["framing"]
        prev_pos = events[-1]["pos_to"]

    return events


def validate_camera_continuity(
    camera_events: list[dict[str, Any]],
    *,
    face_rect: list[float] | str | None = None,
    fps: float = 24.0,
    intentional_cut_frames: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Validate zoom resolution caps, zero black-edge overscan, face crop safety, and boundary continuity."""
    cuts = intentional_cut_frames or set()
    issues: list[dict[str, Any]] = []
    ordered = sorted(camera_events, key=lambda c: (c["start_frame"], c["camera_id"]))

    for idx, c in enumerate(ordered):
        cid = c["camera_id"]
        res_lim = float(c.get("source_resolution_limit", 1.35))
        s0, s1 = float(c["scale_from"]), float(c["scale_to"])
        if s0 < 1.0 - 1e-6 or s1 < 1.0 - 1e-6:
            issues.append({
                "severity": "P0",
                "camera_id": cid,
                "code": "CAMERA_BLACK_EDGE_UNDERSCAN",
                "message": f"Scale < 1.0 ({s0} -> {s1}) exposes black canvas borders on full-bleed footage",
            })
        if s0 > res_lim + 1e-6 or s1 > res_lim + 1e-6:
            issues.append({
                "severity": "P1",
                "camera_id": cid,
                "code": "CAMERA_OVERZOOM",
                "message": f"Zoom ({s0}->{s1}) exceeds source resolution cap {res_lim}",
            })
        # Check translation overscan against available zoom margin
        for tag, sc, pos in (("start", s0, c.get("pos_from", [0.0, 0.0])), ("end", s1, c.get("pos_to", [0.0, 0.0]))):
            margin = max(0.0, (sc - 1.0) / (2.0 * max(sc, 1.0))) + 1e-4
            if abs(float(pos[0])) > margin or abs(float(pos[1])) > margin:
                issues.append({
                    "severity": "P0",
                    "camera_id": cid,
                    "code": "CAMERA_PAN_BLACK_EDGE",
                    "message": f"Pan offset {pos} at {tag} exceeds safe margin {margin:.4f} for scale={sc}",
                })

        # Check face_rect remains inside viewport under zoom/anchor
        if isinstance(face_rect, list) and len(face_rect) == 4 and c["target_region"] == [0.0, 0.0, 1.0, 1.0]:
            ax, ay = float(c["anchor"][0]), float(c["anchor"][1])
            fx0, fy0, fx1, fy1 = [float(v) for v in face_rect]
            for sc, pos in ((s0, c.get("pos_from", [0.0, 0.0])), (s1, c.get("pos_to", [0.0, 0.0]))):
                nx0 = ax + (fx0 - ax) * sc + float(pos[0])
                ny0 = ay + (fy0 - ay) * sc + float(pos[1])
                nx1 = ax + (fx1 - ax) * sc + float(pos[0])
                ny1 = ay + (fy1 - ay) * sc + float(pos[1])
                if nx0 < 0.0 or ny0 < 0.0 or nx1 > 1.0 or ny1 > 1.0:
                    issues.append({
                        "severity": "P1",
                        "camera_id": cid,
                        "code": "CAMERA_FACE_CHOP",
                        "message": f"Face rect [{nx0:.2f},{ny0:.2f},{nx1:.2f},{ny1:.2f}] clipped at scale={sc}",
                    })

        # Check velocity cap
        dur_sec = max(1 / fps, (int(c["end_frame"]) - int(c["start_frame"])) / fps)
        vel = abs(s1 - s0) / dur_sec
        if vel > float(c.get("velocity_limit", 0.5)) + 1e-6:
            issues.append({
                "severity": "P1",
                "camera_id": cid,
                "code": "CAMERA_VELOCITY_EXCESS",
                "message": f"Zoom velocity {vel:.3f}/s exceeds limit {c.get('velocity_limit')}",
            })

        # Continuity with previous camera event in same continuity group
        if idx > 0:
            prev = ordered[idx - 1]
            same_group = prev.get("continuity_group") == c.get("continuity_group")
            is_cut = c["start_frame"] in cuts
            if same_group and not is_cut and abs(float(prev["scale_to"]) - s0) > 0.08:
                issues.append({
                    "severity": "P1",
                    "camera_id": cid,
                    "code": "CAMERA_DISCONTINUITY",
                    "message": f"Non-cut scale jump at frame {c['start_frame']}: {prev['scale_to']} -> {s0}",
                })
    return issues


def build_camera_path(
    beats: list[dict[str, Any]],
    *,
    mode: str = "talking_head",
    source_width: int = 1080,
    source_height: int = 1920,
    output_width: int = 1080,
    output_height: int = 1920,
    rotation_deg: int = 0,
    face_rect: list[float] | str | None = None,
) -> list[dict[str, Any]]:
    """R1.05 rotation-aware virtual 2D camera path builder honoring source pixel coverage & face safety."""
    eff_w, eff_h = (source_height, source_width) if abs(int(rotation_deg)) in (90, 270) else (source_width, source_height)
    return plan_camera_path(
        beats,
        mode=mode,
        source_width=eff_w,
        source_height=eff_h,
        output_width=output_width,
        output_height=output_height,
        face_rect=face_rect,
    )
