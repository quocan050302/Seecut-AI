"""KH-03 Adaptive Visual Component System — Real Compiled Families, Security Guard & Collision Solver.

Defines 8 new, genuinely compiled KH-03 component families (registered in KH-02 registry and
compiled into real HTML/CSS/SVG/GSAP by compile_plan.py), strict XSS/eval/network parameter
sanitization, swept bounding-box collision resolution across 9:16/16:9/1:1/4:5, and fallback chains.
"""

from __future__ import annotations

import math
import re
from typing import Any

from scripts.kh02.motion import evaluate_pose, plan_motion
from scripts.kh02.registry import build_registry, select_capability
from scripts.kh02.typography import measure_text_layout

from .contracts import (
    assert_kh03_schema,
    is_strict_number,
    sanitize_identifier,
)

# 8 new genuinely compiled KH-03 visual component families (A..H coverage)
KH03_COMPONENT_CATALOG: list[dict[str, Any]] = [
    {
        "capability_id": "kh03.contradiction_lockup",
        "family": "typography",
        "kind": "kh03_contradiction_lockup",
        "purpose": "Bold typographic contradiction lockup (A != B / ?) with animated SVG stroke & curiosity cue",
        "safe_fallback_id": "r1.kinetic_phrase_mask",
        "sound_roles": ["hook_accent", "emphasis_impact"],
    },
    {
        "capability_id": "kh03.proportional_reservoir",
        "family": "semantic_data",
        "kind": "kh03_proportional_reservoir",
        "purpose": "Exact Decimal-verified proportional segmented allocation bar with pattern+label accessibility",
        "safe_fallback_id": "local.editorial_panel",
        "sound_roles": ["step_click", "paper_rustle"],
    },
    {
        "capability_id": "kh03.rolling_counter",
        "family": "semantic_data",
        "kind": "kh03_rolling_counter",
        "purpose": "Tabular-numeral discrete arithmetic counter settling on verified numeric states",
        "safe_fallback_id": "local.editorial_panel",
        "sound_roles": ["ui_click", "payoff_chime"],
    },
    {
        "capability_id": "kh03.causal_flow_graph",
        "family": "explanatory_structure",
        "kind": "kh03_causal_flow_graph",
        "purpose": "Progressive multi-node cause -> mechanism -> effect diagram with labeled SVG arrows",
        "safe_fallback_id": "hf.stagger_cascade",
        "sound_roles": ["step_click", "evidence_reveal"],
    },
    {
        "capability_id": "kh03.annotated_timeline",
        "family": "explanatory_structure",
        "kind": "kh03_annotated_timeline",
        "purpose": "Source-backed chronological or procedural timeline with progressive step reveals",
        "safe_fallback_id": "hf.stagger_cascade",
        "sound_roles": ["step_click", "transition_whoosh"],
    },
    {
        "capability_id": "kh03.comparison_matrix",
        "family": "semantic_data",
        "kind": "kh03_comparison_matrix",
        "purpose": "Truthful labeled side-by-side comparison across comparable real attributes",
        "safe_fallback_id": "hf.comparison_split",
        "sound_roles": ["object_pop", "evidence_reveal"],
    },
    {
        "capability_id": "kh03.proof_frame_reveal",
        "family": "product_footage",
        "kind": "kh03_proof_frame_reveal",
        "purpose": "Masked editorial reveal of verified product/capture/cover with subtle book-edge thickness (zero fake UI)",
        "safe_fallback_id": "r1.clip_mask_wipe",
        "sound_roles": ["evidence_reveal", "paper_rustle"],
    },
    {
        "capability_id": "kh03.cta_magnetic_underline",
        "family": "narrative_retention",
        "kind": "kh03_cta_magnetic_underline",
        "purpose": "Clean action/payoff lockup with animated SVG magnetic underline & truthful non-deceptive callout",
        "safe_fallback_id": "r1.light_sweep_badge",
        "sound_roles": ["payoff_chime"],
    },
]

_FORBIDDEN_PARAM_PATTERNS = (
    re.compile(r"(?i)<\s*script"),
    re.compile(r"(?i)javascript\s*:"),
    re.compile(r"(?i)\bon(?:load|error|click|mouseover)\s*="),
    re.compile(r"(?i)\beval\s*\("),
    re.compile(r"(?i)\bnew\s+Function\b"),
    re.compile(r"(?i)\bimport\s*\("),
    re.compile(r"(?i)https?://"),
    re.compile(r"(?i)file://"),
)


def discover_capabilities(registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return available KH-02 + KH-03 verified capabilities from unified registry."""
    reg = registry or build_registry()
    kh03_ids = {c["capability_id"] for c in KH03_COMPONENT_CATALOG}
    present = [c for c in reg.get("capabilities", []) if c["capability_id"] in kh03_ids]
    return {
        "total_registry_capabilities": len(reg.get("capabilities", [])),
        "kh03_capabilities_registered": len(present),
        "kh03_capability_ids": [c["capability_id"] for c in present],
        "catalog": list(KH03_COMPONENT_CATALOG),
    }


def _scan_malicious_params(obj: Any, path: str = "$") -> list[str]:
    issues: list[str] = []
    if isinstance(obj, str):
        for pat in _FORBIDDEN_PARAM_PATTERNS:
            if pat.search(obj):
                issues.append(f"SECURITY_PARAM_REJECTED at {path}: matched {pat.pattern!r}")
    elif isinstance(obj, float) and not math.isfinite(obj):
        issues.append(f"NONFINITE_NUMBER at {path}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            issues.extend(_scan_malicious_params(item, f"{path}[{i}]"))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            issues.extend(_scan_malicious_params(k, f"{path}.<key:{k}>"))
            issues.extend(_scan_malicious_params(v, f"{path}.{k}"))
    return issues


def validate_component_spec(
    spec: dict[str, Any],
    *,
    registry: dict[str, Any] | None = None,
    aspect_ratio: str = "9:16",
    fps: float = 24.0,
    profile: str = "editorial_pro",
) -> tuple[bool, list[str]]:
    """Validate ComponentSpec schema, security sandbox rules, bbox bounds & capability availability."""
    errs = [f"{e['code']}: {e['message']}" for e in _validate_spec_schema_safe(spec)]
    errs.extend(_scan_malicious_params(spec.get("params") or {}, "$.params"))
    errs.extend(_scan_malicious_params(spec.get("data_bindings") or {}, "$.data_bindings"))

    if not errs:
        sf, ef = int(spec["start_frame"]), int(spec["end_frame"])
        if ef <= sf:
            errs.append(f"INVALID_FRAME_RANGE: [{sf}, {ef})")
        bx, by, bw, bh = [float(x) for x in spec["normalized_bbox"]]
        if bw <= 0.04 or bh <= 0.02 or bx + bw > 1.001 or by + bh > 1.001:
            errs.append(f"OUT_OF_BOUNDS_BBOX: {spec['normalized_bbox']}")
        if registry is not None:
            dur_s = max(0.1, (ef - sf) / max(1.0, fps))
            sel = select_capability(
                registry, spec["capability_id"], aspect_ratio=aspect_ratio, duration_sec=dur_s, profile=profile,
            )
            if sel["selected_id"] is None:
                errs.append(f"CAPABILITY_BLOCKED: {sel['reason']}")
    return (len(errs) == 0), errs


def _validate_spec_schema_safe(spec: dict[str, Any]) -> list[dict[str, str]]:
    from .contracts import validate_kh03_schema
    return validate_kh03_schema(spec, "component_spec")


def design_component(
    *,
    component_id: str,
    requested_capability_id: str,
    beat_id: str,
    role: str,
    start_frame: int,
    end_frame: int,
    normalized_bbox: list[float],
    params: dict[str, Any],
    semantic_anchor: str,
    registry: dict[str, Any] | None = None,
    aspect_ratio: str = "9:16",
    fps: float = 24.0,
    profile: str = "editorial_pro",
    z_order: int = 32,
    asset_ref: str | None = None,
    data_bindings: dict[str, Any] | None = None,
    easing: str | None = None,
    anchor_word_id: str | None = None,
    anchor_precision: str | None = None,
    internal_nodes: list[dict[str, Any]] | None = None,
    shared_element_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Design a schema-valid ComponentSpec with automatic verified fallback for unsupported capability requests."""
    reg = registry or build_registry()
    dur_s = max(0.3, (int(end_frame) - int(start_frame)) / max(1.0, fps))
    sel = select_capability(reg, requested_capability_id, aspect_ratio=aspect_ratio, duration_sec=dur_s, profile=profile)
    eff_cap_id = sel["selected_id"] or "local.clean_fade_slide"

    cat_meta = next((c for c in KH03_COMPONENT_CATALOG if c["capability_id"] == eff_cap_id), None)
    family = cat_meta["family"] if cat_meta else ("typography" if "kinetic" in eff_cap_id else "explanatory_structure")
    kind = cat_meta["kind"] if cat_meta else str(params.get("kind") or "editorial_headline")
    fallback_id = cat_meta["safe_fallback_id"] if cat_meta else "local.clean_fade_slide"

    # Reject malicious params fail-closed before building spec
    sec_issues = _scan_malicious_params(params, "$.params")
    if sec_issues:
        raise ValueError(f"Malicious component params rejected: {sec_issues[:2]}")

    eff_easing = easing or ("power2.out" if profile == "minimal_documentary" else "power3.out")
    if profile == "minimal_documentary" and eff_easing.startswith("back.out"):
        eff_easing = "power2.out"

    total = max(2, int(end_frame) - int(start_frame))
    en = min(max(3, int(round(0.25 * fps))), max(1, total // 3))
    ex = min(max(2, int(round(0.16 * fps))), max(1, total // 4))
    hd = max(0, total - en - ex)

    clean_params = dict(params)
    clean_params["kind"] = kind
    # Responsive font floor so text remains legible (>= 11.5px effective) when scaled down to 360px mobile width
    canvas_w = 1280 if aspect_ratio == "16:9" else 720
    min_font_px = max(23, int((11.5 * canvas_w / 360.0) + 0.999))
    raw_fs = clean_params.get("font_size_px", 24)
    if isinstance(raw_fs, (int, float)) and raw_fs < min_font_px:
        clean_params["font_size_px"] = min_font_px
    if sel.get("fallback_used"):
        clean_params["fallback_from"] = requested_capability_id
        clean_params["fallback_reason"] = sel["reason"]

    spec = {
        "schema_version": "kh03.component_spec.v1",
        "component_id": sanitize_identifier(component_id),
        "family": family,
        "capability_id": eff_cap_id,
        "kind": kind,
        "role": role,
        "beat_id": beat_id,
        "start_frame": int(start_frame),
        "end_frame": int(end_frame),
        "normalized_bbox": [round(float(x), 4) for x in normalized_bbox],
        "z_order": int(z_order),
        "easing": eff_easing,
        "entry_frames": en,
        "hold_frames": hd,
        "exit_frames": ex,
        "params": clean_params,
        "data_bindings": dict(data_bindings or {}),
        "asset_ref": asset_ref,
        "protected_regions_checked": False,
        "safe_fallback_id": fallback_id,
        "rights": "ORIGINAL_KH03_COMPONENT_POLYFORM_NONCOMMERCIAL",
        "semantic_anchor": semantic_anchor,
        "anchor_word_id": anchor_word_id,
        "anchor_precision": anchor_precision,
        "internal_nodes": list(internal_nodes or []),
        "shared_element_ids": list(shared_element_ids or []),
    }
    assert_kh03_schema(spec, "component_spec")
    return spec


def _rects_overlap(a: list[float], b: list[float]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx + 1e-5 or bx + bw <= ax + 1e-5 or ay + ah <= by + 1e-5 or by + bh <= ay + 1e-5)


def solve_visual_collisions(
    specs: list[dict[str, Any]],
    *,
    protected_zones: list[dict[str, Any]],
    clear_zones: list[dict[str, Any]],
    canvas_width: int = 720,
    canvas_height: int = 1280,
    y_sweep_px: float = 16.0,
) -> dict[str, Any]:
    """Check swept entry/hold/exit bounding boxes against face/hands/objects & reposition into verified clear zones."""
    dy_norm = y_sweep_px / float(max(1, canvas_height))
    prot_boxes = [list(z["bbox_norm"]) for z in protected_zones if isinstance(z.get("bbox_norm"), list) and len(z["bbox_norm"]) == 4]
    clear_boxes = [list(z["bbox_norm"]) for z in clear_zones if isinstance(z.get("bbox_norm"), list) and len(z["bbox_norm"]) == 4]

    resolved: list[dict[str, Any]] = []
    reflows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for sp in specs:
        out = dict(sp)
        bx, by, bw, bh = [float(v) for v in out["normalized_bbox"]]
        swept = [bx, max(0.0, by - dy_norm), bw, min(1.0 - max(0.0, by - dy_norm), bh + dy_norm)]
        hit = any(_rects_overlap(swept, pb) for pb in prot_boxes)

        if hit and clear_boxes:
            placed = False
            for cb in clear_boxes:
                cx, cy, cw, ch = cb
                # Fit component cleanly inside clear box with sweep margin
                nx = cx
                ny = cy + min(0.004, dy_norm * 0.25)
                nw = min(bw, cw)
                nh = min(bh, max(0.05, ch - dy_norm))
                cand_swept = [nx, max(0.0, ny - dy_norm * 0.25), nw, nh + dy_norm * 0.25]
                if not any(_rects_overlap(cand_swept, pb) for pb in prot_boxes):
                    old_box = list(out["normalized_bbox"])
                    out["normalized_bbox"] = [round(nx, 4), round(ny, 4), round(nw, 4), round(nh, 4)]
                    reflows.append({"component_id": out["component_id"], "from": old_box, "to": out["normalized_bbox"]})
                    placed = True
                    break
            if not placed:
                conflicts.append({"component_id": out["component_id"], "bbox": out["normalized_bbox"], "reason": "No non-colliding clear zone available"})
        elif hit:
            conflicts.append({"component_id": out["component_id"], "bbox": out["normalized_bbox"], "reason": "Collides with protected zone and no clear zone provided"})

        # Check 360px mobile legibility of primary headline text if present
        headline = str((out.get("params") or {}).get("headline") or "")
        if headline:
            lay = measure_text_layout(
                headline,
                font_size_px=int((out.get("params") or {}).get("font_size_px", 28)),
                box_width_px=out["normalized_bbox"][2] * canvas_width,
                box_height_px=out["normalized_bbox"][3] * canvas_height,
                canvas_width_px=canvas_width,
            )
            out["params"] = dict(out.get("params") or {})
            out["params"]["mobile_360px_readable"] = bool(lay["mobile_readable"])

        out["protected_regions_checked"] = (len([c for c in conflicts if c["component_id"] == out["component_id"]]) == 0)
        resolved.append(out)

    return {
        "ok": len(conflicts) == 0,
        "components": resolved,
        "reflows": reflows,
        "conflicts": conflicts,
    }


def compile_component_events(
    specs: list[dict[str, Any]],
    *,
    fps: float = 24.0,
    profile: str = "editorial_pro",
) -> list[dict[str, Any]]:
    """Convert validated KH-03 ComponentSpecs into KH-02 SHOTBOOK_V2 visual_events ready for compile_plan.py."""
    raw_events: list[dict[str, Any]] = []
    for idx, sp in enumerate(specs):
        assert_kh03_schema(sp, "component_spec")
        layer_cat = "evbg" if (sp["family"] == "product_footage" and sp.get("asset_ref")) else (
            "title" if sp["family"] == "typography" else "card"
        )
        anchor_wid = sp.get("anchor_word_id")
        anchor_prec = str(sp.get("anchor_precision") or ("word_exact" if anchor_wid else "beat_start"))
        ev_params = dict(sp["params"])
        if sp.get("internal_nodes"):
            ev_params["internal_nodes"] = list(sp["internal_nodes"])
        if sp.get("shared_element_ids"):
            ev_params["shared_element_ids"] = list(sp["shared_element_ids"])
        raw_events.append({
            "event_id": sp["component_id"],
            "beat_id": sp["beat_id"],
            "track_id": "track_evidence" if layer_cat == "evbg" else "track_info_primary",
            "layer_id": sanitize_identifier(f"kh03_{sp['component_id']}"),
            "layer_cat": layer_cat,
            "z_order": int(sp["z_order"]),
            "asset_ref": sp.get("asset_ref"),
            "capability_id": sp["capability_id"],
            "start_frame": int(sp["start_frame"]),
            "end_frame": int(sp["end_frame"]),
            "anchor_word_id": anchor_wid,
            "anchor_mode": "keyword_exact" if anchor_prec == "word_exact" else "beat_start",
            "entry_frames": int(sp["entry_frames"]),
            "hold_frames": int(sp["hold_frames"]),
            "exit_frames": int(sp["exit_frames"]),
            "normalized_bbox": list(sp["normalized_bbox"]),
            "opacity": 1.0,
            "transform": {"scale_from": 0.96, "scale_to": 1.0, "y_offset_px": 8},
            "mask": {"type": "clip_inset_horizontal"} if sp["kind"] == "kh03_proof_frame_reveal" else None,
            "blend": "normal",
            "easing": sp["easing"],
            "params": ev_params,
            "safe_zone": True,
            "face_exclusion": True,
            "semantic_reason": sp["semantic_anchor"],
            "expected_visual_evidence": f"KH-03 {sp['capability_id']} ({sp['family']}) visible in verified safe zone.",
        })
    return plan_motion(raw_events, fps=fps, profile=profile)


def audit_component_motion(visual_events: list[dict[str, Any]], *, fps: float = 24.0) -> dict[str, Any]:
    """Verify deterministic outer + internal-node motion progression across start/mid/end frames (CMI-02)."""
    families: dict[str, int] = {}
    anomalies: list[str] = []
    internal_node_count = 0
    word_anchored_count = 0
    for ev in visual_events:
        cid = ev.get("capability_id", "unknown")
        families[cid] = families.get(cid, 0) + 1
        sf, ef = int(ev["start_frame"]), int(ev["end_frame"])
        mid = sf + max(1, (ef - sf) // 2)
        p_start = evaluate_pose(ev, sf)
        p_mid = evaluate_pose(ev, mid)
        p_after = evaluate_pose(ev, ef)
        if p_mid["opacity"] < 0.5:
            anomalies.append(f"{ev['event_id']}: low mid-beat opacity {p_mid['opacity']}")
        if p_after["opacity"] != 0.0:
            anomalies.append(f"{ev['event_id']}: did not exit cleanly at end_frame {ef}")
        if evaluate_pose(ev, mid) != p_mid or evaluate_pose(ev, sf) != p_start:
            anomalies.append(f"{ev['event_id']}: non-deterministic pose evaluation")
        if ev.get("anchor_word_id"):
            word_anchored_count += 1
        inodes = (ev.get("params") or {}).get("internal_nodes") or []
        internal_node_count += len(inodes)
        for nd in inodes:
            nsf, nef = int(nd.get("start_frame", sf)), int(nd.get("end_frame", ef))
            if nef <= nsf:
                anomalies.append(f"{ev['event_id']}.{nd.get('node_id')}: invalid internal frame range [{nsf},{nef})")
    return {
        "total_events": len(visual_events),
        "unique_capabilities": len(families),
        "capability_counts": families,
        "internal_node_count": internal_node_count,
        "word_anchored_events": word_anchored_count,
        "anomalies": anomalies,
        "pass": len(anomalies) == 0,
    }


def register_verified_component(registry: dict[str, Any], capability_id: str, preview_info: dict[str, Any]) -> dict[str, Any]:
    """Mark a KH-03 component as IMPLEMENTED_VERIFIED only after real browser render verification."""
    from scripts.kh02.registry import mark_gallery_verified
    return mark_gallery_verified(registry, {capability_id: preview_info})
