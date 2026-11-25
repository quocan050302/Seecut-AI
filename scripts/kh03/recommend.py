"""KH-03 Five Universal Editing Skills Registry, Cross-Niche Recommender & User Menu Issuer.

Strictly five universal skills (SK-01..SK-05) — NO hidden AUTO or HYBRID sixth skill.
Scores are editorial fit estimates (never view/CTR/retention predictions), using deterministic
rubric weights and tie-breaking. NEVER auto-selects a skill!
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import REGISTRY_VERSION, VALID_SKILL_IDS
from .contracts import (
    STATE_AWAITING_USER_SELECTION,
    assert_kh03_schema,
    hash_artifact,
    transition_state,
    write_json_atomic,
)

RUBRIC_WEIGHTS: dict[str, float] = {
    "content_communication_fit": 0.25,
    "explanation_burden_fit": 0.20,
    "narrative_hook_fit": 0.18,
    "visual_craft_product_fit": 0.15,
    "source_action_continuity_fit": 0.12,
    "asset_feasibility_fit": 0.10,
}


def build_skill_registry() -> dict[str, Any]:
    """Return authoritative, schema-validated registry of the exact FIVE universal skills."""
    skills: list[dict[str, Any]] = [
        {
            "skill_id": "SK-01",
            "version": "1.0.0",
            "name": "Viral Dynamic Editing",
            "short_tagline": "High-energy truthful hooks, pattern shifts, kinetic emphasis & rhythmic sound",
            "intent": "Deliver energetic clarity, truthful pattern interruption, and fast curiosity-to-payoff momentum across any niche without sacrificing comprehension.",
            "pacing_principles": [
                "Adapt visual change density to semantic beats (never fixed every-N-second cuts).",
                "Open within 0-3s using a truthful contradiction, stakes frame, or surprising question.",
                "Respect voice cadence and safe layout boundaries on mobile.",
            ],
            "hook_strategy_pool": ["contradiction_type_lockup", "stakes_first_visual_proof", "counterintuitive_question"],
            "footage_grammar": {"role": "timely_support_broll_and_proof", "allow_full_replace_under_fullscreen_lock": False},
            "explanation_strategy_pool": ["proportional_reservoir", "rolling_counter", "contrast_comparison", "causal_arrow_burst"],
            "motion_design_principles": ["High-contrast kinetic type", "Directional clip wipes", "Clean spring pops", "Zero seizure flashes"],
            "audio_strategy": {"energy_mode": "kinetic", "style": "rhythmic_motivated_accents", "voice_leads": True},
            "failure_modes": ["Effect spam competing with speech", "Premature payoff spoiler", "Fake urgency/FOMO"],
            "compatibility": {"aspects": ["9:16", "16:9", "1:1", "4:5"], "supports_fullscreen_lock": True, "supports_fixed_pip": True},
            "prohibited_behaviors": ["Random jump cuts", "Unreadable word-by-word karaoke spam", "Fabricated claims or fake UI"],
            "fallback_policy": "If external B-roll or SFX unavailable, use A-roll + adaptive semantic motion graphics and disclose FOOTAGE_PARTIAL / BLOCKED_SFX_ASSETS.",
            "qa_rubric": {"priority_dimensions": ["hook_payoff_retention", "clarity_explanation", "audio_comfort_sync"]},
            "doc_path": "references/kh03/SK-01-viral-dynamic.md",
            "preferred_theme": "editorial_obsidian_ivory",
            "preferred_energy": "kinetic",
            "preferred_profile": "editorial_pro",
        },
        {
            "skill_id": "SK-02",
            "version": "1.0.0",
            "name": "Premium Motion Design",
            "short_tagline": "Elevated editorial art direction, spatial masks, refined product reveals & tactile sound",
            "intent": "Craft cohesive, design-forward visual storytelling with bespoke typography, palette harmony, spatial masks, and truthful product/concept reveals across any niche.",
            "pacing_principles": [
                "Measured choreography with confident settle holds around hero typography and product artifacts.",
                "Maintain unified grid geometry and spatial continuity across beats.",
            ],
            "hook_strategy_pool": ["editorial_hero_reveal", "precision_typographic_lockup", "tactile_artifact_spotlight"],
            "footage_grammar": {"role": "curated_verified_product_and_editorial_frames", "allow_full_replace_under_fullscreen_lock": False},
            "explanation_strategy_pool": ["refined_data_reservoir", "comparison_matrix", "proof_frame_reveal", "schematic_callout"],
            "motion_design_principles": ["Brand-derived palette", "Mask wipes & shape continuity", "Restrained power3 easing", "Zero fake 3D or neon spam"],
            "audio_strategy": {"energy_mode": "proof", "style": "refined_tactile_textures", "voice_leads": True},
            "failure_modes": ["Style overriding comprehension", "Repetitive glass-card stacks", "Invented product UI"],
            "compatibility": {"aspects": ["9:16", "16:9", "1:1", "4:5"], "supports_fullscreen_lock": True, "supports_fixed_pip": True},
            "prohibited_behaviors": ["Fake software screenshots", "Ornamental clutter hiding text", "Cheap neon flashes"],
            "fallback_policy": "When full UI captures are absent, elevate verified cover/text typography and document EVIDENCE_GAPS.md.",
            "qa_rubric": {"priority_dimensions": ["visual_art_direction", "clarity_explanation", "motion_footage_integration"]},
            "doc_path": "references/kh03/SK-02-premium-motion.md",
            "preferred_theme": "editorial_burgundy_parchment",
            "preferred_energy": "proof",
            "preferred_profile": "editorial_pro",
        },
        {
            "skill_id": "SK-03",
            "version": "1.0.0",
            "name": "Cinematic Storytelling",
            "short_tagline": "Narrative tension, atmosphere, causal arcs, visual callbacks & measured sound bridges",
            "intent": "Build emotional stakes, atmosphere, chronological/causal progression, and earned payoff at any length from compact 20s narratives to long-form essays across any niche.",
            "pacing_principles": [
                "Honor dramatic pauses, emotional holds, and narrative turns.",
                "Use J/L-style audio continuity bridges only when cut policy and A/V sync permit.",
            ],
            "hook_strategy_pool": ["in_media_res_question", "atmospheric_stakes_anchor", "visual_callback_setup"],
            "footage_grammar": {"role": "atmospheric_broll_timelines_and_callbacks", "allow_full_replace_under_fullscreen_lock": False},
            "explanation_strategy_pool": ["annotated_timeline", "causal_flow_graph", "before_after_real_assets"],
            "motion_design_principles": ["Warm editorial/archival tones", "Zero spring overshoot", "Clean dissolves & motivated pans"],
            "audio_strategy": {"energy_mode": "suspense", "style": "ambience_bridges_and_tension_release", "voice_leads": True},
            "failure_modes": ["Forced 3-act tropes on utility clips", "Unsupported dramatization", "Fabricated archival footage"],
            "compatibility": {"aspects": ["9:16", "16:9", "1:1", "4:5"], "supports_fullscreen_lock": True, "supports_fixed_pip": True},
            "prohibited_behaviors": ["Fake archival claims", "Spring overshoot bounce on serious scenes", "Obscuring human expression"],
            "fallback_policy": "Without contextual B-roll, rely on grounded A-roll framing, timeline graphics, and natural room sound.",
            "qa_rubric": {"priority_dimensions": ["hook_payoff_retention", "semantic_alignment", "audio_comfort_sync"]},
            "doc_path": "references/kh03/SK-03-cinematic-storytelling.md",
            "preferred_theme": "documentary_warm_archive",
            "preferred_energy": "suspense",
            "preferred_profile": "minimal_documentary",
        },
        {
            "skill_id": "SK-04",
            "version": "1.0.0",
            "name": "Intelligent Visual Explainer",
            "short_tagline": "Deep comprehension graphs, exact visual math, causal diagrams & verifiable proof",
            "intent": "Maximize audience understanding of complex mechanisms, numeric allocations, workflows, and cause-effect relationships across any niche.",
            "pacing_principles": [
                "Move step-by-step from known premise to mechanism to verified conclusion.",
                "Hold quantitative/diagram states long enough to read cleanly at 360px mobile scale.",
            ],
            "hook_strategy_pool": ["paradox_equation_hook", "mechanism_question_frame", "misconception_vs_reality"],
            "footage_grammar": {"role": "demonstration_proof_and_labeled_schematics", "allow_full_replace_under_fullscreen_lock": False},
            "explanation_strategy_pool": ["causal_flow_graph", "proportional_reservoir", "rolling_counter", "annotated_timeline", "comparison_matrix"],
            "motion_design_principles": ["One evolving visual model", "Color + pattern + label accessibility", "Exact tabular numerals"],
            "audio_strategy": {"energy_mode": "explanatory", "style": "clean_semantic_step_and_resolution_cues", "voice_leads": True},
            "failure_modes": ["Captions-only on hard concepts", "Misleading chart proportions", "Wrong arithmetic"],
            "compatibility": {"aspects": ["9:16", "16:9", "1:1", "4:5"], "supports_fullscreen_lock": True, "supports_fixed_pip": True},
            "prohibited_behaviors": ["Unverified arithmetic", "Fake UI disguised as real capture", "Decoration masquerading as explanation"],
            "fallback_policy": "When live UI captures are absent, render explicitly labeled CONCEPT_ILLUSTRATION diagrams with verified numbers.",
            "qa_rubric": {"priority_dimensions": ["clarity_explanation", "semantic_alignment", "motion_footage_integration"]},
            "doc_path": "references/kh03/SK-04-visual-explainer.md",
            "preferred_theme": "explainer_cobalt_chalk",
            "preferred_energy": "explanatory",
            "preferred_profile": "editorial_pro",
        },
        {
            "skill_id": "SK-05",
            "version": "1.0.0",
            "name": "Natural Retention Editing",
            "short_tagline": "Authentic creator presence, real physical action continuity & restrained clear overlays",
            "intent": "Preserve genuine human delivery and real-world physical action while sustaining clarity and engagement through restrained, intentional graphics and natural audio.",
            "pacing_principles": [
                "Edit around natural breaths, eye contact, expressions, and physical action continuity.",
                "Never jump across hand/object states or hide real physical open loops.",
            ],
            "hook_strategy_pool": ["authentic_presence_plus_compact_tension", "real_action_open_loop", "conversational_curiosity"],
            "footage_grammar": {"role": "selective_negative_space_insets_preserving_aroll", "allow_full_replace_under_fullscreen_lock": False},
            "explanation_strategy_pool": ["compact_evolving_ledger", "negative_space_annotations", "clean_payoff_ring"],
            "motion_design_principles": ["Harmonized source palette", "Lightweight compact overlays", "Zero dimming of speaker", "Clean exits"],
            "audio_strategy": {"energy_mode": "calm", "style": "natural_room_sound_plus_restrained_micro_accents", "voice_leads": True},
            "failure_modes": ["Confusing restraint with unedited raw video", "Aggressive sales chrome", "Covering active hands/props"],
            "compatibility": {"aspects": ["9:16", "16:9", "1:1", "4:5"], "supports_fullscreen_lock": True, "supports_fixed_pip": True},
            "prohibited_behaviors": ["Manufacturing fake open loops", "Aggressive hype badges", "Hiding presenter or physical props"],
            "fallback_policy": "Operates natively on raw A-roll + restrained original graphics + preserved natural audio.",
            "qa_rubric": {"priority_dimensions": ["semantic_alignment", "audio_comfort_sync", "clarity_explanation"]},
            "doc_path": "references/kh03/SK-05-natural-retention.md",
            "preferred_theme": "seecut_geek_craft",
            "preferred_energy": "calm",
            "preferred_profile": "editorial_pro",
        },
    ]

    reg = {
        "schema_version": "kh03.skill_registry.v1",
        "registry_version": REGISTRY_VERSION,
        "allow_auto_skill": False,
        "skills": skills,
    }
    assert_kh03_schema(reg, "skill_registry")
    return reg


def get_skill_contract(skill_id: str) -> dict[str, Any]:
    reg = build_skill_registry()
    for sk in reg["skills"]:
        if sk["skill_id"] == skill_id:
            return sk
    raise ValueError(f"Invalid KH-03 skill_id {skill_id!r}; expected one of {VALID_SKILL_IDS}")


def recommend_skills(
    input_analysis: dict[str, Any],
    semantic_graph: dict[str, Any],
    *,
    constraints: dict[str, Any] | None = None,
    available_asset_count: int = 0,
    license_decision: str = "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
) -> dict[str, Any]:
    """Score ALL FIVE universal skills purely from content/audio/action signals (zero niche hardcoding)."""
    reg = build_skill_registry()
    ns = input_analysis["narrative_structure"]
    exp_b = float(ns["explanation_burden"])
    act_c = float(ns["action_continuity_score"])
    has_hook = bool(ns["has_hook_tension"])
    has_cta = bool(ns["has_cta"])
    has_steps = bool(ns["has_step_sequence"])
    has_causal = bool(ns["has_causal_chain"])
    n_nums = len(input_analysis.get("numeric_claims", []))
    intent = input_analysis.get("intent", "mixed")
    dur = float(input_analysis["media"]["duration_sec"])
    eff_c = dict(input_analysis.get("constraints") or {})
    eff_c.update(constraints or {})
    fullscreen_lock = bool(eff_c.get("fullscreen_lock") or eff_c.get("layout_lock") == "FULLSCREEN_9_16")

    tech_blockers: list[str] = []
    if license_decision in ("BLOCKED_COMMERCIAL_LICENSE", "BLOCKED_LICENSE_UNRESOLVED"):
        tech_blockers.append(
            f"PROBLEM-01 License Gate ({license_decision}): YeJe-cpu/SeeCut render blocked without written commercial permission from aiesecxuanli@gmail.com."
        )
    if input_analysis["asr_status"] == "ASR_UNCERTAIN":
        tech_blockers.append("ASR_UNCERTAIN: Supplied script and spoken audio diverged noticeably; review transcript reconciliation.")

    # Compute transparent dimension scores (0..100) for every skill based solely on measured signals
    scored: list[dict[str, Any]] = []
    for sk in reg["skills"]:
        sid = sk["skill_id"]
        if sid == "SK-01":
            dims = {
                "content_communication_fit": 86.0 if (has_hook and dur <= 45.0) else 72.0,
                "explanation_burden_fit": 78.0 if n_nums >= 2 else 68.0,
                "narrative_hook_fit": 94.0 if has_hook else 66.0,
                "visual_craft_product_fit": 80.0 if has_cta else 72.0,
                "source_action_continuity_fit": 76.0 if not fullscreen_lock else 78.0,
                "asset_feasibility_fit": 84.0 if available_asset_count > 0 else 78.0,
            }
            why = "Strong hook tension & punchy short-form cadence benefit from kinetic contrast, pattern shifts, and rhythmic accents."
            trade_offs = ["Higher visual energy can compete with dense narration if not carefully paced.", "Less contemplative than SK-03/SK-05."]
            preview = "Frame-0 contradiction/stakes lockup, responsive kinetic emphasis, sharp directional wipes, and rhythmic SFX accents."
        elif sid == "SK-02":
            dims = {
                "content_communication_fit": 88.0 if intent in ("persuade_product", "mixed") else 74.0,
                "explanation_burden_fit": 76.0 if (n_nums >= 1 or has_steps) else 70.0,
                "narrative_hook_fit": 78.0 if has_hook else 72.0,
                "visual_craft_product_fit": 94.0 if (has_cta or available_asset_count > 0) else 80.0,
                "source_action_continuity_fit": 80.0,
                "asset_feasibility_fit": 88.0 if available_asset_count > 0 else 72.0,
            }
            why = "Elevated visual polish, bespoke palette harmony, and tactile product/concept framing maximize perceived quality."
            trade_offs = ["Requires disciplined restraint to avoid ornamental graphics outshining core explanation.", "Best with verified product/visual assets."]
            preview = "Unified editorial grid, source-derived palette, smooth mask wipes, verified artifact/product showcase, refined tactile sound."
        elif sid == "SK-03":
            dims = {
                "content_communication_fit": 90.0 if intent in ("storytell", "document") else (76.0 if dur >= 28.0 else 66.0),
                "explanation_burden_fit": 76.0 if has_causal else 68.0,
                "narrative_hook_fit": 84.0 if (has_causal or intent == "storytell") else 70.0,
                "visual_craft_product_fit": 74.0,
                "source_action_continuity_fit": 82.0,
                "asset_feasibility_fit": 80.0 if available_asset_count > 0 else 74.0,
            }
            why = "Causal arc, stakes progression, and atmospheric pacing shine with motivated visual callbacks and measured sound bridges."
            trade_offs = ["Slower emotional build may feel understated for purely utility/rapid-math hooks.", "Benefits greatly from contextual B-roll."]
            preview = "Atmospheric opening stakes, chronological/causal visual progression, zero-overshoot dissolves, and ambient sound bridges."
        elif sid == "SK-04":
            dims = {
                "content_communication_fit": 94.0 if (exp_b >= 0.45 or n_nums >= 2 or has_steps) else 72.0,
                "explanation_burden_fit": min(98.0, 58.0 + exp_b * 42.0),
                "narrative_hook_fit": 86.0 if (has_hook and (n_nums >= 2 or has_causal)) else 72.0,
                "visual_craft_product_fit": 82.0,
                "source_action_continuity_fit": 82.0 if fullscreen_lock else 80.0,
                "asset_feasibility_fit": 90.0,
            }
            why = f"High explanatory density (explanation_burden={exp_b:.2f}, numeric_claims={n_nums}, steps={has_steps}, causal={has_causal}) strongly rewards structured visual explanation."
            trade_offs = ["Prioritizes precision and readability over cinematic drama or rapid visual churn.", "Must keep mobile 360px diagrams uncluttered."]
            preview = "Evolving visual explanation model (exact proportional math, causal arrows or annotated steps) synced word-by-word with clean step cues."
        else:  # SK-05
            dims = {
                "content_communication_fit": 84.0 if (act_c >= 0.60 or fullscreen_lock) else 72.0,
                "explanation_burden_fit": 74.0 if exp_b <= 0.65 else 70.0,
                "narrative_hook_fit": 76.0 if act_c >= 0.55 else 68.0,
                "visual_craft_product_fit": 76.0,
                "source_action_continuity_fit": min(96.0, 62.0 + act_c * 34.0 + (6.0 if fullscreen_lock else 0.0)),
                "asset_feasibility_fit": 92.0,
            }
            why = f"Strong physical creator presence & action continuity (action_score={act_c:.2f}, open_loops={len(input_analysis.get('action_open_loops', []))}) thrive with lightweight negative-space overlays."
            trade_offs = ["More restrained visual punch than SK-01/SK-02.", "Complex multi-variable concepts need concise negative-space summaries."]
            preview = "Creator and physical actions remain uninterrupted centerpiece; compact negative-space visual ledger + natural audio lead."

        total = round(sum(dims[k] * w for k, w in RUBRIC_WEIGHTS.items()), 2)
        prereqs = [
            f"Explicit user choice via `select --skill {sid}` (mandatory gate).",
            "Rights-cleared external footage/SFX if optional enhancements are requested.",
        ]
        blockers = list(tech_blockers)
        scored.append({
            "skill_id": sid,
            "name": sk["name"],
            "fit_score": total,
            "score_breakdown": {k: round(v, 2) for k, v in dims.items()},
            "feasible": True,
            "why_fit": why,
            "trade_offs": trade_offs,
            "expected_edit_preview": preview,
            "prerequisites": prereqs,
            "blockers": blockers,
        })

    # Deterministic tie-break: fit_score desc -> feasible desc -> skill_id asc
    ranked = sorted(scored, key=lambda c: (-c["fit_score"], not c["feasible"], c["skill_id"]))
    best = ranked[0]
    # Keep canonical SK-01..SK-05 presentation order in candidates array
    candidates_ordered = sorted(scored, key=lambda c: c["skill_id"])

    rec = {
        "schema_version": "kh03.recommendation.v1",
        "run_id": input_analysis["run_id"],
        "registry_version": reg["registry_version"],
        "video_sha256": input_analysis["video_sha256"],
        "script_sha256": input_analysis["script_sha256"],
        "recommended_skill_id": best["skill_id"],
        "recommendation_rationale": (
            f"Recommended {best['skill_id']} ({best['name']}, editorial fit {best['fit_score']}/100): {best['why_fit']} "
            "All five universal skills remain available — user MUST choose explicitly before execution continues."
        ),
        "editorial_fit_disclaimer": (
            "Fit scores are deterministic editorial heuristics only — NOT measured audience retention, CTR, or live vidIQ predictions."
        ),
        "user_selection_required": True,
        "auto_selected": False,
        "tie_break_rule": "fit_score DESC -> feasible DESC -> skill_id ASC",
        "rubric_weights": dict(RUBRIC_WEIGHTS),
        "candidates": candidates_ordered,
        "technical_blockers": tech_blockers,
    }
    assert_kh03_schema(rec, "recommendation")
    return rec


def format_recommendation_menu(recommendation: dict[str, Any], *, run_dir: Path) -> str:
    """Render readable 5-skill selection menu with exact resume command (works in any host/terminal)."""
    rec_id = recommendation["recommended_skill_id"]
    lines = [
        f"# KH-03 Universal Editing Skill Recommendation — `{recommendation['run_id']}`",
        "",
        f"> **Status:** `AWAITING_USER_SELECTION` — **MANDATORY STOP.** AI will NOT auto-select a skill.",
        f"> **Disclaimer:** {recommendation['editorial_fit_disclaimer']}",
        "",
        f"## Recommended Option: `{rec_id}`",
        recommendation["recommendation_rationale"],
        "",
    ]
    if recommendation.get("technical_blockers"):
        lines.append("## Technical / License Notices")
        for b in recommendation["technical_blockers"]:
            lines.append(f"- {b}")
        lines.append("")

    lines.extend([
        "## Choose ONE of the Five Universal Editing Skills",
        "",
        "| # | Skill ID | Name | Editorial Fit | Why / Best Use | Key Trade-Off |",
        "|---|---|---|---:|---|---|",
    ])
    for idx, c in enumerate(recommendation["candidates"], start=1):
        badge = " **(RECOMMENDED)**" if c["skill_id"] == rec_id else ""
        t_off = c["trade_offs"][0] if c["trade_offs"] else ""
        lines.append(
            f"| {idx} | `{c['skill_id']}`{badge} | {c['name']} | {c['fit_score']:.1f}/100 | {c['why_fit']} | {t_off} |"
        )

    lines.extend([
        "",
        "### Expected Visual & Audio Preview by Skill",
        "",
    ])
    for c in recommendation["candidates"]:
        lines.append(f"- **`{c['skill_id']}` — {c['name']}**: {c['expected_edit_preview']}")

    lines.extend([
        "",
        "## How to Proceed (Explicit User Selection Required)",
        "Reply with one of `SK-01`, `SK-02`, `SK-03`, `SK-04`, or `SK-05`, OR run:",
        "```bash",
        f"python -m scripts.kh03 select --run-root \"{run_dir}\" --skill {rec_id}",
        f"python -m scripts.kh03 execute --run-root \"{run_dir}\" --mode plan",
        "```",
        "",
    ])
    return "\n".join(lines)


def issue_recommendation(
    recommendation: dict[str, Any],
    run_dir: Path,
    *,
    host_ui_supported: bool = False,
) -> dict[str, Any]:
    """Persist SKILL_RECOMMENDATION.json/.md, transition strictly to AWAITING_USER_SELECTION, and return menu."""
    assert_kh03_schema(recommendation, "recommendation")
    rec_json_p = write_json_atomic(run_dir / "selection" / "SKILL_RECOMMENDATION.json", recommendation)
    menu_md = format_recommendation_menu(recommendation, run_dir=run_dir)
    rec_md_p = run_dir / "selection" / "SKILL_RECOMMENDATION.md"
    rec_md_p.write_text(menu_md, encoding="utf-8")

    transition_state(
        run_dir,
        STATE_AWAITING_USER_SELECTION,
        reason=f"Issued 5-skill recommendation ({recommendation['recommended_skill_id']}); halted awaiting explicit user choice.",
        run_id=recommendation["run_id"],
    )
    return {
        "status": STATE_AWAITING_USER_SELECTION,
        "recommended_skill_id": recommendation["recommended_skill_id"],
        "recommendation_sha256": hash_artifact(recommendation),
        "recommendation_json": str(rec_json_p),
        "recommendation_md": str(rec_md_p),
        "interaction_mode": "HOST_NATIVE_UI" if host_ui_supported else "TEXT_MENU_FALLBACK",
        "menu_text": menu_md,
        "auto_selected": False,
        "next_command": f'python -m scripts.kh03 select --run-root "{run_dir}" --skill <SK-01|SK-02|SK-03|SK-04|SK-05>',
    }
