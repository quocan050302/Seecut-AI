"""KH-02 Virtual 2D Camera Direction & Explicit Presenter Policy Geometry (KH-02.R2).

Supports legacy LEGACY_DYNAMIC framing (default for v1 briefs) alongside explicit optional
FIXED_PIP (including BOTTOM_LEFT / BOTTOM_RIGHT / TOP_LEFT / TOP_RIGHT / CUSTOM),
ADAPTIVE_PIP, DYNAMIC_HYBRID, and FACELESS presenter policies with face-safe crop math
and reserved presenter collision bounds.
"""

from __future__ import annotations

import math
from typing import Any

from .contracts import validate_presenter_policy

# Configurable platform UI exclusion guides (explicit design assumptions, never universal guarantees)
PLATFORM_SAFE_ZONES: dict[str, dict[str, float]] = {
    "reels": {"top": 0.09, "bottom": 0.19, "left": 0.045, "right": 0.11},
    "tiktok": {"top": 0.09, "bottom": 0.21, "left": 0.045, "right": 0.12},
    "shorts": {"top": 0.09, "bottom": 0.19, "left": 0.045, "right": 0.11},
    "youtube": {"top": 0.06, "bottom": 0.11, "left": 0.04, "right": 0.04},
    "square": {"top": 0.06, "bottom": 0.10, "left": 0.045, "right": 0.045},
    "web": {"top": 0.05, "bottom": 0.09, "left": 0.04, "right": 0.04},
    "custom": {"top": 0.07, "bottom": 0.12, "left": 0.045, "right": 0.045},
}

SAFE_ZONE_PROFILE_OVERRIDES: dict[str, dict[str, float]] = {
    "REELS_SAFE": PLATFORM_SAFE_ZONES["reels"],
    "TIKTOK_SAFE": PLATFORM_SAFE_ZONES["tiktok"],
    "SHORTS_SAFE": PLATFORM_SAFE_ZONES["shorts"],
    "YOUTUBE_16_9": PLATFORM_SAFE_ZONES["youtube"],
    "MINIMAL": {"top": 0.04, "bottom": 0.06, "left": 0.04, "right": 0.04},
}


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
    return round(min(hard_cap, max(1.08, ratio * 1.15)), 3)


def normalize_presenter_policy(
    raw_policy: dict[str, Any] | None = None,
    *,
    brief_mode: str = "talking_head",
    has_presenter_source: bool = True,
) -> dict[str, Any]:
    """Normalize optional presenter_policy with deterministic v1 migration default LEGACY_DYNAMIC."""
    if raw_policy is None:
        if brief_mode == "faceless":
            return {
                "mode": "FACELESS",
                "visibility": "EDITORIAL_ALLOWED",
                "anchor": "BOTTOM_LEFT",
                "custom_box": None,
                "shape": "ROUNDED_RECT",
                "width_ratio": 0.30,
                "min_width_ratio": 0.24,
                "max_width_ratio": 0.36,
                "aspect_ratio": "SOURCE_AWARE",
                "safe_zone_profile": "PLATFORM_AWARE",
                "face_tracking": "OFF",
                "occlusion_policy": "LEGACY_CHECK_ONLY",
                "allow_fullscreen": False,
                "allow_anchor_switch": False,
                "allow_hide": True,
                "border_radius_px": 22,
                "guard_margin_ratio": 0.024,
            }
        return {
            "mode": "LEGACY_DYNAMIC",
            "visibility": "EDITORIAL_ALLOWED",
            "anchor": "TOP_LEFT",
            "custom_box": None,
            "shape": "ROUNDED_RECT",
            "width_ratio": 0.32,
            "min_width_ratio": 0.24,
            "max_width_ratio": 0.38,
            "aspect_ratio": "SOURCE_AWARE",
            "safe_zone_profile": "PLATFORM_AWARE",
            "face_tracking": "STATIC_VERIFIED",
            "occlusion_policy": "LEGACY_CHECK_ONLY",
            "allow_fullscreen": True,
            "allow_anchor_switch": True,
            "allow_hide": True,
            "border_radius_px": 22,
            "guard_margin_ratio": 0.02,
        }

    p = dict(raw_policy)
    mode = p.setdefault("mode", "LEGACY_DYNAMIC")
    if mode == "FIXED_PIP":
        p.setdefault("visibility", "ALWAYS_VISIBLE")
        p.setdefault("anchor", "BOTTOM_LEFT")
        p.setdefault("shape", "ROUNDED_RECT")
        p.setdefault("width_ratio", 0.30)
        p.setdefault("min_width_ratio", 0.24)
        p.setdefault("max_width_ratio", 0.36)
        p.setdefault("aspect_ratio", "SOURCE_AWARE")
        p.setdefault("safe_zone_profile", "PLATFORM_AWARE")
        p.setdefault("face_tracking", "AUTO_WHEN_AVAILABLE")
        p.setdefault("occlusion_policy", "RESERVE_PRESENTER_ZONE")
        p.setdefault("allow_fullscreen", False)
        p.setdefault("allow_anchor_switch", False)
        p.setdefault("allow_hide", False)
        p.setdefault("border_radius_px", 22 if p["shape"] == "ROUNDED_RECT" else 999)
        p.setdefault("guard_margin_ratio", 0.024)
    elif mode in ("ADAPTIVE_PIP", "DYNAMIC_HYBRID"):
        p.setdefault("visibility", "ALWAYS_VISIBLE" if mode == "ADAPTIVE_PIP" else "EDITORIAL_ALLOWED")
        p.setdefault("anchor", "BOTTOM_LEFT")
        p.setdefault("shape", "ROUNDED_RECT")
        p.setdefault("width_ratio", 0.30)
        p.setdefault("min_width_ratio", 0.24)
        p.setdefault("max_width_ratio", 0.36)
        p.setdefault("aspect_ratio", "SOURCE_AWARE")
        p.setdefault("safe_zone_profile", "PLATFORM_AWARE")
        p.setdefault("face_tracking", "AUTO_WHEN_AVAILABLE")
        p.setdefault("occlusion_policy", "RESERVE_PRESENTER_ZONE")
        p.setdefault("allow_fullscreen", mode == "DYNAMIC_HYBRID")
        p.setdefault("allow_anchor_switch", True)
        p.setdefault("allow_hide", p["visibility"] != "ALWAYS_VISIBLE")
        p.setdefault("border_radius_px", 22 if p["shape"] == "ROUNDED_RECT" else 999)
        p.setdefault("guard_margin_ratio", 0.024)
    elif mode == "FACELESS":
        p.setdefault("visibility", "EDITORIAL_ALLOWED")
        p.setdefault("anchor", "BOTTOM_LEFT")
        p.setdefault("shape", "ROUNDED_RECT")
        p.setdefault("width_ratio", 0.30)
        p.setdefault("min_width_ratio", 0.24)
        p.setdefault("max_width_ratio", 0.36)
        p.setdefault("aspect_ratio", "SOURCE_AWARE")
        p.setdefault("safe_zone_profile", "PLATFORM_AWARE")
        p.setdefault("face_tracking", "OFF")
        p.setdefault("occlusion_policy", "LEGACY_CHECK_ONLY")
        p.setdefault("allow_fullscreen", False)
        p.setdefault("allow_anchor_switch", False)
        p.setdefault("allow_hide", True)
        p.setdefault("border_radius_px", 22)
        p.setdefault("guard_margin_ratio", 0.02)
    else:
        p.setdefault("visibility", "EDITORIAL_ALLOWED")
        p.setdefault("anchor", "TOP_LEFT")
        p.setdefault("shape", "ROUNDED_RECT")
        p.setdefault("width_ratio", 0.32)
        p.setdefault("min_width_ratio", 0.24)
        p.setdefault("max_width_ratio", 0.38)
        p.setdefault("aspect_ratio", "SOURCE_AWARE")
        p.setdefault("safe_zone_profile", "PLATFORM_AWARE")
        p.setdefault("face_tracking", "STATIC_VERIFIED")
        p.setdefault("occlusion_policy", "LEGACY_CHECK_ONLY")
        p.setdefault("allow_fullscreen", True)
        p.setdefault("allow_anchor_switch", True)
        p.setdefault("allow_hide", True)
        p.setdefault("border_radius_px", 22)
        p.setdefault("guard_margin_ratio", 0.02)
    p.setdefault("custom_box", None)

    errs = validate_presenter_policy(p, brief_mode=brief_mode, has_presenter_source=has_presenter_source)
    if errs:
        raise ValueError(f"Invalid presenter_policy: {errs[0]['code']}: {errs[0]['message']}")
    return p


def resolve_safe_zone_for_platform(
    *,
    platform: str = "reels",
    aspect_ratio: str = "9:16",
    safe_zone_profile: str = "PLATFORM_AWARE",
) -> dict[str, float]:
    """Return explicit platform-aware UI safe zone guide."""
    if safe_zone_profile in SAFE_ZONE_PROFILE_OVERRIDES:
        return dict(SAFE_ZONE_PROFILE_OVERRIDES[safe_zone_profile])
    if aspect_ratio == "16:9":
        return dict(PLATFORM_SAFE_ZONES["youtube"])
    if aspect_ratio == "1:1":
        return dict(PLATFORM_SAFE_ZONES["square"])
    return dict(PLATFORM_SAFE_ZONES.get(platform, PLATFORM_SAFE_ZONES["custom"]))


def resolve_presenter_region(
    policy: dict[str, Any],
    *,
    output_width: int = 720,
    output_height: int = 1280,
    source_width: int = 1080,
    source_height: int = 1920,
    rotation_deg: int = 0,
    platform: str = "reels",
    aspect_ratio: str = "9:16",
    anchor_override: str | None = None,
) -> dict[str, Any]:
    """Compute exact normalized & integer pixel presenter box obeying platform safe zones & mobile visibility."""
    eff_sw, eff_sh = (source_height, source_width) if abs(int(rotation_deg)) in (90, 270) else (source_width, source_height)
    sz = resolve_safe_zone_for_platform(
        platform=platform,
        aspect_ratio=aspect_ratio,
        safe_zone_profile=str(policy.get("safe_zone_profile", "PLATFORM_AWARE")),
    )
    anchor = anchor_override or str(policy.get("anchor", "BOTTOM_LEFT"))
    shape = str(policy.get("shape", "ROUNDED_RECT"))
    is_landscape = output_width > output_height
    is_square = abs(output_width - output_height) <= 16

    if anchor == "CUSTOM" and isinstance(policy.get("custom_box"), list) and len(policy["custom_box"]) == 4:
        nx, ny, nw, nh = [float(v) for v in policy["custom_box"]]
    else:
        wr = float(policy.get("width_ratio", 0.30))
        min_wr = float(policy.get("min_width_ratio", 0.24))
        max_wr = float(policy.get("max_width_ratio", 0.36))
        if is_landscape:
            wr = min(wr, 0.25)
            min_wr = min(min_wr, wr)
        elif is_square:
            wr = min(wr, 0.28)
        # Ensure readable face box on 360px mobile viewport (>= 86px container width)
        mobile_target_wr = 88.0 / 360.0
        if not is_landscape and wr < mobile_target_wr:
            wr = min(max_wr, max(min_wr, mobile_target_wr))
        nw = round(max(min_wr, min(max_wr, wr)), 4)

        # Determine container height from shape & aspect policy without distorting source
        ar_mode = str(policy.get("aspect_ratio", "SOURCE_AWARE"))
        canvas_ratio = output_width / float(max(1, output_height))
        if shape == "CIRCLE" or ar_mode == "SQUARE_1_1":
            box_hw_ratio = 1.0  # pixel height / pixel width
        elif ar_mode == "PORTRAIT_9_16":
            box_hw_ratio = 16.0 / 9.0
        elif ar_mode == "LANDSCAPE_16_9":
            box_hw_ratio = 9.0 / 16.0
        elif ar_mode == "PORTRAIT_3_4":
            box_hw_ratio = 4.0 / 3.0
        else:
            # SOURCE_AWARE: preserve comfortable head+shoulders framing (near 4:3 portrait on portrait canvas, 4:3/1:1 on landscape)
            src_hw = eff_sh / float(max(1, eff_sw))
            if is_landscape:
                box_hw_ratio = min(1.0, max(0.68, src_hw if src_hw < 1.0 else 0.92))
            else:
                box_hw_ratio = min(1.38, max(0.95, src_hw * 0.76))

        nh = round(nw * box_hw_ratio * canvas_ratio, 4)
        max_h = max(0.14, 1.0 - sz["top"] - sz["bottom"] - 0.10)
        nh = round(min(nh, max_h), 4)
        if shape == "CIRCLE":
            # Keep exact pixel square for clean circle clip
            side_px = min(int(round(nw * output_width)), int(round(nh * output_height)))
            nw = round(side_px / float(output_width), 4)
            nh = round(side_px / float(output_height), 4)

        left_edge = max(sz["left"], 0.05)
        right_edge = max(sz["right"], 0.05)
        top_edge = max(sz["top"], 0.06)
        bot_edge = sz["bottom"]

        if anchor == "BOTTOM_LEFT":
            nx = left_edge
            ny = 1.0 - bot_edge - nh
        elif anchor == "BOTTOM_RIGHT":
            nx = 1.0 - right_edge - nw
            ny = 1.0 - bot_edge - nh
        elif anchor == "TOP_RIGHT":
            nx = 1.0 - right_edge - nw
            ny = top_edge
        else:  # TOP_LEFT
            nx = left_edge
            ny = top_edge

        nx = round(max(0.02, min(1.0 - nw - 0.02, nx)), 4)
        ny = round(max(0.02, min(1.0 - nh - 0.02, ny)), 4)

    px_x = int(round(nx * output_width))
    px_y = int(round(ny * output_height))
    px_w = max(32, int(round(nw * output_width)))
    px_h = max(32, int(round(nh * output_height)))
    mobile_w_px = round(nw * 360.0, 1)
    mobile_h_px = round((px_h / float(max(1, output_width))) * 360.0, 1)

    return {
        "anchor": anchor,
        "shape": shape,
        "border_radius_px": int(policy.get("border_radius_px", 22 if shape == "ROUNDED_RECT" else 999)),
        "normalized_box": [round(px_x / output_width, 4), round(px_y / output_height, 4), round(px_w / output_width, 4), round(px_h / output_height, 4)],
        "pixel_box": [px_x, px_y, px_w, px_h],
        "canvas_size": [output_width, output_height],
        "safe_zone_used": sz,
        "bottom_clearance_ratio": round(1.0 - ((px_y + px_h) / float(output_height)), 4),
        "mobile_360px_box_px": [mobile_w_px, mobile_h_px],
        "mobile_readable": mobile_w_px >= 82.0,
    }


def fit_face_safe_crop(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
    *,
    face_rect: list[float] | str | None = None,
    rotation_deg: int = 0,
    shape: str = "ROUNDED_RECT",
    border_radius_px: int = 22,
    extra_zoom: float = 1.0,
    face_tracking_policy: str = "AUTO_WHEN_AVAILABLE",
) -> dict[str, Any]:
    """Compute exact source->PIP cover crop keeping face + mouth + headroom strictly inside clipped inner bounds."""
    eff_sw, eff_sh = (source_height, source_width) if abs(int(rotation_deg)) in (90, 270) else (source_width, source_height)
    eff_sw = max(1, int(eff_sw))
    eff_sh = max(1, int(eff_sh))
    tw = max(1, int(target_width))
    th = max(1, int(target_height))

    has_rect = isinstance(face_rect, list) and len(face_rect) == 4
    fx0, fy0, fx1, fy1 = [float(v) for v in face_rect] if has_rect else (0.24, 0.12, 0.76, 0.54)
    fcx = max(0.10, min(0.90, (fx0 + fx1) / 2.0))
    fcy = max(0.10, min(0.85, (fy0 + fy1) / 2.0))
    # Include chin/mouth (+0.04) and headroom (-0.04)
    req_x0 = max(0.0, fx0 - 0.03)
    req_x1 = min(1.0, fx1 + 0.03)
    req_y0 = max(0.0, fy0 - 0.04)
    req_y1 = min(1.0, fy1 + 0.05)
    req_w = max(0.05, req_x1 - req_x0)
    req_h = max(0.05, req_y1 - req_y0)

    # object-fit: cover visible fraction of source before extra zoom
    src_ar = eff_sw / float(eff_sh)
    tgt_ar = tw / float(th)
    if src_ar > tgt_ar:
        # Source is wider than container -> horizontal crop
        vis_w_base = tgt_ar / src_ar
        vis_h_base = 1.0
    else:
        # Source is taller than container -> vertical crop
        vis_w_base = 1.0
        vis_h_base = src_ar / tgt_ar

    # Cap zoom by pixel headroom AND face/mouth containment inside shape inset
    res_cap = compute_max_safe_zoom(eff_sw, eff_sh, tw, th, hard_cap=1.45)
    inner_margin = 0.14 if shape == "CIRCLE" else 0.05
    usable_frac = max(0.55, 1.0 - 2.0 * inner_margin)
    face_zoom_cap = min(
        (vis_w_base * usable_frac) / req_w,
        (vis_h_base * usable_frac) / req_h,
    )
    safe_zoom = round(max(1.0, min(extra_zoom, res_cap, max(1.0, face_zoom_cap))), 3)

    vis_w = min(1.0, vis_w_base / safe_zoom)
    vis_h = min(1.0, vis_h_base / safe_zoom)

    # Center visible source crop around face center (slightly biased downward to preserve chin/shoulders)
    crop_cx = fcx
    crop_cy = min(0.85, fcy + 0.025)
    sx0 = max(0.0, min(1.0 - vis_w, crop_cx - vis_w / 2.0))
    sy0 = max(0.0, min(1.0 - vis_h, crop_cy - vis_h / 2.0))
    sx1 = sx0 + vis_w
    sy1 = sy0 + vis_h

    # CSS object-position percentages corresponding to base cover crop
    obj_pos_x = 50.0 if vis_w_base >= 0.999 else max(0.0, min(100.0, ((crop_cx - vis_w_base / 2.0) / max(1e-6, 1.0 - vis_w_base)) * 100.0))
    obj_pos_y = 50.0 if vis_h_base >= 0.999 else max(0.0, min(100.0, ((crop_cy - vis_h_base / 2.0) / max(1e-6, 1.0 - vis_h_base)) * 100.0))

    # Map required face+mouth+headroom box into container-normalized [0..1] coordinates
    in_x0 = (req_x0 - sx0) / max(1e-6, vis_w)
    in_y0 = (req_y0 - sy0) / max(1e-6, vis_h)
    in_x1 = (req_x1 - sx0) / max(1e-6, vis_w)
    in_y1 = (req_y1 - sy0) / max(1e-6, vis_h)

    face_clipped = (in_x0 < 0.0 - 1e-4 or in_y0 < 0.0 - 1e-4 or in_x1 > 1.0 + 1e-4 or in_y1 > 1.0 + 1e-4)
    circle_clipped = False
    if shape == "CIRCLE":
        # Check all 4 corners of face rect lie within inscribed circle radius <= 0.52 (slight soft tolerance for forehead corners)
        for px_c in (in_x0, in_x1):
            for py_c in (in_y0, in_y1):
                if math.hypot(px_c - 0.5, py_c - 0.5) > 0.52:
                    circle_clipped = True

    tracking_status = (
        "FALLBACK_STATIC_VERIFIED" if has_rect
        else ("TRACKING_OFF" if face_tracking_policy == "OFF" else "TRACKING_UNAVAILABLE_DEFAULT_RECT")
    )

    return {
        "source_effective_size": [eff_sw, eff_sh],
        "target_size": [tw, th],
        "source_crop_norm": [round(sx0, 4), round(sy0, 4), round(sx1, 4), round(sy1, 4)],
        "object_position": f"{obj_pos_x:.1f}% {obj_pos_y:.1f}%",
        "transform_origin": f"{fcx * 100.0:.1f}% {fcy * 100.0:.1f}%",
        "scale": safe_zoom,
        "max_safe_zoom": res_cap,
        "face_in_container_norm": [round(max(0.0, in_x0), 4), round(max(0.0, in_y0), 4), round(min(1.0, in_x1), 4), round(min(1.0, in_y1), 4)],
        "face_safe": (not face_clipped) and (not circle_clipped),
        "face_clipped": face_clipped,
        "circle_clipped": circle_clipped,
        "tracking_status": tracking_status,
        "border_radius_px": border_radius_px,
    }


def compute_presenter_reserved_region(
    presenter_region: dict[str, Any],
    *,
    guard_margin_ratio: float = 0.024,
) -> list[float]:
    """Return normalized [x, y, w, h] exclusion box protecting presenter PIP + guard margin."""
    bx, by, bw, bh = [float(v) for v in presenter_region["normalized_box"]]
    m = max(0.0, float(guard_margin_ratio))
    rx0 = max(0.0, bx - m)
    ry0 = max(0.0, by - m)
    rx1 = min(1.0, bx + bw + m)
    ry1 = min(1.0, by + bh + m)
    return [round(rx0, 4), round(ry0, 4), round(rx1 - rx0, 4), round(ry1 - ry0, 4)]


def choose_framing(
    beat: dict[str, Any],
    *,
    mode: str = "talking_head",
    face_rect: list[float] | str | None = None,
    has_real_evidence: bool = False,
    presenter_policy: dict[str, Any] | None = None,
    output_width: int = 720,
    output_height: int = 1280,
    source_width: int = 1080,
    source_height: int = 1920,
    rotation_deg: int = 0,
    platform: str = "reels",
    aspect_ratio: str = "9:16",
) -> dict[str, Any]:
    """Choose semantic 2D framing honoring explicit presenter_policy or legacy dynamic default."""
    pol_mode = (presenter_policy or {}).get("mode", "LEGACY_DYNAMIC")
    if mode == "faceless" or pol_mode == "FACELESS":
        return {
            "framing": "object_focus_full",
            "target_region": [0.0, 0.0, 1.0, 1.0],
            "anchor": [0.5, 0.5],
            "scale_from": 1.0,
            "scale_to": 1.04,
            "presenter_opacity": 0.0,
            "presenter_z_order": 10,
            "reason": "Faceless mode focuses on focal visual subject without mandatory presenter frame.",
        }

    fn = beat.get("semantic_function", "hook")
    vm = beat.get("visual_mode", "")
    if isinstance(face_rect, list) and len(face_rect) == 4:
        ax = round((face_rect[0] + face_rect[2]) / 2.0, 3)
        ay = round((face_rect[1] + face_rect[3]) / 2.0, 3)
    else:
        ax, ay = 0.5, 0.36

    # Explicit FIXED_PIP: constant box & opacity 1.0 across EVERY beat (overrides hook fullscreen preference)
    if pol_mode == "FIXED_PIP" and presenter_policy is not None:
        reg = resolve_presenter_region(
            presenter_policy,
            output_width=output_width,
            output_height=output_height,
            source_width=source_width,
            source_height=source_height,
            rotation_deg=rotation_deg,
            platform=platform,
            aspect_ratio=aspect_ratio,
        )
        crop = fit_face_safe_crop(
            source_width, source_height, reg["pixel_box"][2], reg["pixel_box"][3],
            face_rect=face_rect,
            rotation_deg=rotation_deg,
            shape=reg["shape"],
            border_radius_px=reg["border_radius_px"],
            extra_zoom=1.12,
            face_tracking_policy=str(presenter_policy.get("face_tracking", "AUTO_WHEN_AVAILABLE")),
        )
        return {
            "framing": f"fixed_pip_{reg['anchor'].lower()}",
            "target_region": reg["normalized_box"],
            "anchor": [ax, ay],
            "scale_from": crop["scale"],
            "scale_to": crop["scale"],
            "presenter_opacity": 1.0,
            "presenter_shape": reg["shape"],
            "presenter_z_order": 200,
            "presenter_crop": crop,
            "main_canvas_scale_from": 1.0,
            "main_canvas_scale_to": 1.03 if fn in ("hook", "evidence", "proof", "payoff") else 1.015,
            "reason": f"FIXED_PIP ({reg['anchor']}, ALWAYS_VISIBLE) keeps presenter constant while main canvas drives {fn}.",
        }

    # Explicit ADAPTIVE_PIP: may shift anchor only if allow_anchor_switch=True
    if pol_mode == "ADAPTIVE_PIP" and presenter_policy is not None:
        anchor_use = str(presenter_policy.get("anchor", "BOTTOM_LEFT"))
        if presenter_policy.get("allow_anchor_switch") and fn in ("comparison", "demonstration", "step"):
            anchor_use = "BOTTOM_RIGHT" if anchor_use == "BOTTOM_LEFT" else "BOTTOM_LEFT"
        reg = resolve_presenter_region(
            presenter_policy,
            output_width=output_width,
            output_height=output_height,
            source_width=source_width,
            source_height=source_height,
            rotation_deg=rotation_deg,
            platform=platform,
            aspect_ratio=aspect_ratio,
            anchor_override=anchor_use,
        )
        crop = fit_face_safe_crop(
            source_width, source_height, reg["pixel_box"][2], reg["pixel_box"][3],
            face_rect=face_rect, rotation_deg=rotation_deg, shape=reg["shape"], extra_zoom=1.08,
        )
        return {
            "framing": f"adaptive_pip_{reg['anchor'].lower()}",
            "target_region": reg["normalized_box"],
            "anchor": [ax, ay],
            "scale_from": crop["scale"],
            "scale_to": crop["scale"],
            "presenter_opacity": 1.0,
            "presenter_shape": reg["shape"],
            "presenter_z_order": 200,
            "presenter_crop": crop,
            "reason": f"ADAPTIVE_PIP positioned at {reg['anchor']} for {fn}.",
        }

    # Explicit DYNAMIC_HYBRID: full presenter on hook/payoff when allowed, PIP on proof/steps; never hides if ALWAYS_VISIBLE
    if pol_mode == "DYNAMIC_HYBRID" and presenter_policy is not None:
        if presenter_policy.get("allow_fullscreen", True) and fn in ("hook", "payoff", "cta", "reaction"):
            return {
                "framing": "full_push_in",
                "target_region": [0.0, 0.0, 1.0, 1.0],
                "anchor": [ax, ay],
                "scale_from": 1.0,
                "scale_to": 1.05,
                "presenter_opacity": 1.0,
                "presenter_shape": "ROUNDED_RECT",
                "presenter_z_order": 200 if presenter_policy.get("visibility") == "ALWAYS_VISIBLE" else 10,
                "reason": f"DYNAMIC_HYBRID full-frame presenter emphasis on {fn}.",
            }
        reg = resolve_presenter_region(
            presenter_policy,
            output_width=output_width, output_height=output_height,
            source_width=source_width, source_height=source_height,
            rotation_deg=rotation_deg, platform=platform, aspect_ratio=aspect_ratio,
        )
        crop = fit_face_safe_crop(
            source_width, source_height, reg["pixel_box"][2], reg["pixel_box"][3],
            face_rect=face_rect, rotation_deg=rotation_deg, shape=reg["shape"], extra_zoom=1.08,
        )
        return {
            "framing": f"hybrid_pip_{reg['anchor'].lower()}",
            "target_region": reg["normalized_box"],
            "anchor": [ax, ay],
            "scale_from": crop["scale"],
            "scale_to": crop["scale"],
            "presenter_opacity": 1.0,
            "presenter_shape": reg["shape"],
            "presenter_z_order": 200,
            "presenter_crop": crop,
            "reason": f"DYNAMIC_HYBRID PIP at {reg['anchor']} yielding main stage to {fn}.",
        }

    # Explicit FULLSCREEN_9_16 / fullscreen_lock: uniform full canvas from frame 0 through EOF (zero PIP/zoom/crop/fade)
    if presenter_policy is not None and bool(presenter_policy.get("fullscreen_lock")):
        return {
            "framing": "full_medium",
            "target_region": [0.0, 0.0, 1.0, 1.0],
            "anchor": [0.5, 0.5],
            "scale_from": 1.0,
            "scale_to": 1.0,
            "presenter_opacity": 1.0,
            "presenter_shape": "ROUNDED_RECT",
            "presenter_z_order": 10,
            "reason": f"FULLSCREEN_LOCK active: uniform 1.0x full-canvas framing across {fn} (zero PIP, zoom, pan, or fade).",
        }

    # LEGACY_DYNAMIC (preserves exact v1/R1 framing defaults)
    if "pip_bottom_left" in vm:
        return {
            "framing": "pip_bottom_left",
            "target_region": [0.055, 0.585, 0.30, 0.225],
            "anchor": [ax, ay],
            "scale_from": 1.0,
            "scale_to": 1.02,
            "reason": "Legacy explicit bottom-left PIP framing.",
        }
    if "pip_bottom_right" in vm:
        return {
            "framing": "pip_bottom_right",
            "target_region": [0.59, 0.585, 0.30, 0.225],
            "anchor": [ax, ay],
            "scale_from": 1.0,
            "scale_to": 1.02,
            "reason": "Legacy explicit bottom-right PIP framing.",
        }
    if "pip_top_right" in vm:
        return {
            "framing": "pip_top_right",
            "target_region": [0.62, 0.06, 0.32, 0.24],
            "anchor": [ax, ay],
            "scale_from": 1.0,
            "scale_to": 1.02,
            "reason": "Yield center/left frame to visual showcase while keeping presenter visible.",
        }
    if "pip" in vm or (has_real_evidence and fn in ("evidence", "proof", "step", "comparison", "mechanism")):
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
    presenter_policy: dict[str, Any] | None = None,
    rotation_deg: int = 0,
    platform: str = "reels",
    aspect_ratio: str = "9:16",
    adaptive_layout_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build continuous, resolution-capped 2D virtual camera keyframes across beats."""
    max_zoom = compute_max_safe_zoom(source_width, source_height, output_width, output_height)
    pol_mode = (presenter_policy or {}).get("mode", "LEGACY_DYNAMIC")
    fs_locked = bool(presenter_policy and presenter_policy.get("fullscreen_lock"))

    # KH-03.R2 ADL-01/03: Authoritative ADAPTIVE_DYNAMIC layout sequence (never overrides explicit fullscreen_lock or FIXED_PIP!)
    if (
        adaptive_layout_plan is not None
        and not fs_locked
        and pol_mode not in ("FIXED_PIP", "ADAPTIVE_PIP", "FACELESS")
        and adaptive_layout_plan.get("layout_policy") == "ADAPTIVE_DYNAMIC"
        and adaptive_layout_plan.get("beats")
    ):
        lay_by_beat = {lb["beat_id"]: lb for lb in adaptive_layout_plan["beats"]}
        tr_by_to = {tr["to_beat_id"]: tr for tr in adaptive_layout_plan.get("transitions", [])}
        events_ad: list[dict[str, Any]] = []
        prev_fr = "full_canvas"
        for idx, b in enumerate(beats):
            lb = lay_by_beat.get(b["beat_id"]) or adaptive_layout_plan["beats"][min(idx, len(adaptive_layout_plan["beats"]) - 1)]
            tr = tr_by_to.get(b["beat_id"])
            lmode = str(lb["layout_mode"]).lower()
            crop = dict(lb.get("source_crop") or {})
            crop["border_radius_px"] = int(lb.get("border_radius_px", 0))
            cam_ev_ad: dict[str, Any] = {
                "camera_id": f"cam_{idx + 1:02d}",
                "beat_id": b["beat_id"],
                "start_frame": int(b["start_frame"]),
                "end_frame": int(b["end_frame"]),
                "target_region": list(lb["presenter_box_norm"]),
                "framing_from": prev_fr if idx > 0 else lmode,
                "framing_to": lmode,
                "scale_from": 1.0,
                "scale_to": 1.0,
                "pos_from": [0.0, 0.0],
                "pos_to": [0.0, 0.0],
                "anchor": list(lb.get("anchor") or [0.5, 0.36]),
                "easing": str((tr or {}).get("easing") or "power3.inOut"),
                "velocity_limit": 0.45,
                "acceleration_limit": 0.95,
                "continuity_group": "cg_adaptive_choreography",
                "reason": str(lb.get("reason") or f"ADAPTIVE_DYNAMIC {lmode}"),
                "source_resolution_limit": max_zoom,
                "presenter_opacity": float(lb.get("presenter_opacity", 1.0)),
                "presenter_shape": "ROUNDED_RECT",
                "presenter_z_order": int(lb.get("presenter_z_order", 20)),
                "presenter_crop": crop,
            }
            if tr is not None:
                cam_ev_ad["reframe_duration_sec"] = float(tr.get("duration_sec", 0.52))
                cam_ev_ad["transition_id"] = str(tr.get("transition_id", ""))
            events_ad.append(cam_ev_ad)
            prev_fr = lmode
        return events_ad

    events: list[dict[str, Any]] = []
    prev_scale = 1.0
    prev_framing = "full_medium"
    prev_pos = [0.0, 0.0]

    for idx, b in enumerate(beats):
        has_ev = b.get("proof_status") == "VERIFIED_REAL_EVIDENCE"
        fr = choose_framing(
            b,
            mode=mode,
            face_rect=face_rect,
            has_real_evidence=has_ev,
            presenter_policy=presenter_policy,
            output_width=output_width,
            output_height=output_height,
            source_width=source_width,
            source_height=source_height,
            rotation_deg=rotation_deg,
            platform=platform,
            aspect_ratio=aspect_ratio,
        )
        if presenter_policy and bool(presenter_policy.get("fullscreen_lock")):
            sc_from = 1.0
            sc_to = 1.0
            pos_from = [0.0, 0.0]
            pos_to = [0.0, 0.0]
            res_lim = max_zoom
        elif pol_mode in ("FIXED_PIP", "ADAPTIVE_PIP") and fr.get("presenter_crop"):
            crop = fr["presenter_crop"]
            sc_from = float(crop["scale"])
            sc_to = float(crop["scale"])
            pos_from = [0.0, 0.0]
            pos_to = [0.0, 0.0]
            res_lim = max(max_zoom, float(crop["max_safe_zoom"]))
        else:
            sc_from = min(max_zoom, max(1.0, prev_scale if idx > 0 else fr["scale_from"]))
            sc_to = min(max_zoom, max(1.0, fr["scale_to"]))
            max_dx = max(0.0, (sc_to - 1.0) / (2.0 * sc_to))
            max_dy = max(0.0, (sc_to - 1.0) / (2.0 * sc_to))
            target_pos = [0.0, round(-min(0.015, max_dy * 0.8), 4)] if sc_to > 1.01 else [0.0, 0.0]
            pos_from = prev_pos if idx > 0 else [0.0, 0.0]
            pos_to = [max(-max_dx, min(max_dx, target_pos[0])), max(-max_dy, min(max_dy, target_pos[1]))]
            res_lim = max_zoom

        cam_ev: dict[str, Any] = {
            "camera_id": f"cam_{idx + 1:02d}",
            "beat_id": b["beat_id"],
            "start_frame": int(b["start_frame"]),
            "end_frame": int(b["end_frame"]),
            "target_region": fr["target_region"],
            "framing_from": prev_framing if idx > 0 else fr["framing"],
            "framing_to": fr["framing"],
            "scale_from": round(sc_from, 3),
            "scale_to": round(sc_to, 3),
            "pos_from": pos_from,
            "pos_to": pos_to,
            "anchor": fr["anchor"],
            "easing": "power2.inOut",
            "velocity_limit": 0.35,
            "acceleration_limit": 0.85,
            "continuity_group": "cg_presenter_fixed" if pol_mode == "FIXED_PIP" else "cg_main",
            "reason": fr["reason"],
            "source_resolution_limit": res_lim,
        }
        if "presenter_opacity" in fr:
            cam_ev["presenter_opacity"] = float(fr["presenter_opacity"])
        if "presenter_shape" in fr:
            cam_ev["presenter_shape"] = str(fr["presenter_shape"])
        if "presenter_z_order" in fr:
            cam_ev["presenter_z_order"] = int(fr["presenter_z_order"])
        if "presenter_crop" in fr:
            cam_ev["presenter_crop"] = fr["presenter_crop"]
        if "main_canvas_scale_from" in fr:
            cam_ev["main_canvas_scale_from"] = float(fr["main_canvas_scale_from"])
            cam_ev["main_canvas_scale_to"] = float(fr["main_canvas_scale_to"])
        events.append(cam_ev)
        prev_scale = sc_to
        prev_framing = fr["framing"]
        prev_pos = pos_to

    return events


def validate_presenter_path(
    camera_events: list[dict[str, Any]],
    presenter_policy: dict[str, Any] | None,
    *,
    face_rect: list[float] | str | None = None,
    total_frames: int | None = None,
    fps: float = 24.0,
    has_presenter_source: bool = True,
) -> list[dict[str, Any]]:
    """Validate presenter policy compliance across all beats: constant geometry, visibility, face crop & coverage."""
    if not presenter_policy:
        return []
    pol_mode = presenter_policy.get("mode", "LEGACY_DYNAMIC")
    vis = presenter_policy.get("visibility", "EDITORIAL_ALLOWED")
    issues: list[dict[str, Any]] = []

    if pol_mode in ("LEGACY_DYNAMIC", "FACELESS"):
        return issues

    if not has_presenter_source:
        issues.append({
            "severity": "P0",
            "camera_id": "presenter_policy",
            "code": "BLOCKED_PRESENTER_SOURCE",
            "message": f"Presenter mode {pol_mode} requires a continuous usable presenter source",
        })
        return issues

    ordered = sorted(camera_events, key=lambda c: (int(c["start_frame"]), c["camera_id"]))
    if not ordered:
        issues.append({
            "severity": "P0",
            "camera_id": "presenter_policy",
            "code": "PRESENTER_EMPTY_CAMERA_PATH",
            "message": "Non-faceless presenter mode requires camera_events covering [0, total_frames)",
        })
        return issues

    # Verify full contiguous coverage from frame 0 to total_frames when ALWAYS_VISIBLE
    if vis == "ALWAYS_VISIBLE":
        if int(ordered[0]["start_frame"]) != 0:
            issues.append({
                "severity": "P0",
                "camera_id": ordered[0]["camera_id"],
                "code": "PRESENTER_MISSING_FRAME_ZERO",
                "message": f"ALWAYS_VISIBLE presenter starts at frame {ordered[0]['start_frame']} instead of 0",
            })
        if total_frames is not None and int(ordered[-1]["end_frame"]) != int(total_frames):
            issues.append({
                "severity": "P0",
                "camera_id": ordered[-1]["camera_id"],
                "code": "PRESENTER_MISSING_EOF",
                "message": f"ALWAYS_VISIBLE presenter ends at {ordered[-1]['end_frame']} != total_frames {total_frames}",
            })
        for idx in range(len(ordered) - 1):
            if int(ordered[idx]["end_frame"]) != int(ordered[idx + 1]["start_frame"]):
                issues.append({
                    "severity": "P0",
                    "camera_id": ordered[idx + 1]["camera_id"],
                    "code": "PRESENTER_COVERAGE_GAP",
                    "message": f"Gap between {ordered[idx]['end_frame']} and {ordered[idx + 1]['start_frame']}",
                })

    ref_region = ordered[0]["target_region"]
    for idx, c in enumerate(ordered):
        cid = c["camera_id"]
        reg = c["target_region"]
        op = float(c.get("presenter_opacity", 0.0 if c.get("framing_to") == "hidden_for_evidence" else 1.0))

        if vis == "ALWAYS_VISIBLE" and (op < 0.99 or c.get("framing_to") == "hidden_for_evidence"):
            issues.append({
                "severity": "P0",
                "camera_id": cid,
                "code": "PRESENTER_HIDDEN_VIOLATION",
                "message": f"ALWAYS_VISIBLE violated at {cid} (opacity={op}, framing={c.get('framing_to')})",
            })

        if pol_mode == "FIXED_PIP":
            if reg == [0.0, 0.0, 1.0, 1.0] or (reg[2] >= 0.90 and reg[3] >= 0.90):
                issues.append({
                    "severity": "P0",
                    "camera_id": cid,
                    "code": "PRESENTER_FULLSCREEN_FORBIDDEN",
                    "message": f"FIXED_PIP cannot switch to fullscreen region {reg}",
                })
            if any(abs(float(reg[k]) - float(ref_region[k])) > 1e-3 for k in range(4)):
                issues.append({
                    "severity": "P0",
                    "camera_id": cid,
                    "code": "PRESENTER_REGION_DRIFT",
                    "message": f"FIXED_PIP region drifted from {ref_region} to {reg} at {cid}",
                })

        if pol_mode == "ADAPTIVE_PIP" and not presenter_policy.get("allow_anchor_switch", False):
            if any(abs(float(reg[k]) - float(ref_region[k])) > 1e-3 for k in range(4)):
                issues.append({
                    "severity": "P0",
                    "camera_id": cid,
                    "code": "PRESENTER_UNAUTHORIZED_ANCHOR_SWITCH",
                    "message": f"ADAPTIVE_PIP moved anchor without allow_anchor_switch=true at {cid}",
                })

        # Verify PIP crop safety when crop metadata exists
        crop = c.get("presenter_crop")
        if isinstance(crop, dict) and crop.get("face_safe") is False:
            issues.append({
                "severity": "P0",
                "camera_id": cid,
                "code": "PRESENTER_PIP_FACE_CLIPPED",
                "message": f"PIP crop clips face/mouth/headroom at {cid}: {crop.get('face_in_container_norm')}",
            })
    return issues


def validate_camera_continuity(
    camera_events: list[dict[str, Any]],
    *,
    face_rect: list[float] | str | None = None,
    fps: float = 24.0,
    intentional_cut_frames: set[int] | None = None,
    presenter_policy: dict[str, Any] | None = None,
    total_frames: int | None = None,
) -> list[dict[str, Any]]:
    """Validate zoom resolution caps, zero black-edge overscan, face crop safety, presenter policy, and boundary continuity."""
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
        for tag, sc, pos in (("start", s0, c.get("pos_from", [0.0, 0.0])), ("end", s1, c.get("pos_to", [0.0, 0.0]))):
            margin = max(0.0, (sc - 1.0) / (2.0 * max(sc, 1.0))) + 1e-4
            if abs(float(pos[0])) > margin or abs(float(pos[1])) > margin:
                issues.append({
                    "severity": "P0",
                    "camera_id": cid,
                    "code": "CAMERA_PAN_BLACK_EDGE",
                    "message": f"Pan offset {pos} at {tag} exceeds safe margin {margin:.4f} for scale={sc}",
                })

        # Full-viewport face crop check
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

        dur_sec = max(1 / fps, (int(c["end_frame"]) - int(c["start_frame"])) / fps)
        vel = abs(s1 - s0) / dur_sec
        if vel > float(c.get("velocity_limit", 0.5)) + 1e-6:
            issues.append({
                "severity": "P1",
                "camera_id": cid,
                "code": "CAMERA_VELOCITY_EXCESS",
                "message": f"Zoom velocity {vel:.3f}/s exceeds limit {c.get('velocity_limit')}",
            })

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

    if presenter_policy:
        issues.extend(validate_presenter_path(
            ordered, presenter_policy, face_rect=face_rect, total_frames=total_frames, fps=fps,
        ))
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
    presenter_policy: dict[str, Any] | None = None,
    platform: str = "reels",
    aspect_ratio: str = "9:16",
) -> list[dict[str, Any]]:
    """R1/R2 rotation-aware virtual 2D camera path builder honoring source pixel coverage, face safety & presenter policy."""
    eff_w, eff_h = (source_height, source_width) if abs(int(rotation_deg)) in (90, 270) else (source_width, source_height)
    return plan_camera_path(
        beats,
        mode=mode,
        source_width=eff_w,
        source_height=eff_h,
        output_width=output_width,
        output_height=output_height,
        face_rect=face_rect,
        presenter_policy=presenter_policy,
        rotation_deg=rotation_deg,
        platform=platform,
        aspect_ratio=aspect_ratio,
    )
