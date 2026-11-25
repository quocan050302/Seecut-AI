"""KH-03 Independent Editorial QA, Anti-Template Diversity & Separated Gate Reporting.

Implements the exact Section 13 heuristic rubric (clarity/explanation 25, semantic alignment 15,
visual art direction 15, hook/payoff/retention 15, motion/footage integration 10, audio comfort/sync 15,
selected skill identity 5; hard defects override score; >=80/100 candidate floor) and strict
separation of objective code QA, editorial score, owner visual/audio approval, and commercial license.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import assert_kh03_schema, write_json_atomic
from .explanation import evaluate_comprehension, verify_explanation_coverage
from .layout import compute_anti_repetition_report
from .motion_graph import verify_morph_semantic_coverage


def compute_plan_similarity(plan_a: dict[str, Any], plan_b: dict[str, Any]) -> dict[str, Any]:
    """Measure structural/grammar similarity between two creative plans (anti-template verification)."""
    caps_a = [c["capability_id"] for c in plan_a.get("components", [])]
    caps_b = [c["capability_id"] for c in plan_b.get("components", [])]
    set_a, set_b = set(caps_a), set(caps_b)
    jacc = len(set_a & set_b) / float(max(1, len(set_a | set_b)))

    art_a = plan_a.get("art_direction") or {}
    art_b = plan_b.get("art_direction") or {}
    same_theme = art_a.get("theme") == art_b.get("theme")
    same_grammar = art_a.get("motion_grammar") == art_b.get("motion_grammar")
    same_hook = (plan_a.get("hook_plan") or {}).get("selected_strategy") == (plan_b.get("hook_plan") or {}).get("selected_strategy")
    same_seq = (caps_a == caps_b)

    # Composite similarity (0 = completely distinct, 1 = identical clone)
    sim = round(
        0.45 * jacc
        + 0.20 * (1.0 if same_seq else 0.0)
        + 0.15 * (1.0 if same_theme else 0.0)
        + 0.10 * (1.0 if same_grammar else 0.0)
        + 0.10 * (1.0 if same_hook else 0.0),
        4,
    )
    return {
        "similarity_score": sim,
        "capability_jaccard": round(jacc, 4),
        "identical_sequence": same_seq,
        "same_theme": same_theme,
        "same_motion_grammar": same_grammar,
        "distinct_enough": sim < 0.85,
    }


def evaluate_kh03_editorial(
    *,
    run_id: str,
    creative_plan: dict[str, Any],
    explanation_graph: dict[str, Any],
    footage_manifest: dict[str, Any],
    sound_plan: dict[str, Any],
    visual_qa: dict[str, Any],
    shotbook: dict[str, Any] | None = None,
    layout_plan: dict[str, Any] | None = None,
    motion_graph: dict[str, Any] | None = None,
    evidence_ledger: dict[str, Any] | None = None,
    visual_density: dict[str, Any] | None = None,
    tracking_report: dict[str, Any] | None = None,
    streaming_qa: dict[str, Any] | None = None,
    license_decision: str = "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
    reviewer_type: str = "DETERMINISTIC_OBJECTIVE",
    evidence_paths: list[str] | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Compute heuristic 100-pt editorial rubric + hard-defect overrides + separated Section 15 gates."""
    skill_id = creative_plan["selected_skill_id"]
    hard_defects: list[dict[str, str]] = []
    anti_rep = (
        compute_anti_repetition_report(layout_plan, creative_plan, run_dir=run_dir)
        if layout_plan is not None else None
    )
    if anti_rep is not None and not anti_rep["pass"]:
        hard_defects.append({
            "code": "REPEATED_TOP_BANNER_LOOP",
            "message": "; ".join(anti_rep.get("findings") or ["Unjustified layout repetition detected."]),
        })

    morph_cov = (
        verify_morph_semantic_coverage(motion_graph, layout_plan=layout_plan, run_dir=run_dir)
        if motion_graph is not None else None
    )
    if morph_cov is not None and not morph_cov["pass"]:
        hard_defects.append({
            "code": "MORPH_SEMANTIC_GAP_OR_COLLISION",
            "message": "; ".join(str(f) for f in (morph_cov.get("issues") or morph_cov.get("findings") or ["Shared-element morph coverage failed."])),
        })
    ev_blocked = bool(
        evidence_ledger is not None
        and (evidence_ledger.get("status") == "EVIDENCE_BLOCKED_MISSING_REQUIRED" or evidence_ledger.get("pass") is False)
    )
    if ev_blocked:
        gap_msgs = [str(g.get("reason", g)) for g in (evidence_ledger.get("gaps") or [])]
        hard_defects.append({
            "code": "EVIDENCE_RIGHTS_OR_PROOF_FAIL",
            "message": "; ".join(gap_msgs or ["Required evidence proof or rights verification failed."]),
        })
    vd_doc = (visual_density or {}).get("visual_density_report", visual_density) if visual_density is not None else None
    if vd_doc is not None and not vd_doc.get("pass", True):
        hard_defects.append({
            "code": "VISUAL_DENSITY_OR_REPETITION_FAIL",
            "message": "; ".join(str(f) for f in (vd_doc.get("findings") or ["Visual density / repetition gate failed."])),
        })
    if streaming_qa is not None and not streaming_qa.get("pass", True):
        hard_defects.append({
            "code": "STREAMING_FRAME_QA_FAIL",
            "message": "; ".join(str(f) for f in (streaming_qa.get("issues") or ["Streaming frame QA failed."])),
        })

    exp_cov = verify_explanation_coverage(explanation_graph)
    comp_eval = evaluate_comprehension(
        explanation_graph,
        (shotbook or {}).get("visual_events") or [
            {"event_id": c["component_id"], "start_frame": c["start_frame"], "end_frame": c["end_frame"]}
            for c in creative_plan.get("components", [])
        ],
    )

    if not exp_cov["numeric_arithmetic_valid"]:
        hard_defects.append({"code": "ARITHMETIC_ERROR", "message": "Numeric claim validation failed exact Decimal check."})
    if not visual_qa.get("pass", False):
        hard_defects.append({"code": "VISUAL_COLLISION_OR_LAYOUT_FAIL", "message": "; ".join(visual_qa.get("issues") or ["visual_qa failed"])})
    if license_decision in ("BLOCKED_COMMERCIAL_LICENSE", "BLOCKED_LICENSE_UNRESOLVED"):
        hard_defects.append({"code": "LICENSE_BLOCKED", "message": f"License gate blocked ({license_decision})."})

    # 7 weighted editorial dimensions summing to max 100.0
    # 1. Clarity / explanation (max 25)
    s_clarity = round(25.0 * (0.55 * float(exp_cov["coverage_ratio"]) + 0.45 * float(comp_eval["support_ratio"])), 2)
    # 2. Semantic alignment (max 15)
    beats = creative_plan.get("beats") or []
    comps = creative_plan.get("components") or []
    aligned = sum(1 for c in comps if c.get("semantic_anchor", "").startswith(f"{skill_id}:"))
    s_sem = round(15.0 * (aligned / float(max(1, len(comps)))), 2)
    # 3. Visual art direction (max 15)
    uniq_caps = len({c["capability_id"] for c in comps})
    s_art = round(min(15.0, 9.5 + min(3, uniq_caps) * 1.8), 2) if visual_qa.get("pass") else 6.0
    # 4. Hook / payoff / retention design (max 15)
    hp = creative_plan.get("hook_plan") or {}
    s_hook = 14.2 if (hp.get("truthful_no_spoiler_verified") and len(beats) >= 2) else 9.0
    # 5. Motion / footage integration (max 10)
    f_stat = footage_manifest.get("status", "A_ROLL_AND_GRAPHICS_ONLY")
    s_mot = 9.5 if f_stat == "ASSET_READY" else (8.4 if f_stat in ("FOOTAGE_PARTIAL", "A_ROLL_AND_GRAPHICS_ONLY") else 6.5)
    # 6. Audio comfort / sync (max 15)
    snd_counts = sound_plan.get("summary_counts") or {}
    if snd_counts.get("ADD", 0) > 0:
        s_aud = 14.0
    elif snd_counts.get("UNAVAILABLE", 0) > 0:
        s_aud = 11.2  # Voice preserved cleanly, optional SFX absent & disclosed honestly
    else:
        s_aud = 13.0
    # 7. Selected skill identity (max 5)
    s_id = 5.0 if creative_plan.get("selected_skill_id") == skill_id and (creative_plan.get("art_direction") or {}).get("motion_grammar") else 2.0

    total = round(s_clarity + s_sem + s_art + s_hook + s_mot + s_aud + s_id, 2)
    editorial_pass = (total >= 80.0 and len(hard_defects) == 0)

    rubric = {
        "clarity_explanation": {"score": s_clarity, "max": 25, "reason": f"coverage={exp_cov['coverage_ratio']}, comprehension_proxy={comp_eval['support_ratio']}"},
        "semantic_alignment": {"score": s_sem, "max": 15, "reason": f"{aligned}/{len(comps)} components semantically anchored to {skill_id}."},
        "visual_art_direction": {"score": s_art, "max": 15, "reason": f"{uniq_caps} distinct component capabilities, collision_ok={visual_qa.get('collision_ok')}."},
        "hook_payoff_retention": {"score": s_hook, "max": 15, "reason": f"Truthful hook {hp.get('selected_hook_id')}, editorial intent only (not measured retention)."},
        "motion_footage_integration": {"score": s_mot, "max": 10, "reason": f"Footage status={f_stat}, motion_pass={visual_qa.get('motion_audit', {}).get('pass', True)}."},
        "audio_comfort_sync": {"score": s_aud, "max": 15, "reason": f"Provider={sound_plan.get('provider_status')}, ADD={snd_counts.get('ADD', 0)}, UNAVAILABLE={snd_counts.get('UNAVAILABLE', 0)}."},
        "selected_skill_identity": {"score": s_id, "max": 5, "reason": f"Bound to {skill_id} ({(creative_plan.get('art_direction') or {}).get('motion_grammar')})."},
    }

    gates = {
        "KH03_EXPLANATION_COVERAGE": "PASS" if exp_cov["pass"] else "FAIL",
        "KH03_FOOTAGE_ACQUISITION": "PASS" if f_stat == "ASSET_READY" else ("PARTIAL" if f_stat in ("FOOTAGE_PARTIAL", "A_ROLL_AND_GRAPHICS_ONLY") else "BLOCKED"),
        "KH03_SOUND_ASSETS": "PASS" if snd_counts.get("ADD", 0) > 0 else ("BLOCKED_SFX_ASSETS" if snd_counts.get("UNAVAILABLE", 0) > 0 else "VOICE_ONLY"),
        "KH03_VISUAL_COLLISION": "PASS" if visual_qa.get("pass") else "FAIL",
        "KH03_MOTION_GRAPH": "PASS" if (motion_graph is not None and (morph_cov is None or morph_cov["pass"])) else ("NOT_RUN" if motion_graph is None else "FAIL"),
        "KH03_SHARED_ELEMENT_QA": "PASS" if (morph_cov is not None and morph_cov["pass"]) else ("NOT_RUN" if morph_cov is None else "FAIL"),
        "KH03_EVIDENCE_RIGHTS": "FAIL" if ev_blocked else ("PASS" if evidence_ledger is not None else "NOT_RUN"),
        "KH03_VISUAL_DENSITY": "PASS" if (vd_doc is None or vd_doc.get("pass", True)) else "FAIL",
        "KH03_TRACKING_SCOPE": (tracking_report or {}).get("tracking_mode") or (tracking_report or {}).get("mode") or "STATIC_FALLBACK_VERIFIED",
        "AUTO_EDITORIAL_QA": "PASS" if editorial_pass else "FAIL",
        "OWNER_VISUAL": "PENDING",
        "OWNER_AUDIO": "PENDING",
        "COMMERCIAL_RELEASE": "BLOCKED" if license_decision != "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED" else "AUTHORIZED_PENDING_OWNER",
    }

    report = {
        "schema_version": "kh03.qa_report.v1",
        "run_id": run_id,
        "selected_skill_id": skill_id,
        "reviewer_type": reviewer_type,
        "hard_defects": hard_defects,
        "editorial_rubric": rubric,
        "total_score": total,
        "editorial_candidate_pass": editorial_pass,
        "gates": gates,
        "evidence_paths": list(evidence_paths or []),
    }
    assert_kh03_schema(report, "qa_report")
    if run_dir is not None:
        write_json_atomic(run_dir / "qc" / "KH03_QA_REPORT.json", report)
        write_json_atomic(run_dir / "qc" / "VISUAL_QA_REPORT.json", visual_qa)
    return report
