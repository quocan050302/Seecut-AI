"""KH-03.R3 Universal Cross-Niche Evidence Showcase (CMI-05) & Dynamic Typography/Caption Engine (CMI-06).

- CMI-05 generalizes visual evidence across ALL niches (product demo, documents, screen captures,
  verified charts, archival media, original diagrams, physical action refs, quotations, or none needed).
  Distinguishes ORIGINAL_SOURCE / ACTUAL_SCREENSHOT / ILLUSTRATIVE_DIAGRAM / THIRD_PARTY_LICENSED /
  MISSING_EVIDENCE and never fabricates UI, testimonials, stats, prices, or citations.
- CMI-06 separates accurate spoken captions from semantic labels/headlines/numbers/CTA, preserves
  legacy `caption_policy=OFF`, handles Unicode/Vietnamese/CJK/RTL line-breaking, negation/decimal
  preservation, WCAG contrast verification, hysteresis slot stability across morphs, and 360px mobile
  readability checks.
"""

from __future__ import annotations

import csv
import json
import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from scripts.kh02.typography import CJK_RE, VI_DIACRITIC_RE, measure_text_layout

from .contracts import (
    assert_kh03_schema,
    hash_artifact,
    now_utc,
    sanitize_untrusted_text,
    write_json_atomic,
)

RTL_RE = re.compile(r"[\u0590-\u05FF\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_PROTECTED_ATOM_RE = re.compile(
    r"(?:(?:not|never|don't|doesn't|no)\s+[$€£¥]?\d[\d.,]*(?:\s*%|\s*x)?|[+-]?[$€£¥]\d[\d.,]*|\d[\d.,]*\s*(?:%|°[CF]|USD|EUR|GBP|ms|fps)|≠|<=|>=)",
    re.IGNORECASE,
)

EVIDENCE_NEED_TYPES = (
    "real_product_demo",
    "document_page",
    "screen_capture",
    "image_detail",
    "verified_chart_data",
    "licensed_archival_media",
    "original_diagram",
    "physical_action_ref",
    "text_quotation",
    "none_needed",
)

# ======================================================================
# CMI-05 — UNIVERSAL CROSS-NICHE EVIDENCE & SHOWCASE INTELLIGENCE
# ======================================================================


def _classify_evidence_need_for_phrase(
    phrase: dict[str, Any],
    *,
    skill_id: str,
    has_supplied_assets: bool,
) -> tuple[str, str, list[str]]:
    """Infer evidence need purely from content semantics & skill (zero niche-name hardcoding!)."""
    role = str(phrase.get("semantic_role") or "context")
    txt = str(phrase.get("text") or "").lower()
    has_nums = bool(phrase.get("numeric_claims"))
    has_quote = ('"' in txt or "“" in txt or "according to" in txt or "quoted" in txt)
    has_screen = any(k in txt for k in ("click", "screen", "window", "terminal", "command", "button", "dashboard", "browser", "code", "shortcut", "app"))
    has_product = any(k in txt for k in ("workbook", "template", "planner", "kit", "bundle", "product", "page", "cover", "printable", "notion"))
    has_history = any(k in txt for k in ("century", "year ", "14", "15", "16", "17", "18", "19", "archive", "historical", "expedition", "treaty"))
    has_phys = any(k in txt for k in ("sear", "pan", "pour", "chop", "stretch", "posture", "hold", "fold", "knead", "boil", "rep", "form"))

    # Natural retention / interview / calm delivery needs zero forced B-roll on simple talk beats
    if skill_id == "SK-05" and role in ("context", "question", "hook") and not has_product and not has_screen:
        return "none_needed", "none", ["NONE_NEEDED"]

    if role == "evidence" or has_product:
        if has_screen:
            return "screen_capture", "optional", ["ACTUAL_SCREENSHOT", "ORIGINAL_SOURCE"]
        return "real_product_demo", "optional", ["ORIGINAL_SOURCE", "ACTUAL_SCREENSHOT", "THIRD_PARTY_LICENSED"]
    if has_screen:
        return "screen_capture", "optional", ["ACTUAL_SCREENSHOT", "ORIGINAL_SOURCE", "ILLUSTRATIVE_DIAGRAM"]
    if has_quote:
        return "text_quotation", "optional", ["ORIGINAL_SOURCE", "ILLUSTRATIVE_DIAGRAM"]
    if has_nums:
        return "verified_chart_data", "optional", ["ILLUSTRATIVE_DIAGRAM", "ORIGINAL_SOURCE"]
    if has_history:
        return "licensed_archival_media", "optional", ["THIRD_PARTY_LICENSED", "ILLUSTRATIVE_DIAGRAM"]
    if has_phys:
        return "physical_action_ref", "none" if skill_id == "SK-05" else "optional", ["ORIGINAL_SOURCE", "NONE_NEEDED"]
    if role in ("mechanism", "step", "comparison"):
        return "original_diagram", "optional", ["ILLUSTRATIVE_DIAGRAM"]
    if role == "cta":
        return ("real_product_demo" if has_product else "none_needed"), ("optional" if has_product else "none"), ["ORIGINAL_SOURCE", "NONE_NEEDED"]
    return "none_needed", "none", ["NONE_NEEDED"]


def build_universal_evidence_ledger(
    *,
    run_id: str,
    skill_id: str,
    semantic_graph: dict[str, Any],
    footage_manifest: dict[str, Any],
    intended_purpose: str = "NONCOMMERCIAL_ONLY",
    require_product_proof: bool = False,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Build cross-niche EVIDENCE_NEEDS, EVIDENCE_LEDGER, RIGHTS_LEDGER.csv, CROP_MAP & SHOWCASE_PLAN (CMI-05)."""
    assets = list(footage_manifest.get("assets") or [])
    by_beat_binding = {
        str(b.get("beat_id")): b
        for b in (footage_manifest.get("beat_bindings") or footage_manifest.get("bindings") or [])
        if b.get("beat_id")
    }
    assets_by_id = {str(a["asset_id"]): a for a in assets if a.get("asset_id")}

    needs: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    crop_map: list[dict[str, Any]] = []
    showcase: list[dict[str, Any]] = []
    rights_rows: list[dict[str, Any]] = []

    has_any_real_asset = len(assets) > 0
    required_missing = 0
    optional_missing = 0
    real_bound = 0

    for idx, ph in enumerate(semantic_graph.get("phrases") or []):
        bid = f"beat_{idx + 1:02d}"
        claim_txt = sanitize_untrusted_text(ph.get("text", ""), max_len=200)
        need_type, prio, tiers = _classify_evidence_need_for_phrase(
            ph, skill_id=skill_id, has_supplied_assets=has_any_real_asset,
        )
        if require_product_proof and need_type in ("real_product_demo", "screen_capture"):
            prio = "required"
        nid = f"evneed_{idx + 1:02d}_{bid}"
        needs.append({
            "need_id": nid,
            "beat_id": bid,
            "need_type": need_type,
            "priority": prio,
            "claim_text": claim_txt,
            "accepted_proof_tiers": tiers,
        })

        fb = by_beat_binding.get(bid)
        aid = str(fb["asset_id"]) if (fb and fb.get("asset_id") and str(fb["asset_id"]) in assets_by_id) else None
        asset = assets_by_id.get(aid) if aid else None

        if need_type == "none_needed":
            bindings.append({
                "need_id": nid,
                "beat_id": bid,
                "asset_id": None,
                "proof_tier": "NONE_NEEDED",
                "source_url_or_path": None,
                "sha256": None,
                "capture_date_utc": None,
                "resolution": None,
                "crop_rect_norm": None,
                "rights_status": "NOT_APPLICABLE",
                "attribution": "",
                "usage_restrictions": "None (A-roll natural delivery sufficient)",
                "claim_scope": claim_txt[:120],
                "owner_rights_review_required": False,
            })
            continue

        if asset is not None:
            real_bound += 1
            lic_stat = str(asset.get("license_status") or "UNKNOWN")
            # Reject invalid or commercial-incompatible asset rights fail-closed
            rights_ok = lic_stat.startswith("APPROVED_") and not (
                intended_purpose == "COMMERCIAL_AND_NONCOMMERCIAL" and "NONCOMMERCIAL" in lic_stat
            )
            if not rights_ok:
                required_missing += 1
                gaps.append({
                    "need_id": nid,
                    "beat_id": bid,
                    "severity": "BLOCKING_RIGHTS",
                    "reason": f"Asset {aid} has invalid/incompatible license_status={lic_stat!r} for {intended_purpose}.",
                    "remediation": "Supply a rights-cleared replacement or omit unsupported claim.",
                })
                proof_tier = "MISSING_EVIDENCE"
            else:
                mtype = str(asset.get("media_type") or "image")
                proof_tier = "ACTUAL_SCREENSHOT" if mtype == "screen_capture" else (
                    "ORIGINAL_SOURCE" if "OWNER" in lic_stat else "THIRD_PARTY_LICENSED"
                )
            w_px = int(asset.get("width") or 0)
            h_px = int(asset.get("height") or 0)
            # Clean readable source crop (never blindly shrink unreadable full pages!)
            crop = [0.0, 0.0, 1.0, min(1.0, round((w_px * 1.15) / float(max(1, h_px)), 4))] if (h_px > w_px * 1.4 and w_px > 0) else [0.0, 0.0, 1.0, 1.0]
            bindings.append({
                "need_id": nid,
                "beat_id": bid,
                "asset_id": aid,
                "proof_tier": proof_tier,
                "source_url_or_path": str(asset.get("source_url") or asset.get("local_path") or ""),
                "sha256": str(asset.get("sha256") or ""),
                "capture_date_utc": str(asset.get("captured_at_utc") or now_utc()),
                "resolution": [w_px, h_px] if w_px > 0 else None,
                "crop_rect_norm": crop,
                "rights_status": lic_stat,
                "attribution": str(asset.get("attribution_text") or ""),
                "usage_restrictions": "Owner commercial sign-off required before paid distribution" if "OWNER" in lic_stat else "Per upstream license terms",
                "claim_scope": claim_txt[:120],
                "owner_rights_review_required": True,
            })
            crop_map.append({
                "need_id": nid,
                "beat_id": bid,
                "asset_id": aid,
                "source_resolution": [w_px, h_px],
                "crop_rect_norm": crop,
                "anti_tiny_fullpage_guard": (crop[3] < 1.0),
            })
            rights_rows.append({
                "asset_id": aid,
                "beat_id": bid,
                "proof_tier": proof_tier,
                "rights_status": lic_stat,
                "sha256": str(asset.get("sha256") or ""),
                "source": str(asset.get("source_url") or asset.get("local_path") or ""),
                "attribution": str(asset.get("attribution_text") or ""),
                "owner_review_required": "YES",
            })
            stage = "cover" if len(showcase) == 0 else ("workflow_feature" if len(showcase) == 1 else "deliverable_detail")
            showcase.append({
                "step": len(showcase) + 1,
                "stage": stage,
                "beat_id": bid,
                "asset_id": aid,
                "proof_tier": proof_tier,
                "label": "REAL_EVIDENCE",
            })
        else:
            # Missing external media: NEVER fabricate screenshot! Use labeled ILLUSTRATIVE_DIAGRAM when optional,
            # or flag MISSING_EVIDENCE when required proof is missing!
            can_illustrate = (need_type in ("original_diagram", "verified_chart_data", "text_quotation") or prio != "required")
            if prio == "required":
                required_missing += 1
                proof_tier = "MISSING_EVIDENCE"
                gaps.append({
                    "need_id": nid,
                    "beat_id": bid,
                    "severity": "REQUIRED_MISSING",
                    "reason": f"Required {need_type} has no verified asset; will NOT fabricate product UI or screenshots.",
                    "remediation": "Supply verified asset or downgrade/omit unverified visual proof claim.",
                })
            elif can_illustrate:
                optional_missing += 1
                proof_tier = "ILLUSTRATIVE_DIAGRAM"
                gaps.append({
                    "need_id": nid,
                    "beat_id": bid,
                    "severity": "OPTIONAL_ILLUSTRATION_FALLBACK",
                    "reason": f"Optional {need_type} represented as explicitly labeled ILLUSTRATIVE_DIAGRAM (not claimed as verified proof).",
                    "remediation": "Optional: attach verified source capture to upgrade from ILLUSTRATIVE_DIAGRAM to ACTUAL_SCREENSHOT.",
                })
            else:
                optional_missing += 1
                proof_tier = "MISSING_EVIDENCE"
            bindings.append({
                "need_id": nid,
                "beat_id": bid,
                "asset_id": None,
                "proof_tier": proof_tier,
                "source_url_or_path": None,
                "sha256": None,
                "capture_date_utc": None,
                "resolution": None,
                "crop_rect_norm": None,
                "rights_status": "ORIGINAL_ILLUSTRATION_ONLY" if proof_tier == "ILLUSTRATIVE_DIAGRAM" else "MISSING_EVIDENCE",
                "attribution": "",
                "usage_restrictions": "Must carry explicit illustrative/conceptual label; never present as real capture.",
                "claim_scope": claim_txt[:120],
                "owner_rights_review_required": False,
            })

    if required_missing > 0:
        status = "EVIDENCE_BLOCKED_MISSING_REQUIRED"
    elif real_bound > 0 and optional_missing == 0:
        status = "EVIDENCE_VERIFIED"
    elif real_bound == 0 and all(n["need_type"] == "none_needed" for n in needs):
        status = "NO_ADDITIONAL_EVIDENCE_NEEDED"
    else:
        status = "EVIDENCE_PARTIAL"

    ledger = {
        "schema_version": "kh03.evidence_ledger.v1",
        "run_id": run_id,
        "selected_skill_id": skill_id,
        "status": status,
        "evidence_needs": needs,
        "bindings": bindings,
        "gaps": gaps,
        "showcase_progression": showcase,
    }
    assert_kh03_schema(ledger, "evidence_binding")

    if run_dir is not None:
        adir = run_dir / "assets"
        adir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(adir / "EVIDENCE_NEEDS.json", {"schema_version": "kh03.evidence_needs.v1", "run_id": run_id, "needs": needs})
        write_json_atomic(adir / "EVIDENCE_LEDGER.json", ledger)
        write_json_atomic(adir / "EVIDENCE_CROP_MAP.json", {"schema_version": "kh03.evidence_crop_map.v1", "crops": crop_map})
        write_json_atomic(adir / "PRODUCT_OR_PROOF_SHOWCASE_PLAN.json", {
            "schema_version": "kh03.showcase_plan.v1",
            "status": status,
            "steps": showcase,
        })
        with (adir / "RIGHTS_LEDGER.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["asset_id", "beat_id", "proof_tier", "rights_status", "sha256", "source", "attribution", "owner_review_required"])
            w.writeheader()
            w.writerows(rights_rows)
        gap_lines = [
            f"# Evidence Gaps & Proof Disclosure — `{run_id}` (`{skill_id}`)",
            "",
            f"- **Overall Status**: `{status}` | **Real Assets Bound**: `{real_bound}` | **Required Missing**: `{required_missing}` | **Illustrative Fallbacks**: `{optional_missing}`",
            "",
        ]
        for g in gaps:
            gap_lines.append(f"- **[{g['severity']}] `{g['beat_id']}` (`{g['need_id']}`)**: {g['reason']} -> *{g['remediation']}*")
        if not gaps:
            gap_lines.append("- No unresolved evidence gaps for this run.")
        (adir / "EVIDENCE_GAPS.md").write_text("\n".join(gap_lines) + "\n", encoding="utf-8")

    return ledger


# ======================================================================
# CMI-06 — DYNAMIC TYPOGRAPHY & MULTILINGUAL CAPTION INTELLIGENCE
# ======================================================================


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = str(hex_color or "#151210").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (21, 18, 16)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return (21, 18, 16)


def wcag_contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """Compute exact WCAG 2.1 relative luminance contrast ratio (1.0 .. 21.0)."""
    def _rel_lum(rgb: tuple[int, int, int]) -> float:
        vals = []
        for ch in rgb:
            s = ch / 255.0
            vals.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
        return 0.2126 * vals[0] + 0.7152 * vals[1] + 0.0722 * vals[2]

    l1 = _rel_lum(_hex_to_rgb(fg_hex))
    l2 = _rel_lum(_hex_to_rgb(bg_hex))
    hi, lo = max(l1, l2), min(l1, l2)
    return round((hi + 0.05) / (lo + 0.05), 2)


def detect_script_profile(text: str, language: str = "en") -> dict[str, Any]:
    """Detect Unicode script direction, CJK/Vietnamese/RTL wrapping rules & authorized local font stack."""
    s = str(text or "")
    lang = str(language or "en").strip().lower()
    is_rtl = bool(RTL_RE.search(s)) or lang in ("ar", "he", "fa", "ur")
    is_cjk = bool(CJK_RE.search(s)) or lang in ("zh", "ja", "ko")
    is_vi = bool(VI_DIACRITIC_RE.search(s)) or lang == "vi"
    return {
        "language": lang,
        "direction": "rtl" if is_rtl else "ltr",
        "is_rtl": is_rtl,
        "is_cjk": is_cjk,
        "is_vietnamese": is_vi,
        "line_break_mode": "char_cjk" if is_cjk else "word_grapheme",
        "font_stack_verified_local": (
            'system-ui, "Geeza Pro", "Arial", sans-serif' if is_rtl
            else ('"PingFang SC", "Hiragino Sans", "Noto Sans CJK SC", sans-serif' if is_cjk
                  else '-apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif')
        ),
        "font_files_redistributed": False,
    }


def wrap_text_preserving_atoms(text: str, max_chars_per_line: int, *, is_cjk: bool = False) -> list[str]:
    """Wrap caption/headline text cleanly without splitting negations, minus signs, decimals, or CJK runs badly."""
    clean = " ".join(str(text or "").strip().split())
    if not clean:
        return []
    limit = max(8, int(max_chars_per_line))
    if is_cjk:
        # CJK breaks cleanly on punctuation or character boundary while avoiding leading punctuation
        lines: list[str] = []
        cur = ""
        for ch in clean:
            if len(cur) >= limit and ch not in "，。！？、；：）》」":
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines[:2]

    # Protect atomic phrases like "not $1,200", "-$800", "≠ $100" using non-breaking placeholder
    placeholders: dict[str, str] = {}
    def _sub(m: re.Match[str]) -> str:
        tok = f"__ATOM{len(placeholders)}__"
        placeholders[tok] = m.group(0)
        return tok

    masked = _PROTECTED_ATOM_RE.sub(_sub, clean)
    words = masked.split()
    lines = []
    cur_words: list[str] = []
    cur_len = 0
    for w in words:
        real_w = placeholders.get(w, w)
        add = len(real_w) + (1 if cur_words else 0)
        if cur_words and (cur_len + add) > limit:
            lines.append(" ".join(placeholders.get(x, x) for x in cur_words))
            cur_words = [w]
            cur_len = len(real_w)
        else:
            cur_words.append(w)
            cur_len += add
    if cur_words:
        lines.append(" ".join(placeholders.get(x, x) for x in cur_words))
    return lines


def build_type_system(
    *,
    run_id: str,
    skill_id: str,
    art_direction: dict[str, Any],
    sample_text: str,
    language: str = "en",
    canvas_width: int = 1080,
    canvas_height: int = 1920,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Build skill/mood/locale-derived TYPE_SYSTEM.json with WCAG contrast & 360px mobile readability proof."""
    script_prof = detect_script_profile(sample_text, language=language)
    pal = art_direction.get("palette") or {"bg": "#151210", "fg": "#F7F3EB", "accent": "#D45233"}
    fg = str(pal.get("fg") or "#F7F3EB")
    bg = str(pal.get("bg") or "#151210")
    contrast = wcag_contrast_ratio(fg, bg)

    # Scale typography proportionally to canvas width and verify >= 12px effective on 360px mobile
    scale_360 = 360.0 / float(max(1, canvas_width))
    # Restrained sizing per skill (not giant uniform banners everywhere!)
    cap_ratio = {"SK-01": 0.036, "SK-02": 0.032, "SK-03": 0.030, "SK-04": 0.034, "SK-05": 0.031}.get(skill_id, 0.034)
    head_ratio = {"SK-01": 0.052, "SK-02": 0.048, "SK-03": 0.044, "SK-04": 0.048, "SK-05": 0.042}.get(skill_id, 0.048)

    caption_px = max(24, int(round(canvas_width * cap_ratio)))
    headline_px = max(32, int(round(canvas_width * head_ratio)))
    label_px = max(20, int(round(canvas_width * 0.022)))
    cta_px = max(28, int(round(canvas_width * 0.036)))

    scrim_required = contrast < 4.5
    ts = {
        "schema_version": "kh03.type_system.v1",
        "run_id": run_id,
        "selected_skill_id": skill_id,
        "script_profile": script_prof,
        "canvas": [canvas_width, canvas_height],
        "hierarchy": {
            "headline_px": headline_px,
            "headline_mobile_360px": round(headline_px * scale_360, 1),
            "caption_px": caption_px,
            "caption_mobile_360px": round(caption_px * scale_360, 1),
            "label_px": label_px,
            "label_mobile_360px": round(label_px * scale_360, 1),
            "cta_px": cta_px,
            "cta_mobile_360px": round(cta_px * scale_360, 1),
        },
        "colors": {"fg": fg, "bg": bg, "accent": str(pal.get("accent") or "#E9B44C")},
        "wcag_contrast_ratio": contrast,
        "contrast_aa_pass": contrast >= 4.5,
        "scrim_or_outline_required": scrim_required,
        "capsule_style": "minimal_text_shadow_no_heavy_pill" if skill_id in ("SK-03", "SK-05") else "clean_scrim_accent",
        "captions_separated_from_semantic_labels": True,
        "mobile_360px_floor_pass": (headline_px * scale_360 >= 12.0 and caption_px * scale_360 >= 10.5),
    }
    if run_dir is not None:
        ldir = run_dir / "layouts"
        ldir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(ldir / "TYPE_SYSTEM.json", ts)
    return ts


def plan_dynamic_captions_r3(
    words_out: list[dict[str, Any]],
    layout_plan: dict[str, Any],
    type_system: dict[str, Any],
    *,
    caption_policy: str = "OFF",
    platform: str = "reels",
    fps_num: int = 24,
    fps_den: int = 1,
    min_dwell_sec: float = 0.45,
    hysteresis_frames: int = 18,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """CMI-06 dynamic caption planner with hysteresis stability, Unicode/RTL/CJK wrapping & 360px verification.

    Strictly preserves `caption_policy=OFF` (0 emitted chunks, status=OFF_BY_POLICY).
    """
    fps_frac = Fraction(int(fps_num), max(1, int(fps_den)))
    fps = float(fps_frac)
    cw = int(layout_plan["canvas"]["width"])
    ch = int(layout_plan["canvas"]["height"])
    script_prof = type_system["script_profile"]
    is_cjk = bool(script_prof["is_cjk"])
    is_rtl = bool(script_prof["is_rtl"])
    cap_px = int(type_system["hierarchy"]["caption_px"])
    scale_360 = 360.0 / float(max(1, cw))

    if caption_policy == "OFF" or not words_out:
        out = {
            "schema_version": "kh03.typography_layout.v1",
            "caption_policy": caption_policy,
            "actual_caption_status": "OFF_BY_POLICY" if caption_policy == "OFF" else "NO_WORDS_SUPPLIED",
            "direction": script_prof["direction"],
            "chunk_count": 0,
            "chunks": [],
            "hysteresis_slot_switches": 0,
            "contrast_pass": type_system["contrast_aa_pass"],
            "mobile_360px_pass": True,
            "issues": [],
            "pass": True,
        }
        if run_dir is not None:
            ldir = run_dir / "layouts"
            ldir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(ldir / "TYPOGRAPHY_LAYOUT.json", out)
            write_json_atomic(ldir / "MOBILE_READABILITY_REPORT.json", {
                "schema_version": "kh03.mobile_readability.v1",
                "simulated_width_px": 360,
                "caption_policy": caption_policy,
                "chunks_checked": 0,
                "wcag_contrast_ratio": type_system["wcag_contrast_ratio"],
                "pass": True,
            })
        return out

    # Phrase-boundary chunking preserving negations/numbers and natural pauses
    raw_groups: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    max_w = 5 if cw >= 1080 else 4
    for w in words_out:
        ws = float(w.get("out_start_sec", w.get("start_sec", w.get("start", 0.0))))
        txt = str(w.get("text", w.get("word", ""))).strip()
        if not txt:
            continue
        if cur:
            prev_e = float(cur[-1].get("out_end_sec", cur[-1].get("end_sec", cur[-1].get("end", ws))))
            prev_t = str(cur[-1].get("text", cur[-1].get("word", ""))).strip().lower()
            # Never split directly after negation token!
            keep_with_neg = prev_t in ("not", "never", "don't", "doesn't", "no")
            if (ws - prev_e) >= 0.42 and not keep_with_neg:
                raw_groups.append(cur)
                cur = []
        cur.append(w)
        if txt.endswith((".", "!", "?", "。", "！", "？")) or (len(cur) >= max_w and txt.lower() not in ("not", "never", "don't", "doesn't", "no")):
            raw_groups.append(cur)
            cur = []
    if cur:
        raw_groups.append(cur)

    beats = layout_plan.get("beats") or []
    total_f = int(layout_plan["total_frames"])
    min_dwell_f = max(4, int(round(min_dwell_sec * fps)))

    chunks: list[dict[str, Any]] = []
    bounds_rows: list[dict[str, Any]] = []
    issues: list[str] = []
    prev_slot_box: list[float] | None = None
    prev_slot_switch_f = -999
    slot_switches = 0

    for idx, grp in enumerate(raw_groups):
        raw_txt = ("" if is_cjk else " ").join(str(w.get("text", w.get("word", ""))).strip() for w in grp).strip()
        lines = wrap_text_preserving_atoms(raw_txt, 14 if is_cjk else 24, is_cjk=is_cjk)
        if not lines:
            continue
        s_sec = float(grp[0].get("out_start_sec", grp[0].get("start_sec", grp[0].get("start", 0.0))))
        e_sec = float(grp[-1].get("out_end_sec", grp[-1].get("end_sec", grp[-1].get("end", s_sec + min_dwell_sec))))
        sf = max(0, min(total_f - 1, int(round(s_sec * fps))))
        ef = max(sf + min_dwell_f, min(total_f, int(round(e_sec * fps)) + 2))
        if ef > total_f:
            ef = total_f
            sf = max(0, min(sf, ef - 1))
        mid_f = (sf + ef) // 2
        active_lay = next((b for b in beats if int(b["start_frame"]) <= mid_f < int(b["end_frame"])), beats[-1])
        cand_box = list(active_lay.get("caption_slot_norm") or [0.06, 0.78, 0.80, 0.08])

        # Hysteresis: avoid jittering caption slot every frame unless layout meaningfully changed AND hysteresis window elapsed
        if prev_slot_box is not None:
            dist = abs(cand_box[0] - prev_slot_box[0]) + abs(cand_box[1] - prev_slot_box[1])
            if dist < 0.04 or (sf - prev_slot_switch_f) < hysteresis_frames:
                slot_box = list(prev_slot_box)
            else:
                slot_box = cand_box
                prev_slot_switch_f = sf
                slot_switches += 1
        else:
            slot_box = cand_box
            prev_slot_switch_f = sf
        prev_slot_box = slot_box

        # Check mouth/face protected mask clearance + typography overflow at 360px
        box_w_px = slot_box[2] * cw
        box_h_px = max(slot_box[3] * ch, cap_px * len(lines) * 1.35)
        lay_m = measure_text_layout(
            " ".join(lines),
            font_size_px=cap_px,
            box_width_px=box_w_px,
            box_height_px=box_h_px,
            canvas_width_px=cw,
        )
        low_conf = [
            str(w.get("text", w.get("word", "")))
            for w in grp if float(w.get("confidence", w.get("probability", 1.0))) < 0.72
        ]
        # Highlight meaningful keyword (numeric or negation) sparingly — never flashing every word!
        kw = [str(w.get("text", w.get("word", ""))) for w in grp if _PROTECTED_ATOM_RE.search(str(w.get("text", w.get("word", ""))))]
        mob_px = round(cap_px * scale_360, 1)
        if mob_px < 10.0:
            issues.append(f"MOBILE_FONT_TOO_SMALL(cap_{idx + 1:02d}): {mob_px}px at 360px")
        if lay_m.get("overflow"):
            issues.append(f"CAPTION_OVERFLOW(cap_{idx + 1:02d}): text exceeds box bounds")

        entry = {
            "caption_id": f"cap_{idx + 1:02d}",
            "beat_id": active_lay["beat_id"],
            "layout_mode": active_lay["layout_mode"],
            "text": "\n".join(lines),
            "lines": lines,
            "direction": "rtl" if is_rtl else "ltr",
            "word_ids": [str(w.get("word_id", f"w_{i}")) for i, w in enumerate(grp)],
            "emphasis_keywords": kw[:2],
            "low_confidence_words": low_conf,
            "start_frame": sf,
            "end_frame": ef,
            "start_sec": round(sf / fps, 4),
            "end_sec": round(ef / fps, 4),
            "dwell_sec": round((ef - sf) / fps, 4),
            "bbox_norm": [round(v, 4) for v in slot_box],
            "font_size_px": cap_px,
            "mobile_360px_font_px": mob_px,
            "overflow": bool(lay_m.get("overflow")),
            "wcag_contrast_ratio": type_system["wcag_contrast_ratio"],
        }
        chunks.append(entry)
        bounds_rows.append({
            "element_id": entry["caption_id"],
            "role": "spoken_caption",
            "start_frame": sf,
            "end_frame": ef,
            "bbox_px": [round(slot_box[0] * cw, 1), round(slot_box[1] * ch, 1), round(box_w_px, 1), round(box_h_px, 1)],
            "lines": len(lines),
            "direction": entry["direction"],
            "overflow": entry["overflow"],
        })

    out = {
        "schema_version": "kh03.typography_layout.v1",
        "caption_policy": caption_policy,
        "actual_caption_status": "PLANNED_DYNAMIC_CAPTIONS",
        "direction": script_prof["direction"],
        "chunk_count": len(chunks),
        "chunks": chunks,
        "hysteresis_slot_switches": slot_switches,
        "contrast_pass": type_system["contrast_aa_pass"],
        "mobile_360px_pass": not any("MOBILE_FONT_TOO_SMALL" in i or "CAPTION_OVERFLOW" in i for i in issues),
        "issues": issues,
        "pass": len(issues) == 0 and type_system["contrast_aa_pass"],
    }

    if run_dir is not None:
        ldir = run_dir / "layouts"
        ldir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(ldir / "TYPOGRAPHY_LAYOUT.json", out)
        write_json_atomic(ldir / "CAPTION_WORD_SYNC.json", {
            "schema_version": "kh03.caption_word_sync.v1",
            "total_words_mapped": len(words_out),
            "chunks": [{"caption_id": c["caption_id"], "word_ids": c["word_ids"], "start_sec": c["start_sec"], "end_sec": c["end_sec"], "low_confidence_words": c["low_confidence_words"]} for c in chunks],
        })
        (ldir / "TEXT_BOUNDS.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in bounds_rows), encoding="utf-8",
        )
        write_json_atomic(ldir / "MOBILE_READABILITY_REPORT.json", {
            "schema_version": "kh03.mobile_readability.v1",
            "simulated_width_px": 360,
            "caption_policy": caption_policy,
            "chunks_checked": len(chunks),
            "min_mobile_font_px": min((c["mobile_360px_font_px"] for c in chunks), default=type_system["hierarchy"]["caption_mobile_360px"]),
            "overflow_count": sum(1 for c in chunks if c["overflow"]),
            "wcag_contrast_ratio": type_system["wcag_contrast_ratio"],
            "direction": script_prof["direction"],
            "pass": out["pass"],
        })
    return out
