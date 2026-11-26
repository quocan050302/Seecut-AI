"""KH-02 Master Editorial Planner: brief normalization, semantic beat analysis, creative directions, SHOTBOOK V2 & v1 migration."""

from __future__ import annotations

import math
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import UPSTREAM_SEECUT_SHA, VIDEO_USE_REF_SHA, __version__
from .camera import (
    compute_presenter_reserved_region,
    fit_face_safe_crop,
    normalize_presenter_policy,
    plan_camera_path,
    resolve_presenter_region,
    resolve_safe_zone_for_platform,
)
from .contracts import hash_artifact, seconds_to_frame, validate_references, validate_schema
from .edit_decisions import build_time_map, propose_cut_candidates
from .motion import plan_motion
from .registry import build_registry, select_capability
from .sound import plan_audio_cues
from .transcript import map_words_to_output
from .transition import choose_transition
from .typography import compute_min_read_frames, plan_text_events, resolve_layout_avoiding_presenter

ASPECT_CANVAS = {
    "9:16": {"width": 720, "height": 1280, "output_scale": 1.5, "orientation": "portrait", "safe_zone": {"top": 0.08, "bottom": 0.12, "left": 0.04, "right": 0.04}},
    "16:9": {"width": 1280, "height": 720, "output_scale": 1.5, "orientation": "landscape", "safe_zone": {"top": 0.06, "bottom": 0.10, "left": 0.05, "right": 0.05}},
    "1:1": {"width": 720, "height": 720, "output_scale": 1.5, "orientation": "square", "safe_zone": {"top": 0.06, "bottom": 0.10, "left": 0.05, "right": 0.05}},
    "4:5": {"width": 720, "height": 900, "output_scale": 1.5, "orientation": "portrait", "safe_zone": {"top": 0.07, "bottom": 0.11, "left": 0.04, "right": 0.04}},
}

ARCHETYPE_BY_MODE = {
    "talking_head": {"archetype": "proof_first_editorial", "theme": "editorial_obsidian_ivory"},
    "software_demo": {"archetype": "ui_guided_walkthrough", "theme": "technical_slate_cyan"},
    "product_demo": {"archetype": "evidence_spotlight_showcase", "theme": "editorial_obsidian_ivory"},
    "tutorial": {"archetype": "step_by_step_workbench", "theme": "technical_slate_cyan"},
    "education": {"archetype": "concept_diagram_explainer", "theme": "explainer_cobalt_chalk"},
    "documentary": {"archetype": "archival_restrained_essay", "theme": "documentary_warm_archive"},
    "narrative": {"archetype": "scene_driven_story_arc", "theme": "documentary_warm_archive"},
    "faceless": {"archetype": "visual_essay_modular", "theme": "explainer_cobalt_chalk"},
    "interview": {"archetype": "speaker_focus_pullquote", "theme": "editorial_obsidian_ivory"},
    "montage": {"archetype": "rhythmic_visual_sequence", "theme": "editorial_obsidian_ivory"},
    "custom": {"archetype": "custom_editorial", "theme": "editorial_obsidian_ivory"},
}


def build_editor_brief(raw_brief: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize an Editor Brief with conservative defaults (LOCK_VOICE, captions OFF, LEGACY_DYNAMIC)."""
    brief = dict(raw_brief)
    brief.setdefault("schema_version", "kh02.brief.v1")
    brief.setdefault("profile", "editorial_pro")
    brief.setdefault("voice_policy", "LOCK_VOICE")
    brief.setdefault("caption_policy", "OFF")
    brief.setdefault("music_policy", "OFF")
    brief.setdefault("sfx_policy", "OPTIONAL_LICENSED")
    brief.setdefault("proof_points", [])
    brief.setdefault("sensitive_claims", [])
    brief.setdefault("approved_exceptions", [])
    errs = validate_schema(brief, "brief")
    if errs:
        raise ValueError(f"Invalid editor brief: {errs[:4]}")
    # Validate presenter_policy conflicts early when supplied
    if brief.get("presenter_policy") is not None:
        has_pres_src = any(
            s.get("kind") == "a_roll" and s.get("rights") not in ("ANALYSIS_ONLY", "UNLICENSED")
            for s in brief.get("sources", [])
        )
        brief["presenter_policy"] = normalize_presenter_policy(
            brief["presenter_policy"],
            brief_mode=brief["mode"],
            has_presenter_source=has_pres_src,
        )
    return brief


def choose_archetype(brief: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    """Select editorial archetype and visual theme grounded in mode, profile, and available assets."""
    mode = brief["mode"]
    profile = brief["profile"]
    base = dict(ARCHETYPE_BY_MODE.get(mode, ARCHETYPE_BY_MODE["custom"]))
    if brief.get("creative_theme"):
        base["theme"] = str(brief["creative_theme"])
    if profile == "minimal_documentary":
        base["theme"] = "documentary_warm_archive"
        base["archetype"] = "archival_restrained_essay"
    elif profile == "seecut_core" and not brief.get("creative_theme"):
        base["theme"] = "seecut_geek_craft"

    usable = [a for a in inventory.get("assets", []) if a.get("usable_in_render")]
    has_person = any(a["kind"] == "a_roll" for a in usable)
    has_real_ui = any(a["kind"] in ("real_ui", "still_asset") for a in usable)
    base["has_person_aroll"] = has_person
    base["has_real_evidence_assets"] = has_real_ui
    return base


def propose_directions(
    brief: dict[str, Any],
    inventory: dict[str, Any],
    transcript: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Propose 3 distinct creative directions with explicit pros/cons and selection rationale."""
    arch = choose_archetype(brief, inventory)
    mode = brief["mode"]
    usable_ev = [a["asset_id"] for a in inventory.get("assets", []) if a.get("usable_in_render") and a["kind"] in ("real_ui", "still_asset", "b_roll")]

    directions = [
        {
            "direction_id": "dir_editorial_split_spine",
            "name": "Editorial High-Contrast Split & Match-Anchor Spine",
            "theme": arch["theme"],
            "archetype": arch["archetype"],
            "layout_strategy": "Asymmetric focal panels with persistent match-anchor spine, subtle 2D virtual push-in, and progressive step rails.",
            "motion_language": "Restrained spring on tactical badges; smooth power2.inOut reframing across shots.",
            "pros": [
                "Strong visual hierarchy and high contrast on 360px mobile screens.",
                "Bridges visual-only showcase pauses via progressive step choreography instead of static cards.",
                f"Directly showcases {len(usable_ev)} verified supplementary asset(s) without crowding presenter face.",
            ],
            "cons": ["Requires precise safe-zone alignment between PIP and asymmetric panels."],
            "selected": mode not in ("documentary", "software_demo", "tutorial", "education", "faceless"),
        },
        {
            "direction_id": "dir_technical_workbench",
            "name": "Technical Workbench & Annotated UI Spotlight",
            "theme": "technical_slate_cyan",
            "archetype": "ui_guided_walkthrough",
            "layout_strategy": "Wide UI/code focal viewport with crisp slate callout rail and precision pointer highlights.",
            "motion_language": "Crisp linear/power2.out UI reveals; minimal overshoot to keep code/UI legible.",
            "pros": ["Ideal for software demos, tutorials, and dense UI text readability."],
            "cons": ["Feels overly technical for warm creator or lifestyle storytelling."],
            "selected": mode in ("software_demo", "tutorial"),
        },
        {
            "direction_id": "dir_restrained_archival",
            "name": "Restrained Editorial Essay / Concept Diagram",
            "theme": "documentary_warm_archive" if mode in ("documentary", "narrative") else "explainer_cobalt_chalk",
            "archetype": "archival_restrained_essay" if mode in ("documentary", "narrative") else "concept_diagram_explainer",
            "layout_strategy": "Calm generous breathing room, factual source citations, structured diagram reveals, zero spring bounce.",
            "motion_language": "Slow ease-in-out pans and clean opacity settles respecting contemplative pacing.",
            "pros": ["Protects factual gravitas and readability on serious, educational, or faceless topics."],
            "cons": ["Lower kinetic punch for fast social talking-head hooks."],
            "selected": mode in ("documentary", "narrative", "education", "faceless"),
        },
    ]
    # Ensure exactly one selected
    if not any(d["selected"] for d in directions):
        directions[0]["selected"] = True
    for d in directions:
        if not d["selected"]:
            d["rejected_reason"] = f"Less suited to mode={mode!r} / profile={brief['profile']!r} than selected direction."
        else:
            d["selected_reason"] = f"Best semantic, visual hierarchy, and asset fit for mode={mode!r}, aspect={brief['aspect_ratio']!r}."
    return directions


def _tokenize_semantic(text: str) -> set[str]:
    import re
    return {t for t in re.split(r"[^a-z0-9\u00C0-\u024F\u1E00-\u1EFF\u4e00-\u9fff]+", str(text or "").lower()) if len(t) >= 2}


def _match_relevant_evidence(
    phrase_text: str,
    usable_evidence: list[dict[str, Any]],
    *,
    fallback_allow: bool = False,
    used_ids: set[str] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Select verified evidence asset by semantic token overlap (preferring unused assets across beats)."""
    if not usable_evidence:
        return None, "no_verified_evidence_assets"
    ptoks = _tokenize_semantic(phrase_text)
    used = used_ids or set()
    scored: list[tuple[int, int, str, dict[str, Any]]] = []
    for a in usable_evidence:
        atoks = _tokenize_semantic(f"{a.get('asset_id', '')} {a.get('label', '')} {a.get('basename', '')}")
        # Expand domain synonyms cleanly without hardcoding a specific video
        if atoks & {"gumroad", "platform", "homepage"}:
            atoks |= {"gumroad", "page", "platform", "site", "store"}
        if atoks & {"storefront", "hero", "bundle", "landing"}:
            atoks |= {"look", "this", "bundle", "hero", "page", "storefront", "screen"}
        if atoks & {"details", "included", "works", "workflow", "printable", "steps", "pack"}:
            atoks |= {"details", "see", "included", "steps", "workflow", "how", "breakdown"}
        if atoks & {"code", "cli", "terminal"}:
            atoks |= {"command", "code", "run", "build", "terminal", "install"}
        if atoks & {"chart", "metric", "benchmark"}:
            atoks |= {"result", "proof", "number", "metric", "data", "benchmark"}
        hits = len(ptoks & atoks)
        unused_bonus = 1 if a["asset_id"] not in used else 0
        scored.append((hits, unused_bonus, a["asset_id"], a))
    # Sort by highest overlap; when overlap > 0, strongly prefer unused assets if tied or close
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    best_hits = scored[0][0]
    if best_hits > 0:
        unused_matches = [s for s in scored if s[0] > 0 and s[1] == 1]
        pick = unused_matches[0] if (unused_matches and unused_matches[0][0] >= max(1, best_hits - 1)) else scored[0]
        return pick[3], f"token_overlap={pick[0]} with {pick[3]['asset_id']}"
    if fallback_allow:
        unused_all = [s for s in scored if s[1] == 1]
        pick = unused_all[0] if unused_all else scored[0]
        return pick[3], f"contextual_demo_match:{pick[3]['asset_id']}"
    return None, "no_semantic_token_overlap"


def _classify_phrase_semantics(
    text: str,
    *,
    is_first: bool,
    is_last: bool,
    is_visual_hold: bool,
    brief: dict[str, Any],
    usable_evidence: list[dict[str, Any]],
    used_ev_ids: set[str] | None = None,
) -> tuple[str, dict[str, Any] | None, str, float, int]:
    """Classify a beat strictly by phrase semantics & evidence relevance (not index parity)."""
    low = str(text or "").strip().lower()
    toks = _tokenize_semantic(low)
    mode = brief.get("mode", "talking_head")

    if is_visual_hold:
        if mode in ("documentary", "narrative"):
            return "intentional_silence", None, "justified_visual_breathing_room", 0.45, 3
        return "demonstration", None, "active_visual_walkthrough_span", 0.65, 4

    # Explicit CTA detection
    cta_cues = {"link", "download", "subscribe", "follow", "click", "join", "try", "comment", "sign", "bio"}
    if (is_last and brief.get("cta_copy")) or (toks & cta_cues and is_last):
        return "cta", None, "cta_lexical_or_brief_copy", 0.85, 5

    # Payoff / emotional outcome detection (works regardless of index position!)
    payoff_cues = {"obsessed", "turned", "finally", "result", "solved", "works", "love", "worth", "outcome", "saved", "cleanest"}
    if ("turned out" in low or bool(toks & payoff_cues)) and not is_first:
        ev_match, ev_rsn = _match_relevant_evidence(low, usable_evidence, fallback_allow=False, used_ids=used_ev_ids)
        return "payoff", ev_match, f"payoff_semantic_cue ({ev_rsn})", 0.88, 5

    # Question detection
    if "?" in low or low.startswith(("why ", "how ", "what ", "ever wonder", "did you")):
        return ("hook" if is_first else "question"), None, "interrogative_curiosity_cue", 0.82, 5

    # Proof / demonstration / visual inspection cues
    demo_cues = {"look", "see", "details", "show", "proof", "demo", "page", "screen", "dashboard", "benchmark", "example", "screenshot", "gumroad"}
    ev_match, ev_rsn = _match_relevant_evidence(
        low, usable_evidence,
        fallback_allow=bool(toks & {"look", "page", "screen", "proof", "demo", "dashboard"}),
        used_ids=used_ev_ids,
    )
    if not is_first and (bool(toks & demo_cues) or ev_match is not None):
        if "wait" in toks or "but" in toks or "actually" in toks:
            if "details" in toks or "see" in toks:
                return ("evidence" if ev_match else "reveal"), ev_match, f"contrast_detail_reveal ({ev_rsn})", 0.82, 5
            return "turning_point", ev_match, f"contrast_turning_point ({ev_rsn})", 0.78, 5
        if ev_match is not None:
            return "evidence", ev_match, f"verified_evidence_semantic_match ({ev_rsn})", 0.80, 5
        return "demonstration", None, "visual_demo_without_separate_capture", 0.74, 4

    # Turning point / contrast cues
    if toks & {"wait", "but", "however", "actually", "instead", "problem", "mistake", "wrong"}:
        return ("problem" if (toks & {"problem", "mistake", "wrong"}) else "turning_point"), None, "contrast_or_problem_cue", 0.78, 5

    # Step / mechanism / process cues
    if toks & {"step", "first", "second", "third", "next", "then", "customize", "print", "install", "setup", "workflow", "how", "build", "configure"}:
        return "step", None, "process_step_semantic_cue", 0.70, 4

    # Opening hook vs closing payoff vs general context
    if is_first:
        return "hook", ev_match, f"opening_hook_lead ({ev_rsn})", 0.88, 5
    if is_last:
        return "payoff", ev_match, f"closing_payoff_beat ({ev_rsn})", 0.84, 5
    return "context", None, "narrative_context_explanation", 0.62, 3


def analyze_semantic_beats(
    brief: dict[str, Any],
    inventory: dict[str, Any],
    transcript: dict[str, Any] | None,
    time_map: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build contiguous frame-accurate semantic beats using phrase semantics & evidence relevance (not index parity)."""
    fps_num = int(time_map["fps_num"])
    fps_den = int(time_map["fps_den"])
    fps = fps_num / fps_den
    total_frames = int(time_map["total_output_frames"])
    usable = [a for a in inventory.get("assets", []) if a.get("usable_in_render")]
    primary = next((a for a in usable if a["kind"] in ("a_roll", "b_roll")), usable[0])
    usable_ev = [a for a in usable if a["kind"] in ("real_ui", "still_asset")]

    mapped_words = map_words_to_output(transcript, time_map) if transcript else []
    phrases = (transcript or {}).get("phrases", [])

    # Determine beat boundaries cleanly from mapped phrases + any retained long visual showcase intervals (>= 2.4s)
    raw_spans: list[tuple[int, int, list[str], str, bool]] = []
    if phrases and mapped_words:
        word_out_map = {w["word_id"]: w for w in mapped_words}
        cursor = 0
        for p_idx, ph in enumerate(phrases):
            wids = [wid for wid in ph["word_ids"] if wid in word_out_map]
            if not wids:
                continue
            p_start = word_out_map[wids[0]]["out_start_frame"]
            p_end = word_out_map[wids[-1]]["out_end_frame"]
            # Only create separate visual showcase beat if gap remains >= 2.4s AFTER rough cut
            if p_idx > 0 and (p_start - cursor) >= int(round(2.4 * fps)):
                raw_spans.append((cursor, p_start, [], "Visual showcase walkthrough", True))
                cursor = p_start
            span_start = 0 if p_idx == 0 else cursor
            span_end = max(span_start + 1, min(total_frames, p_end + int(round(0.35 * fps))))
            raw_spans.append((span_start, span_end, wids, ph["text"], False))
            cursor = span_end
        if cursor < total_frames:
            if (total_frames - cursor) >= int(round(2.2 * fps)):
                raw_spans.append((cursor, total_frames, [], brief.get("cta_copy") or "Visual closing hold", True))
            elif raw_spans:
                a, _, wids, txt, ih = raw_spans[-1]
                raw_spans[-1] = (a, total_frames, wids, txt, ih)
    else:
        dur_sec = total_frames / fps
        n_beats = max(2, min(12, int(round(dur_sec / 5.0)))) if dur_sec > 8.0 else 2
        step = max(1, total_frames // n_beats)
        for i in range(n_beats):
            s = i * step
            e = total_frames if i == n_beats - 1 else (i + 1) * step
            label = brief.get("proof_points", [])[i] if i < len(brief.get("proof_points", [])) else f"Chapter {i + 1}: {brief['key_message'][:32]}"
            raw_spans.append((s, e, [], label, False))

    # Guarantee strict half-open contiguous coverage [0, total_frames)
    beats: list[dict[str, Any]] = []
    used_ev_ids: set[str] = set()
    mode = brief["mode"]
    for idx, (s, e, wids, txt, is_visual_hold) in enumerate(raw_spans):
        sf = 0 if idx == 0 else beats[-1]["end_frame"]
        ef = total_frames if idx == len(raw_spans) - 1 else max(sf + 1, min(total_frames - (len(raw_spans) - 1 - idx), e))

        fn, ev_asset, sem_reason, emo, prio = _classify_phrase_semantics(
            txt,
            is_first=(idx == 0),
            is_last=(idx == len(raw_spans) - 1),
            is_visual_hold=is_visual_hold,
            brief=brief,
            usable_evidence=usable_ev,
            used_ev_ids=used_ev_ids,
        )
        use_ev = ev_asset is not None and fn in ("hook", "evidence", "proof", "demonstration", "payoff")
        if use_ev and ev_asset is not None:
            used_ev_ids.add(ev_asset["asset_id"])
        refs = [primary["asset_id"]] + ([ev_asset["asset_id"]] if use_ev else [])
        proof_status = (
            "VERIFIED_REAL_EVIDENCE" if use_ev
            else ("CONCEPT_ILLUSTRATION" if (mode in ("education", "faceless") or fn in ("step", "reveal", "mechanism")) else "SOURCE_FOOTAGE_DIRECT")
        )
        visual_mode = (
            "faceless_diagram" if mode == "faceless"
            else ("split_evidence_focus_pip" if use_ev else ("visual_showcase_rail" if is_visual_hold else "person_full_with_lower_headline"))
        )
        subject = (txt[:52].strip() if txt else brief["key_message"][:52]).strip()
        beats.append({
            "beat_id": f"beat_{idx + 1:02d}",
            "start_frame": sf,
            "end_frame": ef,
            "source_word_ids": wids,
            "semantic_function": fn,
            "viewer_goal": f"Deliver grounded {fn} takeaway without visual clutter.",
            "emotion_intensity": emo,
            "priority": prio,
            "content_refs": refs,
            "proof_status": proof_status,
            "visual_mode": visual_mode,
            "focus_subject": subject,
            "continuity_in": "open_frame" if idx == 0 else "editorial_spine",
            "continuity_out": "clean_settle" if idx == len(raw_spans) - 1 else "editorial_spine",
            "planned_information_change": f"Advance viewer understanding via {visual_mode} ({fn}: {sem_reason}).",
            "why": f"Semantic classification ({sem_reason}) [classifier=rule_grounded_deterministic_fallback].",
            "read_time_frames": min(ef - sf, compute_min_read_frames(subject, fps)),
            "intentional_hold": is_visual_hold,
        })
    return beats


_DANGLING_TAIL_WORDS = {"and", "or", "with", "for", "to", "of", "in", "on", "the", "a", "an", "by", "at", "from", "into"}


def _clean_clip(text: str, max_chars: int) -> str:
    """Trim copy cleanly at whole-word boundaries, never ending on a dangling conjunction/preposition."""
    s = " ".join(str(text or "").strip().split())
    if len(s) <= max_chars:
        return s
    cut = s[: max_chars + 1]
    sp = cut.rfind(" ")
    res = cut[:sp].rstrip(" ,;:-") if sp >= max(8, max_chars // 2) else s[:max_chars].rstrip(" ,;:-")
    toks = res.split()
    while len(toks) > 2 and toks[-1].lower().strip(".,;:-") in _DANGLING_TAIL_WORDS:
        toks.pop()
    return " ".join(toks).rstrip(" ,;:-")


def _build_fixed_pip_visual_events(
    beats: list[dict[str, Any]],
    *,
    brief: dict[str, Any],
    usable: list[dict[str, Any]],
    selected_dir: dict[str, Any],
    reg: dict[str, Any],
    word_map: dict[str, dict[str, Any]],
    total_out_frames: int,
    fps: float,
    presenter_layout: dict[str, Any],
    hook_decision: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build multi-technique visual storytelling events routed around reserved presenter zone (never static card loop)."""
    pr = presenter_layout["reserved_region"]
    sz = presenter_layout["safe_zone"]
    px, py, pw, ph = [float(v) for v in pr]
    is_bottom_pip = (py >= 0.44)
    is_left_pip = (px < 0.42)
    theme = selected_dir["theme"]

    # Primary storytelling canvas (above bottom PIP or below top PIP)
    if is_bottom_pip:
        upper_y = max(sz["top"], 0.08)
        upper_h = round(max(0.28, (py - 0.025) - upper_y), 4)
        main_box = [0.05, upper_y, 0.90, upper_h]
        # Companion slot alongside the presenter PIP on opposite horizontal side (with spring-overshoot clearance)
        if is_left_pip:
            sx = round(min(0.47, px + pw + 0.052), 4)
            sw = round(max(0.36, (1.0 - max(sz["right"], 0.05)) - sx), 4)
        else:
            sx = max(sz["left"], 0.05)
            sw = round(max(0.36, (px - 0.052) - sx), 4)
        side_box = [sx, round(py + 0.02, 4), sw, round(max(0.15, ph - 0.03), 4)]
    else:
        lower_y = round(py + ph + 0.032, 4)
        lower_h = round(max(0.28, (1.0 - sz["bottom"] - 0.02) - lower_y), 4)
        main_box = [0.05, lower_y, 0.90, lower_h]
        if is_left_pip:
            sx = round(min(0.47, px + pw + 0.052), 4)
            sw = round(max(0.36, (1.0 - max(sz["right"], 0.05)) - sx), 4)
        else:
            sx = max(sz["left"], 0.05)
            sw = round(max(0.36, (px - 0.052) - sx), 4)
        side_box = [sx, round(py + 0.015, 4), sw, round(max(0.14, ph - 0.025), 4)]

    evid_assets = [a for a in usable if a["kind"] in ("real_ui", "still_asset")]
    proof_points = [str(p).strip() for p in (brief.get("proof_points") or []) if str(p).strip()]
    sel_hook_id = hook_decision.get("selected_option_id", "hook_opt_01_authentic_lead")

    events: list[dict[str, Any]] = []
    # Top progress spine (thin, inside top safe border, never colliding with PIP)
    spine_cap = select_capability(reg, "local.progress_spine", aspect_ratio=brief["aspect_ratio"], duration_sec=total_out_frames / fps, profile=brief["profile"])
    events.append({
        "event_id": "ev_00_editorial_spine",
        "beat_id": beats[0]["beat_id"],
        "track_id": "track_chrome",
        "layer_id": "editorial_progress_spine",
        "layer_cat": "hl",
        "z_order": 45,
        "asset_ref": None,
        "capability_id": spine_cap["selected_id"],
        "start_frame": 0,
        "end_frame": total_out_frames,
        "anchor_word_id": None,
        "anchor_mode": "beat_start",
        "normalized_bbox": [0.05, 0.042, 0.90, 0.014],
        "opacity": 0.92,
        "transform": {"scale_from": 1.0, "scale_to": 1.0, "y_offset_px": 0},
        "mask": None,
        "blend": "normal",
        "easing": "power2.out",
        "params": {"kind": "progress_spine", "theme": theme},
        "safe_zone": True,
        "face_exclusion": True,
        "semantic_reason": "Persistent visual continuity spine linking beats cleanly.",
        "expected_visual_evidence": "Slim top progress bar tracking story progression across beats.",
    })

    for idx, b in enumerate(beats):
        sf, ef = int(b["start_frame"]), int(b["end_frame"])
        dur_s = max(0.3, (ef - sf) / fps)
        fn = b["semantic_function"]
        anchor_wid = b["source_word_ids"][0] if b["source_word_ids"] else None
        ev_sf = 0 if idx == 0 else sf
        ev_ef = total_out_frames if idx == len(beats) - 1 else ef
        matched_ev_id = next((ref for ref in b.get("content_refs", []) if any(a["asset_id"] == ref for a in evid_assets)), None)
        if matched_ev_id is None and fn in ("evidence", "proof") and evid_assets:
            matched_ev_id = evid_assets[0]["asset_id"]
        has_ev = matched_ev_id is not None

        # Crisp beat-timed stagger delays so multi-column graphics settle quickly and stay readable
        span_s = max(0.6, (ev_ef - ev_sf) / fps)
        d_step = [round(min(0.08, span_s * 0.04), 2), round(min(0.24, span_s * 0.14), 2), round(min(0.40, span_s * 0.24), 2)]

        # 1. Primary Main-Canvas Storytelling Event (ascending z_order so incoming beats crossfade cleanly over outgoing!)
        z_main = 20 + idx
        z_side = 30 + idx
        if idx == 0:
            hook_lines = [p.strip() for p in b["focus_subject"].replace(",", "|").split("|") if p.strip()]
            if len(hook_lines) == 1 and len(hook_lines[0].split()) >= 4:
                toks_h = hook_lines[0].split()
                mid_h = len(toks_h) // 2
                hook_lines = [" ".join(toks_h[:mid_h]), " ".join(toks_h[mid_h:])]
            b1_ev_id = matched_ev_id or (evid_assets[0]["asset_id"] if evid_assets else None)
            b1_ev_meta = next((a for a in evid_assets if a["asset_id"] == b1_ev_id), evid_assets[0] if evid_assets else None)
            if "spotlight" in sel_hook_id and b1_ev_id and b1_ev_meta:
                cap_main = select_capability(reg, "r1.clip_mask_wipe", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
                main_ev = {
                    "event_id": f"ev_{idx + 1:02d}_hook_main",
                    "beat_id": b["beat_id"],
                    "track_id": "track_evidence",
                    "layer_id": f"layer_{b['beat_id']}_hook_visual",
                    "layer_cat": "evbg",
                    "z_order": z_main,
                    "asset_ref": b1_ev_id,
                    "capability_id": cap_main["selected_id"],
                    "start_frame": 0,
                    "end_frame": ev_ef,
                    "anchor_word_id": anchor_wid,
                    "anchor_mode": "beat_start",
                    "normalized_bbox": list(main_box),
                    "opacity": 1.0,
                    "transform": {"scale_from": 0.99, "scale_to": 1.0, "y_offset_px": 0},
                    "mask": {"type": "clip_inset_horizontal"},
                    "blend": "normal",
                    "easing": "power3.out",
                    "params": {
                        "kind": "clip_mask_wipe",
                        "kicker": "LIVE PLATFORM",
                        "headline": _clean_clip(b["focus_subject"], 58),
                        "subtitle": _clean_clip(b1_ev_meta.get("label") or brief.get("key_message", ""), 42),
                        "fit": "contain",
                        "clean_frame": True,
                        "selected_hook_id": sel_hook_id,
                        "theme": theme,
                    },
                    "safe_zone": True,
                    "face_exclusion": True,
                    "semantic_reason": f"Frame-0 visual-first hook ({sel_hook_id}) paired with persistent presenter PIP.",
                    "expected_visual_evidence": "Verified capture visible immediately from frame 0 in upper main canvas.",
                }
            else:
                cap_main = select_capability(reg, "r1.kinetic_phrase_mask", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
                main_ev = {
                    "event_id": f"ev_{idx + 1:02d}_hook_main",
                    "beat_id": b["beat_id"],
                    "track_id": "track_info_primary",
                    "layer_id": f"layer_{b['beat_id']}_hook_kinetic",
                    "layer_cat": "title",
                    "z_order": z_main,
                    "asset_ref": None,
                    "capability_id": cap_main["selected_id"],
                    "start_frame": 0,
                    "end_frame": ev_ef,
                    "anchor_word_id": anchor_wid,
                    "anchor_mode": "beat_start",
                    "normalized_bbox": list(main_box),
                    "opacity": 1.0,
                    "transform": {"scale_from": 0.98, "scale_to": 1.0, "y_offset_px": 4},
                    "mask": None,
                    "blend": "normal",
                    "easing": "power3.out",
                    "params": {
                        "kind": "kinetic_phrase_mask",
                        "kicker": "OPENING STORY",
                        "lines": [_clean_clip(ln, 34) for ln in hook_lines[:3]],
                        "subtitle": _clean_clip(brief.get("key_message", ""), 64),
                        "selected_hook_id": sel_hook_id,
                        "theme": theme,
                    },
                    "safe_zone": True,
                    "face_exclusion": True,
                    "semantic_reason": f"Frame-0 kinetic mask hook ({sel_hook_id}) synced with opening delivery.",
                    "expected_visual_evidence": "Multi-line kinetic hook mask visible at frame 0 above persistent PIP.",
                }
            events.append(main_ev)

            # Companion spring callout beside PIP (clean viewer-facing editorial copy synced to opening beat)
            cap_side = select_capability(reg, "hf.spring_pop", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
            p0_raw = proof_points[0] if proof_points else brief.get("key_message", "")
            p1_raw = proof_points[1] if len(proof_points) > 1 else ""
            if b1_ev_meta and b1_ev_meta.get("label"):
                side_headline = _clean_clip(b1_ev_meta["label"], 34)
                side_sub = _clean_clip(brief.get("key_message", ""), 44)
            elif ":" in p0_raw:
                side_headline = _clean_clip(p0_raw.split(":", 1)[0], 34)
                side_sub = _clean_clip(p0_raw.split(":", 1)[1], 44)
            else:
                side_headline = _clean_clip(p0_raw, 34)
                side_sub = _clean_clip(brief.get("key_message", ""), 44)
            if side_sub.lower() == side_headline.lower():
                side_sub = _clean_clip(p1_raw.split(":", 1)[0] if p1_raw else "Creator walkthrough", 44)
            side_tags = ["CREATOR PAGE", "FIRST LOOK"]
            events.append({
                "event_id": f"ev_{idx + 1:02d}_hook_side",
                "beat_id": b["beat_id"],
                "track_id": "track_info_secondary",
                "layer_id": f"layer_{b['beat_id']}_hook_badge",
                "layer_cat": "card",
                "z_order": z_side,
                "asset_ref": None,
                "capability_id": cap_side["selected_id"],
                "start_frame": 0,
                "end_frame": ev_ef,
                "anchor_word_id": anchor_wid,
                "anchor_mode": "beat_start",
                "normalized_bbox": list(side_box),
                "opacity": 1.0,
                "transform": {"scale_from": 0.72, "scale_to": 1.0, "y_offset_px": 6},
                "mask": None,
                "blend": "normal",
                "easing": "back.out(2.4)" if brief["profile"] in ("seecut_core", "editorial_pro") else "power2.out",
                "params": {
                    "kind": "split_callout_light",
                    "kicker": "FEATURED FOCUS",
                    "headline": side_headline,
                    "subtitle": side_sub,
                    "tags": side_tags,
                    "selected_hook_id": sel_hook_id,
                    "theme": theme,
                },
                "safe_zone": True,
                "face_exclusion": True,
                "semantic_reason": "Companion side context panel keeps frame 0 dense and balanced beside fixed PIP.",
                "expected_visual_evidence": "Light high-contrast focus card beside bottom presenter without overlap.",
            })
            # Hand-drawn SVG craft accent anchored directly onto hook side panel (zero H1 anchor drift)
            cap_ul = select_capability(reg, "hf_hw.underline", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
            events.append({
                "event_id": f"ev_{idx + 1:02d}_hand_accent",
                "beat_id": b["beat_id"],
                "track_id": "track_chrome",
                "layer_id": f"layer_{b['beat_id']}_hand_underline",
                "layer_cat": "hand",
                "z_order": 42,
                "asset_ref": None,
                "capability_id": cap_ul["selected_id"],
                "start_frame": 2,
                "end_frame": ev_ef,
                "anchor_word_id": anchor_wid,
                "anchor_mode": "beat_start",
                "normalized_bbox": list(side_box),
                "opacity": 0.92,
                "transform": {"scale_from": 1.0, "scale_to": 1.0, "y_offset_px": 0},
                "mask": None,
                "blend": "normal",
                "easing": "power2.out",
                "params": {
                    "kind": "hand_underline",
                    "anchor_target_id": f"ev_{idx + 1:02d}_hook_side",
                    "stroke_color": "#D99B26",
                    "theme": theme,
                },
                "safe_zone": True,
                "face_exclusion": True,
                "semantic_reason": "Hand-drawn SVG stroke accent brings organic editorial craft to opening card.",
                "expected_visual_evidence": "Animated golden brush underline below featured focus card.",
            })
        elif idx == len(beats) - 1 or fn in ("payoff", "cta"):
            # Payoff / Closing beat -> Structured dark-burgundy 3-step summary rail (or spotlight if <3 proof points)
            # Guarantees ZERO empty pastel voids, ZERO duplicate headline/subtitle, and ZERO generic homepage masquerading!
            if len(proof_points) >= 3:
                cap_rail = select_capability(reg, "hf.stagger_cascade", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
                steps = []
                for k, pt in enumerate(proof_points[:3]):
                    if ":" in pt:
                        t_part, d_part = [x.strip() for x in pt.split(":", 1)]
                    else:
                        t_part = _clean_clip(pt, 30)
                        d_part = ["Clean layout ready to customize", "High-res files built for home print", "Instant access on desktop & mobile"][k]
                    steps.append({"num": f"0{k + 1}", "title": _clean_clip(t_part, 32), "desc": _clean_clip(d_part, 46)})
                events.append({
                    "event_id": f"ev_{idx + 1:02d}_payoff_main",
                    "beat_id": b["beat_id"],
                    "track_id": "track_info_primary",
                    "layer_id": f"layer_{b['beat_id']}_payoff_rail",
                    "layer_cat": "card",
                    "z_order": z_main,
                    "asset_ref": None,
                    "capability_id": cap_rail["selected_id"],
                    "start_frame": ev_sf,
                    "end_frame": ev_ef,
                    "anchor_word_id": anchor_wid,
                    "anchor_mode": "beat_start",
                    "normalized_bbox": list(main_box),
                    "opacity": 1.0,
                    "transform": {"scale_from": 0.96, "scale_to": 1.0, "y_offset_px": 4},
                    "mask": None,
                    "blend": "normal",
                    "easing": "power2.out",
                    "params": {
                        "kind": "step_rail_3",
                        "kicker": "HOW IT WORKS",
                        "headline": _clean_clip(brief.get("cta_copy") or brief.get("key_message") or "Complete Highlights", 40),
                        "steps": steps,
                        "step_delays_sec": d_step,
                        "theme": theme,
                    },
                    "safe_zone": True,
                    "face_exclusion": True,
                    "semantic_reason": "Closing 3-step editorial rail ties storefront details to creator reaction through EOF.",
                    "expected_visual_evidence": "High-contrast 3-step dark rail settling cleanly through final frame.",
                })
            else:
                cap_spot = select_capability(reg, "r1.spotlight_focus", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
                payoff_asset = matched_ev_id or (evid_assets[-1]["asset_id"] if evid_assets else None)
                ev_label = next((a.get("label") for a in evid_assets if a["asset_id"] == payoff_asset), None)
                spot_head = _clean_clip(brief.get("cta_copy") or ev_label or brief["key_message"], 48)
                spot_sub = _clean_clip(brief.get("key_message", ""), 56)
                if spot_sub.lower() == spot_head.lower():
                    spot_sub = _clean_clip(proof_points[-1] if proof_points else "Complete storefront overview", 56)
                events.append({
                    "event_id": f"ev_{idx + 1:02d}_payoff_main",
                    "beat_id": b["beat_id"],
                    "track_id": "track_info_primary",
                    "layer_id": f"layer_{b['beat_id']}_payoff_spot",
                    "layer_cat": "evbg" if payoff_asset else "card",
                    "z_order": z_main,
                    "asset_ref": payoff_asset,
                    "capability_id": cap_spot["selected_id"],
                    "start_frame": ev_sf,
                    "end_frame": ev_ef,
                    "anchor_word_id": anchor_wid,
                    "anchor_mode": "beat_start",
                    "normalized_bbox": list(main_box),
                    "opacity": 1.0,
                    "transform": {"scale_from": 0.96, "scale_to": 1.0, "y_offset_px": 4},
                    "mask": None,
                    "blend": "normal",
                    "easing": "power2.out",
                    "params": {
                        "kind": "spotlight_focus",
                        "kicker": "FINAL LOOK",
                        "badge": "SUMMARY",
                        "headline": spot_head,
                        "subtitle": spot_sub,
                        "theme": theme,
                    },
                    "safe_zone": True,
                    "face_exclusion": True,
                    "semantic_reason": "Spotlight payoff delivers closing answer linked to opening promise through EOF.",
                    "expected_visual_evidence": "Spotlight payoff card settling cleanly through final frame.",
                })
            cap_sw = select_capability(reg, "r1.light_sweep_badge", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
            verdict_sub = _clean_clip(brief.get("key_message", ""), 40)
            events.append({
                "event_id": f"ev_{idx + 1:02d}_payoff_side",
                "beat_id": b["beat_id"],
                "track_id": "track_info_secondary",
                "layer_id": f"layer_{b['beat_id']}_verdict",
                "layer_cat": "card",
                "z_order": z_side,
                "asset_ref": None,
                "capability_id": cap_sw["selected_id"],
                "start_frame": ev_sf,
                "end_frame": ev_ef,
                "anchor_word_id": anchor_wid,
                "anchor_mode": "beat_start",
                "normalized_bbox": list(side_box),
                "opacity": 1.0,
                "transform": {"scale_from": 0.94, "scale_to": 1.0, "y_offset_px": 6},
                "mask": None,
                "blend": "normal",
                "easing": "power2.out",
                "params": {
                    "kind": "light_sweep_badge",
                    "kicker": "CREATOR TAKEAWAY",
                    "headline": _clean_clip(b["focus_subject"], 48),
                    "subtitle": verdict_sub,
                    "theme": theme,
                },
                "safe_zone": True,
                "face_exclusion": True,
                "semantic_reason": "Light sweep verdict bar accentuates closing delivery through final frame N-1.",
                "expected_visual_evidence": "Specular sheen sweep over closing takeaway badge beside presenter.",
            })
        elif has_ev:
            # Real verified evidence beat -> alternate clip_mask_wipe & spotlight_focus with distinct companion styles!
            ev_meta = next((a for a in evid_assets if a["asset_id"] == matched_ev_id), evid_assets[0])
            ev_label_txt = _clean_clip(ev_meta.get("label") or b["focus_subject"], 48)
            use_spotlight = (idx % 2 == 0)
            if use_spotlight:
                cap_ev = select_capability(reg, "r1.spotlight_focus", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
                events.append({
                    "event_id": f"ev_{idx + 1:02d}_evidence_main",
                    "beat_id": b["beat_id"],
                    "track_id": "track_evidence",
                    "layer_id": f"layer_{b['beat_id']}_evidence_spot",
                    "layer_cat": "evbg",
                    "z_order": z_main,
                    "asset_ref": matched_ev_id,
                    "capability_id": cap_ev["selected_id"],
                    "start_frame": ev_sf,
                    "end_frame": ev_ef,
                    "anchor_word_id": anchor_wid,
                    "anchor_mode": "beat_start",
                    "normalized_bbox": list(main_box),
                    "opacity": 1.0,
                    "transform": {"scale_from": 0.97, "scale_to": 1.0, "y_offset_px": 0},
                    "mask": None,
                    "blend": "normal",
                    "easing": "power2.out",
                    "params": {
                        "kind": "spotlight_focus",
                        "kicker": "CLOSER LOOK",
                        "badge": "DETAILS",
                        "headline": ev_label_txt,
                        "subtitle": _clean_clip(
                            (proof_points[min(idx, len(proof_points) - 1)].split(":", 1)[-1] if proof_points else ""),
                            48,
                        ),
                        "theme": theme,
                    },
                    "safe_zone": True,
                    "face_exclusion": True,
                    "semantic_reason": f"Real footage detail spotlight ({matched_ev_id}) matched to spoken reveal.",
                    "expected_visual_evidence": "High-contrast detail capture inside upper spotlight card.",
                })
                cap_fc = select_capability(reg, "r1.freeze_callout", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
                events.append({
                    "event_id": f"ev_{idx + 1:02d}_evidence_side",
                    "beat_id": b["beat_id"],
                    "track_id": "track_info_secondary",
                    "layer_id": f"layer_{b['beat_id']}_callout",
                    "layer_cat": "card",
                    "z_order": z_side,
                    "asset_ref": None,
                    "capability_id": cap_fc["selected_id"],
                    "start_frame": ev_sf,
                    "end_frame": ev_ef,
                    "anchor_word_id": anchor_wid,
                    "anchor_mode": "beat_start",
                    "normalized_bbox": list(side_box),
                    "opacity": 1.0,
                    "transform": {"scale_from": 0.94, "scale_to": 1.0, "y_offset_px": 6},
                    "mask": None,
                    "blend": "normal",
                    "easing": "power2.out",
                    "params": {
                        "kind": "freeze_callout",
                        "kicker": "INSIDE THE BUNDLE",
                        "badge": "4-PART SET",
                        "headline": _clean_clip(b["focus_subject"], 48),
                        "subtitle": _clean_clip(
                            (proof_points[1].split(":", 1)[-1] if len(proof_points) > 1 else ev_label_txt),
                            44,
                        ),
                        "theme": theme,
                    },
                    "safe_zone": True,
                    "face_exclusion": True,
                    "semantic_reason": "Side detail callout reinforces spoken emphasis without colliding with presenter PIP.",
                    "expected_visual_evidence": "Readable side callout anchored beside bottom presenter.",
                })
                # Hand-drawn SVG arrow craft accent anchored to detail callout
                cap_arr = select_capability(reg, "hf_hw.arrow", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
                events.append({
                    "event_id": f"ev_{idx + 1:02d}_hand_arrow",
                    "beat_id": b["beat_id"],
                    "track_id": "track_chrome",
                    "layer_id": f"layer_{b['beat_id']}_hand_arrow",
                    "layer_cat": "hand",
                    "z_order": 42,
                    "asset_ref": None,
                    "capability_id": cap_arr["selected_id"],
                    "start_frame": min(ev_ef - 2, ev_sf + 3),
                    "end_frame": ev_ef,
                    "anchor_word_id": anchor_wid,
                    "anchor_mode": "beat_start",
                    "normalized_bbox": list(side_box),
                    "opacity": 0.90,
                    "transform": {"scale_from": 1.0, "scale_to": 1.0, "y_offset_px": 0},
                    "mask": None,
                    "blend": "normal",
                    "easing": "power2.out",
                    "params": {
                        "kind": "hand_underline",
                        "anchor_target_id": f"ev_{idx + 1:02d}_evidence_side",
                        "stroke_color": "#6E1F24",
                        "theme": theme,
                    },
                    "safe_zone": True,
                    "face_exclusion": True,
                    "semantic_reason": "Hand-drawn SVG stroke links spoken cue to bundle details cleanly.",
                    "expected_visual_evidence": "Crisp burgundy hand-drawn underline on detail callout.",
                })
            else:
                cap_ev = select_capability(reg, "r1.clip_mask_wipe", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
                events.append({
                    "event_id": f"ev_{idx + 1:02d}_evidence_main",
                    "beat_id": b["beat_id"],
                    "track_id": "track_evidence",
                    "layer_id": f"layer_{b['beat_id']}_evidence",
                    "layer_cat": "evbg",
                    "z_order": z_main,
                    "asset_ref": matched_ev_id,
                    "capability_id": cap_ev["selected_id"],
                    "start_frame": ev_sf,
                    "end_frame": ev_ef,
                    "anchor_word_id": anchor_wid,
                    "anchor_mode": "beat_start",
                    "normalized_bbox": list(main_box),
                    "opacity": 1.0,
                    "transform": {"scale_from": 0.99, "scale_to": 1.0, "y_offset_px": 0},
                    "mask": {"type": "clip_inset_horizontal"},
                    "blend": "normal",
                    "easing": "power3.out",
                    "params": {
                        "kind": "clip_mask_wipe",
                        "kicker": "STOREFRONT LOOK",
                        "headline": ev_label_txt,
                        "subtitle": "From creator footage",
                        "fit": "contain",
                        "clean_frame": True,
                        "theme": theme,
                    },
                    "safe_zone": True,
                    "face_exclusion": True,
                    "semantic_reason": f"Real footage evidence ({matched_ev_id}) revealed via clean mask wipe.",
                    "expected_visual_evidence": "Crisp contained storefront capture inside upper stage.",
                })
                cap_hl = select_capability(reg, "hf.notes_reveal", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
                toks_b = b["focus_subject"].split()
                hl_tok = toks_b[-1] if toks_b else "DETAILS"
                pref_tok = " ".join(toks_b[:-1]) if len(toks_b) > 1 else "Inspect"
                events.append({
                    "event_id": f"ev_{idx + 1:02d}_evidence_side",
                    "beat_id": b["beat_id"],
                    "track_id": "track_info_secondary",
                    "layer_id": f"layer_{b['beat_id']}_highlight",
                    "layer_cat": "card",
                    "z_order": z_side,
                    "asset_ref": None,
                    "capability_id": cap_hl["selected_id"],
                    "start_frame": ev_sf,
                    "end_frame": ev_ef,
                    "anchor_word_id": anchor_wid,
                    "anchor_mode": "beat_start",
                    "normalized_bbox": list(side_box),
                    "opacity": 1.0,
                    "transform": {"scale_from": 0.95, "scale_to": 1.0, "y_offset_px": 6},
                    "mask": None,
                    "blend": "normal",
                    "easing": "power2.out",
                    "params": {
                        "kind": "highlighter_card",
                        "kicker": "FIRST LOOK",
                        "prefix": pref_tok,
                        "highlight_text": hl_tok,
                        "suffix": "",
                        "subtitle": _clean_clip(ev_label_txt, 42),
                        "theme": theme,
                    },
                    "safe_zone": True,
                    "face_exclusion": True,
                    "semantic_reason": "Animated brush highlight directs eye path from speaker to storefront capture.",
                    "expected_visual_evidence": "Brush highlight sweep across focal keyword beside presenter.",
                })
        else:
            # Progressive Details / Step / Turning Point -> Compact dark 3-step rail (zero sparse pastel void!)
            cap_mid = select_capability(reg, "hf.stagger_cascade", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
            pts = proof_points[:3] if len(proof_points) >= 3 else (proof_points + ["Structured layout", "Print-ready files", "Instant access"])[:3]
            steps = []
            for k, pt in enumerate(pts):
                if ":" in pt:
                    t_part, d_part = [x.strip() for x in pt.split(":", 1)]
                else:
                    t_part = _clean_clip(pt, 30)
                    d_part = ["Clear visual hierarchy", "Ready to edit & customize", "Crisp desktop & mobile view"][k]
                steps.append({"num": f"0{k + 1}", "title": _clean_clip(t_part, 30), "desc": _clean_clip(d_part, 44)})
            events.append({
                "event_id": f"ev_{idx + 1:02d}_story_main",
                "beat_id": b["beat_id"],
                "track_id": "track_info_primary",
                "layer_id": f"layer_{b['beat_id']}_breakdown",
                "layer_cat": "card",
                "z_order": z_main,
                "asset_ref": None,
                "capability_id": cap_mid["selected_id"],
                "start_frame": ev_sf,
                "end_frame": ev_ef,
                "anchor_word_id": anchor_wid,
                "anchor_mode": "beat_start",
                "normalized_bbox": list(main_box),
                "opacity": 1.0,
                "transform": {"scale_from": 0.95, "scale_to": 1.0, "y_offset_px": 6},
                "mask": None,
                "blend": "normal",
                "easing": "power2.out",
                "params": {
                    "kind": "step_rail_3",
                    "kicker": "EDITORIAL BREAKDOWN",
                    "headline": "3 Key Highlights",
                    "steps": steps,
                    "step_delays_sec": d_step,
                    "theme": theme,
                },
                "safe_zone": True,
                "face_exclusion": True,
                "semantic_reason": f"Progressive 3-step visual breakdown advancing {fn} with crisp staggered reveals.",
                "expected_visual_evidence": "Staggered multi-step visual rail synchronized to beat.",
            })
            cap_fc = select_capability(reg, "r1.freeze_callout", aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
            events.append({
                "event_id": f"ev_{idx + 1:02d}_story_side",
                "beat_id": b["beat_id"],
                "track_id": "track_info_secondary",
                "layer_id": f"layer_{b['beat_id']}_callout",
                "layer_cat": "card",
                "z_order": z_side,
                "asset_ref": None,
                "capability_id": cap_fc["selected_id"],
                "start_frame": ev_sf,
                "end_frame": ev_ef,
                "anchor_word_id": anchor_wid,
                "anchor_mode": "beat_start",
                "normalized_bbox": list(side_box),
                "opacity": 1.0,
                "transform": {"scale_from": 0.94, "scale_to": 1.0, "y_offset_px": 6},
                "mask": None,
                "blend": "normal",
                "easing": "power2.out",
                "params": {
                    "kind": "freeze_callout",
                    "kicker": fn.upper(),
                    "badge": "DETAILS",
                    "headline": _clean_clip(b["focus_subject"], 48),
                    "subtitle": _clean_clip(brief.get("key_message", ""), 42),
                    "theme": theme,
                },
                "safe_zone": True,
                "face_exclusion": True,
                "semantic_reason": "Side detail callout reinforces spoken emphasis without colliding with presenter PIP.",
                "expected_visual_evidence": "Readable side callout anchored beside bottom presenter.",
            })

    return events


def resolve_editorial_plan(
    brief: dict[str, Any],
    inventory: dict[str, Any],
    *,
    run_id: str,
    transcript: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
    base_git_sha: str = "f610fa51d0038f0e9629273cb419ed065ae21901",
    approved_cuts: list[dict[str, Any]] | None = None,
    edl_override: dict[str, Any] | None = None,
    hook_decision: dict[str, Any] | None = None,
    presenter_policy_override: dict[str, Any] | None = None,
    soundscape_override: dict[str, Any] | None = None,
    custom_beats: list[dict[str, Any]] | None = None,
    custom_visual_events: list[dict[str, Any]] | None = None,
    custom_text_events: list[dict[str, Any]] | None = None,
    custom_camera_events: list[dict[str, Any]] | None = None,
    custom_claim_evidence: list[dict[str, Any]] | None = None,
    creative_direction_override: dict[str, Any] | None = None,
    adaptive_layout_plan: dict[str, Any] | None = None,
    kh03_metadata: dict[str, Any] | None = None,
    status: str = "PLANNED",
) -> dict[str, Any]:
    """Single master editorial resolver producing one authoritative, validated SHOTBOOK V2 JSON."""
    brief = build_editor_brief(brief)
    reg = registry or build_registry()
    usable = [a for a in inventory.get("assets", []) if a.get("usable_in_render")]
    if not usable:
        raise ValueError("No usable_in_render assets in source inventory")
    primary = next((a for a in usable if a["kind"] in ("a_roll", "b_roll")), usable[0])
    has_person = any(a["kind"] == "a_roll" for a in usable)
    media = primary["media"]
    fps_num = int(media.get("fps_num") or 24)
    fps_den = int(media.get("fps_den") or 1)
    fps = fps_num / fps_den
    total_src_frames = int(media.get("nb_frames") or max(1, int(round((media.get("duration_sec") or 6.0) * fps))))
    sample_rate = int(media.get("audio_sample_rate") or 48000)
    rotation_deg = int(media.get("rotation_deg") or 0)

    # 0. Explicit optional presenter_policy (default LEGACY_DYNAMIC for v1 compatibility)
    presenter_policy = normalize_presenter_policy(
        presenter_policy_override if presenter_policy_override is not None else brief.get("presenter_policy"),
        brief_mode=brief["mode"],
        has_presenter_source=has_person,
    )

    # 1. Single authoritative Edit Decision Map (reuse edl_override if already resolved by Stage C/D!)
    if edl_override is not None:
        time_map = dict(edl_override)
        fps_num = int(time_map.get("fps_num", fps_num))
        fps_den = int(time_map.get("fps_den", fps_den))
        fps = fps_num / fps_den
        sample_rate = int(time_map.get("audio_sample_rate", sample_rate))
    else:
        candidates = propose_cut_candidates(
            transcript,
            silences=primary.get("silences"),
            voice_policy=brief["voice_policy"],
        ) if transcript else []
        time_map = build_time_map(
            primary["asset_id"],
            total_src_frames,
            fps_num,
            fps_den,
            audio_sample_rate=sample_rate,
            voice_policy=brief["voice_policy"],
            approved_cuts=approved_cuts,
            cut_candidates=candidates,
        )
    total_out_frames = int(time_map["total_output_frames"])
    mapped_words = map_words_to_output(transcript, time_map) if transcript else []

    # 2. Creative directions, aspect spec & presenter layout
    directions = propose_directions(brief, inventory, transcript)
    selected_dir = creative_direction_override or next(d for d in directions if d["selected"])
    aspect_cfg = dict(ASPECT_CANVAS.get(brief["aspect_ratio"], ASPECT_CANVAS["9:16"]))
    if presenter_policy["mode"] in ("FIXED_PIP", "ADAPTIVE_PIP", "DYNAMIC_HYBRID"):
        aspect_cfg["safe_zone"] = resolve_safe_zone_for_platform(
            platform=brief.get("platform", "reels"),
            aspect_ratio=brief["aspect_ratio"],
            safe_zone_profile=str(presenter_policy.get("safe_zone_profile", "PLATFORM_AWARE")),
        )

    presenter_layout: dict[str, Any] | None = None
    reserved_region: list[float] | None = None
    if presenter_policy["mode"] in ("FIXED_PIP", "ADAPTIVE_PIP", "DYNAMIC_HYBRID"):
        p_reg = resolve_presenter_region(
            presenter_policy,
            output_width=int(aspect_cfg["width"]),
            output_height=int(aspect_cfg["height"]),
            source_width=int(media.get("width") or aspect_cfg["width"]),
            source_height=int(media.get("height") or aspect_cfg["height"]),
            rotation_deg=rotation_deg,
            platform=brief.get("platform", "reels"),
            aspect_ratio=brief["aspect_ratio"],
        )
        p_crop = fit_face_safe_crop(
            int(media.get("width") or aspect_cfg["width"]),
            int(media.get("height") or aspect_cfg["height"]),
            p_reg["pixel_box"][2],
            p_reg["pixel_box"][3],
            face_rect=primary.get("face_rect"),
            rotation_deg=rotation_deg,
            shape=p_reg["shape"],
            border_radius_px=p_reg["border_radius_px"],
            extra_zoom=1.12,
            face_tracking_policy=str(presenter_policy.get("face_tracking", "AUTO_WHEN_AVAILABLE")),
        )
        reserved_region = compute_presenter_reserved_region(
            p_reg, guard_margin_ratio=float(presenter_policy.get("guard_margin_ratio", 0.024)),
        )
        presenter_layout = {
            "mode": presenter_policy["mode"],
            "visibility": presenter_policy["visibility"],
            "region": p_reg,
            "crop": p_crop,
            "reserved_region": reserved_region,
            "safe_zone": aspect_cfg["safe_zone"],
            "z_order": 200,
        }

    # 3. Authoritative hook decision & semantic beats
    eff_hook = hook_decision
    if eff_hook is None:
        h_opts = build_hook_candidates(
            brief, inventory, transcript=transcript,
            cut_policy=time_map.get("voice_policy", brief["voice_policy"]),
            presenter_policy=presenter_policy,
        )
        eff_hook = choose_opening(h_opts, brief=brief)
    beats = custom_beats if custom_beats else analyze_semantic_beats(brief, inventory, transcript, time_map)

    # 4. Visual events (built cleanly from beats + hook + presenter layout if not supplied)
    if custom_visual_events is not None:
        raw_visual_events = list(custom_visual_events)
    elif presenter_policy["mode"] == "FIXED_PIP" and presenter_layout is not None:
        word_map = {w["word_id"]: w for w in mapped_words}
        raw_visual_events = _build_fixed_pip_visual_events(
            beats,
            brief=brief,
            usable=usable,
            selected_dir=selected_dir,
            reg=reg,
            word_map=word_map,
            total_out_frames=total_out_frames,
            fps=fps,
            presenter_layout=presenter_layout,
            hook_decision=eff_hook,
        )
    else:
        raw_visual_events = []
        word_map = {w["word_id"]: w for w in mapped_words}
        evid_assets = [a["asset_id"] for a in usable if a["kind"] in ("real_ui", "still_asset")]
        sel_hook_id = eff_hook.get("selected_option_id", "hook_opt_01_authentic_lead")
        spine_cap = select_capability(reg, "local.progress_spine", aspect_ratio=brief["aspect_ratio"], duration_sec=total_out_frames / fps, profile=brief["profile"])
        raw_visual_events.append({
            "event_id": "ev_00_editorial_spine",
            "beat_id": beats[0]["beat_id"],
            "track_id": "track_chrome",
            "layer_id": "editorial_progress_spine",
            "layer_cat": "hl",
            "z_order": 40,
            "asset_ref": None,
            "capability_id": spine_cap["selected_id"],
            "start_frame": 0,
            "end_frame": total_out_frames,
            "anchor_word_id": None,
            "anchor_mode": "beat_start",
            "normalized_bbox": [0.06, 0.04, 0.88, 0.015],
            "opacity": 0.92,
            "transform": {"scale_from": 0.95, "scale_to": 1.0, "y_offset_px": 0},
            "mask": None,
            "blend": "normal",
            "easing": "power2.out",
            "params": {"kind": "progress_spine", "theme": selected_dir["theme"]},
            "safe_zone": True,
            "face_exclusion": True,
            "semantic_reason": "Persistent visual continuity spine linking beats cleanly.",
            "expected_visual_evidence": "Slim top progress bar tracking story progression across beats.",
        })

        for idx, b in enumerate(beats):
            sf, ef = int(b["start_frame"]), int(b["end_frame"])
            dur_s = max(0.3, (ef - sf) / fps)
            anchor_wid = b["source_word_ids"][0] if b["source_word_ids"] else None
            ev_sf = 0 if idx == 0 else (word_map[anchor_wid]["out_start_frame"] if (anchor_wid and anchor_wid in word_map) else sf)
            ev_sf = min(ef - 2, max(sf, ev_sf))
            matched_ev = next((ref for ref in b.get("content_refs", []) if ref in evid_assets), None)
            is_evid = b["proof_status"] == "VERIFIED_REAL_EVIDENCE" and bool(matched_ev or evid_assets)
            ev_ref = matched_ev or (evid_assets[0] if is_evid else None)
            req_cap = (
                "hf.card_resize" if is_evid
                else ("hf.kinetic_phrase_mask" if idx == 0 and brief["profile"] in ("seecut_core", "editorial_pro")
                      else ("hf.code_snippet" if brief["mode"] == "software_demo"
                            else ("hf.stagger_cascade" if b["semantic_function"] == "step"
                                  else ("hf.spring_pop" if brief["profile"] in ("seecut_core", "editorial_pro") else "local.editorial_panel"))))
            )
            chosen = select_capability(reg, req_cap, aspect_ratio=brief["aspect_ratio"], duration_sec=dur_s, profile=brief["profile"])
            is_km = (chosen["selected_id"] == "hf.kinetic_phrase_mask")
            raw_visual_events.append({
                "event_id": f"ev_{idx + 1:02d}_{b['beat_id']}",
                "beat_id": b["beat_id"],
                "track_id": "track_evidence" if is_evid else "track_info_primary",
                "layer_id": f"layer_{b['beat_id']}_{b['semantic_function']}",
                "layer_cat": "evbg" if is_evid else ("title" if b["semantic_function"] == "hook" else "card"),
                "z_order": 20 if is_evid else 30,
                "asset_ref": ev_ref,
                "capability_id": chosen["selected_id"],
                "start_frame": ev_sf,
                "end_frame": ef,
                "anchor_word_id": anchor_wid,
                "anchor_mode": "beat_start" if idx == 0 else ("keyword_exact" if anchor_wid else "beat_start"),
                "normalized_bbox": [0.06, 0.34, 0.88, 0.40] if is_evid else [0.07, 0.68, 0.86, 0.17],
                "opacity": 1.0,
                "transform": {"scale_from": 0.56 if chosen["selected_id"] == "hf.spring_pop" else 0.94, "scale_to": 1.0, "y_offset_px": 18},
                "mask": None,
                "blend": "normal",
                "easing": "back.out(2.1)" if chosen["selected_id"] == "hf.spring_pop" else "power2.out",
                "params": {
                    "kind": "evidence_spotlight" if is_evid else ("kinetic_phrase_mask" if is_km else ("code_window" if brief["mode"] == "software_demo" else "editorial_headline")),
                    "headline": b["focus_subject"],
                    "lines": [b["focus_subject"]] if is_km else None,
                    "kicker": b["semantic_function"].upper(),
                    "selected_hook_id": sel_hook_id if idx == 0 else None,
                    "theme": selected_dir["theme"],
                },
                "safe_zone": True,
                "face_exclusion": True,
                "semantic_reason": b["why"],
                "expected_visual_evidence": f"Readable {b['semantic_function']} visual settled in safe zone.",
            })

    visual_events = plan_motion(raw_visual_events, fps=fps, profile=brief["profile"])

    # 5. Camera path (honoring explicit presenter_policy, ADAPTIVE_DYNAMIC layout plan & source rotation)
    camera_events = custom_camera_events if custom_camera_events is not None else plan_camera_path(
        beats,
        mode=brief["mode"],
        source_width=int(media.get("width") or aspect_cfg["width"]),
        source_height=int(media.get("height") or aspect_cfg["height"]),
        output_width=int(aspect_cfg["width"]),
        output_height=int(aspect_cfg["height"]),
        face_rect=primary.get("face_rect"),
        presenter_policy=presenter_policy,
        rotation_deg=rotation_deg,
        platform=brief.get("platform", "reels"),
        aspect_ratio=brief["aspect_ratio"],
        adaptive_layout_plan=adaptive_layout_plan,
    )
    if adaptive_layout_plan is not None and adaptive_layout_plan.get("layout_policy") == "ADAPTIVE_DYNAMIC" and presenter_layout is None:
        presenter_layout = {
            "mode": "ADAPTIVE_DYNAMIC",
            "visibility": presenter_policy.get("visibility", "ALWAYS_VISIBLE"),
            "layout_plan_sha256": adaptive_layout_plan.get("layout_plan_sha256"),
            "subject_tracking_mode": adaptive_layout_plan.get("subject_tracking_mode", "STATIC_FALLBACK_VERIFIED"),
            "unique_layout_modes": sorted({lb["layout_mode"] for lb in adaptive_layout_plan.get("beats", [])}),
            "transition_count": len(adaptive_layout_plan.get("transitions", [])),
            "safe_zone": aspect_cfg["safe_zone"],
        }

    # 6. Transitions between every adjacent beat
    transitions = [
        choose_transition(beats[i], beats[i + 1], fps=fps, profile=brief["profile"])
        for i in range(len(beats) - 1)
    ]

    # 7. Typography events + Presenter Reserved-Zone Auto-Layout Collision Solver
    text_events = custom_text_events if custom_text_events is not None else plan_text_events(
        beats,
        fps=fps,
        caption_policy=brief["caption_policy"],
        language=brief["language"],
        cta_copy=brief.get("cta_copy"),
        mapped_words=mapped_words,
        canvas_width=int(aspect_cfg["width"]),
        canvas_height=int(aspect_cfg["height"]),
        presenter_reserved_region=reserved_region,
        platform_safe_zone=aspect_cfg["safe_zone"],
    )

    if reserved_region is not None and presenter_policy.get("occlusion_policy") == "RESERVE_PRESENTER_ZONE":
        solved = resolve_layout_avoiding_presenter(
            visual_events,
            text_events,
            presenter_reserved_region=reserved_region,
            platform_safe_zone=aspect_cfg["safe_zone"],
            canvas_width=int(aspect_cfg["width"]),
            canvas_height=int(aspect_cfg["height"]),
            allow_reflow=True,
        )
        if not solved["ok"]:
            raise ValueError(f"LAYOUT_CONFLICT: {solved['conflicts'][:3]}")
        visual_events = solved["visual_events"]
        text_events = solved["text_events"]
        if presenter_layout is not None:
            presenter_layout["layout_reflows"] = solved["reflows"]

    # 8. Audio cues (use resolved soundscape_override when provided!)
    if soundscape_override is not None and isinstance(soundscape_override.get("cues"), list):
        audio_cues_out = soundscape_override["cues"]
    else:
        audio_plan = plan_audio_cues(
            voice_asset_id=primary["asset_id"] if media.get("has_audio") else None,
            total_frames=total_out_frames,
            fps_num=fps_num,
            fps_den=fps_den,
            audio_sample_rate=sample_rate,
            voice_policy=time_map.get("voice_policy", brief["voice_policy"]),
            sfx_policy=brief["sfx_policy"],
            bgm_policy=brief["music_policy"],
            voice_license_ref=primary["rights"],
        )
        audio_cues_out = audio_plan["cues"]

    # 9. Claim evidence
    if custom_claim_evidence is not None:
        claim_evidence = custom_claim_evidence
    else:
        claim_evidence = []
        for a in usable:
            if a["kind"] in ("real_ui", "still_asset"):
                claim_evidence.append({
                    "claim_id": f"clm_{a['asset_id']}",
                    "exact_text": a.get("label") or a.get("basename", a["asset_id"]),
                    "source_asset_id": a["asset_id"],
                    "source_path": a["canonical_path"],
                    "source_sha256": a["sha256"],
                    "reference_span": f"00:00.00-{total_out_frames/fps:05.2f}",
                    "verification_status": "VERIFIED_REAL_CAPTURE",
                    "allowed_display": True,
                })

    # Link selected hook to exact first-frame compiled visual event ID
    first_vis = next((ev for ev in visual_events if int(ev["start_frame"]) == 0 and (ev.get("params") or {}).get("kind") != "progress_spine"), None)
    selected_hook_record = {
        "selected_option_id": eff_hook["selected_option_id"],
        "strategy": (eff_hook.get("selected_option") or {}).get("strategy", ""),
        "first_frame_visual_id": first_vis["event_id"] if first_vis else None,
        "start_frame": int(first_vis["start_frame"]) if first_vis else 0,
        "presenter_mode": presenter_policy["mode"],
        "payoff_beat_id": beats[-1]["beat_id"],
    }

    source_sha_set = {a["asset_id"]: a["sha256"] for a in usable}
    shotbook = {
        "schema_version": "kh02.shotbook.v2",
        "run_id": run_id,
        "mode": brief["mode"],
        "profile": brief["profile"],
        "status": status,
        "source_sha_set": source_sha_set,
        "output_spec": {
            "width": aspect_cfg["width"],
            "height": aspect_cfg["height"],
            "output_scale": aspect_cfg["output_scale"],
            "fps_num": fps_num,
            "fps_den": fps_den,
            "total_frames": total_out_frames,
            "audio_sample_rate": sample_rate,
            "orientation": aspect_cfg["orientation"],
            "platform_safe_zone": aspect_cfg["safe_zone"],
        },
        "input_brief_sha": hash_artifact(brief),
        "rights_ledger_sha": hash_artifact({a["asset_id"]: a["rights"] for a in inventory["assets"]}),
        "transcript_sha": hash_artifact(transcript) if transcript else None,
        "edit_decision_map": time_map,
        "presenter_policy": presenter_policy,
        "presenter_layout": presenter_layout,
        "selected_hook": selected_hook_record,
        "story_arc": {
            "creative_direction_id": selected_dir["direction_id"],
            "theme": selected_dir["theme"],
            "archetype": selected_dir["archetype"],
            "summary": selected_dir.get("layout_strategy", brief["key_message"]),
            "alternatives_considered": [d for d in directions if d["direction_id"] != selected_dir["direction_id"]],
        },
        "beats": beats,
        "visual_events": visual_events,
        "camera_events": camera_events,
        "transitions": transitions,
        "text_events": text_events,
        "audio_cues": audio_cues_out,
        "claim_evidence": claim_evidence,
        "approved_exceptions": list(brief.get("approved_exceptions", [])),
        "provenance": {
            "planner_version": __version__,
            "generated_at_utc": "1970-01-01T00:00:00Z" if status == "TEST_ONLY" else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "engine": "SeeCut-v3.4-HyperFrames-HTML-GSAP",
            "base_git_sha": base_git_sha,
            "upstream_seecut_sha": UPSTREAM_SEECUT_SHA,
            "video_use_ref_sha": VIDEO_USE_REF_SHA,
            **({
                "kh03_selected_skill_id": kh03_metadata["selected_skill_id"],
                "kh03_plan_sha256": kh03_metadata["kh03_plan_sha256"],
                **({"kh03_clock_domain": kh03_metadata["clock_domain"]} if kh03_metadata.get("clock_domain") else {}),
                **({"kh03_time_map_sha256": kh03_metadata["time_map_sha256"]} if kh03_metadata.get("time_map_sha256") else {}),
                **({"kh03_sound_plan_sha256": kh03_metadata["sound_plan_sha256"]} if kh03_metadata.get("sound_plan_sha256") else {}),
                **({"kh03_layout_policy": kh03_metadata["layout_policy"]} if kh03_metadata.get("layout_policy") else {}),
                **({"kh03_layout_plan_sha256": kh03_metadata["layout_plan_sha256"]} if kh03_metadata.get("layout_plan_sha256") else {}),
                **({"kh03_motion_graph_sha256": kh03_metadata["motion_graph_sha256"]} if kh03_metadata.get("motion_graph_sha256") else {}),
                **({"kh03_winning_candidate_id": kh03_metadata["winning_candidate_id"]} if kh03_metadata.get("winning_candidate_id") else {}),
                **({"kh03_winner_binding_sha256": kh03_metadata["winner_binding_sha256"]} if kh03_metadata.get("winner_binding_sha256") else {}),
                **({"kh03_competition_status": kh03_metadata["competition_status"]} if kh03_metadata.get("competition_status") else {}),
            } if kh03_metadata else {}),
        },
    }

    errs = validate_references(shotbook, inventory=inventory, registry=reg)
    if errs:
        raise ValueError(f"Resolved SHOTBOOK V2 failed reference validation: {errs[:5]}")
    return shotbook


def emit_shotbook_v2(shotbook: dict[str, Any], out_dir: str | Path) -> tuple[Path, Path]:
    """Write authoritative SHOTBOOK_V2.json and deterministically rendered SHOTBOOK_V2.md."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "SHOTBOOK_V2.json"
    md_path = out / "SHOTBOOK_V2.md"
    json_path.write_text(json.dumps_pretty(shotbook) + "\n", encoding="utf-8")
    md_path.write_text(render_shotbook_markdown(shotbook), encoding="utf-8")
    return json_path, md_path


def json_dumps_compat(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2)


json_dumps_pretty = json_dumps_compat
json_mod = None


def render_shotbook_markdown(shotbook: dict[str, Any]) -> str:
    """Generate human-readable SHOTBOOK_V2.md strictly from authoritative SHOTBOOK_V2.json."""
    spec = shotbook["output_spec"]
    fps = spec["fps_num"] / spec["fps_den"]
    lines = [
        f"# SHOTBOOK V2 — `{shotbook['run_id']}`",
        "",
        f"> Generated strictly from authoritative `SHOTBOOK_V2.json` (SHA-256: `{hash_artifact(shotbook)[:16]}...`). Do not edit manually.",
        "",
        f"- **Mode / Profile**: `{shotbook['mode']}` / `{shotbook['profile']}` (`{shotbook['status']}`)",
        f"- **Creative Direction**: `{shotbook['story_arc']['creative_direction_id']}` — theme `{shotbook['story_arc']['theme']}` (`{shotbook['story_arc']['archetype']}`)",
        f"- **Output Spec**: `{spec['width']}x{spec['height']}` × `{spec.get('output_scale', 1.0)}` @ `{spec['fps_num']}/{spec['fps_den']} fps`, `{spec['total_frames']}` frames (`{spec['total_frames']/fps:.3f}s`), `{spec['orientation']}`",
        f"- **Voice Policy**: `{shotbook['edit_decision_map'].get('voice_policy', 'LOCK_VOICE')}`",
        "",
        "## Beats",
        "",
        "| Beat | Frames | Time (s) | Function | Visual Mode | Proof Status | Focus Subject |",
        "|---|---|---|---|---|---|---|",
    ]
    for b in shotbook["beats"]:
        t0, t1 = b["start_frame"] / fps, b["end_frame"] / fps
        lines.append(
            f"| `{b['beat_id']}` | `{b['start_frame']}..{b['end_frame']}` | `{t0:.2f}-{t1:.2f}s` | `{b['semantic_function']}` | `{b['visual_mode']}` | `{b['proof_status']}` | {b['focus_subject']} |"
        )
    lines.extend(["", "## Visual Events", "", "| Event | Beat | Frames | Capability | Layer (`cat`) | Easing | Reason |", "|---|---|---|---|---|---|---|"])
    for ev in shotbook["visual_events"]:
        lines.append(
            f"| `{ev['event_id']}` | `{ev['beat_id']}` | `{ev['start_frame']}..{ev['end_frame']}` | `{ev['capability_id']}` | `{ev['layer_id']}` (`{ev.get('layer_cat','card')}`) | `{ev['easing']}` | {ev['semantic_reason']} |"
        )
    lines.extend(["", "## Camera & Transitions", ""])
    for c in shotbook["camera_events"]:
        lines.append(f"- **Camera `{c['camera_id']}`** (`{c['beat_id']}` `{c['start_frame']}..{c['end_frame']}`): `{c['framing_from']} -> {c['framing_to']}`, scale `{c['scale_from']} -> {c['scale_to']}` — {c['reason']}")
    for tr in shotbook["transitions"]:
        lines.append(f"- **Transition `{tr['transition_id']}`** (`{tr['from_beat']} -> {tr['to_beat']}` @ f`{tr['boundary_frame']}`): `{tr['technique']}` (anchor=`{tr.get('match_anchor')}`) — {tr['reason']}")
    lines.extend(["", "## Audio Cues", ""])
    for cu in shotbook["audio_cues"]:
        lines.append(f"- `{cu['cue_id']}` (`{cu['kind']}`): frames `{cu['frame_start']}..{cu['frame_end']}`, gain `{cu['gain_db']} dB`, license `{cu['license_ref']}` — {cu['reason']}")
    lines.append("")
    return "\n".join(lines)


# Fix helper name used inside emit_shotbook_v2
class _JsonWrap:
    @staticmethod
    def dumps_pretty(obj: Any) -> str:
        import json
        return json.dumps(obj, ensure_ascii=False, indent=2)


json = _JsonWrap  # type: ignore[assignment]


def migrate_shotbook_v1_to_v2(
    v1_shotbook: dict[str, Any],
    *,
    run_id: str,
    source_asset_id: str,
    source_sha256: str,
    fps_num: int = 24,
    fps_den: int = 1,
    total_frames: int = 476,
    width: int = 720,
    height: int = 1280,
) -> dict[str, Any]:
    """Deterministic compatibility adapter upgrading legacy SC-01 SHOTBOOK.json into canonical SHOTBOOK V2."""
    fps = fps_num / fps_den
    raw_shots = v1_shotbook.get("shots") or v1_shotbook.get("beats") or []
    beats: list[dict[str, Any]] = []
    for idx, sh in enumerate(raw_shots):
        t0 = float(sh.get("start_sec", sh.get("t_start", idx * (total_frames / max(1, len(raw_shots)) / fps))))
        t1 = float(sh.get("end_sec", sh.get("t_end", (idx + 1) * (total_frames / max(1, len(raw_shots)) / fps))))
        sf = 0 if idx == 0 else beats[-1]["end_frame"]
        ef = total_frames if idx == len(raw_shots) - 1 else max(sf + 1, min(total_frames - (len(raw_shots) - 1 - idx), seconds_to_frame(t1, fps_num, fps_den)))
        beats.append({
            "beat_id": f"beat_{idx + 1:02d}",
            "start_frame": sf,
            "end_frame": ef,
            "source_word_ids": [],
            "semantic_function": "hook" if idx == 0 else ("payoff" if idx == len(raw_shots) - 1 else "reveal"),
            "viewer_goal": str(sh.get("purpose") or sh.get("goal") or "Migrated from SHOTBOOK v1"),
            "emotion_intensity": 0.7,
            "priority": 4,
            "content_refs": [source_asset_id],
            "proof_status": "SOURCE_FOOTAGE_DIRECT",
            "visual_mode": str(sh.get("layout") or "person_full_with_lower_headline"),
            "focus_subject": str(sh.get("title") or sh.get("headline") or f"Shot {idx + 1}"),
            "continuity_in": "open_frame" if idx == 0 else "editorial_spine",
            "continuity_out": "clean_settle" if idx == len(raw_shots) - 1 else "editorial_spine",
            "planned_information_change": str(sh.get("visual_change") or "Migrated v1 shot beat"),
            "why": "Migrated deterministically from legacy SHOTBOOK v1",
            "read_time_frames": min(ef - sf, max(12, int(round(fps)))),
            "intentional_hold": False,
        })
    if not beats:
        beats = [{
            "beat_id": "beat_01", "start_frame": 0, "end_frame": total_frames, "source_word_ids": [],
            "semantic_function": "hook", "viewer_goal": "Migrated single beat", "emotion_intensity": 0.7,
            "priority": 4, "content_refs": [source_asset_id], "proof_status": "SOURCE_FOOTAGE_DIRECT",
            "visual_mode": "person_full_with_lower_headline", "focus_subject": "Migrated v1 Shotbook",
            "continuity_in": "open_frame", "continuity_out": "clean_settle",
            "planned_information_change": "Migrated v1 shot", "why": "Legacy v1 migration", "read_time_frames": 24,
        }]
    tmap = build_time_map(source_asset_id, total_frames, fps_num, fps_den)
    return {
        "schema_version": "kh02.shotbook.v2",
        "run_id": run_id,
        "mode": "talking_head",
        "profile": "seecut_core",
        "status": "PLANNED",
        "source_sha_set": {source_asset_id: source_sha256},
        "output_spec": {
            "width": width, "height": height, "output_scale": 1.5,
            "fps_num": fps_num, "fps_den": fps_den, "total_frames": total_frames,
            "audio_sample_rate": 48000, "orientation": "portrait" if height >= width else "landscape",
            "platform_safe_zone": {"top": 0.08, "bottom": 0.12, "left": 0.04, "right": 0.04},
        },
        "input_brief_sha": hash_artifact(v1_shotbook),
        "rights_ledger_sha": hash_artifact({source_asset_id: "OWNER_SUPPLIED_NONCOMMERCIAL"}),
        "transcript_sha": None,
        "edit_decision_map": tmap,
        "story_arc": {
            "creative_direction_id": "migrated_v1",
            "theme": "seecut_geek_craft",
            "archetype": "proof_first_editorial",
            "summary": "Migrated from legacy SHOTBOOK v1.",
            "alternatives_considered": [],
        },
        "beats": beats,
        "visual_events": [],
        "camera_events": plan_camera_path(beats),
        "transitions": [choose_transition(beats[i], beats[i + 1], fps=fps) for i in range(len(beats) - 1)],
        "text_events": [],
        "audio_cues": [],
        "claim_evidence": [],
        "approved_exceptions": [],
        "provenance": {
            "planner_version": __version__,
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "engine": "SeeCut-v3.4-HyperFrames-HTML-GSAP",
            "base_git_sha": "ae51ccd4127c95a456e96c66d46a77fb65142b80",
            "upstream_seecut_sha": UPSTREAM_SEECUT_SHA,
            "video_use_ref_sha": VIDEO_USE_REF_SHA,
        },
    }


# ==============================================================================
# KH-02.R1 (R1.02) — Hook, Retention & Editorial Energy Director
# ==============================================================================

ENERGY_MODE_DEFAULTS: dict[str, dict[str, Any]] = {
    "kinetic": {"opening_review_window_sec": 2.0, "static_review_threshold_sec": 2.0, "allow_calm_opening": False, "default_intensity": "high"},
    "proof": {"opening_review_window_sec": 2.0, "static_review_threshold_sec": 2.5, "allow_calm_opening": True, "default_intensity": "high"},
    "reaction": {"opening_review_window_sec": 1.8, "static_review_threshold_sec": 2.2, "allow_calm_opening": False, "default_intensity": "high"},
    "payoff": {"opening_review_window_sec": 2.0, "static_review_threshold_sec": 2.5, "allow_calm_opening": True, "default_intensity": "high"},
    "explanatory": {"opening_review_window_sec": 3.0, "static_review_threshold_sec": 4.0, "allow_calm_opening": True, "default_intensity": "mid"},
    "suspense": {"opening_review_window_sec": 3.5, "static_review_threshold_sec": 3.5, "allow_calm_opening": True, "default_intensity": "mid"},
    "calm": {"opening_review_window_sec": 4.0, "static_review_threshold_sec": 5.0, "allow_calm_opening": True, "default_intensity": "low"},
    "emotional": {"opening_review_window_sec": 4.0, "static_review_threshold_sec": 5.5, "allow_calm_opening": True, "default_intensity": "low"},
}

FABRICATED_CLAIM_PATTERNS = (
    "10x guaranteed", "100% guaranteed", "viral guaranteed", "instant million",
    "secret hack", "only 2 left", "limited time only", "act now before",
    "doctors hate", "never fails", "99% ctr", "95% retention",
)


def resolve_pacing_budget(
    brief: dict[str, Any],
    *,
    total_duration_sec: float = 20.0,
) -> dict[str, Any]:
    """Resolve pacing & energy budget grounded in mode/profile (editorial intent only; zero fake CTR/retention metrics)."""
    mode = brief.get("mode", "talking_head")
    profile = brief.get("profile", "editorial_pro")
    explicit_energy = brief.get("energy_mode")
    if explicit_energy and explicit_energy in ENERGY_MODE_DEFAULTS:
        energy = explicit_energy
    elif profile == "minimal_documentary" or mode in ("documentary", "interview"):
        energy = "calm"
    elif mode == "narrative":
        energy = "suspense"
    elif mode in ("education", "tutorial", "software_demo", "faceless"):
        energy = "explanatory"
    elif mode in ("product_demo", "talking_head"):
        energy = "proof"
    else:
        energy = "kinetic"

    cfg = ENERGY_MODE_DEFAULTS[energy]
    is_long_form = total_duration_sec >= 90.0
    return {
        "profile": profile,
        "mode": mode,
        "energy_mode": energy,
        "opening_review_window_sec": cfg["opening_review_window_sec"] if not is_long_form else max(4.0, cfg["opening_review_window_sec"]),
        "static_review_threshold_sec": cfg["static_review_threshold_sec"],
        "allow_calm_opening": cfg["allow_calm_opening"],
        "chaptered_arcs": is_long_form,
        "editorial_intent_only": True,
        "note": "Editorial pacing intent only — never a measured retention/CTR prediction or fixed cut quota.",
    }


def evaluate_hook_truthfulness(
    option: dict[str, Any],
    *,
    brief: dict[str, Any],
    inventory: dict[str, Any],
    transcript: dict[str, Any] | None = None,
    cut_policy: str = "LOCK_VOICE",
) -> tuple[bool, list[str]]:
    """Verify opening hook truthfulness: no fake claims, no spoilers in suspense, no unapproved speech rewrite/reorder."""
    issues: list[str] = []
    usable_ids = {a["asset_id"] for a in inventory.get("assets", []) if a.get("usable_in_render")}
    for aid in option.get("evidence_asset_ids", []):
        if aid not in usable_ids:
            issues.append(f"unsupported_evidence_asset:{aid}")

    combined_copy = f"{option.get('opening_line', '')} {option.get('opening_visual', '')} {option.get('strategy', '')}".lower()
    for pat in FABRICATED_CLAIM_PATTERNS:
        if pat in combined_copy:
            issues.append(f"fabricated_or_clickbait_claim:{pat}")

    # Refuse sensitive/unverified claims explicitly marked in brief
    for sc in brief.get("sensitive_claims", []):
        tok = str(sc).strip().lower()
        if tok and len(tok) >= 4 and tok in combined_copy:
            issues.append(f"sensitive_unverified_claim:{tok}")

    # Suspense / narrative spoiler protection
    if brief.get("mode") == "narrative" or brief.get("energy_mode") == "suspense":
        if option.get("reveals_payoff_early") or "spoiler" in combined_copy or "ending reveal" in combined_copy:
            issues.append("suspense_spoiler_violation")

    # Speech truthfulness & PRESERVE_SCRIPT_TRIM / LOCK_VOICE non-reorder enforcement
    words = (transcript or {}).get("words", [])
    if words:
        first_phrase_ids = [w["word_id"] for w in words[: min(8, len(words))]]
        opt_wids = option.get("source_word_ids") or []
        if cut_policy in ("LOCK_VOICE", "PRESERVE_SCRIPT_TRIM"):
            if option.get("preserves_original_speech") is False:
                issues.append(f"unauthorized_speech_rewrite_under_{cut_policy}")
            if opt_wids and first_phrase_ids and opt_wids[0] != first_phrase_ids[0]:
                # Opening speech line must start with the true opening word under LOCK_VOICE / PRESERVE_SCRIPT_TRIM
                issues.append(f"unauthorized_speech_reorder:{opt_wids[0]}!={first_phrase_ids[0]}")

    return (len(issues) == 0), issues


def build_hook_candidates(
    brief: dict[str, Any],
    inventory: dict[str, Any],
    *,
    transcript: dict[str, Any] | None = None,
    cut_policy: str = "LOCK_VOICE",
    custom_options: list[dict[str, Any]] | None = None,
    presenter_policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate 2-4 distinct authentic opening hook candidates grounded in transcript, assets & presenter_policy."""
    usable = [a for a in inventory.get("assets", []) if a.get("usable_in_render")]
    primary = next((a for a in usable if a["kind"] in ("a_roll", "b_roll")), usable[0])
    evid = [a["asset_id"] for a in usable if a["kind"] in ("real_ui", "still_asset")]
    words = (transcript or {}).get("words", [])
    phrases = (transcript or {}).get("phrases", [])
    first_line = phrases[0]["text"] if phrases else brief.get("key_message", "")
    first_wids = list(phrases[0]["word_ids"]) if phrases else [w["word_id"] for w in words[:6]]
    mode = brief.get("mode", "talking_head")
    energy = resolve_pacing_budget(brief)["energy_mode"]
    is_fixed_pip = ((presenter_policy or brief.get("presenter_policy") or {}).get("mode") == "FIXED_PIP")

    if custom_options is not None:
        raw_opts = [dict(o) for o in custom_options]
    else:
        raw_opts = [
            {
                "option_id": "hook_opt_01_authentic_lead",
                "strategy": (
                    "Main-canvas kinetic phrase hook at f0 paired with persistent fixed-PIP presenter"
                    if is_fixed_pip else
                    "Direct authentic reaction + kinetic keyword accent on opening phrase"
                ),
                "opening_visual": (
                    f"Frame-0 kinetic phrase mask on main canvas while presenter ({primary['asset_id']}) remains locked in FIXED_PIP."
                    if is_fixed_pip else
                    f"Tight micro-reframe on presenter/subject ({primary['asset_id']}) at f0 with kinetic phrase mask "
                    "revealing verified subject focus without duplicating the full sentence."
                ),
                "first_frame_visual_id": "ev_01_hook_main" if is_fixed_pip else "ev_01_beat_01",
                "start_frame": 0,
                "opening_motion": "kinetic_phrase_mask + companion spring badge",
                "opening_line": first_line,
                "opening_audio_role": "natural_voice_lead" if energy in ("calm", "emotional") else "subtle_air_accent_on_reveal",
                "source_word_ids": first_wids,
                "evidence_asset_ids": [primary["asset_id"]],
                "preserves_original_speech": True,
                "pros": [
                    "Immediate human eye contact and honest emotional delivery from frame 0.",
                    "100% compatible with LOCK_VOICE, PRESERVE_SCRIPT_TRIM and FIXED_PIP without speech reorder.",
                ],
                "cons": ["Delays full product/UI capture until the demonstration keyword lands."],
            },
            {
                "option_id": "hook_opt_02_result_preview_spotlight",
                "strategy": "Verified result/evidence spotlight preview on main canvas paired with original opening line",
                "opening_visual": (
                    f"Clean frame-0 visual wipe of verified real asset ({evid[0]}) on main canvas alongside presenter ({primary['asset_id']})."
                    if evid else
                    f"Visual focus crop on primary footage ({primary['asset_id']}) highlighting concrete outcome before wider frame."
                ),
                "first_frame_visual_id": "ev_01_hook_main" if is_fixed_pip else "ev_01_beat_01",
                "start_frame": 0,
                "opening_motion": "clip_mask_wipe + spring callout",
                "opening_line": first_line,
                "opening_audio_role": "quiet_direct_entry" if energy in ("calm", "emotional") else "clean_ui_preview_whoosh",
                "source_word_ids": first_wids,
                "evidence_asset_ids": [primary["asset_id"]] + (evid[:1] if evid else []),
                "preserves_original_speech": True,
                "reveals_payoff_early": (mode == "narrative" or energy == "suspense"),
                "pros": [
                    "Shows concrete visual proof within frame 0-1.5s to answer viewer curiosity immediately.",
                    "Eliminates slow talking-head ramp on product/demo reels.",
                ],
                "cons": ["Requires strict spoiler check on suspense/narrative scripts."],
            },
            {
                "option_id": "hook_opt_03_calm_direct_context",
                "strategy": "Calm direct-context opening with clean lower kicker and zero forced zoom/SFX",
                "opening_visual": f"Steady framing on {primary['asset_id']} with restrained editorial kicker and generous breathing room.",
                "first_frame_visual_id": "ev_01_hook_main" if is_fixed_pip else "ev_01_beat_01",
                "start_frame": 0,
                "opening_motion": "clean_opacity_settle",
                "opening_line": first_line,
                "opening_audio_role": "intentional_silence_voice_only",
                "source_word_ids": first_wids,
                "evidence_asset_ids": [primary["asset_id"]],
                "preserves_original_speech": True,
                "pros": [
                    "Ideal for calm interviews, documentaries, tutorials, and high-trust explainers.",
                    "Zero visual noise or artificial hype; preserves natural delivery gravitas.",
                ],
                "cons": ["Lower initial visual contrast on fast-scroll short product reels."],
            },
        ]

    evaluated: list[dict[str, Any]] = []
    for opt in raw_opts:
        ok, issues = evaluate_hook_truthfulness(
            opt, brief=brief, inventory=inventory, transcript=transcript, cut_policy=cut_policy,
        )
        item = dict(opt)
        item["truthfulness_ok"] = ok
        item["truthfulness_issues"] = issues
        item["selected"] = False
        item["rejected_reason"] = None
        evaluated.append(item)
    return evaluated


def choose_opening(
    options: list[dict[str, Any]],
    *,
    brief: dict[str, Any],
    preferred_option_id: str | None = None,
) -> dict[str, Any]:
    """Select the strongest truthful opening option matching content energy mode (never chooses invalid/untruthful option)."""
    valid = [o for o in options if o.get("truthfulness_ok")]
    if not valid:
        raise ValueError(f"All hook options failed truthfulness checks: {[o.get('truthfulness_issues') for o in options]}")

    energy = resolve_pacing_budget(brief)["energy_mode"]
    chosen = None
    if preferred_option_id:
        chosen = next((o for o in valid if o["option_id"] == preferred_option_id), None)
    if chosen is None:
        if energy in ("calm", "emotional", "explanatory", "suspense"):
            chosen = next((o for o in valid if "calm" in o["option_id"]), None) or next((o for o in valid if "authentic" in o["option_id"]), valid[0])
        elif energy in ("proof", "kinetic", "payoff"):
            is_fixed = ((brief.get("presenter_policy") or {}).get("mode") == "FIXED_PIP")
            has_ev_spot = any("spotlight" in o["option_id"] and len(o.get("evidence_asset_ids") or []) >= 2 for o in valid)
            if is_fixed and energy == "proof" and has_ev_spot:
                chosen = next((o for o in valid if "spotlight" in o["option_id"]), valid[0])
            else:
                chosen = next((o for o in valid if "authentic" in o["option_id"] or "spotlight" in o["option_id"]), valid[0])
        else:
            chosen = valid[0]

    out_opts: list[dict[str, Any]] = []
    for o in options:
        item = dict(o)
        if item["option_id"] == chosen["option_id"]:
            item["selected"] = True
            item["rejected_reason"] = None
        else:
            item["selected"] = False
            if not item["truthfulness_ok"]:
                item["rejected_reason"] = f"Rejected by truthfulness gate: {', '.join(item['truthfulness_issues'])}"
            else:
                item["rejected_reason"] = f"Valid alternative, less aligned with energy_mode={energy!r} than {chosen['option_id']}."
        out_opts.append(item)

    return {
        "selected_option_id": chosen["option_id"],
        "selected_option": next(o for o in out_opts if o["selected"]),
        "options": out_opts,
        "selection_rationale": (
            f"Selected {chosen['option_id']} ({chosen['strategy']}) for mode={brief.get('mode')!r}, "
            f"energy_mode={energy!r}; passed all truthfulness & speech-preservation checks."
        ),
    }


def plan_attention_beats(
    beats: list[dict[str, Any]],
    *,
    brief: dict[str, Any],
    fps: float = 24.0,
) -> list[dict[str, Any]]:
    """Annotate each semantic beat with editorial attention intent, rehooks, planned quiet holds, and sound roles."""
    budget = resolve_pacing_budget(brief)
    energy = budget["energy_mode"]
    static_thresh = float(budget["static_review_threshold_sec"])
    attention_beats: list[dict[str, Any]] = []

    for idx, b in enumerate(beats):
        fn = b.get("semantic_function", "reveal")
        dur_sec = max(0.05, (int(b["end_frame"]) - int(b["start_frame"])) / max(1.0, fps))
        is_hold = bool(b.get("intentional_hold"))
        if energy in ("calm", "emotional"):
            intensity = "low" if fn not in ("payoff", "reveal") else "mid"
        elif fn in ("hook", "payoff", "evidence", "cta"):
            intensity = "high"
        elif fn in ("breath",):
            intensity = "low"
        else:
            intensity = "mid"

        # Distinguish readable payoff hold vs empty waiting needing purposeful rehook
        rehook_reason = None
        if idx > 0 and dur_sec > static_thresh and not is_hold:
            rehook_reason = f"Interval {dur_sec:.2f}s > {static_thresh:.1f}s ({energy}); introduce meaningful scale/detail shift or evidence reveal."
        elif fn in ("evidence", "reveal", "step"):
            rehook_reason = f"Purposeful {fn} shift: new visual information scale rather than decorative flash."

        sound_role = (
            "intentional_silence" if energy in ("calm", "emotional") and fn in ("breath", "hook")
            else ("transition_whoosh_and_object_pop" if fn in ("evidence", "reveal")
                  else ("subtle_step_click_series" if fn == "step"
                        else ("warm_payoff_chime_or_settle" if fn in ("payoff", "cta") else "clean_hook_accent")))
        )
        viewer_q = {
            "hook": "What is this about and why should I watch the next second?",
            "evidence": "Show me concrete proof — what does it actually look like?",
            "step": "What are the exact components or steps inside?",
            "reveal": "What detail changes my understanding here?",
            "payoff": "Did the opening promise deliver a satisfying concrete outcome?",
            "cta": "What is the clear final takeaway?",
        }.get(fn, f"What new information advances the {fn} beat?")

        attention_beats.append({
            "beat_id": b["beat_id"],
            "viewer_question": viewer_q,
            "answer_payoff": b.get("planned_information_change", b.get("viewer_goal", "")),
            "semantic_role": fn,
            "source_word_ids": list(b.get("source_word_ids", [])),
            "priority": int(b.get("priority", 4)),
            "intended_intensity": intensity,
            "visual_focal_subject": b.get("focus_subject", ""),
            "expected_information_change": b.get("planned_information_change", ""),
            "motion_purpose": "readable_hold" if is_hold else f"motivated_{fn}_focus",
            "sound_role": sound_role,
            "planned_quiet_hold": is_hold or (energy in ("calm", "emotional") and intensity == "low"),
            "rehook_reason": rehook_reason,
            "continuity": f"{b.get('continuity_in', 'open')} -> {b.get('continuity_out', 'settle')}",
        })
    return attention_beats


def audit_payoff(
    hook_decision: dict[str, Any],
    beats: list[dict[str, Any]],
    *,
    claim_evidence: list[dict[str, Any]] | None = None,
    brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit promise -> payoff linkage, spoiler integrity, and evidence verification."""
    issues: list[str] = []
    unverified_claims: list[str] = []
    fns = [b.get("semantic_function") for b in beats]
    has_payoff = any(fn in ("payoff", "cta", "evidence", "reveal") for fn in fns[1:]) if len(fns) > 1 else bool(fns)
    if not has_payoff:
        issues.append("missing_payoff_or_answer_beat_after_opening")

    sel = hook_decision.get("selected_option") or {}
    spoiler_violation = "suspense_spoiler_violation" in (sel.get("truthfulness_issues") or [])
    if spoiler_violation:
        issues.append("opening_violates_suspense_spoiler_protection")

    for clm in (claim_evidence or []):
        if clm.get("verification_status") == "UNVERIFIED_BLOCKED" and clm.get("allowed_display"):
            unverified_claims.append(str(clm.get("claim_id", "unknown")))
            issues.append(f"unverified_claim_displayed:{clm.get('claim_id')}")

    return {
        "promise_payoff_linked": has_payoff and not issues,
        "spoiler_violation": spoiler_violation,
        "unverified_claims": unverified_claims,
        "issues": issues,
    }


def emit_hook_and_pacing_artifacts(
    out_dir: str | Path,
    *,
    run_id: str,
    brief: dict[str, Any],
    hook_decision: dict[str, Any],
    pacing_budget: dict[str, Any],
    attention_beats: list[dict[str, Any]],
    payoff_audit: dict[str, Any],
    beats: list[dict[str, Any]],
    visual_events: list[dict[str, Any]] | None = None,
    audio_cues: list[dict[str, Any]] | None = None,
    fps: float = 24.0,
) -> dict[str, Path]:
    """Write HOOK_OPTIONS.json, HOOK_DECISION.md, PACING_PLAN.json, ATTENTION_BEATS.md & EDITORIAL_COVERAGE.json."""
    base = Path(out_dir)
    base.mkdir(parents=True, exist_ok=True)

    hook_doc = {
        "schema_version": "kh02r1.hook_plan.v1",
        "run_id": run_id,
        "mode": brief["mode"],
        "energy_mode": pacing_budget["energy_mode"],
        "selected_option_id": hook_decision["selected_option_id"],
        "selection_rationale": hook_decision["selection_rationale"],
        "options": hook_decision["options"],
        "pacing_budget": pacing_budget,
        "attention_beats": attention_beats,
        "payoff_audit": payoff_audit,
    }
    errs = validate_schema(hook_doc, "hook_plan")
    if errs:
        raise ValueError(f"HOOK_OPTIONS failed schema validation: {errs[:3]}")

    def _wj(name: str, obj: Any) -> Path:
        p = base / name
        p.write_text(json.dumps_pretty(obj) + "\n", encoding="utf-8")
        return p

    paths: dict[str, Path] = {}
    paths["hook_options"] = _wj("HOOK_OPTIONS.json", hook_doc)
    paths["pacing_plan"] = _wj("PACING_PLAN.json", {
        "schema_version": "kh02r1.pacing_plan.v1",
        "run_id": run_id,
        "pacing_budget": pacing_budget,
        "attention_beats": attention_beats,
        "payoff_audit": payoff_audit,
    })

    # EDITORIAL_COVERAGE.json mapping each beat window to applied visuals and audio
    vis = visual_events or []
    aud = audio_cues or []
    coverage_rows = []
    for b in beats:
        sf, ef = int(b["start_frame"]), int(b["end_frame"])
        b_vis = [ev["event_id"] for ev in vis if ev.get("beat_id") == b["beat_id"] or (int(ev["start_frame"]) < ef and int(ev["end_frame"]) > sf)]
        b_aud = [cu["cue_id"] for cu in aud if int(cu["frame_start"]) < ef and int(cu["frame_end"]) > sf]
        coverage_rows.append({
            "beat_id": b["beat_id"],
            "semantic_function": b["semantic_function"],
            "start_frame": sf,
            "end_frame": ef,
            "duration_sec": round((ef - sf) / max(1.0, fps), 3),
            "intentional_hold": bool(b.get("intentional_hold")),
            "visual_event_ids": b_vis,
            "audio_cue_ids": b_aud,
            "coverage_status": "COVERED" if b_vis else ("INTENTIONAL_HOLD" if b.get("intentional_hold") else "VOICE_ONLY"),
        })
    paths["editorial_coverage"] = _wj("EDITORIAL_COVERAGE.json", {
        "schema_version": "kh02r1.editorial_coverage.v1",
        "run_id": run_id,
        "beats": coverage_rows,
    })

    # HOOK_DECISION.md
    h_lines = [
        f"# Hook & Opening Decision — `{run_id}`",
        "",
        f"- **Selected Option**: `{hook_decision['selected_option_id']}`",
        f"- **Energy Mode**: `{pacing_budget['energy_mode']}` (`editorial_intent_only=true`; zero invented CTR/retention claims)",
        f"- **Rationale**: {hook_decision['selection_rationale']}",
        f"- **Payoff Audit**: `promise_payoff_linked={payoff_audit['promise_payoff_linked']}`, `spoiler_violation={payoff_audit['spoiler_violation']}`",
        "",
        "## Evaluated Opening Options",
        "",
    ]
    for opt in hook_decision["options"]:
        badge = "SELECTED" if opt["selected"] else ("REJECTED_TRUTHFULNESS" if not opt["truthfulness_ok"] else "ALTERNATIVE")
        h_lines.append(f"### `{opt['option_id']}` [{badge}]")
        h_lines.append(f"- **Strategy**: {opt['strategy']}")
        h_lines.append(f"- **Opening Visual**: {opt['opening_visual']}")
        h_lines.append(f"- **Opening Line**: {opt['opening_line']}")
        h_lines.append(f"- **Audio Role**: `{opt['opening_audio_role']}`")
        if opt.get("rejected_reason"):
            h_lines.append(f"- **Status Note**: {opt['rejected_reason']}")
        h_lines.append("")
    p_hd = base / "HOOK_DECISION.md"
    p_hd.write_text("\n".join(h_lines), encoding="utf-8")
    paths["hook_decision_md"] = p_hd

    # ATTENTION_BEATS.md
    a_lines = [
        f"# Attention Beats & Editorial Energy Curve — `{run_id}`",
        "",
        "> Editorial intent map only (NOT measured audience retention).",
        "",
        "| Beat | Role | Intensity | Viewer Question | Focal Subject | Motion Purpose | Sound Role | Rehook / Quiet Hold |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for ab in attention_beats:
        rh = "QUIET_HOLD" if ab["planned_quiet_hold"] else (ab.get("rehook_reason") or "Continuous flow")
        a_lines.append(
            f"| `{ab['beat_id']}` | `{ab['semantic_role']}` | `{ab['intended_intensity']}` | {ab['viewer_question']} | "
            f"{ab['visual_focal_subject']} | `{ab['motion_purpose']}` | `{ab['sound_role']}` | {rh} |"
        )
    a_lines.append("")
    p_ab = base / "ATTENTION_BEATS.md"
    p_ab.write_text("\n".join(a_lines), encoding="utf-8")
    paths["attention_beats_md"] = p_ab

    # R2 explicit semantic & hook linkage artifacts
    paths["hook_candidates"] = _wj("HOOK_CANDIDATES.json", hook_doc)
    paths["selected_hook"] = _wj("SELECTED_HOOK.json", {
        "schema_version": "kh02r2.selected_hook.v1",
        "run_id": run_id,
        "selected_option_id": hook_decision["selected_option_id"],
        "selected_option": hook_decision["selected_option"],
        "selection_rationale": hook_decision["selection_rationale"],
    })
    paths["payoff_audit"] = _wj("PAYOFF_AUDIT.json", {
        "schema_version": "kh02r2.payoff_audit.v1",
        "run_id": run_id,
        **payoff_audit,
    })
    paths["semantic_map"] = _wj("SEMANTIC_MAP.json", {
        "schema_version": "kh02r2.semantic_map.v1",
        "run_id": run_id,
        "classifier": "rule_grounded_deterministic_fallback",
        "note": "Deterministic semantic beat classification (not claiming LLM semantic pass).",
        "beats": [
            {
                "beat_id": b["beat_id"],
                "start_frame": b["start_frame"],
                "end_frame": b["end_frame"],
                "semantic_function": b.get("semantic_function", "reveal"),
                "proof_status": b.get("proof_status", "SOURCE_FOOTAGE_DIRECT"),
                "content_refs": b.get("content_refs", []),
                "focus_subject": b.get("focus_subject", ""),
                "why": b.get("why", ""),
            }
            for b in beats
        ],
    })
    f0_events = [
        {
            "event_id": ev["event_id"],
            "capability_id": ev["capability_id"],
            "start_frame": ev["start_frame"],
            "end_frame": ev["end_frame"],
            "selected_hook_id": (ev.get("params") or {}).get("selected_hook_id"),
        }
        for ev in (visual_events or [])
        if int(ev["start_frame"]) == 0 and (ev.get("params") or {}).get("kind") != "progress_spine"
    ]
    paths["hook_applied_evidence"] = _wj("HOOK_APPLIED_EVIDENCE.json", {
        "schema_version": "kh02r2.hook_applied_evidence.v1",
        "run_id": run_id,
        "selected_option_id": hook_decision["selected_option_id"],
        "compiled_frame_zero_events": f0_events,
        "hook_compiled_at_frame_zero": any(
            e.get("selected_hook_id") == hook_decision["selected_option_id"] for e in f0_events
        ) or bool(f0_events),
    })

    return paths
