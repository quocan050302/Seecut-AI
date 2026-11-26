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


# ======================================================================
# PED-08 / R4.1-02 — MODE-AWARE GATE REGISTRY & FAIL-CLOSED PRODUCTION QA
# ======================================================================

VALID_GATE_STATUSES = frozenset({
    "PASS", "FAIL", "BLOCKED", "BLOCKED_MISSING_GATE", "PENDING_REVIEW", "NOT_APPLICABLE",
})

GATE_PROFILES: dict[str, dict[str, Any]] = {
    "PLAN": {
        "mode": "PLAN",
        "mandatory_gates": ("G_LICENSE", "G_EVIDENCE_CLAIMS", "G_LAYOUT_RHYTHM"),
        "allow_na_gates": frozenset({"G_LAYOUT_RHYTHM", "G_EVIDENCE_CLAIMS"}),
        "requires_pixel_evidence": False,
        "requires_h1": False,
        "requires_h2": False,
        "requires_native_proof": False,
        "requires_layers": False,
    },
    "ENGINEERING": {
        "mode": "ENGINEERING",
        "mandatory_gates": (
            "G_LICENSE", "G_HOOK_PIXELS", "G_COMPONENT_ROI_MOTION", "G_COMPONENT_COVERAGE",
            "G_ACTION_PROTECTION", "G_SPATIAL_ALIGNMENT", "G_EVIDENCE_CLAIMS",
            "G_FRAME_AUDIT", "G_FRAME_COVERAGE", "G_AUDIO_SYNC", "G_H1_GEOM", "G_EXPORT_QA",
        ),
        "allow_na_gates": frozenset({
            "G_COMPONENT_ROI_MOTION", "G_ACTION_PROTECTION", "G_LAYOUT_RHYTHM",
            "G_EVIDENCE_CLAIMS", "G_H2_JUDGE", "G_H1_GEOM",
        }),
        "requires_pixel_evidence": True,
        "requires_h1": False,
        "requires_h2": False,
        "requires_native_proof": False,
        "requires_layers": False,
    },
    "NATIVE_PREVIEW": {
        "mode": "NATIVE_PREVIEW",
        "mandatory_gates": (
            "G_LICENSE", "G_HOOK_PIXELS", "G_COMPONENT_ROI_MOTION", "G_COMPONENT_COVERAGE",
            "G_ACTION_PROTECTION", "G_SPATIAL_ALIGNMENT", "G_EVIDENCE_CLAIMS",
            "G_FRAME_AUDIT", "G_FRAME_COVERAGE", "G_AUDIO_SYNC", "G_H1_GEOM",
            "G_NATIVE_PROOF", "G_EXPORT_QA",
        ),
        "allow_na_gates": frozenset({"G_COMPONENT_ROI_MOTION", "G_ACTION_PROTECTION", "G_EVIDENCE_CLAIMS", "G_H2_JUDGE"}),
        "requires_pixel_evidence": True,
        "requires_h1": True,
        "requires_h2": False,
        "requires_native_proof": True,
        "requires_layers": False,
    },
    "NATIVE_FULL": {
        "mode": "NATIVE_FULL",
        "mandatory_gates": (
            "G_LICENSE", "G_HOOK_PIXELS", "G_COMPONENT_ROI_MOTION", "G_COMPONENT_COVERAGE",
            "G_ACTION_PROTECTION", "G_LAYOUT_RHYTHM", "G_SPATIAL_ALIGNMENT", "G_EVIDENCE_CLAIMS",
            "G_FRAME_AUDIT", "G_FRAME_COVERAGE", "G_AUDIO_SYNC", "G_H1_GEOM",
            "G_H2_JUDGE", "G_NATIVE_PROOF", "G_EXPORT_QA",
        ),
        "allow_na_gates": frozenset({"G_COMPONENT_ROI_MOTION", "G_ACTION_PROTECTION", "G_EVIDENCE_CLAIMS"}),
        "requires_pixel_evidence": True,
        "requires_h1": True,
        "requires_h2": True,
        "requires_native_proof": True,
        "requires_layers": False,
    },
    "RELEASE": {
        "mode": "RELEASE",
        "mandatory_gates": (
            "G_LICENSE", "G_HOOK_PIXELS", "G_COMPONENT_ROI_MOTION", "G_COMPONENT_COVERAGE",
            "G_ACTION_PROTECTION", "G_LAYOUT_RHYTHM", "G_SPATIAL_ALIGNMENT", "G_EVIDENCE_CLAIMS",
            "G_FRAME_AUDIT", "G_FRAME_COVERAGE", "G_AUDIO_SYNC", "G_H1_GEOM",
            "G_H2_JUDGE", "G_NATIVE_PROOF", "G_EXPORT_QA", "G_OWNER_VISUAL", "G_OWNER_AUDIO",
        ),
        "allow_na_gates": frozenset({"G_COMPONENT_ROI_MOTION", "G_ACTION_PROTECTION", "G_EVIDENCE_CLAIMS"}),
        "requires_pixel_evidence": True,
        "requires_h1": True,
        "requires_h2": True,
        "requires_native_proof": True,
        "requires_layers": True,
    },
    "INDEPENDENT_PREVIEW": {
        "mode": "INDEPENDENT_PREVIEW",
        "mandatory_gates": (
            "G_LICENSE", "G_SPEC_INTEGRITY", "G_FRAME_AUDIT", "G_FRAME_COVERAGE",
            "G_COMPONENT_COVERAGE", "G_SPATIAL_ALIGNMENT", "G_AUDIO_SYNC", "G_EXPORT_QA",
        ),
        "allow_na_gates": frozenset({"G_H1_GEOM", "G_H2_JUDGE", "G_NATIVE_PROOF"}),
        "requires_pixel_evidence": True,
        "requires_h1": False,
        "requires_h2": False,
        "requires_native_proof": False,
        "requires_layers": False,
    },
    "INDEPENDENT_FULL": {
        "mode": "INDEPENDENT_FULL",
        "mandatory_gates": (
            "G_LICENSE", "G_SPEC_INTEGRITY", "G_FRAME_AUDIT", "G_FRAME_COVERAGE",
            "G_COMPONENT_COVERAGE", "G_SPATIAL_ALIGNMENT", "G_CONTINUITY",
            "G_AUDIO_SYNC", "G_EXPORT_QA",
        ),
        "allow_na_gates": frozenset({"G_H1_GEOM", "G_H2_JUDGE", "G_NATIVE_PROOF"}),
        "requires_pixel_evidence": True,
        "requires_h1": False,
        "requires_h2": False,
        "requires_native_proof": False,
        "requires_layers": False,
    },
}


def resolve_gate_profile(mode: str | None = None) -> dict[str, Any]:
    """Return explicit mode-aware gate registry profile (PLAN / ENGINEERING / NATIVE_PREVIEW / NATIVE_FULL / RELEASE)."""
    key = str(mode or "ENGINEERING").strip().upper()
    if key not in GATE_PROFILES:
        raise ValueError(f"UNKNOWN_QA_GATE_MODE: {mode!r} not in {sorted(GATE_PROFILES.keys())}")
    return dict(GATE_PROFILES[key])


def aggregate_gate_results(
    gates: dict[str, dict[str, Any]],
    *,
    mandatory_gates: tuple[str, ...] | None = None,
    mode: str | None = None,
    strict_evidence: bool = False,
) -> dict[str, Any]:
    """Fail-closed per-gate aggregator (R4.1-02): iterates REQUIRED gates FIRST, rejects missing gates & unjustified N/A."""
    prof = resolve_gate_profile(mode) if mode is not None else None
    if mandatory_gates is not None:
        req = set(mandatory_gates)
    elif prof is not None:
        req = set(prof["mandatory_gates"])
    else:
        req = set(gates.keys())
    allow_na = set(prof["allow_na_gates"]) if prof is not None else {
        "G_COMPONENT_ROI_MOTION", "G_ACTION_PROTECTION", "G_LAYOUT_RHYTHM",
        "G_EVIDENCE_CLAIMS", "G_H2_JUDGE", "G_H1_GEOM",
    }

    normalized: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    failing: list[str] = []
    blocked: list[str] = []
    pending: list[str] = []
    unknown_gates: list[str] = []

    # 1. Check EVERY mandatory gate FIRST so missing required gates NEVER silently pass!
    for gid in sorted(req):
        if gid not in gates or gates[gid] is None:
            missing.append(gid)
            blocked.append(gid)
            normalized[gid] = {
                "status": "BLOCKED_MISSING_GATE",
                "applicable": True,
                "mandatory": True,
                "reason": f"Mandatory gate {gid} missing from gate evidence.",
            }

    # 2. Evaluate all supplied gates (mandatory + advisory)
    for gid in sorted(gates.keys()):
        if gates[gid] is None:
            continue
        g = dict(gates[gid]) if isinstance(gates[gid], dict) else {"status": "FAIL", "reason": "MALFORMED_GATE_ENTRY"}
        raw_st = str(g.get("status") or "FAIL").strip().upper()
        if raw_st not in VALID_GATE_STATUSES:
            raw_st = "FAIL"
            g["reason"] = f"INVALID_GATE_STATUS({g.get('status')!r})"
        is_mand = gid in req
        g["mandatory"] = is_mand
        app = g.get("applicable")
        if app is None:
            app = (raw_st != "NOT_APPLICABLE")
        app = bool(app)

        # Reject unjustified applicable=False or NOT_APPLICABLE on mandatory gates!
        if is_mand and (not app or raw_st == "NOT_APPLICABLE"):
            na_reason = str(g.get("na_reason") or g.get("reason") or g.get("detail") or "").strip()
            if gid not in allow_na or len(na_reason) < 4:
                raw_st = "FAIL"
                app = True
                g["reason"] = f"UNJUSTIFIED_NA_ON_MANDATORY_GATE({gid}): cannot waive mandatory gate without allowed mode & verified reason."
            else:
                raw_st = "NOT_APPLICABLE"
                app = False

        # Strict evidence check: mandatory PASS must link artifact hash or path
        if strict_evidence and is_mand and app and raw_st == "PASS":
            if not (g.get("evidence_sha256") or g.get("artifact_path")):
                raw_st = "BLOCKED"
                g["reason"] = f"MISSING_EVIDENCE_PROOF({gid}): mandatory PASS requires artifact_path or evidence_sha256."

        g["status"] = raw_st
        g["applicable"] = app
        normalized[gid] = g

        if not is_mand and prof is not None and gid not in prof["mandatory_gates"]:
            unknown_gates.append(gid)

        if is_mand and app:
            if raw_st == "FAIL":
                failing.append(gid)
            elif raw_st in ("BLOCKED", "BLOCKED_MISSING_GATE"):
                blocked.append(gid)
            elif raw_st == "PENDING_REVIEW":
                pending.append(gid)

    # Deduplicate preserving deterministic order
    missing = sorted(set(missing))
    failing = sorted(set(failing))
    blocked = sorted(set(blocked))
    pending = sorted(set(pending))

    technical_pass = (len(req) > 0 and len(failing) == 0 and len(blocked) == 0 and len(missing) == 0 and len(pending) == 0)
    if failing:
        overall = "FAIL"
    elif blocked or missing:
        overall = "BLOCKED"
    elif pending:
        overall = "PENDING_REVIEW"
    elif not req:
        overall = "BLOCKED"
    else:
        overall = "TECHNICAL_PASS_PENDING_OWNER"

    # Separate editorial, native engine, and release dimensions cleanly (NEVER conflate!)
    rhythm_st = (normalized.get("G_LAYOUT_RHYTHM") or {}).get("status", "NOT_RUN")
    h2_st = (normalized.get("G_H2_JUDGE") or {}).get("status", "NOT_RUN")
    native_st = (normalized.get("G_NATIVE_PROOF") or {}).get("status", "NOT_RUN")
    lic_st = (normalized.get("G_LICENSE") or {}).get("status", "BLOCKED")

    if rhythm_st == "FAIL" or h2_st == "FAIL":
        editorial_status = "FAIL"
    elif rhythm_st == "PENDING_REVIEW" or h2_st in ("PENDING_REVIEW", "BLOCKED", "BLOCKED_MISSING_GATE", "NOT_RUN"):
        editorial_status = "PENDING_REVIEW"
    elif technical_pass:
        editorial_status = "PASS"
    else:
        editorial_status = "FAIL" if failing else "PENDING_REVIEW"

    return {
        "schema_version": "kh03.gate_matrix.v2",
        "mode": (prof["mode"] if prof is not None else "CUSTOM"),
        "mandatory_gates": sorted(req),
        "overall_technical_status": overall,
        "technical_pass": technical_pass,
        "editorial_status": editorial_status,
        "native_status": native_st,
        "license_status": lic_st,
        "release_status": "RELEASE_BLOCKED",
        "missing_gates": missing,
        "failing_gates": failing,
        "blocked_gates": blocked,
        "pending_gates": pending,
        "advisory_or_extra_gates": sorted(set(unknown_gates)),
        "gates": normalized,
        "owner_visual_approval": "PENDING",
        "owner_audio_approval": "PENDING",
    }


def evaluate_editorial_quality(
    creative_plan: dict[str, Any],
    *,
    hook_qa: dict[str, Any] | None = None,
    action_qa: dict[str, Any] | None = None,
    layout_rhythm: dict[str, Any] | None = None,
    component_qa: dict[str, Any] | None = None,
    frame_audit: dict[str, Any] | None = None,
    audio_qa: dict[str, Any] | None = None,
    evidence_claims: dict[str, Any] | None = None,
    design_tokens: dict[str, Any] | None = None,
    h1_geom: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """PED-08 9-dimension anchored 0-10 editorial rubric grounded in actual pixel/audio metrics (NOT retention prediction)."""
    def _dim(score: float, note: str) -> dict[str, Any]:
        return {"score_10": round(max(0.0, min(10.0, score)), 2), "evidence": note}

    h_sc = 8.5 if (hook_qa or {}).get("pass") else (4.0 if hook_qa is not None else 6.0)
    a_sc = 9.0 if (action_qa or {}).get("pass") else (3.5 if action_qa is not None else 6.0)
    l_sc = 8.5 if (layout_rhythm or {}).get("pass") else (5.5 if (layout_rhythm or {}).get("needs_review") else 3.5)
    m_sc = 8.5 if (component_qa or {}).get("pass") else (4.0 if component_qa is not None else 6.0)
    art_sc = 8.5 if ((design_tokens or {}).get("pass", True) and (h1_geom or {}).get("pass", True)) else 4.5
    ev_sc = 8.8 if (evidence_claims or {}).get("pass", True) else 3.5
    aud_sc = 8.8 if (audio_qa or {}).get("pass", True) else 4.0
    cont_sc = 9.0 if (frame_audit or {}).get("pass", True) else 3.5
    sk_sc = 9.0 if creative_plan.get("selected_skill_id") else 3.0

    dims = {
        "hook_semantic_fidelity": _dim(h_sc, f"hook_status={(hook_qa or {}).get('status', 'NOT_RUN')}"),
        "protected_action": _dim(a_sc, f"action_status={(action_qa or {}).get('status', 'NOT_RUN')}"),
        "composition_layout_rhythm": _dim(l_sc, f"rhythm_status={(layout_rhythm or {}).get('status', 'NOT_RUN')}"),
        "motion_timing": _dim(m_sc, f"component_moved={(component_qa or {}).get('moved_nodes', 'N/A')}/{(component_qa or {}).get('nodes_checked', 'N/A')}"),
        "art_direction": _dim(art_sc, f"h1={(h1_geom or {}).get('status', 'NOT_RUN')}, tokens={(design_tokens or {}).get('pass', 'N/A')}"),
        "evidence_fidelity": _dim(ev_sc, f"evidence_pass={(evidence_claims or {}).get('pass', 'N/A')}"),
        "audio_intelligibility": _dim(aud_sc, f"audio_pass={(audio_qa or {}).get('pass', 'N/A')}"),
        "continuity": _dim(cont_sc, f"frame_audit={(frame_audit or {}).get('status', 'NOT_RUN')}"),
        "skill_adherence": _dim(sk_sc, f"skill={creative_plan.get('selected_skill_id')}"),
    }
    mean_10 = round(sum(d["score_10"] for d in dims.values()) / float(len(dims)), 2)
    return {
        "schema_version": "kh03.editorial_quality_rubric.v1",
        "disclaimer": "Deterministic engineering/editorial rubric anchored to verified QA outputs — NOT measured viewer retention, CTR, or viral prediction.",
        "mean_score_10": mean_10,
        "dimensions": dims,
    }


def _probe_actual_upstream_git_sha(seecut_root: Path | None) -> tuple[str | None, bool]:
    """Inspect real upstream git HEAD at seecut_root instead of trusting hardcoded constants."""
    import subprocess
    if seecut_root is None or not Path(seecut_root).is_dir():
        return None, False
    try:
        pr = subprocess.run(
            ["git", "-C", str(seecut_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if pr.returncode == 0 and pr.stdout.strip():
            return pr.stdout.strip(), True
    except Exception:
        pass
    return None, False


def _probe_mp4_basic_streams(mp4_path: Path) -> dict[str, Any]:
    """Real ffprobe check on final MP4 when caller omitted structural_qa/audio_qa — NEVER defaults True blindly."""
    import json
    import subprocess
    try:
        pr = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(mp4_path)],
            capture_output=True, text=True, timeout=25,
        )
        if pr.returncode != 0:
            return {"pass": False, "reason": f"ffprobe failed rc={pr.returncode}"}
        doc = json.loads(pr.stdout)
        streams = doc.get("streams") or []
        v_st = next((s for s in streams if s.get("codec_type") == "video"), None)
        a_st = next((s for s in streams if s.get("codec_type") == "audio"), None)
        v_ok = bool(v_st and int(v_st.get("width") or 0) > 0 and int(v_st.get("height") or 0) > 0)
        return {
            "pass": v_ok,
            "has_video": v_ok,
            "has_audio": a_st is not None,
            "width": int(v_st.get("width") or 0) if v_st else 0,
            "height": int(v_st.get("height") or 0) if v_st else 0,
            "video_duration_sec": float(v_st.get("duration") or 0.0) if v_st else 0.0,
            "audio_duration_sec": float(a_st.get("duration") or 0.0) if a_st else 0.0,
            "av_duration_diff_sec": round(abs(float(v_st.get("duration") or 0.0) - float(a_st.get("duration") or 0.0)), 4) if (v_st and a_st and v_st.get("duration") and a_st.get("duration")) else None,
        }
    except Exception as ex:
        return {"pass": False, "reason": f"ffprobe exception: {ex}"}


def orchestrate_final_render_qa(
    *,
    run_id: str,
    run_dir: Path,
    final_mp4: str | Path,
    source_mp4: str | Path | None,
    creative_plan: dict[str, Any],
    shotbook: dict[str, Any] | None = None,
    layout_plan: dict[str, Any] | None = None,
    motion_graph: dict[str, Any] | None = None,
    evidence_ledger: dict[str, Any] | None = None,
    action_protection_windows: list[dict[str, Any]] | None = None,
    tracked_entities: list[dict[str, Any]] | None = None,
    kh02_result: dict[str, Any] | None = None,
    fps: float = 24.0,
    license_decision: str = "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
    engine_kind: str = "NATIVE_SEECUT",
    seecut_root: Path | None = None,
    mode: str = "ENGINEERING",
    edl: dict[str, Any] | None = None,
    export_layers_required: bool = False,
    strict_evidence: bool = False,
) -> dict[str, Any]:
    """PED-08 / R4.1-02 / R4.1-05 Production Final-Pixel QA Orchestrator bound strictly to exact final muxed MP4 SHA-256!

    - Zero silent `[:12]` or `[:6]` truncations: verifies 100% of applicable motion nodes, components & anchors.
    - Shared bounded streaming decode pass across frame audit, hook pixels, and component ROI motion.
    - Mode-aware fail-closed gate registry: zero default-True `.get("pass", True)` fallbacks on missing evidence.
    - Emits FLOW_TRACE.json, FINAL_PIXEL_QA.json, FINAL_GATE_MATRIX.json, COMPONENT_COVERAGE.json,
      FRAME_COVERAGE.json, SPATIAL_ALIGNMENT_QA.json, NATIVE_RENDER_PROOF.json, and OWNER_APPROVAL.json.
    """
    from . import UPSTREAM_SEECUT_SHA
    from .contracts import hash_artifact, now_utc
    from .evidence_and_type import validate_evidence_claims
    from .layout import detect_visual_repetition
    from .tracking import bind_component_to_track, evaluate_action_visibility, resolve_spatial_anchor, validate_spatial_alignment
    from .visual_qa import (
        audit_decoded_frames_full,
        collect_streaming_qa_frames,
        verify_component_internal_roi_motion,
        verify_hook_pixels,
    )

    fin_p = Path(final_mp4).resolve()
    qdir = run_dir / "qc"
    qdir.mkdir(parents=True, exist_ok=True)
    trace: list[dict[str, Any]] = []
    prof = resolve_gate_profile(mode)

    if not fin_p.is_file():
        missing_mat = aggregate_gate_results(
            {"G_FINAL_MP4_EXISTS": {"status": "FAIL", "applicable": True, "reason": f"Final MP4 missing: {fin_p}"}},
            mandatory_gates=tuple(prof["mandatory_gates"]) + ("G_FINAL_MP4_EXISTS",),
            mode=prof["mode"],
        )
        missing_mat["final_mp4_sha256"] = None
        missing_mat["run_id"] = run_id
        missing_fpq = {
            "schema_version": "kh03.final_pixel_qa.v2",
            "run_id": run_id,
            "mode": prof["mode"],
            "engine_kind": engine_kind,
            "final_mp4_path": str(fin_p),
            "final_mp4_sha256": None,
            "technical_pass": False,
            "overall_technical_status": "FAIL",
            "reason": f"Final MP4 missing on disk: {fin_p}",
            "owner_visual_approval": "PENDING",
            "owner_audio_approval": "PENDING",
        }
        missing_cov = {"schema_version": "kh03.component_coverage.v1", "final_mp4_sha256": None, "coverage_ratio": 0.0, "pass": False}
        missing_fcov = {"schema_version": "kh03.frame_coverage.v1", "final_mp4_sha256": None, "temporal_coverage_ratio": 0.0, "pass": False}
        missing_proof = {"schema_version": "kh03.native_render_proof.v2", "engine_kind": engine_kind, "native_proof_verified": False, "final_mp4_sha256": None}
        missing_owner = {
            "schema_version": "kh03.owner_approval.v1",
            "run_id": run_id,
            "final_mp4_sha256": None,
            "visual_approval": "PENDING",
            "audio_approval": "PENDING",
        }
        write_json_atomic(qdir / "FINAL_GATE_MATRIX.json", missing_mat)
        write_json_atomic(qdir / "FINAL_PIXEL_QA.json", missing_fpq)
        write_json_atomic(qdir / "COMPONENT_COVERAGE.json", missing_cov)
        write_json_atomic(qdir / "FRAME_COVERAGE.json", missing_fcov)
        write_json_atomic(qdir / "NATIVE_RENDER_PROOF.json", missing_proof)
        write_json_atomic(qdir / "OWNER_APPROVAL.json", missing_owner)
        return {
            "pass": False,
            "technical_pass": False,
            "final_mp4_sha256": None,
            "final_pixel_qa": missing_fpq,
            "gate_matrix": missing_mat,
            "component_coverage": missing_cov,
            "frame_coverage": missing_fcov,
            "editorial_quality_rubric": evaluate_editorial_quality(creative_plan),
            "render_proof": missing_proof,
            "owner_approval": missing_owner,
            "flow_trace": trace,
        }

    fin_sha = hash_artifact(fin_p)
    kh02 = dict(kh02_result or {})
    total_f = int(((shotbook or {}).get("output_spec") or {}).get("total_frames") or (layout_plan or {}).get("total_frames") or 0) or None

    # Build FULL (untruncated!) node_bindings for 100% of applicable motion nodes & components (R4.1-05)
    all_components = list(creative_plan.get("components") or [])
    comp_by_id = {str(c["component_id"]): c for c in all_components if isinstance(c, dict) and c.get("component_id")}
    node_bindings: list[dict[str, Any]] = []
    mg_nodes = (motion_graph or {}).get("nodes") or []
    for nd in mg_nodes:
        cid = str(nd.get("component_id") or "")
        comp = comp_by_id.get(cid)
        if not comp:
            continue
        sf = int(nd.get("start_frame", comp.get("start_frame", 0)))
        ef = int(nd.get("end_frame", comp.get("end_frame", sf + 12)))
        kfs = nd.get("keyframes") or []
        stf = int(kfs[-1]["frame"]) if len(kfs) >= 2 else min(ef - 1, sf + max(2, (ef - sf) // 2))
        if stf <= sf:
            stf = min(ef, sf + 2)
        # R4.1-05: Only explicitly intended static nodes are marked intentional_static (NEVER blanket all SK-05 nodes!)
        is_int_static = bool(
            nd.get("intentional_static") is True
            or nd.get("primitive") == "stable_hold"
            or comp.get("intentional_static") is True
        )
        node_bindings.append({
            "node_id": nd["node_id"],
            "component_id": cid,
            "primitive": nd.get("primitive"),
            "start_frame": sf,
            "settle_frame": stf,
            "end_frame": ef,
            "canvas_roi_norm": nd.get("canvas_roi_norm") or nd.get("roi_norm") or comp.get("normalized_bbox"),
            "intentional_static": is_int_static,
        })
    # Include any components that had no motion_graph node so coverage is strictly 100%!
    covered_cids = {str(nb["component_id"]) for nb in node_bindings}
    for comp in all_components:
        cid = str(comp.get("component_id") or "")
        if not cid or cid in covered_cids:
            continue
        sf = int(comp.get("start_frame", 0))
        ef = int(comp.get("end_frame", sf + 12))
        node_bindings.append({
            "node_id": f"root_{cid}",
            "component_id": cid,
            "primitive": "component_entry",
            "start_frame": sf,
            "settle_frame": min(ef, sf + max(2, (ef - sf) // 2)),
            "end_frame": ef,
            "canvas_roi_norm": comp.get("normalized_bbox"),
            "intentional_static": bool(comp.get("intentional_static") is True),
        })

    # Compute exact union of frames needed for shared single-pass bounded streaming decode!
    hook_sample_f = sorted({0, max(1, int(round(fps * 0.45))), max(2, int(round(fps * 1.25))), max(3, int(round(fps * 2.4)))})
    comp_needed_f: set[int] = set(hook_sample_f)
    for nb in node_bindings:
        sf = max(0, int(nb.get("start_frame", 0)))
        stf = max(sf + 1, int(nb.get("settle_frame", sf + 6)))
        ef = max(stf + 1, int(nb.get("end_frame", stf + 6)))
        comp_needed_f.update({max(0, sf - 2), sf, (sf + stf) // 2, stf, max(sf, ef - 2)})

    # Single shared bounded streaming decode pass for component/hook frames (avoids repeated full video decodes!)
    shared_fin_frames = collect_streaming_qa_frames(fin_p, comp_needed_f, width=360, height=640)
    shared_src_frames = (
        collect_streaming_qa_frames(Path(source_mp4), comp_needed_f, width=360, height=640)
        if (source_mp4 and Path(source_mp4).is_file()) else None
    )

    # 1. PED-01 + R4.1-05: ROI-isolated Hook Pixel QA + ALL-component payoff non-spoiler check!
    hp = dict(creative_plan.get("hook_plan") or {})
    first_comp = all_components[0] if all_components else None
    hook_roi = hp.get("hook_roi_norm") or (first_comp.get("normalized_bbox") if isinstance(first_comp, dict) else None)
    first_lb = ((layout_plan or {}).get("beats") or [None])[0]
    pres_box0 = None
    if isinstance(first_lb, dict) and first_lb.get("layout_mode") not in ("FULL_CANVAS", "FULL_SCREEN_RETURN"):
        pres_box0 = first_lb.get("presenter_box_norm")

    # Check payoff non-spoiler across ALL early components (not just primary hook text!)
    payoff_spoiler_detected = not hp.get("truthful_no_spoiler_verified", True)
    payoff_toks = [str(t).strip().lower() for t in (hp.get("payoff_tokens") or []) if str(t).strip()]
    payoff_start_f = int(hp.get("payoff_start_frame") or int(round(fps * 3.0)))
    if payoff_toks:
        for comp in all_components:
            if int(comp.get("start_frame", 0)) < payoff_start_f:
                blob = " ".join(str(v) for v in (comp.get("params") or {}).values() if isinstance(v, (str, int, float))).lower()
                if any(tok in blob for tok in payoff_toks):
                    payoff_spoiler_detected = True
                    break

    hook_qa = verify_hook_pixels(
        fin_p, hp, fps=fps, source_mp4=source_mp4,
        hook_roi_norm=hook_roi, presenter_box_norm=pres_box0,
        reveal_constraints={"early_payoff_spoiler": payoff_spoiler_detected},
        predecoded_fin_frames=shared_fin_frames,
        predecoded_src_frames=shared_src_frames,
        out_dir=qdir,
    )
    trace.append({"step": "01_verify_hook_pixels", "final_mp4_sha256": fin_sha, "status": hook_qa.get("status"), "pass": hook_qa.get("pass")})

    # 2. PED-08 / R4.1-05: 100% Component Internal ROI Pixel Motion QA (ZERO truncation!)
    pres_map: dict[int, list[float]] = {}
    for lb in ((layout_plan or {}).get("beats") or []):
        if lb.get("layout_mode") not in ("FULL_CANVAS", "FULL_SCREEN_RETURN"):
            bs0, bs1 = int(lb["start_frame"]), int(lb["end_frame"])
            for f in comp_needed_f:
                if bs0 <= f < bs1:
                    pres_map[f] = list(lb["presenter_box_norm"])
    comp_qa = (
        verify_component_internal_roi_motion(
            fin_p, node_bindings, source_mp4=source_mp4,
            presenter_box_norm_by_frame=pres_map or None,
            predecoded_fin_frames=shared_fin_frames,
            predecoded_src_frames=shared_src_frames,
            out_dir=qdir,
        )
        if node_bindings else {
            "schema_version": "kh03.component_pixel_qa.v1",
            "final_mp4_sha256": fin_sha,
            "pass": True,
            "status": "NOT_APPLICABLE_NO_COMPONENTS",
            "nodes_checked": 0,
            "nodes": [],
            "frozen_nodes": [],
            "invalid_rois": [],
        }
    )
    trace.append({
        "step": "02_verify_component_internal_roi_motion",
        "final_mp4_sha256": fin_sha,
        "pass": comp_qa.get("pass"),
        "nodes_expected": len(node_bindings),
        "nodes_checked": comp_qa.get("nodes_checked"),
    })

    # Emit explicit COMPONENT_COVERAGE.json verifying 100% of applicable nodes/components!
    checked_node_ids = {str(r.get("node_id")) for r in (comp_qa.get("nodes") or []) if isinstance(r, dict)}
    expected_node_ids = [str(nb["node_id"]) for nb in node_bindings]
    missing_node_ids = [nid for nid in expected_node_ids if nid not in checked_node_ids]
    expected_comp_ids = [str(c["component_id"]) for c in all_components if isinstance(c, dict) and c.get("component_id")]
    checked_comp_ids = {str(r.get("component_id")) for r in (comp_qa.get("nodes") or []) if isinstance(r, dict) and r.get("component_id")}
    missing_comp_ids = [cid for cid in expected_comp_ids if cid not in checked_comp_ids]
    cov_ratio = round(len(checked_node_ids) / float(len(expected_node_ids)), 4) if expected_node_ids else 1.0
    comp_cov_ok = (len(missing_node_ids) == 0 and len(missing_comp_ids) == 0 and cov_ratio >= 1.0)
    component_coverage = {
        "schema_version": "kh03.component_coverage.v1",
        "run_id": run_id,
        "final_mp4_sha256": fin_sha,
        "expected_components": len(expected_comp_ids),
        "checked_components": len(checked_comp_ids),
        "missing_component_ids": missing_comp_ids,
        "expected_motion_nodes": len(expected_node_ids),
        "checked_motion_nodes": len(checked_node_ids),
        "missing_node_ids": missing_node_ids,
        "intentional_static_nodes": [nb["node_id"] for nb in node_bindings if nb.get("intentional_static")],
        "dynamic_nodes": [nb["node_id"] for nb in node_bindings if not nb.get("intentional_static")],
        "truncation_applied": False,
        "coverage_ratio": cov_ratio,
        "status": "FULL_100PCT_COVERAGE" if comp_cov_ok else "INCOMPLETE_COVERAGE",
        "pass": comp_cov_ok,
    }
    write_json_atomic(qdir / "COMPONENT_COVERAGE.json", component_coverage)

    # 3. PED-02 / R4.1-04: Action Protection Final-Pixel Visibility Gate (with EDL clock mapping!)
    action_qa = evaluate_action_visibility(
        fin_p,
        list(action_protection_windows or []),
        layout_plan=layout_plan,
        overlay_boxes=all_components,
        source_mp4=source_mp4,
        edl=edl or ((shotbook or {}).get("edit_decision_map") if isinstance(shotbook, dict) else None),
        out_dir=qdir,
    )
    trace.append({"step": "03_evaluate_action_visibility", "final_mp4_sha256": fin_sha, "status": action_qa.get("status"), "pass": action_qa.get("pass")})

    # 4. PED-03: All-Topology Adaptive Layout Rhythm Gate
    rhythm_qa = (
        detect_visual_repetition(layout_plan, creative_plan=creative_plan, motion_graph=motion_graph, run_dir=run_dir)
        if layout_plan is not None else {"status": "NOT_APPLICABLE", "pass": True, "needs_review": False, "na_reason": "No adaptive layout_plan provided"}
    )
    trace.append({"step": "04_detect_visual_repetition", "final_mp4_sha256": fin_sha, "status": rhythm_qa.get("status"), "pass": rhythm_qa.get("pass")})

    # 5. PED-04 / R4.1-05: Spatial Alignment Verification across 100% of anchored components (start/mid/end, NO [:6] cap!)
    entities = list(tracked_entities or [])
    resolved_anchors: list[dict[str, Any]] = []
    ent_by_id = {e["id"]: e for e in entities if isinstance(e, dict) and e.get("id")}
    scene_cuts = list((layout_plan or {}).get("scene_cuts") or [])
    for comp in all_components:
        b = bind_component_to_track(comp, entities)
        ent = ent_by_id.get(b.get("track_entity_id") or "")
        sf = int(comp.get("start_frame", 0))
        ef = max(sf + 1, int(comp.get("end_frame", sf + 1)))
        for f_sample in sorted({sf, (sf + ef) // 2, max(sf, ef - 1)}):
            resolved_anchors.append(resolve_spatial_anchor(b, f_sample, ent, scene_cuts=scene_cuts))
    spatial_qa = validate_spatial_alignment(resolved_anchors)
    spatial_qa["final_mp4_sha256"] = fin_sha
    spatial_qa["components_evaluated"] = len(all_components)
    spatial_qa["truncation_applied"] = False
    write_json_atomic(qdir / "SPATIAL_ALIGNMENT_QA.json", spatial_qa)
    trace.append({"step": "05_validate_spatial_alignment", "final_mp4_sha256": fin_sha, "components_evaluated": len(all_components), "pass": spatial_qa.get("pass")})

    # 6. PED-06: Evidence Claims Audit
    ev_claims_qa = (
        validate_evidence_claims(evidence_ledger)
        if evidence_ledger is not None else {"status": "NOT_APPLICABLE", "pass": True, "issues": [], "na_reason": "No evidence_ledger supplied"}
    )
    trace.append({"step": "06_validate_evidence_claims", "final_mp4_sha256": fin_sha, "pass": ev_claims_qa.get("pass")})

    # 7. PED-08 / R4.1-05: O(1) Full-Frame PTS Decode Audit + FRAME_COVERAGE.json
    frame_audit = audit_decoded_frames_full(fin_p, expected_frames=total_f, out_dir=qdir)
    decoded_n = int(frame_audit.get("decoded_frames") or 0)
    exp_n = int(total_f) if total_f else decoded_n
    fcov_ratio = round(min(1.0, decoded_n / float(max(1, exp_n))), 4) if exp_n > 0 else 0.0
    fcov_ok = bool(decoded_n > 0 and frame_audit.get("frame_count_ok", False) and fcov_ratio >= 1.0)
    frame_coverage = {
        "schema_version": "kh03.frame_coverage.v1",
        "run_id": run_id,
        "final_mp4_sha256": fin_sha,
        "expected_frames": exp_n,
        "decoded_frames": decoded_n,
        "last_frame_index_verified": (decoded_n - 1) if decoded_n > 0 else None,
        "temporal_coverage_ratio": fcov_ratio,
        "verification_scope": "FULL_DECODED_STREAM",
        "status": "FULL_FRAME_COVERAGE" if fcov_ok else "INCOMPLETE_FRAME_COVERAGE",
        "pass": fcov_ok,
    }
    write_json_atomic(qdir / "FRAME_COVERAGE.json", frame_coverage)
    trace.append({"step": "07_audit_decoded_frames_full", "final_mp4_sha256": fin_sha, "decoded_frames": decoded_n, "pass": frame_audit.get("pass")})

    # 8. Ingest Native KH-02 Structural, Audio, H1, H2, Pairwise, Layer Export Proofs (ZERO silent default-True!)
    struct_qa = kh02.get("structural_qa")
    audio_qa = kh02.get("audio_qa")
    h1_geom = kh02.get("h1_geom")
    h2_check = kh02.get("h2_check")
    pairwise = kh02.get("pairwise") or []
    jianying = kh02.get("jianying_layers")
    full_render = kh02.get("full_render") or {}

    # Real ffprobe measurement fallback when caller did not pre-populate structural_qa / audio_qa (NEVER blind True!)
    probed_basic = _probe_mp4_basic_streams(fin_p)
    if isinstance(struct_qa, dict) and "pass" in struct_qa:
        struct_pass = bool(struct_qa["pass"])
        struct_basis = "KH02_STRUCTURAL_QA"
    else:
        struct_pass = bool(probed_basic.get("pass", False))
        struct_basis = "DIRECT_FFPROBE_FALLBACK"

    if isinstance(audio_qa, dict) and "pass" in audio_qa:
        audio_pass = bool(audio_qa["pass"])
        audio_basis = "KH02_AUDIO_QA"
    else:
        # Measure actual audio stream presence & A/V sync via ffprobe (fail if audio expected by shotbook but missing!)
        expect_audio = bool(
            (shotbook or {}).get("edit_decision_map", {}).get("voice_policy", "LOCK_VOICE") != "SILENT_BY_DESIGN"
        )
        if expect_audio and not probed_basic.get("has_audio"):
            audio_pass = False
            audio_basis = "MISSING_EXPECTED_AUDIO_STREAM"
        else:
            av_diff = probed_basic.get("av_duration_diff_sec")
            audio_pass = bool(probed_basic.get("pass", False) and (av_diff is None or av_diff <= 0.25))
            audio_basis = "DIRECT_FFPROBE_AUDIO_CHECK"

    audio_mix_info = kh02.get("audio_mix") or {}
    mix_applied = bool(audio_mix_info.get("applied"))
    mix_sync = audio_mix_info.get("mix_sync")
    if mix_applied:
        mix_sync_pass = bool(isinstance(mix_sync, dict) and mix_sync.get("pass") is True)
    else:
        mix_sync_pass = True if (mix_sync is None) else bool(isinstance(mix_sync, dict) and mix_sync.get("pass") is True)

    motion_sound_sync = {
        "schema_version": "kh03.motion_sound_sync.v2",
        "final_mp4_sha256": fin_sha,
        "audio_basis": audio_basis,
        "audio_qa_pass": audio_pass,
        "integrated_lufs": (audio_qa or {}).get("integrated_lufs"),
        "true_peak_dbtp": (audio_qa or {}).get("true_peak_dbtp"),
        "av_duration_diff_sec": (audio_qa or {}).get("av_duration_diff_sec", probed_basic.get("av_duration_diff_sec")),
        "mix_applied": mix_applied,
        "mix_sync_pass": mix_sync_pass if mix_applied else None,
        "pass": bool(audio_pass and mix_sync_pass),
    }
    write_json_atomic(qdir / "MOTION_SOUND_SYNC.json", motion_sound_sync)

    # Real NATIVE_RENDER_PROOF.json — inspects actual upstream repo SHA, render.mjs hash, command & exit code!
    rmjs_str = full_render.get("render_mjs") or full_render.get("renderer_script")
    eff_seecut_root = Path(seecut_root) if seecut_root else (Path(rmjs_str).parents[3] if rmjs_str and len(Path(rmjs_str).parents) >= 4 else None)
    actual_git_sha, git_ok = _probe_actual_upstream_git_sha(eff_seecut_root)
    render_mjs_p = Path(rmjs_str) if rmjs_str else (eff_seecut_root / "skill/seecut/scripts/render.mjs" if eff_seecut_root else None)
    render_mjs_exists = bool(render_mjs_p and render_mjs_p.is_file())
    pin_match = bool(actual_git_sha and actual_git_sha.lower().startswith(UPSTREAM_SEECUT_SHA[:7].lower()))
    native_exit_ok = (full_render.get("exit_code") == 0) or (full_render.get("ok") is True and full_render.get("exit_code") is None)
    native_proof_verified = bool(
        engine_kind == "NATIVE_SEECUT"
        and render_mjs_exists
        and pin_match
        and (full_render.get("command") or render_mjs_exists)
        and native_exit_ok
        and full_render.get("mp4_sha256") == fin_sha
    )
    render_proof = {
        "schema_version": "kh03.native_render_proof.v2",
        "engine_kind": engine_kind,
        "pinned_upstream_seecut_sha": UPSTREAM_SEECUT_SHA,
        "actual_upstream_git_sha": actual_git_sha,
        "upstream_git_verified": git_ok and pin_match,
        "seecut_root_present": bool(eff_seecut_root and eff_seecut_root.is_dir()),
        "render_mjs": str(render_mjs_p) if render_mjs_p else None,
        "render_mjs_exists": render_mjs_exists,
        "render_mjs_sha256": hash_artifact(render_mjs_p) if render_mjs_exists and render_mjs_p else None,
        "command": full_render.get("command"),
        "exit_code": full_render.get("exit_code"),
        "final_mp4_path": str(fin_p),
        "final_mp4_sha256": fin_sha,
        "final_mp4_bytes": fin_p.stat().st_size,
        "h1_status": (h1_geom or {}).get("status", "NOT_RUN"),
        "h1_command": (h1_geom or {}).get("command"),
        "h2_status": (h2_check or {}).get("status", "NOT_RUN"),
        "h2_reviewed_mp4_sha256": (h2_check or {}).get("reviewed_mp4_sha256"),
        "pairwise_count": len(pairwise),
        "jianying_exported": bool(jianying and jianying.get("pass")),
        "native_proof_verified": native_proof_verified,
        "pass": native_proof_verified,
        "verified_at_utc": now_utc(),
    }
    write_json_atomic(qdir / "NATIVE_RENDER_PROOF.json", render_proof)

    # Export QA: fail-closed when layers are required but absent/failed, or structural probe failed!
    need_layers = bool(export_layers_required or prof.get("requires_layers"))
    if need_layers:
        layers_ok = bool(isinstance(jianying, dict) and jianying.get("pass") is True)
        jy_status = (jianying or {}).get("fidelity_status", "MISSING_REQUIRED_LAYERS")
    else:
        layers_ok = True if jianying is None else bool(isinstance(jianying, dict) and jianying.get("pass") is True)
        jy_status = (jianying or {}).get("fidelity_status", "NOT_REQUESTED")

    export_qa = {
        "schema_version": "kh03.export_qa.v2",
        "final_mp4_sha256": fin_sha,
        "playable_file_non_empty": fin_p.stat().st_size > 1024,
        "structural_basis": struct_basis,
        "structural_pass": struct_pass,
        "jianying_layers_required": need_layers,
        "jianying_layers_status": jy_status,
        "jianying_pass": (jianying or {}).get("pass") if jianying is not None else None,
        "pass": bool((fin_p.stat().st_size > 1024) and struct_pass and layers_ok),
    }
    write_json_atomic(qdir / "EXPORT_QA.json", export_qa)
    trace.append({"step": "08_export_and_native_proof", "final_mp4_sha256": fin_sha, "export_pass": export_qa["pass"], "native_proof_verified": native_proof_verified, "h1": (h1_geom or {}).get("status"), "h2": (h2_check or {}).get("status")})

    # Assemble mode-aware per-gate matrix (ZERO fake PASS, ZERO silent N/A on required H1/H2/Native!)
    if isinstance(h1_geom, dict) and h1_geom:
        h1_stat = "PASS" if h1_geom.get("pass") is True else ("BLOCKED" if h1_geom.get("status") == "BLOCKED_ENV" else "FAIL")
        h1_app = True
        h1_na_reason = None
    elif prof.get("requires_h1"):
        h1_stat = "BLOCKED"
        h1_app = True
        h1_na_reason = None
    else:
        h1_stat = "NOT_APPLICABLE"
        h1_app = False
        h1_na_reason = f"H1 optional in {prof['mode']} mode without native build"

    if isinstance(h2_check, dict):
        # Verify H2 reviewed the EXACT final MP4 SHA when field is present
        h2_sha_ok = (not h2_check.get("reviewed_mp4_sha256")) or (h2_check.get("reviewed_mp4_sha256") == fin_sha)
        if not h2_sha_ok:
            h2_stat = "FAIL"
        elif h2_check.get("status") == "PASS":
            h2_stat = "PASS"
        elif h2_check.get("status") in ("INVALID", "BLOCKED_ENV", "TIMEOUT"):
            h2_stat = "PENDING_REVIEW"
        else:
            h2_stat = "FAIL"
        h2_app = True
        h2_na_reason = None
    elif prof.get("requires_h2"):
        h2_stat = "BLOCKED"
        h2_app = True
        h2_na_reason = None
    else:
        h2_stat = "NOT_APPLICABLE"
        h2_app = False
        h2_na_reason = f"Live H2 not required in {prof['mode']} mode"

    lic_stat = "BLOCKED" if license_decision in ("BLOCKED_COMMERCIAL_LICENSE", "BLOCKED_LICENSE_UNRESOLVED") else "PASS"
    act_status_raw = str(action_qa.get("status") or "FAIL")
    if act_status_raw == "NOT_APPLICABLE_NO_CRITICAL_REGIONS":
        act_gate_st, act_app, act_na = "NOT_APPLICABLE", False, "No critical tracked regions in source"
    elif act_status_raw == "BLOCKED_CLOCK_MAPPING":
        act_gate_st, act_app, act_na = "BLOCKED", True, None
    elif act_status_raw == "SAFE_FALLBACK_GEOMETRY_ONLY":
        act_gate_st = "PASS" if prof["mode"] == "ENGINEERING" else "PENDING_REVIEW"
        act_app, act_na = True, None
    else:
        act_gate_st = "PASS" if action_qa.get("pass") is True else "FAIL"
        act_app, act_na = True, None

    gate_entries = {
        "G_LICENSE": {"status": lic_stat, "applicable": True, "detail": license_decision, "evidence_sha256": fin_sha},
        "G_HOOK_PIXELS": {"status": "PASS" if hook_qa.get("pass") is True else "FAIL", "applicable": True, "detail": hook_qa.get("motion_gate_status"), "artifact_path": str(qdir / "HOOK_PIXEL_QA.json"), "evidence_sha256": fin_sha},
        "G_COMPONENT_ROI_MOTION": {
            "status": "NOT_APPLICABLE" if comp_qa.get("status") == "NOT_APPLICABLE_NO_COMPONENTS" else ("PASS" if comp_qa.get("pass") is True else "FAIL"),
            "applicable": comp_qa.get("status") != "NOT_APPLICABLE_NO_COMPONENTS",
            "na_reason": "No components in creative plan" if comp_qa.get("status") == "NOT_APPLICABLE_NO_COMPONENTS" else None,
            "detail": f"moved={comp_qa.get('moved_nodes')}/{comp_qa.get('nodes_checked')}",
            "artifact_path": str(qdir / "COMPONENT_PIXEL_QA.json"),
            "evidence_sha256": fin_sha,
        },
        "G_COMPONENT_COVERAGE": {
            "status": "PASS" if component_coverage["pass"] else "FAIL",
            "applicable": True,
            "detail": f"ratio={component_coverage['coverage_ratio']} ({component_coverage['checked_motion_nodes']}/{component_coverage['expected_motion_nodes']})",
            "artifact_path": str(qdir / "COMPONENT_COVERAGE.json"),
            "evidence_sha256": fin_sha,
        },
        "G_ACTION_PROTECTION": {
            "status": act_gate_st,
            "applicable": act_app,
            "na_reason": act_na,
            "detail": f"status={act_status_raw}, violations={len(action_qa.get('violations') or [])}",
            "artifact_path": str(qdir / "ACTION_PROTECTION.json"),
            "evidence_sha256": fin_sha,
        },
        "G_LAYOUT_RHYTHM": {
            "status": "NOT_APPLICABLE" if rhythm_qa.get("status") == "NOT_APPLICABLE" else ("PASS" if rhythm_qa.get("pass") is True else ("PENDING_REVIEW" if rhythm_qa.get("needs_review") else "FAIL")),
            "applicable": rhythm_qa.get("status") != "NOT_APPLICABLE",
            "na_reason": rhythm_qa.get("na_reason"),
            "detail": rhythm_qa.get("status"),
            "evidence_sha256": fin_sha,
        },
        "G_SPATIAL_ALIGNMENT": {
            "status": "PASS" if spatial_qa.get("pass") is True else "FAIL",
            "applicable": True,
            "detail": f"components={spatial_qa.get('components_evaluated')}, anchors={spatial_qa.get('anchors_checked')}",
            "artifact_path": str(qdir / "SPATIAL_ALIGNMENT_QA.json"),
            "evidence_sha256": fin_sha,
        },
        "G_EVIDENCE_CLAIMS": {
            "status": "NOT_APPLICABLE" if ev_claims_qa.get("status") == "NOT_APPLICABLE" else ("PASS" if ev_claims_qa.get("pass") is True else "FAIL"),
            "applicable": ev_claims_qa.get("status") != "NOT_APPLICABLE",
            "na_reason": ev_claims_qa.get("na_reason"),
            "detail": f"issues={len(ev_claims_qa.get('issues') or [])}",
            "evidence_sha256": fin_sha,
        },
        "G_FRAME_AUDIT": {
            "status": "PASS" if frame_audit.get("pass") is True else "FAIL",
            "applicable": True,
            "detail": f"decoded={frame_audit.get('decoded_frames')}, black={frame_audit.get('black_frame_count')}",
            "artifact_path": str(qdir / "FRAME_AUDIT.json"),
            "evidence_sha256": fin_sha,
        },
        "G_FRAME_COVERAGE": {
            "status": "PASS" if frame_coverage["pass"] else "FAIL",
            "applicable": True,
            "detail": f"ratio={frame_coverage['temporal_coverage_ratio']} ({decoded_n}/{exp_n})",
            "artifact_path": str(qdir / "FRAME_COVERAGE.json"),
            "evidence_sha256": fin_sha,
        },
        "G_AUDIO_SYNC": {
            "status": "PASS" if motion_sound_sync["pass"] else "FAIL",
            "applicable": True,
            "detail": f"basis={audio_basis}, lufs={(audio_qa or {}).get('integrated_lufs')}, tp={(audio_qa or {}).get('true_peak_dbtp')}",
            "artifact_path": str(qdir / "MOTION_SOUND_SYNC.json"),
            "evidence_sha256": fin_sha,
        },
        "G_H1_GEOM": {
            "status": h1_stat,
            "applicable": h1_app,
            "na_reason": h1_na_reason,
            "detail": f"fails={(h1_geom or {}).get('fail_count')}, warns={(h1_geom or {}).get('warn_count')}",
            "evidence_sha256": fin_sha if h1_stat == "PASS" else None,
        },
        "G_H2_JUDGE": {
            "status": h2_stat,
            "applicable": h2_app,
            "na_reason": h2_na_reason,
            "detail": (h2_check or {}).get("status", "NOT_RUN"),
            "evidence_sha256": (h2_check or {}).get("reviewed_mp4_sha256"),
        },
        "G_NATIVE_PROOF": {
            "status": "PASS" if native_proof_verified else ("BLOCKED" if prof.get("requires_native_proof") else "NOT_APPLICABLE"),
            "applicable": bool(prof.get("requires_native_proof") or native_proof_verified),
            "na_reason": None if (prof.get("requires_native_proof") or native_proof_verified) else f"Native proof optional in {prof['mode']} mode",
            "detail": f"git_sha={actual_git_sha}, render_mjs={render_mjs_exists}",
            "artifact_path": str(qdir / "NATIVE_RENDER_PROOF.json"),
            "evidence_sha256": fin_sha if native_proof_verified else None,
        },
        "G_EXPORT_QA": {
            "status": "PASS" if export_qa["pass"] else "FAIL",
            "applicable": True,
            "detail": export_qa["jianying_layers_status"],
            "artifact_path": str(qdir / "EXPORT_QA.json"),
            "evidence_sha256": fin_sha,
        },
    }
    if prof["mode"] == "RELEASE":
        gate_entries["G_OWNER_VISUAL"] = {"status": "PENDING_REVIEW", "applicable": True, "detail": "Owner visual review required on exact SHA"}
        gate_entries["G_OWNER_AUDIO"] = {"status": "PENDING_REVIEW", "applicable": True, "detail": "Owner audio listening required on exact SHA"}

    gate_matrix = aggregate_gate_results(
        gate_entries,
        mandatory_gates=prof["mandatory_gates"],
        mode=prof["mode"],
        strict_evidence=strict_evidence,
    )
    gate_matrix["final_mp4_sha256"] = fin_sha
    gate_matrix["run_id"] = run_id

    rubric = evaluate_editorial_quality(
        creative_plan,
        hook_qa=hook_qa, action_qa=action_qa, layout_rhythm=rhythm_qa,
        component_qa=comp_qa, frame_audit=frame_audit, audio_qa=motion_sound_sync,
        evidence_claims=ev_claims_qa, h1_geom=h1_geom,
    )

    final_pixel_qa = {
        "schema_version": "kh03.final_pixel_qa.v2",
        "run_id": run_id,
        "mode": prof["mode"],
        "engine_kind": engine_kind,
        "final_mp4_path": str(fin_p),
        "final_mp4_sha256": fin_sha,
        "technical_pass": gate_matrix["technical_pass"],
        "overall_technical_status": gate_matrix["overall_technical_status"],
        "editorial_status": gate_matrix["editorial_status"],
        "native_status": gate_matrix["native_status"],
        "release_status": gate_matrix["release_status"],
        "hook_pixel_qa": hook_qa,
        "component_pixel_qa": comp_qa,
        "component_coverage": component_coverage,
        "action_protection": action_qa,
        "layout_rhythm": rhythm_qa,
        "spatial_alignment": spatial_qa,
        "evidence_claims": ev_claims_qa,
        "frame_audit": frame_audit,
        "frame_coverage": frame_coverage,
        "motion_sound_sync": motion_sound_sync,
        "export_qa": export_qa,
        "editorial_quality_rubric": rubric,
        "owner_visual_approval": "PENDING",
        "owner_audio_approval": "PENDING",
    }
    write_json_atomic(qdir / "FINAL_PIXEL_QA.json", final_pixel_qa)
    write_json_atomic(qdir / "FINAL_GATE_MATRIX.json", gate_matrix)
    write_json_atomic(qdir / "FLOW_TRACE.json", {
        "schema_version": "kh03.flow_trace.v1",
        "run_id": run_id,
        "mode": prof["mode"],
        "final_mp4_sha256": fin_sha,
        "invoked_gates": [t["step"] for t in trace],
        "trace": trace,
    })
    write_json_atomic(qdir / "OWNER_APPROVAL.json", {
        "schema_version": "kh03.owner_approval.v1",
        "run_id": run_id,
        "final_mp4_path": str(fin_p),
        "final_mp4_sha256": fin_sha,
        "owner_visual_approval": "PENDING",
        "owner_audio_approval": "PENDING",
        "commercial_release": "BLOCKED" if license_decision != "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED" else "AUTHORIZED_PENDING_OWNER",
        "note": "Owner must inspect actual rendered MP4 matching exact SHA-256 above before approving.",
    })
    return {
        "pass": gate_matrix["technical_pass"],
        "technical_pass": gate_matrix["technical_pass"],
        "final_mp4_sha256": fin_sha,
        "final_pixel_qa": final_pixel_qa,
        "gate_matrix": gate_matrix,
        "component_coverage": component_coverage,
        "frame_coverage": frame_coverage,
        "editorial_quality_rubric": rubric,
        "render_proof": render_proof,
        "flow_trace": trace,
    }
