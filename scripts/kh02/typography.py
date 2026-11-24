"""KH-02 Typography & Layout Direction + Optional Caption SRT Helper.

Selective adaptation of phrase-aware SRT chunking from:
  browser-use/video-use @ 43cfc566833548093455c9d4a46bd69913b7e552
  helpers/render.py::chunk_words, build_master_srt (MIT License, Copyright (c) 2026 Browser Use).
Captions remain OFF by default unless explicitly requested by brief.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

# Reading rates (characters/sec) by script class (conservative mobile-readable floor)
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
VI_DIACRITIC_RE = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]")

FONT_FALLBACK_STACKS = {
    "system-sans-bold": "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Hiragino Sans GB', 'Noto Sans', 'Segoe UI', Arial, sans-serif",
    "editorial-serif": "Georgia, 'Songti SC', 'Noto Serif', 'Times New Roman', serif",
    "mono-code": "ui-monospace, 'SF Mono', Menlo, Monaco, Consolas, monospace",
    "handwritten-accent": "'Caveat', 'PingFang SC', cursive, sans-serif",
}


def compute_min_read_frames(text: str, fps: float, *, role: str = "key_phrase") -> int:
    """Estimate conservative minimum readable hold duration in frames for Latin, Vietnamese, and CJK text."""
    stripped = text.strip()
    if not stripped:
        return int(round(0.4 * fps))
    cjk_count = len(CJK_RE.findall(stripped))
    if cjk_count > len(stripped) * 0.3:
        # ~6 CJK chars/sec comfortable mobile pace + 0.35s acquisition
        sec = 0.35 + len(stripped) / 6.0
    else:
        words = max(1, len(stripped.split()))
        # ~3.2 words/sec + 0.35s acquisition; CTA holds longer (~1.5s floor when duration permits)
        sec = 0.35 + words / 3.2
    if role == "cta":
        sec = max(1.2, sec)
    elif role == "label":
        sec = max(0.5, min(sec, 1.5))
    else:
        sec = max(0.75, sec)
    return max(4, int(math.ceil(sec * fps)))


def measure_text_layout(
    text: str,
    *,
    font_size_px: int,
    box_width_px: float,
    box_height_px: float,
    canvas_width_px: int = 720,
    language: str = "en",
) -> dict[str, Any]:
    """Deterministic glyph/line-wrap/mobile-360px legibility estimator supporting EN, VI diacritics, and CJK."""
    has_cjk = bool(CJK_RE.search(text))
    has_vi = bool(VI_DIACRITIC_RE.search(text))
    # Average glyph advance relative to font_size_px
    char_em = 1.02 if has_cjk else (0.58 if has_vi else 0.55)
    # Extra line-height headroom for Vietnamese stacked diacritics (e.g., ệ, ử, ẫ)
    line_height_ratio = 1.38 if has_vi else (1.30 if has_cjk else 1.22)

    chars_per_line = max(1, int(box_width_px / max(1.0, font_size_px * char_em)))
    lines: list[str] = []
    if has_cjk:
        for i in range(0, len(text), chars_per_line):
            lines.append(text[i : i + chars_per_line])
    else:
        cur = ""
        for tok in text.split():
            cand = f"{cur} {tok}".strip()
            if len(cand) <= chars_per_line or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = tok
        if cur:
            lines.append(cur)
    if not lines:
        lines = [""]

    needed_height = len(lines) * font_size_px * line_height_ratio
    overflow = needed_height > box_height_px * 1.08
    # Scale to 360px phone viewport
    phone_px = round(font_size_px * (360.0 / max(1.0, float(canvas_width_px))), 2)
    mobile_readable = phone_px >= 10.5

    return {
        "line_count": len(lines),
        "wrapped_lines": lines,
        "needed_height_px": round(needed_height, 1),
        "box_height_px": round(box_height_px, 1),
        "overflow": overflow,
        "phone_360px_font_size": phone_px,
        "mobile_readable": mobile_readable,
        "has_vietnamese_diacritics": has_vi,
        "has_cjk": has_cjk,
        "recommended_line_height": line_height_ratio,
    }


def plan_text_events(
    beats: list[dict[str, Any]],
    *,
    fps: float = 24.0,
    caption_policy: str = "OFF",
    language: str = "en",
    cta_copy: str | None = None,
    mapped_words: list[dict[str, Any]] | None = None,
    canvas_width: int = 720,
    canvas_height: int = 1280,
    presenter_reserved_region: list[float] | None = None,
    platform_safe_zone: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Plan semantic typography hierarchy sized for 360px mobile legibility, box fit & optional presenter avoidance."""
    min_mobile_px = int(math.ceil(11.2 * (float(canvas_width) / 360.0)))
    is_landscape = canvas_width > canvas_height
    sz = platform_safe_zone or {"top": 0.08, "bottom": 0.12, "left": 0.04, "right": 0.04}
    safe_l = round(max(0.05, float(sz["left"])), 4)
    safe_r = round(max(0.05, float(sz["right"])), 4)
    safe_t = round(max(0.06, float(sz["top"])), 4)
    safe_b = round(max(0.10, float(sz["bottom"])), 4)
    safe_w = round(max(0.45, 1.0 - safe_l - safe_r), 4)

    if presenter_reserved_region and len(presenter_reserved_region) == 4:
        px, py, pw, ph = [float(v) for v in presenter_reserved_region]
        # Place primary copy cleanly above or opposite the reserved presenter PIP box inside platform_safe_zone
        if py >= 0.44:
            box_pos = [safe_l, max(safe_t, round(py - 0.18, 4)), safe_w, 0.16]
            if px < 0.42:
                cx = round(min(0.46, px + pw + 0.025), 4)
                cw = round(max(0.36, (1.0 - safe_r) - cx), 4)
                cap_pos = [cx, round(min(1.0 - safe_b - 0.12, py + 0.02), 4), cw, 0.12]
            else:
                cw = round(max(0.36, (px - 0.025) - safe_l), 4)
                cap_pos = [safe_l, round(min(1.0 - safe_b - 0.12, py + 0.02), 4), cw, 0.12]
        else:
            box_pos = [safe_l, min(1.0 - safe_b - 0.17, 0.62 if is_landscape else 0.64), safe_w, 0.17]
            cap_pos = [safe_l, min(1.0 - safe_b - 0.10, 0.78 if is_landscape else 0.79), safe_w, 0.10]
    else:
        box_pos = [safe_l, 0.62, safe_w, 0.24] if is_landscape else [safe_l, min(1.0 - safe_b - 0.18, 0.68), safe_w, 0.18]
        cap_pos = [safe_l, min(1.0 - safe_b - 0.10, 0.82), safe_w, 0.10]
    box_w_px = box_pos[2] * canvas_width
    box_h_px = box_pos[3] * canvas_height

    text_events: list[dict[str, Any]] = []
    for idx, b in enumerate(beats):
        fn = b.get("semantic_function", "hook")
        role = "hero" if fn == "hook" else ("cta" if fn == "cta" else ("key_phrase" if fn in ("payoff", "reveal") else "label"))
        copy = cta_copy if (fn == "cta" and cta_copy) else b.get("focus_subject", "").strip()
        if not copy:
            continue
        sf = int(b["start_frame"])
        ef = int(b["end_frame"])
        min_hold = min(max(4, ef - sf), compute_min_read_frames(copy, fps, role=role))
        pref = max(min_mobile_px, 44 if role == "hero" else (38 if role in ("key_phrase", "cta") else 32))
        # Step down font size down to min_mobile_px if wrapped lines exceed box height
        font_sz = pref
        while font_sz > min_mobile_px:
            lay = measure_text_layout(copy, font_size_px=font_sz, box_width_px=box_w_px, box_height_px=box_h_px, canvas_width_px=canvas_width, language=language)
            if not lay["overflow"]:
                break
            font_sz -= 2
        text_events.append({
            "text_id": f"txt_{idx + 1:02d}_{b['beat_id']}",
            "beat_id": b["beat_id"],
            "text": copy,
            "word_ids": list(b.get("source_word_ids", []))[:6],
            "role": role,
            "hierarchy": 1 if role == "hero" else (2 if role in ("key_phrase", "cta") else 3),
            "style": {"font_size_px": font_sz, "weight": 800 if role != "label" else 700, "z_order": 50},
            "font_ref": "system-sans-bold",
            "start_frame": sf,
            "end_frame": ef,
            "minimum_read_frames": min_hold,
            "position": list(box_pos),
            "caption_policy": caption_policy,
            "emphasis": [copy.split()[0]] if copy.split() else [],
            "contrast_intent": "high_contrast_verified",
        })

    # Add top-layer captions ONLY if brief explicitly opted into ON_REQUESTED
    if caption_policy == "ON_REQUESTED" and mapped_words:
        chunks = chunk_words(mapped_words)
        for c_idx, ch in enumerate(chunks):
            txt = " ".join(w["text"] for w in ch).strip()
            if not txt:
                continue
            csf = int(ch[0]["out_start_frame"])
            cef = max(csf + 4, int(ch[-1]["out_end_frame"]))
            # Assign to covering beat
            b_match = next((b["beat_id"] for b in beats if int(b["start_frame"]) <= csf < int(b["end_frame"])), beats[0]["beat_id"])
            text_events.append({
                "text_id": f"cap_{c_idx + 1:03d}",
                "beat_id": b_match,
                "text": txt,
                "word_ids": [w["word_id"] for w in ch],
                "role": "caption",
                "hierarchy": 4,
                "style": {"font_size_px": 30, "weight": 800, "z_order": 999},
                "font_ref": "system-sans-bold",
                "start_frame": csf,
                "end_frame": cef,
                "minimum_read_frames": min(cef - csf, compute_min_read_frames(txt, fps, role="label")),
                "position": list(cap_pos),
                "caption_policy": "ON_REQUESTED",
                "emphasis": [],
                "contrast_intent": "caption_top_layer_stroke",
            })
    return text_events


def validate_readability(
    text_events: list[dict[str, Any]],
    *,
    canvas_width: int = 720,
    canvas_height: int = 1280,
    fps: float = 24.0,
    caption_policy: str = "OFF",
    platform_safe_zone: dict[str, float] | None = None,
    max_visual_z_order: int = 100,
) -> list[dict[str, Any]]:
    """Validate text hold duration, phone-scale legibility, box overflow, caption policy & top-layer z-order."""
    sz = platform_safe_zone or {"top": 0.08, "bottom": 0.10, "left": 0.04, "right": 0.04}
    issues: list[dict[str, Any]] = []

    for tx in text_events:
        tid = tx["text_id"]
        role = tx["role"]
        if caption_policy == "OFF" and role == "caption":
            issues.append({
                "severity": "P0",
                "text_id": tid,
                "code": "TYPO_UNREQUESTED_CAPTION",
                "message": "Caption event present despite caption_policy='OFF'",
            })
        if role == "caption":
            z = int((tx.get("style") or {}).get("z_order", 0))
            if z <= max_visual_z_order:
                issues.append({
                    "severity": "P1",
                    "text_id": tid,
                    "code": "TYPO_CAPTION_NOT_TOP_LAYER",
                    "message": f"Caption z_order={z} must be strictly above all overlays ({max_visual_z_order})",
                })
        dur_frames = int(tx["end_frame"]) - int(tx["start_frame"])
        min_req = int(tx["minimum_read_frames"])
        if dur_frames < min_req:
            issues.append({
                "severity": "P1",
                "text_id": tid,
                "code": "TYPO_SHORT_HOLD",
                "message": f"Text hold {dur_frames}f ({dur_frames/fps:.2f}s) < minimum_read_frames {min_req}f ({min_req/fps:.2f}s)",
            })
        bx, by, bw, bh = [float(v) for v in tx["position"]]
        if bx < sz["left"] - 0.02 or by < sz["top"] - 0.02 or (bx + bw) > (1.0 - sz["right"] + 0.02) or (by + bh) > (1.0 - sz["bottom"] + 0.02):
            issues.append({
                "severity": "P1",
                "text_id": tid,
                "code": "TYPO_SAFE_ZONE_CLIP",
                "message": f"Text box {tx['position']} intrudes outside platform_safe_zone {sz}",
            })
        font_sz = int((tx.get("style") or {}).get("font_size_px", 28))
        layout = measure_text_layout(
            tx["text"],
            font_size_px=font_sz,
            box_width_px=bw * canvas_width,
            box_height_px=bh * canvas_height,
            canvas_width_px=canvas_width,
        )
        if layout["overflow"]:
            issues.append({
                "severity": "P1",
                "text_id": tid,
                "code": "TYPO_TEXT_OVERFLOW",
                "message": f"Wrapped text needs {layout['needed_height_px']}px > box {layout['box_height_px']}px",
            })
        if not layout["mobile_readable"]:
            issues.append({
                "severity": "P1",
                "text_id": tid,
                "code": "TYPO_MOBILE_TOO_SMALL",
                "message": f"Font renders at {layout['phone_360px_font_size']}px on 360px phone (<10.5px floor)",
            })
        font_ref = tx.get("font_ref", "system-sans-bold")
        if font_ref not in FONT_FALLBACK_STACKS:
            issues.append({
                "severity": "P1",
                "text_id": tid,
                "code": "TYPO_UNKNOWN_FONT_REF",
                "message": f"Font {font_ref!r} lacks explicit local fallback stack (no unauthorized download)",
            })
    return issues


def chunk_words(
    words: list[dict[str, Any]],
    *,
    chunk_words_target: int = 2,
    chunk_max_words: int = 4,
    chunk_pause_sec: float = 0.28,
    chunk_min_sec: float = 0.55,
) -> list[list[dict[str, Any]]]:
    """Group output-mapped words into phrase-aware subtitle chunks (adapted from video-use MIT)."""
    punct_break = set(".!?,;:")
    chunks: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for idx, w in enumerate(words):
        cur.append(w)
        text = str(w.get("text", "")).strip()
        nxt = words[idx + 1] if idx + 1 < len(words) else None
        s_key = "out_start_sec" if "out_start_sec" in w else "start_sec"
        e_key = "out_end_sec" if "out_end_sec" in w else "end_sec"
        gap = (float(nxt[s_key]) - float(w[e_key])) if nxt else 0.0
        dur = float(w[e_key]) - float(cur[0][s_key])
        if (
            nxt is None
            or (text and text[-1] in punct_break)
            or gap >= chunk_pause_sec
            or len(cur) >= chunk_max_words
            or (len(cur) >= chunk_words_target and dur >= chunk_min_sec)
        ):
            chunks.append(cur)
            cur = []
    return chunks


def _fmt_srt_time(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    hh, rem = divmod(ms, 3600000)
    mm, rem = divmod(rem, 60000)
    ss, ms = divmod(rem, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def build_master_srt(mapped_words: list[dict[str, Any]], out_path: str | Path) -> Path:
    """Write output-clock SRT only when captions are explicitly requested (adapted from video-use MIT)."""
    chunks = chunk_words(mapped_words)
    lines: list[str] = []
    for idx, ch in enumerate(chunks, 1):
        t0 = float(ch[0]["out_start_sec"])
        t1 = max(t0 + 0.35, float(ch[-1]["out_end_sec"]))
        body = " ".join(w["text"].strip() for w in ch).strip()
        lines.extend([str(idx), f"{_fmt_srt_time(t0)} --> {_fmt_srt_time(t1)}", body, ""])
    dst = Path(out_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines), encoding="utf-8")
    return dst


def measure_layout_mobile(
    text: str,
    *,
    font_size_px: int,
    normalized_box: list[float],
    canvas_width: int = 720,
    canvas_height: int = 1280,
    language: str = "en",
) -> dict[str, Any]:
    """R1.05 mobile 360px readability & layout metric wrapper accepting normalized box coordinates."""
    bx, by, bw, bh = [float(v) for v in normalized_box]
    return measure_text_layout(
        text,
        font_size_px=font_size_px,
        box_width_px=bw * canvas_width,
        box_height_px=bh * canvas_height,
        canvas_width_px=canvas_width,
        language=language,
    )


def compute_swept_bbox(
    bbox: list[float],
    *,
    transform: dict[str, Any] | None = None,
    easing: str = "power2.out",
    canvas_width: int = 720,
    canvas_height: int = 1280,
) -> list[float]:
    """Compute conservative swept normalized [x, y, w, h] covering entry shift, spring overshoot & exit motion."""
    x, y, w, h = [float(v) for v in bbox]
    tr = transform or {}
    y_off = float(tr.get("y_offset_px", 0.0)) / float(max(1, canvas_height))
    exit_up = -10.0 / float(max(1, canvas_height))
    sc_peak = max(float(tr.get("scale_from", 1.0)), float(tr.get("scale_to", 1.0)))
    if str(easing).startswith("back.out"):
        sc_peak = max(sc_peak, float(tr.get("scale_to", 1.0)) * 1.12)
    extra_w = max(0.0, (sc_peak - 1.0) * w) / 2.0
    extra_h = max(0.0, (sc_peak - 1.0) * h) / 2.0
    x0 = x - extra_w
    x1 = x + w + extra_w
    y0 = min(y, y + y_off, y + exit_up) - extra_h
    y1 = max(y + h, y + h + y_off, y + h + exit_up) + extra_h
    return [round(x0, 4), round(y0, 4), round(max(0.001, x1 - x0), 4), round(max(0.001, y1 - y0), 4)]


def boxes_overlap(a: list[float], b: list[float], *, pad: float = 0.0) -> tuple[bool, float]:
    """Return (overlaps, intersection_ratio_of_min_area) for two normalized [x, y, w, h] boxes."""
    ax0, ay0, aw, ah = [float(v) for v in a]
    bx0, by0, bw, bh = [float(v) for v in b]
    ix0 = max(ax0 - pad, bx0)
    iy0 = max(ay0 - pad, by0)
    ix1 = min(ax0 + aw + pad, bx0 + bw)
    iy1 = min(ay0 + ah + pad, by0 + bh)
    iw = max(0.0, ix1 - ix0)
    ih = max(0.0, iy1 - iy0)
    area = iw * ih
    if area <= 1e-9:
        return False, 0.0
    min_area = max(1e-9, min(aw * ah, bw * bh))
    return True, round(area / min_area, 4)


def resolve_layout_avoiding_presenter(
    visual_events: list[dict[str, Any]],
    text_events: list[dict[str, Any]],
    *,
    presenter_reserved_region: list[float] | None,
    platform_safe_zone: dict[str, float] | None = None,
    canvas_width: int = 720,
    canvas_height: int = 1280,
    allow_reflow: bool = True,
) -> dict[str, Any]:
    """Swept-bounds auto-layout solver keeping all visual/text/caption events collision-free with reserved presenter zone.

    Never hides the presenter to resolve conflicts. Returns LAYOUT_CONFLICT diagnostics if no readable slot fits.
    """
    if not presenter_reserved_region or len(presenter_reserved_region) != 4:
        return {
            "ok": True,
            "status": "NO_RESERVED_PRESENTER_ZONE",
            "visual_events": [dict(ev) for ev in visual_events],
            "text_events": [dict(tx) for tx in text_events],
            "reflows": [],
            "conflicts": [],
        }

    sz = platform_safe_zone or {"top": 0.08, "bottom": 0.14, "left": 0.04, "right": 0.04}
    pr = [float(v) for v in presenter_reserved_region]
    px, py, pw, ph = pr
    pad = 0.015

    reflows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    out_vis: list[dict[str, Any]] = []
    out_txt: list[dict[str, Any]] = []

    def _find_safe_slot(orig: list[float], transform: dict[str, Any] | None, easing: str, min_w: float, min_h: float) -> list[float] | None:
        ox, oy, ow, oh = [float(v) for v in orig]
        # Account for peak spring overshoot expansion + entry y_offset when placing reflow candidates
        spring_extra = 0.065 if str(easing).startswith("back.out") else 0.01
        y_pad_extra = abs(float((transform or {}).get("y_offset_px", 0.0))) / float(max(1, canvas_height)) + 0.015
        cands: list[list[float]] = []
        # Candidate 1: Opposite horizontal column alongside presenter (e.g. right of BOTTOM_LEFT)
        if px < 0.42:
            rx = round(px + pw + pad + spring_extra * min(ow, 0.50), 4)
            avail_w = round((1.0 - sz["right"]) - rx, 4)
            if avail_w >= min_w:
                cw = min(ow, avail_w)
                ch = min(oh, max(min_h, (1.0 - sz["bottom"]) - max(sz["top"], oy)))
                cy = max(sz["top"], min(1.0 - sz["bottom"] - ch, oy))
                cands.append([rx, round(cy, 4), round(cw, 4), round(ch, 4)])
        else:
            avail_w = round((px - pad - spring_extra * min(ow, 0.50)) - sz["left"], 4)
            if avail_w >= min_w:
                cw = min(ow, avail_w)
                ch = min(oh, max(min_h, (1.0 - sz["bottom"]) - max(sz["top"], oy)))
                cy = max(sz["top"], min(1.0 - sz["bottom"] - ch, oy))
                cands.append([sz["left"], round(cy, 4), round(cw, 4), round(ch, 4)])
        # Candidate 2: Upper stage above presenter (with vertical entry/overshoot clearance)
        avail_above = round((py - pad - y_pad_extra - spring_extra * min(oh, 0.25)) - sz["top"], 4)
        if avail_above >= min_h:
            ch = min(oh, avail_above)
            cw = min(ow, (1.0 - sz["left"] - sz["right"]))
            cx = max(sz["left"], min(1.0 - sz["right"] - cw, ox))
            cy = max(sz["top"], py - pad - y_pad_extra - spring_extra * ch - ch)
            cands.append([round(cx, 4), round(cy, 4), round(cw, 4), round(ch, 4)])
        # Candidate 3: Lower stage below presenter (for TOP_LEFT / TOP_RIGHT presenter)
        avail_below = round((1.0 - sz["bottom"]) - (py + ph + pad + y_pad_extra), 4)
        if avail_below >= min_h:
            ch = min(oh, avail_below)
            cw = min(ow, (1.0 - sz["left"] - sz["right"]))
            cx = max(sz["left"], min(1.0 - sz["right"] - cw, ox))
            cy = py + ph + pad + y_pad_extra
            cands.append([round(cx, 4), round(cy, 4), round(cw, 4), round(ch, 4)])

        for cand in cands:
            sw = compute_swept_bbox(cand, transform=transform, easing=easing, canvas_width=canvas_width, canvas_height=canvas_height)
            ov, _ = boxes_overlap(sw, pr)
            if not ov and cand[0] >= sz["left"] - 0.02 and (cand[0] + cand[2]) <= (1.0 - sz["right"] + 0.02) and cand[1] >= sz["top"] - 0.02 and (cand[1] + cand[3]) <= (1.0 - sz["bottom"] + 0.02):
                return cand
        return None

    for ev in visual_events:
        ec = dict(ev)
        params = ec.get("params") or {}
        if params.get("kind") == "progress_spine" or ec.get("layer_cat") == "hand" or ec.get("face_exclusion") is False:
            out_vis.append(ec)
            continue
        bbox = [float(v) for v in ec["normalized_bbox"]]
        swept = compute_swept_bbox(
            bbox, transform=ec.get("transform"), easing=str(ec.get("easing", "power2.out")),
            canvas_width=canvas_width, canvas_height=canvas_height,
        )
        ov, ratio = boxes_overlap(swept, pr)
        if ov:
            if not allow_reflow:
                conflicts.append({
                    "kind": "visual_event",
                    "id": ec["event_id"],
                    "frames": [int(ec["start_frame"]), int(ec["end_frame"])],
                    "bbox": bbox,
                    "swept_bbox": swept,
                    "presenter_reserved": pr,
                    "overlap_ratio": ratio,
                    "code": "LAYOUT_CONFLICT",
                    "message": f"Visual event {ec['event_id']} swept bounds collide with reserved presenter zone ({ratio:.2%})",
                })
            else:
                slot = _find_safe_slot(bbox, ec.get("transform"), str(ec.get("easing", "power2.out")), min_w=0.34, min_h=0.10)
                if slot is not None:
                    reflows.append({"kind": "visual_event", "id": ec["event_id"], "from_bbox": bbox, "to_bbox": slot, "reason": "Avoid reserved presenter PIP zone"})
                    ec["normalized_bbox"] = slot
                else:
                    conflicts.append({
                        "kind": "visual_event",
                        "id": ec["event_id"],
                        "frames": [int(ec["start_frame"]), int(ec["end_frame"])],
                        "bbox": bbox,
                        "swept_bbox": swept,
                        "presenter_reserved": pr,
                        "overlap_ratio": ratio,
                        "code": "LAYOUT_CONFLICT",
                        "message": f"No collision-free slot for visual event {ec['event_id']} without violating readability or hiding presenter",
                    })
        out_vis.append(ec)

    for tx in text_events:
        tc = dict(tx)
        pos = [float(v) for v in tc["position"]]
        swept = compute_swept_bbox(pos, transform={"y_offset_px": 6}, easing="power2.out", canvas_width=canvas_width, canvas_height=canvas_height)
        ov, ratio = boxes_overlap(swept, pr)
        if ov:
            if not allow_reflow:
                conflicts.append({
                    "kind": "text_event",
                    "id": tc["text_id"],
                    "frames": [int(tc["start_frame"]), int(tc["end_frame"])],
                    "bbox": pos,
                    "swept_bbox": swept,
                    "presenter_reserved": pr,
                    "overlap_ratio": ratio,
                    "code": "LAYOUT_CONFLICT",
                    "message": f"Text/caption {tc['text_id']} collides with reserved presenter zone ({ratio:.2%})",
                })
            else:
                slot = _find_safe_slot(pos, {"y_offset_px": 6}, "power2.out", min_w=0.34, min_h=0.08)
                if slot is not None:
                    reflows.append({"kind": "text_event", "id": tc["text_id"], "from_bbox": pos, "to_bbox": slot, "reason": "Avoid reserved presenter PIP zone"})
                    tc["position"] = slot
                else:
                    conflicts.append({
                        "kind": "text_event",
                        "id": tc["text_id"],
                        "frames": [int(tc["start_frame"]), int(tc["end_frame"])],
                        "bbox": pos,
                        "swept_bbox": swept,
                        "presenter_reserved": pr,
                        "overlap_ratio": ratio,
                        "code": "LAYOUT_CONFLICT",
                        "message": f"No collision-free slot for text/caption {tc['text_id']} without hiding presenter",
                    })
        out_txt.append(tc)

    return {
        "ok": len(conflicts) == 0,
        "status": "PASS" if not conflicts else "LAYOUT_CONFLICT",
        "visual_events": out_vis,
        "text_events": out_txt,
        "reflows": reflows,
        "conflicts": conflicts,
    }
