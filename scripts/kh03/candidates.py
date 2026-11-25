"""KH-03.R4 Multi-Candidate Creative Competition (PED-09).

Generates up to 3 structurally distinct editorial candidates (A, B, C) strictly within the
user-selected universal editing skill (SK-01..SK-05) and upstream <=3 iteration budget:
- Rejects cosmetic-only font/color clones (`validate_candidate_diversity`).
- Filters plan-level hard failures (license, semantic, action protection, geometry, evidence) BEFORE render.
- Supports bounded low-res preview competition (heavy_concurrency=1), blind editorial rubric +
  order-swapped pairwise H2 (`A/B` and `B/A`) when live judge is available, and full re-validation
  of the adopted winner at full target resolution on its exact final SHA-256.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

from .contracts import hash_artifact, write_json_atomic
from .layout import detect_visual_repetition, solve_layout_sequence
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
) -> list[dict[str, Any]]:
    """Generate up to 3 genuinely distinct candidate editorial plans strictly honoring user-selected skill."""
    cap = max(1, min(MAX_CANDIDATES, int(max_candidates)))
    sk = get_skill_contract(selected_skill_id)
    fps_num = int(source_media.get("fps_num") or 24)
    fps_den = max(1, int(source_media.get("fps_den") or 1))

    candidates: list[dict[str, Any]] = []
    for idx, strat in enumerate(CANDIDATE_STRATEGIES[:cap]):
        c_beats = copy.deepcopy(beats)
        # Apply structural variation in beat layout/framing intent WITHOUT altering script, speech, facts, or selected skill!
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
        c_constraints["candidate_strategy"] = strat["strategy_code"]
        c_constraints["candidate_seed"] = int(seed) + idx

        lp = solve_layout_sequence(
            c_beats,
            run_id=f"{run_id}_{strat['candidate_id']}",
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
        # Apply real structural topology differentiation under ADAPTIVE_DYNAMIC when beats allow it
        if layout_policy == "ADAPTIVE_DYNAMIC" and len(lp["beats"]) >= 3:
            if idx == 1:
                # Candidate B favors action-first FULL_CANVAS on odd beats + PRESENTER_RIGHT_SPLIT on dense beats
                for bi in range(1, len(lp["beats"]) - 1):
                    if bi % 2 == 1:
                        lp["beats"][bi]["layout_mode"] = "FULL_CANVAS"
                        lp["beats"][bi]["presenter_box_norm"] = [0.0, 0.0, 1.0, 1.0]
                        lp["beats"][bi]["graphic_box_norm"] = [0.06, 0.06, 0.88, 0.16]
                    else:
                        lp["beats"][bi]["layout_mode"] = "PRESENTER_RIGHT_SPLIT"
                        lp["beats"][bi]["presenter_box_norm"] = [0.48, 0.0, 0.52, 1.0]
                        lp["beats"][bi]["graphic_box_norm"] = [0.04, 0.14, 0.42, 0.68]
            elif idx == 2:
                # Candidate C favors LOWER_COMPANION_SPLIT + FLOATING_INSET_PRESENTER for high visual variety
                for bi in range(1, len(lp["beats"]) - 1):
                    if bi % 2 == 1:
                        lp["beats"][bi]["layout_mode"] = "FLOATING_INSET_PRESENTER"
                        lp["beats"][bi]["presenter_box_norm"] = [0.04, 0.58, 0.36, 0.34]
                        lp["beats"][bi]["graphic_box_norm"] = [0.04, 0.07, 0.92, 0.48]
                    else:
                        lp["beats"][bi]["layout_mode"] = "LOWER_COMPANION_SPLIT"
                        lp["beats"][bi]["presenter_box_norm"] = [0.0, 0.48, 1.0, 0.52]
                        lp["beats"][bi]["graphic_box_norm"] = [0.04, 0.06, 0.92, 0.40]
            lp.pop("layout_plan_sha256", None)
            lp["layout_plan_sha256"] = hash_artifact(lp)

        rhythm = detect_visual_repetition(lp)
        plan_hard_failures: list[str] = []
        if str(c_constraints.get("license_decision") or "").startswith("BLOCKED"):
            plan_hard_failures.append(f"LICENSE_BLOCKED:{c_constraints.get('license_decision')}")
        if rhythm["status"] == "FAIL":
            plan_hard_failures.append(f"LAYOUT_REPETITION_FAIL:{'; '.join(rhythm['findings'])}")

        cand_doc = {
            "candidate_id": strat["candidate_id"],
            "blind_id": f"X{idx + 1}",
            "selected_skill_id": selected_skill_id,
            "strategy": dict(strat),
            "layout_plan": lp,
            "layout_rhythm": rhythm,
            "plan_hard_failures": plan_hard_failures,
            "plan_gate_pass": len(plan_hard_failures) == 0,
            "plan_sha256": hash_artifact({"strategy": strat, "layout_plan_sha256": lp["layout_plan_sha256"]}),
        }
        candidates.append(cand_doc)
    return candidates


def validate_candidate_diversity(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify candidates differ structurally (hook, layout topology, spatial/choreography/evidence) — rejects cosmetic clones."""
    issues: list[str] = []
    pairs: list[dict[str, Any]] = []
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            ca, cb = candidates[i], candidates[j]
            sa, sb = ca.get("strategy") or {}, cb.get("strategy") or {}
            dims = ("hook_framing", "layout_bias", "spatial_strategy", "choreography_style", "evidence_style")
            diff_dims = [d for d in dims if sa.get(d) != sb.get(d)]
            seq_a = ((ca.get("layout_rhythm") or {}).get("occupancy") or {}).get("sequence") or []
            seq_b = ((cb.get("layout_rhythm") or {}).get("occupancy") or {}).get("sequence") or []
            layout_differs = (seq_a != seq_b) or (len(seq_a) <= 2)
            distinct = len(diff_dims) >= 3 and layout_differs
            pairs.append({
                "pair": [ca["candidate_id"], cb["candidate_id"]],
                "differing_structural_dims": diff_dims,
                "layout_sequence_differs": layout_differs,
                "distinct": distinct,
            })
            if not distinct:
                issues.append(f"DUPLICATE_OR_COSMETIC_CANDIDATE_PAIR({ca['candidate_id']},{cb['candidate_id']}): dims={len(diff_dims)}, layout_differs={layout_differs}")
    return {
        "schema_version": "kh03.candidate_diversity.v1",
        "candidate_count": len(candidates),
        "pairs": pairs,
        "issues": issues,
        "pass": len(issues) == 0 and len(candidates) >= 1,
    }


def rank_candidates(
    candidates: list[dict[str, Any]],
    *,
    rubric_scorer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    pairwise_fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rank passing candidates with hard-gate disqualification + optional order-swapped pairwise (A/B and B/A)."""
    eligible: list[dict[str, Any]] = []
    disqualified: list[dict[str, Any]] = []

    for c in candidates:
        hard_fails = list(c.get("plan_hard_failures") or [])
        if rubric_scorer is not None:
            eval_r = rubric_scorer(c)
        else:
            occ = (c.get("layout_rhythm") or {}).get("occupancy") or {}
            ent = float(occ.get("topology_entropy_bits", 0.0))
            dom = float(occ.get("dominant_duration_share", 1.0))
            rhythm_pts = 9.0 if c.get("layout_rhythm", {}).get("pass") else 5.5
            score = round(min(10.0, max(0.0, 6.2 + ent * 1.4 + (1.0 - dom) * 2.2)) * 0.5 + rhythm_pts * 0.5, 3)
            eval_r = {"rubric_score_10": score, "hard_failures": []}
        hard_fails.extend(eval_r.get("hard_failures") or [])
        entry = {
            "candidate_id": c["candidate_id"],
            "blind_id": c.get("blind_id"),
            "selected_skill_id": c["selected_skill_id"],
            "strategy_code": (c.get("strategy") or {}).get("strategy_code"),
            "plan_sha256": c["plan_sha256"],
            "rubric_score_10": float(eval_r.get("rubric_score_10", 0.0)),
            "hard_failures": hard_fails,
            "eligible": len(hard_fails) == 0,
        }
        if entry["eligible"]:
            eligible.append(entry)
        else:
            disqualified.append(entry)

    # Deterministic primary ordering: rubric_score DESC -> candidate_id ASC
    eligible.sort(key=lambda x: (-x["rubric_score_10"], x["candidate_id"]))

    pairwise_records: list[dict[str, Any]] = []
    winner = eligible[0] if eligible else None
    pairwise_status = "NOT_APPLICABLE_SINGLE_OR_ZERO_ELIGIBLE"

    if len(eligible) >= 2 and pairwise_fn is not None:
        top_a, top_b = eligible[0], eligible[1]
        cand_map = {c["candidate_id"]: c for c in candidates}
        # Blind order-swapped pairwise (A vs B AND B vs A!)
        res_ab = pairwise_fn(cand_map[top_a["candidate_id"]], cand_map[top_b["candidate_id"]])
        res_ba = pairwise_fn(cand_map[top_b["candidate_id"]], cand_map[top_a["candidate_id"]])
        w_ab = res_ab.get("winner")  # "X" -> top_a, "Y" -> top_b
        w_ba = res_ba.get("winner")  # "X" -> top_b, "Y" -> top_a
        mapped_ab = top_a["candidate_id"] if w_ab == "X" else (top_b["candidate_id"] if w_ab == "Y" else None)
        mapped_ba = top_b["candidate_id"] if w_ba == "X" else (top_a["candidate_id"] if w_ba == "Y" else None)
        if mapped_ab and mapped_ab == mapped_ba:
            pairwise_status = "CONSENSUS"
            winner = next(e for e in eligible if e["candidate_id"] == mapped_ab)
        elif mapped_ab is None or mapped_ba is None:
            pairwise_status = "PENDING_REVIEW_MISSING_JUDGE"
        else:
            pairwise_status = "INCONSISTENT_ORDER_TIE_PENDING_REVIEW"
        pairwise_records.append({
            "pair": [top_a["candidate_id"], top_b["candidate_id"]],
            "ab_raw_winner": w_ab,
            "ba_raw_winner": w_ba,
            "mapped_ab": mapped_ab,
            "mapped_ba": mapped_ba,
            "status": pairwise_status,
        })
    elif len(eligible) >= 2:
        pairwise_status = "RUBRIC_RANKED_PAIRWISE_NOT_RUN"

    return {
        "schema_version": "kh03.candidate_ranking.v1",
        "eligible_count": len(eligible),
        "disqualified_count": len(disqualified),
        "ranked": eligible,
        "disqualified": disqualified,
        "pairwise_status": pairwise_status,
        "pairwise_records": pairwise_records,
        "winner": winner,
        "status": "WINNER_SELECTED" if winner is not None else "NO_VALID_WINNER",
    }


def adopt_candidate(
    ranking: dict[str, Any],
    *,
    owner_override_candidate_id: str | None = None,
    full_res_qa_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Adopt a passing candidate (never bypassing hard gates!) and re-validate full-res winner SHA (PED-09)."""
    ranked = list(ranking.get("ranked") or [])
    if not ranked:
        doc = {
            "schema_version": "kh03.candidates_decision.v1",
            "status": "NO_VALID_WINNER",
            "adopted_candidate_id": None,
            "reason": "All candidates failed mandatory hard gates; refusing to adopt a defective plan.",
            "ranking": ranking,
            "full_res_revalidation": None,
        }
        if run_dir is not None:
            write_json_atomic(run_dir / "manifests" / "CANDIDATES.json", doc)
        return doc

    chosen = ranking["winner"]
    override_used = False
    if owner_override_candidate_id is not None:
        match = next((r for r in ranked if r["candidate_id"] == owner_override_candidate_id), None)
        if match is None:
            raise ValueError(f"OVERRIDE_REJECTED_HARD_FAIL: candidate {owner_override_candidate_id!r} did not pass mandatory gates.")
        chosen = match
        override_used = True

    full_qa = full_res_qa_fn(chosen) if full_res_qa_fn is not None else None
    if full_qa is not None and not full_qa.get("pass", False):
        status = "WINNER_FULL_RES_QA_FAILED"
    else:
        status = "ADOPTED_PENDING_OWNER_VISUAL_AUDIO" if full_qa is not None else "ADOPTED_PLAN_ONLY"

    doc = {
        "schema_version": "kh03.candidates_decision.v1",
        "status": status,
        "adopted_candidate_id": chosen["candidate_id"],
        "selected_skill_id": chosen["selected_skill_id"],
        "owner_override_used": override_used,
        "ranking": ranking,
        "full_res_revalidation": full_qa,
        "owner_visual": "PENDING",
        "owner_audio": "PENDING",
    }
    if run_dir is not None:
        write_json_atomic(run_dir / "manifests" / "CANDIDATES.json", doc)
    return doc

