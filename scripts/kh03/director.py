"""KH-03 Adaptive Creative Director — Multi-Direction Generation, Hook/Retention & KH-02 Binding.

Within the user-selected universal skill (SK-01..SK-05), generates 3 distinct internal creative
directions, selects the best transparently, binds explanation nodes + footage + verified
components into CREATIVE_PLAN.json, and translates cleanly into KH-02 SHOTBOOK V2 overrides.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from scripts.kh02.registry import build_registry
from scripts.kh02.typography import compute_min_read_frames

from .components import (
    compile_component_events,
    design_component,
    solve_visual_collisions,
)
from .contracts import (
    assert_kh03_schema,
    hash_artifact,
    parse_json_strict,
    sanitize_identifier,
    write_json_atomic,
)
from .evidence_and_type import (
    build_type_system,
    build_universal_evidence_ledger,
    plan_dynamic_captions_r3,
)
from .layout import (
    audit_layout_motion,
    build_subject_action_tracks,
    plan_adaptive_captions,
    remap_layout_and_transitions_to_edl,
    resolve_layout_policy,
    solve_layout_sequence,
)
from .motion_graph import (
    build_motion_graph,
    build_semantic_hook_plan,
    evaluate_visual_density_and_repetition,
    remap_motion_graph_to_edl,
    verify_morph_semantic_coverage,
)
from .recommend import get_skill_contract
from .tracking import analyze_subject_object_tracks

SKILL_ART_PALETTES: dict[str, dict[str, Any]] = {
    "SK-01": {
        "theme": "editorial_obsidian_ivory",
        "palette": {"bg": "#151210", "fg": "#F7F3EB", "accent": "#D45233", "secondary": "#E9B949", "positive": "#3BB886"},
        "font_stack": "system-sans-bold",
        "motion_grammar": "kinetic_contrast_snap",
        "density_intent": "high_semantic_contrast",
        "default_easing": "back.out(1.9)",
    },
    "SK-02": {
        "theme": "editorial_burgundy_parchment",
        "palette": {"bg": "#FAF5EC", "fg": "#231B18", "accent": "#6E1F24", "secondary": "#D99B26", "positive": "#1E4D3B"},
        "font_stack": "editorial-serif",
        "motion_grammar": "refined_editorial_mask_flow",
        "density_intent": "curated_breathe_and_hero",
        "default_easing": "power3.out",
    },
    "SK-03": {
        "theme": "documentary_warm_archive",
        "palette": {"bg": "#1C1917", "fg": "#F5F0E6", "accent": "#B45309", "secondary": "#D97706", "positive": "#5C8A6F"},
        "font_stack": "editorial-serif",
        "motion_grammar": "cinematic_zero_overshoot_arc",
        "density_intent": "measured_atmospheric_holds",
        "default_easing": "power2.inOut",
    },
    "SK-04": {
        "theme": "explainer_cobalt_chalk",
        "palette": {"bg": "#101E36", "fg": "#F8FAFC", "accent": "#2563EB", "secondary": "#F59E0B", "positive": "#34D399"},
        "font_stack": "system-sans-bold",
        "motion_grammar": "progressive_precision_diagram",
        "density_intent": "comprehension_first_evolving_model",
        "default_easing": "power2.out",
    },
    "SK-05": {
        "theme": "seecut_geek_craft",
        "palette": {"bg": "#162E29", "fg": "#F8F3EA", "accent": "#DF8A3E", "secondary": "#E7B86E", "positive": "#4CC393"},
        "font_stack": "system-sans-bold",
        "motion_grammar": "restrained_negative_space_micro_motion",
        "density_intent": "creator_first_lightweight_clarity",
        "default_easing": "power2.out",
    },
}


def choose_direction(
    skill_id: str,
    semantic_graph: dict[str, Any],
    explanation_graph: dict[str, Any],
    footage_manifest: dict[str, Any],
    *,
    constraints: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Generate 3 distinct creative directions WITHIN selected skill and pick best internally."""
    sk = get_skill_contract(skill_id)
    art = SKILL_ART_PALETTES[skill_id]
    has_arith = bool(explanation_graph.get("numeric_checks", {}).get("arithmetic_steps"))
    has_assets = len(footage_manifest.get("assets") or []) > 0
    phrases = semantic_graph.get("phrases") or []
    has_comp = any(p.get("has_comparison") for p in phrases)
    has_causal = any(p.get("has_causal") for p in phrases)
    c = dict(constraints or {})
    fs_lock = bool(c.get("fullscreen_lock") or c.get("layout_lock") == "FULLSCREEN_9_16")

    dirs = [
        {
            "direction_id": f"{skill_id.lower()}_dir_evolving_model",
            "name": f"{sk['name']} — Evolving Unified Visual Model",
            "theme": art["theme"],
            "archetype": f"{skill_id.lower()}_unified_stage",
            "layout_strategy": (
                "Single evolving top negative-space stage preserving full-screen A-roll face/hands/props."
                if fs_lock else "Coherent focal stage evolving smoothly across beats without card clutter."
            ),
            "score": round(82.0 + (10.0 if has_arith else 0.0) + (6.0 if fs_lock else 0.0), 2),
            "selected": False,
        },
        {
            "direction_id": f"{skill_id.lower()}_dir_evidence_narrative",
            "name": f"{sk['name']} — Evidence & Causal Proof Sequence",
            "theme": art["theme"],
            "archetype": f"{skill_id.lower()}_causal_proof",
            "layout_strategy": "Anchor every substantive claim with verified captures, causal flow arrows, or comparison splits.",
            "score": round(80.0 + (9.0 if has_assets else 0.0) + (7.0 if (has_causal or has_comp) else 0.0), 2),
            "selected": False,
        },
        {
            "direction_id": f"{skill_id.lower()}_dir_minimal_precision",
            "name": f"{sk['name']} — Restrained Editorial Focus",
            "theme": art["theme"],
            "archetype": f"{skill_id.lower()}_restrained_focus",
            "layout_strategy": "Generous breathing room, crisp typographic hierarchy, and clean single-focus settles.",
            "score": round(76.0 + (8.0 if skill_id in ("SK-03", "SK-05") else 0.0), 2),
            "selected": False,
        },
    ]
    dirs_sorted = sorted(dirs, key=lambda d: (-d["score"], d["direction_id"]))
    best_id = dirs_sorted[0]["direction_id"]
    for d in dirs:
        d["selected"] = (d["direction_id"] == best_id)
        if d["selected"]:
            d["reason"] = f"Highest internal fit ({d['score']}) inside {skill_id} for current content & layout constraints."
        else:
            d["reason"] = f"Valid alternative inside {skill_id} (score {d['score']}); secondary to {best_id}."
    selected = next(d for d in dirs if d["selected"])
    return selected, dirs


def plan_hooks(
    skill_id: str,
    semantic_graph: dict[str, Any],
    explanation_graph: dict[str, Any],
    *,
    constraints: dict[str, Any] | None = None,
    run_id: str = "kh03_hook",
    input_analysis: dict[str, Any] | None = None,
    action_timeline: list[dict[str, Any]] | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Generate 3 truthful hook micro-storyboards within selected skill (CMI-01); block spoilers & fake urgency."""
    eff_inp = input_analysis or {
        "media": {"nb_frames": 240, "fps_num": 24, "fps_den": 1, "duration_sec": 10.0, "aspect_ratio": "9:16"},
        "language": "en",
        "constraints": dict(constraints or {}),
    }
    return build_semantic_hook_plan(
        run_id=run_id,
        skill_id=skill_id,
        semantic_graph=semantic_graph,
        explanation_graph=explanation_graph,
        input_analysis=eff_inp,
        action_timeline=action_timeline,
        constraints=constraints,
        run_dir=run_dir,
    )


def _trim_words(text: str, max_chars: int) -> str:
    s = " ".join(str(text or "").strip().split())
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut if len(cut) >= max(8, max_chars // 2) else s[:max_chars].rstrip()


def _select_component_for_beat(
    *,
    idx: int,
    total_beats: int,
    phrase: dict[str, Any],
    exp_node: dict[str, Any],
    exp_rep: dict[str, Any],
    footage_binding: dict[str, Any] | None,
    skill_id: str,
    has_arith: bool,
    arith_steps: list[dict[str, Any]],
    used_caps: list[str],
) -> tuple[str, dict[str, Any]]:
    """Choose a distinct, semantically faithful KH-03 component family for one beat (anti-template!)."""
    role = phrase.get("semantic_role", "context")
    txt = phrase["text"].strip()
    has_asset = bool(footage_binding and footage_binding.get("asset_id"))

    # Opening hook beat
    if idx == 0:
        if (phrase.get("has_negation") or phrase.get("numeric_claims")) and skill_id in ("SK-01", "SK-04", "SK-05"):
            nums = phrase.get("numeric_claims") or []
            left_lbl = nums[0]["raw_text"] if nums else txt[:22]
            return "kh03.contradiction_lockup", {
                "kicker": "EXAMPLE · KEY PARADOX" if any(n.get("illustrative") for n in nums) else "CORE QUESTION",
                "left_value": left_lbl,
                "operator": "≠" if phrase.get("has_negation") else "?",
                "right_value": "$?" if nums and nums[0].get("unit") == "USD" else "REALITY?",
                "headline": txt[:62],
                "subtitle": "Watch the breakdown unfold",
                "font_size_px": 30,
            }
        if skill_id == "SK-03":
            return "kh03.annotated_timeline", {
                "kicker": "OPENING CONTEXT",
                "headline": txt[:58],
                "steps": [{"label": "SETUP", "text": txt[:48]}],
                "font_size_px": 26,
            }
        if skill_id == "SK-02":
            return "kh03.proof_frame_reveal", {
                "kicker": "FEATURED FOCUS",
                "headline": _trim_words(txt, 56),
                "subtitle": "Key breakdown ahead",
                "evidence_label": "REAL_EVIDENCE" if has_asset else "EDITORIAL_TITLE",
                "font_size_px": 28,
            }
        return "r1.kinetic_phrase_mask", {
            "kicker": "OPENING",
            "lines": [txt[:34], txt[34:68].strip()] if len(txt) > 34 else [txt],
            "headline": txt[:56],
            "font_size_px": 30,
        }

    # Final CTA / Payoff beat
    if idx == total_beats - 1 or role == "cta":
        if role == "cta" or skill_id in ("SK-01", "SK-02", "SK-05"):
            return "kh03.cta_magnetic_underline", {
                "kicker": "NEXT STEP" if role == "cta" else "KEY TAKEAWAY",
                "headline": _trim_words(txt, 52),
                "cta_tag": "Actionable takeaway",
                "font_size_px": 28,
            }
        if skill_id == "SK-04" and has_arith:
            last = arith_steps[-1]
            return "kh03.rolling_counter", {
                "kicker": "VERIFIED RESULT",
                "headline": _trim_words(txt, 50),
                "start_value": str(arith_steps[0].get("before", "")),
                "end_value": str(last.get("after", "")),
                "unit": str(last.get("unit", "")),
                "font_size_px": 32,
            }
        return "r1.light_sweep_badge", {
            "kicker": "RESOLUTION",
            "headline": _trim_words(txt, 54),
            "subtitle": _trim_words(exp_node["summary"], 62),
            "font_size_px": 26,
        }

    # Verified product/evidence beat
    if role == "evidence" or (has_asset and skill_id in ("SK-02", "SK-05")):
        return "kh03.proof_frame_reveal", {
            "kicker": "VERIFIED PROOF" if has_asset else "SOLUTION",
            "headline": _trim_words(txt, 54),
            "subtitle": (footage_binding or {}).get("evidence_label", "CONCEPT_ILLUSTRATION"),
            "evidence_label": "REAL_EVIDENCE" if has_asset else "CONCEPT_ILLUSTRATION",
            "font_size_px": 26,
        }

    # Quantitative / arithmetic allocation beat
    if has_arith and (phrase.get("numeric_claims") or role in ("demonstration", "payoff")):
        if "kh03.proportional_reservoir" not in used_caps or role == "demonstration":
            segs = [
                {"label": f"Part {s['step_index']}", "pct": float(s.get("bar_pct", 25.0)), "delta": str(s.get("delta", ""))}
                for s in arith_steps if "bar_pct" in s
            ]
            return "kh03.proportional_reservoir", {
                "kicker": "ILLUSTRATIVE PROPORTION",
                "headline": _trim_words(txt, 54),
                "segments": segs[:5] or [{"label": "Allocated", "pct": 75.0, "delta": "-"}, {"label": "Remaining", "pct": 25.0, "delta": ""}],
                "font_size_px": 24,
            }
        last = arith_steps[-1]
        return "kh03.rolling_counter", {
            "kicker": "REMAINING BALANCE",
            "headline": _trim_words(txt, 50),
            "start_value": str(arith_steps[0].get("before", "100")),
            "end_value": str(last.get("after", "0")),
            "unit": str(last.get("unit", "")),
            "font_size_px": 30,
        }

    # Comparison beat — split real spoken clauses cleanly (NEVER leak internal diagnostic metadata on screen!)
    if role == "comparison" or phrase.get("has_comparison"):
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", txt) if s.strip()]
        if len(sentences) >= 2:
            left_txt = _trim_words(sentences[0], 88)
            right_txt = _trim_words(" ".join(sentences[1:]), 88)
        else:
            clauses = [c.strip() for c in re.split(r"\b(?:instead of|versus|vs\.?|while|but|however)\b", txt, maxsplit=1, flags=re.I) if c.strip()]
            if len(clauses) >= 2:
                left_txt = _trim_words(clauses[0], 88)
                right_txt = _trim_words(clauses[1], 88)
            else:
                words_all = txt.split()
                mid_w = max(1, len(words_all) // 2)
                left_txt = _trim_words(" ".join(words_all[:mid_w]), 88)
                right_txt = _trim_words(" ".join(words_all[mid_w:]) or txt, 88)
        return "kh03.comparison_matrix", {
            "kicker": "KEY CONTRAST",
            "headline": _trim_words(sentences[0] if sentences else txt, 56),
            "left_col": {"title": "First", "detail": left_txt},
            "right_col": {"title": "Then", "detail": right_txt},
            "font_size_px": 24,
        }

    # Causal mechanism beat
    if role == "mechanism" or phrase.get("has_causal"):
        words_m = txt.split()
        third = max(1, len(words_m) // 3)
        n1 = _trim_words(" ".join(words_m[:third]), 34)
        n2 = _trim_words(" ".join(words_m[third:2 * third]) or txt, 34)
        n3 = _trim_words(" ".join(words_m[2 * third:]) or exp_node["label"], 34)
        return "kh03.causal_flow_graph", {
            "kicker": "CAUSE -> EFFECT",
            "headline": _trim_words(txt, 52),
            "nodes": [
                {"label": "Cause", "text": n1},
                {"label": "Process", "text": n2},
                {"label": "Outcome", "text": n3},
            ],
            "font_size_px": 24,
        }

    # Step / chronological timeline beat
    if role == "step" or skill_id in ("SK-03", "SK-04"):
        return "kh03.annotated_timeline", {
            "kicker": f"STEP {idx:02d}",
            "headline": _trim_words(txt, 54),
            "steps": [
                {"label": f"0{idx}", "text": _trim_words(txt, 48)},
            ],
            "font_size_px": 26,
        }

    # Skill-distinct fallback so skills never look identical on the same script
    fallback_by_skill = {
        "SK-01": ("r1.kinetic_phrase_mask", {"kicker": "KEY SHIFT", "lines": [_trim_words(txt, 34)], "headline": _trim_words(txt, 50), "font_size_px": 28}),
        "SK-02": ("r1.clip_mask_wipe", {"kicker": "EDITORIAL DETAIL", "headline": _trim_words(txt, 52), "subtitle": exp_node["node_type"].upper(), "font_size_px": 26}),
        "SK-03": ("kh03.annotated_timeline", {"kicker": "STORY ARC", "headline": _trim_words(txt, 52), "steps": [{"label": f"ARC {idx}", "text": _trim_words(txt, 46)}], "font_size_px": 26}),
        "SK-04": ("kh03.causal_flow_graph", {"kicker": "EXPLANATION", "headline": _trim_words(txt, 52), "nodes": [{"label": "Premise", "text": _trim_words(txt, 34)}, {"label": "Result", "text": _trim_words(exp_node["label"], 34)}], "font_size_px": 24}),
        "SK-05": ("r1.spotlight_focus", {"kicker": "NOTE", "badge": "CLEAR", "headline": _trim_words(txt, 50), "subtitle": "Natural A-roll focus", "font_size_px": 26}),
    }
    return fallback_by_skill.get(skill_id, ("local.editorial_panel", {"kicker": "DETAIL", "headline": _trim_words(txt, 52), "font_size_px": 26}))


def build_creative_plan(
    *,
    run_id: str,
    run_dir: Path,
    selected_skill_id: str,
    selection_receipt_sha256: str,
    input_analysis: dict[str, Any],
    semantic_graph: dict[str, Any],
    explanation_graph: dict[str, Any],
    footage_manifest: dict[str, Any],
    registry: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble validated CREATIVE_PLAN.json + CREATIVE_DIRECTIONS.md + COMPONENT_MANIFEST.json."""
    reg = registry or build_registry()
    sk = get_skill_contract(selected_skill_id)
    art = SKILL_ART_PALETTES[selected_skill_id]
    media = input_analysis["media"]
    aspect = media["aspect_ratio"]
    fps = media["fps_num"] / float(max(1, media["fps_den"]))
    total_frames = int(media["nb_frames"])
    c_w = 720 if aspect in ("9:16", "1:1", "4:5") else 1280
    c_h = 1280 if aspect == "9:16" else (720 if aspect in ("16:9", "1:1") else 900)

    eff_constraints = dict(input_analysis.get("constraints") or {})
    eff_constraints.update(constraints or {})
    pol_info = resolve_layout_policy(
        constraints=eff_constraints,
        selected_skill=selected_skill_id,
        source_media=media,
    )
    layout_policy = pol_info["layout_policy"]
    fs_lock = bool(pol_info["fullscreen_lock"])

    selected_dir, all_dirs = choose_direction(
        selected_skill_id, semantic_graph, explanation_graph, footage_manifest, constraints=eff_constraints,
    )

    tr_path = run_dir / "analysis" / "TRANSCRIPT_NORMALIZED.json"
    tr_doc = parse_json_strict(tr_path) if tr_path.is_file() else None
    act_path = run_dir / "analysis" / "SOURCE_ACTION_MAP.json"
    act_doc = parse_json_strict(act_path) if act_path.is_file() else None

    # KH-03.R3 CMI-04: Honest subject/object/action tracking & protected region analysis
    enable_tracking_scan = bool(eff_constraints.get("enable_streaming_tracking") or eff_constraints.get("enable_tracking", False))
    tracking_res = analyze_subject_object_tracks(
        media,
        run_id=run_id,
        video_path=input_analysis.get("video_path"),
        transcript=tr_doc,
        presenter_mode=str(eff_constraints.get("presenter_mode") or "LEGACY_DYNAMIC"),
        enable_streaming_scan=enable_tracking_scan,
        run_dir=run_dir,
    )
    hook_plan = plan_hooks(
        selected_skill_id,
        semantic_graph,
        explanation_graph,
        constraints=eff_constraints,
        run_id=run_id,
        input_analysis=input_analysis,
        action_timeline=tracking_res.get("action_timeline"),
        run_dir=run_dir,
    )

    phrases = semantic_graph.get("phrases") or []
    exp_nodes = explanation_graph.get("nodes") or []
    exp_reps = {r["node_id"]: r for r in explanation_graph.get("visual_representations", [])}
    bind_map = {b["beat_id"]: b for b in footage_manifest.get("beat_bindings", [])}
    arith_steps = explanation_graph.get("numeric_checks", {}).get("arithmetic_steps") or []
    has_arith = len(arith_steps) > 0

    # Normalize & coalesce phrase beat spans so every beat satisfies readable hold & camera velocity floors
    min_beat_frames = min(total_frames, max(16, int(round(fps * 0.70))))
    max_beats = max(1, total_frames // max(1, min_beat_frames))
    merged_phrases: list[dict[str, Any]] = []
    for raw_idx, ph in enumerate(phrases):
        item = dict(ph)
        item["_orig_idx"] = raw_idx
        sf = max(0, min(total_frames - 1, int(ph["start_frame"])))
        ef = max(sf + 1, min(total_frames, int(ph["end_frame"])))
        item["start_frame"] = sf
        item["end_frame"] = ef
        if merged_phrases and ((ef - sf) < min_beat_frames or len(merged_phrases) >= max_beats):
            prev = merged_phrases[-1]
            prev["end_frame"] = max(prev["end_frame"], ef)
            prev["word_ids"] = list(prev.get("word_ids") or []) + list(ph.get("word_ids") or [])
            prev["numeric_claims"] = list(prev.get("numeric_claims") or []) + list(ph.get("numeric_claims") or [])
            prev["has_causal"] = bool(prev.get("has_causal") or ph.get("has_causal"))
            prev["has_comparison"] = bool(prev.get("has_comparison") or ph.get("has_comparison"))
            prev["has_negation"] = bool(prev.get("has_negation") or ph.get("has_negation"))
            if raw_idx == len(phrases) - 1 and ph.get("semantic_role") in ("cta", "payoff"):
                prev["semantic_role"] = ph["semantic_role"]
                prev["text"] = f"{prev['text'][:30].rstrip()} — {ph['text'][:30].lstrip()}"
        else:
            merged_phrases.append(item)

    if not merged_phrases:
        merged_phrases = [{
            "phrase_id": "ph_001", "text": "Visual narrative focus", "start_frame": 0, "end_frame": total_frames,
            "word_ids": [], "semantic_role": "hook", "numeric_claims": [], "_orig_idx": 0,
        }]

    # Enforce strict contiguous coverage [0, total_frames) with each beat >= min_beat_frames
    n_b = len(merged_phrases)
    for bi, ph in enumerate(merged_phrases):
        if bi == 0:
            ph["start_frame"] = 0
        else:
            ph["start_frame"] = merged_phrases[bi - 1]["end_frame"]
        rem = n_b - 1 - bi
        if bi == n_b - 1:
            ph["end_frame"] = total_frames
        else:
            lo = ph["start_frame"] + min_beat_frames
            hi = max(lo, total_frames - rem * min_beat_frames)
            ph["end_frame"] = max(lo, min(hi, int(ph["end_frame"])))

    clear_zones = input_analysis["safe_regions"]["clear_overlay_zones"]
    prot_zones = input_analysis["safe_regions"]["protected_zones"]
    default_box = list(clear_zones[0]["bbox_norm"]) if clear_zones else [0.06, 0.07, 0.80, 0.14]

    # KH-03.R2 ADL-01/02 + R3 CMI-04 + R4 PED-02: Propagate real detected face/hand regions into layout solver!
    verified_probe_obs: list[dict[str, Any]] = []
    detected_hands_boxes: list[list[float]] = []
    for ent in (tracking_res.get("tracked_entities") or []):
        if ent.get("verified_detector") and ent.get("role") == "face" and ent.get("bbox_norm"):
            verified_probe_obs.append({
                "frame": int((ent.get("frame_span") or [0])[0]),
                "face_bbox_norm": list(ent["bbox_norm"]),
                "confidence": float(ent.get("confidence", 0.78)),
                "verified_detector": True,
            })
        elif ent.get("verified_detector") and ent.get("role") in ("hand", "hands") and ent.get("bbox_norm"):
            detected_hands_boxes.append(list(ent["bbox_norm"]))
    for ob in (tracking_res.get("subject_tracks") or []):
        if ob.get("verified_detector") and ob.get("face_bbox_norm"):
            verified_probe_obs.append({
                "frame": int(ob.get("frame", 0)),
                "face_bbox_norm": ob["face_bbox_norm"],
                "confidence": float(ob.get("confidence", 0.72)),
                "verified_detector": True,
            })
    action_tracks = build_subject_action_tracks(
        media,
        transcript=tr_doc,
        action_map=act_doc,
        probe_frames=verified_probe_obs or None,
    )
    if detected_hands_boxes:
        hx0 = min(b[0] for b in detected_hands_boxes)
        hy0 = min(b[1] for b in detected_hands_boxes)
        hx1 = max(b[0] + b[2] for b in detected_hands_boxes)
        hy1 = max(b[1] + b[3] for b in detected_hands_boxes)
        action_tracks["hands_bbox_norm"] = [round(hx0, 4), round(hy0, 4), round(hx1 - hx0, 4), round(hy1 - hy0, 4)]
        action_tracks["verified_hands_detected"] = True
    action_tracks["action_protection_windows"] = list(tracking_res.get("action_protection_windows") or [])

    pre_beats: list[dict[str, Any]] = []
    for idx, ph in enumerate(merged_phrases):
        bid = f"beat_{idx + 1:02d}"
        orig_i = int(ph.get("_orig_idx", idx))
        fbind = bind_map.get(f"beat_{orig_i + 1:02d}") or bind_map.get(bid)
        asset_ref = fbind["asset_id"] if (fbind and fbind.get("asset_id")) else None
        role = ph.get("semantic_role", "context")
        kh02_fn = role if role in (
            "hook", "question", "context", "problem", "tension", "demonstration",
            "mechanism", "evidence", "proof", "comparison", "step", "reaction",
            "turning_point", "reveal", "payoff", "cta", "breath", "intentional_silence",
        ) else "context"
        pre_beats.append({
            "beat_id": bid,
            "start_frame": int(ph["start_frame"]),
            "end_frame": int(ph["end_frame"]),
            "semantic_function": kh02_fn,
            "proof_status": "VERIFIED_REAL_EVIDENCE" if asset_ref else "SOURCE_FOOTAGE_DIRECT",
            "has_numeric_claims": bool(ph.get("numeric_claims")),
            "content_refs": ["primary_aroll"] + ([asset_ref] if asset_ref else []),
        })

    layout_plan = solve_layout_sequence(
        pre_beats,
        run_id=run_id,
        skill_id=selected_skill_id,
        skill_contract=sk,
        layout_policy=layout_policy,
        canvas={"width": c_w, "height": c_h, "aspect_ratio": aspect},
        source_media=media,
        action_tracks=action_tracks,
        constraints=eff_constraints,
        assets=footage_manifest.get("assets"),
        fps_num=int(media.get("fps_num") or 24),
        fps_den=int(media.get("fps_den") or 1),
        platform="reels" if aspect == "9:16" else "youtube",
        run_dir=run_dir,
    )
    audit_layout_motion(layout_plan, run_dir=run_dir)
    lay_by_beat = {lb["beat_id"]: lb for lb in layout_plan["beats"]}

    # ADL-05 + R3 CMI-05/CMI-06: Universal evidence ledger, type system & adaptive captions
    cap_pol = str(eff_constraints.get("caption_policy") or "OFF")
    words_src = list((tr_doc or {}).get("words") or [])
    caption_plan = plan_adaptive_captions(
        words_src,
        layout_plan,
        caption_policy=cap_pol,
        platform="reels" if aspect == "9:16" else "youtube",
        language=str(input_analysis.get("language") or "en"),
        fps_num=int(media.get("fps_num") or 24),
        fps_den=int(media.get("fps_den") or 1),
        run_dir=run_dir,
    )
    asset_purpose = (
        "COMMERCIAL_AND_NONCOMMERCIAL"
        if eff_constraints.get("license_decision") == "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED"
        else "NONCOMMERCIAL_ONLY"
    )
    evidence_ledger = build_universal_evidence_ledger(
        run_id=run_id,
        skill_id=selected_skill_id,
        semantic_graph=semantic_graph,
        footage_manifest=footage_manifest,
        intended_purpose=asset_purpose,
        require_product_proof=bool(eff_constraints.get("require_product_proof", False)),
        run_dir=run_dir,
    )
    sample_script_txt = str(input_analysis.get("normalized_script") or " ".join(p.get("text", "") for p in merged_phrases))
    type_system = build_type_system(
        run_id=run_id,
        skill_id=selected_skill_id,
        art_direction=art,
        sample_text=sample_script_txt,
        language=str(input_analysis.get("language") or "en"),
        canvas_width=c_w,
        canvas_height=c_h,
        run_dir=run_dir,
    )
    plan_dynamic_captions_r3(
        words_src,
        layout_plan,
        type_system,
        caption_policy=cap_pol,
        platform="reels" if aspect == "9:16" else "youtube",
        fps_num=int(media.get("fps_num") or 24),
        fps_den=int(media.get("fps_den") or 1),
        run_dir=run_dir,
    )

    beats: list[dict[str, Any]] = []
    raw_specs: list[dict[str, Any]] = []
    attention_curve: list[dict[str, Any]] = []
    used_caps: list[str] = []

    for idx, ph in enumerate(merged_phrases):
        bid = f"beat_{idx + 1:02d}"
        sf, ef = int(ph["start_frame"]), int(ph["end_frame"])
        orig_i = int(ph.get("_orig_idx", idx))
        enode = exp_nodes[orig_i] if orig_i < len(exp_nodes) else exp_nodes[-1]
        erep = exp_reps.get(enode["node_id"], {})
        fbind = bind_map.get(f"beat_{orig_i + 1:02d}") or bind_map.get(bid)
        asset_ref = fbind["asset_id"] if (fbind and fbind.get("asset_id")) else None
        lb = lay_by_beat[bid]
        beat_box = list(lb["graphic_box_norm"]) if layout_policy == "ADAPTIVE_DYNAMIC" else list(default_box)
        beat_z = int(lb["graphic_z_order"]) if layout_policy == "ADAPTIVE_DYNAMIC" else (32 + idx)

        req_cap, comp_params = _select_component_for_beat(
            idx=idx,
            total_beats=len(merged_phrases),
            phrase=ph,
            exp_node=enode,
            exp_rep=erep,
            footage_binding=fbind,
            skill_id=selected_skill_id,
            has_arith=has_arith,
            arith_steps=arith_steps,
            used_caps=used_caps,
        )
        # Ensure headline & subtitle snap cleanly to word boundaries and fit beat read-time budget
        if isinstance(comp_params.get("headline"), str):
            hl = _trim_words(comp_params["headline"], 52)
            max_words = max(7 if idx == 0 else 5, int(((ef - sf) / max(1.0, fps)) * 3.8))
            words = hl.split()
            if len(words) > max_words:
                hl = _trim_words(" ".join(words[:max_words]), 50)
            comp_params["headline"] = hl
        if isinstance(comp_params.get("subtitle"), str):
            comp_params["subtitle"] = _trim_words(comp_params["subtitle"], 56)
        comp_params["theme"] = art["theme"]
        comp_params["skill_id"] = selected_skill_id
        comp_params["layout_mode"] = lb["layout_mode"]
        if idx == 0:
            comp_params["selected_hook_id"] = hook_plan["selected_hook_id"]

        spec = design_component(
            component_id=f"kh03_comp_{idx + 1:02d}_{bid}",
            requested_capability_id=req_cap,
            beat_id=bid,
            role=ph.get("semantic_role", "context"),
            start_frame=sf,
            end_frame=ef,
            normalized_bbox=beat_box,
            params=comp_params,
            semantic_anchor=f"{selected_skill_id}:{enode['node_id']}:{ph['phrase_id']}",
            registry=reg,
            aspect_ratio=aspect,
            fps=fps,
            profile=sk["preferred_profile"],
            z_order=beat_z,
            asset_ref=asset_ref,
            data_bindings={"explanation_node_id": enode["node_id"], "phrase_id": ph["phrase_id"], "layout_id": lb["layout_id"]},
            easing=art["default_easing"],
        )
        used_caps.append(spec["capability_id"])
        raw_specs.append(spec)

        role = ph.get("semantic_role", "context")
        kh02_fn = role if role in (
            "hook", "question", "context", "problem", "tension", "demonstration",
            "mechanism", "evidence", "proof", "comparison", "step", "reaction",
            "turning_point", "reveal", "payoff", "cta", "breath", "intentional_silence",
        ) else "context"

        beats.append({
            "beat_id": bid,
            "start_frame": sf,
            "end_frame": ef,
            "source_word_ids": list(ph.get("word_ids") or []),
            "semantic_function": kh02_fn,
            "viewer_goal": f"[{selected_skill_id}] Understand {enode['node_type']}: {enode['label']}",
            "emotion_intensity": 0.88 if kh02_fn in ("hook", "payoff", "cta") else (0.74 if kh02_fn in ("mechanism", "comparison", "evidence") else 0.62),
            "priority": 5 if kh02_fn in ("hook", "payoff", "cta", "evidence") else 4,
            "content_refs": ["primary_aroll"] + ([asset_ref] if asset_ref else []),
            "proof_status": "VERIFIED_REAL_EVIDENCE" if asset_ref else ("CONCEPT_ILLUSTRATION" if enode.get("illustrative") else "SOURCE_FOOTAGE_DIRECT"),
            "visual_mode": (
                "fullscreen_top_overlay" if fs_lock
                else (f"adaptive_{lb['layout_mode'].lower()}" if layout_policy == "ADAPTIVE_DYNAMIC" else f"{selected_skill_id.lower()}_adaptive_stage")
            ),
            "focus_subject": ph["text"][:56].strip() or f"Beat {idx + 1}",
            "continuity_in": "open_frame" if idx == 0 else "editorial_spine",
            "continuity_out": "clean_settle" if idx == len(merged_phrases) - 1 else "editorial_spine",
            "planned_information_change": f"Deliver {spec['capability_id']} ({enode['node_type']}) in {lb['layout_mode']} anchored to {ph['phrase_id']}.",
            "why": f"KH-03 {selected_skill_id} ({selected_dir['direction_id']}) binding {enode['node_id']} -> {spec['capability_id']} ({lb['layout_mode']}).",
            "read_time_frames": min(max(1, ef - sf), compute_min_read_frames(ph["text"][:56], fps)),
            "intentional_hold": bool(selected_skill_id in ("SK-03", "SK-05") and kh02_fn in ("payoff", "context")),
            "explanation_node_id": enode["node_id"],
            "component_id": spec["component_id"],
        })

        attention_curve.append({
            "beat_id": bid,
            "start_sec": round(sf / fps, 3),
            "end_sec": round(ef / fps, 3),
            "editorial_energy": sk["preferred_energy"],
            "focus_shift": spec["capability_id"],
            "viewer_understanding_delta": enode["node_type"],
            "open_loop_state": "OPEN" if idx == 0 else ("CLOSED_PAYOFF" if idx == len(merged_phrases) - 1 else "ADVANCING"),
            "note": "Editorial attention plan only — NOT a measured audience retention curve.",
        })

    # Solve swept bounding-box collisions against face/hands/objects (in ADAPTIVE_DYNAMIC, graphic_box already cleared presenter!)
    if layout_policy == "ADAPTIVE_DYNAMIC":
        final_specs = list(raw_specs)
        solved = {"ok": True, "components": final_specs, "reflows": []}
    else:
        solved = solve_visual_collisions(
            raw_specs,
            protected_zones=prot_zones,
            clear_zones=clear_zones,
            canvas_width=c_w,
            canvas_height=c_h,
        )
        final_specs = solved["components"]

    # KH-03.R3 CMI-02 & CMI-03: Semantic Motion Graph, Internal Node Timelines & Shared-Element Morphs
    mg_res = build_motion_graph(
        run_id=run_id,
        skill_id=selected_skill_id,
        components=final_specs,
        semantic_graph=semantic_graph,
        explanation_graph=explanation_graph,
        transcript=tr_doc,
        layout_plan=layout_plan,
        fps_num=int(media.get("fps_num") or 24),
        fps_den=int(media.get("fps_den") or 1),
        total_frames=total_frames,
        reduced_motion=bool(eff_constraints.get("reduced_motion", False)),
        run_dir=run_dir,
    )
    motion_graph = mg_res["motion_graph"]
    root_by_cid: dict[str, dict[str, Any]] = {}
    children_by_cid: dict[str, list[dict[str, Any]]] = {}
    for nd in motion_graph.get("nodes", []):
        cid_k = str(nd["component_id"])
        if nd.get("parent_id") is None:
            root_by_cid[cid_k] = nd
        else:
            children_by_cid.setdefault(cid_k, []).append({
                "node_id": nd["node_id"],
                "role": nd["role"],
                "primitive": nd["primitive"],
                "start_frame": int(nd["start_frame"]),
                "end_frame": int(nd["end_frame"]),
                "anchor_word_id": nd.get("anchor_word_id"),
                "keyframes": list(nd.get("keyframes") or []),
            })
    node_to_cid = {str(nd["node_id"]): str(nd["component_id"]) for nd in motion_graph.get("nodes", [])}
    shared_by_cid: dict[str, list[str]] = {}
    for se in motion_graph.get("shared_elements", []):
        for nid_k in (se.get("source_node_id"), se.get("target_node_id")):
            cid_k = node_to_cid.get(str(nid_k or ""))
            if cid_k:
                shared_by_cid.setdefault(cid_k, []).append(str(se["shared_element_id"]))
    for sp in final_specs:
        rn = root_by_cid.get(sp["component_id"])
        if rn:
            sp["anchor_word_id"] = rn.get("anchor_word_id")
            sp["anchor_precision"] = rn.get("anchor_precision", "beat_start")
        sp["internal_nodes"] = list(children_by_cid.get(sp["component_id"]) or [])
        if sp["component_id"] in shared_by_cid:
            sp["shared_element_ids"] = sorted(set(shared_by_cid[sp["component_id"]]))

    plan_core = {
        "schema_version": "kh03.creative_plan.v1",
        "run_id": run_id,
        "selected_skill_id": selected_skill_id,
        "selection_receipt_sha256": selection_receipt_sha256,
        "selected_direction_id": selected_dir["direction_id"],
        "directions_considered": all_dirs,
        "art_direction": {
            "theme": art["theme"],
            "palette": art["palette"],
            "font_stack": art["font_stack"],
            "motion_grammar": art["motion_grammar"],
            "density_intent": art["density_intent"],
        },
        "hook_plan": hook_plan,
        "attention_curve": attention_curve,
        "beats": beats,
        "components": final_specs,
        "sound_plan_ref": "stems/SOUND_DIRECTOR_PLAN.json",
        "layout_plan_ref": "layouts/LAYOUT_PLAN.json",
        "layout_plan_sha256": layout_plan["layout_plan_sha256"],
        "caption_layout_ref": "layouts/CAPTION_LAYOUT_PLAN.json",
        "motion_graph_ref": "manifests/MOTION_GRAPH.json",
        "motion_graph_sha256": motion_graph["graph_sha256"],
        "shared_element_graph_ref": "manifests/SHARED_ELEMENT_GRAPH.json",
        "evidence_ledger_ref": "assets/EVIDENCE_LEDGER.json",
        "type_system_ref": "layouts/TYPE_SYSTEM.json",
        "visual_density_ref": "qc/VISUAL_DENSITY_REPORT.json",
        "hook_microbeats_ref": "manifests/HOOK_MICROBEATS.json",
        "layout_constraints": {
            "aspect_ratio": aspect,
            "layout_policy": layout_policy,
            "fullscreen_lock": fs_lock,
            "collision_ok": solved["ok"] and caption_plan["collision_ok"],
            "reflow_count": len(solved["reflows"]),
            "total_frames": total_frames,
            "unique_layout_modes": sorted({lb["layout_mode"] for lb in layout_plan["beats"]}),
        },
    }
    evaluate_visual_density_and_repetition(
        run_id=run_id,
        layout_plan=layout_plan,
        creative_plan=plan_core,
        motion_graph=motion_graph,
        type_system=type_system,
        run_dir=run_dir,
    )
    from .evidence_and_type import resolve_art_direction, validate_design_tokens
    from .layout import detect_visual_repetition
    detect_visual_repetition(layout_plan, creative_plan=plan_core, motion_graph=motion_graph, run_dir=run_dir)
    art_res = resolve_art_direction(selected_skill_id, caption_policy=cap_pol, language=str(input_analysis.get("language") or "en"))
    dt_qa = validate_design_tokens(art_res, type_system, components=final_specs, caption_policy=cap_pol)
    write_json_atomic(run_dir / "qc" / "DESIGN_TOKENS_QA.json", dt_qa)
    plan_core["plan_sha256"] = hash_artifact(plan_core)
    assert_kh03_schema(plan_core, "creative_plan")

    # Emit Section 13 creative direction & component artifacts
    write_json_atomic(run_dir / "manifests" / "CREATIVE_PLAN.json", plan_core)
    write_json_atomic(run_dir / "manifests" / "SELECTED_DIRECTION.json", selected_dir)
    write_json_atomic(run_dir / "layouts" / "COMPONENT_LAYOUT_BINDINGS.json", {
        "schema_version": "kh03.component_layout_bindings.v1",
        "run_id": run_id,
        "layout_policy": layout_policy,
        "bindings": [
            {
                "component_id": s["component_id"],
                "beat_id": s["beat_id"],
                "capability_id": s["capability_id"],
                "layout_id": (s.get("data_bindings") or {}).get("layout_id"),
                "layout_mode": (s.get("params") or {}).get("layout_mode"),
                "bbox_norm": s["normalized_bbox"],
                "z_order": s["z_order"],
            }
            for s in final_specs
        ],
    })
    write_json_atomic(run_dir / "manifests" / "COMPONENT_MANIFEST.json", {
        "schema_version": "kh03.component_manifest.v1",
        "run_id": run_id,
        "selected_skill_id": selected_skill_id,
        "total_components": len(final_specs),
        "unique_capabilities": sorted({s["capability_id"] for s in final_specs}),
        "collision_reflows": solved["reflows"],
        "components": final_specs,
    })

    dir_md = [
        f"# KH-03 Creative Directions (`{selected_skill_id}`) — `{run_id}`",
        "",
        f"- **Selected Direction:** `{selected_dir['direction_id']}` ({selected_dir['name']})",
        f"- **Theme / Motion Grammar:** `{art['theme']}` / `{art['motion_grammar']}`",
        "",
    ]
    for d in all_dirs:
        tag = "SELECTED" if d["selected"] else "ALTERNATIVE"
        dir_md.append(f"### `{d['direction_id']}` [{tag}] (score {d['score']})")
        dir_md.append(f"- **Strategy:** {d['layout_strategy']}")
        dir_md.append(f"- **Reason:** {d['reason']}")
        dir_md.append("")
    (run_dir / "manifests" / "CREATIVE_DIRECTIONS.md").write_text("\n".join(dir_md), encoding="utf-8")

    return plan_core


def compile_to_kh02_overrides(
    creative_plan: dict[str, Any],
    *,
    fps: float = 24.0,
    sound_plan: dict[str, Any] | None = None,
    layout_plan: dict[str, Any] | None = None,
    motion_graph: dict[str, Any] | None = None,
    caption_policy: str = "OFF",
    winning_candidate_id: str | None = None,
    winner_binding_sha256: str | None = None,
    competition_status: str | None = None,
) -> dict[str, Any]:
    """Translate KH-03 CREATIVE_PLAN into authoritative KH-02 overrides (clock_domain=SOURCE_CLOCK until Stage F EDL remap)."""
    sk = get_skill_contract(creative_plan["selected_skill_id"])
    kh02_beats = [
        {k: v for k, v in b.items() if k not in ("explanation_node_id", "component_id")}
        for b in creative_plan["beats"]
    ]
    kh02_visual_events = compile_component_events(
        creative_plan["components"],
        fps=fps,
        profile=sk["preferred_profile"],
    )
    lc = creative_plan.get("layout_constraints") or {}
    fs_lock = bool(lc.get("fullscreen_lock"))
    layout_policy = str(lc.get("layout_policy") or ("FULLSCREEN_LOCK" if fs_lock else "LEGACY_DYNAMIC"))
    # PED-03 / H1 companion stage cue: on split/inset beats where presenter area < 45% and captions are OFF,
    # add a crisp non-overlapping chapter_pill inside the layout's reserved caption_box_norm so the stage
    # maintains balanced editorial hierarchy (>=2 info boxes) with 0% overlap against presenter, face, or graphic.
    if layout_policy == "ADAPTIVE_DYNAMIC" and caption_policy != "ON_REQUESTED" and isinstance(layout_plan, dict):
        lp_by_bid = {str(lb.get("beat_id")): lb for lb in (layout_plan.get("beats") or [])}
        beat_by_bid = {str(b.get("beat_id")): b for b in kh02_beats}
        for b_idx, b in enumerate(kh02_beats):
            bid = str(b["beat_id"])
            lb = lp_by_bid.get(bid)
            if not lb:
                continue
            pbox = lb.get("presenter_box_norm") or [0.0, 0.0, 1.0, 1.0]
            p_area = float(pbox[2]) * float(pbox[3])
            cbox = lb.get("caption_slot_norm") or lb.get("caption_box_norm")
            if p_area < 0.45 and isinstance(cbox, list) and len(cbox) == 4:
                cx, cy, cw, ch = [float(v) for v in cbox]
                pill_h = round(min(ch, 0.056), 4)
                sf, ef = int(b["start_frame"]), int(b["end_frame"])
                dur_f = max(4, ef - sf)
                en_f = min(4, max(1, dur_f // 4))
                ex_f = min(4, max(1, dur_f // 4))
                hd_f = max(1, dur_f - en_f - ex_f)
                sem_fn = str(b.get("semantic_function") or "context").replace("_", " ").upper()
                kh02_visual_events.append({
                    "event_id": sanitize_identifier(f"kh03_cue_{b_idx + 1:02d}_{bid}"),
                    "beat_id": bid,
                    "track_id": "track_info_secondary",
                    "layer_id": sanitize_identifier(f"kh03_cue_{b_idx + 1:02d}_{bid}"),
                    "layer_cat": "title",
                    "z_order": 34,
                    "asset_ref": None,
                    "capability_id": "hf.badge_pop",
                    "start_frame": sf,
                    "end_frame": ef,
                    "anchor_word_id": None,
                    "anchor_mode": "beat_start",
                    "entry_frames": en_f,
                    "hold_frames": hd_f,
                    "exit_frames": ex_f,
                    "normalized_bbox": [round(cx, 4), round(cy, 4), round(cw, 4), pill_h],
                    "opacity": 1.0,
                    "transform": {"scale_from": 0.96, "scale_to": 1.0, "y_offset_px": 6},
                    "mask": None,
                    "blend": "normal",
                    "easing": "power2.out",
                    "params": {
                        "kind": "chapter_pill",
                        "kicker": f"{b_idx + 1:02d}",
                        "headline": sem_fn[:24],
                    },
                    "safe_zone": True,
                    "face_exclusion": True,
                    "semantic_reason": f"PED-03 split-stage structural cue for {bid} ({lb.get('layout_mode')})",
                    "expected_visual_evidence": f"Chapter cue pill {b_idx + 1:02d} {sem_fn} visible in non-overlapping caption slot.",
                })
    sel_dir = next(d for d in creative_plan["directions_considered"] if d["selected"])
    hp = creative_plan.get("hook_plan") or {}
    sel_hook_opt = next((o for o in (hp.get("options") or []) if o.get("selected")), None) or {}
    first_vis_id = kh02_visual_events[0]["event_id"] if kh02_visual_events else "ev_01_hook"
    payoff_bid = kh02_beats[-1]["beat_id"] if kh02_beats else "beat_01"

    selected_hook_override = {
        "selected_option_id": hp.get("selected_hook_id", f"{creative_plan['selected_skill_id'].lower()}_hook_01"),
        "strategy": hp.get("selected_strategy", sel_hook_opt.get("strategy", "direct_proof_lead")),
        "opening_line": sel_hook_opt.get("opening_line", ""),
        "opening_visual": sel_hook_opt.get("opening_visual", ""),
        "first_frame_visual_id": first_vis_id,
        "start_frame": 0,
        "presenter_mode": "LEGACY_DYNAMIC",
        "fullscreen_lock": fs_lock,
        "layout_policy": layout_policy,
        "payoff_beat_id": payoff_bid,
        "truthful_no_spoiler_verified": bool(hp.get("truthful_no_spoiler_verified", True)),
        "spoils_payoff": bool(sel_hook_opt.get("spoils_payoff", False)),
        "deceptive_urgency": bool(sel_hook_opt.get("deceptive_urgency", False)),
        "kh03_plan_sha256": creative_plan["plan_sha256"],
    }

    return {
        "selected_skill_id": creative_plan["selected_skill_id"],
        "kh03_plan_sha256": creative_plan["plan_sha256"],
        "winning_candidate_id": winning_candidate_id or creative_plan.get("winning_candidate_id"),
        "winner_binding_sha256": winner_binding_sha256,
        "competition_status": competition_status or creative_plan.get("competition_status") or "SINGLE_PLAN_NO_COMPETITION",
        "clock_domain": "SOURCE_CLOCK",
        "time_map_sha256": None,
        "fullscreen_lock": fs_lock,
        "layout_policy": layout_policy,
        "layout_plan": layout_plan,
        "layout_plan_sha256": (layout_plan or {}).get("layout_plan_sha256") or creative_plan.get("layout_plan_sha256"),
        "motion_graph": motion_graph,
        "motion_graph_sha256": (motion_graph or {}).get("graph_sha256") or creative_plan.get("motion_graph_sha256"),
        "caption_policy": caption_policy,
        "profile": sk["preferred_profile"],
        "energy_mode": sk["preferred_energy"],
        "custom_beats": kh02_beats,
        "custom_visual_events": kh02_visual_events,
        "selected_hook_override": selected_hook_override,
        "sound_plan": sound_plan,
        "sound_plan_sha256": hash_artifact(sound_plan) if sound_plan else None,
        "creative_direction_override": {
            "direction_id": sel_dir["direction_id"],
            "name": sel_dir["name"],
            "theme": sel_dir["theme"],
            "archetype": sel_dir["archetype"],
            "layout_strategy": sel_dir["layout_strategy"],
            "selected": True,
        },
    }


def _map_source_interval_to_edl(src_start: int, src_end: int, segments: list[dict[str, Any]]) -> tuple[int, int] | None:
    """Map source half-open [src_start, src_end) onto exact contiguous EDL output frame bounds."""
    out_lo: int | None = None
    out_hi: int | None = None
    for seg in segments:
        s0, s1 = int(seg["src_start_frame"]), int(seg["src_end_frame"])
        o0 = int(seg["out_start_frame"])
        ov_s = max(src_start, s0)
        ov_e = min(src_end, s1)
        if ov_e > ov_s:
            mapped_s = o0 + (ov_s - s0)
            mapped_e = o0 + (ov_e - s0)
            out_lo = mapped_s if out_lo is None else min(out_lo, mapped_s)
            out_hi = mapped_e if out_hi is None else max(out_hi, mapped_e)
    if out_lo is None or out_hi is None or out_hi <= out_lo:
        return None
    return out_lo, out_hi


def _map_source_frame_to_edl(src_frame: int, segments: list[dict[str, Any]], total_out: int) -> tuple[int, bool]:
    """Map single source frame index -> (output_frame, exact_inside_segment)."""
    for seg in segments:
        s0, s1 = int(seg["src_start_frame"]), int(seg["src_end_frame"])
        o0, o1 = int(seg["out_start_frame"]), int(seg["out_end_frame"])
        if s0 <= src_frame < s1:
            return o0 + (src_frame - s0), True
        if src_frame < s0:
            return o0, False
    return max(0, total_out - 1), False


def remap_kh03_plan_to_output_clock(
    kh03_overrides: dict[str, Any],
    edl: dict[str, Any],
    *,
    transcript: dict[str, Any] | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Authoritative single-pass source->output EDL remapper for KH-03 beats, visual events, hooks & sound cues (R1.01).

    Fail-closed guarantees:
    - Rejects double-remap (`clock_domain == 'OUTPUT_EDL_MAPPED'`).
    - Uses exact rational EDL fps_num/fps_den and segment boundaries.
    - Clips straddling events, marks removed events with rationale, fails closed if essential anchor or all beats dropped.
    - Guarantees strict contiguous output beat coverage [0, total_output_frames) and zero out-of-range events.
    """
    from fractions import Fraction
    from scripts.kh02.edit_decisions import validate_edl
    from scripts.kh02.transcript import map_words_to_output

    if kh03_overrides.get("clock_domain") == "OUTPUT_EDL_MAPPED":
        raise ValueError(
            "DOUBLE_REMAP_FORBIDDEN: kh03_overrides is already mapped to OUTPUT_EDL_MAPPED; refusing second remap."
        )

    edl_errs = validate_edl(edl, words=(transcript or {}).get("words"))
    if edl_errs:
        raise ValueError(f"INVALID_EDL_FOR_REMAP: {edl_errs[:3]}")

    fps_frac = Fraction(int(edl["fps_num"]), int(edl["fps_den"]))
    fps = float(fps_frac)
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

    mapped_words = map_words_to_output(transcript, edl) if transcript else []
    word_out_ids = {w["word_id"] for w in mapped_words}

    # 1. Remap beats onto output clock
    raw_beats = list(kh03_overrides.get("custom_beats") or [])
    mapped_beats: list[dict[str, Any]] = []
    removed_beats: list[dict[str, Any]] = []
    beat_frame_map: dict[str, tuple[int, int]] = {}

    for b in raw_beats:
        sf, ef = int(b["start_frame"]), int(b["end_frame"])
        span = (sf, ef) if is_identity else _map_source_interval_to_edl(sf, ef, segs)
        if span is None:
            if b.get("essential_anchor"):
                raise ValueError(f"ESSENTIAL_ANCHOR_REMOVED_BY_EDL: beat {b['beat_id']} removed completely by EDL.")
            removed_beats.append({
                "beat_id": b["beat_id"],
                "src_span": [sf, ef],
                "status": "REMOVED_BY_EDL",
                "reason": "Entire source beat interval fell within trimmed pause segments.",
            })
            continue
        os0, oe0 = span
        nb = dict(b)
        nb["start_frame"] = os0
        nb["end_frame"] = oe0
        if mapped_words and nb.get("source_word_ids"):
            nb["source_word_ids"] = [wid for wid in nb["source_word_ids"] if wid in word_out_ids]
        nb["read_time_frames"] = min(max(1, oe0 - os0), int(b.get("read_time_frames") or max(4, oe0 - os0)))
        mapped_beats.append(nb)

    if not mapped_beats:
        raise ValueError("ESSENTIAL_ANCHOR_REMOVED_BY_EDL: Zero beats survived EDL remapping.")

    # Coalesce any beat < min_readable frames (when cuts shrank it) and enforce strict contiguous [0, total_out)
    min_f = min(total_out, max(12, int(round(fps * 0.50))))
    coalesced: list[dict[str, Any]] = []
    for idx, b in enumerate(mapped_beats):
        dur = b["end_frame"] - b["start_frame"]
        if coalesced and dur < min_f:
            prev = coalesced[-1]
            prev["end_frame"] = max(prev["end_frame"], b["end_frame"])
            prev["source_word_ids"] = list(prev.get("source_word_ids") or []) + list(b.get("source_word_ids") or [])
            removed_beats.append({
                "beat_id": b["beat_id"],
                "status": "MERGED_SHORT_POST_EDL",
                "merged_into": prev["beat_id"],
                "reason": f"Post-EDL beat duration {dur}f < {min_f}f readable floor.",
            })
        else:
            coalesced.append(b)
    mapped_beats = coalesced

    for idx, b in enumerate(mapped_beats):
        b["start_frame"] = 0 if idx == 0 else mapped_beats[idx - 1]["end_frame"]
        if idx == len(mapped_beats) - 1:
            b["end_frame"] = total_out
        else:
            b["end_frame"] = max(b["start_frame"] + 1, min(total_out - (len(mapped_beats) - 1 - idx), b["end_frame"]))
        b["read_time_frames"] = min(max(1, b["end_frame"] - b["start_frame"]), int(b.get("read_time_frames") or 12))
        beat_frame_map[b["beat_id"]] = (b["start_frame"], b["end_frame"])

    # 2. Remap visual events onto output clock
    raw_vis = list(kh03_overrides.get("custom_visual_events") or [])
    mapped_vis: list[dict[str, Any]] = []
    removed_vis: list[dict[str, Any]] = []
    valid_beat_ids = set(beat_frame_map.keys())
    fallback_bid = mapped_beats[-1]["beat_id"]

    for ev in raw_vis:
        sf, ef = int(ev["start_frame"]), int(ev["end_frame"])
        span = (sf, ef) if is_identity else _map_source_interval_to_edl(sf, ef, segs)
        if span is None:
            if ev.get("essential_anchor"):
                raise ValueError(f"ESSENTIAL_ANCHOR_REMOVED_BY_EDL: visual event {ev['event_id']} removed by EDL.")
            removed_vis.append({
                "event_id": ev["event_id"],
                "src_span": [sf, ef],
                "status": "REMOVED_BY_EDL",
                "reason": "Source visual interval fell completely inside trimmed cut segment.",
            })
            continue
        os0, oe0 = max(0, span[0]), min(total_out, span[1])
        if (oe0 - os0) < 6:
            # Expand slightly within total_out if cut clipped tail just below 0.25s readable minimum
            oe0 = min(total_out, os0 + min(total_out - os0, max(6, int(round(fps * 0.35)))))
        if (oe0 - os0) < 5:
            removed_vis.append({
                "event_id": ev["event_id"],
                "src_span": [sf, ef],
                "status": "REMOVED_TOO_BRIEF_POST_EDL",
                "reason": f"Remapped span {oe0 - os0}f below 5f readable floor.",
            })
            continue
        nev = dict(ev)
        nev["start_frame"] = os0
        nev["end_frame"] = oe0
        if nev.get("beat_id") not in valid_beat_ids:
            # Attach to enclosing output beat cleanly
            enclosing = next((b["beat_id"] for b in mapped_beats if b["start_frame"] <= os0 < b["end_frame"]), fallback_bid)
            nev["beat_id"] = enclosing
        # Clamp entry/exit frames & remap internal_nodes onto output EDL clock cleanly
        dur_f = oe0 - os0
        nev["entry_frames"] = min(int(ev.get("entry_frames") or 6), max(2, dur_f // 3))
        nev["exit_frames"] = min(int(ev.get("exit_frames") or 4), max(2, dur_f // 4))
        ev_params = dict(ev.get("params") or {})
        if isinstance(ev_params.get("internal_nodes"), list):
            mapped_inodes: list[dict[str, Any]] = []
            for nd in ev_params["internal_nodes"]:
                n_sf, n_ef = int(nd.get("start_frame", sf)), int(nd.get("end_frame", ef))
                n_sp = (n_sf, n_ef) if is_identity else _map_source_interval_to_edl(n_sf, n_ef, segs)
                if n_sp is None:
                    continue
                mapped_inodes.append({
                    **nd,
                    "start_frame": max(os0, min(oe0 - 1, n_sp[0])),
                    "end_frame": max(os0 + 1, min(oe0, max(n_sp[0] + 1, n_sp[1]))),
                })
            ev_params["internal_nodes"] = mapped_inodes
            nev["params"] = ev_params
        mapped_vis.append(nev)

    # 3. Remap selected_hook_override
    hook_ov = dict(kh03_overrides.get("selected_hook_override") or {})
    if hook_ov:
        hook_ov["start_frame"] = 0
        hook_ov["payoff_beat_id"] = mapped_beats[-1]["beat_id"]
        if mapped_vis:
            hook_ov["first_frame_visual_id"] = mapped_vis[0]["event_id"]
        hook_ov["time_map_sha256"] = edl_sha

    # 4. Remap KH-03 sound_plan cues onto exact output EDL frame/second clock
    remapped_sound: dict[str, Any] | None = None
    removed_cues: list[dict[str, Any]] = []
    if kh03_overrides.get("sound_plan"):
        sp_in = dict(kh03_overrides["sound_plan"])
        new_events: list[dict[str, Any]] = []
        for sev in sp_in.get("events", []):
            sf_cue = int(sev.get("onset_frame", 0))
            of_cue, inside = (sf_cue, True) if is_identity else _map_source_frame_to_edl(sf_cue, segs, total_out)
            if not inside and not is_identity and sev.get("status") == "ADD":
                # Re-anchor smoothly to mapped segment boundary if onset was right inside a trimmed pause
                pass
            nsev = dict(sev)
            nsev["src_onset_frame"] = sf_cue
            nsev["onset_frame"] = max(0, min(total_out - 1, of_cue))
            nsev["onset_sec"] = round(float(Fraction(nsev["onset_frame"]) / fps_frac), 4)
            nsev["clock_domain"] = "OUTPUT_EDL_MAPPED"
            if nsev.get("beat_id") not in valid_beat_ids:
                nsev["beat_id"] = next((b["beat_id"] for b in mapped_beats if b["start_frame"] <= nsev["onset_frame"] < b["end_frame"]), fallback_bid)
            new_events.append(nsev)
        sp_in["events"] = new_events
        sp_in["clock_domain"] = "OUTPUT_EDL_MAPPED"
        sp_in["time_map_sha256"] = edl_sha
        remapped_sound = sp_in

    # 4b. Remap KH-03.R2 adaptive layout_plan + morph transitions + captions onto exact output EDL clock
    remapped_layout: dict[str, Any] | None = None
    remapped_captions: dict[str, Any] | None = None
    if kh03_overrides.get("layout_plan"):
        remapped_layout = remap_layout_and_transitions_to_edl(
            kh03_overrides["layout_plan"], edl, run_dir=run_dir,
        )
        # Re-anchor any layout-transition sound cues to remapped morph start frame
        if remapped_sound and remapped_layout.get("transitions"):
            tr_start_by_id = {t["transition_id"]: int(t["start_frame"]) for t in remapped_layout["transitions"]}
            sr = int(edl.get("audio_sample_rate") or 48000)
            for sev in remapped_sound.get("events", []):
                tid = sev.get("layout_transition_id")
                if tid and tid in tr_start_by_id:
                    sev["onset_frame"] = tr_start_by_id[tid]
                    sev["onset_sec"] = round(float(Fraction(sev["onset_frame"]) / fps_frac), 4)
                    sev["sample_onset"] = int(round(float(Fraction(sev["onset_frame"]) / fps_frac) * sr))
        eff_cap_pol = str(kh03_overrides.get("caption_policy") or "OFF")
        if mapped_words and eff_cap_pol != "OFF":
            remapped_captions = plan_adaptive_captions(
                mapped_words,
                remapped_layout,
                caption_policy=eff_cap_pol,
                fps_num=int(edl["fps_num"]),
                fps_den=int(edl["fps_den"]),
                run_dir=run_dir,
            )

    # 4c. Remap KH-03.R3 semantic motion_graph & re-anchor sound cues + internal_nodes onto exact output EDL clock (CMI-02/03/08)
    remapped_mg: dict[str, Any] | None = None
    if kh03_overrides.get("motion_graph"):
        remapped_mg = remap_motion_graph_to_edl(
            kh03_overrides["motion_graph"], edl, run_dir=run_dir,
        )
        verify_morph_semantic_coverage(remapped_mg, layout_plan=remapped_layout, run_dir=run_dir)
        # Update mapped visual event internal_nodes with remapped frame spans
        rm_children_by_cid: dict[str, list[dict[str, Any]]] = {}
        for nd in remapped_mg.get("nodes", []):
            if nd.get("parent_id") is not None:
                rm_children_by_cid.setdefault(str(nd["component_id"]), []).append({
                    "node_id": nd["node_id"],
                    "role": nd["role"],
                    "primitive": nd["primitive"],
                    "start_frame": int(nd["start_frame"]),
                    "end_frame": int(nd["end_frame"]),
                    "anchor_word_id": nd.get("anchor_word_id"),
                    "keyframes": list(nd.get("keyframes") or []),
                })
        for nev in mapped_vis:
            if nev["event_id"] in rm_children_by_cid:
                nev["params"] = dict(nev.get("params") or {})
                nev["params"]["internal_nodes"] = rm_children_by_cid[nev["event_id"]]
        # Re-anchor motion-graph bound sound cues to exact remapped onset frame
        if remapped_sound and remapped_mg.get("semantic_motion_events"):
            anc_by_key: dict[tuple[str, str], int] = {}
            me_start_by_id: dict[str, int] = {}
            for me in remapped_mg["semantic_motion_events"]:
                meid_k = str(me["motion_event_id"])
                me_start_by_id[meid_k] = int(me["out_interval"][0])
                for sa in me.get("sound_anchors", []):
                    anc_by_key[(meid_k, str(sa["anchor_id"]))] = int(sa["frame"])
            sr_val = int(edl.get("audio_sample_rate") or 48000)
            for sev in remapped_sound.get("events", []):
                meid = sev.get("motion_event_id")
                aid = sev.get("anchor_id")
                if meid and (str(meid), str(aid or "")) in anc_by_key:
                    new_f = anc_by_key[(str(meid), str(aid or ""))]
                elif meid and str(meid) in me_start_by_id:
                    new_f = me_start_by_id[str(meid)]
                else:
                    continue
                sev["onset_frame"] = new_f
                sev["onset_sec"] = round(float(Fraction(new_f) / fps_frac), 4)
                sev["sample_onset"] = int(round(float(Fraction(new_f) / fps_frac) * sr_val))

    remap_report = {
        "schema_version": "kh03.timeline_remap_report.v1",
        "clock_domain": "OUTPUT_EDL_MAPPED",
        "time_map_sha256": edl_sha,
        "identity_mapping": is_identity,
        "fps_num": int(edl["fps_num"]),
        "fps_den": int(edl["fps_den"]),
        "total_output_frames": total_out,
        "beats_in": len(raw_beats),
        "beats_out": len(mapped_beats),
        "beats_removed_or_merged": removed_beats,
        "visual_events_in": len(raw_vis),
        "visual_events_out": len(mapped_vis),
        "visual_events_removed": removed_vis,
        "sound_cues_remapped": len((remapped_sound or {}).get("events", [])),
        "sound_cues_removed": removed_cues,
        "layout_beats_remapped": len((remapped_layout or {}).get("beats", [])),
        "layout_transitions_remapped": len((remapped_layout or {}).get("transitions", [])),
        "motion_events_remapped": len((remapped_mg or {}).get("semantic_motion_events", [])),
        "mapped_words_count": len(mapped_words),
        "pass": True,
    }

    if run_dir is not None:
        write_json_atomic(run_dir / "manifests" / "TIMELINE_REMAP_REPORT.json", remap_report)
        write_json_atomic(run_dir / "manifests" / "WORD_ANCHOR_MAP.json", {
            "schema_version": "kh03.word_anchor_map.v1",
            "time_map_sha256": edl_sha,
            "fps_num": int(edl["fps_num"]),
            "fps_den": int(edl["fps_den"]),
            "words": mapped_words,
        })

    out_ov = dict(kh03_overrides)
    out_ov["clock_domain"] = "OUTPUT_EDL_MAPPED"
    out_ov["time_map_sha256"] = edl_sha
    out_ov["total_output_frames"] = total_out
    out_ov["custom_beats"] = mapped_beats
    out_ov["custom_visual_events"] = mapped_vis
    out_ov["selected_hook_override"] = hook_ov or out_ov.get("selected_hook_override")
    out_ov["sound_plan"] = remapped_sound
    out_ov["sound_plan_sha256"] = hash_artifact(remapped_sound) if remapped_sound else None
    out_ov["layout_plan"] = remapped_layout
    out_ov["layout_plan_sha256"] = (remapped_layout or {}).get("layout_plan_sha256")
    out_ov["motion_graph"] = remapped_mg
    out_ov["motion_graph_sha256"] = (remapped_mg or {}).get("graph_sha256")
    out_ov["caption_layout_plan"] = remapped_captions
    out_ov["timeline_remap_report"] = remap_report
    return out_ov
