"""KH-02.R1 Multi-Layer Sound Design, Adaptive Density & Chunked PCM Stem Mixer (R1.04).

All audio processing runs on derived stems only (source footage immutable).
Uses FFmpeg solely in its authorized audio/stem/mux role — never as a second video compositor.
"""

from __future__ import annotations

import json
import math
import struct
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from .contracts import hash_artifact, is_strict_number, sanitize_identifier, validate_schema
from .sfx_assets import assert_not_test_only_in_production, measure_onset, probe_sfx
from .sound import measure_final_audio

# Illustrative editorial density profiles (NOT mandatory minima or acceptance quotas!)
DENSITY_PROFILES: dict[str, dict[str, Any]] = {
    "calm": {
        "illustrative_30s_range": [0, 8],
        "max_per_3s_guideline": 1,
        "max_avg_per_sec_guideline": 0.30,
        "sfx_rel_voice_db_range": [-20.0, -14.0],
        "default_bgm_rel_db": -24.0,
        "allow_zero_cues": True,
    },
    "explanatory": {
        "illustrative_30s_range": [8, 22],
        "max_per_3s_guideline": 2,
        "max_avg_per_sec_guideline": 0.65,
        "sfx_rel_voice_db_range": [-17.0, -12.0],
        "default_bgm_rel_db": -22.0,
        "allow_zero_cues": True,
    },
    "proof": {
        "illustrative_30s_range": [12, 28],
        "max_per_3s_guideline": 3,
        "max_avg_per_sec_guideline": 0.90,
        "sfx_rel_voice_db_range": [-16.0, -11.0],
        "default_bgm_rel_db": -20.0,
        "allow_zero_cues": True,
    },
    "kinetic": {
        "illustrative_30s_range": [15, 40],
        "max_per_3s_guideline": 4,
        "max_avg_per_sec_guideline": 1.30,
        "sfx_rel_voice_db_range": [-15.0, -10.5],
        "default_bgm_rel_db": -19.0,
        "allow_zero_cues": True,
    },
    "suspense": {
        "illustrative_30s_range": [6, 18],
        "max_per_3s_guideline": 2,
        "max_avg_per_sec_guideline": 0.60,
        "sfx_rel_voice_db_range": [-18.0, -12.0],
        "default_bgm_rel_db": -23.0,
        "allow_zero_cues": True,
    },
    "emotional": {
        "illustrative_30s_range": [0, 6],
        "max_per_3s_guideline": 1,
        "max_avg_per_sec_guideline": 0.25,
        "sfx_rel_voice_db_range": [-20.0, -15.0],
        "default_bgm_rel_db": -24.0,
        "allow_zero_cues": True,
    },
}

ROLE_TO_CATEGORY = {
    "hook_accent": "whoosh",
    "transition_whoosh": "whoosh",
    "object_pop": "pop",
    "ui_click": "click",
    "evidence_reveal": "whoosh",
    "step_click": "click",
    "emphasis_impact": "impact",
    "payoff_chime": "chime",
    "riser": "riser",
    "paper_rustle": "rustle",
    "foley": "foley",
    "ambience": "ambience",
    "bgm": "bgm",
}

ROLE_PRELAP_SEC = {
    "transition_whoosh": -0.08,
    "evidence_reveal": -0.06,
    "riser": -0.35,
    "hook_accent": 0.0,
    "object_pop": 0.0,
    "ui_click": 0.0,
    "step_click": 0.0,
    "emphasis_impact": 0.04,
    "payoff_chime": 0.0,
    "paper_rustle": 0.0,
}

ROLE_DEFAULT_REL_DB = {
    "hook_accent": -12.5,
    "transition_whoosh": -15.0,
    "object_pop": -15.5,
    "ui_click": -16.0,
    "evidence_reveal": -14.5,
    "step_click": -16.0,
    "emphasis_impact": -12.0,
    "payoff_chime": -15.0,
    "riser": -16.0,
    "paper_rustle": -17.0,
    "ambience": -26.0,
    "bgm": -22.0,
}


def resolve_density_policy(
    *,
    mode: str = "talking_head",
    profile: str = "editorial_pro",
    energy_mode: str | None = None,
    duration_sec: float = 20.0,
    approved_exceptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve adaptive sound density profile and log any explicit conflict with upstream 00-总纲 / sfx.py legacy limits."""
    em = energy_mode
    if not em:
        if profile == "minimal_documentary" or mode in ("documentary", "interview"):
            em = "calm"
        elif mode == "narrative":
            em = "suspense"
        elif mode in ("tutorial", "software_demo", "education", "faceless"):
            em = "explanatory"
        elif mode in ("product_demo", "talking_head"):
            em = "proof"
        else:
            em = "kinetic"

    cfg = DENSITY_PROFILES.get(em, DENSITY_PROFILES["proof"])
    exc_ids = {str(e.get("rule_id", "")) for e in (approved_exceptions or [])}
    legacy_cap_3s = 2
    legacy_cap_avg = 0.70

    exceeds_legacy = cfg["max_per_3s_guideline"] > legacy_cap_3s or cfg["max_avg_per_sec_guideline"] > legacy_cap_avg
    r1_exception_approved = "ALLOW_R1_ADAPTIVE_SFX_DENSITY" in exc_ids
    effective_3s = cfg["max_per_3s_guideline"] if (not exceeds_legacy or r1_exception_approved) else legacy_cap_3s
    effective_avg = cfg["max_avg_per_sec_guideline"] if (not exceeds_legacy or r1_exception_approved) else legacy_cap_avg

    conflict_note = None
    if exceeds_legacy:
        if r1_exception_approved:
            conflict_note = (
                f"Upstream sfx.py legacy talking-head guideline (<=2/3s, <=0.7/s) explicitly extended to "
                f"<= {effective_3s}/3s, <= {effective_avg}/s under approved exception ALLOW_R1_ADAPTIVE_SFX_DENSITY."
            )
        else:
            conflict_note = (
                f"Energy mode {em!r} prefers <= {cfg['max_per_3s_guideline']}/3s, but conservative upstream legacy cap "
                f"(<=2/3s, <=0.7/s) kept active because ALLOW_R1_ADAPTIVE_SFX_DENSITY was not explicitly approved."
            )

    return {
        "energy_mode": em,
        "profile": profile,
        "mode": mode,
        "duration_sec": round(duration_sec, 3),
        "illustrative_30s_range": cfg["illustrative_30s_range"],
        "effective_max_per_3s": effective_3s,
        "effective_max_avg_per_sec": effective_avg,
        "sfx_rel_voice_db_range": cfg["sfx_rel_voice_db_range"],
        "default_bgm_rel_db": cfg["default_bgm_rel_db"],
        "allow_zero_cues": True,
        "no_artificial_min_count": True,
        "upstream_legacy_conflict": conflict_note,
    }


def choose_cue_sound(
    role: str,
    verified_assets: list[dict[str, Any]],
    *,
    usage_counts: dict[str, int] | None = None,
    max_repeats_per_asset: int = 2,
    production_mode: bool = True,
) -> dict[str, Any] | None:
    """Select cohesive verified sound asset avoiding monotonous repetition and blocking TEST_ONLY in production."""
    assert_not_test_only_in_production(verified_assets, production_mode=production_mode)
    want_cat = ROLE_TO_CATEGORY.get(role, "foley")
    counts = usage_counts or {}

    matching = [a for a in verified_assets if a.get("category") == want_cat and a.get("approved_purpose") != "BLOCKED"]
    if not matching:
        # Soft fallback only within compatible transient families (e.g. click <-> pop, chime <-> ding)
        compat = {"click": ("pop",), "pop": ("click",), "chime": ("ding",), "ding": ("chime",), "rustle": ("foley",)}.get(want_cat, ())
        matching = [a for a in verified_assets if a.get("category") in compat and a.get("approved_purpose") != "BLOCKED"]
    if not matching:
        return None

    # Prefer least-used asset, then CC0, then deterministic asset_id
    def _key(a: dict[str, Any]) -> tuple[int, int, str]:
        used = counts.get(a["asset_id"], 0)
        over_cap = 1 if used >= max_repeats_per_asset else 0
        lic_rank = 0 if a.get("license_status") == "APPROVED_CC0" else 1
        return (over_cap, used, lic_rank, a["asset_id"])

    matching.sort(key=_key)
    return matching[0]


def align_cue_to_event(
    *,
    cue_id: str,
    beat_id: str | None,
    visual_event_id: str | None,
    visual_trigger_frame: int,
    role: str,
    asset: dict[str, Any] | None,
    fps_num: int,
    fps_den: int = 1,
    audio_sample_rate: int = 48000,
    total_frames: int,
    local_voice_peak_db: float = -8.0,
    local_voice_window: list[float] | None = None,
    justification: str = "",
) -> dict[str, Any]:
    """Align a sound cue to exact output frame & sample clock using measured source onset (never filename guess)."""
    fps_frac = Fraction(fps_num, fps_den)
    fps = float(fps_frac)
    total_dur_sec = float(Fraction(total_frames) / fps_frac)
    total_samples = int(round(total_dur_sec * audio_sample_rate))

    prelap = ROLE_PRELAP_SEC.get(role, 0.0)
    intentionally_early = prelap < 0.0
    intentionally_late = prelap > 0.0
    trigger_type = (
        "anticipation" if prelap < -0.15
        else ("cross_seam" if prelap < 0.0 else ("settle" if prelap > 0.0 else "onset"))
    )

    event_sec = float(Fraction(visual_trigger_frame) / fps_frac)
    raw_intended_sec = event_sec + prelap
    clamped_note = ""
    if raw_intended_sec < 0.0:
        raw_intended_sec = 0.0
        clamped_note = " (prelap clamped cleanly to t=0)"

    intended_onset_sample = min(max(0, total_samples - 1), max(0, int(round(raw_intended_sec * audio_sample_rate))))
    src_onset_sec = float(asset.get("onset_sec", 0.0)) if asset else 0.0
    src_onset_sample = int(round(src_onset_sec * audio_sample_rate))
    src_dur = float(asset.get("duration_sec", 0.45)) if asset else 0.45

    # Trim leading dead air before source onset so cue hits right on intended_onset_sample
    trim_start = max(0.0, src_onset_sec)
    usable_dur = max(0.05, min(src_dur - trim_start, 2.2 if role in ("riser", "payoff_chime") else 1.2))
    # Never accidentally extend past EOS; shorten with clean fade-out at EOS
    remaining_sec = max(0.04, total_dur_sec - raw_intended_sec)
    target_dur = min(usable_dur, remaining_sec)
    trim_end = round(trim_start + target_dur, 4)
    target_samples = max(1, int(round(target_dur * audio_sample_rate)))

    f_start = min(total_frames - 1, max(0, int(math.floor(raw_intended_sec * fps + 0.5))))
    f_end = min(total_frames, max(f_start + 1, int(math.ceil((raw_intended_sec + target_dur) * fps))))

    rel_db = ROLE_DEFAULT_REL_DB.get(role, -15.0)
    # Adjust relative gain using local voice peak vs asset measured peak so loud assets never clip voice
    asset_peak = float(asset.get("peak_db", -6.0)) if asset else -6.0
    desired_peak = local_voice_peak_db + rel_db
    computed_gain = round(max(-42.0, min(6.0, desired_peak - asset_peak)), 2)

    fade_out_ms = min(80.0, max(15.0, target_dur * 150.0))
    if raw_intended_sec + target_dur >= total_dur_sec - 0.02:
        fade_out_ms = min(120.0, max(25.0, remaining_sec * 350.0))

    kind = f"sfx_{ROLE_TO_CATEGORY.get(role, 'foley')}"
    if kind not in ("sfx_hook", "sfx_pop", "sfx_whoosh", "sfx_impact", "sfx_ding", "sfx_click", "sfx_riser", "sfx_rustle", "sfx_chime", "sfx_texture", "sfx_foley"):
        kind = "sfx_foley"

    return {
        "cue_id": sanitize_identifier(cue_id),
        "beat_id": beat_id,
        "visual_event_id": visual_event_id,
        "kind": kind,
        "role": role,
        "asset_ref": asset["asset_id"] if asset else None,
        "asset_id": asset["asset_id"] if asset else None,
        "source_sha256": asset["sha256"] if asset else None,
        "evidence_ref": asset.get("license_evidence") if asset else None,
        "trigger_type": trigger_type,
        "visual_trigger_frame": int(visual_trigger_frame),
        "frame_start": f_start,
        "frame_end": f_end,
        "sample_start": intended_onset_sample,
        "intended_onset_sample": intended_onset_sample,
        "actual_source_onset_sample": src_onset_sample if asset else None,
        "measured_onset_sample": None,
        "trim_start_sec": round(trim_start, 4),
        "trim_end_sec": trim_end,
        "target_duration_samples": target_samples,
        "gain_db": computed_gain,
        "local_voice_window": local_voice_window or [round(max(0.0, event_sec - 1.0), 3), round(min(total_dur_sec, event_sec + 1.0), 3)],
        "fade_in_ms": 8.0,
        "fade_out_ms": round(fade_out_ms, 2),
        "ducking_db": 0.0,
        "ducking_group": "sfx_transient",
        "pan": 0.0,
        "layer_priority": 4,
        "license_ref": asset["license_status"] if asset else "MISSING_LICENSED_ASSET",
        "reason": f"{justification}{clamped_note}".strip() or f"Aligned {role} to frame {visual_trigger_frame}{clamped_note}",
        "justification": f"{justification}{clamped_note}".strip() or f"Purposeful {role} accent on visual event.",
        "allow_overlap_reason": None,
        "intentionally_early": intentionally_early,
        "intentionally_late": intentionally_late,
        "optional_flag": True,
        "status": "VERIFIED_ASSET" if asset else "UNAVAILABLE",
    }


def plan_music_bed(
    beats: list[dict[str, Any]],
    *,
    total_frames: int,
    fps_num: int,
    fps_den: int = 1,
    audio_sample_rate: int = 48000,
    bgm_policy: str = "OFF",
    bgm_asset: dict[str, Any] | None = None,
    local_voice_rms_db: float = -22.0,
    rel_db: float = -22.0,
    source_contains_music: bool = False,
) -> dict[str, Any]:
    """Plan licensed BGM bed under voice with semantic turn fades; strictly OFF if unlicensed or source already has music."""
    if bgm_policy == "OFF":
        return {"status": "OFF_BY_BRIEF", "cue": None, "reason": "BGM disabled by brief."}
    if source_contains_music:
        return {"status": "OFF_BY_BRIEF", "cue": None, "reason": "Source audio already contains music; refusing competing BGM stack."}
    if not bgm_asset or bgm_asset.get("approved_purpose") == "BLOCKED":
        st = "BLOCKED_UNLICENSED" if bgm_policy == "REQUIRED_LICENSED" else "BGM_NOT_AVAILABLE"
        return {"status": st, "cue": None, "reason": "No verified licensed BGM asset available."}

    fps_frac = Fraction(fps_num, fps_den)
    total_dur_sec = float(Fraction(total_frames) / fps_frac)
    total_samples = int(round(total_dur_sec * audio_sample_rate))
    asset_rms = float(bgm_asset.get("rms_db", -16.0))
    # Voice-relative bed target (-18 to -26 dB below local dialogue RMS)
    clamped_rel = max(-26.0, min(-18.0, rel_db))
    target_rms = local_voice_rms_db + clamped_rel
    gain_db = round(max(-45.0, min(0.0, target_rms - asset_rms)), 2)

    cue = {
        "cue_id": "bgm_01_bed",
        "beat_id": beats[0]["beat_id"] if beats else None,
        "visual_event_id": None,
        "kind": "bgm",
        "role": "bgm",
        "asset_ref": bgm_asset["asset_id"],
        "asset_id": bgm_asset["asset_id"],
        "source_sha256": bgm_asset["sha256"],
        "evidence_ref": bgm_asset.get("license_evidence"),
        "trigger_type": "continuous",
        "visual_trigger_frame": 0,
        "frame_start": 0,
        "frame_end": total_frames,
        "sample_start": 0,
        "intended_onset_sample": 0,
        "actual_source_onset_sample": 0,
        "measured_onset_sample": None,
        "trim_start_sec": 0.0,
        "trim_end_sec": round(min(float(bgm_asset["duration_sec"]), total_dur_sec), 4),
        "target_duration_samples": total_samples,
        "gain_db": gain_db,
        "local_voice_window": [0.0, round(total_dur_sec, 3)],
        "fade_in_ms": 250.0,
        "fade_out_ms": 400.0,
        "ducking_db": -9.0,
        "ducking_group": "music_ducked_by_voice",
        "pan": 0.0,
        "layer_priority": 8,
        "license_ref": bgm_asset["license_status"],
        "reason": f"Licensed BGM bed at {clamped_rel:.1f} dB relative to voice RMS with smooth sidechain ducking.",
        "justification": "Supports pacing across visual showcase spans without masking dialogue.",
        "allow_overlap_reason": "continuous_background_bed",
        "intentionally_early": False,
        "intentionally_late": False,
        "optional_flag": bgm_policy == "OPTIONAL_LICENSED",
        "status": "VERIFIED_ASSET",
    }
    return {"status": "PLANNED_LICENSED", "cue": cue, "reason": "Verified licensed BGM bed planned."}


def schedule_ducking(
    speech_spans: list[tuple[float, float]],
    *,
    total_duration_sec: float,
    attack_ms: float = 45.0,
    release_ms: float = 260.0,
    duck_db: float = -9.0,
) -> dict[str, Any]:
    """Compute smooth non-pumping sidechain ducking envelope (attack 20-80ms, release 150-400ms)."""
    att = max(20.0, min(80.0, float(attack_ms)))
    rel = max(150.0, min(400.0, float(release_ms)))
    dk = max(-18.0, min(-3.0, float(duck_db)))

    # Merge closely spaced speech spans (< 0.35s apart) to prevent rhythmic pumping
    merged: list[list[float]] = []
    for s, e in sorted(speech_spans):
        s_c = max(0.0, float(s))
        e_c = min(total_duration_sec, float(e))
        if e_c <= s_c:
            continue
        if merged and (s_c - merged[-1][1]) < 0.35:
            merged[-1][1] = max(merged[-1][1], e_c)
        else:
            merged.append([s_c, e_c])

    segments: list[dict[str, float]] = []
    for s, e in merged:
        segments.append({
            "ramp_down_start_sec": round(max(0.0, s - att / 1000.0), 4),
            "hold_start_sec": round(s, 4),
            "hold_end_sec": round(e, 4),
            "ramp_up_end_sec": round(min(total_duration_sec, e + rel / 1000.0), 4),
            "duck_db": dk,
        })
    return {
        "attack_ms": att,
        "release_ms": rel,
        "duck_db": dk,
        "merged_speech_windows": len(segments),
        "segments": segments,
    }


def plan_soundscape(
    shotbook: dict[str, Any],
    verified_assets: list[dict[str, Any]],
    *,
    voice_asset_id: str | None = None,
    voice_license_ref: str = "OWNER_SUPPLIED_NONCOMMERCIAL",
    voice_peak_db: float = -6.3,
    voice_rms_db: float = -22.2,
    speech_spans: list[tuple[float, float]] | None = None,
    energy_mode: str | None = None,
    sfx_policy: str = "OPTIONAL_LICENSED",
    bgm_policy: str = "OFF",
    bgm_asset: dict[str, Any] | None = None,
    ambience_asset: dict[str, Any] | None = None,
    approved_exceptions: list[dict[str, Any]] | None = None,
    production_mode: bool = True,
) -> dict[str, Any]:
    """Plan complete multi-layer soundscape synchronized with SHOTBOOK V2 events & adaptive density."""
    assert_not_test_only_in_production(verified_assets, production_mode=production_mode)
    spec = shotbook["output_spec"]
    fps_num, fps_den = int(spec["fps_num"]), int(spec["fps_den"])
    fps = fps_num / fps_den
    total_frames = int(spec["total_frames"])
    sr = int(spec.get("audio_sample_rate", 48000))
    dur_sec = total_frames / fps

    density_pol = resolve_density_policy(
        mode=shotbook["mode"],
        profile=shotbook["profile"],
        energy_mode=energy_mode,
        duration_sec=dur_sec,
        approved_exceptions=approved_exceptions or shotbook.get("approved_exceptions"),
    )

    cues: list[dict[str, Any]] = []
    event_coverage: list[dict[str, Any]] = []
    warnings: list[str] = []
    usage_counts: dict[str, int] = {}

    if density_pol.get("upstream_legacy_conflict"):
        warnings.append(density_pol["upstream_legacy_conflict"])

    # 1. Voice backbone stem
    v_pol = shotbook["edit_decision_map"].get("voice_policy", "LOCK_VOICE")
    if v_pol != "SILENT_BY_DESIGN" and voice_asset_id:
        cues.append({
            "cue_id": "aud_voice_backbone",
            "beat_id": shotbook["beats"][0]["beat_id"] if shotbook["beats"] else None,
            "visual_event_id": None,
            "kind": "voice",
            "role": "primary_voice_backbone",
            "asset_ref": voice_asset_id,
            "asset_id": voice_asset_id,
            "source_sha256": shotbook["source_sha_set"].get(voice_asset_id),
            "evidence_ref": voice_license_ref,
            "trigger_type": "continuous",
            "visual_trigger_frame": 0,
            "frame_start": 0,
            "frame_end": total_frames,
            "sample_start": 0,
            "intended_onset_sample": 0,
            "actual_source_onset_sample": 0,
            "measured_onset_sample": 0,
            "trim_start_sec": 0.0,
            "trim_end_sec": round(dur_sec, 4),
            "target_duration_samples": int(round(dur_sec * sr)),
            "gain_db": 0.0,
            "local_voice_window": [0.0, round(dur_sec, 3)],
            "fade_in_ms": 0.0,
            "fade_out_ms": 0.0,
            "ducking_db": 0.0,
            "ducking_group": "voice_master",
            "pan": 0.0,
            "layer_priority": 1,
            "license_ref": voice_license_ref,
            "reason": f"{v_pol} original narration backbone.",
            "justification": "Dialogue intelligibility has top mix priority.",
            "allow_overlap_reason": "primary_voice_backbone",
            "intentionally_early": False,
            "intentionally_late": False,
            "optional_flag": False,
            "status": "VERIFIED_ASSET",
        })

    # 2. Evaluate meaningful visual events & transitions with adaptive density (never auto-SFX every card!)
    beat_map = {b["beat_id"]: b for b in shotbook.get("beats", [])}
    selected_times: list[float] = []
    max_3s = int(density_pol["effective_max_per_3s"])

    def _window_count(t_sec: float) -> int:
        return sum(1 for x in selected_times if abs(x - t_sec) <= 1.5)

    for ev in shotbook.get("visual_events", []):
        eid = ev["event_id"]
        bid = ev["beat_id"]
        beat = beat_map.get(bid, {})
        kind_p = str((ev.get("params") or {}).get("kind", ""))
        t_sec = int(ev["start_frame"]) / fps

        # Skip persistent chrome or subtle decorative strokes from forced SFX
        if kind_p in ("progress_spine", "hand_underline", "hand_circle"):
            event_coverage.append({
                "event_id": eid, "beat_id": bid, "decision": "KEEP_NATURAL",
                "reason": "Subtle chrome/hand-drawn accent intentionally left quiet to avoid cluttered mix.",
            })
            continue

        # Calm / emotional intentional quiet holds
        if density_pol["energy_mode"] in ("calm", "emotional") and beat.get("intentional_hold"):
            event_coverage.append({
                "event_id": eid, "beat_id": bid, "decision": "SILENCE",
                "reason": "Intentional quiet emotional/readable hold in calm profile.",
            })
            continue

        if sfx_policy == "OFF":
            event_coverage.append({"event_id": eid, "beat_id": bid, "decision": "SILENCE", "reason": "sfx_policy=OFF"})
            continue

        # Enforce rolling density guardrail (merge closely spaced events cleanly)
        if _window_count(t_sec) >= max_3s:
            event_coverage.append({
                "event_id": eid, "beat_id": bid, "decision": "MERGE_WITH_OTHER",
                "reason": f"Rolling 3s window already has {max_3s} cue(s); merged with neighboring accent to avoid clutter.",
            })
            continue

        # Map visual semantics -> purposeful sound role
        fn = beat.get("semantic_function", "reveal")
        if fn == "hook":
            role = "hook_accent"
        elif kind_p in ("evidence_spotlight", "clip_mask_wipe", "spotlight_focus"):
            role = "evidence_reveal"
        elif kind_p in ("stagger_grid_4", "step_pillars_3", "step_rail_3"):
            role = "step_click"
        elif kind_p in ("verdict_bar", "light_sweep_badge") or fn in ("payoff", "cta"):
            role = "payoff_chime"
        else:
            role = "object_pop"

        asset = choose_cue_sound(role, verified_assets, usage_counts=usage_counts, production_mode=production_mode)
        cue = align_cue_to_event(
            cue_id=f"sfx_{len(cues):02d}_{role}",
            beat_id=bid,
            visual_event_id=eid,
            visual_trigger_frame=int(ev["start_frame"]),
            role=role,
            asset=asset,
            fps_num=fps_num,
            fps_den=fps_den,
            audio_sample_rate=sr,
            total_frames=total_frames,
            local_voice_peak_db=voice_peak_db,
            justification=f"Synced {role} to {eid} ({fn}/{kind_p or ev['capability_id']}).",
        )
        cues.append(cue)
        selected_times.append(t_sec)
        if asset:
            usage_counts[asset["asset_id"]] = usage_counts.get(asset["asset_id"], 0) + 1
            event_coverage.append({
                "event_id": eid, "beat_id": bid, "decision": "ADD",
                "cue_id": cue["cue_id"], "asset_id": asset["asset_id"], "role": role,
                "reason": cue["justification"],
            })
        else:
            event_coverage.append({
                "event_id": eid, "beat_id": bid, "decision": "UNAVAILABLE",
                "cue_id": cue["cue_id"], "role": role,
                "reason": f"No verified licensed asset for category={ROLE_TO_CATEGORY.get(role, 'foley')!r}.",
            })

    # 3. Optional ambience & licensed BGM bed
    bgm_res = plan_music_bed(
        shotbook.get("beats", []),
        total_frames=total_frames,
        fps_num=fps_num,
        fps_den=fps_den,
        audio_sample_rate=sr,
        bgm_policy=bgm_policy,
        bgm_asset=bgm_asset,
        local_voice_rms_db=voice_rms_db,
        rel_db=float(density_pol["default_bgm_rel_db"]),
    )
    if bgm_res["cue"]:
        cues.append(bgm_res["cue"])

    if ambience_asset and ambience_asset.get("approved_purpose") != "BLOCKED":
        assert_not_test_only_in_production([ambience_asset], production_mode=production_mode)
        cues.append({
            "cue_id": "amb_01_room",
            "beat_id": None,
            "visual_event_id": None,
            "kind": "ambience",
            "role": "ambience",
            "asset_ref": ambience_asset["asset_id"],
            "asset_id": ambience_asset["asset_id"],
            "source_sha256": ambience_asset["sha256"],
            "evidence_ref": ambience_asset.get("license_evidence"),
            "trigger_type": "continuous",
            "visual_trigger_frame": 0,
            "frame_start": 0,
            "frame_end": total_frames,
            "sample_start": 0,
            "intended_onset_sample": 0,
            "actual_source_onset_sample": 0,
            "measured_onset_sample": None,
            "trim_start_sec": 0.0,
            "trim_end_sec": round(min(float(ambience_asset["duration_sec"]), dur_sec), 4),
            "target_duration_samples": int(round(dur_sec * sr)),
            "gain_db": -26.0,
            "local_voice_window": [0.0, round(dur_sec, 3)],
            "fade_in_ms": 200.0,
            "fade_out_ms": 300.0,
            "ducking_db": -4.0,
            "ducking_group": "ambience_bed",
            "pan": 0.0,
            "layer_priority": 9,
            "license_ref": ambience_asset["license_status"],
            "reason": "Subtle licensed room/texture ambience bed.",
            "justification": "Prevents sterile digital dead air across pauses without masking speech.",
            "allow_overlap_reason": "continuous_ambience_bed",
            "intentionally_early": False,
            "intentionally_late": False,
            "optional_flag": True,
            "status": "VERIFIED_ASSET",
        })

    sfx_cues = [c for c in cues if c["kind"].startswith("sfx_")]
    verified_sfx = [c for c in sfx_cues if c["status"] == "VERIFIED_ASSET"]
    unavailable_sfx = [c for c in sfx_cues if c["status"] == "UNAVAILABLE"]

    if sfx_policy == "OFF":
        sfx_status = "OFF_BY_BRIEF"
    elif not sfx_cues:
        sfx_status = "PLANNED_LICENSED" if density_pol["allow_zero_cues"] else "SFX_NOT_AVAILABLE"
    elif verified_sfx and not unavailable_sfx:
        sfx_status = "PLANNED_LICENSED"
    elif verified_sfx and unavailable_sfx:
        sfx_status = "PARTIAL_MISSING_CUES"
    else:
        sfx_status = "BLOCKED_UNLICENSED" if sfx_policy == "REQUIRED_LICENSED" else "BLOCKED_SFX_ASSETS"

    # Compute measured density telemetry (never fabricated benchmarks)
    unique_hashes = sorted({c["source_sha256"] for c in verified_sfx if c.get("source_sha256")})
    max_rolling_3s = 0
    for t in selected_times:
        w = sum(1 for x in selected_times if t <= x < t + 3.0)
        max_rolling_3s = max(max_rolling_3s, w)

    ducking_plan = schedule_ducking(speech_spans or [], total_duration_sec=dur_sec)

    plan = {
        "schema_version": "kh02r1.sfx_cues.v2",
        "plan_sha256": hash_artifact(shotbook),
        "voice_policy": v_pol,
        "sfx_status": sfx_status,
        "bgm_status": bgm_res["status"],
        "voice_peak_db": voice_peak_db,
        "voice_lufs": voice_rms_db,
        "density_policy": {
            **density_pol,
            "planned_sfx_count": len(sfx_cues),
            "verified_sfx_count": len(verified_sfx),
            "unavailable_sfx_count": len(unavailable_sfx),
            "unique_sfx_hashes": len(unique_hashes),
            "max_rolling_3s_density": max_rolling_3s,
            "avg_density_per_sec": round(len(sfx_cues) / max(0.5, dur_sec), 3),
            "asset_repetition_counts": usage_counts,
        },
        "ducking_plan": ducking_plan,
        "event_coverage": event_coverage,
        "stems": {
            "voice_reference": None,
            "dry_voice": None,
            "sfx": None,
            "ambience": None,
            "music": None,
            "mix": None,
        },
        "cues": cues,
        "warnings": warnings,
    }
    errs = validate_schema(plan, "sfx_cues")
    if errs:
        raise ValueError(f"Generated soundscape failed sfx_cues schema: {errs[:3]}")
    return plan


def validate_audio_graph(
    sound_plan: dict[str, Any],
    *,
    total_frames: int,
    fps_num: int,
    fps_den: int = 1,
    audio_sample_rate: int = 48000,
    asset_paths: dict[str, Path] | None = None,
    asset_sha_map: dict[str, str] | None = None,
    expected_plan_sha256: str | None = None,
    production_mode: bool = True,
) -> list[dict[str, Any]]:
    """Fail-closed validation of multi-layer sound graph: bounds, duplicate IDs, stale plan SHA, rights & asset hashes."""
    errors: list[dict[str, Any]] = []
    if expected_plan_sha256 and sound_plan.get("plan_sha256") != expected_plan_sha256:
        errors.append({
            "severity": "P0", "cue_id": "$", "code": "STALE_SOUND_PLAN_SHA",
            "message": f"Sound plan SHA {sound_plan.get('plan_sha256')} != current timeline/shotbook SHA {expected_plan_sha256}",
        })

    fps_frac = Fraction(fps_num, fps_den)
    total_samples = int(round(float(Fraction(total_frames) / fps_frac) * audio_sample_rate))
    seen_ids: set[str] = set()

    for c in sound_plan.get("cues", []):
        cid = c["cue_id"]
        if cid in seen_ids:
            errors.append({"severity": "P0", "cue_id": cid, "code": "DUPLICATE_CUE_ID", "message": f"Duplicate cue_id {cid!r}"})
        seen_ids.add(cid)

        fs, fe = int(c["frame_start"]), int(c["frame_end"])
        if fs < 0 or fe <= fs or fe > total_frames:
            errors.append({"severity": "P0", "cue_id": cid, "code": "CUE_FRAME_OOB", "message": f"[{fs},{fe}) outside [0,{total_frames})"})

        samp = int(c.get("intended_onset_sample", c.get("sample_start", 0)))
        if samp < 0 or samp >= total_samples:
            errors.append({"severity": "P0", "cue_id": cid, "code": "CUE_SAMPLE_OOB", "message": f"onset sample {samp} outside [0,{total_samples})"})

        gain = c.get("gain_db", 0.0)
        if not is_strict_number(gain) or gain < -60.0 or gain > 12.0:
            errors.append({"severity": "P0", "cue_id": cid, "code": "CUE_INVALID_GAIN", "message": f"Invalid gain_db={gain!r}"})

        if production_mode and c.get("license_ref") in ("TEST_ONLY_FIXTURE", "UNLICENSED", "ANALYSIS_ONLY", "MISSING_LICENSED_ASSET", "", None):
            if c.get("status") != "UNAVAILABLE":
                errors.append({"severity": "P0", "cue_id": cid, "code": "CUE_FORBIDDEN_LICENSE", "message": f"Forbidden license_ref={c.get('license_ref')!r}"})

        if c["kind"] != "voice" and c.get("status") != "UNAVAILABLE" and asset_paths is not None:
            aid = c.get("asset_id") or c.get("asset_ref")
            ap = asset_paths.get(aid) if aid else None
            if not ap or not ap.is_file():
                errors.append({"severity": "P0", "cue_id": cid, "code": "CUE_ASSET_MISSING", "message": f"Missing audio asset file for {aid!r}"})
            elif c.get("source_sha256"):
                actual_sha = (asset_sha_map or {}).get(aid) or hash_artifact(ap)
                if actual_sha != c["source_sha256"]:
                    errors.append({
                        "severity": "P0", "cue_id": cid, "code": "CUE_ASSET_HASH_MISMATCH",
                        "message": f"Asset {aid!r} hash mismatch ({actual_sha[:12]} != {c['source_sha256'][:12]})",
                    })
    return errors


def _make_silence_wav(out_wav: Path, duration_sec: float, sample_rate: int = 48000) -> Path:
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=stereo",
        "-t", f"{duration_sec:.6f}",
        "-c:a", "pcm_s16le", str(out_wav),
    ]
    pr = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if pr.returncode != 0:
        raise RuntimeError(f"Failed creating silent stem {out_wav}: {pr.stderr[-200:]}")
    return out_wav


def render_stems(
    sound_plan: dict[str, Any],
    *,
    dry_voice_source: str | Path | None,
    asset_paths: dict[str, Path],
    stems_dir: str | Path,
    total_frames: int,
    fps_num: int,
    fps_den: int = 1,
    audio_sample_rate: int = 48000,
    chunk_size_cues: int = 16,
    production_mode: bool = True,
) -> dict[str, Any]:
    """Render real multi-layer 48kHz stereo PCM WAV stems (dry_voice, sfx, ambience, music, mix) via chunked FFmpeg argv."""
    errs = validate_audio_graph(
        sound_plan, total_frames=total_frames, fps_num=fps_num, fps_den=fps_den,
        audio_sample_rate=audio_sample_rate, asset_paths=asset_paths, production_mode=production_mode,
    )
    if errs:
        raise ValueError(f"Cannot render invalid audio graph: {errs[:4]}")

    sdir = Path(stems_dir).resolve()
    sdir.mkdir(parents=True, exist_ok=True)
    fps_frac = Fraction(fps_num, fps_den)
    dur_sec = float(Fraction(total_frames) / fps_frac)

    # 1. dry_voice.wav (exact output duration stereo 48kHz PCM)
    dry_wav = sdir / "dry_voice.wav"
    if dry_voice_source and Path(dry_voice_source).is_file():
        cmd_v = [
            "ffmpeg", "-v", "error", "-y", "-i", str(Path(dry_voice_source).resolve()),
            "-vn", "-af", f"aresample={audio_sample_rate},apad=whole_dur={dur_sec:.6f},atrim=0:{dur_sec:.6f}",
            "-ac", "2", "-ar", str(audio_sample_rate), "-c:a", "pcm_s16le", str(dry_wav),
        ]
        pr = subprocess.run(cmd_v, capture_output=True, text=True, timeout=120)
        if pr.returncode != 0:
            raise RuntimeError(f"Failed extracting dry_voice stem: {pr.stderr[-300:]}")
    else:
        _make_silence_wav(dry_wav, dur_sec, audio_sample_rate)

    # 2. Render SFX stem in bounded chunks (prevents huge amix fan-in on long timelines)
    sfx_cues = [
        c for c in sound_plan.get("cues", [])
        if c["kind"].startswith("sfx_") and c.get("status") in ("VERIFIED_ASSET", "APPLIED", "ONSET_VERIFIED")
    ]
    sfx_wav = sdir / "sfx.wav"
    applied_cues: list[dict[str, Any]] = []
    filter_audit: list[str] = []

    if not sfx_cues:
        _make_silence_wav(sfx_wav, dur_sec, audio_sample_rate)
    else:
        chunk_wavs: list[Path] = []
        for ch_idx in range(0, len(sfx_cues), max(1, chunk_size_cues)):
            batch = sfx_cues[ch_idx : ch_idx + max(1, chunk_size_cues)]
            ch_out = sdir / f"_sfx_chunk_{ch_idx // max(1, chunk_size_cues):02d}.wav"
            cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r={audio_sample_rate}:cl=stereo:d={dur_sec:.6f}"]
            fparts: list[str] = ["[0:a]anull[base]"]
            mix_labels: list[str] = ["[base]"]
            for i, c in enumerate(batch, 1):
                aid = c.get("asset_id") or c.get("asset_ref")
                ap = asset_paths[aid]
                cmd.extend(["-i", str(ap.resolve())])
                ts = float(c.get("trim_start_sec", 0.0))
                te = float(c.get("trim_end_sec") or (ts + (int(c.get("target_duration_samples", audio_sample_rate // 2)) / audio_sample_rate)))
                seg_d = max(0.03, te - ts)
                delay_ms = int(round((int(c["intended_onset_sample"]) / float(audio_sample_rate)) * 1000.0))
                gain_db = float(c.get("gain_db", -15.0))
                fi = min(float(c.get("fade_in_ms", 8.0)) / 1000.0, seg_d * 0.35)
                fo = min(float(c.get("fade_out_ms", 25.0)) / 1000.0, seg_d * 0.45)
                chain = (
                    f"[{i}:a]aresample={audio_sample_rate},aformat=channel_layouts=stereo,"
                    f"atrim=start={ts:.5f}:end={te:.5f},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:st=0:d={fi:.5f},afade=t=out:st={max(0.0, seg_d - fo):.5f}:d={fo:.5f},"
                    f"volume={gain_db:.2f}dB,adelay={delay_ms}|{delay_ms}[c{i}]"
                )
                fparts.append(chain)
                mix_labels.append(f"[c{i}]")
                c_copy = dict(c)
                c_copy["status"] = "APPLIED"
                c_copy["applied_delay_ms"] = delay_ms
                applied_cues.append(c_copy)

            # normalize=0 preserves exact planned dB gain without FFmpeg amix halving every cue!
            fparts.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:normalize=0,atrim=0:{dur_sec:.6f}[out]")
            fgraph = ";".join(fparts)
            filter_audit.append(fgraph)
            cmd.extend(["-filter_complex", fgraph, "-map", "[out]", "-ac", "2", "-ar", str(audio_sample_rate), "-c:a", "pcm_s16le", str(ch_out)])
            pr = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if pr.returncode != 0:
                raise RuntimeError(f"SFX chunk {ch_idx} FFmpeg failed: {pr.stderr[-400:]}")
            chunk_wavs.append(ch_out)

        if len(chunk_wavs) == 1:
            chunk_wavs[0].replace(sfx_wav)
        else:
            cmd_m = ["ffmpeg", "-v", "error", "-y"]
            for cw in chunk_wavs:
                cmd_m.extend(["-i", str(cw)])
            lbls = "".join(f"[{i}:a]" for i in range(len(chunk_wavs)))
            cmd_m.extend([
                "-filter_complex", f"{lbls}amix=inputs={len(chunk_wavs)}:duration=first:normalize=0,atrim=0:{dur_sec:.6f}[out]",
                "-map", "[out]", "-ac", "2", "-ar", str(audio_sample_rate), "-c:a", "pcm_s16le", str(sfx_wav),
            ])
            pr = subprocess.run(cmd_m, capture_output=True, text=True, timeout=120)
            if pr.returncode != 0:
                raise RuntimeError(f"SFX chunk merge failed: {pr.stderr[-300:]}")
            for cw in chunk_wavs:
                cw.unlink(missing_ok=True)

    # 3. Ambience stem
    amb_wav = sdir / "ambience.wav"
    amb_cues = [c for c in sound_plan.get("cues", []) if c["kind"] == "ambience" and c.get("status") == "VERIFIED_ASSET"]
    if amb_cues:
        ac = amb_cues[0]
        ap = asset_paths[ac["asset_id"]]
        fi = float(ac.get("fade_in_ms", 200.0)) / 1000.0
        fo = float(ac.get("fade_out_ms", 300.0)) / 1000.0
        cmd_a = [
            "ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", str(ap.resolve()),
            "-t", f"{dur_sec:.6f}",
            "-af", f"aresample={audio_sample_rate},aformat=channel_layouts=stereo,volume={float(ac['gain_db']):.2f}dB,"
                   f"afade=t=in:st=0:d={min(fi, dur_sec*0.4):.4f},afade=t=out:st={max(0.0, dur_sec - min(fo, dur_sec*0.4)):.4f}:d={min(fo, dur_sec*0.4):.4f}",
            "-ac", "2", "-ar", str(audio_sample_rate), "-c:a", "pcm_s16le", str(amb_wav),
        ]
        pr = subprocess.run(cmd_a, capture_output=True, text=True, timeout=120)
        if pr.returncode != 0:
            raise RuntimeError(f"Ambience stem failed: {pr.stderr[-300:]}")
    else:
        _make_silence_wav(amb_wav, dur_sec, audio_sample_rate)

    # 4. Music stem with smooth sidechain ducking driven by dry_voice.wav
    music_wav = sdir / "music.wav"
    bgm_cues = [c for c in sound_plan.get("cues", []) if c["kind"] == "bgm" and c.get("status") == "VERIFIED_ASSET"]
    if bgm_cues:
        bc = bgm_cues[0]
        bp = asset_paths[bc["asset_id"]]
        duck = sound_plan.get("ducking_plan") or {}
        att_ms = float(duck.get("attack_ms", 45.0))
        rel_ms = float(duck.get("release_ms", 260.0))
        fi = float(bc.get("fade_in_ms", 250.0)) / 1000.0
        fo = float(bc.get("fade_out_ms", 400.0)) / 1000.0
        f_bgm = (
            f"[0:a]aresample={audio_sample_rate},aformat=channel_layouts=stereo,atrim=0:{dur_sec:.6f},"
            f"volume={float(bc['gain_db']):.2f}dB,"
            f"afade=t=in:st=0:d={min(fi, dur_sec*0.3):.4f},afade=t=out:st={max(0.0, dur_sec - min(fo, dur_sec*0.3)):.4f}:d={min(fo, dur_sec*0.3):.4f}[m0];"
            f"[m0][1:a]sidechaincompress=threshold=0.03:ratio=4:attack={att_ms:.1f}:release={rel_ms:.1f}:makeup=1[mout]"
        )
        cmd_b = [
            "ffmpeg", "-v", "error", "-y",
            "-stream_loop", "-1", "-i", str(bp.resolve()),
            "-i", str(dry_wav),
            "-t", f"{dur_sec:.6f}",
            "-filter_complex", f_bgm, "-map", "[mout]",
            "-ac", "2", "-ar", str(audio_sample_rate), "-c:a", "pcm_s16le", str(music_wav),
        ]
        pr = subprocess.run(cmd_b, capture_output=True, text=True, timeout=120)
        if pr.returncode != 0:
            raise RuntimeError(f"Music ducked stem failed: {pr.stderr[-300:]}")
    else:
        _make_silence_wav(music_wav, dur_sec, audio_sample_rate)

    # 5. Final mix.wav combining all 4 stems with soft true-peak limiter (-1.0 dBTP ceiling = 0.891)
    mix_wav = sdir / "mix.wav"
    cmd_mix = [
        "ffmpeg", "-v", "error", "-y",
        "-i", str(dry_wav), "-i", str(sfx_wav), "-i", str(amb_wav), "-i", str(music_wav),
        "-filter_complex",
        f"[0:a][1:a][2:a][3:a]amix=inputs=4:duration=first:normalize=0,"
        f"alimiter=limit=0.88:attack=5:release=50:level=false:latency=true,atrim=0:{dur_sec:.6f}[mix]",
        "-map", "[mix]", "-ac", "2", "-ar", str(audio_sample_rate), "-c:a", "pcm_s16le", str(mix_wav),
    ]
    pr = subprocess.run(cmd_mix, capture_output=True, text=True, timeout=120)
    if pr.returncode != 0:
        raise RuntimeError(f"Final mix stem failed: {pr.stderr[-300:]}")

    stems_map = {
        "dry_voice": str(dry_wav),
        "sfx": str(sfx_wav),
        "ambience": str(amb_wav),
        "music": str(music_wav),
        "mix": str(mix_wav),
    }
    stem_hashes = {k: hash_artifact(Path(v)) for k, v in stems_map.items()}
    return {
        "stems": stems_map,
        "stem_hashes": stem_hashes,
        "applied_cues": applied_cues,
        "expected_duration_sec": round(dur_sec, 4),
        "filter_chain_count": len(filter_audit),
    }


def measure_stems(stems_map: dict[str, str | Path]) -> dict[str, Any]:
    """Measure real EBU R128 loudness, true peak, duration & hash on every rendered audio stem."""
    out: dict[str, Any] = {}
    for name, path_val in stems_map.items():
        if not path_val:
            out[name] = {"measured": False, "status": "NOT_PRESENT"}
            continue
        p = Path(path_val)
        if not p.is_file():
            out[name] = {"measured": False, "status": "MISSING_FILE"}
            continue
        m = measure_final_audio(p)
        pr = probe_sfx(p)
        out[name] = {
            "path": str(p),
            "sha256": hash_artifact(p),
            "duration_sec": pr.get("duration_sec"),
            "sample_rate": pr.get("sample_rate"),
            "channels": pr.get("channels"),
            "measured": bool(m.get("measured")),
            "integrated_lufs": m.get("integrated_lufs"),
            "true_peak_dbtp": m.get("true_peak_dbtp"),
            "max_volume_db": m.get("max_volume_db"),
            "clipping_detected": m.get("clipping_detected"),
        }
    return out


def mix_audio_checked(
    video_path: str | Path,
    mix_wav_path: str | Path,
    out_mp4_path: str | Path,
    *,
    expected_duration_sec: float | None = None,
    audio_bitrate: str = "192k",
) -> dict[str, Any]:
    """Mux lossless video packets (-c:v copy) with verified mix WAV into atomic output MP4."""
    v = Path(video_path).resolve()
    a = Path(mix_wav_path).resolve()
    dst = Path(out_mp4_path).resolve()
    if v == dst:
        raise ValueError("Refusing to overwrite input video during audio mux")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".mux.part.mp4")

    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-i", str(v), "-i", str(a),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", audio_bitrate, "-ar", "48000",
        "-movflags", "+faststart", str(tmp),
    ]
    pr = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if pr.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Audio mux failed rc={pr.returncode}: {pr.stderr[-300:]}")

    # Verify muxed audio stream exists and duration matches within tolerance
    m_audio = measure_final_audio(tmp)
    pr_info = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration:format=duration", "-of", "json", str(tmp)],
        capture_output=True, text=True, timeout=30,
    )
    info = json.loads(pr_info.stdout or "{}")
    v_st = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a_st = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v_st or not a_st:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Muxed MP4 lost video or audio stream")

    v_dur = float(v_st.get("duration") or info.get("format", {}).get("duration") or 0.0)
    a_dur = float(a_st.get("duration") or 0.0)
    drift = abs(v_dur - a_dur)
    if expected_duration_sec is not None and abs(a_dur - expected_duration_sec) > 0.12:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Muxed audio duration {a_dur:.3f}s drifted from expected {expected_duration_sec:.3f}s")

    tmp.replace(dst)
    return {
        "ok": True,
        "mp4_path": str(dst),
        "mp4_sha256": hash_artifact(dst),
        "video_duration_sec": round(v_dur, 4),
        "audio_duration_sec": round(a_dur, 4),
        "av_drift_sec": round(drift, 4),
        "audio_metrics": m_audio,
    }


def verify_mix_sync(
    sound_plan: dict[str, Any],
    applied_cues: list[dict[str, Any]],
    *,
    sfx_stem_wav: str | Path,
    mix_wav: str | Path,
    fps_num: int,
    fps_den: int = 1,
    audio_sample_rate: int = 48000,
) -> dict[str, Any]:
    """Measure real cue onset in rendered sfx.wav against intended_onset_sample (tol max(1 frame, 50ms))."""
    fps = fps_num / fps_den
    tol_sec = max(1.0 / fps, 0.050)
    tol_samples = int(round(tol_sec * audio_sample_rate))

    sfx_p = Path(sfx_stem_wav)
    mix_p = Path(mix_wav)
    mix_m = measure_final_audio(mix_p)
    if not mix_m.get("measured"):
        return {"pass": False, "status": "NOT_VERIFIED_MISSING_ANALYZER", "cue_checks": [], "issues": ["Missing EBU R128 measurement on mix.wav"]}

    # Decode mono 16-bit PCM from sfx.wav for exact per-cue onset verification
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(sfx_p), "-vn", "-ac", "1", "-ar", str(audio_sample_rate), "-f", "s16le", "-"],
        capture_output=True, timeout=60,
    )
    samples = struct.unpack(f"<{len(proc.stdout) // 2}h", proc.stdout[: (len(proc.stdout) // 2) * 2]) if proc.returncode == 0 and proc.stdout else ()

    cue_checks: list[dict[str, Any]] = []
    issues: list[str] = []
    win_samp = max(1, int(0.01 * audio_sample_rate))  # 10ms RMS bins

    for c in applied_cues:
        cid = c["cue_id"]
        intended = int(c["intended_onset_sample"])
        search_lo = max(0, intended - tol_samples)
        search_hi = min(len(samples), intended + max(tol_samples * 2, int(0.15 * audio_sample_rate)))
        if search_hi <= search_lo or not samples:
            issues.append(f"{cid}: no SFX stem samples in window")
            cue_checks.append({"cue_id": cid, "pass": False, "intended_onset_sample": intended, "measured_onset_sample": None, "error_ms": None})
            continue
        seg = samples[search_lo:search_hi]
        peak = max(abs(x) for x in seg) if seg else 0
        if peak < 15:
            issues.append(f"{cid}: cue missing or silent in rendered sfx.wav (peak={peak})")
            cue_checks.append({"cue_id": cid, "pass": False, "intended_onset_sample": intended, "measured_onset_sample": None, "error_ms": None})
            continue
        thresh = max(12.0, peak * 0.12)
        found_rel = next((i for i, x in enumerate(seg) if abs(x) >= thresh), 0)
        measured = search_lo + found_rel
        err_samples = measured - intended
        err_ms = round((err_samples / float(audio_sample_rate)) * 1000.0, 2)
        ok = abs(err_samples) <= tol_samples
        if not ok:
            issues.append(f"{cid}: onset drift {err_ms:+.1f}ms exceeds tolerance +/-{tol_sec*1000:.1f}ms")
        cue_checks.append({
            "cue_id": cid,
            "role": c["role"],
            "asset_id": c.get("asset_id"),
            "source_sha256": c.get("source_sha256"),
            "intended_onset_sample": intended,
            "measured_onset_sample": measured,
            "error_ms": err_ms,
            "tolerance_ms": round(tol_sec * 1000.0, 2),
            "intentionally_early": bool(c.get("intentionally_early")),
            "pass": ok,
        })

    if mix_m.get("clipping_detected") or float(mix_m.get("true_peak_dbtp", 0.0)) > -0.9:
        issues.append(f"True peak violation on mix.wav: {mix_m.get('true_peak_dbtp')} dBTP (> -0.9 dBTP)")

    return {
        "schema_version": "kh02r1.sfx_onset_qa.v1",
        "pass": len(issues) == 0,
        "status": "PASS" if not issues else "FAIL",
        "mix_sha256": hash_artifact(mix_p),
        "sfx_stem_sha256": hash_artifact(sfx_p),
        "mix_metrics": mix_m,
        "tolerance_ms": round(tol_sec * 1000.0, 2),
        "cue_checks": cue_checks,
        "issues": issues,
    }


def _stream_pcm_window(path: Path, start_sec: float, dur_sec: float, sample_rate: int = 48000) -> list[int]:
    """Read bounded PCM window via FFmpeg seek (avoids full-file RAM decode on long-form audio)."""
    s0 = max(0.0, float(start_sec))
    d = max(0.02, min(15.0, float(dur_sec)))
    pr = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", f"{s0:.4f}", "-t", f"{d:.4f}",
            "-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-",
        ],
        capture_output=True, timeout=60,
    )
    if pr.returncode != 0 or not pr.stdout:
        return []
    n = len(pr.stdout) // 2
    return list(struct.unpack(f"<{n}h", pr.stdout[: n * 2]))


def compare_dry_and_mix(
    dry_voice_wav: str | Path,
    mix_wav: str | Path,
    speech_spans: list[tuple[float, float]],
    *,
    audio_sample_rate: int = 48000,
) -> dict[str, Any]:
    """Compare sample-aligned dry voice vs final mix over protected speech windows using bounded streaming reads."""
    dry_p = Path(dry_voice_wav)
    mix_p = Path(mix_wav)
    if not dry_p.is_file() or not mix_p.is_file():
        return {"pass": False, "status": "NOT_VERIFIED", "error": "Missing dry or mix WAV"}

    pr_d = probe_sfx(dry_p, max_duration_sec=86400.0)
    pr_m = probe_sfx(mix_p, max_duration_sec=86400.0)
    if not pr_d.get("valid") or not pr_m.get("valid"):
        return {"pass": False, "status": "NOT_VERIFIED", "error": "Failed to probe dry or mix WAV"}

    dry_dur = float(pr_d["duration_sec"])
    mix_dur = float(pr_m["duration_sec"])
    dry_samples = int(round(dry_dur * audio_sample_rate))
    mix_samples = int(round(mix_dur * audio_sample_rate))
    len_diff_samples = abs(dry_samples - mix_samples)

    windows: list[dict[str, Any]] = []
    issues: list[str] = []
    if len_diff_samples > int(0.05 * audio_sample_rate):
        issues.append(f"Dry vs mix sample length mismatch: {dry_samples} vs {mix_samples}")

    spans = speech_spans if speech_spans else [(0.0, min(dry_dur, mix_dur))]
    # Chunk any long speech span into <=10s windows so memory stays O(1) even on multi-hour podcasts
    chunked_spans: list[tuple[float, float]] = []
    for s, e in spans:
        cur = max(0.0, float(s))
        end_t = min(dry_dur, mix_dur, float(e))
        while cur < end_t - 0.02:
            nxt = min(end_t, cur + 10.0)
            chunked_spans.append((cur, nxt))
            cur = nxt

    for idx, (s, e) in enumerate(chunked_spans):
        dur = max(0.02, e - s)
        d_seg = _stream_pcm_window(dry_p, s, dur, sample_rate=audio_sample_rate)
        m_seg = _stream_pcm_window(mix_p, s, dur, sample_rate=audio_sample_rate)
        n = min(len(d_seg), len(m_seg))
        if n <= 0:
            continue
        d_seg = d_seg[:n]
        m_seg = m_seg[:n]
        d_rms = math.sqrt(sum(x * x for x in d_seg) / n) / 32768.0
        m_rms = math.sqrt(sum(x * x for x in m_seg) / n) / 32768.0
        res_rms = math.sqrt(sum((m_seg[k] - d_seg[k]) ** 2 for k in range(n)) / n) / 32768.0
        d_db = round(20.0 * math.log10(max(1e-6, d_rms)), 2)
        m_db = round(20.0 * math.log10(max(1e-6, m_rms)), 2)
        res_db = round(20.0 * math.log10(max(1e-6, res_rms)), 2)
        voice_to_accompaniment_db = round(d_db - res_db, 2)
        masked = (d_db > -45.0 and voice_to_accompaniment_db < 5.0)
        dropout = (d_db > -40.0 and m_db < d_db - 6.0)
        if masked:
            issues.append(f"Speech window {idx + 1} [{s:.2f},{e:.2f}s] accompaniment masking proxy too high (voice-over-residual={voice_to_accompaniment_db:.1f} dB)")
        if dropout:
            issues.append(f"Speech window {idx + 1} [{s:.2f},{e:.2f}s] voice attenuation/dropout detected ({d_db} -> {m_db} dB)")
        windows.append({
            "window_index": idx + 1,
            "start_sec": round(s, 3),
            "end_sec": round(e, 3),
            "dry_voice_rms_db": d_db,
            "mix_rms_db": m_db,
            "accompaniment_residual_rms_db": res_db,
            "voice_over_accompaniment_db": voice_to_accompaniment_db,
            "masked_warning": masked,
            "dropout_detected": dropout,
        })

    return {
        "pass": len(issues) == 0,
        "status": "PASS" if not issues else "FAIL",
        "dry_samples": dry_samples,
        "mix_samples": mix_samples,
        "sample_diff": len_diff_samples,
        "streaming_bounded": True,
        "windows": windows,
        "issues": issues,
        "note": "Signal masking proxy only; subjective listening approval remains OWNER_AUDIO=PENDING.",
    }
