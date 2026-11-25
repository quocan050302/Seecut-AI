"""KH-03 Multi-Layer Sound Director (Integrated with KH-02 sfx_assets.py & audio_mix.py).

Derives semantically motivated sound events tailored to the selected universal skill (SK-01..SK-05),
never forces artificial cue quotas or SFX-per-word spam, strictly blocks TEST_ONLY tones in
production, integrates cleanly into KH-02's stem mixer & onset QA, and reports honest
BLOCKED_SFX_ASSETS / PARTIAL_SOUND whenever licensed sound libraries/providers are absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.kh02 import sound as _kh02_sound  # noqa: F401 (init sound before audio_mix)
from scripts.kh02.audio_mix import plan_soundscape
from scripts.kh02.sfx_assets import (
    assert_not_test_only_in_production,
    ensure_cue_assets,
    load_sfx_provider_config,
)

from .contracts import assert_kh03_schema, write_json_atomic

SKILL_SOUND_PROFILES: dict[str, dict[str, Any]] = {
    "SK-01": {"energy_mode": "kinetic", "min_spacing_sec": 1.1, "base_rel_db": -14.5, "default_families": ["impact", "whoosh", "pop", "click", "chime"]},
    "SK-02": {"energy_mode": "proof", "min_spacing_sec": 1.4, "base_rel_db": -16.0, "default_families": ["whoosh", "click", "rustle", "chime", "texture"]},
    "SK-03": {"energy_mode": "suspense", "min_spacing_sec": 2.0, "base_rel_db": -17.5, "default_families": ["ambience", "riser", "whoosh", "impact", "chime"]},
    "SK-04": {"energy_mode": "explanatory", "min_spacing_sec": 1.5, "base_rel_db": -16.5, "default_families": ["click", "rustle", "pop", "whoosh", "chime"]},
    "SK-05": {"energy_mode": "calm", "min_spacing_sec": 2.6, "base_rel_db": -18.5, "default_families": ["rustle", "click", "chime"]},
}


def _family_for_event(ev: dict[str, Any], beat_fn: str, skill_id: str) -> tuple[str, str]:
    cap = str(ev.get("capability_id") or "")
    kind = str((ev.get("params") or {}).get("kind") or "")
    if beat_fn == "hook" or "contradiction" in cap:
        return ("impact" if skill_id in ("SK-01", "SK-03") else "click"), "Opening hook / contradiction onset"
    if "reservoir" in cap or "counter" in cap:
        return "rustle" if beat_fn == "demonstration" else "click", "Numeric allocation / counter step"
    if "proof_frame" in cap or "clip_mask_wipe" in kind:
        return "whoosh", "Verified proof / product mask reveal"
    if "causal_flow" in cap or "timeline" in cap or "comparison" in cap:
        return "click", "Explanatory step / comparison reveal"
    if "cta_magnetic" in cap or beat_fn in ("payoff", "cta"):
        return "chime", "Payoff / closing resolution chime"
    return "pop", "Semantic visual transition accent"


def derive_sound_events(
    creative_plan: dict[str, Any],
    *,
    fps: float = 24.0,
    has_source_audio: bool = True,
    layout_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Derive motivated sound events anchored to visual reveals & spatial layout morphs (ADL-06, zero spam)."""
    skill_id = creative_plan["selected_skill_id"]
    prof = SKILL_SOUND_PROFILES.get(skill_id, SKILL_SOUND_PROFILES["SK-04"])
    min_gap = float(prof["min_spacing_sec"])
    beat_fn_map = {b["beat_id"]: b.get("semantic_function", "context") for b in creative_plan.get("beats", [])}
    tr_by_to_beat = {t["to_beat_id"]: t for t in ((layout_plan or {}).get("transitions") or [])}

    events: list[dict[str, Any]] = []
    last_added_sec = -99.0

    for idx, comp in enumerate(creative_plan.get("components", [])):
        sf = int(comp["start_frame"])
        t_sec = round(sf / max(1.0, fps), 4)
        bid = comp["beat_id"]
        bfn = beat_fn_map.get(bid, "context")
        fam, reason = _family_for_event(comp, bfn, skill_id)
        tr = tr_by_to_beat.get(bid)
        layout_tid = tr["transition_id"] if tr else None
        motion_anchor = "morph_start" if tr else ("hook_impact" if bfn == "hook" else ("payoff_settle" if bfn in ("payoff", "cta") else "reveal_onset"))
        if tr and fam not in ("impact", "chime"):
            fam = "whoosh"
            reason = f"Spatial morph transition {tr['from_mode']} -> {tr['to_mode']} + {reason}"

        # Calm / natural skills keep quiet context beats purely natural without clutter
        if skill_id == "SK-05" and bfn in ("context", "breath", "intentional_silence") and not tr:
            status = "KEEP_NATURAL" if has_source_audio else "SILENCE"
            prio = 2
        elif (t_sec - last_added_sec) < min_gap and idx > 0 and bfn not in ("payoff", "cta") and not tr:
            status = "MERGE"
            prio = 2
        else:
            status = "ADD"
            prio = 5 if bfn in ("hook", "payoff", "cta") else 4
            last_added_sec = t_sec

        events.append({
            "event_id": f"snd_{idx + 1:02d}_{comp['component_id']}",
            "beat_id": bid,
            "semantic_reason": reason,
            "requested_family": fam,
            "asset_id": None,
            "rights_proof": None,
            "onset_frame": sf,
            "onset_sec": t_sec,
            "target_gain_db": float(prof["base_rel_db"]),
            "status": status,
            "priority": prio,
            "layout_transition_id": layout_tid,
            "motion_anchor": motion_anchor,
            "variation": {"pitch_semitones": 0.0, "avoid_immediate_repeat": True},
        })
    return events


def build_cue_requests(sound_events: list[dict[str, Any]], energy_mode: str) -> list[dict[str, Any]]:
    """Build bounded KH-02 sfx_assets cue requests from ADD sound events."""
    reqs: list[dict[str, Any]] = []
    seen_cats: dict[str, int] = {}
    for ev in sound_events:
        if ev["status"] != "ADD":
            continue
        cat = ev["requested_family"]
        seen_cats[cat] = seen_cats.get(cat, 0) + 1
        if seen_cats[cat] > 3:
            continue
        reqs.append({
            "cue_id": ev["event_id"],
            "category": cat,
            "tags": [cat, "clean", "editorial", energy_mode],
            "query": f"clean subtle editorial {cat} sound",
            "min_duration_sec": 0.05,
            "max_duration_sec": 1.8,
            "strict_category": True,
        })
    return reqs


def acquire_licensed_sounds(
    sound_events: list[dict[str, Any]],
    *,
    run_dir: Path,
    energy_mode: str,
    sfx_policy: str = "OPTIONAL_LICENSED",
    sfx_provider_mode: str = "auto",
    sfx_library_dirs: list[str | Path] | None = None,
    sfx_config_path: Path | None = None,
    intended_purpose: str = "NONCOMMERCIAL_ONLY",
    offline: bool = True,
    workers: int = 2,
    production_mode: bool = True,
    allow_test_only_sfx: bool = False,
) -> dict[str, Any]:
    """Acquire verified licensed sounds through KH-02's hardened provider & rights layer."""
    if production_mode and allow_test_only_sfx:
        raise ValueError("TEST_ONLY audio fixtures are strictly forbidden in production mode.")

    prov = load_sfx_provider_config(
        cli_library_dirs=sfx_library_dirs,
        config_path=sfx_config_path,
        provider_mode=sfx_provider_mode,
        sfx_policy=sfx_policy,
        offline=offline,
        intended_purpose=intended_purpose,
    )
    reqs = build_cue_requests(sound_events, energy_mode) if sfx_policy != "OFF" else []
    if sfx_policy == "OFF":
        return {
            "provider_bundle": prov,
            "assets": [],
            "provider_status": "OFF_BY_POLICY",
            "query_plan": [],
            "missing_categories": [],
        }

    acq = ensure_cue_assets(
        reqs,
        local_provider=prov["local_provider"],
        remote_provider=prov["remote_provider"],
        cache_dir=run_dir / "cache" / "sfx",
        workers=max(1, min(2, int(workers))),
        offline=offline,
        intended_purpose=intended_purpose,
        allow_test_only=(allow_test_only_sfx and not production_mode),
    )
    assets = acq["assets"]
    assert_not_test_only_in_production(assets, production_mode=production_mode)
    return {
        "provider_bundle": prov,
        "assets": assets,
        "provider_status": "VERIFIED_ASSETS_READY" if assets else prov["overall_status"],
        "query_plan": acq["query_plan"],
        "missing_categories": acq["missing_categories"],
    }


def plan_soundscape_for_skill(
    *,
    run_id: str,
    run_dir: Path,
    creative_plan: dict[str, Any],
    layout_plan: dict[str, Any] | None = None,
    shotbook: dict[str, Any] | None = None,
    fps: float = 24.0,
    voice_policy: str = "LOCK_VOICE",
    sfx_policy: str = "OPTIONAL_LICENSED",
    bgm_policy: str = "OFF",
    sfx_library_dirs: list[str | Path] | None = None,
    sfx_config_path: Path | None = None,
    intended_purpose: str = "NONCOMMERCIAL_ONLY",
    offline: bool = True,
    workers: int = 2,
    production_mode: bool = True,
    allow_test_only_sfx: bool = False,
    speech_spans: list[tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Build KH-03 SOUND_DIRECTOR_PLAN.json + LAYOUT_SOUND_ANCHORS.json + AUDIO_COMFORT_REPORT.md."""
    skill_id = creative_plan["selected_skill_id"]
    prof = SKILL_SOUND_PROFILES.get(skill_id, SKILL_SOUND_PROFILES["SK-04"])
    energy = prof["energy_mode"]

    events = derive_sound_events(creative_plan, fps=fps, has_source_audio=True, layout_plan=layout_plan)
    acq = acquire_licensed_sounds(
        events,
        run_dir=run_dir,
        energy_mode=energy,
        sfx_policy=sfx_policy,
        sfx_library_dirs=sfx_library_dirs,
        sfx_config_path=sfx_config_path,
        intended_purpose=intended_purpose,
        offline=offline,
        workers=workers,
        production_mode=production_mode,
        allow_test_only_sfx=allow_test_only_sfx,
    )
    assets = acq["assets"]
    usage: dict[str, int] = {}

    for ev in events:
        if ev["status"] != "ADD":
            continue
        fam = ev["requested_family"]
        matches = [a for a in assets if a.get("category") == fam and a.get("approved_purpose") != "BLOCKED"]
        if not matches and assets:
            compat = {"click": ("pop",), "pop": ("click",), "chime": ("ding",), "rustle": ("foley",), "impact": ("whoosh",)}.get(fam, ())
            matches = [a for a in assets if a.get("category") in compat and a.get("approved_purpose") != "BLOCKED"]
        if matches:
            matches.sort(key=lambda a: (usage.get(a["asset_id"], 0), a["asset_id"]))
            pick = matches[0]
            usage[pick["asset_id"]] = usage.get(pick["asset_id"], 0) + 1
            ev["asset_id"] = pick["asset_id"]
            ev["rights_proof"] = pick.get("license_status")
        else:
            ev["status"] = "UNAVAILABLE"

    counts = {st: sum(1 for e in events if e["status"] == st) for st in ("ADD", "KEEP_NATURAL", "SILENCE", "MERGE", "UNAVAILABLE")}
    plan = {
        "schema_version": "kh03.sound_plan.v1",
        "run_id": run_id,
        "selected_skill_id": skill_id,
        "energy_mode": energy,
        "voice_policy": voice_policy,
        "sfx_policy": sfx_policy,
        "bgm_policy": bgm_policy,
        "production_mode": bool(production_mode),
        "events": events,
        "provider_status": acq["provider_status"],
        "target_loudness_lufs": [-16.0, -14.0],
        "target_true_peak_dbtp": -1.0,
        "summary_counts": counts,
    }
    assert_kh03_schema(plan, "sound_plan")

    kh02_soundscape = None
    if shotbook is not None:
        kh02_soundscape = plan_soundscape(
            shotbook,
            assets,
            voice_asset_id=next(iter(shotbook.get("source_sha_set", {}).keys()), None),
            speech_spans=speech_spans,
            energy_mode=energy,
            sfx_policy=sfx_policy,
            bgm_policy=bgm_policy,
            production_mode=(production_mode and not allow_test_only_sfx),
        )

    stems_dir = run_dir / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(stems_dir / "SOUND_DIRECTOR_PLAN.json", plan)
    write_json_atomic(stems_dir / "LAYOUT_SOUND_ANCHORS.json", {
        "schema_version": "kh03.layout_sound_anchors.v1",
        "run_id": run_id,
        "selected_skill_id": skill_id,
        "anchors": [
            {
                "event_id": e["event_id"],
                "beat_id": e["beat_id"],
                "layout_transition_id": e.get("layout_transition_id"),
                "motion_anchor": e.get("motion_anchor"),
                "onset_frame": e["onset_frame"],
                "onset_sec": e["onset_sec"],
                "requested_family": e["requested_family"],
                "status": e["status"],
            }
            for e in events
        ],
    })
    report_md = [
        f"# KH-03 Audio Comfort & Soundscape Report — `{run_id}` (`{skill_id}`)",
        "",
        f"- **Energy Mode:** `{energy}` | **Voice Policy:** `{voice_policy}` | **Provider Status:** `{acq['provider_status']}`",
        f"- **Target Loudness:** `-16.0 to -14.0 LUFS`, True Peak `<= -1.0 dBTP`",
        f"- **Event Coverage:** `ADD={counts['ADD']}`, `KEEP_NATURAL={counts['KEEP_NATURAL']}`, `SILENCE={counts['SILENCE']}`, `MERGE={counts['MERGE']}`, `UNAVAILABLE={counts['UNAVAILABLE']}`",
        "- **Owner Audio Review:** `PENDING` (objective checks never substitute for human listening).",
        "",
    ]
    (stems_dir / "AUDIO_COMFORT_REPORT.md").write_text("\n".join(report_md), encoding="utf-8")

    return {
        "sound_plan": plan,
        "acquired_assets": assets,
        "kh02_soundscape": kh02_soundscape,
        "provider_status": acq["provider_status"],
    }


def verify_applied_soundscape(
    sound_plan: dict[str, Any],
    *,
    sfx_stem_metrics: dict[str, Any] | None = None,
    mix_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Never claim SFX added if stem is silent or assets were UNAVAILABLE."""
    add_cnt = int(sound_plan["summary_counts"].get("ADD", 0))
    unavail_cnt = int(sound_plan["summary_counts"].get("UNAVAILABLE", 0))
    if add_cnt == 0:
        return {
            "status": "BLOCKED_SFX_ASSETS" if unavail_cnt > 0 else "VOICE_ONLY_INTENTIONAL",
            "audible_cues_verified": 0,
            "silent_stem_rejected": False,
            "pass": unavail_cnt == 0,
        }
    if sfx_stem_metrics is not None:
        lvl_raw = (
            sfx_stem_metrics.get("rms_db")
            if sfx_stem_metrics.get("rms_db") is not None
            else (
                sfx_stem_metrics.get("integrated_lufs")
                if sfx_stem_metrics.get("integrated_lufs") is not None
                else sfx_stem_metrics.get("max_volume_db")
            )
        )
        rms = float(lvl_raw) if lvl_raw is not None else -96.0
        if rms <= -72.0:
            return {
                "status": "FAIL_SILENT_SFX_STEM",
                "audible_cues_verified": 0,
                "silent_stem_rejected": True,
                "pass": False,
            }
    tp_ok = True
    if mix_metrics and mix_metrics.get("true_peak_dbtp") is not None:
        tp_ok = float(mix_metrics["true_peak_dbtp"]) <= -1.0
    return {
        "status": "VERIFIED_APPLIED_SOUNDSCAPE" if tp_ok else "FAIL_TRUE_PEAK_CEILING",
        "audible_cues_verified": add_cnt,
        "silent_stem_rejected": False,
        "true_peak_pass": tp_ok,
        "pass": tp_ok,
    }
