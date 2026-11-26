"""KH-03.R4.1 Multi-Candidate Creative Competition & End-to-End Winner Binding (PED-09 / R4.1-01).

Generates up to 3 structurally distinct editorial candidate bundles (A, B, C) strictly within the
user-selected universal editing skill (SK-01..SK-05) and upstream <=3 iteration budget:
- Each candidate is a complete isolated bundle (`creative_plan`, `layout_plan`, `motion_graph`,
  `evidence_bindings`, `sound_constraints`) with immutable canonical hashes & isolated workspace.
- Enforces `validate_candidate_diversity()` as a real pre-render gate rejecting cosmetic clones.
- Separates diagnostic `rubric_top` from verified `winner`: with 2+ eligible candidates, requires
  blind order-swapped AB/BA pairwise consensus; on tie, order disagreement, missing judge, or
  invalid receipt sets `winner=None` and blocks silent adoption or fallback to original plan.
- `bind_winner_to_execution()` materializes the winning candidate into canonical downstream plans
  and stamps full provenance hashes (`WINNER_BINDING.json`, `CREATIVE_PLAN.json`, `LAYOUT_PLAN.json`,
  `MOTION_GRAPH.json`, `SHOTBOOK_V2.json`).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

from .contracts import hash_artifact, now_utc, sanitize_identifier, write_json_atomic
from .layout import compute_source_crop_for_box, detect_visual_repetition, solve_layout_sequence
from .motion_graph import build_motion_graph
from .recommend import get_skill_contract

MAX_CANDIDATES = 3

# 3 structurally distinct editorial archetypes adaptable across EVERY universal skill & niche
CANDIDATE_STRATEGIES: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "cand_A",
        "label": "A",
        "strategy_code": "ASYMMETRIC_SPATIAL_CHOREOGRAPHY",
        "hook_framing": "typography_led_negative_space",
        "layout_bias": "asymmetric_left_right_alternating",
        "spatial_strategy": "track_anchored_callouts",
        "choreography_style": "4_phase_impact_settle",
        "evidence_style": "floating_inset_hero_stage",
    },
    {
        "candidate_id": "cand_B",
        "label": "B",
        "strategy_code": "ACTION_FIRST_EDITORIAL_COMPANION",
        "hook_framing": "action_led_full_presence",
        "layout_bias": "fullscreen_anchored_with_side_companion",
        "spatial_strategy": "perimeter_safe_companion",
        "choreography_style": "measured_continuity_glide",
        "evidence_style": "split_zoom_with_live_action",
    },
    {
        "candidate_id": "cand_C",
        "label": "C",
        "strategy_code": "EVIDENCE_DRIVEN_VISUAL_RHYTHM",
        "hook_framing": "evidence_or_question_contrast",
        "layout_bias": "dynamic_alternating_multi_topology",
        "spatial_strategy": "shared_element_handoff_anchors",
        "choreography_style": "staggered_progressive_reveal",
        "evidence_style": "full_width_annotated_proof",
    },
)

NON_CONSENSUS_PAIRWISE_STATES = frozenset({
    "INCONSISTENT_ORDER_TIE_PENDING_REVIEW",
    "PAIRWISE_TIE_PENDING_REVIEW",
    "PENDING_REVIEW_MISSING_JUDGE",
    "BLOCKED_JUDGE",
    "PLAN_ONLY_PAIRWISE_NOT_RUN",
    "RUBRIC_RANKED_PAIRWISE_NOT_RUN",
    "NO_ELIGIBLE_CANDIDATES",
})


def _recompute_layout_transitions(lp: dict[str, Any], fps_num: int, fps_den: int) -> None:
    """Ensure transition metadata matches candidate beat layout modes cleanly after topology adaptation."""
    beats = lp.get("beats") or []
    fps = fps_num / float(max(1, fps_den))
    dur_f = max(4, min(10, int(round(fps * 0.25))))
    transitions: list[dict[str, Any]] = []
    for idx in range(len(beats) - 1):
        b0, b1 = beats[idx], beats[idx + 1]
        if b0.get("layout_mode") == b1.get("layout_mode"):
            continue
        bf = int(b1["start_frame"])
        sf = max(int(b0["start_frame"]) + 2, bf - dur_f // 2)
        ef = min(int(b1["end_frame"]) - 1, sf + dur_f)
        if ef <= sf:
            ef = min(int(b1["end_frame"]), sf + 2)
        transitions.append({
            "transition_id": f"tr_{idx + 1:02d}_{b0['beat_id']}_to_{b1['beat_id']}",
            "from_beat_id": b0["beat_id"],
            "to_beat_id": b1["beat_id"],
            "from_mode": b0["layout_mode"],
            "to_mode": b1["layout_mode"],
            "boundary_frame": bf,
            "start_frame": sf,
            "end_frame": ef,
            "duration_frames": max(1, ef - sf),
            "easing": "power2.inOut",
            "anchor_lock": True,
        })
    lp["transitions"] = transitions


def build_candidate_bundle(
    *,
    run_id: str,
    selected_skill_id: str,
    strategy: dict[str, Any],
    idx: int,
    beats: list[dict[str, Any]],
    canvas: dict[str, Any],
    source_media: dict[str, Any],
    action_tracks: dict[str, Any],
    layout_policy: str = "ADAPTIVE_DYNAMIC",
    constraints: dict[str, Any] | None = None,
    assets: list[dict[str, Any]] | None = None,
    base_creative_plan: dict[str, Any] | None = None,
    semantic_graph: dict[str, Any] | None = None,
    explanation_graph: dict[str, Any] | None = None,
    transcript: dict[str, Any] | None = None,
    selection_receipt_sha256: str | None = None,
    source_hashes: dict[str, str] | None = None,
    seed: int = 20261125,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """Build one complete isolated candidate bundle (creative_plan + layout_plan + motion_graph + hashes)."""
    sk = get_skill_contract(selected_skill_id)
    fps_num = int(source_media.get("fps_num") or 24)
    fps_den = max(1, int(source_media.get("fps_den") or 1))
    src_w = int(source_media.get("width") or canvas.get("width") or 720)
    src_h = int(source_media.get("height") or canvas.get("height") or 1280)
    cw = int(canvas.get("width") or 720)
    ch = int(canvas.get("height") or 1280)

    c_beats = copy.deepcopy(beats)
    for bi, b in enumerate(c_beats):
        if idx == 0:
            b["candidate_layout_hint"] = "asymmetric_split"
        elif idx == 1:
            b["candidate_layout_hint"] = "action_first_fullscreen"
            if bi == 0:
                b["continuity_justified"] = True
        else:
            b["candidate_layout_hint"] = "evidence_alternating"
            if bi == max(1, len(c_beats) - 2):
                b["semantic_function"] = b.get("semantic_function") or "evidence"

    c_constraints = dict(constraints or {})
    c_constraints["candidate_strategy"] = strategy["strategy_code"]
    c_constraints["candidate_seed"] = int(seed) + idx

    lp = solve_layout_sequence(
        c_beats,
        run_id=f"{run_id}_{strategy['candidate_id']}",
        skill_id=selected_skill_id,
        skill_contract=sk,
        layout_policy=layout_policy,
        canvas=canvas,
        source_media=source_media,
        action_tracks=action_tracks,
        constraints=c_constraints,
        assets=assets,
        fps_num=fps_num,
        fps_den=fps_den,
    )
    # Apply genuine structural topology differentiation under ADAPTIVE_DYNAMIC when beats allow it
    if layout_policy == "ADAPTIVE_DYNAMIC" and len(lp["beats"]) >= 3:
        hands_bb = action_tracks.get("hands_bbox_norm") if isinstance(action_tracks, dict) else None
        if idx == 1:
            for bi in range(1, len(lp["beats"]) - 1):
                lb = lp["beats"][bi]
                if bi % 2 == 1:
                    lb["layout_mode"] = "FULL_CANVAS"
                    lb["presenter_box_norm"] = [0.0, 0.0, 1.0, 1.0]
                    lb["graphic_box_norm"] = [0.06, 0.06, 0.88, 0.16]
                    lb["caption_slot_norm"] = [0.06, 0.78, 0.80, 0.08]
                else:
                    lb["layout_mode"] = "PRESENTER_RIGHT_SPLIT"
                    lb["presenter_box_norm"] = [0.48, 0.0, 0.52, 1.0]
                    lb["graphic_box_norm"] = [0.04, 0.14, 0.42, 0.68]
                    lb["caption_slot_norm"] = [0.04, 0.84, 0.42, 0.07]
                pbox = lb["presenter_box_norm"]
                fc = tuple(lb.get("anchor") or [0.5, 0.36])
                lb["source_crop"] = compute_source_crop_for_box(
                    src_w, src_h, max(16, int(round(pbox[2] * cw))), max(16, int(round(pbox[3] * ch))),
                    focus_center=(fc[0], fc[1]), hands_bbox=hands_bb,
                )
        elif idx == 2:
            for bi in range(1, len(lp["beats"]) - 1):
                lb = lp["beats"][bi]
                if bi % 2 == 1:
                    lb["layout_mode"] = "FLOATING_INSET_PRESENTER"
                    lb["presenter_box_norm"] = [0.04, 0.58, 0.36, 0.34]
                    lb["graphic_box_norm"] = [0.04, 0.07, 0.92, 0.48]
                    lb["caption_slot_norm"] = [0.42, 0.68, 0.50, 0.08]
                else:
                    lb["layout_mode"] = "LOWER_COMPANION_SPLIT"
                    lb["presenter_box_norm"] = [0.0, 0.48, 1.0, 0.52]
                    lb["graphic_box_norm"] = [0.04, 0.06, 0.92, 0.40]
                    lb["caption_slot_norm"] = [0.06, 0.41, 0.80, 0.06]
                pbox = lb["presenter_box_norm"]
                fc = tuple(lb.get("anchor") or [0.5, 0.36])
                lb["source_crop"] = compute_source_crop_for_box(
                    src_w, src_h, max(16, int(round(pbox[2] * cw))), max(16, int(round(pbox[3] * ch))),
                    focus_center=(fc[0], fc[1]), hands_bbox=hands_bb,
                )
        _recompute_layout_transitions(lp, fps_num, fps_den)
        lp.pop("layout_plan_sha256", None)
        lp["layout_plan_sha256"] = hash_artifact(lp)

    # Materialize candidate-specific creative_plan & motion_graph when base_creative_plan is supplied
    cand_creative_plan: dict[str, Any] | None = None
    cand_motion_graph: dict[str, Any] | None = None
    if base_creative_plan is not None:
        cand_creative_plan = copy.deepcopy(base_creative_plan)
        cand_creative_plan["selected_direction_id"] = f"{selected_skill_id.lower()}_{strategy['candidate_id']}_{strategy['strategy_code'].lower()[:24]}"
        cand_creative_plan["candidate_id"] = strategy["candidate_id"]
        cand_creative_plan["candidate_strategy"] = strategy["strategy_code"]
        lp_by_bid = {str(lb["beat_id"]): lb for lb in (lp.get("beats") or [])}

        # Sync candidate beats & components with this candidate's layout geometry and framing strategy
        for b in cand_creative_plan.get("beats") or []:
            lb = lp_by_bid.get(str(b["beat_id"]))
            if lb:
                if layout_policy == "ADAPTIVE_DYNAMIC":
                    b["visual_mode"] = f"adaptive_{lb['layout_mode'].lower()}"
                b["why"] = f"KH-03 {selected_skill_id} [{strategy['candidate_id']}:{strategy['strategy_code']}] ({lb['layout_mode']})."

        for comp in cand_creative_plan.get("components") or []:
            lb = lp_by_bid.get(str(comp.get("beat_id")))
            params = dict(comp.get("params") or {})
            params["candidate_id"] = strategy["candidate_id"]
            params["candidate_strategy"] = strategy["strategy_code"]
            params["choreography_style"] = strategy["choreography_style"]
            params["spatial_strategy"] = strategy["spatial_strategy"]
            if lb:
                params["layout_mode"] = lb["layout_mode"]
                if layout_policy == "ADAPTIVE_DYNAMIC":
                    comp["normalized_bbox"] = list(lb["graphic_box_norm"])
                    comp["z_order"] = int(lb.get("graphic_z_order", comp.get("z_order", 32)))
            comp["params"] = params

        # Update hook framing strategy metadata without altering facts or spoiling payoff
        hp = dict(cand_creative_plan.get("hook_plan") or {})
        hp["candidate_hook_framing"] = strategy["hook_framing"]
        cand_creative_plan["hook_plan"] = hp

        lc = dict(cand_creative_plan.get("layout_constraints") or {})
        lc["unique_layout_modes"] = sorted({lb["layout_mode"] for lb in (lp.get("beats") or [])})
        cand_creative_plan["layout_constraints"] = lc
        cand_creative_plan["layout_plan_sha256"] = lp["layout_plan_sha256"]

        # Rebuild candidate-specific motion_graph bound to this candidate's components + layout_plan
        if semantic_graph is not None and explanation_graph is not None:
            total_f = int(lp.get("total_frames") or source_media.get("nb_frames") or 240)
            mg_res = build_motion_graph(
                run_id=f"{run_id}_{strategy['candidate_id']}",
                skill_id=selected_skill_id,
                components=cand_creative_plan.get("components") or [],
                semantic_graph=semantic_graph,
                explanation_graph=explanation_graph,
                transcript=transcript,
                layout_plan=lp,
                fps_num=fps_num,
                fps_den=fps_den,
                total_frames=total_f,
                reduced_motion=bool(c_constraints.get("reduced_motion", False)),
                run_dir=None,
            )
            cand_motion_graph = mg_res["motion_graph"]
            cand_creative_plan["motion_graph_sha256"] = cand_motion_graph["graph_sha256"]
            # Sync component internal_nodes from candidate's rebuilt motion_graph
            children_by_cid: dict[str, list[dict[str, Any]]] = {}
            for nd in cand_motion_graph.get("nodes", []):
                if nd.get("parent_id") is not None:
                    children_by_cid.setdefault(str(nd["component_id"]), []).append({
                        "node_id": nd["node_id"],
                        "role": nd["role"],
                        "primitive": nd["primitive"],
                        "start_frame": int(nd["start_frame"]),
                        "end_frame": int(nd["end_frame"]),
                        "anchor_word_id": nd.get("anchor_word_id"),
                        "keyframes": list(nd.get("keyframes") or []),
                    })
            for comp in cand_creative_plan.get("components") or []:
                comp["internal_nodes"] = list(children_by_cid.get(comp["component_id"]) or [])

        cand_creative_plan.pop("plan_sha256", None)
        cand_creative_plan["plan_sha256"] = hash_artifact(cand_creative_plan)

    rhythm = detect_visual_repetition(lp, creative_plan=cand_creative_plan, motion_graph=cand_motion_graph)
    plan_hard_failures: list[str] = []
    if str(c_constraints.get("license_decision") or "").startswith("BLOCKED"):
        plan_hard_failures.append(f"LICENSE_BLOCKED:{c_constraints.get('license_decision')}")
    if rhythm["status"] == "FAIL":
        plan_hard_failures.append(f"LAYOUT_REPETITION_FAIL:{'; '.join(rhythm['findings'])}")

    plan_sha = (
        cand_creative_plan["plan_sha256"]
        if cand_creative_plan is not None
        else hash_artifact({"strategy": strategy, "layout_plan_sha256": lp["layout_plan_sha256"]})
    )
    mg_sha = cand_motion_graph["graph_sha256"] if cand_motion_graph is not None else None
    src_hashes_clean = dict(source_hashes or {})

    bundle_identity = {
        "candidate_id": strategy["candidate_id"],
        "selected_skill_id": selected_skill_id,
        "selection_receipt_sha256": selection_receipt_sha256,
        "source_hashes": src_hashes_clean,
        "plan_sha256": plan_sha,
        "layout_plan_sha256": lp["layout_plan_sha256"],
        "motion_graph_sha256": mg_sha,
    }
    bundle_sha = hash_artifact(bundle_identity)

    cand_doc: dict[str, Any] = {
        "schema_version": "kh03.creative_candidate.v2",
        "candidate_id": strategy["candidate_id"],
        "label": strategy["label"],
        "blind_id": f"X{idx + 1}",
        "selected_skill_id": selected_skill_id,
        "selection_receipt_sha256": selection_receipt_sha256,
        "source_hashes": src_hashes_clean,
        "strategy": dict(strategy),
        "creative_plan": cand_creative_plan,
        "layout_plan": lp,
        "motion_graph": cand_motion_graph,
        "layout_rhythm": rhythm,
        "sound_constraints": {"choreography_style": strategy["choreography_style"], "layout_policy": layout_policy},
        "plan_hard_failures": plan_hard_failures,
        "plan_gate_pass": len(plan_hard_failures) == 0,
        "plan_sha256": plan_sha,
        "layout_plan_sha256": lp["layout_plan_sha256"],
        "motion_graph_sha256": mg_sha,
        "bundle_sha256": bundle_sha,
        "workspace_dir": str(workspace_dir) if workspace_dir is not None else None,
        "preview_mp4_path": None,
        "preview_mp4_sha256": None,
        "preview_qa_pass": None,
    }

    if workspace_dir is not None:
        workspace_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(workspace_dir / "LAYOUT_PLAN.json", lp)
        if cand_creative_plan is not None:
            write_json_atomic(workspace_dir / "CREATIVE_PLAN.json", cand_creative_plan)
        if cand_motion_graph is not None:
            write_json_atomic(workspace_dir / "MOTION_GRAPH.json", cand_motion_graph)
        # Compact manifest without duplicating large sub-trees twice on disk
        manifest_copy = {k: v for k, v in cand_doc.items() if k not in ("creative_plan", "layout_plan", "motion_graph")}
        write_json_atomic(workspace_dir / "CANDIDATE_MANIFEST.json", manifest_copy)

    return cand_doc


def validate_candidate_bundle(
    candidate: dict[str, Any],
    *,
    expected_skill_id: str | None = None,
    expected_selection_sha: str | None = None,
    expected_source_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Verify candidate bundle structural integrity, hash linkage, skill fidelity & source/selection binding."""
    issues: list[str] = []
    cid = str(candidate.get("candidate_id") or "")
    if not cid:
        issues.append("MISSING_CANDIDATE_ID")
    if expected_skill_id and candidate.get("selected_skill_id") != expected_skill_id:
        issues.append(f"SKILL_MISMATCH({candidate.get('selected_skill_id')}!={expected_skill_id})")
    if expected_selection_sha and candidate.get("selection_receipt_sha256") != expected_selection_sha:
        issues.append("SELECTION_RECEIPT_SHA_MISMATCH")
    if expected_source_hashes:
        got_sh = candidate.get("source_hashes") or {}
        for k, v in expected_source_hashes.items():
            if got_sh.get(k) != v:
                issues.append(f"SOURCE_HASH_MISMATCH({k})")

    lp = candidate.get("layout_plan")
    if isinstance(lp, dict):
        lp_copy = {k: v for k, v in lp.items() if k != "layout_plan_sha256"}
        if lp.get("layout_plan_sha256") and hash_artifact(lp_copy) != lp["layout_plan_sha256"]:
            issues.append("TAMPERED_LAYOUT_PLAN_SHA256")
        if candidate.get("layout_plan_sha256") and lp.get("layout_plan_sha256") != candidate["layout_plan_sha256"]:
            issues.append("CANDIDATE_LAYOUT_SHA_DRIFT")

    cp = candidate.get("creative_plan")
    if isinstance(cp, dict):
        cp_copy = {k: v for k, v in cp.items() if k != "plan_sha256"}
        if cp.get("plan_sha256") and hash_artifact(cp_copy) != cp["plan_sha256"]:
            issues.append("TAMPERED_CREATIVE_PLAN_SHA256")
        if candidate.get("plan_sha256") and cp.get("plan_sha256") != candidate["plan_sha256"]:
            issues.append("CANDIDATE_PLAN_SHA_DRIFT")

    if candidate.get("bundle_sha256"):
        recomp = hash_artifact({
            "candidate_id": candidate.get("candidate_id"),
            "selected_skill_id": candidate.get("selected_skill_id"),
            "selection_receipt_sha256": candidate.get("selection_receipt_sha256"),
            "source_hashes": dict(candidate.get("source_hashes") or {}),
            "plan_sha256": candidate.get("plan_sha256"),
            "layout_plan_sha256": candidate.get("layout_plan_sha256"),
            "motion_graph_sha256": candidate.get("motion_graph_sha256"),
        })
        if recomp != candidate["bundle_sha256"]:
            issues.append("TAMPERED_CANDIDATE_BUNDLE_SHA256")

    if candidate.get("plan_hard_failures"):
        issues.append(f"PLAN_HARD_FAILURES:{candidate['plan_hard_failures']}")

    return {"candidate_id": cid, "issues": issues, "pass": len(issues) == 0}


def generate_creative_candidates(
    *,
    run_id: str,
    selected_skill_id: str,
    beats: list[dict[str, Any]],
    canvas: dict[str, Any],
    source_media: dict[str, Any],
    action_tracks: dict[str, Any],
    layout_policy: str = "ADAPTIVE_DYNAMIC",
    constraints: dict[str, Any] | None = None,
    assets: list[dict[str, Any]] | None = None,
    max_candidates: int = MAX_CANDIDATES,
    seed: int = 20261125,
    base_creative_plan: dict[str, Any] | None = None,
    semantic_graph: dict[str, Any] | None = None,
    explanation_graph: dict[str, Any] | None = None,
    transcript: dict[str, Any] | None = None,
    selection_receipt_sha256: str | None = None,
    source_hashes: dict[str, str] | None = None,
    run_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Generate up to 3 genuinely distinct isolated candidate bundles strictly honoring user-selected skill."""
    cap = max(1, min(MAX_CANDIDATES, int(max_candidates)))
    # Do not fabricate multiple cosmetic clones if layout constraints strictly prevent structural diversity!
    constraint_single = (layout_policy == "FULLSCREEN_LOCK" and len(beats) < 2)
    eff_cap = 1 if constraint_single else cap

    candidates: list[dict[str, Any]] = []
    for idx, strat in enumerate(CANDIDATE_STRATEGIES[:eff_cap]):
        ws = (run_dir / "candidates" / strat["label"]) if run_dir is not None else None
        cand = build_candidate_bundle(
            run_id=run_id,
            selected_skill_id=selected_skill_id,
            strategy=strat,
            idx=idx,
            beats=beats,
            canvas=canvas,
            source_media=source_media,
            action_tracks=action_tracks,
            layout_policy=layout_policy,
            constraints=constraints,
            assets=assets,
            base_creative_plan=base_creative_plan,
            semantic_graph=semantic_graph,
            explanation_graph=explanation_graph,
            transcript=transcript,
            selection_receipt_sha256=selection_receipt_sha256,
            source_hashes=source_hashes,
            seed=seed,
            workspace_dir=ws,
        )
        if constraint_single:
            cand["single_candidate_reason"] = f"Constraint layout_policy={layout_policy} with {len(beats)} beat(s) permits 1 canonical candidate."
        candidates.append(cand)
    return candidates


def validate_candidate_diversity(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Real pre-render diversity gate: compares structural strategy, layout sequences & component geometry, rejecting cosmetic clones."""
    issues: list[str] = []
    pairs: list[dict[str, Any]] = []
    rejected_ids: set[str] = set()

    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            ca, cb = candidates[i], candidates[j]
            sa, sb = ca.get("strategy") or {}, cb.get("strategy") or {}
            dims = ("hook_framing", "layout_bias", "spatial_strategy", "choreography_style", "evidence_style")
            diff_dims = [d for d in dims if sa.get(d) != sb.get(d)]
            seq_a = ((ca.get("layout_rhythm") or {}).get("occupancy") or {}).get("sequence") or []
            seq_b = ((cb.get("layout_rhythm") or {}).get("occupancy") or {}).get("sequence") or []
            # Also compare beat-level graphic boxes & component capabilities when creative_plan exists
            boxes_a = [b.get("graphic_box_norm") for b in ((ca.get("layout_plan") or {}).get("beats") or [])]
            boxes_b = [b.get("graphic_box_norm") for b in ((cb.get("layout_plan") or {}).get("beats") or [])]
            layout_differs = (seq_a != seq_b) or (boxes_a != boxes_b) or (len(seq_a) <= 2)
            same_plan_sha = bool(ca.get("plan_sha256") and ca.get("plan_sha256") == cb.get("plan_sha256"))
            distinct = (len(diff_dims) >= 3) and layout_differs and (not same_plan_sha)
            pairs.append({
                "pair": [ca["candidate_id"], cb["candidate_id"]],
                "differing_structural_dims": diff_dims,
                "layout_sequence_differs": layout_differs,
                "same_plan_sha": same_plan_sha,
                "distinct": distinct,
            })
            if not distinct:
                rejected_ids.add(cb["candidate_id"])
                issues.append(
                    f"DUPLICATE_OR_COSMETIC_CANDIDATE_PAIR({ca['candidate_id']},{cb['candidate_id']}): "
                    f"dims={len(diff_dims)}, layout_differs={layout_differs}, same_sha={same_plan_sha}"
                )

    valid_cands = [c for c in candidates if c["candidate_id"] not in rejected_ids]
    if len(candidates) == 1:
        status = "SINGLE_CANDIDATE_BY_CONSTRAINT"
    elif not issues and len(valid_cands) >= 2:
        status = "DIVERSITY_VERIFIED"
    else:
        status = "FAIL_COSMETIC_CLONES"

    return {
        "schema_version": "kh03.candidate_diversity.v2",
        "status": status,
        "candidate_count": len(candidates),
        "valid_candidate_ids": [c["candidate_id"] for c in valid_cands],
        "rejected_duplicate_ids": sorted(rejected_ids),
        "pairs": pairs,
        "issues": issues,
        "pass": len(issues) == 0 and len(candidates) >= 1,
    }


def _blind_view(cand: dict[str, Any], blind_slot: str) -> dict[str, Any]:
    """Create a blind evaluation view hiding strategy code/name from pairwise judge while preserving candidate_id for mapping."""
    return {
        "candidate_id": cand["candidate_id"],
        "blind_slot": blind_slot,
        "blind_id": cand.get("blind_id") or blind_slot,
        "selected_skill_id": cand.get("selected_skill_id"),
        "plan_sha256": cand.get("plan_sha256"),
        "layout_plan_sha256": cand.get("layout_plan_sha256"),
        "preview_mp4_path": cand.get("preview_mp4_path"),
        "preview_mp4_sha256": cand.get("preview_mp4_sha256"),
        "layout_rhythm": cand.get("layout_rhythm"),
    }


def _run_blind_ab_ba(
    ca: dict[str, Any],
    cb: dict[str, Any],
    pairwise_fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Run blind order-swapped AB and BA comparison mapping both results back to canonical candidate_id."""
    aid, bid = ca["candidate_id"], cb["candidate_id"]
    try:
        res_ab = pairwise_fn(_blind_view(ca, "X"), _blind_view(cb, "Y"))
        res_ba = pairwise_fn(_blind_view(cb, "X"), _blind_view(ca, "Y"))
    except Exception as ex:
        return {
            "pair": [aid, bid],
            "ab_raw_winner": None,
            "ba_raw_winner": None,
            "mapped_ab": None,
            "mapped_ba": None,
            "consensus_winner_id": None,
            "status": "BLOCKED_JUDGE",
            "error": str(ex)[:200],
        }
    w_ab = (res_ab or {}).get("winner")
    w_ba = (res_ba or {}).get("winner")
    mapped_ab = aid if w_ab == "X" else (bid if w_ab == "Y" else ("TIE" if str(w_ab or "").upper() == "TIE" else None))
    mapped_ba = bid if w_ba == "X" else (aid if w_ba == "Y" else ("TIE" if str(w_ba or "").upper() == "TIE" else None))

    if mapped_ab in (aid, bid) and mapped_ab == mapped_ba:
        st = "CONSENSUS"
        win_id = mapped_ab
    elif mapped_ab == "TIE" or mapped_ba == "TIE":
        st = "PAIRWISE_TIE_PENDING_REVIEW"
        win_id = None
    elif mapped_ab is None or mapped_ba is None:
        st = "PENDING_REVIEW_MISSING_JUDGE"
        win_id = None
    else:
        st = "INCONSISTENT_ORDER_TIE_PENDING_REVIEW"
        win_id = None
    return {
        "pair": [aid, bid],
        "ab_raw_winner": w_ab,
        "ba_raw_winner": w_ba,
        "mapped_ab": mapped_ab,
        "mapped_ba": mapped_ba,
        "consensus_winner_id": win_id,
        "status": st,
        "receipt_sha256": hash_artifact({"pair": [aid, bid], "ab": res_ab, "ba": res_ba}),
    }


def rank_candidates(
    candidates: list[dict[str, Any]],
    *,
    rubric_scorer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    pairwise_fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
    mode: str = "plan",
    require_preview_proof: bool = False,
) -> dict[str, Any]:
    """Rank passing candidates with hard-gate disqualification & strict order-swapped pairwise consensus (R4.1-01).

    NEVER promotes rubric_top to verified `winner` when 2+ eligible candidates lack pairwise consensus
    (tie, order disagreement, missing judge, or judge failure -> `winner=None`).
    """
    eligible: list[dict[str, Any]] = []
    disqualified: list[dict[str, Any]] = []
    need_preview = bool(require_preview_proof or mode in ("preview", "full"))

    for c in candidates:
        hard_fails = list(c.get("plan_hard_failures") or [])
        if need_preview:
            if not c.get("preview_mp4_sha256"):
                hard_fails.append("MISSING_PREVIEW_MP4_PROOF")
            if c.get("preview_qa_pass") is not True:
                hard_fails.append("PREVIEW_HARD_QA_FAILED_OR_MISSING")
        if rubric_scorer is not None:
            eval_r = rubric_scorer(c)
        else:
            occ = (c.get("layout_rhythm") or {}).get("occupancy") or {}
            ent = float(occ.get("topology_entropy_bits", 0.0))
            dom = float(occ.get("dominant_duration_share", 1.0))
            rhythm_pts = 9.0 if (c.get("layout_rhythm") or {}).get("pass") else 5.5
            score = round(min(10.0, max(0.0, 6.2 + ent * 1.4 + (1.0 - dom) * 2.2)) * 0.5 + rhythm_pts * 0.5, 3)
            eval_r = {"rubric_score_10": score, "hard_failures": []}
        hard_fails.extend(eval_r.get("hard_failures") or [])
        entry = {
            "candidate_id": c["candidate_id"],
            "blind_id": c.get("blind_id"),
            "selected_skill_id": c["selected_skill_id"],
            "strategy_code": (c.get("strategy") or {}).get("strategy_code"),
            "plan_sha256": c["plan_sha256"],
            "layout_plan_sha256": c.get("layout_plan_sha256"),
            "motion_graph_sha256": c.get("motion_graph_sha256"),
            "bundle_sha256": c.get("bundle_sha256"),
            "preview_mp4_sha256": c.get("preview_mp4_sha256"),
            "preview_qa_pass": c.get("preview_qa_pass"),
            "rubric_score_10": float(eval_r.get("rubric_score_10", 0.0)),
            "hard_failures": hard_fails,
            "eligible": len(hard_fails) == 0,
        }
        if entry["eligible"]:
            eligible.append(entry)
        else:
            disqualified.append(entry)

    # Deterministic rubric diagnostic ordering: rubric_score DESC -> candidate_id ASC
    eligible.sort(key=lambda x: (-x["rubric_score_10"], x["candidate_id"]))
    rubric_top = eligible[0] if eligible else None

    pairwise_records: list[dict[str, Any]] = []
    winner: dict[str, Any] | None = None
    selection_basis = "NONE"

    if len(eligible) == 0:
        pairwise_status = "NO_ELIGIBLE_CANDIDATES"
        winner = None
    elif len(eligible) == 1:
        pairwise_status = "SINGLE_ELIGIBLE"
        winner = eligible[0]
        selection_basis = "SINGLE_ELIGIBLE_ONLY"
    elif pairwise_fn is not None:
        # Full round-robin order-swapped AB/BA tournament across up to 3 eligible candidates (never skips cand_C!)
        cand_map = {c["candidate_id"]: c for c in candidates}
        wins_by_id: dict[str, int] = {e["candidate_id"]: 0 for e in eligible}
        statuses_seen: list[str] = []
        for i in range(len(eligible)):
            for j in range(i + 1, len(eligible)):
                ca = cand_map[eligible[i]["candidate_id"]]
                cb = cand_map[eligible[j]["candidate_id"]]
                rec = _run_blind_ab_ba(ca, cb, pairwise_fn)
                pairwise_records.append(rec)
                statuses_seen.append(rec["status"])
                if rec["consensus_winner_id"]:
                    wins_by_id[rec["consensus_winner_id"]] += 1

        needed_wins = len(eligible) - 1
        undefeated = [cid for cid, w in wins_by_id.items() if w == needed_wins]
        if len(undefeated) == 1:
            pairwise_status = "CONSENSUS"
            winner = next(e for e in eligible if e["candidate_id"] == undefeated[0])
            selection_basis = "AI_CONSENSUS"
        elif "BLOCKED_JUDGE" in statuses_seen:
            pairwise_status = "BLOCKED_JUDGE"
            winner = None
        elif "PENDING_REVIEW_MISSING_JUDGE" in statuses_seen:
            pairwise_status = "PENDING_REVIEW_MISSING_JUDGE"
            winner = None
        elif "INCONSISTENT_ORDER_TIE_PENDING_REVIEW" in statuses_seen:
            pairwise_status = "INCONSISTENT_ORDER_TIE_PENDING_REVIEW"
            winner = None
        else:
            pairwise_status = "PAIRWISE_TIE_PENDING_REVIEW"
            winner = None
    else:
        # 2+ eligible candidates WITHOUT pairwise_fn -> NEVER silently adopt rubric top as verified winner!
        pairwise_status = "PENDING_REVIEW_MISSING_JUDGE" if mode in ("preview", "full") else "PLAN_ONLY_PAIRWISE_NOT_RUN"
        winner = None

    if winner is not None:
        status = "WINNER_SELECTED" if pairwise_status == "CONSENSUS" else "SINGLE_ELIGIBLE_SELECTED"
    elif len(eligible) == 0:
        status = "NO_VALID_WINNER"
    elif pairwise_status == "BLOCKED_JUDGE":
        status = "BLOCKED_JUDGE"
    elif pairwise_status == "PLAN_ONLY_PAIRWISE_NOT_RUN":
        status = "PLAN_ONLY_NO_PAIRWISE_WINNER"
    else:
        status = "PENDING_REVIEW"

    return {
        "schema_version": "kh03.candidate_ranking.v2",
        "competition_mode": "PLAN_ONLY" if mode == "plan" else mode.upper(),
        "eligible_count": len(eligible),
        "disqualified_count": len(disqualified),
        "ranked": eligible,
        "disqualified": disqualified,
        "rubric_top_diagnostic": rubric_top,
        "pairwise_status": pairwise_status,
        "pairwise_records": pairwise_records,
        "selection_basis": selection_basis,
        "winner": winner,
        "visual_winner_verified": bool(winner is not None and mode in ("preview", "full") and pairwise_status == "CONSENSUS"),
        "status": status,
    }


def adopt_candidate(
    ranking: dict[str, Any],
    *,
    owner_override_candidate_id: str | None = None,
    owner_selection_receipt: dict[str, Any] | None = None,
    full_res_qa_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    mode: str = "plan",
    require_preview_proof: bool = False,
    expected_selection_sha: str | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Fail-closed candidate adoption: rejects tie/pending/missing pairwise/missing preview proof/hard-fail overrides."""
    ranked = list(ranking.get("ranked") or [])
    if not ranked:
        doc = {
            "schema_version": "kh03.candidates_decision.v2",
            "status": "NO_VALID_WINNER",
            "adopted": False,
            "adopted_candidate_id": None,
            "selection_basis": "NONE",
            "reason": "All candidates failed mandatory hard gates; refusing to adopt a defective plan.",
            "ranking": ranking,
            "full_res_revalidation": None,
            "owner_visual": "PENDING",
            "owner_audio": "PENDING",
        }
        if run_dir is not None:
            write_json_atomic(run_dir / "manifests" / "CANDIDATES.json", doc)
        return doc

    override_used = False
    selection_basis = str(ranking.get("selection_basis") or "NONE")
    chosen: dict[str, Any] | None = ranking.get("winner")

    if owner_override_candidate_id is not None:
        match = next((r for r in ranked if r["candidate_id"] == owner_override_candidate_id), None)
        if match is None:
            raise ValueError(f"OVERRIDE_REJECTED_HARD_FAIL: candidate {owner_override_candidate_id!r} did not pass mandatory gates.")
        chosen = match
        override_used = True
        selection_basis = "MANUAL_SELECTION_RECORDED"
    else:
        pw_st = str(ranking.get("pairwise_status") or "")
        if chosen is None or pw_st in NON_CONSENSUS_PAIRWISE_STATES:
            blk_status = (
                "CANDIDATE_BLOCKED_JUDGE" if pw_st == "BLOCKED_JUDGE"
                else ("CANDIDATE_BLOCKED_PLAN_ONLY" if pw_st == "PLAN_ONLY_PAIRWISE_NOT_RUN"
                      else "CANDIDATE_BLOCKED_PENDING_REVIEW")
            )
            doc = {
                "schema_version": "kh03.candidates_decision.v2",
                "status": blk_status,
                "adopted": False,
                "adopted_candidate_id": None,
                "selection_basis": "NONE",
                "reason": (
                    f"Refusing silent adoption: pairwise_status={pw_st!r} without consensus winner "
                    "(rubric_top is diagnostic only and NEVER auto-adopted)."
                ),
                "ranking": ranking,
                "full_res_revalidation": None,
                "owner_visual": "PENDING",
                "owner_audio": "PENDING",
            }
            if run_dir is not None:
                write_json_atomic(run_dir / "manifests" / "CANDIDATES.json", doc)
            return doc

    assert chosen is not None
    need_preview = bool(require_preview_proof or mode in ("preview", "full"))
    if need_preview and (not chosen.get("preview_mp4_sha256") or chosen.get("preview_qa_pass") is not True):
        doc = {
            "schema_version": "kh03.candidates_decision.v2",
            "status": "CANDIDATE_BLOCKED_MISSING_PREVIEW_PROOF",
            "adopted": False,
            "adopted_candidate_id": None,
            "selection_basis": selection_basis,
            "reason": f"Candidate {chosen['candidate_id']} lacks verified preview MP4 SHA or passing preview QA.",
            "ranking": ranking,
            "full_res_revalidation": None,
            "owner_visual": "PENDING",
            "owner_audio": "PENDING",
        }
        if run_dir is not None:
            write_json_atomic(run_dir / "manifests" / "CANDIDATES.json", doc)
        return doc

    full_qa = full_res_qa_fn(chosen) if full_res_qa_fn is not None else None
    if full_qa is not None and not full_qa.get("pass", False):
        status = "WINNER_FULL_RES_QA_FAILED"
        adopted = False
    elif override_used:
        status = "MANUAL_SELECTION_RECORDED"
        adopted = True
    elif full_qa is not None:
        status = "ADOPTED_PENDING_OWNER_VISUAL_AUDIO"
        adopted = True
    elif mode == "plan":
        status = "ADOPTED_PLAN_ONLY"
        adopted = True
    else:
        status = "ADOPTED_PREVIEW_PENDING_FULL_RES"
        adopted = True

    doc = {
        "schema_version": "kh03.candidates_decision.v2",
        "status": status,
        "adopted": adopted,
        "adopted_candidate_id": chosen["candidate_id"] if adopted else None,
        "selected_skill_id": chosen["selected_skill_id"],
        "selection_basis": selection_basis,
        "owner_override_used": override_used,
        "owner_selection_receipt": owner_selection_receipt,
        "expected_selection_sha256": expected_selection_sha,
        "winner_plan_sha256": chosen.get("plan_sha256"),
        "winner_layout_plan_sha256": chosen.get("layout_plan_sha256"),
        "winner_motion_graph_sha256": chosen.get("motion_graph_sha256"),
        "winner_bundle_sha256": chosen.get("bundle_sha256"),
        "winner_preview_mp4_sha256": chosen.get("preview_mp4_sha256"),
        "ranking": ranking,
        "full_res_revalidation": full_qa,
        "owner_visual": "PENDING",
        "owner_audio": "PENDING",
    }
    if run_dir is not None:
        write_json_atomic(run_dir / "manifests" / "CANDIDATES.json", doc)
    return doc


def materialize_candidate_plan(
    candidate: dict[str, Any],
    *,
    run_dir: Path,
    winning_candidate_id: str,
    competition_status: str,
) -> dict[str, Any]:
    """Overwrite canonical CREATIVE_PLAN.json, LAYOUT_PLAN.json, MOTION_GRAPH.json strictly from the winning bundle."""
    cp = copy.deepcopy(candidate.get("creative_plan"))
    lp = copy.deepcopy(candidate.get("layout_plan"))
    mg = copy.deepcopy(candidate.get("motion_graph"))
    if not isinstance(cp, dict) or not isinstance(lp, dict):
        raise ValueError(f"INCOMPLETE_WINNER_BUNDLE({winning_candidate_id}): missing creative_plan or layout_plan.")

    cp["winning_candidate_id"] = winning_candidate_id
    cp["winner_bundle_sha256"] = candidate.get("bundle_sha256")
    cp["competition_status"] = competition_status
    cp.pop("plan_sha256", None)
    cp["plan_sha256"] = hash_artifact(cp)

    write_json_atomic(run_dir / "manifests" / "CREATIVE_PLAN.json", cp)
    write_json_atomic(run_dir / "layouts" / "LAYOUT_PLAN.json", lp)
    if isinstance(mg, dict):
        write_json_atomic(run_dir / "manifests" / "MOTION_GRAPH.json", mg)
    return {"creative_plan": cp, "layout_plan": lp, "motion_graph": mg}


def bind_winner_to_execution(
    candidate_decision: dict[str, Any],
    candidates_by_id: dict[str, dict[str, Any]],
    *,
    run_dir: Path,
    selection_receipt_sha256: str,
    source_hashes: dict[str, str],
    mode: str = "plan",
) -> dict[str, Any]:
    """Bind verified winning candidate end-to-end into canonical downstream plans & WINNER_BINDING.json (R4.1-01)."""
    cid = candidate_decision.get("adopted_candidate_id")
    if not candidate_decision.get("adopted") or not cid or cid not in candidates_by_id:
        binding_doc = {
            "schema_version": "kh03.winner_binding.v1",
            "bound": False,
            "status": str(candidate_decision.get("status") or "CANDIDATE_BLOCKED"),
            "winning_candidate_id": None,
            "selection_receipt_sha256": selection_receipt_sha256,
            "source_hashes": dict(source_hashes),
            "candidates_decision_sha256": hash_artifact(candidate_decision),
            "reason": str(candidate_decision.get("reason") or "No valid adopted winner to bind."),
            "bound_at_utc": now_utc(),
        }
        write_json_atomic(run_dir / "manifests" / "WINNER_BINDING.json", binding_doc)
        return {"bound": False, "binding": binding_doc, "creative_plan": None, "layout_plan": None, "motion_graph": None}

    winner_bundle = candidates_by_id[cid]
    val = validate_candidate_bundle(
        winner_bundle,
        expected_skill_id=candidate_decision.get("selected_skill_id"),
        expected_selection_sha=selection_receipt_sha256,
        expected_source_hashes=source_hashes,
    )
    if not val["pass"]:
        binding_doc = {
            "schema_version": "kh03.winner_binding.v1",
            "bound": False,
            "status": "WINNER_BUNDLE_VALIDATION_FAILED",
            "winning_candidate_id": cid,
            "issues": val["issues"],
            "selection_receipt_sha256": selection_receipt_sha256,
            "source_hashes": dict(source_hashes),
            "bound_at_utc": now_utc(),
        }
        write_json_atomic(run_dir / "manifests" / "WINNER_BINDING.json", binding_doc)
        return {"bound": False, "binding": binding_doc, "creative_plan": None, "layout_plan": None, "motion_graph": None}

    mat = materialize_candidate_plan(
        winner_bundle,
        run_dir=run_dir,
        winning_candidate_id=cid,
        competition_status=str(candidate_decision.get("status")),
    )
    binding_core = {
        "schema_version": "kh03.winner_binding.v1",
        "bound": True,
        "mode": mode,
        "status": str(candidate_decision.get("status")),
        "selection_basis": candidate_decision.get("selection_basis"),
        "winning_candidate_id": cid,
        "selected_skill_id": winner_bundle["selected_skill_id"],
        "selection_receipt_sha256": selection_receipt_sha256,
        "source_hashes": dict(source_hashes),
        "candidates_decision_sha256": hash_artifact(candidate_decision),
        "winner_bundle_sha256": winner_bundle["bundle_sha256"],
        "creative_plan_sha256": mat["creative_plan"]["plan_sha256"],
        "layout_plan_sha256": mat["layout_plan"]["layout_plan_sha256"],
        "motion_graph_sha256": (mat["motion_graph"] or {}).get("graph_sha256"),
        "preview_mp4_sha256": winner_bundle.get("preview_mp4_sha256"),
        "pairwise_status": (candidate_decision.get("ranking") or {}).get("pairwise_status"),
        "pairwise_receipts_sha256": hash_artifact((candidate_decision.get("ranking") or {}).get("pairwise_records") or []),
    }
    binding_core["winner_binding_sha256"] = hash_artifact(binding_core)
    binding_core["bound_at_utc"] = now_utc()
    write_json_atomic(run_dir / "manifests" / "WINNER_BINDING.json", binding_core)
    return {
        "bound": True,
        "binding": binding_core,
        "creative_plan": mat["creative_plan"],
        "layout_plan": mat["layout_plan"],
        "motion_graph": mat["motion_graph"],
    }
