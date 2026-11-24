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
) -> list[dict[str, Any]]:
    """Plan semantic typography hierarchy sized for both 360px mobile legibility and box fit."""
    min_mobile_px = int(math.ceil(11.2 * (float(canvas_width) / 360.0)))
    is_landscape = canvas_width > canvas_height
    box_pos = [0.06, 0.62, 0.88, 0.24] if is_landscape else [0.06, 0.68, 0.88, 0.18]
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
                "position": [0.08, 0.82, 0.84, 0.10],
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
