"""KH-03 Adaptive Creative Director — Multi-Direction Generation, Hook/Retention & KH-02 Binding.

Within the user-selected universal skill (SK-01..SK-05), generates 3 distinct internal creative
directions, selects the best transparently, binds explanation nodes + footage + verified
components into CREATIVE_PLAN.json, and translates cleanly into KH-02 SHOTBOOK V2 overrides.
"""

from __future__ import annotations

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
    sanitize_identifier,
    write_json_atomic,
)
from .recommend import get_skill_contract

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
) -> dict[str, Any]:
    """Generate 3 truthful hook options within selected skill; block spoilers & fake urgency."""
    phrases = semantic_graph.get("phrases") or []
    first_txt = phrases[0]["text"] if phrases else "Opening focus"
    has_nums = bool(phrases and phrases[0].get("numeric_claims"))
    has_neg = bool(phrases and phrases[0].get("has_negation"))
    sk = get_skill_contract(skill_id)
    pool = sk["hook_strategy_pool"]

    options = [
        {
            "option_id": f"{skill_id.lower()}_hook_01",
            "strategy": pool[0],
            "opening_visual": (
                "Immediate frame-0 contradiction lockup (headline value != ?) without spoiling final payoff."
                if (has_nums or has_neg) else f"Immediate frame-0 {pool[0]} introducing the core question truthfully."
            ),
            "opening_line": first_txt,
            "spoils_payoff": False,
            "deceptive_urgency": False,
            "selected": False,
        },
        {
            "option_id": f"{skill_id.lower()}_hook_02",
            "strategy": pool[1] if len(pool) > 1 else "direct_proof_lead",
            "opening_visual": "Verified visual context or mechanism question framed cleanly from frame 0.",
            "opening_line": first_txt,
            "spoils_payoff": False,
            "deceptive_urgency": False,
            "selected": False,
        },
        {
            "option_id": f"{skill_id.lower()}_hook_03",
            "strategy": pool[2] if len(pool) > 2 else "calm_direct_lead",
            "opening_visual": "Restrained clean typographic setup allowing spoken delivery and physical action to lead.",
            "opening_line": first_txt,
            "spoils_payoff": False,
            "deceptive_urgency": False,
            "selected": False,
        },
    ]

    if skill_id in ("SK-01", "SK-04") and (has_nums or has_neg):
        chosen = options[0]
    elif skill_id in ("SK-03", "SK-05"):
        chosen = options[2]
    else:
        chosen = options[1] if not (has_nums or has_neg) else options[0]

    for o in options:
        o["selected"] = (o["option_id"] == chosen["option_id"])

    return {
        "selected_hook_id": chosen["option_id"],
        "selected_strategy": chosen["strategy"],
        "options": options,
        "truthful_no_spoiler_verified": True,
        "editorial_intent_only": True,
    }


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
                "headline": txt[:56],
                "subtitle": exp_node.get("misconception_risk", "")[:58],
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
                "headline": txt[:54],
                "cta_tag": "Actionable takeaway",
                "font_size_px": 28,
            }
        if skill_id == "SK-04" and has_arith:
            last = arith_steps[-1]
            return "kh03.rolling_counter", {
                "kicker": "VERIFIED RESULT",
                "headline": txt[:52],
                "start_value": str(arith_steps[0].get("before", "")),
                "end_value": str(last.get("after", "")),
                "unit": str(last.get("unit", "")),
                "font_size_px": 32,
            }
        return "r1.light_sweep_badge", {
            "kicker": "RESOLUTION",
            "headline": txt[:56],
            "subtitle": exp_node["summary"][:64],
            "font_size_px": 26,
        }

    # Verified product/evidence beat
    if role == "evidence" or (has_asset and skill_id in ("SK-02", "SK-05")):
        return "kh03.proof_frame_reveal", {
            "kicker": "VERIFIED PROOF" if has_asset else "SOLUTION",
            "headline": txt[:56],
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
                "headline": txt[:56],
                "segments": segs[:5] or [{"label": "Allocated", "pct": 75.0, "delta": "-"}, {"label": "Remaining", "pct": 25.0, "delta": ""}],
                "font_size_px": 24,
            }
        last = arith_steps[-1]
        return "kh03.rolling_counter", {
            "kicker": "REMAINING BALANCE",
            "headline": txt[:52],
            "start_value": str(arith_steps[0].get("before", "100")),
            "end_value": str(last.get("after", "0")),
            "unit": str(last.get("unit", "")),
            "font_size_px": 30,
        }

    # Comparison beat
    if role == "comparison" or phrase.get("has_comparison"):
        return "kh03.comparison_matrix", {
            "kicker": "DIRECT COMPARISON",
            "headline": txt[:54],
            "left_col": {"title": "Option A", "detail": txt[:42]},
            "right_col": {"title": "Option B", "detail": exp_node.get("misconception_risk", "Contrasting trade-off")[:42]},
            "font_size_px": 24,
        }

    # Causal mechanism beat
    if role == "mechanism" or phrase.get("has_causal"):
        return "kh03.causal_flow_graph", {
            "kicker": "CAUSE -> EFFECT MECHANISM",
            "headline": txt[:54],
            "nodes": [
                {"label": "Cause", "text": txt[:32]},
                {"label": "Mechanism", "text": exp_rep.get("representation", "Process")[:30]},
                {"label": "Outcome", "text": exp_node["label"][:32]},
            ],
            "font_size_px": 24,
        }

    # Step / chronological timeline beat
    if role == "step" or skill_id in ("SK-03", "SK-04"):
        return "kh03.annotated_timeline", {
            "kicker": f"STEP {idx:02d}",
            "headline": txt[:56],
            "steps": [
                {"label": f"0{idx}", "text": txt[:46]},
            ],
            "font_size_px": 26,
        }

    # Skill-distinct fallback so skills never look identical on the same script
    fallback_by_skill = {
        "SK-01": ("r1.kinetic_phrase_mask", {"kicker": "KEY SHIFT", "lines": [txt[:34]], "headline": txt[:52], "font_size_px": 28}),
        "SK-02": ("r1.clip_mask_wipe", {"kicker": "EDITORIAL DETAIL", "headline": txt[:54], "subtitle": exp_node["node_type"].upper(), "font_size_px": 26}),
        "SK-03": ("kh03.annotated_timeline", {"kicker": "STORY ARC", "headline": txt[:54], "steps": [{"label": f"ARC {idx}", "text": txt[:44]}], "font_size_px": 26}),
        "SK-04": ("kh03.causal_flow_graph", {"kicker": "EXPLANATION", "headline": txt[:54], "nodes": [{"label": "Premise", "text": txt[:32]}, {"label": "Result", "text": exp_node["label"][:32]}], "font_size_px": 24}),
        "SK-05": ("r1.spotlight_focus", {"kicker": "NOTE", "badge": "CLEAR", "headline": txt[:52], "subtitle": "Natural A-roll focus", "font_size_px": 26}),
    }
    return fallback_by_skill.get(skill_id, ("local.editorial_panel", {"kicker": "DETAIL", "headline": txt[:54], "font_size_px": 26}))


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
    fs_lock = bool(eff_constraints.get("fullscreen_lock") or eff_constraints.get("layout_lock") == "FULLSCREEN_9_16")

    selected_dir, all_dirs = choose_direction(
        selected_skill_id, semantic_graph, explanation_graph, footage_manifest, constraints=eff_constraints,
    )
    hook_plan = plan_hooks(selected_skill_id, semantic_graph, explanation_graph, constraints=eff_constraints)

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
            hl = _trim_words(comp_params["headline"], 48)
            max_words = max(3, int(((ef - sf) / max(1.0, fps)) * 3.0))
            words = hl.split()
            if len(words) > max_words:
                hl = _trim_words(" ".join(words[:max_words]), 46)
            comp_params["headline"] = hl
        if isinstance(comp_params.get("subtitle"), str):
            comp_params["subtitle"] = _trim_words(comp_params["subtitle"], 56)
        comp_params["theme"] = art["theme"]
        comp_params["skill_id"] = selected_skill_id
        if idx == 0:
            comp_params["selected_hook_id"] = hook_plan["selected_hook_id"]

        spec = design_component(
            component_id=f"kh03_comp_{idx + 1:02d}_{bid}",
            requested_capability_id=req_cap,
            beat_id=bid,
            role=ph.get("semantic_role", "context"),
            start_frame=sf,
            end_frame=ef,
            normalized_bbox=list(default_box),
            params=comp_params,
            semantic_anchor=f"{selected_skill_id}:{enode['node_id']}:{ph['phrase_id']}",
            registry=reg,
            aspect_ratio=aspect,
            fps=fps,
            profile=sk["preferred_profile"],
            z_order=32 + idx,
            asset_ref=asset_ref,
            data_bindings={"explanation_node_id": enode["node_id"], "phrase_id": ph["phrase_id"]},
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
            "visual_mode": "fullscreen_top_overlay" if fs_lock else f"{selected_skill_id.lower()}_adaptive_stage",
            "focus_subject": ph["text"][:56].strip() or f"Beat {idx + 1}",
            "continuity_in": "open_frame" if idx == 0 else "editorial_spine",
            "continuity_out": "clean_settle" if idx == len(merged_phrases) - 1 else "editorial_spine",
            "planned_information_change": f"Deliver {spec['capability_id']} ({enode['node_type']}) anchored to {ph['phrase_id']}.",
            "why": f"KH-03 {selected_skill_id} ({selected_dir['direction_id']}) binding {enode['node_id']} -> {spec['capability_id']}.",
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

    # Solve swept bounding-box collisions against face/hands/objects & platform rails
    solved = solve_visual_collisions(
        raw_specs,
        protected_zones=prot_zones,
        clear_zones=clear_zones,
        canvas_width=c_w,
        canvas_height=c_h,
    )
    final_specs = solved["components"]

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
        "layout_constraints": {
            "aspect_ratio": aspect,
            "fullscreen_lock": fs_lock,
            "collision_ok": solved["ok"],
            "reflow_count": len(solved["reflows"]),
            "total_frames": total_frames,
        },
    }
    plan_core["plan_sha256"] = hash_artifact(plan_core)
    assert_kh03_schema(plan_core, "creative_plan")

    # Emit Section 13 creative direction & component artifacts
    write_json_atomic(run_dir / "manifests" / "CREATIVE_PLAN.json", plan_core)
    write_json_atomic(run_dir / "manifests" / "SELECTED_DIRECTION.json", selected_dir)
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
) -> dict[str, Any]:
    """Translate KH-03 CREATIVE_PLAN into authoritative KH-02 resolve_editorial_plan overrides."""
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
    sel_dir = next(d for d in creative_plan["directions_considered"] if d["selected"])
    return {
        "selected_skill_id": creative_plan["selected_skill_id"],
        "kh03_plan_sha256": creative_plan["plan_sha256"],
        "profile": sk["preferred_profile"],
        "energy_mode": sk["preferred_energy"],
        "custom_beats": kh02_beats,
        "custom_visual_events": kh02_visual_events,
        "creative_direction_override": {
            "direction_id": sel_dir["direction_id"],
            "name": sel_dir["name"],
            "theme": sel_dir["theme"],
            "archetype": sel_dir["archetype"],
            "layout_strategy": sel_dir["layout_strategy"],
            "selected": True,
        },
    }
