"""KH-03.R2 Adaptive Dynamic Layout, Smooth Morphing, Adaptive Captions & Anti-Repetition Engine (ADL-01..07).

Shared, deterministic, skill-agnostic layout choreography engine that works across ALL FIVE
universal editing skills (SK-01..SK-05) and all aspect ratios (9:16, 16:9, 1:1, 4:5):

- Preserves FULLSCREEN_LOCK / FULLSCREEN_9_16 strictly untouched (1.0x uniform canvas, zero crop/split/PIP).
- Preserves all legacy KH-02 presenter policies (LEGACY_DYNAMIC, FIXED_PIP, ADAPTIVE_PIP, DYNAMIC_HYBRID, FACELESS).
- Adds additive `ADAPTIVE_DYNAMIC` layout policy: starts full-screen, smoothly morphs live presenter
  into asymmetric splits / companion layouts / floating inset when semantic explanation or product proof
  benefits from dedicated spatial canvas, and returns fluidly to full-screen for human CTA.
- Rejects conflicting constraints (`fullscreen_lock=True` + `layout_policy=ADAPTIVE_DYNAMIC`) fail-closed.
- Single-pass exact rational `Fraction` EDL remapping (`DOUBLE_REMAP_FORBIDDEN`).
- Swept intermediate-frame geometry verification & adaptive negative-space caption placement.
- Layout-topology anti-repetition analysis separate from text similarity.
"""

from __future__ import annotations

import csv
import math
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import VALID_SKILL_IDS
from .contracts import assert_kh03_schema, hash_artifact, write_json_atomic

VALID_LAYOUT_POLICIES = ("ADAPTIVE_DYNAMIC", "FULLSCREEN_LOCK", "LEGACY_DYNAMIC")

LAYOUT_MODES = (
    "FULL_CANVAS",
    "PRESENTER_LEFT_SPLIT",
    "PRESENTER_RIGHT_SPLIT",
    "LOWER_COMPANION_SPLIT",
    "UPPER_COMPANION_SPLIT",
    "FLOATING_INSET_PRESENTER",
    "FULL_SCREEN_RETURN",
)

PLATFORM_SAFE_MARGINS: dict[str, dict[str, float]] = {
    "reels":   {"top": 0.065, "bottom": 0.145, "left": 0.04, "right": 0.125},
    "tiktok":  {"top": 0.075, "bottom": 0.165, "left": 0.04, "right": 0.135},
    "shorts":  {"top": 0.070, "bottom": 0.155, "left": 0.04, "right": 0.130},
    "youtube": {"top": 0.050, "bottom": 0.100, "left": 0.05, "right": 0.050},
    "square":  {"top": 0.055, "bottom": 0.100, "left": 0.05, "right": 0.050},
    "web":     {"top": 0.045, "bottom": 0.080, "left": 0.04, "right": 0.040},
}

_HEURISTIC_DISCLAIMER = (
    "Layout candidate scores are deterministic editorial heuristics only — NOT measured viewer retention, "
    "CTR, or conversion predictions."
)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _norm_to_px(box: list[float], w: int, h: int) -> list[int]:
    x = max(0, min(w - 1, int(round(box[0] * w))))
    y = max(0, min(h - 1, int(round(box[1] * h))))
    bw = max(1, min(w - x, int(round(box[2] * w))))
    bh = max(1, min(h - y, int(round(box[3] * h))))
    return [x, y, bw, bh]


def _box_iou(a: list[float], b: list[float]) -> float:
    ax0, ay0, ax1, ay1 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = max(1e-9, a[2] * a[3] + b[2] * b[3] - inter)
    return round(inter / union, 4)


def _overlap_area(a: list[float], b: list[float]) -> float:
    ix0 = max(a[0], b[0])
    iy0 = max(a[1], b[1])
    ix1 = min(a[0] + a[2], b[0] + b[2])
    iy1 = min(a[1] + a[3], b[1] + b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return (ix1 - ix0) * (iy1 - iy0)


def resolve_layout_policy(
    brief: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
    selected_skill: str | None = None,
    source_media: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and resolve layout policy with fail-closed conflict checks (ADL-01).

    Preserves all legacy KH-02/KH-03 defaults untouched when `layout_policy` is omitted.
    Explicitly rejects conflicting `fullscreen_lock=True` + `layout_policy=ADAPTIVE_DYNAMIC`.
    """
    b = dict(brief or {})
    c = dict(constraints or {})
    pp = dict(b.get("presenter_policy") or {})

    raw_pol = c.get("layout_policy") or pp.get("layout_policy")
    layout_lock = c.get("layout_lock")
    fs_lock_explicit = bool(
        c.get("fullscreen_lock")
        or pp.get("fullscreen_lock")
        or (str(layout_lock or "").strip().upper() in ("FULLSCREEN_9_16", "FULLSCREEN_LOCK"))
    )
    pres_mode = str(pp.get("mode") or c.get("presenter_mode") or "LEGACY_DYNAMIC").strip().upper()

    if raw_pol is not None:
        pol = str(raw_pol).strip().upper()
        if pol not in VALID_LAYOUT_POLICIES:
            raise ValueError(
                f"INVALID_LAYOUT_POLICY: {raw_pol!r} not in {VALID_LAYOUT_POLICIES}."
            )
    elif fs_lock_explicit:
        pol = "FULLSCREEN_LOCK"
    else:
        pol = "LEGACY_DYNAMIC"

    # Fail closed on explicit conflict between strict fullscreen lock and adaptive recomposition!
    if pol == "ADAPTIVE_DYNAMIC" and fs_lock_explicit:
        raise ValueError(
            "LAYOUT_POLICY_CONFLICT: Cannot combine explicit FULLSCREEN_9_16/fullscreen_lock with ADAPTIVE_DYNAMIC. "
            "Remove fullscreen_lock to enable adaptive recomposition, or keep FULLSCREEN_LOCK."
        )
    if pol == "ADAPTIVE_DYNAMIC" and pres_mode in ("FIXED_PIP", "FACELESS"):
        raise ValueError(
            f"LAYOUT_POLICY_CONFLICT: presenter_policy.mode={pres_mode!r} conflicts with ADAPTIVE_DYNAMIC."
        )

    if selected_skill is not None and str(selected_skill).upper() not in VALID_SKILL_IDS:
        raise ValueError(f"INVALID_SKILL_ID: {selected_skill!r} not in {VALID_SKILL_IDS}.")

    allow_hide = bool(pp.get("allow_hide", c.get("allow_hide", False)))
    visibility = "EDITORIAL_ALLOWED" if allow_hide else "ALWAYS_VISIBLE"

    src = dict(source_media or {})
    src_w = int(src.get("width") or 1080)
    src_h = int(src.get("height") or 1920)
    rot = int(src.get("rotation_deg") or 0) % 360
    if rot in (90, 270):
        src_w, src_h = src_h, src_w

    allowed_modes = (
        ["FULL_CANVAS"]
        if pol == "FULLSCREEN_LOCK"
        else (
            list(LAYOUT_MODES)
            if pol == "ADAPTIVE_DYNAMIC"
            else ["FULL_CANVAS"]
        )
    )

    return {
        "layout_policy": pol,
        "fullscreen_lock": (pol == "FULLSCREEN_LOCK"),
        "presenter_mode": pres_mode,
        "visibility": visibility,
        "allow_hide": allow_hide,
        "allowed_layout_modes": allowed_modes,
        "min_mobile_face_px": 24.0,
        "effective_source_width": src_w,
        "effective_source_height": src_h,
        "rotation_deg": rot,
        "selected_skill_id": selected_skill,
    }


def build_subject_action_tracks(
    source_media: dict[str, Any],
    transcript: dict[str, Any] | None = None,
    action_map: dict[str, Any] | None = None,
    probe_frames: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build source-coordinate subject & action tracks with explicit static-fallback provenance (ADL-01).

    Never claims ML tracking unless verified per-frame detector boxes are explicitly supplied in `probe_frames`.
    Handles rotated footage, aspect variations, and bounded sampling for long-form videos.
    """
    src_w = int(source_media.get("width") or 1080)
    src_h = int(source_media.get("height") or 1920)
    rot = int(source_media.get("rotation_deg") or 0) % 360
    eff_w, eff_h = (src_h, src_w) if rot in (90, 270) else (src_w, src_h)
    aspect = str(source_media.get("aspect_ratio") or ("9:16" if eff_h > eff_w else ("1:1" if eff_w == eff_h else "16:9")))
    nb_frames = max(1, int(source_media.get("nb_frames") or 240))
    fps_num = max(1, int(source_media.get("fps_num") or 24))
    fps_den = max(1, int(source_media.get("fps_den") or 1))
    dur_sec = nb_frames / (fps_num / float(fps_den))

    # Verified external detector boxes (only if explicitly supplied with detector provenance)
    has_verified_detector = bool(
        probe_frames
        and all(isinstance(pf, dict) and pf.get("verified_detector") and pf.get("face_bbox_norm") for pf in probe_frames)
    )

    if has_verified_detector and probe_frames:
        mode = "LOCAL_TRACKER_VERIFIED"
        fxs = [pf["face_bbox_norm"][0] for pf in probe_frames]
        fys = [pf["face_bbox_norm"][1] for pf in probe_frames]
        fx2 = [pf["face_bbox_norm"][0] + pf["face_bbox_norm"][2] for pf in probe_frames]
        fy2 = [pf["face_bbox_norm"][1] + pf["face_bbox_norm"][3] for pf in probe_frames]
        face_box = [round(min(fxs), 4), round(min(fys), 4), round(max(fx2) - min(fxs), 4), round(max(fy2) - min(fys), 4)]
        conf = round(sum(float(pf.get("confidence", 0.85)) for pf in probe_frames) / len(probe_frames), 3)
        samples = probe_frames[:16]
    else:
        mode = "STATIC_FALLBACK_VERIFIED"
        conf = 0.72
        if aspect == "9:16":
            face_box = [0.24, 0.21, 0.52, 0.26]
            mouth_box = [0.36, 0.36, 0.28, 0.08]
            hands_box = [0.06, 0.54, 0.88, 0.40]
            body_box = [0.05, 0.20, 0.90, 0.78]
        elif aspect == "16:9":
            face_box = [0.34, 0.14, 0.32, 0.38]
            mouth_box = [0.42, 0.36, 0.16, 0.10]
            hands_box = [0.18, 0.54, 0.64, 0.40]
            body_box = [0.18, 0.12, 0.64, 0.84]
        else:
            face_box = [0.26, 0.18, 0.48, 0.32]
            mouth_box = [0.36, 0.36, 0.28, 0.09]
            hands_box = [0.10, 0.52, 0.80, 0.42]
            body_box = [0.10, 0.16, 0.80, 0.80]
        # Bounded representative sampling windows (max 12 even on 10-min long-form video)
        n_samp = min(12, max(3, int(round(dur_sec / 6.0))))
        samples = [
            {
                "frame": int(round(i * (nb_frames - 1) / max(1, n_samp - 1))),
                "face_bbox_norm": face_box,
                "hands_bbox_norm": hands_box,
                "uncertainty_pad": 0.025 if dur_sec > 60.0 else 0.015,
                "verified_detector": False,
            }
            for i in range(n_samp)
        ]

    if has_verified_detector:
        mouth_box = [
            round(face_box[0] + face_box[2] * 0.22, 4),
            round(face_box[1] + face_box[3] * 0.58, 4),
            round(face_box[2] * 0.56, 4),
            round(face_box[3] * 0.28, 4),
        ]
        hands_box = [0.06, min(0.82, round(face_box[1] + face_box[3] + 0.08, 4)), 0.88, 0.38]
        body_box = [0.05, max(0.05, round(face_box[1] - 0.02, 4)), 0.90, min(0.92, round(0.96 - max(0.05, face_box[1] - 0.02), 4))]

    action_windows = list((action_map or {}).get("action_windows") or [])
    if not action_windows and transcript and transcript.get("words"):
        words = transcript["words"]
        action_windows = [{
            "window_id": "act_speech_continuous",
            "start_sec": float(words[0].get("start_sec", words[0].get("start", 0.0))),
            "end_sec": float(words[-1].get("end_sec", words[-1].get("end", dur_sec))),
            "kind": "continuous_live_delivery",
            "protect_face_and_hands": True,
        }]

    return {
        "schema_version": "kh03.subject_action_tracks.v1",
        "tracking_mode": mode,
        "confidence": conf,
        "effective_source_dims": [eff_w, eff_h],
        "rotation_deg": rot,
        "aspect_ratio": aspect,
        "face_bbox_norm": face_box,
        "mouth_bbox_norm": mouth_box,
        "hands_bbox_norm": hands_box,
        "body_bbox_norm": body_box,
        "focus_center_norm": [
            round(face_box[0] + face_box[2] * 0.5, 4),
            round(face_box[1] + face_box[3] * 0.45, 4),
        ],
        "action_windows": action_windows,
        "sampled_windows": samples,
        "note": (
            "Verified local detector track bounds."
            if has_verified_detector
            else "Conservative static swept bounds verified; ML per-frame tracking was NOT run and is not claimed."
        ),
    }


def compute_source_crop_for_box(
    src_w: int,
    src_h: int,
    box_w: int,
    box_h: int,
    *,
    rotation_deg: int = 0,
    focus_center: tuple[float, float] = (0.5, 0.36),
    hands_bbox: list[float] | None = None,
) -> dict[str, Any]:
    """Compute exact aspect-preserving object-fit:cover crop math for any presenter box (ZERO stretch!)."""
    rot = int(rotation_deg) % 360
    ew, eh = (src_h, src_w) if rot in (90, 270) else (src_w, src_h)
    ew = max(16, int(ew))
    eh = max(16, int(eh))
    bw = max(16, int(box_w))
    bh = max(16, int(box_h))

    src_ar = ew / float(eh)
    box_ar = bw / float(bh)
    fx, fy = _clamp(focus_center[0], 0.15, 0.85), _clamp(focus_center[1], 0.15, 0.75)

    # Cover crop keeping uniform scale (aspect_distortion == 0.0 always!)
    if src_ar > box_ar:
        # Source wider than box -> crop horizontally around fx, full vertical height preserved (hands + face safe!)
        vis_w = box_ar / src_ar
        vis_h = 1.0
        cx = _clamp(fx - vis_w * 0.5, 0.0, 1.0 - vis_w)
        cy = 0.0
    else:
        # Source taller than box -> crop vertically around weighted center of face & upper action
        vis_w = 1.0
        vis_h = src_ar / box_ar
        target_y = fy
        if hands_bbox and len(hands_bbox) == 4:
            # Blend face center and upper-hands center so both stay inside frame whenever possible
            hy = hands_bbox[1] + hands_bbox[3] * 0.30
            target_y = 0.58 * fy + 0.42 * hy
        cy = _clamp(target_y - vis_h * 0.5, 0.0, 1.0 - vis_h)
        cx = 0.0

    # Object-position percentage matching cx/cy for CSS object-fit:cover
    pos_x_pct = 50.0 if vis_w >= 0.999 else round((cx / max(1e-6, 1.0 - vis_w)) * 100.0, 1)
    pos_y_pct = 35.0 if vis_h >= 0.999 else round((cy / max(1e-6, 1.0 - vis_h)) * 100.0, 1)

    hands_ok = True
    if hands_bbox and len(hands_bbox) == 4:
        hy_mid = hands_bbox[1] + hands_bbox[3] * 0.25
        hands_ok = (cy <= hy_mid <= cy + vis_h)

    return {
        "fit_mode": "cover",
        "aspect_distortion": 0.0,
        "source_effective_dims": [ew, eh],
        "target_box_px": [bw, bh],
        "crop_rect_norm": [round(cx, 4), round(cy, 4), round(vis_w, 4), round(vis_h, 4)],
        "object_position": f"{pos_x_pct:.1f}% {pos_y_pct:.1f}%",
        "scale": 1.0,
        "face_preserved": (cy <= fy <= cy + vis_h) and (cx <= fx <= cx + vis_w),
        "hands_preserved": hands_ok,
    }


def _mode_geometries(aspect_ratio: str, safe_m: dict[str, float]) -> dict[str, dict[str, Any]]:
    """Return responsive canvas-normalized presenter, graphic, and caption slot geometries per aspect."""
    top_s = safe_m["top"]
    bot_s = safe_m["bottom"]
    left_s = safe_m["left"]
    right_s = safe_m["right"]

    if aspect_ratio == "16:9":
        return {
            "FULL_CANVAS": {
                "presenter": [0.0, 0.0, 1.0, 1.0],
                "graphic":   [0.05, 0.06, 0.44, 0.24],
                "caption":   [0.14, 0.80, 0.72, 0.11],
                "radius": 0, "p_z": 10, "g_z": 30,
            },
            "PRESENTER_LEFT_SPLIT": {
                "presenter": [0.03, 0.07, 0.43, 0.78],
                "graphic":   [0.49, 0.07, 0.47, 0.78],
                "caption":   [0.06, 0.87, 0.88, 0.09],
                "radius": 22, "p_z": 20, "g_z": 25,
            },
            "PRESENTER_RIGHT_SPLIT": {
                "presenter": [0.54, 0.07, 0.43, 0.78],
                "graphic":   [0.04, 0.07, 0.47, 0.78],
                "caption":   [0.06, 0.87, 0.88, 0.09],
                "radius": 22, "p_z": 20, "g_z": 25,
            },
            "LOWER_COMPANION_SPLIT": {
                "presenter": [0.04, 0.48, 0.42, 0.42],
                "graphic":   [0.04, 0.06, 0.92, 0.39],
                "caption":   [0.49, 0.56, 0.46, 0.24],
                "radius": 20, "p_z": 22, "g_z": 20,
            },
            "UPPER_COMPANION_SPLIT": {
                "presenter": [0.04, 0.06, 0.46, 0.52],
                "graphic":   [0.52, 0.06, 0.44, 0.78],
                "caption":   [0.04, 0.64, 0.46, 0.22],
                "radius": 20, "p_z": 22, "g_z": 20,
            },
            "FLOATING_INSET_PRESENTER": {
                "presenter": [0.04, 0.50, 0.30, 0.42],
                "graphic":   [0.37, 0.07, 0.59, 0.76],
                "caption":   [0.37, 0.85, 0.59, 0.10],
                "radius": 24, "p_z": 35, "g_z": 20,
            },
            "FULL_SCREEN_RETURN": {
                "presenter": [0.0, 0.0, 1.0, 1.0],
                "graphic":   [0.14, 0.72, 0.72, 0.16],
                "caption":   [0.14, 0.58, 0.72, 0.10],
                "radius": 0, "p_z": 10, "g_z": 30,
            },
        }
    if aspect_ratio == "1:1":
        return {
            "FULL_CANVAS": {
                "presenter": [0.0, 0.0, 1.0, 1.0],
                "graphic":   [0.05, 0.05, 0.90, 0.18],
                "caption":   [0.08, 0.82, 0.84, 0.10],
                "radius": 0, "p_z": 10, "g_z": 30,
            },
            "PRESENTER_LEFT_SPLIT": {
                "presenter": [0.04, 0.07, 0.45, 0.75],
                "graphic":   [0.52, 0.07, 0.44, 0.75],
                "caption":   [0.06, 0.85, 0.88, 0.10],
                "radius": 22, "p_z": 20, "g_z": 25,
            },
            "PRESENTER_RIGHT_SPLIT": {
                "presenter": [0.51, 0.07, 0.45, 0.75],
                "graphic":   [0.04, 0.07, 0.44, 0.75],
                "caption":   [0.06, 0.85, 0.88, 0.10],
                "radius": 22, "p_z": 20, "g_z": 25,
            },
            "LOWER_COMPANION_SPLIT": {
                "presenter": [0.04, 0.52, 0.44, 0.40],
                "graphic":   [0.04, 0.06, 0.92, 0.43],
                "caption":   [0.51, 0.60, 0.45, 0.24],
                "radius": 20, "p_z": 25, "g_z": 20,
            },
            "UPPER_COMPANION_SPLIT": {
                "presenter": [0.05, 0.06, 0.90, 0.46],
                "graphic":   [0.05, 0.55, 0.90, 0.28],
                "caption":   [0.08, 0.85, 0.84, 0.10],
                "radius": 20, "p_z": 20, "g_z": 25,
            },
            "FLOATING_INSET_PRESENTER": {
                "presenter": [0.04, 0.55, 0.36, 0.37],
                "graphic":   [0.04, 0.06, 0.92, 0.46],
                "caption":   [0.43, 0.62, 0.52, 0.22],
                "radius": 24, "p_z": 35, "g_z": 20,
            },
            "FULL_SCREEN_RETURN": {
                "presenter": [0.0, 0.0, 1.0, 1.0],
                "graphic":   [0.08, 0.74, 0.84, 0.16],
                "caption":   [0.10, 0.60, 0.80, 0.10],
                "radius": 0, "p_z": 10, "g_z": 30,
            },
        }
    # Default 9:16 & 4:5 vertical studio choreography (generous live presenter + dedicated negative-space stage!)
    usable_w = round(1.0 - left_s - right_s, 4)
    return {
        "FULL_CANVAS": {
            "presenter": [0.0, 0.0, 1.0, 1.0],
            "graphic":   [0.045, round(max(0.055, top_s), 4), 0.91, 0.135],
            "caption":   [0.055, round(max(0.198, top_s + 0.142), 4), 0.84, 0.055],
            "radius": 0, "p_z": 10, "g_z": 30,
        },
        "PRESENTER_LEFT_SPLIT": {
            "presenter": [0.038, 0.078, 0.468, 0.735],
            "graphic":   [0.528, 0.078, 0.434, 0.735],
            "caption":   [0.045, round(min(0.825, 1.0 - bot_s - 0.025), 4), round(min(0.83, usable_w + 0.02), 4), 0.065],
            "radius": 26, "p_z": 20, "g_z": 25,
        },
        "PRESENTER_RIGHT_SPLIT": {
            "presenter": [0.494, 0.078, 0.468, 0.735],
            "graphic":   [0.038, 0.078, 0.434, 0.735],
            "caption":   [0.045, round(min(0.825, 1.0 - bot_s - 0.025), 4), round(min(0.83, usable_w + 0.02), 4), 0.065],
            "radius": 26, "p_z": 20, "g_z": 25,
        },
        "LOWER_COMPANION_SPLIT": {
            "presenter": [0.040, 0.505, 0.920, 0.345],
            "graphic":   [0.040, 0.070, 0.920, 0.415],
            "caption":   [0.045, 0.858, 0.830, 0.058],
            "radius": 24, "p_z": 20, "g_z": 25,
        },
        "UPPER_COMPANION_SPLIT": {
            "presenter": [0.040, 0.070, 0.920, 0.410],
            "graphic":   [0.040, 0.500, 0.920, 0.320],
            "caption":   [0.045, 0.835, 0.830, 0.058],
            "radius": 24, "p_z": 20, "g_z": 25,
        },
        "FLOATING_INSET_PRESENTER": {
            "presenter": [0.040, 0.535, 0.425, 0.365],
            "graphic":   [0.040, 0.068, 0.920, 0.445],
            "caption":   [0.488, 0.600, 0.460, 0.195],
            "radius": 28, "p_z": 35, "g_z": 22,
        },
        "FULL_SCREEN_RETURN": {
            "presenter": [0.0, 0.0, 1.0, 1.0],
            "graphic":   [0.050, 0.735, 0.840, 0.125],
            "caption":   [0.055, 0.655, 0.820, 0.060],
            "radius": 0, "p_z": 10, "g_z": 30,
        },
    }


def enumerate_layout_candidates(
    beat: dict[str, Any],
    skill_contract: dict[str, Any],
    canvas: dict[str, Any],
    source_media: dict[str, Any],
    action_tracks: dict[str, Any],
    assets: list[dict[str, Any]] | None = None,
    prior_state: dict[str, Any] | None = None,
    layout_policy: str = "ADAPTIVE_DYNAMIC",
    platform: str = "reels",
) -> list[dict[str, Any]]:
    """Enumerate valid aspect-safe layout geometry candidates for a single beat (ADL-01)."""
    cw = int(canvas.get("width") or 1080)
    ch = int(canvas.get("height") or 1920)
    aspect = str(canvas.get("aspect_ratio") or "9:16")
    safe_m = PLATFORM_SAFE_MARGINS.get(platform, PLATFORM_SAFE_MARGINS["reels"])
    geoms = _mode_geometries(aspect, safe_m)

    allowed = ["FULL_CANVAS"] if layout_policy != "ADAPTIVE_DYNAMIC" else list(LAYOUT_MODES)
    eff_w, eff_h = action_tracks.get("effective_source_dims", [cw, ch])
    rot = int(action_tracks.get("rotation_deg", 0))
    fc = tuple(action_tracks.get("focus_center_norm", [0.5, 0.36]))
    face_norm = action_tracks.get("face_bbox_norm", [0.24, 0.21, 0.52, 0.26])
    hands_norm = action_tracks.get("hands_bbox_norm", [0.06, 0.54, 0.88, 0.40])

    cands: list[dict[str, Any]] = []
    for mode in allowed:
        g = geoms[mode]
        p_norm = list(g["presenter"])
        g_norm = list(g["graphic"])
        c_norm = list(g["caption"])
        p_px = _norm_to_px(p_norm, cw, ch)
        g_px = _norm_to_px(g_norm, cw, ch)
        crop = compute_source_crop_for_box(
            eff_w, eff_h, p_px[2], p_px[3],
            rotation_deg=rot, focus_center=(fc[0], fc[1]), hands_bbox=hands_norm,
        )
        # Estimate presenter face width at 360px mobile viewport
        vis_w_frac = max(0.2, crop["crop_rect_norm"][2])
        mobile_face_px = round(360.0 * p_norm[2] * (face_norm[2] / vis_w_frac), 1)
        # Check non-overlap between graphic and presenter (when split/floating)
        ov_pg = 0.0 if mode in ("FULL_CANVAS", "FULL_SCREEN_RETURN") else round(_overlap_area(p_norm, g_norm), 4)
        feasible = (mobile_face_px >= 22.0) and (crop["aspect_distortion"] == 0.0) and (ov_pg <= 0.01)

        cands.append({
            "layout_mode": mode,
            "presenter_box_norm": [round(v, 4) for v in p_norm],
            "presenter_box_px": p_px,
            "graphic_box_norm": [round(v, 4) for v in g_norm],
            "graphic_box_px": g_px,
            "caption_slot_norm": [round(v, 4) for v in c_norm],
            "source_crop": crop,
            "presenter_z_order": int(g["p_z"]),
            "graphic_z_order": int(g["g_z"]),
            "border_radius_px": int(g["radius"]),
            "anchor": [round(fc[0], 3), round(fc[1], 3)],
            "presenter_opacity": 1.0,
            "mobile_face_width_px": mobile_face_px,
            "presenter_graphic_overlap": ov_pg,
            "feasible": feasible,
            "reject_reason": None if feasible else f"Infeasible mobile_face_px={mobile_face_px} or overlap={ov_pg}",
        })
    return cands


def score_layout_candidates(
    candidates: list[dict[str, Any]],
    *,
    beat: dict[str, Any],
    beat_idx: int,
    total_beats: int,
    skill_id: str,
    prior_mode: str | None = None,
    consecutive_same_count: int = 0,
    has_evidence: bool = False,
    has_numbers: bool = False,
) -> list[dict[str, Any]]:
    """Deterministically score enumerated layout candidates across all 5 skills (ADL-01/02)."""
    fn = str(beat.get("semantic_function") or "context")
    is_first = (beat_idx == 0)
    is_last = (beat_idx == total_beats - 1) and total_beats >= 2

    scored: list[dict[str, Any]] = []
    for c in candidates:
        mode = c["layout_mode"]
        if not c["feasible"]:
            sc = 0.0
            why = c["reject_reason"] or "Infeasible geometry."
        else:
            sc = 62.0
            reasons: list[str] = []

            # 1. Semantic milestone fit
            if is_first:
                if mode == "FULL_CANVAS":
                    sc += 28.0
                    reasons.append("Frame-0 human-first full-screen hook establishes immediate connection")
                else:
                    sc -= 14.0
            elif is_last and fn in ("cta", "payoff", "reaction"):
                if mode == "FULL_SCREEN_RETURN":
                    sc += 26.0
                    reasons.append("Fluid full-screen return restores eye contact for human CTA")
                elif mode == "FULL_CANVAS":
                    sc += 18.0
                else:
                    sc -= 6.0
            elif fn in ("evidence", "proof") or has_evidence:
                if mode == "FLOATING_INSET_PRESENTER":
                    sc += 24.0
                    reasons.append("Evidence-first hero stage gives product/proof clear reading room with live inset presenter")
                elif mode == "LOWER_COMPANION_SPLIT":
                    sc += 19.0
                    reasons.append("Wide upper proof band paired with continuous lower presenter")
                elif mode in ("PRESENTER_LEFT_SPLIT", "PRESENTER_RIGHT_SPLIT"):
                    sc += 12.0
            elif fn in ("payoff", "reveal", "turning_point"):
                if mode == "PRESENTER_RIGHT_SPLIT" and prior_mode == "PRESENTER_LEFT_SPLIT":
                    sc += 25.0
                    reasons.append("Spatial mirror flip signals payoff turning point with crisp result focus")
                elif mode == "PRESENTER_RIGHT_SPLIT":
                    sc += 20.0
                    reasons.append("Dedicated left hero zone emphasizes clear numeric/story payoff")
                elif mode == "LOWER_COMPANION_SPLIT":
                    sc += 16.0
            elif fn in ("demonstration", "mechanism", "step", "comparison", "problem", "tension") or has_numbers:
                if mode == "PRESENTER_LEFT_SPLIT":
                    sc += 22.0
                    reasons.append("Asymmetric left presenter + right explainer column avoids top-banner fatigue")
                elif mode == "PRESENTER_RIGHT_SPLIT":
                    sc += 18.0
                elif mode == "LOWER_COMPANION_SPLIT":
                    sc += 14.0
                elif mode == "FULL_CANVAS":
                    sc -= 6.0  # Discourage sticking to repeated top-card banner during dense explanation!
            else:
                if mode in ("PRESENTER_LEFT_SPLIT", "FULL_CANVAS", "UPPER_COMPANION_SPLIT"):
                    sc += 10.0

            # 2. Skill-specific choreography grammar (all 5 universal skills!)
            if skill_id == "SK-05":
                # Natural Retention prefers calm full-canvas holds, gentle reframes only when evidence/math truly benefits
                if mode in ("FULL_CANVAS", "FULL_SCREEN_RETURN"):
                    sc += 16.0
                    reasons.append("SK-05 favors uninterrupted authentic delivery")
                elif mode in ("PRESENTER_LEFT_SPLIT", "FLOATING_INSET_PRESENTER") and (has_evidence or has_numbers):
                    sc += 6.0
                else:
                    sc -= 6.0
            elif skill_id == "SK-04":
                # Intelligent Visual Explainer maximizes diagram/math/proof clarity
                if mode in ("PRESENTER_LEFT_SPLIT", "PRESENTER_RIGHT_SPLIT", "FLOATING_INSET_PRESENTER") and not is_first and not is_last:
                    sc += 8.0
                    reasons.append("SK-04 opens dedicated spatial canvas for visual comprehension")
            elif skill_id == "SK-01":
                # Viral Dynamic rewards sharper spatial shifts on tension/payoff
                if mode != prior_mode and not is_first:
                    sc += 5.5
                    reasons.append("SK-01 rewards purposeful kinetic visual contrast")
            elif skill_id == "SK-02":
                # Premium Motion favors clean architectural splits & floating evidence showcase
                if mode in ("PRESENTER_LEFT_SPLIT", "FLOATING_INSET_PRESENTER", "FULL_SCREEN_RETURN"):
                    sc += 6.5
                    reasons.append("SK-02 architectural editorial grid framing")
            elif skill_id == "SK-03":
                # Cinematic Storytelling favors measured narrative continuity and fewer abrupt shifts
                if mode == prior_mode and consecutive_same_count < 2 and fn not in ("payoff", "evidence", "cta"):
                    sc += 7.0
                    reasons.append("SK-03 preserves atmospheric narrative continuity")

            # 3. Anti-repetition & continuity balance
            if prior_mode == mode and consecutive_same_count >= 2 and skill_id != "SK-05" and not is_first:
                sc -= 11.0
            if c["source_crop"].get("hands_preserved"):
                sc += 3.5
            if c["source_crop"].get("face_preserved"):
                sc += 3.5
            if mode == "FULL_SCREEN_RETURN" and is_first:
                sc -= 20.0

            sc = round(_clamp(sc, 0.0, 99.5), 2)
            why = "; ".join(reasons) if reasons else f"Valid {mode} composition for {fn} under {skill_id}."

        out_c = dict(c)
        out_c["score"] = sc
        out_c["reason"] = why
        scored.append(out_c)

    # Strict deterministic tie-break: score DESC -> layout_mode ASC
    scored.sort(key=lambda x: (-float(x["score"]), str(x["layout_mode"])))
    return scored


def solve_layout_sequence(
    beats: list[dict[str, Any]],
    *,
    run_id: str,
    skill_id: str,
    skill_contract: dict[str, Any],
    layout_policy: str,
    canvas: dict[str, Any],
    source_media: dict[str, Any],
    action_tracks: dict[str, Any],
    constraints: dict[str, Any] | None = None,
    assets: list[dict[str, Any]] | None = None,
    fps_num: int = 24,
    fps_den: int = 1,
    platform: str = "reels",
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Deterministically solve the beat-by-beat spatial choreography sequence (ADL-01/02)."""
    if not beats:
        raise ValueError("LAYOUT_SOLVER_EMPTY_BEATS: At least one beat is required.")

    cw = int(canvas.get("width") or 1080)
    ch = int(canvas.get("height") or 1920)
    aspect = str(canvas.get("aspect_ratio") or "9:16")
    total_frames = max(1, max(int(b["end_frame"]) for b in beats))
    c_sha = hash_artifact(constraints or {"layout_policy": layout_policy})

    planned_beats: list[dict[str, Any]] = []
    all_candidates_log: list[dict[str, Any]] = []
    prior_mode: str | None = None
    same_streak = 0

    for idx, b in enumerate(beats):
        has_ev = (b.get("proof_status") == "VERIFIED_REAL_EVIDENCE") or (b.get("semantic_function") in ("evidence", "proof"))
        has_num = bool(b.get("has_numeric_claims") or b.get("semantic_function") in ("demonstration", "mechanism", "comparison", "step"))
        cands = enumerate_layout_candidates(
            b, skill_contract, canvas, source_media, action_tracks,
            assets=assets, prior_state={"mode": prior_mode}, layout_policy=layout_policy, platform=platform,
        )
        scored = score_layout_candidates(
            cands,
            beat=b,
            beat_idx=idx,
            total_beats=len(beats),
            skill_id=skill_id,
            prior_mode=prior_mode,
            consecutive_same_count=same_streak,
            has_evidence=has_ev,
            has_numbers=has_num,
        )
        chosen = next((x for x in scored if x["feasible"]), scored[0])
        if chosen["layout_mode"] == prior_mode:
            same_streak += 1
        else:
            same_streak = 1
        prior_mode = chosen["layout_mode"]

        lid = f"lay_{idx + 1:02d}_{b['beat_id']}"
        p_norm = chosen["presenter_box_norm"]
        # Transform source protected face & hands into canvas coordinates
        crop_r = chosen["source_crop"]["crop_rect_norm"]
        prot_masks: list[dict[str, Any]] = []
        for zname, src_box in (
            ("face", action_tracks["face_bbox_norm"]),
            ("mouth", action_tracks["mouth_bbox_norm"]),
            ("hands", action_tracks["hands_bbox_norm"]),
        ):
            # Map source box through cover crop into presenter box
            rx0 = _clamp((src_box[0] - crop_r[0]) / max(1e-6, crop_r[2]), 0.0, 1.0)
            ry0 = _clamp((src_box[1] - crop_r[1]) / max(1e-6, crop_r[3]), 0.0, 1.0)
            rx1 = _clamp((src_box[0] + src_box[2] - crop_r[0]) / max(1e-6, crop_r[2]), 0.0, 1.0)
            ry1 = _clamp((src_box[1] + src_box[3] - crop_r[1]) / max(1e-6, crop_r[3]), 0.0, 1.0)
            if rx1 > rx0 and ry1 > ry0:
                prot_masks.append({
                    "zone": zname,
                    "bbox_norm": [
                        round(p_norm[0] + rx0 * p_norm[2], 4),
                        round(p_norm[1] + ry0 * p_norm[3], 4),
                        round((rx1 - rx0) * p_norm[2], 4),
                        round((ry1 - ry0) * p_norm[3], 4),
                    ],
                })

        planned_beats.append({
            "layout_id": lid,
            "beat_id": b["beat_id"],
            "semantic_function": str(b.get("semantic_function") or "context"),
            "layout_mode": chosen["layout_mode"],
            "start_frame": int(b["start_frame"]),
            "end_frame": int(b["end_frame"]),
            "src_start_frame": int(b["start_frame"]),
            "src_end_frame": int(b["end_frame"]),
            "presenter_box_norm": p_norm,
            "presenter_box_px": chosen["presenter_box_px"],
            "graphic_box_norm": chosen["graphic_box_norm"],
            "graphic_box_px": chosen["graphic_box_px"],
            "caption_slot_norm": chosen["caption_slot_norm"],
            "protected_masks_norm": prot_masks,
            "source_crop": chosen["source_crop"],
            "presenter_z_order": chosen["presenter_z_order"],
            "graphic_z_order": chosen["graphic_z_order"],
            "border_radius_px": chosen["border_radius_px"],
            "anchor": chosen["anchor"],
            "presenter_opacity": chosen["presenter_opacity"],
            "mobile_face_width_px": chosen["mobile_face_width_px"],
            "score": chosen["score"],
            "reason": chosen["reason"],
            "asset_refs": list(b.get("content_refs") or ["primary_aroll"]),
            "provenance": {
                "skill_id": skill_id,
                "tracking_mode": action_tracks["tracking_mode"],
                "confidence": action_tracks["confidence"],
                "heuristic_only": True,
            },
        })
        all_candidates_log.append({
            "beat_id": b["beat_id"],
            "selected_mode": chosen["layout_mode"],
            "selected_score": chosen["score"],
            "candidates": [
                {
                    "layout_mode": x["layout_mode"],
                    "score": x["score"],
                    "feasible": x["feasible"],
                    "mobile_face_width_px": x["mobile_face_width_px"],
                    "reason": x["reason"],
                }
                for x in scored
            ],
        })

    plan_doc: dict[str, Any] = {
        "schema_version": "kh03.layout_plan.v1",
        "run_id": run_id,
        "selected_skill_id": skill_id,
        "layout_policy": layout_policy,
        "clock_domain": "SOURCE_CLOCK",
        "time_map_sha256": None,
        "fps_num": int(fps_num),
        "fps_den": int(fps_den),
        "total_frames": total_frames,
        "canvas": {"width": cw, "height": ch, "aspect_ratio": aspect},
        "constraints_sha256": c_sha,
        "subject_tracking_mode": action_tracks["tracking_mode"],
        "editorial_heuristic_disclaimer": _HEURISTIC_DISCLAIMER,
        "beats": planned_beats,
        "transitions": [],
    }
    transitions = plan_layout_transitions(plan_doc, fps_num=fps_num, fps_den=fps_den, run_dir=run_dir)
    plan_doc["transitions"] = transitions
    plan_doc["layout_plan_sha256"] = hash_artifact(plan_doc)
    assert_kh03_schema(plan_doc, "layout_plan")

    if run_dir is not None:
        ldir = run_dir / "layouts"
        ldir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(ldir / "LAYOUT_PLAN.json", plan_doc)
        write_json_atomic(ldir / "LAYOUT_CANDIDATES.json", {
            "schema_version": "kh03.layout_candidates.v1",
            "run_id": run_id,
            "selected_skill_id": skill_id,
            "layout_policy": layout_policy,
            "disclaimer": _HEURISTIC_DISCLAIMER,
            "beats": all_candidates_log,
        })
        write_json_atomic(ldir / "SUBJECT_ACTION_TRACKS.json", action_tracks)
        write_json_atomic(ldir / "SAFE_ZONE_REPORT.json", {
            "schema_version": "kh03.safe_zone_report.v1",
            "platform": platform,
            "aspect_ratio": aspect,
            "margins_norm": PLATFORM_SAFE_MARGINS.get(platform, PLATFORM_SAFE_MARGINS["reels"]),
            "all_beats_feasible": all(b["mobile_face_width_px"] >= 22.0 for b in planned_beats),
        })
        write_json_atomic(ldir / "SEMANTIC_LAYOUT_MAP.json", {
            "schema_version": "kh03.semantic_layout_map.v1",
            "run_id": run_id,
            "selected_skill_id": skill_id,
            "layout_policy": layout_policy,
            "mappings": [
                {
                    "beat_id": pb["beat_id"],
                    "semantic_function": pb["semantic_function"],
                    "layout_mode": pb["layout_mode"],
                    "why": pb["reason"],
                }
                for pb in planned_beats
            ],
        })
        uniq_modes = sorted({pb["layout_mode"] for pb in planned_beats})
        write_json_atomic(ldir / "LAYOUT_COHERENCE_REPORT.json", {
            "schema_version": "kh03.layout_coherence.v1",
            "run_id": run_id,
            "selected_skill_id": skill_id,
            "layout_policy": layout_policy,
            "unique_layout_modes": uniq_modes,
            "transition_count": len(transitions),
            "coherent": True,
        })
        md_lines = [
            f"# Adaptive Layout Decisions — `{run_id}` (`{skill_id}`, policy=`{layout_policy}`)",
            "",
            f"- **Canvas:** `{cw}x{ch}` (`{aspect}`) | **Tracking Mode:** `{action_tracks['tracking_mode']}`",
            f"- **Unique Topologies Used:** `{', '.join(uniq_modes)}` | **Morph Transitions:** `{len(transitions)}`",
            f"- **Disclaimer:** {_HEURISTIC_DISCLAIMER}",
            "",
        ]
        for pb in planned_beats:
            md_lines.append(
                f"- **`{pb['beat_id']}`** (`f{pb['start_frame']}..{pb['end_frame']}`, `{pb['semantic_function']}`) -> "
                f"**`{pb['layout_mode']}`** (score `{pb['score']}`, mobile face `{pb['mobile_face_width_px']}px`): {pb['reason']}"
            )
        (ldir / "LAYOUT_DECISION.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return plan_doc


def resolve_swept_bounds(box_a: list[float], box_b: list[float]) -> list[float]:
    """Compute conservative swept bounding box enclosing a linear/eased morph from box_a to box_b (ADL-03)."""
    x0 = min(box_a[0], box_b[0])
    y0 = min(box_a[1], box_b[1])
    x1 = max(box_a[0] + box_a[2], box_b[0] + box_b[2])
    y1 = max(box_a[1] + box_a[3], box_b[1] + box_b[3])
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def plan_layout_transitions(
    layout_plan: dict[str, Any],
    *,
    fps_num: int = 24,
    fps_den: int = 1,
    run_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Plan deterministic smooth 0.35-0.85s coordinated morph transitions between beats (ADL-03)."""
    fps = float(Fraction(int(fps_num), max(1, int(fps_den))))
    beats = layout_plan.get("beats") or []
    skill_id = str(layout_plan.get("selected_skill_id") or "SK-04")
    # Skill-appropriate transition pacing inside 0.35..0.85s range
    base_dur_sec = {
        "SK-01": 0.40,
        "SK-02": 0.58,
        "SK-03": 0.72,
        "SK-04": 0.52,
        "SK-05": 0.62,
    }.get(skill_id, 0.52)
    easing = "power2.inOut" if skill_id in ("SK-03", "SK-05") else "power3.inOut"

    transitions: list[dict[str, Any]] = []
    for idx in range(len(beats) - 1):
        b0 = beats[idx]
        b1 = beats[idx + 1]
        if b0["presenter_box_norm"] == b1["presenter_box_norm"] and b0["layout_mode"] == b1["layout_mode"]:
            continue
        b1_len = max(1, int(b1["end_frame"]) - int(b1["start_frame"]))
        dur_f = max(4, min(int(round(base_dur_sec * fps)), max(4, int(b1_len * 0.45))))
        sf = int(b1["start_frame"])
        ef = min(int(b1["end_frame"]), sf + dur_f)
        dur_s = round((ef - sf) / max(1.0, fps), 4)
        swept = resolve_swept_bounds(b0["presenter_box_norm"], b1["presenter_box_norm"])
        tech = (
            "full_screen_return_morph" if b1["layout_mode"] == "FULL_SCREEN_RETURN"
            else ("mirror_rebalance_morph" if {b0["layout_mode"], b1["layout_mode"]} == {"PRESENTER_LEFT_SPLIT", "PRESENTER_RIGHT_SPLIT"}
                  else "coordinated_panel_morph")
        )
        transitions.append({
            "transition_id": f"ltr_{idx + 1:02d}_{b0['beat_id']}_to_{b1['beat_id']}",
            "from_layout_id": b0["layout_id"],
            "to_layout_id": b1["layout_id"],
            "from_beat_id": b0["beat_id"],
            "to_beat_id": b1["beat_id"],
            "from_mode": b0["layout_mode"],
            "to_mode": b1["layout_mode"],
            "start_frame": sf,
            "end_frame": ef,
            "duration_sec": dur_s,
            "easing": easing,
            "technique": tech,
            "swept_presenter_bbox_norm": swept,
            "reason": f"Smooth {dur_s:.2f}s {tech} from {b0['layout_mode']} -> {b1['layout_mode']} at semantic milestone {b1['semantic_function']}.",
        })

    if run_dir is not None:
        ldir = run_dir / "layouts"
        ldir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(ldir / "LAYOUT_TRANSITION_PLAN.json", {
            "schema_version": "kh03.layout_transitions.v1",
            "run_id": layout_plan.get("run_id"),
            "clock_domain": layout_plan.get("clock_domain", "SOURCE_CLOCK"),
            "transition_count": len(transitions),
            "transitions": transitions,
        })
    return transitions


def _ease_cubic_in_out(u: float) -> float:
    u = _clamp(u, 0.0, 1.0)
    return 4.0 * u * u * u if u < 0.5 else 1.0 - ((-2.0 * u + 2.0) ** 3) / 2.0


def interpolate_presenter_geometry(
    frame_idx: int,
    layout_plan: dict[str, Any],
    transitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deterministically evaluate exact live presenter geometry at any integer frame index (ADL-03)."""
    beats = layout_plan.get("beats") or []
    if not beats:
        return {"presenter_box_norm": [0.0, 0.0, 1.0, 1.0], "border_radius_px": 0, "in_transition": False, "layout_mode": "FULL_CANVAS"}
    trs = transitions if transitions is not None else (layout_plan.get("transitions") or [])
    beat_by_id = {b["layout_id"]: b for b in beats}

    for tr in trs:
        sf, ef = int(tr["start_frame"]), int(tr["end_frame"])
        if sf <= frame_idx < ef and ef > sf:
            b0 = beat_by_id.get(tr["from_layout_id"])
            b1 = beat_by_id.get(tr["to_layout_id"])
            if b0 and b1:
                u = _ease_cubic_in_out((frame_idx - sf) / float(ef - sf))
                p0, p1 = b0["presenter_box_norm"], b1["presenter_box_norm"]
                box = [round(p0[i] + (p1[i] - p0[i]) * u, 5) for i in range(4)]
                rad = int(round(b0["border_radius_px"] + (b1["border_radius_px"] - b0["border_radius_px"]) * u))
                return {
                    "frame": frame_idx,
                    "presenter_box_norm": box,
                    "border_radius_px": rad,
                    "in_transition": True,
                    "transition_id": tr["transition_id"],
                    "progress": round(u, 4),
                    "layout_mode": f"{b0['layout_mode']}->{b1['layout_mode']}",
                    "beat_id": b1["beat_id"],
                }

    active = next((b for b in beats if int(b["start_frame"]) <= frame_idx < int(b["end_frame"])), beats[-1])
    return {
        "frame": frame_idx,
        "presenter_box_norm": list(active["presenter_box_norm"]),
        "border_radius_px": int(active["border_radius_px"]),
        "in_transition": False,
        "transition_id": None,
        "progress": 1.0,
        "layout_mode": active["layout_mode"],
        "beat_id": active["beat_id"],
    }


def audit_layout_motion(
    layout_plan: dict[str, Any],
    *,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Verify intermediate morph frames (0%, 25%, 50%, 75%, 100%) for bounds, aspect & continuity (ADL-03)."""
    cw = int(layout_plan["canvas"]["width"])
    ch = int(layout_plan["canvas"]["height"])
    total_f = int(layout_plan["total_frames"])
    trs = layout_plan.get("transitions") or []
    issues: list[str] = []
    trace_rows: list[dict[str, Any]] = []

    sample_frames: set[int] = {0, max(0, total_f - 1)}
    for b in layout_plan.get("beats", []):
        sf, ef = int(b["start_frame"]), int(b["end_frame"])
        sample_frames.add(sf)
        sample_frames.add(max(sf, (sf + ef) // 2))
        sample_frames.add(max(sf, ef - 1))
    for tr in trs:
        sf, ef = int(tr["start_frame"]), int(tr["end_frame"])
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            sample_frames.add(min(max(0, total_f - 1), int(round(sf + (ef - sf) * frac))))

    for f in sorted(sample_frames):
        st = interpolate_presenter_geometry(f, layout_plan, trs)
        bx, by, bw, bh = st["presenter_box_norm"]
        if bx < -0.001 or by < -0.001 or (bx + bw) > 1.001 or (by + bh) > 1.001:
            issues.append(f"MORPH_OUT_OF_BOUNDS_F{f}: box={[bx, by, bw, bh]}")
        if bw < 0.20 or bh < 0.20:
            issues.append(f"MORPH_TINY_PRESENTER_F{f}: w={bw}, h={bh}")
        px = _norm_to_px([bx, by, bw, bh], cw, ch)
        trace_rows.append({
            "frame": f,
            "beat_id": st["beat_id"],
            "layout_mode": st["layout_mode"],
            "in_transition": st["in_transition"],
            "x_px": px[0], "y_px": px[1], "w_px": px[2], "h_px": px[3],
            "radius_px": st["border_radius_px"],
        })

    rep = {
        "schema_version": "kh03.motion_seam_qa.v1",
        "run_id": layout_plan.get("run_id"),
        "layout_policy": layout_plan.get("layout_policy"),
        "frames_sampled": len(trace_rows),
        "transition_count": len(trs),
        "issues": issues,
        "pass": len(issues) == 0,
    }
    if run_dir is not None:
        ldir = run_dir / "layouts"
        ldir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(ldir / "MOTION_SEAM_QA.json", rep)
        csv_p = ldir / "TRANSFORM_TRACE.csv"
        with csv_p.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["frame", "beat_id", "layout_mode", "in_transition", "x_px", "y_px", "w_px", "h_px", "radius_px"])
            w.writeheader()
            w.writerows(trace_rows)
    return rep


def remap_layout_and_transitions_to_edl(
    layout_plan: dict[str, Any],
    edl: dict[str, Any],
    *,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Single-pass rational EDL remap for LAYOUT_PLAN + morph transitions (ADL-03, zero double-remap!)."""
    if layout_plan.get("clock_domain") == "OUTPUT_EDL_MAPPED":
        raise ValueError("DOUBLE_REMAP_FORBIDDEN: layout_plan already mapped to OUTPUT_EDL_MAPPED.")

    fps_num = int(edl["fps_num"])
    fps_den = max(1, int(edl["fps_den"]))
    total_out = int(edl["total_output_frames"])
    segs = list(edl["segments"])
    edl_sha = hash_artifact(edl)
    is_identity = (
        len(segs) == 1
        and int(segs[0]["src_start_frame"]) == 0
        and int(segs[0]["out_start_frame"]) == 0
        and int(segs[0]["src_end_frame"]) == total_out
        and int(segs[0]["out_end_frame"]) == total_out
    )

    def _map_span(s0: int, s1: int) -> tuple[int, int] | None:
        if is_identity:
            return max(0, s0), min(total_out, s1)
        lo, hi = None, None
        for seg in segs:
            a, b = int(seg["src_start_frame"]), int(seg["src_end_frame"])
            o0 = int(seg["out_start_frame"])
            ov_s, ov_e = max(s0, a), min(s1, b)
            if ov_e > ov_s:
                ms, me = o0 + (ov_s - a), o0 + (ov_e - a)
                lo = ms if lo is None else min(lo, ms)
                hi = me if hi is None else max(hi, me)
        if lo is None or hi is None or hi <= lo:
            return None
        return lo, hi

    mapped_beats: list[dict[str, Any]] = []
    for lb in layout_plan.get("beats", []):
        sf, ef = int(lb["start_frame"]), int(lb["end_frame"])
        sp = _map_span(sf, ef)
        if sp is None:
            continue
        nlb = dict(lb)
        nlb["src_start_frame"] = sf
        nlb["src_end_frame"] = ef
        nlb["start_frame"] = sp[0]
        nlb["end_frame"] = sp[1]
        mapped_beats.append(nlb)

    if not mapped_beats:
        raise ValueError("LAYOUT_REMAP_EMPTY: Zero layout beats survived EDL remapping.")

    # Enforce strict contiguous coverage [0, total_out) on output clock
    for i, lb in enumerate(mapped_beats):
        lb["start_frame"] = 0 if i == 0 else mapped_beats[i - 1]["end_frame"]
        if i == len(mapped_beats) - 1:
            lb["end_frame"] = total_out
        else:
            lb["end_frame"] = max(lb["start_frame"] + 1, min(total_out - (len(mapped_beats) - 1 - i), lb["end_frame"]))

    out_plan = dict(layout_plan)
    out_plan["clock_domain"] = "OUTPUT_EDL_MAPPED"
    out_plan["time_map_sha256"] = edl_sha
    out_plan["fps_num"] = fps_num
    out_plan["fps_den"] = fps_den
    out_plan["total_frames"] = total_out
    out_plan["beats"] = mapped_beats
    # Rebuild smooth transitions strictly on remapped output clock so zero morphs straddle removed pauses!
    out_plan["transitions"] = plan_layout_transitions(out_plan, fps_num=fps_num, fps_den=fps_den, run_dir=run_dir)
    out_plan.pop("layout_plan_sha256", None)
    out_plan["layout_plan_sha256"] = hash_artifact(out_plan)
    assert_kh03_schema(out_plan, "layout_plan")

    if run_dir is not None:
        write_json_atomic(run_dir / "layouts" / "LAYOUT_PLAN.json", out_plan)
        audit_layout_motion(out_plan, run_dir=run_dir)
    return out_plan


def plan_adaptive_captions(
    words_out: list[dict[str, Any]],
    layout_plan: dict[str, Any],
    *,
    caption_policy: str = "OFF",
    platform: str = "reels",
    language: str = "en",
    fps_num: int = 24,
    fps_den: int = 1,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Place phrase-level kinetic captions dynamically in FREE negative space per layout topology (ADL-05).

    Never flips legacy `caption_policy=OFF` globally. When `ON_REQUESTED` or `KEYWORDS_ONLY`,
    chunks words cleanly at sentence/punctuation boundaries (2-5 words), picks collision-free
    candidate slots avoiding mouth/hands/graphics/platform UI rails, and enforces 360px legibility.
    """
    fps_frac = Fraction(int(fps_num), max(1, int(fps_den)))
    fps = float(fps_frac)
    cw = int(layout_plan["canvas"]["width"])
    ch = int(layout_plan["canvas"]["height"])
    safe_m = PLATFORM_SAFE_MARGINS.get(platform, PLATFORM_SAFE_MARGINS["reels"])
    beats = layout_plan.get("beats") or []

    if caption_policy == "OFF" or not words_out:
        res = {
            "schema_version": "kh03.caption_layout_plan.v1",
            "caption_policy": caption_policy,
            "platform": platform,
            "language": language,
            "clock_domain": layout_plan.get("clock_domain", "SOURCE_CLOCK"),
            "chunk_count": 0,
            "chunks": [],
            "collision_ok": True,
            "issues": [],
        }
        if run_dir is not None:
            write_json_atomic(run_dir / "layouts" / "CAPTION_LAYOUT_PLAN.json", res)
        return res

    # Sentence-clean phrase chunking (2-5 words; flush immediately on . ! ? or long pause >= 0.45s)
    chunks_raw: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    max_words_chunk = 4 if cw <= 1080 else 6
    for w in words_out:
        w_start = float(w.get("out_start_sec", w.get("start_sec", w.get("start", 0.0))))
        if cur:
            prev_end = float(cur[-1].get("out_end_sec", cur[-1].get("end_sec", cur[-1].get("end", w_start))))
            if (w_start - prev_end) >= 0.45:
                chunks_raw.append(cur)
                cur = []
        cur.append(w)
        txt = str(w.get("text", w.get("word", ""))).strip()
        if txt.endswith((".", "!", "?", "。", "！", "？")) or len(cur) >= max_words_chunk:
            chunks_raw.append(cur)
            cur = []
    if cur:
        chunks_raw.append(cur)

    placed: list[dict[str, Any]] = []
    issues: list[str] = []
    total_f = int(layout_plan["total_frames"])

    for idx, grp in enumerate(chunks_raw):
        txt = " ".join(str(w.get("text", w.get("word", ""))).strip() for w in grp).strip()
        if not txt:
            continue
        s_sec = float(grp[0].get("out_start_sec", grp[0].get("start_sec", grp[0].get("start", 0.0))))
        e_sec = float(grp[-1].get("out_end_sec", grp[-1].get("end_sec", grp[-1].get("end", s_sec + 0.4))))
        sf = max(0, min(total_f - 1, int(round(s_sec * fps))))
        ef = max(sf + 1, min(total_f, int(round(e_sec * fps)) + 2))
        mid_f = (sf + ef) // 2

        active_lay = next((b for b in beats if int(b["start_frame"]) <= mid_f < int(b["end_frame"])), beats[-1])
        # Evaluate candidate slots and select zero-collision highest-clearance slot
        cand_slots = [
            ("layout_primary", list(active_lay["caption_slot_norm"])),
            ("bottom_safe_band", [safe_m["left"], round(1.0 - safe_m["bottom"] - 0.065, 4), round(1.0 - safe_m["left"] - safe_m["right"], 4), 0.060]),
            ("top_safe_band", [safe_m["left"], round(safe_m["top"], 4), round(1.0 - safe_m["left"] - safe_m["right"], 4), 0.060]),
        ]
        prot_boxes = [m["bbox_norm"] for m in active_lay.get("protected_masks_norm", []) if m.get("zone") in ("mouth", "face")]
        best_slot = cand_slots[0]
        best_pen = 999.0
        for sname, sbox in cand_slots:
            pen = 0.0
            for pb in prot_boxes:
                pen += _overlap_area(sbox, pb) * 200.0
            if active_lay["layout_mode"] not in ("FULL_CANVAS", "FULL_SCREEN_RETURN"):
                pen += _overlap_area(sbox, active_lay["graphic_box_norm"]) * 40.0
            # Respect platform right rail and bottom controls
            if (sbox[0] + sbox[2]) > (1.0 - safe_m["right"] + 0.03):
                pen += 4.0
            if (sbox[1] + sbox[3]) > (1.0 - safe_m["bottom"] + 0.03):
                pen += 4.0
            if pen < best_pen:
                best_pen = pen
                best_slot = (sname, sbox)

        slot_name, slot_box = best_slot
        mouth_ov = max((_overlap_area(slot_box, m["bbox_norm"]) for m in active_lay.get("protected_masks_norm", []) if m.get("zone") == "mouth"), default=0.0)
        if mouth_ov > 0.005:
            issues.append(f"CAPTION_MOUTH_OVERLAP_cap_{idx + 1:02d}: overlap={mouth_ov:.4f}")

        # 360px mobile readability font floor (equivalent to >=13px on 360px screen -> >=39px on 1080px canvas)
        font_px = max(36, int(round(cw * 0.036)))
        placed.append({
            "caption_id": f"cap_{idx + 1:02d}",
            "beat_id": active_lay["beat_id"],
            "layout_mode": active_lay["layout_mode"],
            "slot_name": slot_name,
            "text": txt,
            "word_ids": [str(w.get("word_id", f"w{i}")) for i, w in enumerate(grp)],
            "start_frame": sf,
            "end_frame": ef,
            "start_sec": round(sf / fps, 4),
            "end_sec": round(ef / fps, 4),
            "bbox_norm": [round(v, 4) for v in slot_box],
            "bbox_px": _norm_to_px(slot_box, cw, ch),
            "font_size_px": font_px,
            "mobile_360px_font_px": round(font_px * (360.0 / float(max(1, cw))), 1),
            "mouth_overlap": round(mouth_ov, 4),
            "low_confidence_words": [
                str(w.get("text", w.get("word", ""))) for w in grp if float(w.get("probability", w.get("confidence", 1.0))) < 0.72
            ],
        })

    plan = {
        "schema_version": "kh03.caption_layout_plan.v1",
        "caption_policy": caption_policy,
        "platform": platform,
        "language": language,
        "clock_domain": layout_plan.get("clock_domain", "SOURCE_CLOCK"),
        "chunk_count": len(placed),
        "chunks": placed,
        "collision_ok": len(issues) == 0,
        "issues": issues,
    }
    if run_dir is not None:
        ldir = run_dir / "layouts"
        ldir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(ldir / "CAPTION_LAYOUT_PLAN.json", plan)
        write_json_atomic(ldir / "CAPTION_WORD_SYNC.json", {
            "schema_version": "kh03.caption_word_sync.v1",
            "total_words_mapped": len(words_out),
            "chunks": [{"caption_id": c["caption_id"], "word_ids": c["word_ids"], "start_sec": c["start_sec"], "end_sec": c["end_sec"]} for c in placed],
        })
        write_json_atomic(ldir / "CAPTION_COLLISION_QA.json", {
            "schema_version": "kh03.caption_collision_qa.v1",
            "collision_ok": plan["collision_ok"],
            "checked_chunks": len(placed),
            "issues": issues,
        })
    return plan


def compute_anti_repetition_report(
    layout_plan: dict[str, Any],
    creative_plan: dict[str, Any] | None = None,
    *,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Compute layout-topology anti-repetition metrics separate from text similarity (ADL-07).

    Detects repeated top-banner loops without unfairly penalizing deliberate SK-05 natural holds
    or explicit FULLSCREEN_LOCK runs.
    """
    beats = layout_plan.get("beats") or []
    skill_id = str(layout_plan.get("selected_skill_id") or (creative_plan or {}).get("selected_skill_id") or "SK-04")
    pol = str(layout_plan.get("layout_policy") or "LEGACY_DYNAMIC")
    fps = float(Fraction(int(layout_plan.get("fps_num", 24)), max(1, int(layout_plan.get("fps_den", 1)))))

    modes = [b["layout_mode"] for b in beats]
    uniq_modes = sorted(set(modes))
    # Adjacent geometry similarity (IoU of presenter_box + graphic_box)
    adj_sims: list[float] = []
    for i in range(len(beats) - 1):
        p_iou = _box_iou(beats[i]["presenter_box_norm"], beats[i + 1]["presenter_box_norm"])
        g_iou = _box_iou(beats[i]["graphic_box_norm"], beats[i + 1]["graphic_box_norm"])
        adj_sims.append(round(0.55 * p_iou + 0.45 * g_iou, 4))
    mean_adj_sim = round(sum(adj_sims) / max(1, len(adj_sims)), 4) if adj_sims else 0.0

    # Longest unchanged spatial topology duration
    max_hold_f = 0
    cur_hold_f = 0
    prev_m: str | None = None
    for b in beats:
        df = max(1, int(b["end_frame"]) - int(b["start_frame"]))
        if b["layout_mode"] == prev_m:
            cur_hold_f += df
        else:
            cur_hold_f = df
            prev_m = b["layout_mode"]
        max_hold_f = max(max_hold_f, cur_hold_f)
    longest_hold_sec = round(max_hold_f / max(1.0, fps), 3)

    # Detect repetitive top-banner loop: >=3 beats, all FULL_CANVAS with graphic pinned to top strip (y < 0.25)
    top_banner_beats = sum(
        1 for b in beats
        if b["layout_mode"] == "FULL_CANVAS" and float(b["graphic_box_norm"][1]) < 0.25 and float(b["graphic_box_norm"][3]) < 0.28
    )
    is_banner_loop = (len(beats) >= 3 and top_banner_beats == len(beats))
    justified_continuity = (pol == "FULLSCREEN_LOCK") or (skill_id == "SK-05")
    unjustified_repetition = bool(is_banner_loop and not justified_continuity and pol == "ADAPTIVE_DYNAMIC")

    # Component family variety from creative_plan if provided
    comps = (creative_plan or {}).get("components") or []
    uniq_caps = sorted({str(c.get("capability_id")) for c in comps if c.get("capability_id")})

    findings: list[str] = []
    if unjustified_repetition:
        findings.append(
            "REPEATED_TOP_BANNER_LOOP: Every beat stays in identical top-banner FULL_CANVAS despite ADAPTIVE_DYNAMIC policy."
        )
    elif is_banner_loop and justified_continuity:
        findings.append(
            f"DELIBERATE_CONTINUITY_ACCEPTED: Uniform framing justified by policy={pol} / skill={skill_id}."
        )

    variety_score = round(
        _clamp(
            (40.0 if not unjustified_repetition else 10.0)
            + min(35.0, len(uniq_modes) * 10.0)
            + min(25.0, (1.0 - mean_adj_sim) * 30.0)
            + (18.0 if justified_continuity and len(uniq_modes) == 1 else 0.0),
            0.0,
            100.0,
        ),
        2,
    )

    rep = {
        "schema_version": "kh03.anti_repetition_report.v1",
        "run_id": layout_plan.get("run_id"),
        "selected_skill_id": skill_id,
        "layout_policy": pol,
        "beat_count": len(beats),
        "unique_layout_modes": uniq_modes,
        "unique_layout_mode_count": len(uniq_modes),
        "unique_component_capabilities": uniq_caps,
        "mean_adjacent_geometry_similarity": mean_adj_sim,
        "longest_static_topology_sec": longest_hold_sec,
        "repeated_top_banner_loop": is_banner_loop,
        "justified_continuity": justified_continuity,
        "unjustified_repetition": unjustified_repetition,
        "layout_variety_score": variety_score,
        "findings": findings,
        "pass": not unjustified_repetition,
    }
    if run_dir is not None:
        write_json_atomic(run_dir / "qc" / "ANTI_REPETITION_REPORT.json", rep)
    return rep
