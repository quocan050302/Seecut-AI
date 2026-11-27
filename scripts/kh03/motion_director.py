"""DX01-02 — Universal Semantic Motion Director & Reference Style Analyzer.

Selects beat-level motion, footage transforms, transitions, and multi-layer choreography from:
- Verified communicative intent (`claim`, `reveal`, `contrast`, `cause`, `process`, `emphasis`,
  `evidence`, `emotion`, `payoff`, `transition`, `orientation`, `pause`, `none`)
- Source media affordances (talking-head, silent, multi-speaker, faceless, screencast, product,
  documentary, mixed; aspect ratio; available secondary evidence; protected regions)
- Selected universal skill (`SK-01..SK-05`) and explicit user policies (`caption_policy`, `layout_policy`)
- Optional reference-video structural metrics inferred from actual media (with explicit uncertainty;
  ZERO pixel/audio copying, ZERO fabricated metrics, ZERO hard-coded niche lookup tables!).

Eliminates rectangular-card bias by querying `EffectRegistry` for non-card Remotion, SVG path,
footage transform, and dual-moving transition effects with anti-repetition and legitimate `none`
(clean cut / uninterrupted shot) decisions.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .effect_registry import EffectRegistry, get_default_effect_registry
from .ingest_cache import SharedIngestCache, probe_source_cached, single_pass_source_scan

VALID_BEAT_INTENTS: tuple[str, ...] = (
    "claim",
    "reveal",
    "contrast",
    "cause",
    "process",
    "emphasis",
    "evidence",
    "emotion",
    "payoff",
    "transition",
    "orientation",
    "pause",
    "none",
)

# Data-driven skill choreography grammar (strictly cross-niche; NO topic/brand/creator logic!)
_SKILL_CHOREOGRAPHY_PROFILES: dict[str, dict[str, Any]] = {
    "SK-01": {
        "max_effect_density": 0.80,
        "allow_zero_effect_beats": True,
        "preferred_transition": "dx01.trans.whip_blur",
        "secondary_transition": "dx01.trans.mask_wipe",
        "camera_energy": "punchy",
        "prefer_non_card": True,
        "atmosphere_allowed": True,
    },
    "SK-02": {
        "max_effect_density": 0.72,
        "allow_zero_effect_beats": True,
        "preferred_transition": "dx01.trans.mask_wipe",
        "secondary_transition": "dx01.trans.match_reveal",
        "camera_energy": "refined",
        "prefer_non_card": True,
        "atmosphere_allowed": True,
    },
    "SK-03": {
        "max_effect_density": 0.55,
        "allow_zero_effect_beats": True,
        "preferred_transition": "dx01.trans.dual_crossfade",
        "secondary_transition": "cut",
        "camera_energy": "measured",
        "prefer_non_card": True,
        "atmosphere_allowed": True,
    },
    "SK-04": {
        "max_effect_density": 0.78,
        "allow_zero_effect_beats": True,
        "preferred_transition": "dx01.trans.match_reveal",
        "secondary_transition": "dx01.trans.mask_wipe",
        "camera_energy": "clear_purposeful",
        "prefer_non_card": True,
        "atmosphere_allowed": False,
    },
    "SK-05": {
        "max_effect_density": 0.35,
        "allow_zero_effect_beats": True,
        "preferred_transition": "cut",
        "secondary_transition": "dx01.trans.dual_crossfade",
        "camera_energy": "restrained",
        "prefer_non_card": True,
        "atmosphere_allowed": False,
    },
}


def infer_reference_style_profile(
    reference_video_path: Path | None,
    *,
    cache: SharedIngestCache | None = None,
    user_caption_policy: str = "OFF",
) -> dict[str, Any]:
    """Measure real reference pacing/cut/luma characteristics with honest uncertainty (ZERO copying!)."""
    if reference_video_path is None or not Path(reference_video_path).is_file():
        return {
            "schema_version": "kh03.dx01.reference_style.v1",
            "supplied": False,
            "measured": False,
            "uncertainty_note": "No reference video supplied; using data-driven skill & media affordance profile.",
            "effective_caption_policy": user_caption_policy,
        }

    ref_p = Path(reference_video_path).resolve()
    probe = probe_source_cached(ref_p, cache=cache)
    scan = single_pass_source_scan(ref_p, cache=cache, scene_cut_mad_threshold=15.0)
    dur = max(0.5, float(probe["video_duration_sec"]))
    cuts = list(scan.get("scene_cuts") or [])
    cut_times = [0.0] + [float(c["time_sec"]) for c in cuts if 0.25 < float(c["time_sec"]) < dur - 0.25] + [dur]
    shot_durs = [round(cut_times[i + 1] - cut_times[i], 3) for i in range(len(cut_times) - 1) if cut_times[i + 1] > cut_times[i]]
    mean_shot = round(sum(shot_durs) / max(1, len(shot_durs)), 3)
    median_shot = round(sorted(shot_durs)[len(shot_durs) // 2], 3) if shot_durs else round(dur, 3)

    pacing = "fast" if median_shot <= 2.2 else ("moderate" if median_shot <= 4.5 else "calm")
    return {
        "schema_version": "kh03.dx01.reference_style.v1",
        "supplied": True,
        "measured": True,
        "reference_basename": ref_p.name,
        "reference_sha256": probe["sha256"],
        "reference_aspect": probe["aspect_ratio"],
        "reference_duration_sec": round(dur, 3),
        "reference_fps": probe["fps"],
        "detected_cut_count": len(cuts),
        "estimated_shot_count": len(shot_durs),
        "median_shot_duration_sec": median_shot,
        "mean_shot_duration_sec": mean_shot,
        "inferred_pacing_tier": pacing,
        "mean_luma_range": [scan.get("mean_luma_min"), scan.get("mean_luma_max")],
        "sharpness_mean": scan.get("sharpness_mean"),
        "effective_caption_policy": user_caption_policy,
        "pixels_or_audio_reused": False,
        "uncertainty_note": (
            "Shot boundaries are inferred via adjacent-frame MAD (>=15.0); typography style, A/B ratio, "
            "and subjective energy carry heuristic uncertainty and NEVER override user caption_policy or brief."
        ),
    }


def classify_beat_intent(
    beat: dict[str, Any],
    *,
    beat_idx: int,
    total_beats: int,
    phrase: dict[str, Any] | None = None,
    has_secondary_evidence: bool = False,
    is_silent_media: bool = False,
) -> str:
    """Classify a single beat into one of the 13 DX-01 communicative intents purely from verified evidence."""
    fn = str(beat.get("semantic_function") or beat.get("function") or (phrase or {}).get("semantic_role") or "").strip().lower()
    txt = str((phrase or {}).get("text") or beat.get("text") or beat.get("focus_subject") or "").strip()
    has_nums = bool(beat.get("has_numeric_claims") or (phrase or {}).get("numeric_claims") or any(ch.isdigit() for ch in txt))
    has_comp = bool((phrase or {}).get("has_comparison") or fn == "comparison")
    has_causal = bool((phrase or {}).get("has_causal") or fn == "mechanism")
    has_ev = bool(beat.get("proof_status") == "VERIFIED_REAL_EVIDENCE" or fn in ("evidence", "proof") or has_secondary_evidence)

    if fn in ("intentional_silence", "breath"):
        return "pause"
    if is_silent_media and not txt and beat_idx > 0 and beat_idx < total_beats - 1:
        return "orientation" if beat_idx % 2 == 0 else "reveal"
    if beat_idx == 0:
        return "contrast" if (has_comp or (phrase or {}).get("has_negation")) else ("claim" if txt else "orientation")
    if fn in ("payoff", "cta") or beat_idx == total_beats - 1:
        return "payoff" if (has_nums or fn == "payoff") else ("emotion" if fn == "reaction" else "claim")
    if has_ev and fn in ("evidence", "proof", "demonstration"):
        return "evidence"
    if has_comp:
        return "contrast"
    if has_causal:
        return "cause"
    if fn in ("step", "demonstration") or has_nums:
        return "process"
    if fn in ("reveal", "turning_point"):
        return "reveal"
    if fn in ("reaction", "tension", "problem"):
        return "emotion"
    if fn == "context":
        return "orientation"
    return "emphasis" if txt else "none"


def direct_motion_plan(
    *,
    beats: list[dict[str, Any]],
    selected_skill_id: str,
    aspect_ratio: str = "9:16",
    media_type: str = "talking_head",
    fps: float = 24.0,
    phrases: list[dict[str, Any]] | None = None,
    has_secondary_sources: bool = False,
    secondary_source_ids: list[str] | None = None,
    caption_policy: str = "OFF",
    layout_policy: str = "ADAPTIVE_DYNAMIC",
    reference_style: dict[str, Any] | None = None,
    registry: EffectRegistry | None = None,
    reduced_motion: bool = False,
    max_cost_tier: str = "high",
) -> dict[str, Any]:
    """Direct universal non-card beat choreography with anti-repetition, explicit rationale, and fallback paths."""
    reg = registry or get_default_effect_registry()
    prof = _SKILL_CHOREOGRAPHY_PROFILES.get(selected_skill_id, _SKILL_CHOREOGRAPHY_PROFILES["SK-04"])
    ph_list = list(phrases or [])
    sec_ids = list(secondary_source_ids or [])
    is_silent = media_type == "silent"

    # Respect SK-05 / reduced_motion restraint legitimately (zero-effect beats are a feature, not a bug!)
    eff_density = 0.25 if reduced_motion else float(prof["max_effect_density"])
    if reference_style and reference_style.get("measured") and reference_style.get("inferred_pacing_tier") == "calm":
        eff_density = min(eff_density, 0.50)

    decisions: list[dict[str, Any]] = []
    used_effect_history: list[str] = []
    used_family_streak: list[str] = []
    effects_placed = 0
    total_b = max(1, len(beats))

    for idx, beat in enumerate(beats):
        bid = str(beat.get("beat_id") or f"beat_{idx + 1:02d}")
        sf = int(beat.get("start_frame", 0))
        ef = max(sf + 1, int(beat.get("end_frame", sf + int(round(fps * 2.0)))))
        t0 = round(sf / max(1.0, fps), 4)
        t1 = round(ef / max(1.0, fps), 4)
        dur = max(0.1, t1 - t0)
        ph = ph_list[idx] if idx < len(ph_list) else None

        intent = classify_beat_intent(
            beat,
            beat_idx=idx,
            total_beats=total_b,
            phrase=ph,
            has_secondary_evidence=(has_secondary_sources and idx > 0 and idx < total_b - 1),
            is_silent_media=is_silent,
        )

        # Legitimately choose clean shot / zero added graphics when density cap reached or pause/SK-05 breath
        current_density = effects_placed / float(max(1, idx + 1))
        if intent in ("pause", "none") or (
            prof["allow_zero_effect_beats"]
            and idx > 0
            and idx < total_b - 1
            and current_density > eff_density
            and intent not in ("payoff", "evidence")
        ):
            decisions.append({
                "beat_id": bid,
                "start_sec": t0,
                "end_sec": t1,
                "intent": intent,
                "selected_effect_id": None,
                "selected_family": "none",
                "backend": "clean_shot",
                "transition_in": "cut" if idx > 0 else None,
                "footage_effect_id": None,
                "reason": (
                    f"Intentional clean shot / breathing room ({selected_skill_id}, intent={intent}, "
                    f"density={current_density:.2f}/{eff_density:.2f}) — avoids visual oversaturation."
                ),
                "alternatives_considered": [],
                "fallback_id": None,
            })
            used_family_streak.append("none")
            continue

        # Anti-repetition exclusion: avoid reusing the exact same effect back-to-back or repeating one family >2x
        recent_ids = set(used_effect_history[-2:])
        avoid_fam = used_family_streak[-1] if (len(used_family_streak) >= 2 and used_family_streak[-1] == used_family_streak[-2] and used_family_streak[-1] != "none") else None

        role_query = "hook" if idx == 0 else intent
        cands = reg.search(
            semantic_role=role_query,
            media_type=media_type,
            aspect_ratio=aspect_ratio,
            duration_sec=dur,
            max_cost_tier=max_cost_tier,
            caption_policy=caption_policy,
            prefer_non_card=bool(prof["prefer_non_card"]),
            exclude_ids=recent_ids,
            include_legacy=False,
        )
        # Filter out footage-only/transition-only when searching for primary visual beat effect, unless none else
        overlay_or_layer = [c for c in cands if c["family"] != "transitions" and (avoid_fam is None or c["family"] != avoid_fam)]
        if not overlay_or_layer:
            overlay_or_layer = [c for c in cands if c["family"] != "transitions"]

        chosen = overlay_or_layer[0] if overlay_or_layer else None
        resolved_id: str | None = None
        fallback_used: str | None = None
        if chosen is not None:
            rid, r_st, r_why = reg.resolve_with_fallback(chosen["effect_id"])
            resolved_id = rid
            if r_st == "FALLBACK_SELECTED":
                fallback_used = r_why

        # Choose purposeful boundary transition (not mandatory on every shot!)
        trans_in = None
        if idx > 0:
            if selected_skill_id == "SK-05" or reduced_motion:
                trans_in = "cut"
            elif intent in ("evidence", "reveal", "contrast", "payoff"):
                trans_in = prof["preferred_transition"] if (idx % 2 == 1) else prof["secondary_transition"]
            else:
                trans_in = "cut"

        # Choose purposeful direct footage motion ONLY when motivated (NEVER zoom on every beat!)
        footage_fx = None
        if not reduced_motion:
            if idx == 0 and selected_skill_id in ("SK-01", "SK-02", "SK-04"):
                footage_fx = "dx01.footage.push_zoom"
            elif intent == "evidence" and layout_policy == "ADAPTIVE_DYNAMIC" and sec_ids:
                footage_fx = "dx01.depth.floating_evidence" if idx % 2 == 1 else "dx01.footage.pullback_canvas"
            elif intent == "emotion" and selected_skill_id == "SK-03":
                footage_fx = "dx01.footage.exposure_grade"

        if resolved_id:
            effects_placed += 1
            used_effect_history.append(resolved_id)
            used_family_streak.append(chosen["family"] if chosen else "none")
        else:
            used_family_streak.append("none")

        decisions.append({
            "beat_id": bid,
            "start_sec": t0,
            "end_sec": t1,
            "intent": intent,
            "selected_effect_id": resolved_id,
            "selected_family": (chosen["family"] if chosen else "none"),
            "backend": (chosen["backend"] if chosen else "clean_shot"),
            "non_card_form": bool(chosen.get("non_card_form", True)) if chosen else True,
            "transition_in": trans_in,
            "footage_effect_id": footage_fx,
            "fallback_id": (chosen.get("fallback_id") if chosen else None),
            "fallback_used": fallback_used,
            "alternatives_considered": [c["effect_id"] for c in overlay_or_layer[1:4]],
            "reason": (
                f"Selected {resolved_id} (family={chosen['family']}, non_card={chosen['non_card_form']}) "
                f"for intent={intent} under {selected_skill_id} avoiding recent {sorted(recent_ids)}."
                if chosen and resolved_id else f"No eligible registry effect for intent={intent}; keeping clean cut."
            ),
        })

    distinct_families = sorted({d["selected_family"] for d in decisions if d["selected_family"] != "none"})
    distinct_effects = sorted({d["selected_effect_id"] for d in decisions if d["selected_effect_id"]})
    plan = {
        "schema_version": "kh03.dx01.motion_director_plan.v1",
        "selected_skill_id": selected_skill_id,
        "aspect_ratio": aspect_ratio,
        "media_type": media_type,
        "caption_policy": caption_policy,
        "layout_policy": layout_policy,
        "reduced_motion": reduced_motion,
        "reference_style": reference_style or {"supplied": False, "measured": False},
        "beat_count": len(decisions),
        "effects_placed_count": effects_placed,
        "zero_effect_beat_count": sum(1 for d in decisions if d["selected_effect_id"] is None),
        "distinct_families": distinct_families,
        "distinct_effects": distinct_effects,
        "decisions": decisions,
    }
    plan["director_plan_sha256"] = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return plan


__all__ = [
    "VALID_BEAT_INTENTS",
    "classify_beat_intent",
    "direct_motion_plan",
    "infer_reference_style_profile",
]
