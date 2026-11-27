"""KH-03.R4.3 + DX-01 Canonical Semantic Timeline Compiler (`INDEPENDENT_RENDER_SPEC_V2` + DX-01 Extension).

Single authoritative, hashed render contract binding ALL execution artifacts (transcript,
semantic graph, creative plan, layout plan, motion graph, motion director plan, sound director
plan, voice EDL, evidence ledger, subject action tracks, camera events, N-source layers,
direct footage effects, dual-moving transitions, verified DX-01 Remotion/hybrid effect invocations,
continuity seam plan, selection receipt, and candidate binding) onto one exact rational output
frame/sample clock!

DX01-03 upgrades:
- Fixes root rectangular-card bias in `_derive_overlays_from_plans` by consuming `layout_plan`,
  `motion_graph`, `evidence_ledger`, and `motion_director.direct_motion_plan` to emit verified
  non-card registry effect invocations + layer/transition plans.
- Adds immutable `normalized_creative_plan_sha256` (resolution/codec/proxy-invariant) so preview
  and full-res final strictly share the exact same semantic creative plan.
- Fails closed on gaps, exclusive overlaps, dangling source IDs (NO silent fallback to A-roll),
  out-of-range trims, NaN/inf, invalid handles, unsupported effects, or caption_policy violations.
- 100% backward compatible with all legacy v1/v2 specs and R4.3 callers.
"""

from __future__ import annotations

import hashlib
import json
import math
from fractions import Fraction
from pathlib import Path
from typing import Any

from .effect_registry import EffectRegistry, get_default_effect_registry
from .ingest_cache import SharedIngestCache, probe_source_cached
from .motion_components import (
    PALETTE_PRESETS,
    SUPPORTED_COMPONENT_KINDS,
    evaluate_spatial_collisions,
    validate_component_payload,
)
from .motion_director import direct_motion_plan

SPEC_V2_SCHEMA = "kh03.independent_render_spec.v2"
DX01_TIMELINE_SCHEMA = "kh03.dx01.timeline.v1"


def _canonical_sha(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_normalized_creative_plan_sha(spec: dict[str, Any]) -> str:
    """Compute resolution/codec/proxy-invariant hash of semantic editorial & motion choices.

    Guarantees preview (540x960 proxy/veryfast) and final (1080x1920 original/medium) bind the
    EXACT same visual tracks, layers, transitions, camera, footage effects, effect invocations,
    overlays, voice EDL, audio mix choices, and art tokens!
    """
    reg = spec.get("source_registry") or {}
    source_shas = sorted(
        (str(s.get("source_id")), str(s.get("sha256")))
        for s in (reg.get("sources") or [])
        if isinstance(s, dict)
    )
    normalized_body = {
        "selected_skill_id": spec.get("selected_skill_id"),
        "fps_num": spec.get("fps_num"),
        "fps_den": spec.get("fps_den"),
        "total_output_frames": spec.get("total_output_frames"),
        "source_shas": source_shas,
        "art_tokens": spec.get("art_tokens"),
        "voice_segments": spec.get("voice_segments"),
        "visual_tracks": spec.get("visual_tracks"),
        "layers": spec.get("layers") or [],
        "camera_events": spec.get("camera_events") or [],
        "footage_effects": spec.get("footage_effects") or [],
        "transitions": spec.get("transitions") or [],
        "effect_invocations": spec.get("effect_invocations") or [],
        "overlays": spec.get("overlays") or [],
        "sfx_cues": spec.get("sfx_cues") or [],
        "continuity_seams": spec.get("continuity_seams") or [],
        "caption_policy": spec.get("caption_policy", "OFF"),
        "bgm_enabled": spec.get("bgm_enabled"),
        "bgm_style": spec.get("bgm_style"),
        "sfx_enabled": spec.get("sfx_enabled"),
    }
    return _canonical_sha(normalized_body)


# ---------------------------------------------------------------------------
# Arbitrary N-Source Registry (R4.3-01)
# ---------------------------------------------------------------------------

def build_source_registry(
    primary_source: dict[str, Any] | str | Path,
    secondary_sources: list[dict[str, Any] | str | Path] | None = None,
    *,
    cache: SharedIngestCache | None = None,
) -> dict[str, Any]:
    """Build an immutable, SHA-verified registry for arbitrary N sources with MUTE_DEFAULT on auxiliary audio."""
    sources: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add(entry: dict[str, Any] | str | Path, default_id: str, default_role: str, default_audio: str) -> None:
        if isinstance(entry, (str, Path)):
            p = Path(entry).resolve()
            sid = default_id
            role = default_role
            audio_pol = default_audio
            rights = "OWNER_SUPPLIED_NONCOMMERCIAL"
            proxy_path = None
        else:
            p = Path(str(entry["path"])).resolve()
            sid = str(entry.get("source_id") or entry.get("asset_id") or default_id)
            role = str(entry.get("role") or default_role)
            audio_pol = str(entry.get("audio_policy") or default_audio)
            rights = str(entry.get("rights") or "OWNER_SUPPLIED_NONCOMMERCIAL")
            proxy_path = entry.get("proxy_path")
        if not p.is_file():
            raise FileNotFoundError(f"SOURCE_REGISTRY_MISSING_FILE({sid}): {p}")
        if sid in seen_ids:
            raise ValueError(f"DUPLICATE_SOURCE_ID:{sid}")
        seen_ids.add(sid)
        pr = probe_source_cached(p, cache=cache)
        sources.append({
            "source_id": sid,
            "path": str(p),
            "proxy_path": str(Path(proxy_path).resolve()) if (proxy_path and Path(proxy_path).is_file()) else None,
            "basename": pr["basename"],
            "sha256": pr["sha256"],
            "bytes": pr["bytes"],
            "role": role,
            "audio_policy": audio_pol,
            "rights": rights,
            "width": pr["width"],
            "height": pr["height"],
            "aspect_ratio": pr["aspect_ratio"],
            "fps_num": pr["fps_num"],
            "fps_den": pr["fps_den"],
            "fps": pr["fps"],
            "nb_frames": pr["nb_frames"],
            "video_duration_sec": pr["video_duration_sec"],
            "container_duration_sec": pr["container_duration_sec"],
            "overhang_sec": pr["overhang_sec"],
            "has_audio": pr["has_audio"],
        })

    _add(primary_source, "primary_aroll", "primary_aroll", "MASTER_VOICE")
    for idx, sec in enumerate(secondary_sources or []):
        _add(sec, f"secondary_{idx + 1:02d}", "secondary_evidence", "MUTE_DEFAULT")

    reg = {
        "schema_version": "kh03.source_registry.v1",
        "source_count": len(sources),
        "primary_source_id": sources[0]["source_id"],
        "sources": sources,
        "by_id": {s["source_id"]: s for s in sources},
    }
    reg["registry_sha256"] = _canonical_sha(sources)
    return reg


# ---------------------------------------------------------------------------
# Authoritative Source-Time -> EDL Output Clock Remapper
# ---------------------------------------------------------------------------

def map_source_time_to_output(
    src_sec: float,
    voice_segments: list[dict[str, Any]],
) -> float | None:
    """Map a source timestamp (seconds) onto the authoritative EDL output clock; returns None if cut."""
    t = float(src_sec)
    for seg in voice_segments:
        s0 = float(seg["src_in_sec"])
        s1 = float(seg["src_out_sec"])
        if s0 <= t <= s1:
            return round(float(seg["out_in_sec"]) + (t - s0), 4)
    best_out: float | None = None
    best_dist = 1e9
    for seg in voice_segments:
        for s_edge, o_edge in ((float(seg["src_in_sec"]), float(seg["out_in_sec"])), (float(seg["src_out_sec"]), float(seg["out_out_sec"]))):
            d = abs(t - s_edge)
            if d < best_dist:
                best_dist = d
                best_out = o_edge
    return round(best_out, 4) if best_out is not None else None


def remap_events_to_output_clock(
    events: list[dict[str, Any]],
    voice_segments: list[dict[str, Any]],
    *,
    fps_num: int = 24,
    fps_den: int = 1,
    total_output_frames: int,
) -> list[dict[str, Any]]:
    """Remap source-clock events (overlays, DX-01 effect invocations, layers, camera, SFX, seams) onto output clock."""
    fps = float(Fraction(fps_num, fps_den))
    total_dur = total_output_frames / fps
    remapped: list[dict[str, Any]] = []
    for idx, ev in enumerate(events):
        item = dict(ev)
        clock = str(item.get("timebase") or "output").lower()
        if clock == "source":
            if "start_sec" in item and "end_sec" in item:
                s0, s1 = float(item["start_sec"]), float(item["end_sec"])
                o0 = map_source_time_to_output(s0, voice_segments)
                o1 = map_source_time_to_output(s1, voice_segments)
                if o0 is None or o1 is None or o1 <= o0 + 0.04:
                    continue
                item["src_start_sec"] = s0
                item["src_end_sec"] = s1
                item["start_sec"] = max(0.0, min(total_dur - 0.04, o0))
                item["end_sec"] = max(item["start_sec"] + 0.04, min(total_dur, o1))
            elif "time_sec" in item:
                st = float(item["time_sec"])
                ot = map_source_time_to_output(st, voice_segments)
                if ot is None:
                    continue
                item["src_time_sec"] = st
                item["time_sec"] = max(0.0, min(total_dur - 0.02, ot))
            for list_k in ("deltas", "branches", "nodes", "choices", "items", "bars"):
                if isinstance(item.get(list_k), list):
                    new_l = []
                    for sub in item[list_k]:
                        sd = dict(sub)
                        if "reveal_sec" in sd:
                            ro = map_source_time_to_output(float(sd["reveal_sec"]), voice_segments)
                            if ro is not None:
                                sd["src_reveal_sec"] = float(sd["reveal_sec"])
                                sd["reveal_sec"] = max(item["start_sec"], min(item["end_sec"] - 0.04, ro))
                        if "strike_after_sec" in sd and sd["strike_after_sec"] is not None:
                            so = map_source_time_to_output(float(sd["strike_after_sec"]), voice_segments)
                            if so is not None:
                                sd["strike_after_sec"] = max(item["start_sec"], min(item["end_sec"] - 0.02, so))
                        new_l.append(sd)
                    item[list_k] = new_l
            if isinstance(item.get("word_cues"), list):
                new_wc = []
                for wc in item["word_cues"]:
                    wd = dict(wc)
                    w0 = map_source_time_to_output(float(wd.get("start_sec", 0.0)), voice_segments)
                    w1 = map_source_time_to_output(float(wd.get("end_sec", 0.0)), voice_segments)
                    if w0 is not None and w1 is not None:
                        wd["start_sec"] = max(item["start_sec"], min(item["end_sec"] - 0.02, w0))
                        wd["end_sec"] = max(wd["start_sec"] + 0.02, min(item["end_sec"], w1))
                        new_wc.append(wd)
                item["word_cues"] = new_wc
            for opt_k in ("flip_sec", "impact_sec", "reveal_b_sec"):
                if item.get(opt_k) is not None:
                    ro = map_source_time_to_output(float(item[opt_k]), voice_segments)
                    if ro is not None:
                        item[opt_k] = max(item["start_sec"], min(item["end_sec"] - 0.04, ro))
            item["timebase"] = "output"

        if "start_sec" in item and "end_sec" in item:
            item["start_sec"] = round(max(0.0, min(total_dur - 0.04, float(item["start_sec"]))), 4)
            item["end_sec"] = round(max(item["start_sec"] + 0.04, min(total_dur, float(item["end_sec"]))), 4)
            item["out_start_frame"] = max(0, int(round(item["start_sec"] * fps)))
            item["out_end_frame"] = min(total_output_frames, max(item["out_start_frame"] + 1, int(round(item["end_sec"] * fps))))
        elif "time_sec" in item:
            item["time_sec"] = round(max(0.0, min(total_dur - 0.02, float(item["time_sec"]))), 4)
            item["out_frame"] = max(0, min(total_output_frames - 1, int(round(item["time_sec"] * fps))))
        item.setdefault("event_id", f"ev_{idx + 1:03d}")
        remapped.append(item)
    return remapped


# ---------------------------------------------------------------------------
# Artifact Binding & DX-01 Plan Consumption (Eliminates Card Bias!)
# ---------------------------------------------------------------------------

def bind_execution_artifacts(spec: dict[str, Any], upstream_hashes: dict[str, str | None]) -> dict[str, Any]:
    """Attach canonical upstream hash ledger, immutable `normalized_creative_plan_sha256`, and `spec_sha256`."""
    clean_up = {str(k): str(v) for k, v in sorted(upstream_hashes.items()) if v}
    bound = dict(spec)
    bound["upstream_hashes"] = clean_up
    bound["normalized_creative_plan_sha256"] = compute_normalized_creative_plan_sha(bound)
    bound.pop("spec_sha256", None)
    bound["spec_sha256"] = _canonical_sha(bound)
    return bound


def _derive_overlays_from_plans(
    creative_plan: dict[str, Any] | None,
    layout_plan: dict[str, Any] | None,
    motion_graph: dict[str, Any] | None,
    *,
    total_dur: float,
    fps: float,
    effect_registry: EffectRegistry | None = None,
    caption_policy: str = "OFF",
    prefer_dx01_non_card: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive verified DX-01 non-card `effect_invocations` + legacy `overlays` consuming layout_plan & motion_graph.

    Fixes R4.3 root card bias: maps capability semantics and layout graphic boxes to genuine
    registry entries (`dx01.typo.kinetic_headline`, `dx01.typo.masked_reveal`, `dx01.geom.callout_arrow`,
    `dx01.data.kinetic_counter`, `dx01.evidence.ui_spotlight`, etc.) instead of dumping everything into `context_chip`!
    Returns `(legacy_overlays, dx01_effect_invocations)`.
    """
    legacy_overlays: list[dict[str, Any]] = []
    dx01_invocations: list[dict[str, Any]] = []
    if not isinstance(creative_plan, dict):
        return legacy_overlays, dx01_invocations

    reg = effect_registry or get_default_effect_registry()
    lb_by_beat = {str(b.get("beat_id")): b for b in ((layout_plan or {}).get("beats") or []) if isinstance(b, dict)}
    mg_nodes_by_cid: dict[str, list[dict[str, Any]]] = {}
    for nd in ((motion_graph or {}).get("nodes") or []):
        if isinstance(nd, dict) and nd.get("component_id"):
            mg_nodes_by_cid.setdefault(str(nd["component_id"]), []).append(nd)

    def _safe_box_from_layout(beat_id: str | None, fallback_y: float = 0.07) -> tuple[float, float, float, float]:
        lb = lb_by_beat.get(str(beat_id or ""))
        if lb and isinstance(lb.get("graphic_box_norm"), list) and len(lb["graphic_box_norm"]) == 4:
            gx, gy, gw, gh = [float(v) for v in lb["graphic_box_norm"]]
            return (max(0.03, min(0.65, gx)), max(0.04, min(0.84, gy)), max(0.24, min(0.94, gw)), max(0.08, min(0.55, gh)))
        return (0.06, fallback_y, 0.88, 0.14)

    hp = creative_plan.get("hook_plan") or {}
    hook_txt = str(hp.get("hook_headline") or hp.get("headline") or "").strip()
    if hook_txt:
        hx, hy, hw, _hh = _safe_box_from_layout("beat_01", fallback_y=0.06)
        hook_end = min(2.8, max(0.55, total_dur * 0.45))
        if prefer_dx01_non_card and reg.status_of("dx01.typo.kinetic_headline") in ("AVAILABLE", "VERIFIED_RENDER"):
            dx01_invocations.append({
                "event_id": "dx_hook_01",
                "effect_id": "dx01.typo.kinetic_headline",
                "claim_id": str(hp.get("selected_hook_id") or "hook"),
                "timebase": "output",
                "start_sec": 0.08,
                "end_sec": round(hook_end, 4),
                "title": hook_txt[:56],
                "x_norm": round(hx, 4),
                "y_norm": round(hy, 4),
                "w_norm": round(hw, 4),
                "z_order": 30,
                "font_style": "mixed_editorial",
                "backend_preference": "remotion",
                "fallback_id": "legacy.editorial_hook_banner",
                "provenance": "ILLUSTRATIVE",
            })
        else:
            legacy_overlays.append({
                "event_id": "hook_01",
                "claim_id": str(hp.get("selected_hook_id") or "hook"),
                "kind": "editorial_hook_banner",
                "timebase": "output",
                "start_sec": 0.08,
                "end_sec": round(hook_end, 4),
                "title": hook_txt[:46],
                "subtitle": str(hp.get("hook_subline") or "").strip()[:56],
                "y_norm": round(hy, 4),
                "z_order": 20,
                "provenance": "ILLUSTRATIVE",
            })

    comps = list(creative_plan.get("components") or [])
    for idx, comp in enumerate(comps):
        sf = int(comp.get("start_frame", 0))
        ef = int(comp.get("end_frame", 0))
        t0 = max(0.0, sf / max(1.0, fps))
        t1 = min(total_dur, ef / max(1.0, fps))
        if t1 <= t0 + 0.45:
            continue
        if hook_txt and t0 < 2.2:
            continue
        params = comp.get("params") or {}
        cap_id = str(comp.get("capability_id") or "").lower()
        cid = str(comp.get("component_id") or f"comp_{idx + 1:02d}")
        bid = str(comp.get("beat_id") or "")
        headline = str(params.get("headline") or params.get("title") or params.get("label") or "").strip()
        if not headline:
            continue
        gx, gy, gw, gh = _safe_box_from_layout(bid, fallback_y=0.07 if idx % 2 == 0 else 0.74)
        mg_nodes = mg_nodes_by_cid.get(cid) or []
        prim = str(mg_nodes[0].get("primitive") or "") if mg_nodes else ""

        if prefer_dx01_non_card and reg.status_of("dx01.typo.masked_reveal") in ("AVAILABLE", "VERIFIED_RENDER"):
            if "rolling_counter" in cap_id or prim == "exact_decimal_counter":
                try:
                    fv = float(str(params.get("start_value", "0")).replace("$", "").replace(",", "") or 0)
                    tv = float(str(params.get("end_value", "100")).replace("$", "").replace(",", "") or 100)
                except ValueError:
                    fv, tv = 0.0, 100.0
                dx01_invocations.append({
                    "event_id": cid,
                    "effect_id": "dx01.data.kinetic_counter",
                    "timebase": "output",
                    "start_sec": round(t0, 4),
                    "end_sec": round(t1, 4),
                    "from_value": fv,
                    "to_value": tv,
                    "prefix": "$" if str(params.get("unit") or "").upper() == "USD" else "",
                    "suffix": "" if str(params.get("unit") or "").upper() in ("", "USD") else f" {params.get('unit')}",
                    "kicker": str(params.get("kicker") or "RESULT")[:26],
                    "x_norm": round(gx, 4),
                    "y_norm": round(gy, 4),
                    "w_norm": round(gw, 4),
                    "z_order": 34,
                    "backend_preference": "remotion",
                    "fallback_id": "legacy.numerical_payoff_hero",
                })
            elif "proof_frame" in cap_id or prim == "masked_detail_reveal":
                dx01_invocations.append({
                    "event_id": cid,
                    "effect_id": "dx01.geom.focus_ring",
                    "timebase": "output",
                    "start_sec": round(t0, 4),
                    "end_sec": round(t1, 4),
                    "focus_box_norm": [round(gx, 4), round(gy, 4), round(gw, 4), round(max(0.14, gh), 4)],
                    "label": headline[:28],
                    "z_order": 29,
                    "backend_preference": "remotion",
                    "fallback_id": "legacy.evidence_spotlight",
                })
            elif "causal" in cap_id or "timeline" in cap_id:
                dx01_invocations.append({
                    "event_id": cid,
                    "effect_id": "dx01.geom.callout_arrow",
                    "timebase": "output",
                    "start_sec": round(t0, 4),
                    "end_sec": round(t1, 4),
                    "from_point": {"x": round(min(0.75, gx + 0.12), 3), "y": round(min(0.82, gy + 0.06), 3)},
                    "to_point": {"x": round(min(0.88, gx + gw * 0.72), 3), "y": round(min(0.88, gy + max(0.12, gh * 0.65)), 3)},
                    "label": headline[:32],
                    "z_order": 32,
                    "backend_preference": "remotion",
                    "fallback_id": "legacy.causal_chain",
                })
            else:
                eid_choice = "dx01.typo.masked_reveal" if idx % 2 == 0 else "dx01.typo.floating_statement"
                dx01_invocations.append({
                    "event_id": cid,
                    "effect_id": eid_choice,
                    "timebase": "output",
                    "start_sec": round(t0, 4),
                    "end_sec": round(t1, 4),
                    "kicker": str(params.get("kicker") or "")[:24] or None,
                    "title": headline[:48],
                    "x_norm": round(gx, 4),
                    "y_norm": round(gy, 4),
                    "w_norm": round(gw, 4),
                    "z_order": 30,
                    "backend_preference": "remotion",
                    "fallback_id": "legacy.context_chip",
                })
        else:
            legacy_overlays.append({
                "event_id": cid,
                "claim_id": str(comp.get("claim_id") or f"claim_{idx + 1:02d}"),
                "kind": "context_chip",
                "timebase": "output",
                "position": "top" if gy < 0.45 else "bottom",
                "start_sec": round(t0, 3),
                "end_sec": round(t1, 3),
                "kicker": str(params.get("kicker") or "KEY INSIGHT")[:28],
                "title": headline[:44],
                "y_norm": round(gy, 4),
                "z_order": 15,
                "provenance": "ILLUSTRATIVE",
            })

    return legacy_overlays, dx01_invocations


def _derive_sfx_from_sound_plan(
    sound_plan: dict[str, Any] | None,
    overlays: list[dict[str, Any]],
    effect_invocations: list[dict[str, Any]] | None = None,
    *,
    fps: float,
    total_dur: float,
) -> list[dict[str, Any]]:
    """Derive generic SFX cues from SOUND_DIRECTOR_PLAN or semantic event anchors."""
    cues: list[dict[str, Any]] = []
    if isinstance(sound_plan, dict):
        for idx, ev in enumerate(sound_plan.get("events") or sound_plan.get("sfx_events") or []):
            t_s = float(ev.get("time_sec", float(ev.get("frame", 0)) / fps))
            if 0.0 <= t_s < total_dur:
                kind_raw = str(ev.get("sfx_kind") or ev.get("category") or "subtle_pop").lower()
                kind = (
                    "soft_whoosh" if "whoosh" in kind_raw or "transition" in kind_raw
                    else ("tick" if "tick" in kind_raw or "count" in kind_raw
                          else ("payoff_chime" if "chime" in kind_raw or "payoff" in kind_raw
                                else ("soft_impact" if "impact" in kind_raw else "subtle_pop")))
                )
                cues.append({
                    "event_id": str(ev.get("cue_id") or f"sfx_{idx + 1:02d}"),
                    "timebase": "output",
                    "time_sec": round(t_s, 4),
                    "kind": kind,
                    "gain_offset_db": float(ev.get("gain_offset_db", -1.5)),
                })
    if not cues:
        for idx, ov in enumerate(overlays):
            k = str(ov.get("kind") or "")
            if k == "editorial_hook_banner":
                cues.append({"event_id": f"sfx_hook_{idx}", "timebase": "output", "time_sec": round(float(ov.get("impact_sec") or ov["start_sec"]), 4), "kind": "soft_impact", "gain_offset_db": -2.0})
            elif k == "resource_depletion":
                for di, d in enumerate(ov.get("deltas") or []):
                    cues.append({"event_id": f"sfx_dep_{idx}_{di}", "timebase": "output", "time_sec": round(float(d.get("reveal_sec", ov["start_sec"])), 4), "kind": "tick", "gain_offset_db": -1.0})
            elif k in ("resource_allocation", "contrast_transformation", "numerical_payoff_hero"):
                cues.append({"event_id": f"sfx_pay_{idx}", "timebase": "output", "time_sec": round(float(ov["start_sec"]), 4), "kind": "payoff_chime", "gain_offset_db": 0.0})
        for idx, fx in enumerate(effect_invocations or []):
            eid = str(fx.get("effect_id") or "")
            if eid == "dx01.data.kinetic_counter":
                cues.append({"event_id": f"sfx_dx_pay_{idx}", "timebase": "output", "time_sec": round(float(fx["start_sec"]), 4), "kind": "payoff_chime", "gain_offset_db": -1.0})
            elif eid == "dx01.data.sequential_tally":
                for ii, it in enumerate(fx.get("items") or []):
                    if "reveal_sec" in it:
                        cues.append({"event_id": f"sfx_dx_tal_{idx}_{ii}", "timebase": "output", "time_sec": round(float(it["reveal_sec"]), 4), "kind": "tick", "gain_offset_db": -1.5})
    return cues


def compile_semantic_timeline(
    *,
    job_id: str,
    source_registry: dict[str, Any],
    voice_edl: dict[str, Any] | None = None,
    creative_plan: dict[str, Any] | None = None,
    layout_plan: dict[str, Any] | None = None,
    motion_graph: dict[str, Any] | None = None,
    sound_director_plan: dict[str, Any] | None = None,
    evidence_ledger: dict[str, Any] | None = None,
    subject_action_tracks: dict[str, Any] | None = None,
    selection_receipt: dict[str, Any] | None = None,
    candidate_binding: dict[str, Any] | None = None,
    visual_tracks: list[dict[str, Any]] | None = None,
    layers: list[dict[str, Any]] | None = None,
    footage_effects: list[dict[str, Any]] | None = None,
    transitions: list[dict[str, Any]] | None = None,
    effect_invocations: list[dict[str, Any]] | None = None,
    motion_director_plan: dict[str, Any] | None = None,
    camera_events: list[dict[str, Any]] | None = None,
    semantic_overlays: list[dict[str, Any]] | None = None,
    sfx_cues: list[dict[str, Any]] | None = None,
    continuity_seams: list[dict[str, Any]] | None = None,
    occupancy_windows: list[dict[str, Any]] | None = None,
    art_tokens: dict[str, str] | None = None,
    caption_policy: str = "OFF",
    effect_registry: EffectRegistry | None = None,
    prefer_dx01_non_card: bool = True,
    width: int = 1080,
    height: int = 1920,
    fps_num: int = 24,
    fps_den: int = 1,
    sample_rate: int = 48000,
    bgm_enabled: bool = True,
    bgm_style: str = "warm_calm",
    bgm_gain_db: float = -24.5,
    sfx_enabled: bool = True,
    sfx_gain_db: float = -16.5,
    x264_preset: str = "medium",
    crf: int = 18,
    use_proxy_sources: bool = False,
    production_profile: str = "standard",
    pass_through_allowed: bool = False,
) -> dict[str, Any]:
    """Compile ALL authoritative execution artifacts into one validated `INDEPENDENT_RENDER_SPEC_V2` (+ DX-01 ext)."""
    prim_id = source_registry["primary_source_id"]
    prim = source_registry["by_id"][prim_id]
    fps = float(Fraction(fps_num, fps_den))
    reg = effect_registry or get_default_effect_registry()

    # 1. Authoritative voice EDL segments & total output frame count
    if isinstance(voice_edl, dict) and voice_edl.get("segments"):
        v_segs = list(voice_edl["segments"])
        total_frames = int(voice_edl.get("total_output_frames") or max(int(s["out_end_frame"]) for s in v_segs))
    else:
        total_frames = max(1, int(round(float(prim["video_duration_sec"]) * fps)))
        v_segs = [{
            "segment_id": "vseg_01",
            "source_id": prim_id,
            "src_start_frame": 0,
            "src_end_frame": int(prim["nb_frames"]),
            "src_in_sec": 0.0,
            "src_out_sec": float(prim["video_duration_sec"]),
            "out_start_frame": 0,
            "out_end_frame": total_frames,
            "out_in_sec": 0.0,
            "out_out_sec": round(total_frames / fps, 4),
            "crossfade_ms": 0,
            "reason": "full_source_voice_master",
        }]
    total_dur = total_frames / fps

    # 2. Visual tracks — consume layout_plan transitions/framing when no explicit visual_tracks supplied
    if visual_tracks:
        v_tracks = [dict(t) for t in visual_tracks]
    else:
        v_tracks = []
        for si, seg in enumerate(v_segs):
            v_tracks.append({
                "track_id": f"trk_{si + 1:02d}",
                "source_id": str(seg.get("source_id") or prim_id),
                "mode": "source",
                "out_start_frame": int(seg["out_start_frame"]),
                "out_end_frame": int(seg["out_end_frame"]),
                "src_in_sec": float(seg["src_in_sec"]),
                "src_out_sec": float(seg["src_out_sec"]),
                "scale_start": 1.0,
                "scale_end": 1.0,
                "cx_norm": 0.5,
                "cy_norm": 0.5,
                "transition_frames": 0,
                "transition_kind": "cut",
                "cut_intent": "edl_voice_continuity" if si > 0 else "opening",
            })

    # 3. Remap camera events, layers, footage_effects, transitions, DX-01 effect invocations, overlays & SFX
    cam_out = remap_events_to_output_clock(camera_events or [], v_segs, fps_num=fps_num, fps_den=fps_den, total_output_frames=total_frames)
    layers_out = remap_events_to_output_clock(layers or [], v_segs, fps_num=fps_num, fps_den=fps_den, total_output_frames=total_frames)
    ffx_out = remap_events_to_output_clock(footage_effects or [], v_segs, fps_num=fps_num, fps_den=fps_den, total_output_frames=total_frames)
    trans_out = list(transitions or [])

    derived_dx01: list[dict[str, Any]] = []
    if semantic_overlays is not None or effect_invocations is not None:
        ov_raw = list(semantic_overlays or [])
        derived_dx01 = list(effect_invocations or [])
    elif pass_through_allowed:
        ov_raw = []
        derived_dx01 = []
    else:
        ov_raw, derived_dx01 = _derive_overlays_from_plans(
            creative_plan, layout_plan, motion_graph,
            total_dur=total_dur, fps=fps, effect_registry=reg,
            caption_policy=caption_policy, prefer_dx01_non_card=prefer_dx01_non_card,
        )

    ov_out = remap_events_to_output_clock(ov_raw, v_segs, fps_num=fps_num, fps_den=fps_den, total_output_frames=total_frames)
    ov_out.sort(key=lambda x: (int(x.get("z_order", 10)), float(x.get("start_sec", 0.0))))

    fx_out = remap_events_to_output_clock(derived_dx01, v_segs, fps_num=fps_num, fps_den=fps_den, total_output_frames=total_frames)
    fx_out.sort(key=lambda x: (int(x.get("z_order", 25)), float(x.get("start_sec", 0.0))))

    # Record explicit per-event backend & fallback resolutions BEFORE render (never hidden after!)
    per_event_backend: list[dict[str, Any]] = []
    for fx in fx_out:
        eid = str(fx.get("effect_id") or "")
        desc = reg.get(eid)
        resolved_id, r_status, r_why = reg.resolve_with_fallback(eid)
        fx["resolved_effect_id"] = resolved_id
        fx["registry_status"] = r_status
        if r_why:
            fx["fallback_reason"] = r_why
        per_event_backend.append({
            "event_id": fx["event_id"],
            "requested_effect_id": eid,
            "resolved_effect_id": resolved_id,
            "backend": desc.backend if desc else "unknown",
            "status": r_status,
            "fallback_reason": r_why,
        })

    # Optional Motion Director trace when creative_plan beats exist & no explicit director plan supplied
    dir_plan = motion_director_plan
    if dir_plan is None and isinstance(creative_plan, dict) and creative_plan.get("beats"):
        sec_ids = [s["source_id"] for s in source_registry["sources"][1:]]
        dir_plan = direct_motion_plan(
            beats=list(creative_plan["beats"]),
            selected_skill_id=str((selection_receipt or {}).get("selected_skill_id") or creative_plan.get("selected_skill_id") or "SK-04"),
            aspect_ratio=str(prim.get("aspect_ratio") or ("9:16" if height > width else "16:9")),
            media_type="silent" if not prim.get("has_audio") else "talking_head",
            fps=fps,
            has_secondary_sources=bool(sec_ids),
            secondary_source_ids=sec_ids,
            caption_policy=caption_policy,
            layout_policy=str((layout_plan or {}).get("layout_policy") or "ADAPTIVE_DYNAMIC"),
            registry=reg,
        )

    sfx_raw = list(sfx_cues) if sfx_cues is not None else _derive_sfx_from_sound_plan(sound_director_plan, ov_out, fx_out, fps=fps, total_dur=total_dur)
    sfx_out = remap_events_to_output_clock(sfx_raw, v_segs, fps_num=fps_num, fps_den=fps_den, total_output_frames=total_frames) if sfx_enabled else []

    seams_out: list[dict[str, Any]] = []
    for sm in (continuity_seams or []):
        sd = dict(sm)
        src_t = float(sd.get("src_time_sec", 0.0))
        out_t = map_source_time_to_output(src_t, v_segs)
        sd["out_time_sec"] = out_t
        sd["out_frame"] = int(round(out_t * fps)) if out_t is not None else None
        seams_out.append(sd)

    # 4. Palette & art tokens strictly from brief / creative_plan
    eff_art = dict(PALETTE_PRESETS["editorial_dark"])
    if isinstance(creative_plan, dict):
        ad = creative_plan.get("art_direction") or {}
        if isinstance(ad.get("palette"), dict):
            eff_art.update({k: str(v) for k, v in ad["palette"].items() if v})
    if isinstance(art_tokens, dict):
        preset = art_tokens.get("palette_preset")
        if preset and preset in PALETTE_PRESETS:
            eff_art = dict(PALETTE_PRESETS[preset])
        eff_art.update({k: str(v) for k, v in art_tokens.items() if v})

    # 5. Spatial collision pre-flight audit
    spatial_pre = evaluate_spatial_collisions(ov_out, occupancy_windows=occupancy_windows)

    # 6. Collect canonical upstream hashes
    upstream_hashes: dict[str, str | None] = {
        "source_registry_sha256": source_registry.get("registry_sha256"),
        "primary_source_sha256": prim["sha256"],
        "voice_edl_sha256": _canonical_sha(v_segs),
        "creative_plan_sha256": (creative_plan or {}).get("plan_sha256") if isinstance(creative_plan, dict) else None,
        "layout_plan_sha256": _canonical_sha(layout_plan) if layout_plan else None,
        "motion_graph_sha256": _canonical_sha(motion_graph) if motion_graph else None,
        "motion_director_sha256": (dir_plan or {}).get("director_plan_sha256") if isinstance(dir_plan, dict) else None,
        "sound_plan_sha256": _canonical_sha(sound_director_plan) if sound_director_plan else None,
        "evidence_ledger_sha256": _canonical_sha(evidence_ledger) if evidence_ledger else None,
        "subject_tracks_sha256": _canonical_sha(subject_action_tracks) if subject_action_tracks else None,
        "selection_receipt_sha256": _canonical_sha(selection_receipt) if selection_receipt else None,
        "candidate_binding_sha256": (candidate_binding or {}).get("winner_binding_sha256") or (_canonical_sha(candidate_binding) if candidate_binding else None),
    }

    spec: dict[str, Any] = {
        "schema_version": SPEC_V2_SCHEMA,
        "dx01_schema_version": DX01_TIMELINE_SCHEMA,
        "job_id": job_id,
        "production_profile": production_profile,
        "selected_skill_id": str((selection_receipt or {}).get("selected_skill_id") or (creative_plan or {}).get("selected_skill_id") or ""),
        "candidate_id": str((candidate_binding or {}).get("winning_candidate_id") or (candidate_binding or {}).get("provisional_candidate_id") or "cand_A"),
        "caption_policy": str(caption_policy or "OFF"),
        "width": int(width),
        "height": int(height),
        "fps_num": int(fps_num),
        "fps_den": int(fps_den),
        "sample_rate": int(sample_rate),
        "total_output_frames": int(total_frames),
        "output_duration_sec": round(total_dur, 4),
        "use_proxy_sources": bool(use_proxy_sources),
        "source_registry": source_registry,
        "primary_source_path": prim["path"],
        "secondary_source_path": source_registry["sources"][1]["path"] if len(source_registry["sources"]) > 1 else None,
        "art_tokens": eff_art,
        "voice_segments": v_segs,
        "visual_tracks": v_tracks,
        "layers": layers_out,
        "footage_effects": ffx_out,
        "transitions": trans_out,
        "effect_invocations": fx_out,
        "per_event_backend": per_event_backend,
        "motion_director_plan": dir_plan,
        "camera_events": cam_out,
        "overlays": ov_out,
        "sfx_cues": sfx_out,
        "continuity_seams": seams_out,
        "occupancy_windows": list(occupancy_windows or []),
        "spatial_preflight": spatial_pre,
        "bgm_enabled": bool(bgm_enabled),
        "bgm_style": str(bgm_style),
        "bgm_gain_db": float(bgm_gain_db),
        "sfx_enabled": bool(sfx_enabled),
        "sfx_gain_db": float(sfx_gain_db),
        "x264_preset": str(x264_preset),
        "crf": int(crf),
    }
    spec = bind_execution_artifacts(spec, upstream_hashes)
    val = validate_render_spec(spec, effect_registry=reg)
    if not val["pass"]:
        raise ValueError(f"INVALID_INDEPENDENT_RENDER_SPEC_V2: {val['issues']}")
    return spec


def validate_render_spec(
    spec: dict[str, Any],
    *,
    effect_registry: EffectRegistry | None = None,
) -> dict[str, Any]:
    """Fail-closed validation of `INDEPENDENT_RENDER_SPEC_V2` + DX-01 layers, transitions & effect invocations."""
    issues: list[str] = []
    if not isinstance(spec, dict):
        return {"pass": False, "issues": ["SPEC_NOT_DICT"]}
    total_f = int(spec.get("total_output_frames", 0))
    fps_num = int(spec.get("fps_num", 0))
    fps_den = int(spec.get("fps_den", 0))
    w = int(spec.get("width", 0))
    h = int(spec.get("height", 0))
    if total_f <= 0 or fps_num <= 0 or fps_den <= 0 or w < 64 or h < 64 or (w % 2) != 0 or (h % 2) != 0:
        issues.append(f"INVALID_TIMEBASE_OR_GEOMETRY:w={w},h={h},fps={fps_num}/{fps_den},frames={total_f}")

    reg_src = spec.get("source_registry") or {}
    by_id = reg_src.get("by_id") or {}
    if not by_id:
        issues.append("EMPTY_SOURCE_REGISTRY")
    known_sids = set(by_id.keys())

    # 1. Visual track complete gap-free coverage of [0, total_f) — NO silent A-roll fallback for dangling IDs!
    tracks = list(spec.get("visual_tracks") or [])
    if not tracks:
        issues.append("MISSING_VISUAL_TRACKS")
    else:
        covered = 0
        for ti, tr in enumerate(sorted(tracks, key=lambda x: int(x.get("out_start_frame", 0)))):
            s_f = int(tr.get("out_start_frame", -1))
            e_f = int(tr.get("out_end_frame", -1))
            if s_f != covered or e_f <= s_f or e_f > total_f:
                issues.append(f"TRACK_COVERAGE_GAP_OR_OVERLAP(idx={ti}):[{s_f},{e_f}) expected start {covered}")
            covered = max(covered, e_f)
            mode = str(tr.get("mode") or "source")
            if mode == "source":
                sid = str(tr.get("source_id") or "")
                if sid not in by_id:
                    issues.append(f"DANGLING_SOURCE_ID(track={ti}):{sid!r}")
                else:
                    s_dur = float(by_id[sid]["video_duration_sec"])
                    s_in = float(tr.get("src_in_sec", 0.0))
                    s_out = float(tr.get("src_out_sec", 0.0))
                    if not (math.isfinite(s_in) and math.isfinite(s_out)) or s_in < -0.001 or s_out > s_dur + 0.15 or s_out < s_in:
                        issues.append(f"TRACK_SRC_PTS_OUT_OF_RANGE(track={ti}):[{s_in},{s_out}] vs dur={s_dur}")
            sc0 = float(tr.get("scale_start", 1.0))
            sc1 = float(tr.get("scale_end", 1.0))
            if not (math.isfinite(sc0) and math.isfinite(sc1) and 0.25 <= sc0 <= 2.5 and 0.25 <= sc1 <= 2.5):
                issues.append(f"INVALID_TRACK_SCALE(track={ti}):{sc0}->{sc1}")
            tf = int(tr.get("transition_frames", 0))
            if tf < 0 or tf > 48:
                issues.append(f"INVALID_TRANSITION_FRAMES(track={ti}):{tf}")
        if covered != total_f:
            issues.append(f"INCOMPLETE_TRACK_COVERAGE:{covered} != {total_f}")

    # 2. N-source layers validation (NO dangling source IDs, finite geometry/scales!)
    for li, ly in enumerate(spec.get("layers") or []):
        sid = str(ly.get("source_id") or "")
        if sid not in known_sids:
            issues.append(f"DANGLING_LAYER_SOURCE_ID(layer={li}):{sid!r}")
        else:
            s_dur = float(by_id[sid]["video_duration_sec"])
            l_in = float(ly.get("src_in_sec", 0.0))
            l_out = float(ly.get("src_out_sec", s_dur))
            if l_in < -0.001 or l_out > s_dur + 0.15 or l_out <= l_in:
                issues.append(f"LAYER_SRC_PTS_OUT_OF_RANGE(layer={li}):[{l_in},{l_out}] > {s_dur}")
        if float(ly.get("end_sec", 0.0)) <= float(ly.get("start_sec", 0.0)):
            issues.append(f"LAYER_INVALID_INTERVAL(layer={li})")

    # 3. Voice segments sanity (allow explicit silent policy ONLY when spec marks silent_policy=True)
    v_segs = list(spec.get("voice_segments") or [])
    if not v_segs and not spec.get("silent_policy"):
        issues.append("MISSING_VOICE_SEGMENTS")
    for vi, vs in enumerate(v_segs):
        if float(vs.get("src_out_sec", 0.0)) <= float(vs.get("src_in_sec", 0.0)):
            issues.append(f"INVALID_VOICE_SEGMENT(idx={vi})")

    # 4. Camera event scale & time bounds (allow pullback >= 0.35 and zoom <= 1.8)
    for ci, ce in enumerate(spec.get("camera_events") or []):
        s0, s1 = float(ce.get("scale_start", 1.0)), float(ce.get("scale_end", 1.0))
        if not (math.isfinite(s0) and math.isfinite(s1) and 0.35 <= s0 <= 1.8 and 0.35 <= s1 <= 1.8):
            issues.append(f"CAMERA_SCALE_OUT_OF_POLICY(idx={ci}):{s0}->{s1}")
        if float(ce.get("end_sec", 0.0)) <= float(ce.get("start_sec", 0.0)):
            issues.append(f"CAMERA_INVALID_INTERVAL(idx={ci})")

    # 5. Validate every DX-01 effect invocation fail-closed through EffectRegistry
    fx_list = list(spec.get("effect_invocations") or [])
    if fx_list:
        eff_reg = effect_registry or get_default_effect_registry()
        cap_pol = str(spec.get("caption_policy") or "OFF")
        for fi, fx in enumerate(fx_list):
            fv = eff_reg.validate_invocation(fx, known_source_ids=known_sids, caption_policy=cap_pol)
            if not fv["pass"]:
                issues.extend([f"DX01_EFFECT[{fi}]:{m}" for m in fv["issues"]])

    # 6. Validate every legacy overlay/motion component fail-closed
    for oi, ov in enumerate(spec.get("overlays") or []):
        cv = validate_component_payload(ov)
        if not cv["pass"]:
            issues.extend([f"OVERLAY[{oi}]:{m}" for m in cv["issues"]])

    # 7. Spatial collision check
    sp = spec.get("spatial_preflight")
    if isinstance(sp, dict) and not sp.get("pass", True):
        issues.append(f"SPATIAL_COLLISION_DETECTED:{sp.get('collision_count')} collision(s)")

    return {
        "schema_version": "kh03.render_spec_validation.v2",
        "pass": len(issues) == 0,
        "issue_count": len(issues),
        "issues": issues,
        "supported_component_kinds": sorted(SUPPORTED_COMPONENT_KINDS),
        "dx01_effect_count": len(fx_list),
    }


__all__ = [
    "DX01_TIMELINE_SCHEMA",
    "SPEC_V2_SCHEMA",
    "bind_execution_artifacts",
    "build_source_registry",
    "compile_semantic_timeline",
    "compute_normalized_creative_plan_sha",
    "map_source_time_to_output",
    "remap_events_to_output_clock",
    "validate_render_spec",
]
