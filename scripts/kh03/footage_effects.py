"""DX01-04 & DX01-05 — Universal N-Layer Compositor, Direct Footage Effects & True Dual-Moving Transitions.

100% clean-room (Python stdlib + Pillow + NumPy only; ZERO YeJe-cpu/SeeCut PolyForm code/assets)
and 100% cross-niche universal (works on 9:16, 16:9, 1:1, 4:5, N sources, talking-head, silent,
screencast, product, documentary, faceless, mixed):
- True N-layer canvas transforms: position, anchor, scale INCLUDING `< 1.0` pullback-to-canvas,
  rotation, opacity, crop, fit (cover/contain/fill), border radius, soft shadow, 2.5D perspective
  tilt, z-order, and animated keyframe interpolation.
- Direct footage effects: subject-safe push/pull zoom, canvas pullback, 2.5D perspective tilt,
  restrained exposure/contrast/saturation/vignette grade, and real dual-source before/after split wipe.
- True dual-moving transitions: evaluates BOTH outgoing and incoming moving streams at live source PTS
  with explicit handle validation (`dual_crossfade`, `mask_wipe`, `whip_blur`, `match_reveal`, `cut`) —
  NEVER silently freezes the outgoing frame!
- Dialogue-safe speed ramp helper (strictly forbidden on MASTER_VOICE tracks).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .motion_components import ease_in_out_cubic, ease_out_cubic, parse_hex_rgba, render_luxury_backdrop


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def interpolate_keyframes(keyframes: list[dict[str, Any]] | None, t_sec: float, defaults: dict[str, float]) -> dict[str, float]:
    """Deterministically interpolate numeric transform properties across sorted keyframes."""
    out = dict(defaults)
    if not keyframes:
        return out
    kfs = sorted(keyframes, key=lambda k: float(k.get("t_sec", 0.0)))
    if t_sec <= float(kfs[0].get("t_sec", 0.0)):
        for k in defaults:
            if k in kfs[0] and kfs[0][k] is not None:
                out[k] = float(kfs[0][k])
        return out
    if t_sec >= float(kfs[-1].get("t_sec", 0.0)):
        for k in defaults:
            if k in kfs[-1] and kfs[-1][k] is not None:
                out[k] = float(kfs[-1][k])
        return out
    for i in range(len(kfs) - 1):
        t0 = float(kfs[i].get("t_sec", 0.0))
        t1 = float(kfs[i + 1].get("t_sec", 0.0))
        if t0 <= t_sec <= t1:
            span = max(1e-6, t1 - t0)
            raw_u = (t_sec - t0) / span
            ease = str(kfs[i + 1].get("easing") or "cubic_in_out").lower()
            u = raw_u if ease == "linear" else (ease_out_cubic(raw_u) if ease == "cubic_out" else ease_in_out_cubic(raw_u))
            for k in defaults:
                v0 = float(kfs[i].get(k, defaults[k]))
                v1 = float(kfs[i + 1].get(k, v0))
                out[k] = v0 + (v1 - v0) * u
            return out
    return out


def _fit_crop_image(
    img: Image.Image,
    target_w: int,
    target_h: int,
    *,
    fit_mode: str = "cover",
    cx_norm: float = 0.5,
    cy_norm: float = 0.5,
    crop_rect_norm: list[float] | None = None,
    zoom_scale: float = 1.0,
) -> Image.Image:
    """Aspect-preserving cover/contain/crop resize (zero distortion!)."""
    tw = max(4, int(target_w))
    th = max(4, int(target_h))
    src = img
    sw, sh = src.size
    if crop_rect_norm and len(crop_rect_norm) == 4:
        rx, ry, rw, rh = [float(x) for x in crop_rect_norm]
        x0 = max(0, min(sw - 4, int(round(rx * sw))))
        y0 = max(0, min(sh - 4, int(round(ry * sh))))
        x1 = max(x0 + 4, min(sw, int(round((rx + rw) * sw))))
        y1 = max(y0 + 4, min(sh, int(round((ry + rh) * sh))))
        src = src.crop((x0, y0, x1, y1))
        sw, sh = src.size

    if zoom_scale > 1.0005:
        cw = max(8.0, sw / zoom_scale)
        ch = max(8.0, sh / zoom_scale)
        x0 = _clamp(cx_norm * sw - cw * 0.5, 0.0, max(0.0, sw - cw))
        y0 = _clamp(cy_norm * sh - ch * 0.5, 0.0, max(0.0, sh - ch))
        src = src.crop((x0, y0, x0 + cw, y0 + ch))
        sw, sh = src.size

    mode = str(fit_mode or "cover").lower()
    if mode == "fill":
        return src.resize((tw, th), Image.Resampling.LANCZOS)

    src_ar = sw / float(max(1, sh))
    dst_ar = tw / float(max(1, th))
    if mode == "contain":
        if src_ar >= dst_ar:
            nw = tw
            nh = max(2, int(round(tw / src_ar)))
        else:
            nh = th
            nw = max(2, int(round(th * src_ar)))
        resized = src.resize((nw, nh), Image.Resampling.LANCZOS).convert("RGBA")
        canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        canvas.paste(resized, ((tw - nw) // 2, (th - nh) // 2))
        return canvas

    # Default: cover (aspect-preserving, zero stretch!)
    if src_ar > dst_ar:
        cw = sh * dst_ar
        x0 = _clamp(cx_norm * sw - cw * 0.5, 0.0, max(0.0, sw - cw))
        src = src.crop((x0, 0, x0 + cw, sh))
    elif src_ar < dst_ar:
        ch = sw / dst_ar
        y0 = _clamp(cy_norm * sh - ch * 0.5, 0.0, max(0.0, sh - ch))
        src = src.crop((0, y0, sw, y0 + ch))
    return src.resize((tw, th), Image.Resampling.LANCZOS)


def _find_perspective_coeffs(dst_pts: list[tuple[float, float]], src_pts: list[tuple[float, float]]) -> list[float]:
    """Compute exact 8-coeff projective mapping for PIL Image.Transform.PERSPECTIVE."""
    matrix = []
    for p1, p2 in zip(dst_pts, src_pts):
        matrix.append([p1[0], p1[1], 1.0, 0.0, 0.0, 0.0, -p2[0] * p1[0], -p2[0] * p1[1]])
        matrix.append([0.0, 0.0, 0.0, p1[0], p1[1], 1.0, -p2[1] * p1[0], -p2[1] * p1[1]])
    A = np.array(matrix, dtype=np.float64)
    B = np.array([c for pt in src_pts for c in pt], dtype=np.float64)
    res = np.linalg.solve(A, B)
    return [float(v) for v in res]


def apply_perspective_tilt_rgba(
    img_rgba: Image.Image,
    *,
    tilt_x_deg: float = 0.0,
    tilt_y_deg: float = 0.0,
) -> Image.Image:
    """Apply smooth bounded 2.5D perspective warp on an RGBA layer without distorting legibility."""
    tx = _clamp(tilt_x_deg, -16.0, 16.0)
    ty = _clamp(tilt_y_deg, -16.0, 16.0)
    if abs(tx) < 0.25 and abs(ty) < 0.25:
        return img_rgba
    w, h = img_rgba.size
    dx = (ty / 16.0) * (w * 0.055)
    dy = (tx / 16.0) * (h * 0.045)
    src = [(0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))]
    dst = [
        (max(0.0, dx), max(0.0, dy)),
        (min(float(w), w - max(0.0, -dx)), max(0.0, -dy)),
        (min(float(w), w - max(0.0, dx)), min(float(h), h - max(0.0, dy))),
        (max(0.0, -dx), min(float(h), h - max(0.0, -dy))),
    ]
    coeffs = _find_perspective_coeffs(dst, src)
    return img_rgba.transform((w, h), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC)


def apply_exposure_grade_rgb(
    arr_rgb: np.ndarray,
    *,
    exposure_ev: float = 0.0,
    contrast: float = 1.0,
    saturation: float = 1.0,
    warmth: float = 0.0,
    vignette: float = 0.0,
) -> np.ndarray:
    """Vectorized restrained exposure/contrast/saturation/warmth/vignette grade directly on RGB uint8 frame."""
    ev = _clamp(exposure_ev, -0.65, 0.65)
    con = _clamp(contrast, 0.72, 1.38)
    sat = _clamp(saturation, 0.45, 1.38)
    wm = _clamp(warmth, -0.25, 0.25)
    vig = _clamp(vignette, 0.0, 0.68)
    if abs(ev) < 1e-3 and abs(con - 1.0) < 1e-3 and abs(sat - 1.0) < 1e-3 and abs(wm) < 1e-3 and vig < 1e-3:
        return arr_rgb

    x = arr_rgb.astype(np.float32) * (1.0 / 255.0)
    if abs(ev) >= 1e-3:
        x = x * float(2.0 ** ev)
    if abs(con - 1.0) >= 1e-3:
        x = (x - 0.5) * con + 0.5
    if abs(sat - 1.0) >= 1e-3:
        luma = (0.2126 * x[:, :, 0] + 0.7152 * x[:, :, 1] + 0.0722 * x[:, :, 2])[:, :, None]
        x = luma + (x - luma) * sat
    if abs(wm) >= 1e-3:
        x[:, :, 0] *= (1.0 + wm * 0.22)
        x[:, :, 2] *= (1.0 - wm * 0.22)
    if vig >= 1e-3:
        h, w, _ = x.shape
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        nx = (xx / max(1.0, float(w)) - 0.5) * 2.0
        ny = (yy / max(1.0, float(h)) - 0.5) * 2.0
        r2 = np.clip(nx * nx * 0.85 + ny * ny * 0.85, 0.0, 2.0)
        mask = np.clip(1.0 - vig * np.clip(r2 - 0.35, 0.0, 1.65) * 0.65, 0.25, 1.0)
        x *= mask[:, :, None]
    return np.clip(x * 255.0, 0.0, 255.0).astype(np.uint8)


def render_pullback_or_transform_frame(
    img: Image.Image,
    out_w: int,
    out_h: int,
    *,
    scale: float = 1.0,
    cx_norm: float = 0.5,
    cy_norm: float = 0.5,
    x_norm: float = 0.5,
    y_norm: float = 0.5,
    rotation_deg: float = 0.0,
    tilt_x_deg: float = 0.0,
    tilt_y_deg: float = 0.0,
    border_radius_px: int = 0,
    palette: dict[str, str] | None = None,
    shadow: bool = True,
    fit_mode: str = "cover",
    crop_rect_norm: list[float] | None = None,
) -> Image.Image:
    """Render a source frame supporting BOTH `>= 1.0` crop zoom AND `< 1.0` canvas pullback + 2.5D tilt + rotation."""
    sc = _clamp(scale, 0.25, 2.5)
    rot = _clamp(rotation_deg, -25.0, 25.0)
    needs_canvas = (sc < 0.999) or (abs(rot) >= 0.2) or (abs(tilt_x_deg) >= 0.25) or (abs(tilt_y_deg) >= 0.25) or (border_radius_px > 0) or (abs(x_norm - 0.5) > 0.005) or (abs(y_norm - 0.5) > 0.005)

    if not needs_canvas:
        return _fit_crop_image(
            img, out_w, out_h,
            fit_mode=fit_mode, cx_norm=cx_norm, cy_norm=cy_norm,
            crop_rect_norm=crop_rect_norm, zoom_scale=max(1.0, sc),
        ).convert("RGB")

    # Art-directed canvas background when footage pulls back (< 1.0) or tilts
    pal = dict(palette or {"canvas_hex": "#0E1217", "surface_hex": "#1B222C", "accent_hex": "#E5B95C"})
    bg_arr = render_luxury_backdrop(out_w, out_h, pal)
    canvas = Image.fromarray(bg_arr, mode="RGB").convert("RGBA")

    layer_w = max(16, int(round(out_w * min(1.0, sc))))
    layer_h = max(16, int(round(out_h * min(1.0, sc))))
    fg = _fit_crop_image(
        img, layer_w, layer_h,
        fit_mode=fit_mode, cx_norm=cx_norm, cy_norm=cy_norm,
        crop_rect_norm=crop_rect_norm, zoom_scale=max(1.0, sc),
    ).convert("RGBA")

    if border_radius_px > 0 or sc < 0.98:
        rad = border_radius_px if border_radius_px > 0 else max(12, int(round(26 * (out_w / 1080.0))))
        m = Image.new("L", (layer_w, layer_h), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, layer_w, layer_h), radius=rad, fill=255)
        fg.putalpha(m)

    if abs(tilt_x_deg) >= 0.25 or abs(tilt_y_deg) >= 0.25:
        fg = apply_perspective_tilt_rgba(fg, tilt_x_deg=tilt_x_deg, tilt_y_deg=tilt_y_deg)

    if abs(rot) >= 0.2:
        fg = fg.rotate(-rot, resample=Image.Resampling.BICUBIC, expand=True)

    fw, fh = fg.size
    px = int(round(x_norm * out_w - fw * 0.5))
    py = int(round(y_norm * out_h - fh * 0.5))

    if shadow and sc < 0.99:
        sh = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
        alpha_sh = fg.split()[3].point(lambda a: int(a * 0.42))
        black = Image.new("RGBA", (fw, fh), (0, 0, 0, 255))
        black.putalpha(alpha_sh)
        sh.paste(black, (px, py + max(6, int(14 * out_w / 1080.0))))
        sh = sh.filter(ImageFilter.GaussianBlur(radius=max(6, int(18 * out_w / 1080.0))))
        canvas.alpha_composite(sh)

    tmp_fg = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    tmp_fg.paste(fg, (px, py), fg)
    canvas.alpha_composite(tmp_fg)
    return canvas.convert("RGB")


def compose_multilayer_frame(
    canvas_rgb: np.ndarray,
    layers: list[dict[str, Any]],
    *,
    t_sec: float,
    out_w: int,
    out_h: int,
    source_frame_lookup: Any,
    palette: dict[str, str] | None = None,
) -> np.ndarray:
    """Composite arbitrary N positioned/scaled/masked/keyframed video/media layers onto `canvas_rgb` by z_order."""
    if not layers:
        return canvas_rgb
    active = [ly for ly in layers if float(ly.get("start_sec", 0.0)) <= t_sec < float(ly.get("end_sec", 0.0))]
    if not active:
        return canvas_rgb

    active.sort(key=lambda x: int(x.get("z_order", 20)))
    comp = Image.fromarray(canvas_rgb, mode="RGB").convert("RGBA")

    for ly in active:
        t0 = float(ly["start_sec"])
        t1 = float(ly["end_sec"])
        dur = max(0.05, t1 - t0)
        el = t_sec - t0
        rem = t1 - t_sec
        in_p = ease_out_cubic(min(1.0, el / max(0.04, float(ly.get("enter_sec", 0.24)))))
        out_p = ease_out_cubic(min(1.0, rem / max(0.04, float(ly.get("exit_sec", 0.20)))))
        vis = min(in_p, out_p)
        if vis <= 0.01:
            continue

        sid = str(ly.get("source_id") or "")
        src_in = float(ly.get("src_in_sec", 0.0))
        src_out = float(ly.get("src_out_sec", src_in + dur))
        u = _clamp(el / dur, 0.0, 1.0)
        t_src = src_in + u * (src_out - src_in)
        im = source_frame_lookup(sid, t_src)
        if im is None:
            continue

        defaults = {
            "x_norm": float(ly.get("x_norm", 0.5)),
            "y_norm": float(ly.get("y_norm", 0.5)),
            "w_norm": float(ly.get("w_norm", 0.88)),
            "h_norm": float(ly.get("h_norm", 0.45)),
            "scale": float(ly.get("scale", 1.0)),
            "rotation_deg": float(ly.get("rotation_deg", 0.0)),
            "opacity": float(ly.get("opacity", 1.0)),
            "cx_norm": float(ly.get("cx_norm", 0.5)),
            "cy_norm": float(ly.get("cy_norm", 0.5)),
            "tilt_x_deg": float(ly.get("tilt_x_deg", 0.0)),
            "tilt_y_deg": float(ly.get("tilt_y_deg", 0.0)),
        }
        tf = interpolate_keyframes(ly.get("keyframes"), t_sec, defaults)
        eff_op = _clamp(tf["opacity"] * vis, 0.0, 1.0)
        if eff_op <= 0.01:
            continue

        # Optional gentle dim of background behind floating evidence layer
        dim_bg = float(ly.get("dim_bg", 0.0)) * vis
        if dim_bg > 0.01:
            dim_im = Image.new("RGBA", (out_w, out_h), (8, 10, 14, int(255 * _clamp(dim_bg, 0.0, 0.72))))
            comp.alpha_composite(dim_im)

        bw = max(16, int(round(tf["w_norm"] * out_w * _clamp(tf["scale"], 0.2, 2.0))))
        bh = max(16, int(round(tf["h_norm"] * out_h * _clamp(tf["scale"], 0.2, 2.0))))
        layer_im = _fit_crop_image(
            im, bw, bh,
            fit_mode=str(ly.get("fit_mode") or "cover"),
            cx_norm=tf["cx_norm"],
            cy_norm=tf["cy_norm"],
            crop_rect_norm=ly.get("crop_rect_norm"),
            zoom_scale=float(ly.get("inner_zoom", 1.0)),
        ).convert("RGBA")

        rad = int(ly.get("border_radius_px", max(10, int(round(20 * out_w / 1080.0)))))
        mask_kind = str(ly.get("mask") or ("rounded" if rad > 0 else "none")).lower()
        alpha_m = Image.new("L", (bw, bh), int(255 * eff_op))
        if mask_kind == "rounded" and rad > 0:
            alpha_m = Image.new("L", (bw, bh), 0)
            ImageDraw.Draw(alpha_m).rounded_rectangle((0, 0, bw, bh), radius=rad, fill=int(255 * eff_op))
        elif mask_kind == "wipe_lr":
            alpha_m = Image.new("L", (bw, bh), 0)
            ImageDraw.Draw(alpha_m).rectangle((0, 0, int(bw * in_p), bh), fill=int(255 * eff_op))
        layer_im.putalpha(alpha_m)

        if abs(tf["tilt_x_deg"]) >= 0.25 or abs(tf["tilt_y_deg"]) >= 0.25:
            layer_im = apply_perspective_tilt_rgba(layer_im, tilt_x_deg=tf["tilt_x_deg"], tilt_y_deg=tf["tilt_y_deg"])
        if abs(tf["rotation_deg"]) >= 0.2:
            layer_im = layer_im.rotate(-tf["rotation_deg"], resample=Image.Resampling.BICUBIC, expand=True)

        fw, fh = layer_im.size
        slide_y = int(round((1.0 - in_p) * float(ly.get("enter_slide_y_px", 28.0)) * (out_h / 1920.0)))
        px = int(round(tf["x_norm"] * out_w - fw * 0.5))
        py = int(round(tf["y_norm"] * out_h - fh * 0.5)) + slide_y

        if ly.get("shadow", True):
            sh = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
            a_sh = layer_im.split()[3].point(lambda a: int(a * 0.40))
            blk = Image.new("RGBA", (fw, fh), (0, 0, 0, 255))
            blk.putalpha(a_sh)
            sh.paste(blk, (px, py + max(6, int(12 * out_w / 1080.0))))
            sh = sh.filter(ImageFilter.GaussianBlur(radius=max(6, int(16 * out_w / 1080.0))))
            comp.alpha_composite(sh)

        tmp = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
        tmp.paste(layer_im, (px, py), layer_im)
        comp.alpha_composite(tmp)

    return np.asarray(comp.convert("RGB"), dtype=np.uint8)


# ---------------------------------------------------------------------------
# True Dual-Moving Transitions & Handle Validation (DX01-05)
# ---------------------------------------------------------------------------

def validate_transition_handles(
    prev_track: dict[str, Any],
    next_track: dict[str, Any],
    *,
    requested_frames: int,
    source_durations_sec: dict[str, float],
    fps: float,
) -> dict[str, Any]:
    """Validate available outgoing/incoming source handles before running any dual-moving transition.

    Never silently freezes an outgoing frame if handle is insufficient — clamps duration cleanly
    or falls back to clean hard cut (`effective_frames=0`) with explicit rationale.
    """
    req = max(0, int(requested_frames))
    if req == 0:
        return {"requested_frames": 0, "effective_frames": 0, "fallback_to_cut": False, "reason": "clean_cut"}

    p_mode = str(prev_track.get("mode") or "source").lower()
    n_mode = str(next_track.get("mode") or "source").lower()
    p_sid = str(prev_track.get("source_id") or prev_track.get("source") or "")
    n_sid = str(next_track.get("source_id") or next_track.get("source") or "")

    avail_out_f = req
    if p_mode == "source" and p_sid in source_durations_sec:
        s_dur = float(source_durations_sec[p_sid])
        p_end_sec = float(prev_track.get("src_out_sec", 0.0))
        tail_sec = max(0.0, s_dur - p_end_sec - 0.03)
        avail_out_f = int(math.floor(tail_sec * fps))

    # Also ensure transition does not exceed 40% of either adjacent clip length
    p_len = max(1, int(prev_track["out_end_frame"]) - int(prev_track["out_start_frame"]))
    n_len = max(1, int(next_track["out_end_frame"]) - int(next_track["out_start_frame"]))
    max_safe = min(req, avail_out_f, max(0, int(p_len * 0.4)), max(0, int(n_len * 0.4)))

    if max_safe < 2:
        return {
            "requested_frames": req,
            "effective_frames": 0,
            "avail_outgoing_handle_frames": avail_out_f,
            "fallback_to_cut": True,
            "reason": f"Insufficient outgoing handle ({avail_out_f}f) or short clip -> clean hard cut (no frozen tail).",
        }
    return {
        "requested_frames": req,
        "effective_frames": max_safe,
        "avail_outgoing_handle_frames": avail_out_f,
        "clamped": max_safe < req,
        "fallback_to_cut": False,
        "reason": "Dual-moving source handles verified." if max_safe == req else f"Clamped transition {req}f -> {max_safe}f to fit true outgoing handle.",
    }


def _directional_blur_rgb(arr: np.ndarray, radius_px: int, axis: str = "x") -> np.ndarray:
    """Fast bounded 1D box motion blur along horizontal or vertical axis (zero scipy required)."""
    r = max(1, min(32, int(radius_px)))
    k = 2 * r + 1
    f = arr.astype(np.float32)
    if axis == "x":
        pad = np.pad(f, ((0, 0), (r, r), (0, 0)), mode="edge")
        cs = np.cumsum(pad, axis=1)
        out = (cs[:, k:, :] - cs[:, :-k, :]) / float(k)
        # Re-align edge column cleanly
        res = np.empty_like(f)
        res[:, : out.shape[1], :] = out
        if out.shape[1] < f.shape[1]:
            res[:, out.shape[1] :, :] = out[:, -1:, :]
        return np.clip(res, 0.0, 255.0).astype(np.uint8)
    pad = np.pad(f, ((r, r), (0, 0), (0, 0)), mode="edge")
    cs = np.cumsum(pad, axis=0)
    out = (cs[k:, :, :] - cs[:-k, :, :]) / float(k)
    res = np.empty_like(f)
    res[: out.shape[0], :, :] = out
    if out.shape[0] < f.shape[0]:
        res[out.shape[0] :, :, :] = out[-1:, :, :]
    return np.clip(res, 0.0, 255.0).astype(np.uint8)


def render_dual_moving_transition(
    outgoing_rgb: np.ndarray,
    incoming_rgb: np.ndarray,
    *,
    progress: float,
    kind: str = "dx01.trans.dual_crossfade",
    direction: str = "left",
    feather_px: float = 48.0,
    blur_px: float = 20.0,
) -> np.ndarray:
    """Blend TWO simultaneously moving RGB frames (`outgoing_rgb`, `incoming_rgb`) deterministically at `progress in (0, 1)`."""
    p = _clamp(progress, 0.0, 1.0)
    u = ease_in_out_cubic(p)
    h, w, _ = incoming_rgb.shape
    k = str(kind or "dx01.trans.dual_crossfade").lower()

    if k in ("dx01.trans.mask_wipe", "mask_wipe", "wipe"):
        feather = max(4.0, float(feather_px))
        if direction in ("left", "right"):
            xs = np.arange(w, dtype=np.float32)[None, :]
            edge = (w + feather) * u - feather * 0.5
            m = np.clip((edge - xs) / feather + 0.5, 0.0, 1.0) if direction == "left" else np.clip((xs - (w - edge)) / feather + 0.5, 0.0, 1.0)
            mask = m[:, :, None]
        elif direction in ("up", "down"):
            ys = np.arange(h, dtype=np.float32)[:, None]
            edge = (h + feather) * u - feather * 0.5
            m = np.clip((edge - ys) / feather + 0.5, 0.0, 1.0) if direction == "down" else np.clip((ys - (h - edge)) / feather + 0.5, 0.0, 1.0)
            mask = m[:, :, None]
        else:  # iris / radial reveal
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            dist = np.sqrt((xx - w * 0.5) ** 2 + (yy - h * 0.5) ** 2)
            max_r = math.hypot(w * 0.5, h * 0.5) + feather
            edge = max_r * u
            mask = np.clip((edge - dist) / feather + 0.5, 0.0, 1.0)[:, :, None]
        return np.clip(incoming_rgb.astype(np.float32) * mask + outgoing_rgb.astype(np.float32) * (1.0 - mask), 0.0, 255.0).astype(np.uint8)

    if k in ("dx01.trans.whip_blur", "whip_blur", "whip"):
        # Bounded directional motion blur peaking smoothly at midpoint
        peak_r = max(2, int(round(_clamp(blur_px, 4.0, 40.0) * math.sin(u * math.pi))))
        axis = "y" if direction in ("up", "down") else "x"
        shift = int(round((u if u < 0.5 else (u - 1.0)) * (h if axis == "y" else w) * 0.28))
        base = outgoing_rgb if u < 0.5 else incoming_rgb
        other = incoming_rgb if u < 0.5 else outgoing_rgb
        shifted = np.roll(base, -shift, axis=0 if axis == "y" else 1)
        blended = np.clip(shifted.astype(np.float32) * (1.0 - abs(2.0 * u - 1.0) * 0.15) + other.astype(np.float32) * (abs(2.0 * u - 1.0) * 0.15), 0.0, 255.0).astype(np.uint8)
        return _directional_blur_rgb(blended, peak_r, axis=axis)

    if k in ("dx01.trans.match_reveal", "match_reveal"):
        # Incoming slides in smoothly while outgoing gently recedes with subtle dim
        out_dim = np.clip(outgoing_rgb.astype(np.float32) * (1.0 - 0.35 * u), 0.0, 255.0).astype(np.uint8)
        res = out_dim.copy()
        if direction in ("up", "down"):
            offset = int(round((1.0 - u) * h))
            if offset < h:
                res[offset:, :, :] = incoming_rgb[: h - offset, :, :]
        else:
            offset = int(round((1.0 - u) * w))
            if offset < w:
                res[:, offset:, :] = incoming_rgb[:, : w - offset, :]
        return res

    # Default: true dual-moving smooth crossfade
    return np.clip(
        incoming_rgb.astype(np.float32) * u + outgoing_rgb.astype(np.float32) * (1.0 - u),
        0.0, 255.0,
    ).astype(np.uint8)


def render_before_after_split_rgb(
    before_rgb: np.ndarray,
    after_rgb: np.ndarray,
    *,
    progress: float,
    pause_at: float = 0.5,
) -> np.ndarray:
    """Composite two authentic RGB frames across a sweeping vertical divider (paired with Remotion divider HUD)."""
    p = _clamp(progress, 0.0, 1.0)
    if p < 0.35:
        pos = 0.18 + (pause_at - 0.18) * ease_out_cubic(p / 0.35)
    elif p < 0.65:
        pos = pause_at
    else:
        pos = pause_at + (0.84 - pause_at) * ease_out_cubic((p - 0.65) / 0.35)
    h, w, _ = before_rgb.shape
    split_x = max(1, min(w - 1, int(round(w * pos))))
    out = before_rgb.copy()
    out[:, split_x:, :] = after_rgb[:, split_x:, :]
    return out


def resolve_speed_ramp_pts(
    *,
    u_norm: float,
    src_in_sec: float,
    src_out_sec: float,
    ramp_curve: str = "linear",
    is_master_dialogue: bool = False,
) -> float:
    """Evaluate safe source PTS with optional smooth speed ramp ONLY on non-dialogue visual clips."""
    u = _clamp(u_norm, 0.0, 1.0)
    if is_master_dialogue or str(ramp_curve or "linear").lower() == "linear":
        return src_in_sec + u * (src_out_sec - src_in_sec)
    if ramp_curve == "ease_in_out":
        return src_in_sec + ease_in_out_cubic(u) * (src_out_sec - src_in_sec)
    if ramp_curve == "fast_entry_slow_settle":
        return src_in_sec + ease_out_cubic(u) * (src_out_sec - src_in_sec)
    return src_in_sec + u * (src_out_sec - src_in_sec)


__all__ = [
    "apply_exposure_grade_rgb",
    "apply_perspective_tilt_rgba",
    "compose_multilayer_frame",
    "interpolate_keyframes",
    "render_before_after_split_rgb",
    "render_dual_moving_transition",
    "render_pullback_or_transform_frame",
    "resolve_speed_ramp_pts",
    "validate_transition_handles",
]
