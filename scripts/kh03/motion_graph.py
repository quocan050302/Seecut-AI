"""KH-03.R3 Semantic Hook Director (CMI-01), Declarative Motion Scene Graph (CMI-02),
Shared Element Morphing & Single-Pass EDL Remapping (CMI-03), and Multi-Level Visual Density &
Duration-Weighted Anti-Repetition Engine (CMI-07).

Universal, cross-niche architecture:
- Operates strictly on content relations (negation, quantity, comparison, process, chronology,
  cause/effect, hierarchy, spatial, evidence, narrative, natural_action), selected skill, aspect
  ratio, duration, and constraints — ZERO niche-name hardcoding, ZERO single-creator or single-niche special cases.
"""

from __future__ import annotations

import csv
import json
import math
import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any

from .contracts import (
    assert_kh03_schema,
    hash_artifact,
    sanitize_identifier,
    sanitize_untrusted_text,
    write_json_atomic,
)
from .recommend import get_skill_contract

ALLOWED_EASINGS = frozenset({
    "none", "power1.out", "power2.out", "power2.inOut", "power3.out",
    "power3.inOut", "back.out(1.4)", "back.out(1.7)", "back.out(1.9)",
})

# ======================================================================
# CMI-01 — SEMANTIC HOOK DIRECTOR (EVIDENCE-VERIFIED, MICROBEAT GRAPH)
# ======================================================================

_FAKE_URGENCY_RE = re.compile(
    r"(?i)\b(act\s+now|last\s+chance|guaranteed\s+millions?|100%\s+guaranteed|secret\s+they\s+hide|cure\s+overnight|get\s+rich\s+fast)\b"
)


def _extract_payoff_tokens(semantic_graph: dict[str, Any], explanation_graph: dict[str, Any]) -> set[str]:
    """Identify payoff numbers/conclusions from final beats so opening hooks NEVER spoil the payoff!"""
    tokens: set[str] = set()
    phrases = semantic_graph.get("phrases") or []
    if len(phrases) >= 2:
        for ph in phrases[1:]:
            if ph.get("semantic_role") in ("payoff", "demonstration"):
                for nc in ph.get("numeric_claims") or []:
                    tokens.add(str(nc.get("raw_text") or "").strip().lower())
    steps = (explanation_graph.get("numeric_checks") or {}).get("arithmetic_steps") or []
    if steps:
        after_v = str(steps[-1].get("after") or "").strip().lower()
        if after_v:
            tokens.add(after_v)
            tokens.add(f"${after_v}")
    # Remove any number that genuinely appeared in phrase 0 (phrase 0 is the premise, not a spoiler!)
    if phrases:
        for nc in phrases[0].get("numeric_claims") or []:
            tokens.discard(str(nc.get("raw_text") or "").strip().lower())
            tokens.discard(str(nc.get("value") or "").strip().lower())
    return {t for t in tokens if t}


def build_semantic_hook_plan(
    *,
    run_id: str,
    skill_id: str,
    semantic_graph: dict[str, Any],
    explanation_graph: dict[str, Any],
    input_analysis: dict[str, Any],
    action_timeline: list[dict[str, Any]] | None = None,
    constraints: dict[str, Any] | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Build CMI-01 evidence-verified hook candidates, frame-level microbeats, truth ledger & payoff map."""
    phrases = semantic_graph.get("phrases") or []
    media = input_analysis.get("media") or {}
    fps_num = max(1, int(media.get("fps_num") or 24))
    fps_den = max(1, int(media.get("fps_den") or 1))
    fps = float(Fraction(fps_num, fps_den))
    total_frames = max(1, int(media.get("nb_frames") or 240))
    dur_sec = total_frames / max(1.0, fps)
    is_long_form = dur_sec >= 90.0
    is_calm_skill = skill_id in ("SK-03", "SK-05")

    first_ph = phrases[0] if phrases else None
    first_txt = sanitize_untrusted_text((first_ph or {}).get("text", ""), max_len=180)
    has_transcript = bool(first_txt)
    has_neg = bool((first_ph or {}).get("has_negation"))
    has_nums = bool((first_ph or {}).get("numeric_claims"))
    has_causal = bool((first_ph or {}).get("has_causal"))
    has_comp = bool((first_ph or {}).get("has_comparison"))
    has_action_ev = any(a.get("verified_physical_event") for a in (action_timeline or []))
    payoff_tokens = _extract_payoff_tokens(semantic_graph, explanation_graph)
    first_words = list((first_ph or {}).get("word_ids") or [])
    first_end_f = int((first_ph or {}).get("end_frame") or min(total_frames, int(round(fps * 3.0))))
    hook_end_f = min(total_frames, max(min(total_frames, int(round(fps * 1.5))), min(first_end_f, int(round(fps * (6.0 if is_long_form else 3.2))))))

    # Construct 3 genuinely distinct micro-storyboards within the chosen skill (never identical labels!)
    def _make_microbeats(kind: str, headline: str, question_cue: str) -> list[dict[str, Any]]:
        if is_long_form or (is_calm_skill and kind == "calm_context"):
            # Long-form / calm natural opening: intentional unbroken context hold (no forced 3s cuts!)
            return [{
                "microbeat_id": f"{kind}_mb_01",
                "phase": "calm_context_establish",
                "start_frame": 0,
                "end_frame": hook_end_f,
                "visual_intent": headline,
                "word_ids": first_words[:8],
                "continuous_unbroken_shot": True,
            }]
        # Flexible short-form evolving 3-window progression (~0-0.5s / ~0.5-1.5s / ~1.5-3.0s)
        f1 = min(hook_end_f, max(2, int(round(fps * 0.50))))
        f2 = min(hook_end_f, max(f1 + 2, int(round(fps * 1.55))))
        return [
            {
                "microbeat_id": f"{kind}_mb_01_frame0",
                "phase": "immediate_readable_frame0",
                "start_frame": 0,
                "end_frame": f1,
                "visual_intent": headline,
                "word_ids": first_words[:2],
                "continuous_unbroken_shot": False,
            },
            {
                "microbeat_id": f"{kind}_mb_02_contrast",
                "phase": "attention_contrast_cue",
                "start_frame": f1,
                "end_frame": f2,
                "visual_intent": question_cue,
                "word_ids": first_words[2:5],
                "continuous_unbroken_shot": False,
            },
            {
                "microbeat_id": f"{kind}_mb_03_open_loop",
                "phase": "next_semantic_implication",
                "start_frame": f2,
                "end_frame": hook_end_f,
                "visual_intent": "Transition cleanly into proof/mechanism without spoiling final result.",
                "word_ids": first_words[5:9],
                "continuous_unbroken_shot": False,
            },
        ]

    # Build 3 distinct candidate hooks grounded strictly in actual source semantics
    lead_num = (first_ph.get("numeric_claims")[0]["raw_text"] if (first_ph and has_nums) else "")
    illustrative = bool(first_ph and any(n.get("illustrative") for n in (first_ph.get("numeric_claims") or [])))
    ex_prefix = "Example: " if illustrative else ""

    raw_candidates: list[dict[str, Any]] = []
    # Candidate 1: Expectation vs Reality / Visual Contradiction (ONLY valid when negation/comparison/numeric tension exists!)
    cand1_supported = bool(has_transcript and (has_neg or has_comp or has_nums))
    cand1_headline = (
        f"{ex_prefix}{lead_num} ≠ available reality?" if (has_nums and has_neg)
        else (f"{ex_prefix}{first_txt[:54]}" if cand1_supported else "Direct visual context")
    )
    raw_candidates.append({
        "option_id": f"{skill_id.lower()}_hook_01_contradiction",
        "primitive": "expectation_vs_reality" if (has_neg or has_comp) else "visual_question",
        "strategy": "expectation_vs_reality" if (has_neg or has_comp) else "visual_question",
        "opening_line": first_txt or "[Visual-only opening — no transcript]",
        "opening_visual": cand1_headline,
        "microbeats": _make_microbeats("contradiction", cand1_headline, "Highlight semantic tension / exact contrast sign."),
        "content_backed": cand1_supported,
        "clarity_score": 90.0 if cand1_supported else 55.0,
        "hierarchy_score": 88.0,
        "relevance_score": 92.0 if (has_neg or has_nums) else 60.0,
        "payoff_coverage_score": 90.0 if len(phrases) >= 2 else 72.0,
        "tone_fit_score": 92.0 if skill_id in ("SK-01", "SK-04") else (76.0 if skill_id == "SK-02" else 64.0),
        "layout_feasibility_score": 90.0,
    })

    # Candidate 2: Direct Proof / Cause-Effect Mechanism Lead
    cand2_prim = "cause_effect" if has_causal else ("direct_proof" if has_transcript else "unexpected_action")
    cand2_headline = (
        f"How it works: {first_txt[:48]}" if has_causal
        else (f"See the real mechanism: {first_txt[:42]}" if has_transcript else "Direct verified footage action")
    )
    raw_candidates.append({
        "option_id": f"{skill_id.lower()}_hook_02_proof",
        "primitive": cand2_prim,
        "strategy": cand2_prim,
        "opening_line": first_txt or "[Visual-only opening — no transcript]",
        "opening_visual": cand2_headline,
        "microbeats": _make_microbeats("proof", cand2_headline, "Anchor immediate evidence/mechanism cue to speech or action."),
        "content_backed": bool(has_transcript or has_action_ev),
        "clarity_score": 86.0,
        "hierarchy_score": 88.0,
        "relevance_score": 89.0 if (has_causal or not has_neg) else 80.0,
        "payoff_coverage_score": 86.0,
        "tone_fit_score": 92.0 if skill_id in ("SK-02", "SK-04") else 80.0,
        "layout_feasibility_score": 92.0,
    })

    # Candidate 3: Calm Context / Natural Direct Opening (always truthful fallback!)
    cand3_headline = (first_txt[:56] if has_transcript else "Direct verified visual opening")
    raw_candidates.append({
        "option_id": f"{skill_id.lower()}_hook_03_calm",
        "primitive": "calm_context",
        "strategy": "calm_context",
        "opening_line": first_txt or "[Visual-only opening — no transcript]",
        "opening_visual": cand3_headline,
        "microbeats": _make_microbeats("calm_context", cand3_headline, "Hold natural framing cleanly while speaker/action leads."),
        "content_backed": True,
        "clarity_score": 84.0,
        "hierarchy_score": 84.0,
        "relevance_score": 84.0,
        "payoff_coverage_score": 82.0,
        "tone_fit_score": 95.0 if (is_calm_skill or is_long_form) else 74.0,
        "layout_feasibility_score": 96.0,
    })

    # Hard-gate verification on EVERY candidate (truthfulness, no spoiler, no fake urgency, content backing)
    truth_entries: list[dict[str, Any]] = []
    scored_options: list[dict[str, Any]] = []
    for c in raw_candidates:
        vis_low = c["opening_visual"].lower()
        spoils = any(tok in vis_low for tok in payoff_tokens)
        fake_urg = bool(_FAKE_URGENCY_RE.search(c["opening_visual"]))
        contradiction_invented = (c["primitive"] == "expectation_vs_reality" and not (has_neg or has_comp))
        hard_pass = (not spoils) and (not fake_urg) and (not contradiction_invented) and bool(c["content_backed"])
        weighted = round(
            0.22 * c["clarity_score"]
            + 0.16 * c["hierarchy_score"]
            + 0.22 * c["relevance_score"]
            + 0.14 * c["payoff_coverage_score"]
            + 0.16 * c["tone_fit_score"]
            + 0.10 * c["layout_feasibility_score"],
            2,
        )
        opt = {
            "option_id": c["option_id"],
            "primitive": c["primitive"],
            "strategy": c["strategy"],
            "opening_visual": c["opening_visual"],
            "opening_line": c["opening_line"],
            "microbeats": c["microbeats"],
            "spoils_payoff": spoils,
            "deceptive_urgency": fake_urg,
            "contradiction_invented": contradiction_invented,
            "hard_gate_pass": hard_pass,
            "scores": {
                "clarity": c["clarity_score"],
                "hierarchy": c["hierarchy_score"],
                "relevance": c["relevance_score"],
                "payoff_coverage": c["payoff_coverage_score"],
                "tone_fit": c["tone_fit_score"],
                "layout_feasibility": c["layout_feasibility_score"],
                "weighted_editorial_fit": weighted,
            },
            "selected": False,
        }
        scored_options.append(opt)
        truth_entries.append({
            "option_id": c["option_id"],
            "content_backed": bool(c["content_backed"]),
            "spoils_payoff": spoils,
            "deceptive_urgency": fake_urg,
            "contradiction_invented": contradiction_invented,
            "illustrative_labeled": illustrative,
            "hard_gate_pass": hard_pass,
        })

    # Choose highest-scoring candidate that PASSED all hard gates (fallback strictly to calm_context!)
    eligible = [o for o in scored_options if o["hard_gate_pass"]]
    eligible.sort(key=lambda o: (-o["scores"]["weighted_editorial_fit"], o["option_id"]))
    chosen = eligible[0] if eligible else scored_options[-1]
    for o in scored_options:
        o["selected"] = (o["option_id"] == chosen["option_id"])

    payoff_beat_id = f"beat_{len(phrases):02d}" if phrases else "beat_01"
    payoff_map = {
        "schema_version": "kh03.hook_payoff_map.v1",
        "run_id": run_id,
        "selected_hook_id": chosen["option_id"],
        "opening_beat_id": "beat_01",
        "payoff_beat_id": payoff_beat_id,
        "protected_payoff_tokens_not_spoiled": sorted(payoff_tokens),
        "open_loop_closed": len(phrases) >= 2,
        "editorial_intent_only": True,
    }
    hook_plan = {
        "selected_hook_id": chosen["option_id"],
        "selected_strategy": chosen["strategy"],
        "selected_primitive": chosen["primitive"],
        "hook_window_frames": [0, hook_end_f],
        "hook_microbeats": chosen["microbeats"],
        "options": scored_options,
        "truth_ledger": truth_entries,
        "payoff_map": payoff_map,
        "truthful_no_spoiler_verified": bool(chosen["hard_gate_pass"]),
        "editorial_intent_only": True,
        "fallback_used": (not eligible),
    }

    if run_dir is not None:
        mdir = run_dir / "manifests"
        mdir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(mdir / "HOOK_CANDIDATES.json", {"schema_version": "kh03.hook_candidates.v1", "run_id": run_id, "options": scored_options})
        write_json_atomic(mdir / "HOOK_MICROBEATS.json", {"schema_version": "kh03.hook_microbeats.v1", "run_id": run_id, "selected_hook_id": chosen["option_id"], "microbeats": chosen["microbeats"]})
        write_json_atomic(mdir / "HOOK_TRUTH_LEDGER.json", {"schema_version": "kh03.hook_truth_ledger.v1", "run_id": run_id, "entries": truth_entries})
        write_json_atomic(mdir / "SELECTED_HOOK.json", {
            "schema_version": "kh03.selected_hook.v1",
            "run_id": run_id,
            "selected_skill_id": skill_id,
            "selected_hook_id": chosen["option_id"],
            "strategy": chosen["strategy"],
            "opening_visual": chosen["opening_visual"],
            "hook_window_frames": [0, hook_end_f],
            "microbeats": chosen["microbeats"],
            "truthful_no_spoiler_verified": hook_plan["truthful_no_spoiler_verified"],
        })
        write_json_atomic(mdir / "HOOK_PAYOFF_MAP.json", payoff_map)
    return hook_plan


# ======================================================================
# CMI-02 — INTERNAL COMPONENT MOTION / SEMANTIC SCENE GRAPH
# ======================================================================


def _choose_internal_primitive(comp: dict[str, Any], phrase: dict[str, Any] | None) -> tuple[str, str]:
    """Select composable declarative motion primitive purely from semantic relation & component kind."""
    kind = str(comp.get("kind") or "")
    role = str((phrase or {}).get("semantic_role") or comp.get("role") or "context")
    if kind == "kh03_proportional_reservoir":
        return "proportional_allocation", "quantity"
    if kind == "kh03_rolling_counter":
        return "exact_decimal_counter", "quantity"
    if kind == "kh03_causal_flow_graph":
        return "diagram_state_transition", "cause_effect"
    if kind == "kh03_annotated_timeline":
        return "timeline_progression", "chronology"
    if kind == "kh03_comparison_matrix":
        return "comparative_reveal", "comparison"
    if kind == "kh03_proof_frame_reveal":
        return "masked_detail_reveal", "evidence"
    if kind == "kh03_contradiction_lockup":
        return "progressive_draw", "comparison"
    if kind == "kh03_cta_magnetic_underline":
        return "progressive_draw", "narrative"
    if role == "step":
        return "step_completion", "process"
    if role == "context":
        return "stable_hold", "natural_action"
    return "staggered_emergence", "abstract"


def _safe_easing(raw: str | None, *, reduced_motion: bool = False) -> str:
    if reduced_motion:
        return "none"
    e = str(raw or "power2.out").strip()
    return e if e in ALLOWED_EASINGS else "power2.out"


def build_motion_graph(
    *,
    run_id: str,
    skill_id: str,
    components: list[dict[str, Any]],
    semantic_graph: dict[str, Any],
    explanation_graph: dict[str, Any],
    transcript: dict[str, Any] | None = None,
    layout_plan: dict[str, Any] | None = None,
    fps_num: int = 24,
    fps_den: int = 1,
    total_frames: int = 240,
    reduced_motion: bool = False,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Build declarative CMI-02 MOTION_GRAPH.json + CMI-03 SHARED_ELEMENT_GRAPH.json with exact word anchors."""
    fps_frac = Fraction(max(1, int(fps_num)), max(1, int(fps_den)))
    fps = float(fps_frac)
    phrases = semantic_graph.get("phrases") or []
    phrase_by_beat = {f"beat_{i + 1:02d}": ph for i, ph in enumerate(phrases)}
    words_by_id = {
        str(w.get("word_id")): w for w in ((transcript or {}).get("words") or []) if w.get("word_id")
    }
    arith_steps = (explanation_graph.get("numeric_checks") or {}).get("arithmetic_steps") or []

    nodes: list[dict[str, Any]] = []
    sem_events: list[dict[str, Any]] = []
    node_bindings: list[dict[str, Any]] = []
    state_trace_rows: list[dict[str, Any]] = []

    # Track claim/evidence signatures per node for CMI-03 verified shared-element identity!
    claim_node_history: dict[str, list[dict[str, Any]]] = {}

    for c_idx, comp in enumerate(components):
        cid = str(comp["component_id"])
        bid = str(comp["beat_id"])
        sf = max(0, int(comp["start_frame"]))
        ef = max(sf + 2, min(total_frames, int(comp["end_frame"])))
        dur = ef - sf
        bbox = [round(float(v), 4) for v in comp["normalized_bbox"]]
        z = int(comp.get("z_order", 32))
        ph = phrase_by_beat.get(bid)
        prim, relation = _choose_internal_primitive(comp, ph)
        ease = _safe_easing(comp.get("easing"), reduced_motion=reduced_motion)

        # Anchor strictly to verified word_id when present, else disclose phrase_fallback or beat_start
        ph_wids = list((ph or {}).get("word_ids") or [])
        anchor_wid = ph_wids[0] if (ph_wids and ph_wids[0] in words_by_id) else None
        anchor_prec = (
            "word_exact" if (anchor_wid and not words_by_id[anchor_wid].get("synthetic_timing"))
            else ("phrase_fallback" if ph_wids else "beat_start")
        )
        en = min(max(2, int(comp.get("entry_frames", 6))), max(2, dur // 3))
        ex = min(max(2, int(comp.get("exit_frames", 4))), max(2, dur // 4))
        hd = max(0, dur - en - ex)
        min_dwell = min(dur, max(6, int(round(fps * 0.45))))

        # Collect semantic claim refs (exact numbers / evidence asset_ref / beat role)
        claim_refs: list[str] = []
        ev_refs: list[str] = [str(comp["asset_ref"])] if comp.get("asset_ref") else []
        num_exact: dict[str, Any] | None = None
        if ph and ph.get("numeric_claims"):
            for nc in ph["numeric_claims"]:
                claim_refs.append(f"num:{nc['value']}:{nc['unit']}")
            first_nc = ph["numeric_claims"][0]
            try:
                num_exact = {"decimal_value": str(Decimal(str(first_nc["value"]))), "unit": str(first_nc["unit"]), "illustrative": bool(first_nc.get("illustrative"))}
            except InvalidOperation:
                num_exact = None
        if comp.get("kind") == "kh03_rolling_counter" and arith_steps:
            last_after = str(arith_steps[-1].get("after", ""))
            unit = str(arith_steps[-1].get("unit", ""))
            if last_after:
                claim_refs.append(f"num:{last_after}:{unit}")
                num_exact = {"decimal_value": last_after, "unit": unit, "exact_endpoint": True}
        if not claim_refs:
            claim_refs.append(f"rel:{relation}:{bid}")

        # Root container node for component
        root_nid = sanitize_identifier(f"{cid}_root")
        root_kfs = [
            {"frame": sf, "opacity": 1.0 if sf == 0 else 0.0, "tx": 0.0, "ty": 0.0 if reduced_motion else 0.015, "scale": 1.0 if reduced_motion else 0.96, "rotation_deg": 0.0, "progress": 0.0, "state_label": "entry"},
            {"frame": min(ef - 1, sf + en), "opacity": 1.0, "tx": 0.0, "ty": 0.0, "scale": 1.0, "rotation_deg": 0.0, "progress": 1.0, "state_label": "settled"},
            {"frame": max(sf + 1, ef - ex), "opacity": 1.0, "tx": 0.0, "ty": 0.0, "scale": 1.0, "rotation_deg": 0.0, "progress": 1.0, "state_label": "hold_end"},
        ]
        root_node = {
            "node_id": root_nid,
            "component_id": cid,
            "parent_id": None,
            "beat_id": bid,
            "role": "container",
            "primitive": prim,
            "evidence_refs": ev_refs,
            "claim_refs": claim_refs,
            "geometry_norm": bbox,
            "safe_bounds_norm": bbox,
            "local_coord_space": "CANVAS_NORM",
            "z_order": z,
            "anchor_word_id": anchor_wid,
            "anchor_precision": anchor_prec,
            "action_anchor_id": None,
            "start_frame": sf,
            "end_frame": ef,
            "src_start_frame": sf,
            "src_end_frame": ef,
            "min_read_dwell_frames": min_dwell,
            "entry_frames": en,
            "hold_frames": hd,
            "exit_frames": ex,
            "easing": ease,
            "clip_mask": {"type": "inset_horizontal", "from": "0% 36% 0% 0%", "to": "0% 0% 0% 0%"} if prim == "masked_detail_reveal" else None,
            "visibility": True,
            "numeric_exact": num_exact,
            "shared_element_id": None,
            "keyframes": root_kfs,
            "end_state": "SETTLED_HOLD",
        }
        nodes.append(root_node)

        # Internal semantic child sub-nodes (CMI-02: real internal element progression, NOT just outer pop!)
        params = comp.get("params") or {}
        child_specs: list[tuple[str, str, str, list[float], str | None]] = []
        kind = str(comp.get("kind") or "")
        if kind == "kh03_proportional_reservoir":
            segs = params.get("segments") or [{"label": "Part 1", "pct": 60.0}, {"label": "Part 2", "pct": 40.0}]
            acc_x = 0.0
            for s_i, sg in enumerate(segs[:5]):
                w_f = max(0.05, min(1.0 - acc_x, float(sg.get("pct", 20.0)) / 100.0))
                child_specs.append((f"{cid}_seg_{s_i + 1}", "proportional_bar_segment", "proportional_allocation", [round(acc_x, 4), 0.25, round(w_f, 4), 0.28], f"seg:{sg.get('label', s_i)}:{sg.get('delta', '')}"))
                acc_x = min(0.95, acc_x + w_f)
        elif kind == "kh03_causal_flow_graph":
            fnodes = params.get("nodes") or [{"label": "Cause"}, {"label": "Effect"}]
            step_w = round(1.0 / max(1, len(fnodes[:3])), 4)
            for ni, nd in enumerate(fnodes[:3]):
                child_specs.append((f"{cid}_node_{ni + 1}", "causal_step_node", "diagram_state_transition", [round(ni * step_w, 4), 0.22, round(step_w * 0.88, 4), 0.50], f"causal:{nd.get('label', ni)}"))
        elif kind == "kh03_annotated_timeline":
            steps = params.get("steps") or [{"label": "01"}]
            for si, st in enumerate(steps[:3]):
                child_specs.append((f"{cid}_tl_{si + 1}", "timeline_milestone", "timeline_progression", [0.04, round(0.28 + si * 0.22, 4), 0.92, 0.18], f"tl:{st.get('label', si)}"))
        elif kind == "kh03_contradiction_lockup":
            child_specs.append((f"{cid}_op", "contrast_operator", "progressive_draw", [0.42, 0.30, 0.16, 0.34], f"op:{params.get('operator', '!=')}"))
        elif kind == "kh03_rolling_counter":
            child_specs.append((f"{cid}_val", "counter_value", "exact_decimal_counter", [0.08, 0.32, 0.84, 0.42], f"counter:{params.get('end_value', '')}"))
        elif kind == "kh03_comparison_matrix":
            child_specs.append((f"{cid}_left", "comparison_left", "comparative_reveal", [0.03, 0.26, 0.45, 0.66], "cmp:left"))
            child_specs.append((f"{cid}_right", "comparison_right", "comparative_reveal", [0.52, 0.26, 0.45, 0.66], "cmp:right"))
        elif kind == "kh03_proof_frame_reveal":
            child_specs.append((f"{cid}_media", "evidence_crop", "masked_detail_reveal", [0.04, 0.18, 0.92, 0.60], ev_refs[0] if ev_refs else "proof:frame"))
        elif kind == "kh03_cta_magnetic_underline":
            child_specs.append((f"{cid}_ul", "magnetic_underline", "progressive_draw", [0.05, 0.68, 0.90, 0.10], "cta:underline"))
        else:
            child_specs.append((f"{cid}_body", "primary_content", prim, [0.05, 0.20, 0.90, 0.65], claim_refs[0]))

        n_ch = max(1, len(child_specs))
        active_window = max(2, dur - ex - 2)
        for ch_i, (ch_id, ch_role, ch_prim, ch_geom, ch_claim) in enumerate(child_specs):
            # Distribute sub-node activation across spoken words (or proportional sub-beats) inside component window!
            w_idx = min(len(ph_wids) - 1, int(round(ch_i * len(ph_wids) / float(n_ch)))) if ph_wids else -1
            ch_wid = ph_wids[w_idx] if (w_idx >= 0 and ph_wids[w_idx] in words_by_id) else anchor_wid
            if ch_wid and ch_wid in words_by_id and not words_by_id[ch_wid].get("synthetic_timing"):
                w_start_f = int(round(float(words_by_id[ch_wid].get("start_sec", words_by_id[ch_wid].get("start", sf / fps))) * fps))
                ch_sf = max(sf, min(ef - 2, w_start_f))
                ch_prec = "word_exact"
            else:
                ch_sf = min(ef - 2, sf + int(round((ch_i * active_window) / float(n_ch + 1))))
                ch_prec = anchor_prec
            ch_en = min(max(2, int(round(fps * 0.24))), max(2, (ef - ch_sf) // 2))
            ch_settle = min(ef - 1, ch_sf + ch_en)
            ch_nid = sanitize_identifier(ch_id)
            ch_claims = [ch_claim] if ch_claim else list(claim_refs)
            ch_kfs = [
                {"frame": ch_sf, "opacity": 0.0 if ch_sf > 0 else 1.0, "tx": 0.0 if reduced_motion else -0.02, "ty": 0.0 if reduced_motion else 0.015, "scale": 1.0 if reduced_motion else 0.90, "rotation_deg": 0.0, "progress": 0.0, "state_label": "enter"},
                {"frame": ch_settle, "opacity": 1.0, "tx": 0.0, "ty": 0.0, "scale": 1.0, "rotation_deg": 0.0, "progress": 1.0, "state_label": "active"},
                {"frame": max(ch_settle, ef - ex), "opacity": 1.0, "tx": 0.0, "ty": 0.0, "scale": 1.0, "rotation_deg": 0.0, "progress": 1.0, "state_label": "settle"},
            ]
            ch_node = {
                "node_id": ch_nid,
                "component_id": cid,
                "parent_id": root_nid,
                "beat_id": bid,
                "role": ch_role,
                "primitive": ch_prim,
                "evidence_refs": ev_refs,
                "claim_refs": ch_claims,
                "geometry_norm": ch_geom,
                "safe_bounds_norm": bbox,
                "local_coord_space": "COMPONENT_LOCAL_NORM",
                "z_order": z + 1 + ch_i,
                "anchor_word_id": ch_wid,
                "anchor_precision": ch_prec,
                "action_anchor_id": None,
                "start_frame": ch_sf,
                "end_frame": ef,
                "src_start_frame": ch_sf,
                "src_end_frame": ef,
                "min_read_dwell_frames": max(4, min(ef - ch_sf, min_dwell)),
                "entry_frames": ch_en,
                "hold_frames": max(0, ef - ch_sf - ch_en - ex),
                "exit_frames": ex,
                "easing": ease,
                "clip_mask": None,
                "visibility": True,
                "numeric_exact": num_exact if ch_prim in ("exact_decimal_counter", "proportional_allocation") else None,
                "shared_element_id": None,
                "keyframes": ch_kfs,
                "end_state": "SETTLED_HOLD",
            }
            nodes.append(ch_node)

            # Semantic motion event + authoritative sub-event sound anchors (CMI-08 consumes these directly!)
            me_id = sanitize_identifier(f"mev_{ch_nid}")
            s_kind = (
                "click" if ch_prim in ("proportional_allocation", "exact_decimal_counter", "step_completion", "timeline_progression")
                else ("reveal" if ch_prim in ("masked_detail_reveal", "diagram_state_transition", "comparative_reveal") else "arrival")
            )
            sound_anchors = [{
                "anchor_id": f"anc_{me_id}_{s_kind}",
                "kind": s_kind,
                "frame": ch_sf,
                "recommended_family": "click" if s_kind == "click" else ("whoosh" if s_kind == "reveal" else "pop"),
            }]
            if ch_i == n_ch - 1 and ch_settle > ch_sf:
                sound_anchors.append({
                    "anchor_id": f"anc_{me_id}_settle",
                    "kind": "payoff" if (c_idx == len(components) - 1) else "settle",
                    "frame": ch_settle,
                    "recommended_family": "chime" if (c_idx == len(components) - 1) else "click",
                })
            sem_events.append({
                "motion_event_id": me_id,
                "node_id": ch_nid,
                "component_id": cid,
                "beat_id": bid,
                "src_interval": [ch_sf, ef],
                "out_interval": [ch_sf, ef],
                "reason": f"{relation}:{ch_role} anchored via {ch_prec}",
                "desired_state": f"{ch_prim} active -> settled at f{ch_settle}",
                "sound_anchors": sound_anchors,
            })
            # Canvas-space ROI for real rendered internal motion QA (CMI-09)
            roi_x = round(bbox[0] + ch_geom[0] * bbox[2], 4)
            roi_y = round(bbox[1] + ch_geom[1] * bbox[3], 4)
            roi_w = round(max(0.02, ch_geom[2] * bbox[2]), 4)
            roi_h = round(max(0.02, ch_geom[3] * bbox[3]), 4)
            node_bindings.append({
                "node_id": ch_nid,
                "component_id": cid,
                "dom_selector": f"#{ch_nid}",
                "parent_selector": f"#{cid}",
                "canvas_roi_norm": [roi_x, roi_y, roi_w, roi_h],
                "start_frame": ch_sf,
                "settle_frame": ch_settle,
                "end_frame": ef,
                "primitive": ch_prim,
            })
            for kf in ch_kfs:
                state_trace_rows.append({
                    "node_id": ch_nid,
                    "component_id": cid,
                    "frame": kf["frame"],
                    "state": kf.get("state_label", ""),
                    "opacity": kf["opacity"],
                    "scale": kf["scale"],
                    "progress": kf.get("progress", 0.0),
                })

        # Register verifiable claim/evidence tokens on root node for CMI-03 shared-element identity!
        for tok in claim_refs + ev_refs:
            if tok and not tok.startswith("rel:"):
                claim_node_history.setdefault(tok, []).append(root_node)

    # CMI-03: Shared-element morphing across adjacent beats ONLY when claim/evidence binding matches!
    shared_elements: list[dict[str, Any]] = []
    se_counter = 0
    for tok in sorted(claim_node_history.keys()):
        chain = sorted(claim_node_history[tok], key=lambda n: int(n["start_frame"]))
        for i in range(len(chain) - 1):
            src_n = chain[i]
            dst_n = chain[i + 1]
            if src_n["beat_id"] == dst_n["beat_id"]:
                continue
            # Adjacent or closely following semantic handoff (prevent >4 frame blank gap!)
            gap_f = int(dst_n["start_frame"]) - int(src_n["end_frame"])
            if gap_f > int(round(fps * 1.5)):
                continue
            se_counter += 1
            se_id = f"shared_{se_counter:02d}_{sanitize_identifier(tok[:24])}"
            src_n["shared_element_id"] = se_id
            src_n["end_state"] = "MORPH_HANDOFF"
            dst_n["shared_element_id"] = se_id
            m_start = max(int(src_n["start_frame"]) + 2, int(src_n["end_frame"]) - max(4, int(round(fps * 0.25))))
            m_end = min(int(dst_n["end_frame"]) - 1, max(m_start + 4, int(dst_n["start_frame"]) + max(4, int(round(fps * 0.25)))))
            sg = src_n["geometry_norm"]
            dg = dst_n["geometry_norm"]
            sx0 = min(sg[0], dg[0])
            sy0 = min(sg[1], dg[1])
            sx1 = max(sg[0] + sg[2], dg[0] + dg[2])
            sy1 = max(sg[1] + sg[3], dg[1] + dg[3])
            swept = [round(sx0, 4), round(sy0, 4), round(sx1 - sx0, 4), round(sy1 - sy0, 4)]
            own_mode = "SINGLE_DOM_REUSE"

            # Route shared-element handoff around protected face/mouth masks (e.g. during mirror split flips!)
            mid_f = (m_start + m_end) // 2
            lbeats = (layout_plan or {}).get("beats") or []
            active_lb = next((x for x in lbeats if int(x["start_frame"]) <= mid_f < int(x["end_frame"])), None)
            if active_lb:
                fm_boxes = [pm["bbox_norm"] for pm in (active_lb.get("protected_masks_norm") or []) if pm.get("zone") in ("face", "mouth")]
                if any(_rects_overlap(swept, fb) for fb in fm_boxes):
                    min_fy = min(float(fb[1]) for fb in fm_boxes)
                    max_fy2 = max(float(fb[1]) + float(fb[3]) for fb in fm_boxes)
                    if min_fy >= 0.10:
                        # Clean upper safe-band arc strictly above face/mouth
                        swept = [round(sx0, 4), 0.02, round(max(0.10, sx1 - sx0), 4), round(max(0.05, min(0.10, min_fy - 0.035)), 4)]
                    elif max_fy2 <= 0.88:
                        # Clean lower safe-band arc strictly below face/mouth
                        swept = [round(sx0, 4), round(max_fy2 + 0.02, 4), round(max(0.10, sx1 - sx0), 4), round(max(0.05, min(0.10, 0.98 - max_fy2 - 0.02)), 4)]
                    own_mode = "MATCHED_CROSSFADE_HANDOFF"

            shared_elements.append({
                "shared_element_id": se_id,
                "source_node_id": src_n["node_id"],
                "target_node_id": dst_n["node_id"],
                "from_beat_id": src_n["beat_id"],
                "to_beat_id": dst_n["beat_id"],
                "evidence_or_claim_binding": tok,
                "start_frame": m_start,
                "end_frame": m_end,
                "source_geometry_norm": sg,
                "target_geometry_norm": dg,
                "swept_bounds_norm": swept,
                "z_order": max(int(src_n["z_order"]), int(dst_n["z_order"])) + 2,
                "ownership_mode": own_mode,
                "no_duplicate_at_endpoint": True,
                "min_overlap_visibility": 0.45,
            })

    graph = {
        "schema_version": "kh03.motion_graph.v1",
        "run_id": run_id,
        "selected_skill_id": skill_id,
        "clock_domain": "SOURCE_CLOCK",
        "time_map_sha256": None,
        "fps_num": int(fps_num),
        "fps_den": int(fps_den),
        "total_frames": int(total_frames),
        "reduced_motion_policy": "REDUCED_MOTION" if reduced_motion else "STANDARD",
        "nodes": nodes,
        "semantic_motion_events": sem_events,
        "shared_elements": shared_elements,
        "graph_sha256": "0" * 64,
    }
    graph.pop("graph_sha256", None)
    graph["graph_sha256"] = hash_artifact(graph)
    assert_kh03_schema(graph, "motion_graph")

    # CMI-03 morph semantic coverage & blank-gap check across adjacent component seams
    seam_cov = verify_morph_semantic_coverage(graph, layout_plan=layout_plan, run_dir=run_dir)

    if run_dir is not None:
        mdir = run_dir / "manifests"
        mdir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(mdir / "MOTION_GRAPH.json", graph)
        write_json_atomic(mdir / "COMPONENT_NODE_BINDINGS.json", {"schema_version": "kh03.component_node_bindings.v1", "bindings": node_bindings})
        write_json_atomic(mdir / "SEMANTIC_MOTION_EVENTS.json", {"schema_version": "kh03.semantic_motion_events.v1", "events": sem_events})
        (mdir / "COMPONENT_STATE_TRACE.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in state_trace_rows), encoding="utf-8",
        )
        write_json_atomic(mdir / "SHARED_ELEMENT_GRAPH.json", {"schema_version": "kh03.shared_element_graph.v1", "shared_elements": shared_elements})
        write_json_atomic(mdir / "MORPH_PLAN.json", {
            "schema_version": "kh03.morph_plan.v1",
            "clock_domain": graph["clock_domain"],
            "shared_elements": shared_elements,
            "layout_transitions": (layout_plan or {}).get("transitions") or [],
        })
        # Streamed per-frame transform trace for shared elements (bounded)
        with (mdir / "MOTION_TRANSFORM_TRACE.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["shared_element_id", "frame", "u", "x", "y", "w", "h", "src_opacity", "dst_opacity"])
            w.writeheader()
            for se in shared_elements:
                s0, s1 = int(se["start_frame"]), int(se["end_frame"])
                span = max(1, s1 - s0)
                sg, dg = se["source_geometry_norm"], se["target_geometry_norm"]
                for f in range(s0, s1 + 1):
                    u = (f - s0) / float(span)
                    eu = 3 * u * u - 2 * u * u * u
                    w.writerow({
                        "shared_element_id": se["shared_element_id"],
                        "frame": f,
                        "u": round(eu, 4),
                        "x": round(sg[0] + (dg[0] - sg[0]) * eu, 4),
                        "y": round(sg[1] + (dg[1] - sg[1]) * eu, 4),
                        "w": round(sg[2] + (dg[2] - sg[2]) * eu, 4),
                        "h": round(sg[3] + (dg[3] - sg[3]) * eu, 4),
                        "src_opacity": round(max(0.0, 1.0 - eu), 4),
                        "dst_opacity": round(min(1.0, eu), 4),
                    })

    return {
        "motion_graph": graph,
        "node_bindings": node_bindings,
        "morph_coverage": seam_cov,
    }


def _rects_overlap(a: list[float], b: list[float]) -> bool:
    return not (a[0] + a[2] <= b[0] + 1e-5 or b[0] + b[2] <= a[0] + 1e-5 or a[1] + a[3] <= b[1] + 1e-5 or b[1] + b[3] <= a[1] + 1e-5)


def verify_morph_semantic_coverage(
    motion_graph: dict[str, Any],
    *,
    layout_plan: dict[str, Any] | None = None,
    max_unintended_blank_gap_frames: int = 0,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """CMI-03 seam auditor: detects 1-4f blank semantic gaps, premature fades, duplicates & face collisions during morph!"""
    nodes = [n for n in motion_graph.get("nodes", []) if n.get("parent_id") is None]
    nodes.sort(key=lambda n: (int(n["start_frame"]), n["node_id"]))
    shared = list(motion_graph.get("shared_elements") or [])
    layout_beats = (layout_plan or {}).get("beats") or []

    issues: list[str] = []
    blank_gaps: list[dict[str, Any]] = []
    collisions: list[dict[str, Any]] = []

    # 1. Check adjacent root components for unintended blank semantic gaps (1..4 frames or longer)
    for i in range(len(nodes) - 1):
        a, b = nodes[i], nodes[i + 1]
        gap = int(b["start_frame"]) - int(a["end_frame"])
        if gap > max_unintended_blank_gap_frames and gap <= 12:
            # Flag 1-12 frame unintentional blank flash gaps!
            blank_gaps.append({
                "from_node": a["node_id"],
                "to_node": b["node_id"],
                "gap_frames": gap,
                "gap_span": [int(a["end_frame"]), int(b["start_frame"])],
            })
            issues.append(f"BLANK_SEMANTIC_GAP({a['node_id']}->{b['node_id']}): {gap}f empty gap at [{a['end_frame']},{b['start_frame']})")

    # 2. Check every shared-element morph for endpoint duplication, premature fade, & swept face/hand collision
    for se in shared:
        if not se.get("no_duplicate_at_endpoint", True):
            issues.append(f"SHARED_ELEMENT_DUPLICATE({se['shared_element_id']}): duplicate element at morph endpoint")
        if float(se.get("min_overlap_visibility", 0.45)) < 0.25:
            issues.append(f"PREMATURE_FADE({se['shared_element_id']}): overlap visibility below 0.25 floor")
        sw = se.get("swept_bounds_norm") or se["source_geometry_norm"]
        mid_f = (int(se["start_frame"]) + int(se["end_frame"])) // 2
        lb = next((x for x in layout_beats if int(x["start_frame"]) <= mid_f < int(x["end_frame"])), None)
        if lb:
            for pm in lb.get("protected_masks_norm") or []:
                if pm.get("zone") in ("face", "mouth") and _rects_overlap(sw, pm["bbox_norm"]):
                    collisions.append({"shared_element_id": se["shared_element_id"], "zone": pm["zone"], "frame": mid_f})
                    issues.append(f"MORPH_FACE_COLLISION({se['shared_element_id']}): swept morph intersects {pm['zone']} at f{mid_f}")

    rep = {
        "schema_version": "kh03.morph_semantic_coverage.v1",
        "clock_domain": motion_graph.get("clock_domain"),
        "root_components_checked": len(nodes),
        "shared_elements_checked": len(shared),
        "blank_gaps": blank_gaps,
        "morph_collisions": collisions,
        "issues": issues,
        "pass": len(issues) == 0,
    }
    if run_dir is not None:
        qdir = run_dir / "qc"
        qdir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(qdir / "MORPH_SEMANTIC_COVERAGE.json", rep)
        write_json_atomic(qdir / "MORPH_SEAM_REPORT.json", {
            "schema_version": "kh03.morph_seam_report.v1",
            "pass": rep["pass"],
            "blank_gap_count": len(blank_gaps),
            "collision_count": len(collisions),
            "issues": issues,
        })
    return rep


def remap_motion_graph_to_edl(
    motion_graph: dict[str, Any],
    edl: dict[str, Any],
    *,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Single-pass exact rational EDL remapper for CMI-02/03 nodes, keyframes, shared elements & sound anchors.

    Fail-closed: rejects double-remap (`DOUBLE_REMAP_FORBIDDEN`). Explicitly drops events inside removed pauses.
    """
    if motion_graph.get("clock_domain") == "OUTPUT_EDL_MAPPED":
        raise ValueError("DOUBLE_REMAP_FORBIDDEN: motion_graph is already OUTPUT_EDL_MAPPED.")

    segs = list(edl.get("segments") or [])
    total_out = int(edl["total_output_frames"])
    fps_num = int(edl["fps_num"])
    fps_den = int(edl["fps_den"])
    edl_sha = hash_artifact(edl)

    def _map_span(sf: int, ef: int) -> tuple[int, int] | None:
        lo: int | None = None
        hi: int | None = None
        for seg in segs:
            s0, s1 = int(seg["src_start_frame"]), int(seg["src_end_frame"])
            o0 = int(seg["out_start_frame"])
            ov_s, ov_e = max(sf, s0), min(ef, s1)
            if ov_e > ov_s:
                ms, me = o0 + (ov_s - s0), o0 + (ov_e - s0)
                lo = ms if lo is None else min(lo, ms)
                hi = me if hi is None else max(hi, me)
        if lo is None or hi is None or hi <= lo:
            return None
        return max(0, lo), min(total_out, hi)

    def _map_frame(f: int) -> int:
        for seg in segs:
            s0, s1 = int(seg["src_start_frame"]), int(seg["src_end_frame"])
            o0 = int(seg["out_start_frame"])
            if s0 <= f < s1:
                return max(0, min(total_out - 1, o0 + (f - s0)))
            if f < s0:
                return max(0, min(total_out - 1, o0))
        return max(0, total_out - 1)

    new_nodes: list[dict[str, Any]] = []
    surviving_ids: set[str] = set()
    for nd in motion_graph.get("nodes", []):
        span = _map_span(int(nd["start_frame"]), int(nd["end_frame"]))
        if span is None:
            continue
        os0, oe0 = span
        nnd = dict(nd)
        nnd["src_start_frame"] = int(nd["start_frame"])
        nnd["src_end_frame"] = int(nd["end_frame"])
        nnd["start_frame"] = os0
        nnd["end_frame"] = oe0
        nnd["min_read_dwell_frames"] = min(max(1, oe0 - os0), int(nd["min_read_dwell_frames"]))
        nnd["keyframes"] = [
            {**kf, "frame": max(os0, min(oe0 - 1, _map_frame(int(kf["frame"]))))}
            for kf in nd.get("keyframes", [])
        ]
        new_nodes.append(nnd)
        surviving_ids.add(nnd["node_id"])

    new_events: list[dict[str, Any]] = []
    for ev in motion_graph.get("semantic_motion_events", []):
        if ev["node_id"] not in surviving_ids:
            continue
        span = _map_span(int(ev["src_interval"][0]), int(ev["src_interval"][1]))
        if span is None:
            continue
        nev = dict(ev)
        nev["out_interval"] = [span[0], span[1]]
        nev["sound_anchors"] = [
            {**sa, "frame": max(span[0], min(max(span[0], span[1] - 1), _map_frame(int(sa["frame"]))))}
            for sa in ev.get("sound_anchors", [])
        ]
        new_events.append(nev)

    new_shared: list[dict[str, Any]] = []
    for se in motion_graph.get("shared_elements", []):
        if se["source_node_id"] not in surviving_ids or se["target_node_id"] not in surviving_ids:
            continue
        span = _map_span(int(se["start_frame"]), int(se["end_frame"]))
        if span is None:
            continue
        nse = dict(se)
        nse["start_frame"] = span[0]
        nse["end_frame"] = max(span[0] + 1, span[1])
        new_shared.append(nse)

    out_g = dict(motion_graph)
    out_g["clock_domain"] = "OUTPUT_EDL_MAPPED"
    out_g["time_map_sha256"] = edl_sha
    out_g["fps_num"] = fps_num
    out_g["fps_den"] = fps_den
    out_g["total_frames"] = total_out
    out_g["nodes"] = new_nodes
    out_g["semantic_motion_events"] = new_events
    out_g["shared_elements"] = new_shared
    out_g.pop("graph_sha256", None)
    out_g["graph_sha256"] = hash_artifact(out_g)
    assert_kh03_schema(out_g, "motion_graph")

    if run_dir is not None:
        write_json_atomic(run_dir / "manifests" / "MOTION_GRAPH.json", out_g)
        write_json_atomic(run_dir / "manifests" / "SEMANTIC_MOTION_EVENTS.json", {"schema_version": "kh03.semantic_motion_events.v1", "events": new_events})
        write_json_atomic(run_dir / "manifests" / "SHARED_ELEMENT_GRAPH.json", {"schema_version": "kh03.shared_element_graph.v1", "shared_elements": new_shared})
    return out_g


# ======================================================================
# CMI-07 — MULTI-LEVEL VISUAL DENSITY, ART DIRECTION & ANTI-REPETITION
# ======================================================================


def evaluate_visual_density_and_repetition(
    *,
    run_id: str,
    layout_plan: dict[str, Any],
    creative_plan: dict[str, Any],
    motion_graph: dict[str, Any] | None = None,
    type_system: dict[str, Any] | None = None,
    justified_read_holds: list[str] | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Duration-weighted CMI-07 visual density, repetition & hold auditor (catches subtle loops + preserves calm holds!)."""
    beats = layout_plan.get("beats") or []
    comps = creative_plan.get("components") or []
    skill_id = str(layout_plan.get("selected_skill_id") or creative_plan.get("selected_skill_id") or "SK-04")
    pol = str(layout_plan.get("layout_policy") or "LEGACY_DYNAMIC")
    fps = float(Fraction(int(layout_plan.get("fps_num", 24)), max(1, int(layout_plan.get("fps_den", 1)))))
    total_f = max(1, int(layout_plan.get("total_frames") or sum(max(1, int(b["end_frame"]) - int(b["start_frame"])) for b in beats)))
    dur_sec = total_f / max(1.0, fps)
    justified_set = set(justified_read_holds or [])
    justified_continuity = (pol == "FULLSCREEN_LOCK") or (skill_id == "SK-05") or (dur_sec >= 90.0 and skill_id == "SK-03")

    # 1. Duration-weighted topology residence & "top-banner except one beat" loop detector!
    mode_frames: dict[str, int] = {}
    top_banner_frames = 0
    for b in beats:
        df = max(1, int(b["end_frame"]) - int(b["start_frame"]))
        m = str(b["layout_mode"])
        mode_frames[m] = mode_frames.get(m, 0) + df
        gb = b.get("graphic_box_norm") or [0, 0, 1, 1]
        if m == "FULL_CANVAS" and float(gb[1]) < 0.25 and float(gb[3]) < 0.28:
            top_banner_frames += df
    top_banner_ratio = round(top_banner_frames / float(total_f), 4)
    dominant_mode_ratio = round(max(mode_frames.values(), default=total_f) / float(total_f), 4)

    # 2. Component shape/family & visual-appearance repetition (even if component IDs differ!)
    appearances: dict[str, int] = {}
    kind_frames: dict[str, int] = {}
    tiny_text_large_panel: list[str] = []
    unjustified_static_holds: list[dict[str, Any]] = []
    justified_holds_accepted: list[dict[str, Any]] = []
    mg_nodes_by_comp: dict[str, list[dict[str, Any]]] = {}
    for nd in ((motion_graph or {}).get("nodes") or []):
        mg_nodes_by_comp.setdefault(str(nd["component_id"]), []).append(nd)

    total_graphic_area_weighted = 0.0
    motion_events_per_sec = 0.0
    if motion_graph:
        motion_events_per_sec = round(len(motion_graph.get("semantic_motion_events") or []) / max(1.0, dur_sec), 3)

    for c in comps:
        cid = str(c["component_id"])
        df = max(1, int(c["end_frame"]) - int(c["start_frame"]))
        c_sec = df / max(1.0, fps)
        kind = str(c.get("kind") or "unknown")
        kind_frames[kind] = kind_frames.get(kind, 0) + df
        bb = c.get("normalized_bbox") or [0, 0, 0.5, 0.2]
        area = float(bb[2]) * float(bb[3])
        total_graphic_area_weighted += area * (df / float(total_f))

        # Appearance fingerprint: (kind, rounded bbox bucket, easing) — catches different IDs with identical visual look!
        fp = f"{kind}:{round(bb[0],1)},{round(bb[1],1)},{round(bb[2],1)},{round(bb[3],1)}:{c.get('easing')}"
        appearances[fp] = appearances.get(fp, 0) + 1

        # Tiny text inside huge empty panel check
        params = c.get("params") or {}
        fs = float(params.get("font_size_px") or 26)
        text_len = len(str(params.get("headline") or "")) + len(str(params.get("subtitle") or ""))
        if area >= 0.30 and fs < 22 and text_len < 30 and not c.get("asset_ref"):
            tiny_text_large_panel.append(cid)

        # Static hold check (>= 5.5s with <=1 internal motion state unless explicitly justified reading hold or calm skill)
        ch_nodes = [n for n in mg_nodes_by_comp.get(cid, []) if n.get("parent_id") is not None]
        is_justified_hold = (cid in justified_set) or (c.get("beat_id") in justified_set) or justified_continuity or bool(params.get("justified_reading_hold"))
        if c_sec >= 5.5 and len(ch_nodes) <= 1 and not is_justified_hold:
            unjustified_static_holds.append({
                "component_id": cid,
                "beat_id": c.get("beat_id"),
                "duration_sec": round(c_sec, 2),
                "internal_nodes": len(ch_nodes),
                "reason": "Long static component (>=5.5s) lacking progressive internal semantic state changes.",
            })
        elif c_sec >= 5.5 and is_justified_hold:
            justified_holds_accepted.append({
                "component_id": cid,
                "duration_sec": round(c_sec, 2),
                "reason": "Justified reading hold or deliberate calm/fullscreen continuity.",
            })

    # 3. Over-animation clutter check (too many rapid visual motion events/sec on short canvas)
    clutter_ceiling = {"SK-01": 4.2, "SK-02": 3.4, "SK-03": 2.4, "SK-04": 3.6, "SK-05": 2.0}.get(skill_id, 3.5)
    over_animated = motion_events_per_sec > clutter_ceiling

    findings: list[str] = []
    # Flag top-banner loop even if ONE beat deviates (duration-weighted >= 80% top-banner under ADAPTIVE_DYNAMIC!)
    is_banner_loop = (len(beats) >= 3 and top_banner_ratio >= 0.78)
    unjustified_banner_loop = bool(is_banner_loop and not justified_continuity and pol == "ADAPTIVE_DYNAMIC")
    if unjustified_banner_loop:
        findings.append(f"DURATION_WEIGHTED_TOP_BANNER_LOOP: {top_banner_ratio * 100:.1f}% of duration pinned to top-banner FULL_CANVAS under ADAPTIVE_DYNAMIC.")
    repeated_clone_fps = {k: v for k, v in appearances.items() if v >= 4 and len(comps) >= 4}
    if repeated_clone_fps and not justified_continuity:
        findings.append(f"REPEATED_IDENTICAL_APPEARANCE: {len(repeated_clone_fps)} repeated component shape/pose clone(s) >=4x.")
    if unjustified_static_holds:
        findings.append(f"UNJUSTIFIED_STATIC_HOLD: {len(unjustified_static_holds)} component(s) static >=5.5s without semantic progression.")
    if tiny_text_large_panel:
        findings.append(f"TINY_TEXT_IN_EMPTY_PANEL: {tiny_text_large_panel}")
    if over_animated:
        findings.append(f"OVER_ANIMATION_CLUTTER: {motion_events_per_sec} motion events/sec exceeds {skill_id} ceiling {clutter_ceiling}.")

    neg_space_ratio = round(max(0.0, 1.0 - min(0.95, total_graphic_area_weighted)), 4)
    density_ok = (len(findings) == 0)

    density_rep = {
        "schema_version": "kh03.visual_density_report.v1",
        "run_id": run_id,
        "selected_skill_id": skill_id,
        "layout_policy": pol,
        "duration_sec": round(dur_sec, 3),
        "mean_graphic_occupancy_ratio": round(total_graphic_area_weighted, 4),
        "negative_space_ratio": neg_space_ratio,
        "motion_events_per_sec": motion_events_per_sec,
        "motion_clutter_ceiling": clutter_ceiling,
        "over_animated": over_animated,
        "tiny_text_large_panel": tiny_text_large_panel,
        "duration_weighted_top_banner_ratio": top_banner_ratio,
        "dominant_topology_residence_ratio": dominant_mode_ratio,
        "justified_continuity": justified_continuity,
        "findings": findings,
        "pass": density_ok,
    }
    comp_rep = {
        "schema_version": "kh03.component_repetition_report.v1",
        "run_id": run_id,
        "component_count": len(comps),
        "unique_kinds": sorted(kind_frames.keys()),
        "kind_residence_ratio": {k: round(v / float(total_f), 4) for k, v in sorted(kind_frames.items())},
        "repeated_identical_appearance_clones": repeated_clone_fps,
        "pass": not (bool(repeated_clone_fps) and not justified_continuity),
    }
    hold_rep = {
        "schema_version": "kh03.visual_hold_audit.v1",
        "run_id": run_id,
        "unjustified_static_holds": unjustified_static_holds,
        "justified_holds_accepted": justified_holds_accepted,
        "pass": len(unjustified_static_holds) == 0,
    }
    neg_map = {
        "schema_version": "kh03.negative_space_map.v1",
        "run_id": run_id,
        "overall_negative_space_ratio": neg_space_ratio,
        "per_beat": [
            {
                "beat_id": b["beat_id"],
                "layout_mode": b["layout_mode"],
                "graphic_area": round(float(b["graphic_box_norm"][2]) * float(b["graphic_box_norm"][3]), 4),
                "presenter_area": round(float(b["presenter_box_norm"][2]) * float(b["presenter_box_norm"][3]), 4),
            }
            for b in beats
        ],
    }

    if run_dir is not None:
        qdir = run_dir / "qc"
        qdir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(qdir / "VISUAL_DENSITY_REPORT.json", density_rep)
        write_json_atomic(qdir / "COMPONENT_REPETITION_REPORT.json", comp_rep)
        write_json_atomic(qdir / "VISUAL_HOLD_AUDIT.json", hold_rep)
        write_json_atomic(qdir / "NEGATIVE_SPACE_MAP.json", neg_map)
        art = creative_plan.get("art_direction") or {}
        art_md = [
            f"# Art Direction & Visual Density Audit — `{run_id}` (`{skill_id}`)",
            "",
            f"- **Theme / Grammar**: `{art.get('theme')}` / `{art.get('motion_grammar')}`",
            f"- **Layout Policy**: `{pol}` | **Top-Banner Residence**: `{top_banner_ratio * 100:.1f}%` | **Negative Space**: `{neg_space_ratio * 100:.1f}%`",
            f"- **Motion Load**: `{motion_events_per_sec} events/sec` (skill ceiling `{clutter_ceiling}`) | **Pass**: `{density_ok}`",
            f"- **WCAG Contrast**: `{(type_system or {}).get('wcag_contrast_ratio', 'N/A')}`",
            "",
            "## Findings",
        ] + ([f"- {f}" for f in findings] if findings else ["- Balanced hierarchy, spacing, and visual variety within selected skill."])
        (run_dir / "manifests" / "ART_DIRECTION.md").write_text("\n".join(art_md) + "\n", encoding="utf-8")

    return {
        "visual_density_report": density_rep,
        "component_repetition_report": comp_rep,
        "visual_hold_audit": hold_rep,
        "negative_space_map": neg_map,
        "pass": density_ok,
    }
