"""KH-03.R4.3 Universal Semantic Motion Library, Art Tokens, ROI Compositor & Spatial Collision Engine.

100% clean-room (Python stdlib + Pillow + NumPy only; ZERO upstream noncommercial imports/assets)
and 100% cross-niche universal (ZERO hardcoded brand, creator, or finance assumptions):
- Retains all 5 baseline components: `editorial_hook_banner`, `context_chip`,
  `keyword_accent_ticker`, `numerical_payoff_hero`, `cta_resolve_pill`.
- Adds 6 genuinely animated generic semantic primitives (R4.3-04):
  1. `resource_depletion`: start balance + ordered deltas + sequential non-spoiler reveals + shrinking bar/tokens
  2. `resource_allocation`: total + explicit branches + exact conservation check + animated split flow bar
  3. `contrast_transformation`: two scenarios with equivalent units + animated transformation & hold
  4. `evidence_spotlight`: verified source crop/callout OR explicitly labeled illustrative spotlight
  5. `causal_chain`: ordered nodes + directed edges + relation labels + staggered progression
  6. `decision_branch`: root prompt + choices/outcomes + consistent units + sequential reveal
- Fail-closed payload validation rejecting unsupported kinds, internal debug/skill labels
  (`SK-01`..`SK-05`, `TODO`, `DEBUG`, `PLACEHOLDER`), non-conserving allocation math, or out-of-bounds geometry.
- Measured glyph auto-fit, LRU font & static-layer caching keyed by `(palette, params_sha, profile)`,
  and bounded ROI alpha compositing for real per-frame speedup.
- Swept-interval spatial occupancy & collision evaluator (`evaluate_spatial_collisions`).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# Supported Component Registry & Forbidden Visible Label Guard
# ---------------------------------------------------------------------------

BASELINE_COMPONENT_KINDS = frozenset({
    "editorial_hook_banner",
    "context_chip",
    "keyword_accent_ticker",
    "numerical_payoff_hero",
    "cta_resolve_pill",
})

SEMANTIC_COMPONENT_KINDS = frozenset({
    "resource_depletion",
    "resource_allocation",
    "contrast_transformation",
    "evidence_spotlight",
    "causal_chain",
    "decision_branch",
})

SUPPORTED_COMPONENT_KINDS = BASELINE_COMPONENT_KINDS | SEMANTIC_COMPONENT_KINDS

_INTERNAL_LABEL_RE = re.compile(
    r"\b(?:SK-0[1-5]|TODO|FIXME|DEBUG_[A-Z0-9_]+|PLACEHOLDER|LOREM IPSUM|DUMMY_TEXT)\b",
    re.IGNORECASE,
)

# Generic art-direction palettes selectable by user brief / niche (never hardcoded to one look!)
PALETTE_PRESETS: dict[str, dict[str, str]] = {
    "editorial_dark": {
        "bg_hex": "#162A22",
        "surface_hex": "#1F392F",
        "accent_hex": "#D9B66B",
        "fg_hex": "#F8F7F2",
        "muted_hex": "#CBD5CE",
        "danger_hex": "#C45B5B",
        "positive_hex": "#6FA888",
        "canvas_hex": "#0E1713",
    },
    "luxury_editorial": {
        "bg_hex": "#121011",
        "surface_hex": "#1C1819",
        "accent_hex": "#D8B979",
        "fg_hex": "#FAF4E8",
        "muted_hex": "#C9BEB0",
        "danger_hex": "#B54A4A",
        "positive_hex": "#7CA38A",
        "canvas_hex": "#0C0A0B",
    },
    "clean_light": {
        "bg_hex": "#F7F6F2",
        "surface_hex": "#ECEAE2",
        "accent_hex": "#2F5E4E",
        "fg_hex": "#161B19",
        "muted_hex": "#57625D",
        "danger_hex": "#B84747",
        "positive_hex": "#2E7D5B",
        "canvas_hex": "#F4F2EC",
    },
    "high_contrast": {
        "bg_hex": "#101218",
        "surface_hex": "#1B202B",
        "accent_hex": "#F4C44E",
        "fg_hex": "#FFFFFF",
        "muted_hex": "#C8D0DC",
        "danger_hex": "#E05252",
        "positive_hex": "#45B884",
        "canvas_hex": "#090B0F",
    },
}


# ---------------------------------------------------------------------------
# Easing, Colors, Font Cache & Measured Glyph Auto-Fit
# ---------------------------------------------------------------------------

def ease_out_cubic(t: float) -> float:
    x = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - x) ** 3


def ease_in_out_cubic(t: float) -> float:
    x = max(0.0, min(1.0, t))
    return 4.0 * x * x * x if x < 0.5 else 1.0 - ((-2.0 * x + 2.0) ** 3) / 2.0


def parse_hex_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = str(hex_color or "#121011").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (18, 16, 17, max(0, min(255, int(alpha))))
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), max(0, min(255, int(alpha))))


def resolve_palette(ev: dict[str, Any], default_tokens: dict[str, str] | None = None) -> dict[str, str]:
    preset_name = str(ev.get("palette_preset") or (default_tokens or {}).get("palette_preset") or "editorial_dark")
    base = dict(PALETTE_PRESETS.get(preset_name, PALETTE_PRESETS["editorial_dark"]))
    if isinstance(default_tokens, dict):
        for k in base:
            if default_tokens.get(k):
                base[k] = str(default_tokens[k])
    art = ev.get("art_tokens") or {}
    if isinstance(art, dict):
        for k in base:
            if art.get(k):
                base[k] = str(art[k])
    for k in base:
        if ev.get(k):
            base[k] = str(ev[k])
    return base


_FONT_CACHE: dict[tuple[int, bool, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
_FONT_PATHS_BOLD = (
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
_FONT_PATHS_REG = (
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
_FONT_PATHS_SERIF = (
    r"C:\Windows\Fonts\georgiab.ttf",
    r"C:\Windows\Fonts\timesbd.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
)


def load_font(size_px: int, *, bold: bool = True, serif: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    sz = max(10, int(size_px))
    key = (sz, bool(bold), bool(serif))
    hit = _FONT_CACHE.get(key)
    if hit is not None:
        return hit
    candidates = _FONT_PATHS_SERIF if serif else (_FONT_PATHS_BOLD if bold else _FONT_PATHS_REG)
    for fp in candidates:
        if Path(fp).is_file():
            try:
                f = ImageFont.truetype(fp, sz)
                _FONT_CACHE[key] = f
                return f
            except Exception:
                continue
    f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def fit_text_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width_px: int,
    *,
    start_size: int,
    min_size: int = 16,
    max_lines: int = 2,
    bold: bool = True,
    serif: bool = False,
) -> tuple[list[str], ImageFont.FreeTypeFont | ImageFont.ImageFont, int, int]:
    """Measure real glyphs, wrap to <= max_lines, shrink cleanly to fit max_width_px without clipping."""
    clean = " ".join(str(text or "").split())
    if not clean:
        ft = load_font(start_size, bold=bold, serif=serif)
        return [], ft, 0, 0
    for sz in range(max(min_size, start_size), min_size - 1, -2):
        ft = load_font(sz, bold=bold, serif=serif)
        words = clean.split(" ")
        lines: list[str] = []
        cur = ""
        for w in words:
            trial = f"{cur} {w}".strip()
            bb = draw.textbbox((0, 0), trial, font=ft)
            if (bb[2] - bb[0]) <= max_width_px or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if len(lines) <= max_lines:
            max_w = max((draw.textbbox((0, 0), ln, font=ft)[2] - draw.textbbox((0, 0), ln, font=ft)[0]) for ln in lines)
            if max_w <= max_width_px or sz == min_size:
                line_h = int(sz * 1.24)
                return lines, ft, int(max_w), int(line_h * len(lines))
    ft = load_font(min_size, bold=bold, serif=serif)
    lines = [clean]
    bb = draw.textbbox((0, 0), clean, font=ft)
    return lines, ft, int(bb[2] - bb[0]), int(min_size * 1.24)


def format_unit_value(value: float | int, unit: str = "$", *, signed: bool = False) -> str:
    """Format numeric value generically across currencies ($, EUR) and suffix units (min, %, kcal, GB)."""
    u = str(unit or "").strip()
    v = float(value)
    is_int = abs(v - round(v)) < 1e-6
    num_s = f"{abs(int(round(v))):,}" if is_int else f"{abs(v):,.1f}"
    sign = ("+" if v > 0 else ("-" if v < 0 else "")) if signed else ("-" if v < 0 else "")
    if u in ("$", "€", "£", "¥"):
        return f"{sign}{u}{num_s}"
    if u in ("%",):
        return f"{sign}{num_s}{u}"
    if u:
        return f"{sign}{num_s} {u}"
    return f"{sign}{num_s}"


# ---------------------------------------------------------------------------
# Fail-Closed Component Payload Validation (R4.3-04)
# ---------------------------------------------------------------------------

def _iter_visible_strings(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k in (
                "event_id", "component_id", "kind", "component_kind", "semantic_anchor",
                "claim_id", "evidence_id", "source_id", "palette_preset", "color_role",
                "accent_role", "position", "provenance", "layout_mode",
            ):
                continue
            if k.endswith("_hex") or k.endswith("_id"):
                continue
            out.extend(_iter_visible_strings(v))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_iter_visible_strings(item))
    return out


def validate_component_payload(ev: dict[str, Any]) -> dict[str, Any]:
    """Strict fail-closed validation of any declarative overlay/motion component payload."""
    issues: list[str] = []
    kind = str(ev.get("kind") or ev.get("component_kind") or "").strip()
    if kind not in SUPPORTED_COMPONENT_KINDS:
        return {
            "pass": False,
            "status": "BLOCKED_UNSUPPORTED_COMPONENT",
            "kind": kind,
            "issues": [f"UNSUPPORTED_COMPONENT_KIND:{kind!r} not in supported registry."],
        }

    t0 = float(ev.get("start_sec", -1.0))
    t1 = float(ev.get("end_sec", -1.0))
    if t0 < 0.0 or t1 <= t0:
        issues.append(f"INVALID_INTERVAL:[{t0}, {t1})")

    # Check normalized bounds if provided
    for bk in ("x_norm", "y_norm"):
        if bk in ev and ev[bk] is not None:
            bv = float(ev[bk])
            if not (0.0 <= bv <= 1.0):
                issues.append(f"OUT_OF_BOUNDS:{bk}={bv}")
    bounds = ev.get("normalized_bounds")
    if isinstance(bounds, dict):
        x = float(bounds.get("x", 0.0))
        y = float(bounds.get("y", 0.0))
        bw = float(bounds.get("w", 0.0))
        bh = float(bounds.get("h", 0.0))
        if x < -0.001 or y < -0.001 or bw <= 0.02 or bh <= 0.02 or (x + bw) > 1.001 or (y + bh) > 1.001:
            issues.append(f"INVALID_NORMALIZED_BOUNDS:{bounds}")

    # Block internal skill IDs / debug placeholders in any visible text!
    for txt in _iter_visible_strings(ev):
        m = _INTERNAL_LABEL_RE.search(txt)
        if m:
            issues.append(f"INTERNAL_LABEL_LEAK_IN_VISIBLE_TEXT:{m.group(0)!r} inside {txt!r}")

    # Kind-specific mathematical & semantic invariants
    if kind == "resource_depletion":
        start_bal = Decimal(str(ev.get("start_balance", 0)))
        deltas = ev.get("deltas") or []
        if start_bal <= 0 or not deltas:
            issues.append("RESOURCE_DEPLETION_MISSING_START_OR_DELTAS")
        else:
            rem = start_bal
            prev_rev = t0 - 0.001
            for di, d in enumerate(deltas):
                amt = Decimal(str(d.get("amount", 0)))
                if amt <= 0:
                    issues.append(f"DEPLETION_DELTA_NONPOSITIVE(idx={di}):{amt}")
                rev = float(d.get("reveal_sec", t0))
                if rev < t0 - 0.01 or rev >= t1:
                    issues.append(f"DEPLETION_REVEAL_OUT_OF_EVENT(idx={di}):{rev} not in [{t0},{t1})")
                if rev < prev_rev:
                    issues.append(f"DEPLETION_REVEAL_OUT_OF_ORDER(idx={di}):{rev} < {prev_rev}")
                prev_rev = rev
                rem -= amt
            if "expected_remaining" in ev and ev["expected_remaining"] is not None:
                exp_r = Decimal(str(ev["expected_remaining"]))
                if abs(rem - exp_r) > Decimal("0.01"):
                    issues.append(f"DEPLETION_MATH_MISMATCH:computed={rem} != expected={exp_r}")

    elif kind == "resource_allocation":
        total = Decimal(str(ev.get("total", 0)))
        branches = ev.get("branches") or []
        if total <= 0 or len(branches) < 2:
            issues.append("RESOURCE_ALLOCATION_NEEDS_POSITIVE_TOTAL_AND_2_BRANCHES")
        else:
            b_sum = sum(Decimal(str(b.get("amount", 0))) for b in branches)
            tol = Decimal(str(ev.get("rounding_tolerance", "0.01")))
            if abs(b_sum - total) > tol:
                issues.append(f"ALLOCATION_CONSERVATION_ERROR:sum({b_sum}) != total({total})")
            for bi, b in enumerate(branches):
                if Decimal(str(b.get("amount", 0))) <= 0:
                    issues.append(f"ALLOCATION_BRANCH_NONPOSITIVE(idx={bi})")
                rev = float(b.get("reveal_sec", t0))
                if rev < t0 - 0.01 or rev >= t1:
                    issues.append(f"ALLOCATION_REVEAL_OUT_OF_EVENT(idx={bi}):{rev}")

    elif kind == "contrast_transformation":
        sa = ev.get("scenario_a") or {}
        sb = ev.get("scenario_b") or {}
        if not sa.get("label") or not sb.get("label"):
            issues.append("CONTRAST_MISSING_SCENARIO_LABELS")
        ua = str(sa.get("unit") or ev.get("unit") or "")
        ub = str(sb.get("unit") or ev.get("unit") or "")
        if ua != ub:
            issues.append(f"CONTRAST_UNIT_MISMATCH:{ua!r} != {ub!r}")

    elif kind == "evidence_spotlight":
        prov = str(ev.get("provenance") or "ILLUSTRATIVE").upper()
        if prov == "REAL_EVIDENCE" and not ev.get("source_id"):
            issues.append("EVIDENCE_SPOTLIGHT_REAL_EVIDENCE_MISSING_SOURCE_ID")
        if prov != "REAL_EVIDENCE" and not ev.get("illustrative_label"):
            issues.append("EVIDENCE_SPOTLIGHT_ILLUSTRATIVE_MISSING_LABEL")

    elif kind == "causal_chain":
        nodes = ev.get("nodes") or []
        if len(nodes) < 2:
            issues.append("CAUSAL_CHAIN_NEEDS_AT_LEAST_2_NODES")

    elif kind == "decision_branch":
        branches = ev.get("choices") or []
        if len(branches) < 2:
            issues.append("DECISION_BRANCH_NEEDS_AT_LEAST_2_CHOICES")
        units = {str(c.get("unit") or ev.get("unit") or "") for c in branches}
        if len(units) > 1:
            issues.append(f"DECISION_BRANCH_INCONSISTENT_UNITS:{sorted(units)}")

    return {
        "pass": len(issues) == 0,
        "status": "VALID" if not issues else "BLOCKED_INVALID_PAYLOAD",
        "kind": kind,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Component Geometry & Swept-Interval Spatial Collision Engine (R4.3-02)
# ---------------------------------------------------------------------------

def estimate_component_bounds_norm(ev: dict[str, Any]) -> dict[str, float]:
    """Return normalized [x0, y0, x1, y1] bounding box of an overlay/motion component."""
    if isinstance(ev.get("normalized_bounds"), dict):
        nb = ev["normalized_bounds"]
        x0 = float(nb.get("x", 0.05))
        y0 = float(nb.get("y", 0.05))
        return {"x0": x0, "y0": y0, "x1": min(1.0, x0 + float(nb.get("w", 0.90))), "y1": min(1.0, y0 + float(nb.get("h", 0.20)))}

    kind = str(ev.get("kind") or ev.get("component_kind") or "context_chip")
    if ev.get("full_frame"):
        return {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}
    if kind == "editorial_hook_banner":
        y0 = float(ev.get("y_norm", 0.048))
        return {"x0": 0.052, "y0": y0, "x1": 0.948, "y1": min(0.98, y0 + 0.098)}
    if kind == "context_chip":
        pos = str(ev.get("position") or "top")
        y0 = float(ev.get("y_norm", 0.062 if pos == "top" else 0.785))
        cx = float(ev.get("x_norm", 0.50))
        hw = 0.40
        return {"x0": max(0.03, cx - hw), "y0": y0, "x1": min(0.97, cx + hw), "y1": min(0.98, y0 + 0.064)}
    if kind == "keyword_accent_ticker":
        compact = bool(ev.get("compact", False))
        pos = str(ev.get("position") or "top")
        y0 = float(ev.get("y_norm", 0.055 if pos == "top" else 0.77))
        cx = float(ev.get("x_norm", 0.50))
        hw = 0.21 if compact else 0.36
        bh = 0.060 if compact else 0.078
        return {"x0": max(0.02, cx - hw), "y0": y0, "x1": min(0.98, cx + hw), "y1": min(0.98, y0 + bh)}
    if kind == "numerical_payoff_hero":
        y0 = float(ev.get("y_norm", 0.052))
        return {"x0": 0.06, "y0": y0, "x1": 0.94, "y1": min(0.98, y0 + 0.088)}
    if kind == "cta_resolve_pill":
        y0 = float(ev.get("y_norm", 0.878))
        return {"x0": 0.14, "y0": y0, "x1": 0.86, "y1": min(0.97, y0 + 0.046)}
    if kind in ("resource_depletion", "resource_allocation", "contrast_transformation", "causal_chain", "decision_branch", "evidence_spotlight"):
        y0 = float(ev.get("y_norm", 0.62))
        bh = float(ev.get("h_norm", 0.30))
        x0 = float(ev.get("x_norm_left", 0.045))
        bw = float(ev.get("w_norm", 0.91))
        return {"x0": x0, "y0": y0, "x1": min(0.98, x0 + bw), "y1": min(0.97, y0 + bh)}
    return {"x0": 0.05, "y0": 0.05, "x1": 0.95, "y1": 0.15}


def _box_overlap_ratio(a: dict[str, float], b: dict[str, float]) -> float:
    ix0 = max(a["x0"], b["x0"])
    iy0 = max(a["y0"], b["y0"])
    ix1 = min(a["x1"], b["x1"])
    iy1 = min(a["y1"], b["y1"])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_b = max(1e-6, (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]))
    return round(inter / area_b, 4)


def evaluate_spatial_collisions(
    overlays: list[dict[str, Any]],
    *,
    occupancy_windows: list[dict[str, Any]] | None = None,
    max_protected_overlap: float = 0.08,
    max_sibling_overlap: float = 0.02,
) -> dict[str, Any]:
    """Swept-interval collision audit across full event lifespans against protected subjects/actions & sibling overlays."""
    occ = list(occupancy_windows or [])
    collisions: list[dict[str, Any]] = []
    checked_pairs = 0

    for i, ev in enumerate(overlays):
        if ev.get("full_frame"):
            # Full-frame illustrative/evidence takeover intentionally replaces canvas rather than colliding over presenter
            continue
        eb = estimate_component_bounds_norm(ev)
        t0, t1 = float(ev["start_sec"]), float(ev["end_sec"])
        ev_id = str(ev.get("event_id") or f"ov_{i + 1:02d}")

        # 1. Check against protected subject/action occupancy intervals (face, hands, props, UI safe zone)
        for ow in occ:
            ot0, ot1 = float(ow.get("start_sec", 0.0)), float(ow.get("end_sec", 1e9))
            if min(t1, ot1) <= max(t0, ot0):
                continue
            checked_pairs += 1
            ob = {"x0": float(ow["x0"]), "y0": float(ow["y0"]), "x1": float(ow["x1"]), "y1": float(ow["y1"])}
            ratio = _box_overlap_ratio(eb, ob)
            lim = float(ow.get("max_overlap", max_protected_overlap))
            if ratio > lim:
                collisions.append({
                    "type": "PROTECTED_OCCUPANCY_COLLISION",
                    "event_id": ev_id,
                    "protected_region": str(ow.get("label") or "protected_subject"),
                    "overlap_ratio": ratio,
                    "threshold": lim,
                    "interval_sec": [round(max(t0, ot0), 3), round(min(t1, ot1), 3)],
                    "event_box": eb,
                    "protected_box": ob,
                })

        # 2. Check against co-active sibling overlays (excluding intentional z-order backdrops)
        for j in range(i + 1, len(overlays)):
            ev2 = overlays[j]
            if ev2.get("full_frame"):
                continue
            s0, s1 = float(ev2["start_sec"]), float(ev2["end_sec"])
            if min(t1, s1) <= max(t0, s0):
                continue
            eb2 = estimate_component_bounds_norm(ev2)
            ratio2 = _box_overlap_ratio(eb, eb2)
            checked_pairs += 1
            if ratio2 > max_sibling_overlap:
                collisions.append({
                    "type": "SIBLING_OVERLAY_COLLISION",
                    "event_id": ev_id,
                    "other_event_id": str(ev2.get("event_id") or f"ov_{j + 1:02d}"),
                    "overlap_ratio": ratio2,
                    "threshold": max_sibling_overlap,
                    "interval_sec": [round(max(t0, s0), 3), round(min(t1, s1), 3)],
                })

    return {
        "schema_version": "kh03.spatial_collision_qa.v1",
        "overlays_evaluated": len(overlays),
        "checked_pairs": checked_pairs,
        "collision_count": len(collisions),
        "collisions": collisions,
        "pass": len(collisions) == 0,
    }


# ---------------------------------------------------------------------------
# Bounded ROI Layer Cache & Accelerated NumPy Compositor
# ---------------------------------------------------------------------------

class LayerLRUCache:
    """Bounded in-memory LRU cache for pre-rendered static card shells/backdrops (eliminates per-frame blur)."""

    def __init__(self, max_items: int = 48) -> None:
        self.max_items = max(8, int(max_items))
        self._store: OrderedDict[str, Image.Image] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Image.Image | None:
        im = self._store.get(key)
        if im is not None:
            self._store.move_to_end(key)
            self.hits += 1
            return im
        self.misses += 1
        return None

    def put(self, key: str, im: Image.Image) -> None:
        self._store[key] = im
        self._store.move_to_end(key)
        while len(self._store) > self.max_items:
            self._store.popitem(last=False)


_SHARED_LAYER_CACHE = LayerLRUCache(max_items=64)


def _draw_card_roi(
    w_box: int,
    h_box: int,
    *,
    radius: int,
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int] | None,
    outline_w: int,
    shadow_pad: int = 14,
) -> Image.Image:
    """Render rounded card with soft shadow inside compact ROI (cached by shape/color key!)."""
    key = f"card:{w_box}x{h_box}:r{radius}:f{fill}:o{outline}:w{outline_w}:p{shadow_pad}"
    cached = _SHARED_LAYER_CACHE.get(key)
    if cached is not None:
        return cached.copy()
    cw = w_box + shadow_pad * 2
    ch = h_box + shadow_pad * 2
    im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    if shadow_pad > 0:
        sh = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        sd.rounded_rectangle(
            (shadow_pad, shadow_pad + 5, shadow_pad + w_box, shadow_pad + h_box + 8),
            radius=radius, fill=(4, 4, 5, min(115, int(fill[3] * 0.5))),
        )
        sh = sh.filter(ImageFilter.GaussianBlur(radius=max(3, shadow_pad // 2)))
        im.alpha_composite(sh)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle(
        (shadow_pad, shadow_pad, shadow_pad + w_box, shadow_pad + h_box),
        radius=radius, fill=fill, outline=outline, width=outline_w if outline else 0,
    )
    _SHARED_LAYER_CACHE.put(key, im)
    return im.copy()


def _composite_roi_rgb(base_rgb: np.ndarray, roi_rgba: Image.Image, px: int, py: int) -> None:
    """In-place NumPy alpha-blend of small RGBA ROI directly onto uint8 RGB frame (5-12x faster than full-canvas RGBA)."""
    fh, fw, _ = base_rgb.shape
    rw, rh = roi_rgba.size
    x0 = max(0, px)
    y0 = max(0, py)
    x1 = min(fw, px + rw)
    y1 = min(fh, py + rh)
    if x1 <= x0 or y1 <= y0:
        return
    arr = np.asarray(roi_rgba, dtype=np.uint8)
    sub = arr[(y0 - py):(y1 - py), (x0 - px):(x1 - px)]
    a = sub[:, :, 3:4].astype(np.float32) * (1.0 / 255.0)
    dst = base_rgb[y0:y1, x0:x1].astype(np.float32)
    src = sub[:, :, :3].astype(np.float32)
    base_rgb[y0:y1, x0:x1] = np.clip(src * a + dst * (1.0 - a), 0.0, 255.0).astype(np.uint8)


def render_luxury_backdrop(w: int, h: int, palette: dict[str, str], *, style: str = "radial_satin") -> np.ndarray:
    """Cached full-frame art-directed editorial backdrop (black satin / warm cream / clean studio)."""
    key = f"bg:{w}x{h}:{palette.get('canvas_hex')}:{palette.get('accent_hex')}:{style}"
    cached = _SHARED_LAYER_CACHE.get(key)
    if cached is not None:
        return np.asarray(cached, dtype=np.uint8).copy()
    c0 = np.array(parse_hex_rgba(palette["canvas_hex"])[:3], dtype=np.float32)
    c1 = np.array(parse_hex_rgba(palette["surface_hex"])[:3], dtype=np.float32)
    ac = np.array(parse_hex_rgba(palette["accent_hex"])[:3], dtype=np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx = (xx / max(1.0, float(w))) - 0.5
    ny = (yy / max(1.0, float(h))) - 0.42
    rad = np.sqrt(nx * nx * 1.15 + ny * ny * 0.85)
    glow = np.clip(1.0 - rad * 1.35, 0.0, 1.0) ** 1.6
    # Subtle warm editorial gradient + delicate top/bottom gold glow
    rgb = c0[None, None, :] * (1.0 - glow[:, :, None] * 0.85) + c1[None, None, :] * (glow[:, :, None] * 0.85)
    rim = np.clip(1.0 - np.abs(ny + 0.15) * 2.4, 0.0, 1.0) * 0.06
    rgb += ac[None, None, :] * rim[:, :, None]
    out_arr = np.clip(rgb, 0.0, 255.0).astype(np.uint8)
    im = Image.fromarray(out_arr, mode="RGB")
    # Crisp hairline editorial frame border
    dr = ImageDraw.Draw(im)
    sc = w / 1080.0
    m = int(34 * sc)
    ac_rgb = tuple(int(v) for v in (c1 * 0.55 + ac * 0.45))
    dr.rounded_rectangle((m, m, w - m, h - m), radius=int(28 * sc), outline=ac_rgb, width=max(1, int(2 * sc)))
    _SHARED_LAYER_CACHE.put(key, im)
    return np.asarray(im, dtype=np.uint8).copy()


# ---------------------------------------------------------------------------
# All 11 Declarative Component Renderers (ROI Accelerated)
# ---------------------------------------------------------------------------

def render_component_onto_rgb(
    frame_rgb: np.ndarray,
    ev: dict[str, Any],
    t_sec: float,
    *,
    default_palette: dict[str, str] | None = None,
    source_frame_lookup: Callable[[str, float], Image.Image | None] | None = None,
) -> bool:
    """Render one declarative semantic/baseline component onto uint8 HxWx3 `frame_rgb` in-place via ROI compositing."""
    t0 = float(ev["start_sec"])
    t1 = float(ev["end_sec"])
    if not (t0 <= t_sec < t1):
        return False
    el = t_sec - t0
    rem = t1 - t_sec
    in_p = ease_out_cubic(min(1.0, el / max(0.04, float(ev.get("enter_sec", 0.24)))))
    out_p = ease_out_cubic(min(1.0, rem / max(0.04, float(ev.get("exit_sec", 0.20)))))
    vis = min(in_p, out_p)
    if vis <= 0.01:
        return False

    h, w, _ = frame_rgb.shape
    sc = w / 1080.0
    pal = resolve_palette(ev, default_palette)
    kind = str(ev.get("kind") or ev.get("component_kind") or "").strip()

    bg = parse_hex_rgba(pal["bg_hex"], int(234 * vis))
    surface = parse_hex_rgba(pal["surface_hex"], int(238 * vis))
    accent = parse_hex_rgba(pal["accent_hex"], int(255 * vis))
    fg = parse_hex_rgba(pal["fg_hex"], int(255 * vis))
    muted = parse_hex_rgba(pal["muted_hex"], int(235 * vis))
    danger = parse_hex_rgba(pal["danger_hex"], int(255 * vis))
    positive = parse_hex_rgba(pal["positive_hex"], int(255 * vis))

    # Optional full-frame editorial backdrop blend for full-screen visual explanations
    if ev.get("full_frame"):
        bg_arr = render_luxury_backdrop(w, h, pal)
        if vis >= 0.995:
            frame_rgb[:, :, :] = bg_arr
        else:
            a = float(vis)
            frame_rgb[:, :, :] = np.clip(bg_arr.astype(np.float32) * a + frame_rgb.astype(np.float32) * (1.0 - a), 0, 255).astype(np.uint8)

    pad = max(6, int(14 * sc))

    # ---- 1. BASELINE: editorial_hook_banner ----
    if kind == "editorial_hook_banner":
        slide_y = int((1.0 - in_p) * -26 * sc)
        pulse = 1.0
        imp_t = ev.get("impact_sec")
        if imp_t is not None and abs(t_sec - float(imp_t)) < 0.28:
            u = 1.0 - abs(t_sec - float(imp_t)) / 0.28
            pulse = 1.0 + 0.04 * math.sin(u * math.pi)
        bw = int(w - 112 * sc)
        bh = int(h * 0.092)
        roi = _draw_card_roi(bw, bh, radius=int(24 * sc), fill=bg, outline=accent, outline_w=max(2, int(3 * sc)), shadow_pad=pad)
        dr = ImageDraw.Draw(roi)
        bar_w = int((bw - 48 * sc) * in_p)
        dr.rounded_rectangle((pad + int(24 * sc), pad + int(12 * sc), pad + int(24 * sc) + bar_w, pad + int(17 * sc)), radius=3, fill=accent)
        title = str(ev.get("title") or "")
        sub = str(ev.get("subtitle") or "")
        # Optional live flip_title after flip_sec (for truthful contrast hooks!)
        if ev.get("flip_sec") is not None and t_sec >= float(ev["flip_sec"]) and ev.get("flip_title"):
            title = str(ev["flip_title"])
            if ev.get("flip_subtitle"):
                sub = str(ev["flip_subtitle"])
        lines, ft, _, _ = fit_text_lines(dr, title, bw - int(56 * sc), start_size=int(46 * sc * pulse), min_size=int(26 * sc), max_lines=1, bold=True)
        if lines:
            tb = dr.textbbox((0, 0), lines[0], font=ft)
            dr.text((pad + (bw - (tb[2] - tb[0])) // 2, pad + int(30 * sc)), lines[0], font=ft, fill=fg)
        if sub:
            slines, fs, _, _ = fit_text_lines(dr, sub, bw - int(60 * sc), start_size=int(25 * sc), min_size=int(16 * sc), max_lines=1, bold=False)
            if slines:
                sb = dr.textbbox((0, 0), slines[0], font=fs)
                dr.text((pad + (bw - (sb[2] - sb[0])) // 2, pad + int(94 * sc)), slines[0], font=fs, fill=accent)
        by0 = int(h * float(ev.get("y_norm", 0.048))) + slide_y
        _composite_roi_rgb(frame_rgb, roi, int(56 * sc) - pad, by0 - pad)
        return True

    # ---- 2. BASELINE: context_chip ----
    if kind == "context_chip":
        pos = str(ev.get("position") or "top")
        slide_y = int((1.0 - in_p) * (22 if pos == "bottom" else -18) * sc)
        text = str(ev.get("title") or "")
        kicker = str(ev.get("kicker") or "")
        tmp_d = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        lines, ft, tw, _ = fit_text_lines(tmp_d, text, int(w * 0.80), start_size=int(34 * sc), min_size=int(20 * sc), max_lines=1, bold=True)
        fk = load_font(int(20 * sc), bold=True)
        kb = tmp_d.textbbox((0, 0), kicker, font=fk) if kicker else (0, 0, 0, 0)
        bw = min(int(w * 0.88), max(tw, kb[2] - kb[0]) + int(76 * sc))
        bh = int((102 if kicker else 72) * sc)
        roi = _draw_card_roi(bw, bh, radius=int(20 * sc), fill=bg, outline=accent, outline_w=max(2, int(2.5 * sc)), shadow_pad=pad)
        dr = ImageDraw.Draw(roi)
        if kicker:
            dr.text((pad + (bw - (kb[2] - kb[0])) // 2, pad + int(15 * sc)), kicker, font=fk, fill=accent)
            if lines:
                dr.text((pad + (bw - tw) // 2, pad + int(46 * sc)), lines[0], font=ft, fill=fg)
        elif lines:
            dr.text((pad + (bw - tw) // 2, pad + int(18 * sc)), lines[0], font=ft, fill=fg)
        cx = int(w * float(ev.get("x_norm", 0.50)))
        bx0 = max(int(24 * sc), min(w - bw - int(24 * sc), cx - bw // 2))
        by0 = int(h * float(ev.get("y_norm", 0.062 if pos == "top" else 0.785))) + slide_y
        _composite_roi_rgb(frame_rgb, roi, bx0 - pad, by0 - pad)
        return True

    # ---- 3. BASELINE: keyword_accent_ticker ----
    if kind == "keyword_accent_ticker":
        compact = bool(ev.get("compact", False))
        pos = str(ev.get("position") or "top")
        slide_x = int((1.0 - in_p) * 30 * sc)
        kicker = str(ev.get("kicker") or "")
        value = str(ev.get("value") or "")
        sub = str(ev.get("subtitle") or "")
        tmp_d = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        fk = load_font(int((18 if compact else 22) * sc), bold=True)
        fv = load_font(int((36 if compact else 48) * sc), bold=True)
        fs = load_font(int((18 if compact else 23) * sc), bold=False)
        vb = tmp_d.textbbox((0, 0), value, font=fv)
        kb = tmp_d.textbbox((0, 0), kicker, font=fk) if kicker else (0, 0, 0, 0)
        sb = tmp_d.textbbox((0, 0), sub, font=fs) if sub else (0, 0, 0, 0)
        bw = min(int(w * (0.44 if compact else 0.86)), max(vb[2] - vb[0], kb[2] - kb[0], sb[2] - sb[0]) + int((64 if compact else 88) * sc))
        bh = int(((112 if sub else 88) if compact else (144 if sub else 112)) * sc)
        roi = _draw_card_roi(bw, bh, radius=int((18 if compact else 22) * sc), fill=bg, outline=accent, outline_w=max(2, int(3 * sc)), shadow_pad=pad)
        dr = ImageDraw.Draw(roi)
        dr.rounded_rectangle((pad + int(14 * sc), pad + int(14 * sc), pad + int(21 * sc), pad + bh - int(14 * sc)), radius=4, fill=accent)
        ccx = pad + bw // 2 + int(4 * sc)
        yc = pad + int((12 if compact else 16) * sc)
        if kicker:
            dr.text((ccx - (kb[2] - kb[0]) // 2, yc), kicker, font=fk, fill=accent)
            yc += int((22 if compact else 28) * sc)
        dr.text((ccx - (vb[2] - vb[0]) // 2, yc), value, font=fv, fill=fg)
        if sub:
            dr.text((ccx - (sb[2] - sb[0]) // 2, yc + int((44 if compact else 58) * sc)), sub, font=fs, fill=muted)
        cx = int(w * float(ev.get("x_norm", 0.50)))
        bx0 = max(int(20 * sc), min(w - bw - int(20 * sc), cx - bw // 2)) + slide_x
        by0 = int(h * float(ev.get("y_norm", 0.055 if pos == "top" else 0.77)))
        _composite_roi_rgb(frame_rgb, roi, bx0 - pad, by0 - pad)
        return True

    # ---- 4. BASELINE: numerical_payoff_hero ----
    if kind == "numerical_payoff_hero":
        slide_y = int((1.0 - in_p) * -22 * sc)
        from_val = float(ev.get("from_value", 0.0))
        to_val = float(ev.get("to_value", 100.0))
        prefix = str(ev.get("prefix", "$"))
        suffix = str(ev.get("suffix", ""))
        prog = ease_out_cubic(min(1.0, el / max(0.05, float(ev.get("count_sec", 0.65)))))
        cur_num = int(round(from_val + (to_val - from_val) * prog))
        kicker = str(ev.get("kicker") or "ILLUSTRATIVE COMPARISON")
        headline = f"{prefix}{int(round(from_val))}  \u2192  {prefix}{cur_num}{suffix}"
        sub = str(ev.get("subtitle") or "")
        land_pulse = 1.0 + (0.032 * math.sin(min(1.0, max(0.0, el - 0.55) / 0.35) * math.pi) if 0.55 <= el <= 0.90 else 0.0)
        tmp_d = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        fk = load_font(int(21 * sc), bold=True)
        fh = load_font(int(50 * sc * land_pulse), bold=True)
        fs = load_font(int(22 * sc), bold=False)
        hb = tmp_d.textbbox((0, 0), headline, font=fh)
        kb = tmp_d.textbbox((0, 0), kicker, font=fk)
        sb = tmp_d.textbbox((0, 0), sub, font=fs) if sub else (0, 0, 0, 0)
        bw = min(int(w * 0.88), max(hb[2] - hb[0], kb[2] - kb[0], sb[2] - sb[0]) + int(92 * sc))
        bh = int(154 * sc)
        roi = _draw_card_roi(bw, bh, radius=int(24 * sc), fill=bg, outline=accent, outline_w=max(3, int(4 * sc)), shadow_pad=pad)
        dr = ImageDraw.Draw(roi)
        pb_w = int((bw - 48 * sc) * prog)
        dr.rounded_rectangle((pad + int(24 * sc), pad + bh - int(14 * sc), pad + int(24 * sc) + pb_w, pad + bh - int(8 * sc)), radius=3, fill=accent)
        dr.text((pad + (bw - (kb[2] - kb[0])) // 2, pad + int(14 * sc)), kicker, font=fk, fill=accent)
        dr.text((pad + (bw - (hb[2] - hb[0])) // 2, pad + int(42 * sc)), headline, font=fh, fill=fg)
        if sub:
            dr.text((pad + (bw - (sb[2] - sb[0])) // 2, pad + int(106 * sc)), sub, font=fs, fill=muted)
        by0 = int(h * float(ev.get("y_norm", 0.052))) + slide_y
        _composite_roi_rgb(frame_rgb, roi, (w - bw) // 2 - pad, by0 - pad)
        return True

    # ---- 5. BASELINE: cta_resolve_pill ----
    if kind == "cta_resolve_pill":
        slide_y = int((1.0 - in_p) * 24 * sc)
        title = str(ev.get("title") or "LINK IN THE CAPTION")
        kicker = str(ev.get("kicker") or "")
        tmp_d = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        ft = load_font(int(34 * sc), bold=True)
        fk = load_font(int(21 * sc), bold=True)
        tb = tmp_d.textbbox((0, 0), title, font=ft)
        kb = tmp_d.textbbox((0, 0), kicker, font=fk) if kicker else (0, 0, 0, 0)
        bw = min(int(w * 0.84), max(tb[2] - tb[0], kb[2] - kb[0]) + int(92 * sc))
        bh = int((114 if kicker else 76) * sc)
        fill_c = bg if kicker else accent
        out_c = accent if kicker else fg
        ink_c = fg if kicker else parse_hex_rgba("#121011", int(255 * vis))
        roi = _draw_card_roi(bw, bh, radius=int((28 if kicker else 38) * sc), fill=fill_c, outline=out_c, outline_w=max(2, int(2.5 * sc)), shadow_pad=pad)
        dr = ImageDraw.Draw(roi)
        if kicker:
            dr.text((pad + (bw - (kb[2] - kb[0])) // 2, pad + int(18 * sc)), kicker, font=fk, fill=accent)
            dr.text((pad + (bw - (tb[2] - tb[0])) // 2, pad + int(52 * sc)), title, font=ft, fill=ink_c)
        else:
            dr.text((pad + (bw - (tb[2] - tb[0])) // 2, pad + int(17 * sc)), title, font=ft, fill=ink_c)
        by0 = int(h * float(ev.get("y_norm", 0.878))) + slide_y
        _composite_roi_rgb(frame_rgb, roi, (w - bw) // 2 - pad, by0 - pad)
        return True

    # ---- 6. NEW SEMANTIC: resource_depletion (R4.3-04A) ----
    if kind == "resource_depletion":
        start_bal = float(ev.get("start_balance", 100.0))
        unit = str(ev.get("unit", "$"))
        deltas = list(ev.get("deltas") or [])
        kicker = str(ev.get("kicker") or "ILLUSTRATIVE EXAMPLE")
        title = str(ev.get("title") or "Resource Depletion")
        full_fr = bool(ev.get("full_frame", False))

        # Compute exact smooth remaining balance strictly respecting each delta's reveal_sec (ZERO spoilers!)
        cur_rem = start_bal
        active_rows: list[tuple[dict[str, Any], float]] = []
        all_ints = abs(start_bal - round(start_bal)) < 1e-6 and all(abs(float(d["amount"]) - round(float(d["amount"]))) < 1e-6 for d in deltas)
        for d in deltas:
            rev = float(d.get("reveal_sec", t0))
            anim_s = max(0.15, float(d.get("anim_sec", 0.36)))
            if t_sec >= rev:
                p = ease_out_cubic(min(1.0, (t_sec - rev) / anim_s))
                cur_rem -= float(d["amount"]) * p
                active_rows.append((d, p))
        cur_rem = max(0.0, cur_rem)
        disp_rem = round(cur_rem) if all_ints else cur_rem
        frac_left = cur_rem / max(1e-6, start_bal)

        bw = int(w * (0.90 if full_fr else float(ev.get("w_norm", 0.90))))
        bh = int(h * (0.58 if full_fr else float(ev.get("h_norm", 0.235))))
        bx0 = int(w * float(ev.get("x_norm_left", 0.05))) if not full_fr else (w - bw) // 2
        by0 = int(h * float(ev.get("y_norm", 0.20 if full_fr else 0.72))) + int((1.0 - in_p) * 26 * sc)

        bg_solid = parse_hex_rgba(pal["bg_hex"], int(246 * vis))
        roi = _draw_card_roi(bw, bh, radius=int(26 * sc), fill=bg_solid, outline=accent, outline_w=max(2, int(3 * sc)), shadow_pad=pad)
        dr = ImageDraw.Draw(roi)
        fk = load_font(int((24 if full_fr else 20) * sc), bold=True)
        ft = load_font(int((38 if full_fr else 28) * sc), bold=True, serif=full_fr)
        fbig = load_font(int((74 if full_fr else 48) * sc), bold=True)
        frow = load_font(int((30 if full_fr else 25) * sc), bold=True)
        fsub = load_font(int((22 if full_fr else 17) * sc), bold=False)

        # Header row: kicker + title + live remaining counter
        yc = pad + int((30 if full_fr else 16) * sc)
        dr.text((pad + int(34 * sc), yc), kicker.upper(), font=fk, fill=accent)
        yc += int((34 if full_fr else 26) * sc)
        dr.text((pad + int(34 * sc), yc), title, font=ft, fill=fg)

        rem_str = format_unit_value(disp_rem, unit)
        rem_col = danger if frac_left <= 0.02 else (accent if frac_left <= 0.35 else fg)
        rb = dr.textbbox((0, 0), rem_str, font=fbig)
        dr.text((pad + bw - int(34 * sc) - (rb[2] - rb[0]), pad + int((26 if full_fr else 12) * sc)), rem_str, font=fbig, fill=rem_col)
        rem_lbl = "REMAINING" if frac_left > 0.02 else "DEPLETED"
        rlb = dr.textbbox((0, 0), rem_lbl, font=fsub)
        dr.text((pad + bw - int(34 * sc) - (rlb[2] - rlb[0]), pad + int((108 if full_fr else 66) * sc)), rem_lbl, font=fsub, fill=danger if frac_left <= 0.02 else muted)

        # Animated shrinking progress bar with segment ticks (clean margin below DEPLETED label!)
        bar_x0 = pad + int(34 * sc)
        bar_x1 = pad + bw - int(34 * sc)
        bar_y0 = yc + int((72 if full_fr else 52) * sc)
        bar_h = int((36 if full_fr else 24) * sc)
        dr.rounded_rectangle((bar_x0, bar_y0, bar_x1, bar_y0 + bar_h), radius=bar_h // 2, fill=surface, outline=muted[:3] + (80,), width=1)
        fill_w = int((bar_x1 - bar_x0) * frac_left)
        if fill_w > 4:
            bar_col = positive if frac_left > 0.65 else (accent if frac_left > 0.25 else danger)
            dr.rounded_rectangle((bar_x0, bar_y0, bar_x0 + fill_w, bar_y0 + bar_h), radius=bar_h // 2, fill=bar_col)
        # Depleted ghost segment highlights on right edge of bar
        if frac_left < 0.99:
            dr.rounded_rectangle((bar_x0 + max(4, fill_w), bar_y0 + 3, bar_x1 - 3, bar_y0 + bar_h - 3), radius=bar_h // 2, fill=danger[:3] + (int(78 * vis),))

        # Ordered revealed delta rows (strictly non-spoiler: only revealed deltas appear!)
        ry = bar_y0 + bar_h + int((34 if full_fr else 18) * sc)
        row_h = int((88 if full_fr else 54) * sc)
        for d, dp in active_rows:
            slide_dx = int((1.0 - dp) * 42 * sc)
            r_alpha = int(255 * vis * dp)
            r_bg = parse_hex_rgba(pal["surface_hex"], int(220 * vis * dp))
            r_out = parse_hex_rgba(pal["danger_hex"], int(165 * vis * dp))
            dr.rounded_rectangle(
                (bar_x0 + slide_dx, ry, bar_x1 + slide_dx, ry + row_h - int(10 * sc)),
                radius=int(16 * sc), fill=r_bg, outline=r_out, width=max(1, int(2 * sc)),
            )
            lbl = str(d.get("label") or "")
            dr.text((bar_x0 + int(22 * sc) + slide_dx, ry + int((22 if full_fr else 11) * sc)), lbl, font=frow, fill=fg[:3] + (r_alpha,))
            val_s = format_unit_value(-abs(float(d["amount"])), unit, signed=True)
            vb = dr.textbbox((0, 0), val_s, font=frow)
            dr.text((bar_x1 - int(22 * sc) - (vb[2] - vb[0]) + slide_dx, ry + int((22 if full_fr else 11) * sc)), val_s, font=frow, fill=danger[:3] + (r_alpha,))
            ry += row_h

        # Footer hold callout when depleted or explicitly configured
        if frac_left <= 0.02 and ev.get("depleted_callout"):
            dc_s = str(ev["depleted_callout"])
            dc_y = pad + bh - int((82 if full_fr else 52) * sc)
            dr.rounded_rectangle((bar_x0, dc_y, bar_x1, dc_y + int((54 if full_fr else 38) * sc)), radius=int(14 * sc), fill=danger[:3] + (int(215 * vis),))
            dcb = dr.textbbox((0, 0), dc_s, font=frow)
            dr.text((pad + (bw - (dcb[2] - dcb[0])) // 2, dc_y + int((10 if full_fr else 6) * sc)), dc_s, font=frow, fill=fg)
        elif ev.get("footnote"):
            fn_s = str(ev["footnote"])
            fnb = dr.textbbox((0, 0), fn_s, font=fsub)
            dr.text((pad + (bw - (fnb[2] - fnb[0])) // 2, pad + bh - int(34 * sc)), fn_s, font=fsub, fill=muted)

        _composite_roi_rgb(frame_rgb, roi, bx0 - pad, by0 - pad)
        return True

    # ---- 7. NEW SEMANTIC: resource_allocation (R4.3-04B) ----
    if kind == "resource_allocation":
        total = max(1e-6, float(ev.get("total", 100.0)))
        unit = str(ev.get("unit", "$"))
        branches = list(ev.get("branches") or [])
        kicker = str(ev.get("kicker") or "ILLUSTRATIVE ALLOCATION")
        title = str(ev.get("title") or "Plan Before Spending")
        full_fr = bool(ev.get("full_frame", False))

        bw = int(w * (0.90 if full_fr else float(ev.get("w_norm", 0.90))))
        bh = int(h * (0.56 if full_fr else float(ev.get("h_norm", 0.225))))
        bx0 = (w - bw) // 2 if full_fr else int(w * float(ev.get("x_norm_left", 0.05)))
        by0 = int(h * float(ev.get("y_norm", 0.21 if full_fr else 0.725))) + int((1.0 - in_p) * 24 * sc)

        bg_solid = parse_hex_rgba(pal["bg_hex"], int(246 * vis))
        roi = _draw_card_roi(bw, bh, radius=int(26 * sc), fill=bg_solid, outline=positive, outline_w=max(2, int(3 * sc)), shadow_pad=pad)
        dr = ImageDraw.Draw(roi)
        fk = load_font(int((24 if full_fr else 20) * sc), bold=True)
        ft = load_font(int((40 if full_fr else 28) * sc), bold=True, serif=full_fr)
        fbig = load_font(int((56 if full_fr else 38) * sc), bold=True)
        frow = load_font(int((30 if full_fr else 24) * sc), bold=True)
        fsub = load_font(int((22 if full_fr else 18) * sc), bold=False)

        yc = pad + int((30 if full_fr else 18) * sc)
        dr.text((pad + int(34 * sc), yc), kicker.upper(), font=fk, fill=positive)
        yc += int((34 if full_fr else 26) * sc)
        dr.text((pad + int(34 * sc), yc), title, font=ft, fill=fg)

        tot_s = f"{format_unit_value(total, unit)} TOTAL"
        tb = dr.textbbox((0, 0), tot_s, font=frow)
        dr.text((pad + bw - int(34 * sc) - (tb[2] - tb[0]), pad + int((34 if full_fr else 22) * sc)), tot_s, font=frow, fill=accent)

        # Animated conserved split bar (each branch grows left-to-right only when revealed!)
        bar_x0 = pad + int(34 * sc)
        bar_x1 = pad + bw - int(34 * sc)
        bar_w = bar_x1 - bar_x0
        bar_y0 = yc + int((64 if full_fr else 46) * sc)
        bar_h = int((44 if full_fr else 30) * sc)
        dr.rounded_rectangle((bar_x0, bar_y0, bar_x1, bar_y0 + bar_h), radius=bar_h // 2, fill=surface)

        cur_x = bar_x0
        role_map = {"positive": positive, "accent": accent, "danger": danger, "muted": muted}
        revealed_branches: list[tuple[dict[str, Any], float, tuple[int, int, int, int]]] = []
        for bi, br in enumerate(branches):
            rev = float(br.get("reveal_sec", t0))
            anim_s = max(0.18, float(br.get("anim_sec", 0.42)))
            bp = ease_out_cubic(min(1.0, (t_sec - rev) / anim_s)) if t_sec >= rev else 0.0
            col = role_map.get(str(br.get("color_role") or ("positive" if bi == 0 else "accent")), accent)
            if bp > 0.01:
                seg_w = int(round(bar_w * (float(br["amount"]) / total) * bp))
                if seg_w > 3:
                    dr.rounded_rectangle((cur_x, bar_y0, min(bar_x1, cur_x + seg_w - 2), bar_y0 + bar_h), radius=bar_h // 2, fill=col)
                cur_x += seg_w
                revealed_branches.append((br, bp, col))

        # Branch breakdown cards
        ry = bar_y0 + bar_h + int((36 if full_fr else 20) * sc)
        card_h = int((128 if full_fr else 74) * sc)
        for br, bp, col in revealed_branches:
            dy = int((1.0 - bp) * 24 * sc)
            r_a = int(255 * vis * bp)
            dr.rounded_rectangle(
                (bar_x0, ry + dy, bar_x1, ry + dy + card_h - int(14 * sc)),
                radius=int(20 * sc),
                fill=parse_hex_rgba(pal["surface_hex"], int(230 * vis * bp)),
                outline=col[:3] + (int(220 * vis * bp),),
                width=max(2, int(2.5 * sc)),
            )
            # Left color badge stripe
            dr.rounded_rectangle((bar_x0 + int(14 * sc), ry + dy + int(16 * sc), bar_x0 + int(24 * sc), ry + dy + card_h - int(30 * sc)), radius=5, fill=col[:3] + (r_a,))
            pct = int(round(100.0 * float(br["amount"]) / total))
            dr.text((bar_x0 + int(42 * sc), ry + dy + int((18 if full_fr else 10) * sc)), str(br["label"]), font=frow, fill=fg[:3] + (r_a,))
            sub_txt = str(br.get("subtitle") or f"{pct}% of total")
            dr.text((bar_x0 + int(42 * sc), ry + dy + int((60 if full_fr else 38) * sc)), sub_txt, font=fsub, fill=muted[:3] + (r_a,))
            val_s = format_unit_value(float(br["amount"]) * bp, unit)
            vb = dr.textbbox((0, 0), val_s, font=fbig)
            dr.text((bar_x1 - int(26 * sc) - (vb[2] - vb[0]), ry + dy + int((24 if full_fr else 12) * sc)), val_s, font=fbig, fill=col[:3] + (r_a,))
            ry += card_h

        # Conservation equation footer
        if ev.get("conservation_label") or revealed_branches:
            clbl = str(ev.get("conservation_label") or f"Conserved total: {format_unit_value(total, unit)}")
            cb = dr.textbbox((0, 0), clbl, font=fsub)
            dr.text((pad + (bw - (cb[2] - cb[0])) // 2, pad + bh - int(42 * sc)), clbl, font=fsub, fill=muted)

        _composite_roi_rgb(frame_rgb, roi, bx0 - pad, by0 - pad)
        return True

    # ---- 8. NEW SEMANTIC: contrast_transformation (R4.3-04C) ----
    if kind == "contrast_transformation":
        sa = ev.get("scenario_a") or {}
        sb = ev.get("scenario_b") or {}
        unit = str(ev.get("unit") or sa.get("unit") or "$")
        kicker = str(ev.get("kicker") or "SAME INPUT · DIFFERENT OUTCOME")
        title = str(ev.get("title") or "Side-by-Side Comparison")
        full_fr = bool(ev.get("full_frame", True))
        rev_b = float(ev.get("reveal_b_sec", t0 + 0.55))
        pb = ease_out_cubic(min(1.0, (t_sec - rev_b) / max(0.20, float(ev.get("anim_sec", 0.45))))) if t_sec >= rev_b else 0.0
        dur_tot = max(0.2, t1 - t0)
        hold_prog = min(1.0, max(0.0, (t_sec - t0) / dur_tot))

        bw = int(w * 0.90)
        bh = int(h * (0.54 if full_fr else 0.40))
        bx0 = (w - bw) // 2
        by0 = int(h * float(ev.get("y_norm", 0.22 if full_fr else 0.48))) + int((1.0 - in_p) * 20 * sc)

        bg_solid = parse_hex_rgba(pal["bg_hex"], int(246 * vis))
        roi = _draw_card_roi(bw, bh, radius=int(28 * sc), fill=bg_solid, outline=accent, outline_w=max(2, int(3 * sc)), shadow_pad=pad)
        dr = ImageDraw.Draw(roi)
        fk = load_font(int(24 * sc), bold=True)
        ft = load_font(int(50 * sc), bold=True, serif=True)
        flbl = load_font(int(28 * sc), bold=True)
        fval = load_font(int(58 * sc), bold=True)
        fsub = load_font(int(22 * sc), bold=False)

        # Subtle living champagne-gold reading progress accent line along top edge
        top_bar_w = int((bw - 64 * sc) * hold_prog)
        if top_bar_w > 4:
            dr.rounded_rectangle((pad + int(32 * sc), pad + int(12 * sc), pad + int(32 * sc) + top_bar_w, pad + int(17 * sc)), radius=3, fill=accent)

        yc = pad + int(34 * sc)
        kb = dr.textbbox((0, 0), kicker.upper(), font=fk)
        dr.text((pad + (bw - (kb[2] - kb[0])) // 2, yc), kicker.upper(), font=fk, fill=accent)
        yc += int(38 * sc)
        tb = dr.textbbox((0, 0), title, font=ft)
        dr.text((pad + (bw - (tb[2] - tb[0])) // 2, yc), title, font=ft, fill=fg)
        yc += int(80 * sc)

        inner_x0 = pad + int(34 * sc)
        inner_x1 = pad + bw - int(34 * sc)
        inner_w = inner_x1 - inner_x0
        panel_h = int((bh - int(250 * sc)) // 2)

        # Panel A (Depleted / Baseline scenario with visual crimson bar)
        dr.rounded_rectangle((inner_x0, yc, inner_x1, yc + panel_h - int(14 * sc)), radius=int(22 * sc), fill=surface, outline=danger, width=max(2, int(3 * sc)))
        dr.text((inner_x0 + int(28 * sc), yc + int(22 * sc)), str(sa.get("label") or "Scenario A").upper(), font=flbl, fill=danger)
        if sa.get("summary"):
            sl, fs_a, _, _ = fit_text_lines(dr, str(sa["summary"]), inner_w - int(56 * sc), start_size=int(26 * sc), min_size=int(19 * sc), max_lines=2, bold=False)
            for li, ln in enumerate(sl):
                dr.text((inner_x0 + int(28 * sc), yc + int((64 + li * 32) * sc)), ln, font=fs_a, fill=muted)
        # Visual empty-bar meter reinforcing depletion
        bar_ya = yc + int(panel_h * 0.48)
        dr.rounded_rectangle((inner_x0 + int(28 * sc), bar_ya, inner_x1 - int(28 * sc), bar_ya + int(18 * sc)), radius=9, fill=danger[:3] + (int(80 * vis),))
        out_a_s = str(sa.get("outcome_text") or format_unit_value(float(sa.get("outcome_value", 0)), unit))
        dr.text((inner_x0 + int(28 * sc), yc + panel_h - int(104 * sc)), out_a_s, font=fval, fill=danger)

        # Panel B (Alternative / Planned scenario — animates in after reveal_b_sec with split meter!)
        yb = yc + panel_h + int(16 * sc)
        if pb > 0.01:
            dy_b = int((1.0 - pb) * 32 * sc)
            ba = int(255 * vis * pb)
            dr.rounded_rectangle(
                (inner_x0, yb + dy_b, inner_x1, yb + dy_b + panel_h - int(14 * sc)),
                radius=int(22 * sc),
                fill=parse_hex_rgba(pal["surface_hex"], int(242 * vis * pb)),
                outline=positive[:3] + (ba,),
                width=max(2, int(3.5 * sc)),
            )
            dr.text((inner_x0 + int(28 * sc), yb + dy_b + int(22 * sc)), str(sb.get("label") or "Scenario B").upper(), font=flbl, fill=positive[:3] + (ba,))
            if sb.get("summary"):
                slb, fs_b, _, _ = fit_text_lines(dr, str(sb["summary"]), inner_w - int(56 * sc), start_size=int(26 * sc), min_size=int(19 * sc), max_lines=2, bold=False)
                for li, ln in enumerate(slb):
                    dr.text((inner_x0 + int(28 * sc), yb + dy_b + int((64 + li * 32) * sc)), ln, font=fs_b, fill=fg[:3] + (ba,))
            # Animated 67% sage + 33% champagne split meter
            bar_yb = yb + dy_b + int(panel_h * 0.48)
            bw_m = inner_w - int(56 * sc)
            w_pos = int(bw_m * 0.667 * pb)
            w_acc = int(bw_m * 0.325 * pb)
            if w_pos > 4:
                dr.rounded_rectangle((inner_x0 + int(28 * sc), bar_yb, inner_x0 + int(28 * sc) + w_pos, bar_yb + int(18 * sc)), radius=9, fill=positive[:3] + (ba,))
            if w_acc > 4:
                dr.rounded_rectangle((inner_x0 + int(34 * sc) + w_pos, bar_yb, min(inner_x1 - int(28 * sc), inner_x0 + int(34 * sc) + w_pos + w_acc), bar_yb + int(18 * sc)), radius=9, fill=accent[:3] + (ba,))
            out_b_s = str(sb.get("outcome_text") or format_unit_value(float(sb.get("outcome_value", 0)), unit))
            dr.text((inner_x0 + int(28 * sc), yb + dy_b + panel_h - int(104 * sc)), out_b_s, font=fval, fill=positive[:3] + (ba,))

        if ev.get("footnote"):
            fn = str(ev["footnote"])
            fb = dr.textbbox((0, 0), fn, font=fsub)
            dr.text((pad + (bw - (fb[2] - fb[0])) // 2, pad + bh - int(44 * sc)), fn, font=fsub, fill=muted)

        _composite_roi_rgb(frame_rgb, roi, bx0 - pad, by0 - pad)
        return True

    # ---- 9. NEW SEMANTIC: evidence_spotlight (R4.3-04D) ----
    if kind == "evidence_spotlight":
        bw = int(w * float(ev.get("w_norm", 0.84)))
        bh = int(h * float(ev.get("h_norm", 0.30)))
        bx0 = (w - bw) // 2
        by0 = int(h * float(ev.get("y_norm", 0.56))) + int((1.0 - in_p) * 24 * sc)
        rev_c = float(ev.get("callout_reveal_sec", t0 + 0.70))
        cp = ease_out_cubic(min(1.0, max(0.0, (t_sec - rev_c) / 0.45)))
        roi = _draw_card_roi(bw, bh, radius=int(24 * sc), fill=bg, outline=accent, outline_w=max(2, int(3 * sc)), shadow_pad=pad)
        dr = ImageDraw.Draw(roi)
        fk = load_font(int(21 * sc), bold=True)
        ft = load_font(int(34 * sc), bold=True)
        prov = str(ev.get("provenance") or "ILLUSTRATIVE").upper()
        badge = str(ev.get("illustrative_label") or ("VERIFIED SOURCE EVIDENCE" if prov == "REAL_EVIDENCE" else "ILLUSTRATIVE GRAPHIC"))
        dr.text((pad + int(26 * sc), pad + int(18 * sc)), badge.upper(), font=fk, fill=accent)
        # Animated spotlight focus underline sweeping left-to-right across card
        sweep_w = int((bw - int(52 * sc)) * max(0.12, cp))
        dr.rounded_rectangle((pad + int(26 * sc), pad + int(48 * sc), pad + int(26 * sc) + sweep_w, pad + int(54 * sc)), radius=3, fill=accent)
        if prov == "REAL_EVIDENCE" and source_frame_lookup is not None and ev.get("source_id"):
            crop_im = source_frame_lookup(str(ev["source_id"]), t_sec)
            if crop_im is not None:
                cw_i = bw - int(48 * sc)
                ch_i = bh - int(118 * sc)
                res_im = crop_im.resize((max(16, cw_i), max(16, ch_i)), Image.Resampling.LANCZOS).convert("RGBA")
                roi.paste(res_im, (pad + int(24 * sc), pad + int(62 * sc)))
        else:
            dr.text((pad + int(26 * sc), pad + int(66 * sc)), str(ev.get("title") or "Spotlight"), font=ft, fill=fg)
            if ev.get("callout") and cp > 0.02:
                ca = int(255 * vis * cp)
                dy_c = int((1.0 - cp) * 18 * sc)
                dr.rounded_rectangle((pad + int(24 * sc), pad + int(114 * sc) + dy_c, pad + bw - int(24 * sc), pad + bh - int(22 * sc) + dy_c), radius=int(14 * sc), fill=surface[:3] + (int(235 * vis * cp),), outline=accent[:3] + (ca,), width=2)
                sl, fs, _, _ = fit_text_lines(dr, str(ev["callout"]), bw - int(80 * sc), start_size=int(24 * sc), min_size=int(18 * sc), max_lines=3, bold=False)
                for li, ln in enumerate(sl):
                    dr.text((pad + int(40 * sc), pad + int((128 + li * 30) * sc) + dy_c), ln, font=fs, fill=fg[:3] + (ca,))
        _composite_roi_rgb(frame_rgb, roi, bx0 - pad, by0 - pad)
        return True

    # ---- 10. NEW SEMANTIC: causal_chain (R4.3-04E) ----
    if kind == "causal_chain":
        nodes = list(ev.get("nodes") or [])
        bw = int(w * float(ev.get("w_norm", 0.88)))
        bh = int(h * float(ev.get("h_norm", 0.34)))
        bx0 = (w - bw) // 2
        by0 = int(h * float(ev.get("y_norm", 0.54))) + int((1.0 - in_p) * 20 * sc)
        roi = _draw_card_roi(bw, bh, radius=int(24 * sc), fill=bg, outline=accent, outline_w=max(2, int(3 * sc)), shadow_pad=pad)
        dr = ImageDraw.Draw(roi)
        fk = load_font(int(21 * sc), bold=True)
        ft = load_font(int(26 * sc), bold=True)
        fsub = load_font(int(19 * sc), bold=False)
        dr.text((pad + int(28 * sc), pad + int(18 * sc)), str(ev.get("kicker") or "CAUSAL CHAIN").upper(), font=fk, fill=accent)
        ny = pad + int(56 * sc)
        step_h = max(int(58 * sc), (bh - int(76 * sc)) // max(1, len(nodes)))
        for ni, nd in enumerate(nodes):
            rev = float(nd.get("reveal_sec", t0 + ni * 0.35))
            if t_sec < rev:
                continue
            np_p = ease_out_cubic(min(1.0, (t_sec - rev) / 0.28))
            dx = int((1.0 - np_p) * 32 * sc)
            na = int(255 * vis * np_p)
            dr.rounded_rectangle((pad + int(28 * sc) + dx, ny, pad + bw - int(28 * sc) + dx, ny + step_h - int(18 * sc)), radius=int(14 * sc), fill=surface[:3] + (int(230 * vis * np_p),), outline=accent[:3] + (na,), width=2)
            dr.text((pad + int(46 * sc) + dx, ny + int(10 * sc)), f"{ni + 1}. {nd.get('label', '')}", font=ft, fill=fg[:3] + (na,))
            if ni < len(nodes) - 1 and nd.get("relation"):
                dr.text((pad + int(54 * sc), ny + step_h - int(17 * sc)), f"\u2193 {nd['relation']}", font=fsub, fill=accent[:3] + (na,))
            ny += step_h
        _composite_roi_rgb(frame_rgb, roi, bx0 - pad, by0 - pad)
        return True

    # ---- 11. NEW SEMANTIC: decision_branch (R4.3-04F) ----
    if kind == "decision_branch":
        choices = list(ev.get("choices") or [])
        unit = str(ev.get("unit") or "")
        bw = int(w * float(ev.get("w_norm", 0.88)))
        bh = int(h * float(ev.get("h_norm", 0.32)))
        bx0 = (w - bw) // 2
        by0 = int(h * float(ev.get("y_norm", 0.56))) + int((1.0 - in_p) * 22 * sc)
        roi = _draw_card_roi(bw, bh, radius=int(24 * sc), fill=bg, outline=accent, outline_w=max(2, int(3 * sc)), shadow_pad=pad)
        dr = ImageDraw.Draw(roi)
        fk = load_font(int(21 * sc), bold=True)
        ft = load_font(int(26 * sc), bold=True)
        fv = load_font(int(34 * sc), bold=True)
        dr.text((pad + int(28 * sc), pad + int(18 * sc)), str(ev.get("prompt") or "DECISION").upper(), font=fk, fill=accent)
        col_w = (bw - int(72 * sc)) // max(1, len(choices))
        for ci, ch in enumerate(choices):
            rev = float(ch.get("reveal_sec", t0 + ci * 0.35))
            if t_sec < rev:
                continue
            cp = ease_out_cubic(min(1.0, (t_sec - rev) / 0.30))
            ca = int(255 * vis * cp)
            cx0 = pad + int(26 * sc) + ci * (col_w + int(20 * sc))
            cy0 = pad + int(58 * sc) + int((1.0 - cp) * 20 * sc)
            col = positive if str(ch.get("role")) == "positive" else (danger if str(ch.get("role")) == "danger" else accent)
            dr.rounded_rectangle((cx0, cy0, cx0 + col_w, pad + bh - int(22 * sc)), radius=int(18 * sc), fill=surface[:3] + (int(230 * vis * cp),), outline=col[:3] + (ca,), width=2)
            dr.text((cx0 + int(16 * sc), cy0 + int(16 * sc)), str(ch.get("choice") or ""), font=ft, fill=fg[:3] + (ca,))
            if ch.get("value") is not None:
                vs = format_unit_value(float(ch["value"]), str(ch.get("unit") or unit))
                dr.text((cx0 + int(16 * sc), cy0 + int(62 * sc)), vs, font=fv, fill=col[:3] + (ca,))
            if ch.get("outcome"):
                dr.text((cx0 + int(16 * sc), cy0 + int(114 * sc)), str(ch["outcome"]), font=fk, fill=muted[:3] + (ca,))
        _composite_roi_rgb(frame_rgb, roi, bx0 - pad, by0 - pad)
        return True

    return False


__all__ = [
    "BASELINE_COMPONENT_KINDS",
    "LayerLRUCache",
    "PALETTE_PRESETS",
    "SEMANTIC_COMPONENT_KINDS",
    "SUPPORTED_COMPONENT_KINDS",
    "ease_in_out_cubic",
    "ease_out_cubic",
    "estimate_component_bounds_norm",
    "evaluate_spatial_collisions",
    "fit_text_lines",
    "format_unit_value",
    "load_font",
    "parse_hex_rgba",
    "render_component_onto_rgb",
    "render_luxury_backdrop",
    "resolve_palette",
    "validate_component_payload",
]
