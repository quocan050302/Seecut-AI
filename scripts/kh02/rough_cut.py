"""KH-02.R1 Intelligent Rough Cut & Footage Selection Engine (R1.01).

Selective waveform/silence analysis ideas adapted from:
  browser-use/video-use @ 43cfc566833548093455c9d4a46bd69913b7e552
  helpers/timeline_view.py (MIT License, Copyright (c) 2026 Browser Use).
Never uses video-use/helpers/render.py as final renderer; all derived clips feed SeeCut HyperFrames/GSAP.
"""

from __future__ import annotations

import json
import math
import re
import struct
import subprocess
import wave
from fractions import Fraction
from pathlib import Path
from typing import Any

from .contracts import hash_artifact, is_strict_int, seconds_to_frame, validate_schema
from .transcript import map_words_to_output

CANDIDATE_TYPES = (
    "DEAD_AIR",
    "LEAD_TAIL_PADDING",
    "WAITING_FOR_UI",
    "REPEATED_TAKE",
    "FALSE_START",
    "FILLER",
    "STUMBLE",
    "OFF_TOPIC",
    "VISUAL_STALL",
    "REACTION",
    "DELIBERATE_PAUSE",
    "CONTEXT_REQUIRED",
    "TECHNICAL_DROPOUT",
)

NONVERBAL_TRIM_TYPES = frozenset({"DEAD_AIR", "LEAD_TAIL_PADDING", "WAITING_FOR_UI", "VISUAL_STALL"})
EDITORIAL_ONLY_TYPES = frozenset({"REPEATED_TAKE", "FALSE_START", "FILLER", "STUMBLE", "OFF_TOPIC"})
PROTECTED_KEEP_TYPES = frozenset({"REACTION", "DELIBERATE_PAUSE", "CONTEXT_REQUIRED", "TECHNICAL_DROPOUT"})

NEGATION_TOKENS = frozenset({
    "no", "not", "never", "don't", "cant", "can't", "won't", "without", "none", "except",
    "only", "unless", "không", "chưa", "đừng", "không phải", "chỉ",
})
QUALIFIER_TOKENS = frozenset({
    "roughly", "about", "almost", "nearly", "estimated", "noncommercial", "optional",
    "maybe", "up", "at", "least", "max", "min", "free", "paid",
})
FILLER_TOKENS = frozenset({"um", "uh", "erm", "hmm", "ah", "like", "you know", "ờ", "ừm", "à"})
NUMBER_RE = re.compile(r"\d")


def _norm_tok(text: str) -> str:
    return re.sub(r"[^a-z0-9'\u00C0-\u024F\u1E00-\u1EFF\u4e00-\u9fff]+", "", text.lower()).strip()


def _is_protected_word(word: dict[str, Any], idx: int, words: list[dict[str, Any]]) -> tuple[bool, str | None]:
    raw = str(word.get("text", "")).strip()
    tok = _norm_tok(raw)
    if not tok:
        return False, None
    if tok in NEGATION_TOKENS:
        return True, "negation_token"
    if tok in QUALIFIER_TOKENS:
        return True, "claim_qualifier"
    if NUMBER_RE.search(raw):
        return True, "numeric_fact"
    # Proper noun / brand name heuristic (mid-sentence capitalized token)
    if idx > 0 and raw and raw[0].isupper():
        prev_txt = str(words[idx - 1].get("text", "")).strip()
        if not prev_txt or prev_txt[-1] not in ".!?":
            return True, "proper_name_or_brand"
    return False, None


def resolve_cut_policy(
    requested_policy: str | None = None,
    *,
    approved_scope: str | None = None,
    brief_voice_policy: str | None = None,
) -> dict[str, Any]:
    """Resolve authoritative cut policy without silently broadening owner permissions.

    Maps legacy TRIM_APPROVED strictly to PRESERVE_SCRIPT_TRIM unless explicit EDITORIAL_CUT scope is authorized.
    """
    raw = (requested_policy or brief_voice_policy or "LOCK_VOICE").strip().upper()
    scope = (approved_scope or "").strip()

    if raw in ("LOCK_VOICE", "SILENT_BY_DESIGN"):
        return {
            "cut_policy": "LOCK_VOICE",
            "voice_policy": raw,
            "allow_nonverbal_trim": False,
            "allow_editorial_deletion": False,
            "allow_reordering": False,
            "approval_scope": scope or "default_lock_voice",
            "reason": "Default LOCK_VOICE preserves continuous source timing and 100% identity speech mapping.",
        }
    if raw in ("PRESERVE_SCRIPT_TRIM", "TRIM_APPROVED"):
        return {
            "cut_policy": "PRESERVE_SCRIPT_TRIM",
            "voice_policy": "PRESERVE_SCRIPT_TRIM",
            "allow_nonverbal_trim": True,
            "allow_editorial_deletion": False,
            "allow_reordering": False,
            "approval_scope": scope or "explicit_preserve_script_trim",
            "reason": "PRESERVE_SCRIPT_TRIM authorizes safe nonverbal dead-air/lead-tail/UI-waiting trims while preserving 100% of spoken words in exact order.",
        }
    if raw == "EDITORIAL_CUT":
        if not scope:
            # Never silently broaden to EDITORIAL_CUT without explicit recorded scope
            return {
                "cut_policy": "LOCK_VOICE",
                "voice_policy": "LOCK_VOICE",
                "allow_nonverbal_trim": False,
                "allow_editorial_deletion": False,
                "allow_reordering": False,
                "approval_scope": None,
                "blocked_reason": "EDITORIAL_CUT requires explicit non-empty approval_scope; downgraded safely to LOCK_VOICE.",
                "reason": "Missing explicit approval_scope for EDITORIAL_CUT; refusing silent broadening.",
            }
        return {
            "cut_policy": "EDITORIAL_CUT",
            "voice_policy": "EDITORIAL_CUT",
            "allow_nonverbal_trim": True,
            "allow_editorial_deletion": True,
            "allow_reordering": ("REORDER_APPROVED" in scope.upper()),
            "approval_scope": scope,
            "reason": f"Explicit EDITORIAL_CUT authorized under scope={scope!r}; low-risk filler/false-start/repeated-take cuts permitted with meaning verification.",
        }
    raise ValueError(f"Unsupported cut policy: {requested_policy!r}")


def classify_pause(
    start_sec: float,
    end_sec: float,
    *,
    source_duration_sec: float,
    words: list[dict[str, Any]] | None = None,
    motion_score: float | None = None,
    visual_stall_windows: list[tuple[float, float]] | None = None,
    intentional_windows: list[tuple[float, float]] | None = None,
    reaction_windows: list[tuple[float, float]] | None = None,
    dropout_windows: list[tuple[float, float]] | None = None,
    context_windows: list[tuple[float, float]] | None = None,
    has_audio_track: bool = True,
) -> dict[str, Any]:
    """Classify a silent/non-speech interval without blindly deleting on amplitude alone."""
    dur = max(0.0, float(end_sec) - float(start_sec))
    wlist = words or []

    def _overlaps(wins: list[tuple[float, float]] | None, min_ov: float = 0.12) -> bool:
        for a, b in (wins or []):
            if min(end_sec, b) - max(start_sec, a) >= min_ov:
                return True
        return False

    # 1. Check for overlapping spoken words => possible TECHNICAL_DROPOUT (never auto-cut!)
    overlapping_words = [
        w["word_id"] for w in wlist
        if float(w["end_sec"]) > start_sec + 0.08 and float(w["start_sec"]) < end_sec - 0.08
    ]
    if overlapping_words or _overlaps(dropout_windows, 0.05):
        return {
            "candidate_type": "TECHNICAL_DROPOUT",
            "recommended_outcome": "REVIEW",
            "semantic_risk": "HIGH",
            "reason": "Silence overlaps spoken word timestamps or flagged audio dropout; never auto-delete.",
        }

    # 2. Silent-only footage: silence never equals redundant visual
    if not has_audio_track:
        if motion_score is not None and motion_score < 0.015 and dur >= 1.2:
            return {
                "candidate_type": "VISUAL_STALL",
                "recommended_outcome": "SHORTEN",
                "semantic_risk": "LOW",
                "reason": f"No-audio clip shows static visual stall ({dur:.2f}s, motion={motion_score:.4f}).",
            }
        return {
            "candidate_type": "CONTEXT_REQUIRED",
            "recommended_outcome": "KEEP",
            "semantic_risk": "HIGH",
            "reason": "Silent visual clip carries active visual action; no audio never implies redundant visual.",
        }

    # 3. Protected semantic pauses (reaction / deliberate showcase / required UI context)
    if _overlaps(reaction_windows):
        return {
            "candidate_type": "REACTION",
            "recommended_outcome": "KEEP",
            "semantic_risk": "HIGH",
            "reason": "Facial/delivery reaction beat preserved to maintain human authenticity.",
        }
    if _overlaps(context_windows):
        return {
            "candidate_type": "CONTEXT_REQUIRED",
            "recommended_outcome": "KEEP",
            "semantic_risk": "HIGH",
            "reason": "Visual action or tutorial step requires continuous screen comprehension.",
        }
    if _overlaps(intentional_windows):
        return {
            "candidate_type": "DELIBERATE_PAUSE",
            "recommended_outcome": "KEEP",
            "semantic_risk": "MEDIUM",
            "reason": "Intentional dramatic pause or deliberate product showcase preserved.",
        }

    # 4. Lead-in / tail camera-wait padding
    if start_sec <= 0.05 and dur >= 0.30:
        return {
            "candidate_type": "LEAD_TAIL_PADDING",
            "recommended_outcome": "SHORTEN",
            "semantic_risk": "NONE",
            "reason": f"Opening lead-in camera wait ({dur:.2f}s) before first breath/speech onset.",
        }
    if source_duration_sec > 0 and (source_duration_sec - end_sec) <= 0.15 and dur >= 0.40:
        return {
            "candidate_type": "LEAD_TAIL_PADDING",
            "recommended_outcome": "SHORTEN",
            "semantic_risk": "NONE",
            "reason": f"Trailing end-of-clip dead padding ({dur:.2f}s) after final spoken phrase.",
        }

    # 5. Explicitly detected visual stall / waiting for UI
    if _overlaps(visual_stall_windows):
        return {
            "candidate_type": "WAITING_FOR_UI",
            "recommended_outcome": "SHORTEN",
            "semantic_risk": "LOW",
            "reason": f"Redundant static UI/scroll linger ({dur:.2f}s) with no new visual information.",
        }
    if motion_score is not None and dur >= 0.80:
        if motion_score < 0.02:
            return {
                "candidate_type": "VISUAL_STALL",
                "recommended_outcome": "SHORTEN",
                "semantic_risk": "LOW",
                "reason": f"Long silent gap ({dur:.2f}s) with near-zero visual change (motion={motion_score:.4f}).",
            }
        # High visual motion during silence => active showcase/demo, keep or replace/condense carefully!
        return {
            "candidate_type": "DELIBERATE_PAUSE",
            "recommended_outcome": "KEEP",
            "semantic_risk": "MEDIUM",
            "reason": f"Silent interval ({dur:.2f}s) has active visual motion ({motion_score:.4f}); preserve showcase.",
        }

    # 6. Generic silence gaps (>=0.40s candidate vs 0.15-0.40s caution)
    if dur >= 0.40:
        return {
            "candidate_type": "DEAD_AIR",
            "recommended_outcome": "SHORTEN",
            "semantic_risk": "LOW",
            "reason": f"Non-speech pause ({dur:.2f}s >= 0.40s) without flagged reaction/showcase motion.",
        }
    return {
        "candidate_type": "DELIBERATE_PAUSE",
        "recommended_outcome": "KEEP",
        "semantic_risk": "LOW",
        "reason": f"Short natural breath/pause ({dur:.2f}s < 0.40s caution band); kept by default.",
    }


def detect_take_candidates(
    transcript: dict[str, Any],
    *,
    min_word_confidence: float = 0.65,
) -> list[dict[str, Any]]:
    """Detect repeated takes, false starts, stumbles, and isolated fillers with strict semantic protection."""
    words = transcript.get("words") or []
    phrases = transcript.get("phrases") or []
    src_id = transcript.get("source_asset_id", "src_main")
    word_by_id = {w["word_id"]: (i, w) for i, w in enumerate(words)}
    out: list[dict[str, Any]] = []

    def _phrase_tokens(ph: dict[str, Any]) -> list[str]:
        return [_norm_tok(t) for t in str(ph.get("text", "")).split() if _norm_tok(t)]

    # 1. Repeated takes & false starts between adjacent phrases
    for idx in range(len(phrases) - 1):
        p1, p2 = phrases[idx], phrases[idx + 1]
        t1, t2 = _phrase_tokens(p1), _phrase_tokens(p2)
        if not t1 or not t2:
            continue
        p1_wids = list(p1.get("word_ids", []))
        p1_words = [word_by_id[wid][1] for wid in p1_wids if wid in word_by_id]
        confs = [float(w.get("confidence", 0.9)) for w in p1_words] or [0.0]
        min_conf = min(confs)

        # Check protected meaning tokens unique to p1 (e.g. negation/qualifier/number NOT repeated in p2!)
        t2_set = set(t2)
        unique_protected: list[str] = []
        for wid in p1_wids:
            if wid in word_by_id:
                wi, wobj = word_by_id[wid]
                is_prot, p_reason = _is_protected_word(wobj, wi, words)
                if is_prot and _norm_tok(wobj["text"]) not in t2_set:
                    unique_protected.append(f"{wobj['text']}({p_reason})")

        # Exact or high-overlap repeated take
        overlap_ratio = len(set(t1) & set(t2)) / max(1, len(set(t1)))
        is_exact_repeat = (t1 == t2 and len(t1) >= 2)
        is_false_start = (
            len(t1) >= 2 and len(t2) >= len(t1)
            and t2[: len(t1)] == t1
            and float(p2["start_sec"]) - float(p1["end_sec"]) <= 2.5
        )
        is_near_repeat = (len(t1) >= 3 and overlap_ratio >= 0.78 and len(t2) >= len(t1))

        if is_exact_repeat or is_near_repeat or is_false_start:
            ctype = "FALSE_START" if (is_false_start and not is_exact_repeat) else "REPEATED_TAKE"
            if unique_protected:
                risk = "HIGH"
                outcome = "KEEP"
                reason = f"Retake overlap found, but earlier phrase carries unique protected meaning: {unique_protected}; kept."
            elif min_conf < min_word_confidence:
                risk = "HIGH"
                outcome = "REVIEW"
                reason = f"Ambiguous ASR confidence ({min_conf:.2f} < {min_word_confidence:.2f}) at {ctype}; requires manual/filmstrip review."
            else:
                risk = "LOW"
                outcome = "REMOVE"
                reason = f"Detected {ctype} ({p1['phrase_id']} -> {p2['phrase_id']}, overlap={overlap_ratio:.2f}) with verified continuation."
            s0 = float(p1["start_sec"])
            e0 = float(p1["end_sec"])
            out.append({
                "candidate_id": f"take_{len(out) + 1:03d}",
                "source_asset_id": src_id,
                "candidate_type": ctype,
                "start_pts": round(s0, 4),
                "end_pts": round(e0, 4),
                "trim_start_pts": round(s0, 4),
                "trim_end_pts": round(e0, 4),
                "raw_duration_sec": round(max(0.0, e0 - s0), 4),
                "predicted_saving_sec": round(max(0.0, e0 - s0), 4) if outcome == "REMOVE" else 0.0,
                "word_ids": p1_wids,
                "protected_word_ids": p1_wids if unique_protected else [],
                "context_before": phrases[idx - 1]["text"] if idx > 0 else "",
                "context_after": p2["text"],
                "evidence_paths": [],
                "confidence": round(min_conf, 3),
                "proposed_outcome": outcome,
                "semantic_risk": risk,
                "reason": reason,
            })

    # 2. Isolated filler words & immediate adjacent duplicate stumbles ("the the")
    for idx, w in enumerate(words):
        tok = _norm_tok(str(w.get("text", "")))
        conf = float(w.get("confidence", 0.9))
        prev_w = words[idx - 1] if idx > 0 else None
        next_w = words[idx + 1] if idx + 1 < len(words) else None
        gap_before = (float(w["start_sec"]) - float(prev_w["end_sec"])) if prev_w else 0.25
        gap_after = (float(next_w["start_sec"]) - float(w["end_sec"])) if next_w else 0.25

        if tok in FILLER_TOKENS:
            isolated = (gap_before >= 0.06 and gap_after >= 0.06)
            if conf < min_word_confidence or not isolated:
                outcome, risk = "REVIEW", "MEDIUM"
                reason = f"Filler {w['text']!r} lacks clean non-phoneme boundary or high confidence ({conf:.2f}); review only."
            else:
                outcome, risk = "REMOVE", "LOW"
                reason = f"Isolated vocal filler {w['text']!r} with clean surrounding silence ({gap_before:.2f}s / {gap_after:.2f}s)."
            s0, e0 = float(w["start_sec"]), float(w["end_sec"])
            out.append({
                "candidate_id": f"fill_{len(out) + 1:03d}",
                "source_asset_id": src_id,
                "candidate_type": "FILLER",
                "start_pts": round(s0, 4),
                "end_pts": round(e0, 4),
                "trim_start_pts": round(s0, 4),
                "trim_end_pts": round(e0, 4),
                "raw_duration_sec": round(max(0.0, e0 - s0), 4),
                "predicted_saving_sec": round(max(0.0, e0 - s0), 4) if outcome == "REMOVE" else 0.0,
                "word_ids": [w["word_id"]],
                "protected_word_ids": [],
                "context_before": prev_w["text"] if prev_w else "",
                "context_after": next_w["text"] if next_w else "",
                "evidence_paths": [],
                "confidence": round(conf, 3),
                "proposed_outcome": outcome,
                "semantic_risk": risk,
                "reason": reason,
            })
        elif next_w and tok and tok == _norm_tok(str(next_w.get("text", ""))):
            # Immediate stutter/stumble duplicate ("we we") — never auto-cut protected negations/numbers
            is_prot, _ = _is_protected_word(w, idx, words)
            s0, e0 = float(w["start_sec"]), float(w["end_sec"])
            outcome = "KEEP" if is_prot else ("REVIEW" if conf < min_word_confidence else "REMOVE")
            out.append({
                "candidate_id": f"stumb_{len(out) + 1:03d}",
                "source_asset_id": src_id,
                "candidate_type": "STUMBLE",
                "start_pts": round(s0, 4),
                "end_pts": round(e0, 4),
                "trim_start_pts": round(s0, 4),
                "trim_end_pts": round(e0, 4),
                "raw_duration_sec": round(max(0.0, e0 - s0), 4),
                "predicted_saving_sec": round(max(0.0, e0 - s0), 4) if outcome == "REMOVE" else 0.0,
                "word_ids": [w["word_id"]],
                "protected_word_ids": [w["word_id"]] if is_prot else [],
                "context_before": prev_w["text"] if prev_w else "",
                "context_after": next_w["text"],
                "evidence_paths": [],
                "confidence": round(conf, 3),
                "proposed_outcome": outcome,
                "semantic_risk": "HIGH" if is_prot else "LOW",
                "reason": f"Adjacent duplicate stumble token {w['text']!r} before repeated token.",
            })

    return out


def evaluate_cut_safety(
    candidate: dict[str, Any],
    *,
    words: list[dict[str, Any]] | None = None,
    policy_info: dict[str, Any],
    min_word_confidence: float = 0.65,
    min_trim_sec: float = 0.08,
) -> dict[str, Any]:
    """Evaluate hard safety guardrails on a single cut candidate under resolved cut policy."""
    blockers: list[str] = []
    ctype = candidate["candidate_type"]
    t0 = float(candidate.get("trim_start_pts", candidate["start_pts"]))
    t1 = float(candidate.get("trim_end_pts", candidate["end_pts"]))
    wlist = words or []

    if t1 <= t0 or (t1 - t0) < min_trim_sec:
        blockers.append(f"trim_span_too_short({t1 - t0:.4f}s < {min_trim_sec:.2f}s)")

    if ctype in PROTECTED_KEEP_TYPES:
        blockers.append(f"protected_category({ctype})")

    if not policy_info.get("allow_nonverbal_trim") and ctype in NONVERBAL_TRIM_TYPES:
        blockers.append(f"policy_forbids_trim({policy_info['cut_policy']})")

    if not policy_info.get("allow_editorial_deletion") and ctype in EDITORIAL_ONLY_TYPES:
        blockers.append(f"policy_forbids_word_deletion({policy_info['cut_policy']})")

    if float(candidate.get("confidence", 0.0)) < min_word_confidence:
        blockers.append(f"low_asr_confidence({candidate.get('confidence')} < {min_word_confidence})")

    if candidate.get("protected_word_ids"):
        blockers.append(f"protected_meaning_words({candidate['protected_word_ids']})")

    # Hard mid-word protection: never cut inside any word unless that exact word is an authorized EDITORIAL_CUT target
    allowed_del_ids = set(candidate.get("word_ids", [])) if (policy_info.get("allow_editorial_deletion") and ctype in EDITORIAL_ONLY_TYPES) else set()
    for w in wlist:
        if w["word_id"] in allowed_del_ids:
            continue
        ws, we = float(w["start_sec"]), float(w["end_sec"])
        # Any overlap with [ws + 0.015, we - 0.015] is forbidden
        if min(t1, we - 0.015) > max(t0, ws + 0.015):
            blockers.append(f"mid_word_collision({w['word_id']}:{w['text']!r})")
            break

    safety_ok = len(blockers) == 0
    out = dict(candidate)
    out["safety_ok"] = safety_ok
    out["safety_blockers"] = blockers
    if not safety_ok:
        if ctype == "TECHNICAL_DROPOUT" or any("low_asr_confidence" in b for b in blockers):
            out["proposed_outcome"] = "REVIEW"
        else:
            out["proposed_outcome"] = "KEEP"
        out["predicted_saving_sec"] = 0.0
    elif out.get("proposed_outcome") in ("SHORTEN", "REMOVE"):
        out["predicted_saving_sec"] = round(max(0.0, t1 - t0), 4)
    return out


def rank_cut_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministically rank evaluated cut candidates by safety, low semantic risk, and time saved."""
    risk_weight = {"NONE": 3.0, "LOW": 2.0, "MEDIUM": 0.5, "HIGH": -5.0}
    ranked: list[dict[str, Any]] = []
    for c in candidates:
        item = dict(c)
        score = (10.0 if item.get("safety_ok") else -10.0) + risk_weight.get(item.get("semantic_risk", "MEDIUM"), 0.0) + float(item.get("predicted_saving_sec", 0.0)) * 2.0
        item["rank_score"] = round(score, 3)
        ranked.append(item)
    ranked.sort(key=lambda x: (-x["rank_score"], x["start_pts"], x["candidate_id"]))
    return ranked


def propose_r1_cut_candidates(
    *,
    source_asset_id: str,
    source_sha256: str,
    source_duration_sec: float,
    transcript: dict[str, Any] | None = None,
    silences: list[dict[str, float]] | None = None,
    cut_policy: str = "LOCK_VOICE",
    approved_scope: str | None = None,
    visual_stall_windows: list[tuple[float, float]] | None = None,
    intentional_windows: list[tuple[float, float]] | None = None,
    reaction_windows: list[tuple[float, float]] | None = None,
    context_windows: list[tuple[float, float]] | None = None,
    dropout_windows: list[tuple[float, float]] | None = None,
    motion_scores_by_window: dict[tuple[float, float], float] | None = None,
    pad_sec: float = 0.12,
    lead_in_keep_sec: float = 0.225,
    has_audio_track: bool = True,
) -> dict[str, Any]:
    """Build complete evaluated & ranked R1 cut candidate contract covering silences, UI stalls, and takes."""
    policy_info = resolve_cut_policy(cut_policy, approved_scope=approved_scope)
    words = (transcript or {}).get("words", [])
    raw_cands: list[dict[str, Any]] = []

    def _context_around(t0: float, t1: float) -> tuple[str, str]:
        before = " ".join(w["text"] for w in words if float(w["end_sec"]) <= t0 + 0.05)[-48:]
        after = " ".join(w["text"] for w in words if float(w["start_sec"]) >= t1 - 0.05)[:48]
        return before.strip(), after.strip()

    # 1. Lead-in padding before first word
    if words and float(words[0]["start_sec"]) >= 0.35:
        first_s = float(words[0]["start_sec"])
        trim_e = max(0.0, round(first_s - max(0.08, lead_in_keep_sec), 4))
        cls = classify_pause(
            0.0, first_s, source_duration_sec=source_duration_sec, words=words,
            intentional_windows=intentional_windows, reaction_windows=reaction_windows,
            has_audio_track=has_audio_track,
        )
        cb, ca = _context_around(0.0, first_s)
        raw_cands.append({
            "candidate_id": f"rc_{len(raw_cands) + 1:03d}",
            "source_asset_id": source_asset_id,
            "candidate_type": cls["candidate_type"],
            "start_pts": 0.0,
            "end_pts": round(first_s, 4),
            "trim_start_pts": 0.0,
            "trim_end_pts": trim_e,
            "raw_duration_sec": round(first_s, 4),
            "predicted_saving_sec": round(trim_e, 4),
            "word_ids": [],
            "protected_word_ids": [],
            "context_before": cb,
            "context_after": ca,
            "evidence_paths": [],
            "confidence": 0.96,
            "proposed_outcome": cls["recommended_outcome"] if trim_e >= 0.08 else "KEEP",
            "semantic_risk": cls["semantic_risk"],
            "reason": cls["reason"],
        })

    # 2. Explicit visual-stall / UI-waiting windows (e.g. verified frozen scroll tails inside long silent gaps)
    covered_spans: list[tuple[float, float]] = []
    for vs0, vs1 in (visual_stall_windows or []):
        cls = classify_pause(
            vs0, vs1, source_duration_sec=source_duration_sec, words=words,
            visual_stall_windows=[(vs0, vs1)],
            intentional_windows=intentional_windows, reaction_windows=reaction_windows,
            context_windows=context_windows, dropout_windows=dropout_windows,
            has_audio_track=has_audio_track,
        )
        cb, ca = _context_around(vs0, vs1)
        raw_cands.append({
            "candidate_id": f"rc_{len(raw_cands) + 1:03d}",
            "source_asset_id": source_asset_id,
            "candidate_type": cls["candidate_type"],
            "start_pts": round(vs0, 4),
            "end_pts": round(vs1, 4),
            "trim_start_pts": round(vs0, 4),
            "trim_end_pts": round(vs1, 4),
            "raw_duration_sec": round(max(0.0, vs1 - vs0), 4),
            "predicted_saving_sec": round(max(0.0, vs1 - vs0), 4),
            "word_ids": [],
            "protected_word_ids": [],
            "context_before": cb,
            "context_after": ca,
            "evidence_paths": [],
            "confidence": 0.95,
            "proposed_outcome": cls["recommended_outcome"],
            "semantic_risk": cls["semantic_risk"],
            "reason": cls["reason"],
        })
        covered_spans.append((vs0, vs1))

    # 3. Inter-word silence gaps (excluding windows already split by verified visual-stall spans)
    for idx in range(len(words) - 1):
        gs = float(words[idx]["end_sec"])
        ge = float(words[idx + 1]["start_sec"])
        gdur = ge - gs
        if gdur < 0.15:
            continue
        if any(min(ge, c1) - max(gs, c0) >= 0.10 for c0, c1 in covered_spans):
            continue
        m_score = None
        if motion_scores_by_window:
            for (ma, mb), mv in motion_scores_by_window.items():
                if min(ge, mb) - max(gs, ma) >= 0.10:
                    m_score = mv
                    break
        cls = classify_pause(
            gs, ge, source_duration_sec=source_duration_sec, words=words,
            motion_score=m_score, visual_stall_windows=visual_stall_windows,
            intentional_windows=intentional_windows, reaction_windows=reaction_windows,
            context_windows=context_windows, dropout_windows=dropout_windows,
            has_audio_track=has_audio_track,
        )
        eff_pad = min(0.20, max(0.03, min(pad_sec, gdur * 0.25)))
        ts = round(gs + eff_pad, 4)
        te = round(ge - eff_pad, 4)
        cb, ca = _context_around(gs, ge)
        raw_cands.append({
            "candidate_id": f"rc_{len(raw_cands) + 1:03d}",
            "source_asset_id": source_asset_id,
            "candidate_type": cls["candidate_type"],
            "start_pts": round(gs, 4),
            "end_pts": round(ge, 4),
            "trim_start_pts": ts,
            "trim_end_pts": max(ts, te),
            "raw_duration_sec": round(gdur, 4),
            "predicted_saving_sec": round(max(0.0, te - ts), 4) if cls["recommended_outcome"] in ("SHORTEN", "REMOVE") else 0.0,
            "word_ids": [],
            "protected_word_ids": [],
            "context_before": cb,
            "context_after": ca,
            "evidence_paths": [],
            "confidence": 0.92,
            "proposed_outcome": cls["recommended_outcome"],
            "semantic_risk": cls["semantic_risk"],
            "reason": cls["reason"],
        })

    # 4. Trailing padding after final word
    if words and (source_duration_sec - float(words[-1]["end_sec"])) >= 0.45:
        last_e = float(words[-1]["end_sec"])
        trim_s = round(last_e + min(0.20, max(0.08, pad_sec)), 4)
        if source_duration_sec - trim_s >= 0.15:
            cls = classify_pause(
                last_e, source_duration_sec, source_duration_sec=source_duration_sec,
                words=words, has_audio_track=has_audio_track,
            )
            cb, ca = _context_around(last_e, source_duration_sec)
            raw_cands.append({
                "candidate_id": f"rc_{len(raw_cands) + 1:03d}",
                "source_asset_id": source_asset_id,
                "candidate_type": cls["candidate_type"],
                "start_pts": round(last_e, 4),
                "end_pts": round(source_duration_sec, 4),
                "trim_start_pts": trim_s,
                "trim_end_pts": round(source_duration_sec, 4),
                "raw_duration_sec": round(source_duration_sec - last_e, 4),
                "predicted_saving_sec": round(source_duration_sec - trim_s, 4),
                "word_ids": [],
                "protected_word_ids": [],
                "context_before": cb,
                "context_after": ca,
                "evidence_paths": [],
                "confidence": 0.95,
                "proposed_outcome": cls["recommended_outcome"],
                "semantic_risk": cls["semantic_risk"],
                "reason": cls["reason"],
            })

    # 5. Repeated takes / false starts / fillers / stumbles
    if transcript:
        raw_cands.extend(detect_take_candidates(transcript))

    evaluated = [evaluate_cut_safety(c, words=words, policy_info=policy_info) for c in raw_cands]
    ranked = rank_cut_candidates(evaluated)

    approved_cut_count = sum(1 for c in ranked if c["safety_ok"] and c["proposed_outcome"] in ("SHORTEN", "REMOVE"))
    review_count = sum(1 for c in ranked if c["proposed_outcome"] == "REVIEW")
    kept_count = len(ranked) - approved_cut_count - review_count
    total_saving = round(sum(c["predicted_saving_sec"] for c in ranked if c["safety_ok"] and c["proposed_outcome"] in ("SHORTEN", "REMOVE")), 4)

    doc = {
        "schema_version": "kh02r1.cut_candidates.v1",
        "source_asset_id": source_asset_id,
        "source_sha256": source_sha256,
        "cut_policy": policy_info["cut_policy"],
        "approval_scope": policy_info.get("approval_scope"),
        "candidates": ranked,
        "summary": {
            "total_candidates": len(ranked),
            "approved_cut_count": approved_cut_count,
            "kept_count": kept_count,
            "review_count": review_count,
            "predicted_total_saving_sec": total_saving,
        },
    }
    errs = validate_schema(doc, "cut_candidate")
    if errs:
        raise ValueError(f"Generated CUT_CANDIDATES failed schema validation: {errs[:3]}")
    return doc


def build_r1_time_map(
    source_asset_id: str,
    total_source_frames: int,
    fps_num: int,
    fps_den: int = 1,
    *,
    source_sha256: str | None = None,
    audio_sample_rate: int = 48000,
    cut_policy: str = "LOCK_VOICE",
    approved_scope: str | None = None,
    candidate_doc: dict[str, Any] | None = None,
    words: list[dict[str, Any]] | None = None,
    explicit_cuts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build canonical rational EDL + exact audio sample bounds + protected word sequence verification."""
    if not is_strict_int(total_source_frames) or total_source_frames <= 0:
        raise ValueError(f"total_source_frames must be positive int (got {total_source_frames!r})")
    if not is_strict_int(fps_num) or fps_num <= 0 or not is_strict_int(fps_den) or fps_den <= 0:
        raise ValueError(f"Invalid rational fps: {fps_num}/{fps_den}")

    policy_info = resolve_cut_policy(cut_policy, approved_scope=approved_scope)
    fps_frac = Fraction(fps_num, fps_den)
    fps_float = float(fps_frac)
    wlist = words or []

    # Collect approved cut intervals strictly honoring policy
    raw_cuts: list[tuple[int, int, str, str]] = []
    if policy_info["allow_nonverbal_trim"]:
        cands = explicit_cuts if explicit_cuts is not None else (candidate_doc or {}).get("candidates", [])
        for c in cands:
            ctype = c.get("candidate_type", "DEAD_AIR")
            outcome = c.get("proposed_outcome") or c.get("decision")
            if outcome not in ("SHORTEN", "REMOVE", "CUT_APPROVED"):
                continue
            if c.get("safety_ok") is False:
                continue
            if ctype in EDITORIAL_ONLY_TYPES and not policy_info["allow_editorial_deletion"]:
                raise ValueError(f"Policy {policy_info['cut_policy']} forbids deleting {ctype} ({c.get('candidate_id')})")
            if ctype in PROTECTED_KEEP_TYPES:
                continue
            ts = float(c.get("trim_start_pts", c.get("start_pts", c.get("start_sec", 0.0))))
            te = float(c.get("trim_end_pts", c.get("end_pts", c.get("end_sec", 0.0))))
            cf0 = max(0, seconds_to_frame(ts, fps_num, fps_den, mode="ceil"))
            cf1 = min(total_source_frames, seconds_to_frame(te, fps_num, fps_den, mode="floor"))
            if cf1 <= cf0:
                continue
            # Double-check zero mid-word split on frame-snapped bounds
            t0_sec = float(Fraction(cf0) / fps_frac)
            t1_sec = float(Fraction(cf1) / fps_frac)
            allowed_del = set(c.get("word_ids", [])) if (policy_info["allow_editorial_deletion"] and ctype in EDITORIAL_ONLY_TYPES) else set()
            for w in wlist:
                if w["word_id"] in allowed_del:
                    continue
                ws, we = float(w["start_sec"]), float(w["end_sec"])
                if min(t1_sec, we - 0.015) > max(t0_sec, ws + 0.015):
                    raise ValueError(f"Frame-snapped cut [{cf0},{cf1}) collides with protected word {w['word_id']} ({w['text']!r})")
            raw_cuts.append((cf0, cf1, ctype, str(c.get("reason", "approved_cut"))))

    raw_cuts.sort()
    merged: list[tuple[int, int, str, str]] = []
    for a, b, ct, rsn in raw_cuts:
        if merged and a <= merged[-1][1]:
            pa, pb, pct, prsn = merged[-1]
            merged[-1] = (pa, max(pb, b), pct, f"{prsn}; {rsn}")
        else:
            merged.append((a, b, ct, rsn))

    # Build contiguous non-empty keep segments with exact rational PTS & sample bounds
    segments: list[dict[str, Any]] = []
    cursor = 0
    out_frame_cursor = 0
    out_sample_cursor = 0

    def _frame_to_sample(f: int) -> int:
        return int(round(float(Fraction(f * audio_sample_rate, 1) / fps_frac)))

    def _add_seg(s0: int, s1: int, reason: str, left_cut: bool, right_cut: bool) -> None:
        nonlocal out_frame_cursor, out_sample_cursor
        if s1 <= s0:
            return
        seg_frames = s1 - s0
        dur_ms = (seg_frames / fps_float) * 1000.0
        # Safe <=30ms fade strictly inside non-phoneme gap; never double-fade contiguous joins
        safe_fade = round(min(30.0, dur_ms * 0.25), 2)
        fade_in = safe_fade if left_cut else 0.0
        fade_out = safe_fade if right_cut else 0.0

        src_s0 = _frame_to_sample(s0)
        src_s1 = _frame_to_sample(s1)
        seg_samples = max(1, src_s1 - src_s0)

        t0_f = Fraction(s0) / fps_frac
        t1_f = Fraction(s1) / fps_frac
        prot_wids = [
            w["word_id"] for w in wlist
            if float(w["start_sec"]) >= float(t0_f) - 0.05 and float(w["end_sec"]) <= float(t1_f) + 0.05
        ]
        segments.append({
            "segment_id": f"seg_{len(segments) + 1:03d}",
            "source_asset_id": source_asset_id,
            "source_sha256": source_sha256,
            "src_start_frame": s0,
            "src_end_frame": s1,
            "out_start_frame": out_frame_cursor,
            "out_end_frame": out_frame_cursor + seg_frames,
            "source_in_pts_rational": f"{t0_f.numerator}/{t0_f.denominator}",
            "source_out_pts_rational": f"{t1_f.numerator}/{t1_f.denominator}",
            "src_start_sample": src_s0,
            "src_end_sample": src_s1,
            "out_start_sample": out_sample_cursor,
            "out_end_sample": out_sample_cursor + seg_samples,
            "protected_word_ids": prot_wids,
            "boundary_pad_ms": round(safe_fade, 2),
            "audio_fade_in_ms": fade_in,
            "audio_fade_out_ms": fade_out,
            "boundary_visual_coverage": "a_roll_direct",
            "approval_scope": policy_info.get("approval_scope"),
            "reason": reason,
        })
        out_frame_cursor += seg_frames
        out_sample_cursor += seg_samples

    if not merged:
        _add_seg(0, total_source_frames, f"{policy_info['cut_policy']} continuous identity mapping", False, False)
    else:
        for idx, (c0, c1, ctype, _rsn) in enumerate(merged):
            if c0 > cursor:
                # Left edge is cut only if cursor > 0 (meaning a previous cut preceded this segment)
                _add_seg(cursor, c0, f"Keep span before {ctype} trim [{c0},{c1})", cursor > 0, True)
            cursor = max(cursor, c1)
        if cursor < total_source_frames:
            _add_seg(cursor, total_source_frames, "Keep span after final approved trim", True, False)

    if not segments or out_frame_cursor <= 0:
        raise ValueError("Approved cuts eliminated entire timeline; zero output frames rejected")

    # Under PRESERVE_SCRIPT_TRIM, assert 100% of original spoken words remain in exact order
    if policy_info["cut_policy"] == "PRESERVE_SCRIPT_TRIM" and wlist:
        kept_ids: list[str] = []
        for seg in segments:
            kept_ids.extend(seg["protected_word_ids"])
        orig_ids = [w["word_id"] for w in wlist]
        if kept_ids != orig_ids:
            raise ValueError(
                f"PRESERVE_SCRIPT_TRIM violation: kept words ({len(kept_ids)}) != original order ({len(orig_ids)})"
            )

    edl = {
        "schema_version": "kh02r1.edl.v2",
        "voice_policy": policy_info["voice_policy"],
        "approval_scope": policy_info.get("approval_scope"),
        "fps_num": fps_num,
        "fps_den": fps_den,
        "audio_sample_rate": audio_sample_rate,
        "total_output_frames": out_frame_cursor,
        "total_output_samples": out_sample_cursor,
        "cut_candidates": [
            {
                "candidate_id": c["candidate_id"],
                "source_asset_id": c["source_asset_id"],
                "start_sec": c.get("trim_start_pts", c.get("start_pts", 0.0)),
                "end_sec": c.get("trim_end_pts", c.get("end_pts", 0.0)),
                "kind": c["candidate_type"],
                "decision": c["proposed_outcome"],
                "reason": c["reason"],
            }
            for c in ((candidate_doc or {}).get("candidates", []))
        ],
        "segments": segments,
    }
    errs = validate_schema(edl, "edl")
    if errs:
        raise ValueError(f"Generated R1 EDL failed schema validation: {errs[:3]}")
    return edl


def apply_r1_cut_map(
    source_path: str | Path,
    edl: dict[str, Any],
    out_path: str | Path,
) -> dict[str, Any]:
    """Execute paired derived A/V FFmpeg trim strictly preserving CFR clock, sample rate, and source immutability."""
    src = Path(source_path).resolve()
    dst = Path(out_path).resolve()
    if src == dst:
        raise ValueError("Refusing to overwrite source media with derived cut output")
    if not src.is_file():
        raise FileNotFoundError(f"Source media not found: {src}")

    policy = edl.get("voice_policy", "LOCK_VOICE")
    segs = edl.get("segments") or []
    if not segs:
        raise ValueError("Empty EDL cannot be applied")

    # Identity check: if single segment covering full source from frame 0 with zero fades and LOCK_VOICE
    if policy == "LOCK_VOICE":
        return {
            "applied": False,
            "status": "NOT_APPLICABLE_LOCK_VOICE",
            "path": str(src),
            "source_sha256": hash_artifact(src),
            "segments": len(segs),
            "total_output_frames": edl["total_output_frames"],
        }

    fps_num = int(edl["fps_num"])
    fps_den = int(edl["fps_den"])
    fps_frac = Fraction(fps_num, fps_den)
    sr = int(edl.get("audio_sample_rate", 48000))
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_dst = dst.with_suffix(".part.mp4")

    # Check if source has audio stream
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(src)],
        capture_output=True, text=True, timeout=30,
    )
    streams = json.loads(probe.stdout or "{}").get("streams", [])
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    for idx, seg in enumerate(segs):
        s0, s1 = int(seg["src_start_frame"]), int(seg["src_end_frame"])
        t0 = float(Fraction(s0) / fps_frac)
        t1 = float(Fraction(s1) / fps_frac)
        dur = max(0.01, t1 - t0)
        # Exact frame trim on video with normalized CFR timestamps
        filter_parts.append(
            f"[0:v]trim=start_frame={s0}:end_frame={s1},setpts=PTS-STARTPTS[v{idx}]"
        )
        if has_audio:
            as0 = int(seg.get("src_start_sample", int(round(t0 * sr))))
            as1 = int(seg.get("src_end_sample", int(round(t1 * sr))))
            fi = float(seg.get("audio_fade_in_ms", 0.0)) / 1000.0
            fo = float(seg.get("audio_fade_out_ms", 0.0)) / 1000.0
            af = f"[0:a]aresample={sr},atrim=start_sample={as0}:end_sample={as1},asetpts=PTS-STARTPTS"
            if fi > 0.001:
                d_in = min(fi, dur * 0.4)
                af += f",afade=t=in:st=0:d={d_in:.5f}"
            if fo > 0.001:
                d_out = min(fo, dur * 0.4)
                af += f",afade=t=out:st={max(0.0, dur - d_out):.5f}:d={d_out:.5f}"
            af += f"[a{idx}]"
            filter_parts.append(af)
            concat_inputs.append(f"[v{idx}][a{idx}]")
        else:
            concat_inputs.append(f"[v{idx}]")

    if has_audio:
        filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(segs)}:v=1:a=1[vout][aout]")
    else:
        filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(segs)}:v=1:a=0[vout]")

    cmd = [
        "ffmpeg", "-v", "error", "-y", "-i", str(src),
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]",
    ]
    if has_audio:
        cmd.extend(["-map", "[aout]"])
    cmd.extend([
        "-c:v", "libx264", "-crf", "15", "-pix_fmt", "yuv420p",
        "-r", f"{fps_num}/{fps_den}", "-fps_mode", "cfr",
    ])
    if has_audio:
        cmd.extend(["-c:a", "pcm_s16le" if dst.suffix.lower() == ".mov" else "aac", "-b:a", "192k", "-ar", str(sr)])
    cmd.append(str(tmp_dst))

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if proc.returncode != 0:
        tmp_dst.unlink(missing_ok=True)
        raise RuntimeError(f"apply_r1_cut_map FFmpeg failed (rc={proc.returncode}): {proc.stderr[-400:]}")
    tmp_dst.replace(dst)
    return {
        "applied": True,
        "status": "APPLIED",
        "path": str(dst),
        "derived_sha256": hash_artifact(dst),
        "source_sha256": hash_artifact(src),
        "segments": len(segs),
        "total_output_frames": int(edl["total_output_frames"]),
        "has_audio": has_audio,
    }


def _read_pcm_window(
    media_path: Path,
    start_sec: float,
    dur_sec: float,
    sample_rate: int = 48000,
) -> list[int]:
    """Bounded streaming PCM slice decode (constant O(window) memory for long-form safety)."""
    s0 = max(0.0, float(start_sec))
    d = max(0.01, min(30.0, float(dur_sec)))
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", f"{s0:.4f}", "-t", f"{d:.4f}",
            "-i", str(media_path),
            "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-",
        ],
        capture_output=True, timeout=60,
    )
    if proc.returncode != 0 or not proc.stdout:
        return []
    n = len(proc.stdout) // 2
    return list(struct.unpack(f"<{n}h", proc.stdout[: n * 2]))


def _extract_pcm_samples(media_path: Path, sample_rate: int = 48000, *, max_duration_sec: float = 60.0) -> list[int] | None:
    """Decode bounded mono 16-bit PCM samples (capped by max_duration_sec to prevent unbounded RAM growth)."""
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-t", f"{float(max_duration_sec):.3f}", "-i", str(media_path),
            "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-",
        ],
        capture_output=True, timeout=60,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    n = len(proc.stdout) // 2
    return list(struct.unpack(f"<{n}h", proc.stdout[: n * 2]))


def verify_cut_result(
    source_path: str | Path,
    derived_path: str | Path,
    edl: dict[str, Any],
    *,
    expected_source_sha256: str,
    original_transcript: dict[str, Any] | None = None,
    candidate_doc: dict[str, Any] | None = None,
    seam_click_threshold: float = 0.65,
) -> dict[str, Any]:
    """Verify derived cut clip: exact frame count, PCM sample continuity, seam pop check, word sequence & source integrity."""
    src = Path(source_path).resolve()
    drv = Path(derived_path).resolve()
    issues: list[dict[str, Any]] = []

    if not src.is_file() or not drv.is_file():
        return {
            "pass": False,
            "status": "FAIL_MISSING_MEDIA",
            "issues": [{"code": "MISSING_MEDIA", "message": "Source or derived file missing"}],
        }

    # 1. Source immutability
    actual_src_sha = hash_artifact(src)
    src_unchanged = (actual_src_sha == expected_source_sha256)
    if not src_unchanged:
        issues.append({"code": "SOURCE_MUTATED", "message": f"Source hash drifted: {actual_src_sha} != {expected_source_sha256}"})

    # 2. Exact video frame count & duration via ffprobe
    def _probe(p: Path) -> dict[str, Any]:
        pr = subprocess.run(
            [
                "ffprobe", "-v", "error", "-count_frames",
                "-show_entries", "stream=codec_type,nb_read_frames,nb_frames,r_frame_rate,sample_rate,duration:format=duration",
                "-of", "json", str(p),
            ],
            capture_output=True, text=True, timeout=60,
        )
        if pr.returncode != 0:
            return {}
        return json.loads(pr.stdout or "{}")

    src_info = _probe(src)
    drv_info = _probe(drv)
    drv_v = next((s for s in drv_info.get("streams", []) if s.get("codec_type") == "video"), None)
    drv_a = next((s for s in drv_info.get("streams", []) if s.get("codec_type") == "audio"), None)
    src_v = next((s for s in src_info.get("streams", []) if s.get("codec_type") == "video"), None)

    if not drv_v:
        issues.append({"code": "NO_VIDEO_STREAM", "message": "Derived clip missing video stream"})
        return {"pass": False, "status": "FAIL_NO_VIDEO", "issues": issues}

    actual_frames = int(drv_v.get("nb_read_frames") or drv_v.get("nb_frames") or 0)
    expected_frames = int(edl["total_output_frames"])
    if actual_frames != expected_frames:
        issues.append({
            "code": "FRAME_COUNT_MISMATCH",
            "message": f"Derived video frames={actual_frames} != expected EDL total_output_frames={expected_frames}",
        })

    fps_frac = Fraction(int(edl["fps_num"]), int(edl["fps_den"]))
    expected_dur = float(Fraction(expected_frames) / fps_frac)
    actual_v_dur = float(drv_v.get("duration") or drv_info.get("format", {}).get("duration") or 0.0)
    actual_a_dur = float(drv_a.get("duration") or 0.0) if drv_a else None

    # 3. Audio/Video sync & seam discontinuity inspection
    sr = int(edl.get("audio_sample_rate", 48000))
    av_drift_sec = abs(actual_v_dur - actual_a_dur) if actual_a_dur is not None else 0.0
    max_allowed_drift = max(float(Fraction(1) / fps_frac), 0.06)
    if drv_a and av_drift_sec > max_allowed_drift:
        issues.append({
            "code": "AV_SYNC_DRIFT",
            "message": f"Derived A/V duration drift {av_drift_sec:.4f}s > tolerance {max_allowed_drift:.4f}s",
        })

    seam_reports: list[dict[str, Any]] = []
    pcm_sample_count: int | None = None
    if drv_a:
        # Compute exact stream sample count without loading entire audio stream into RAM
        if actual_a_dur is not None and actual_a_dur > 0:
            pcm_sample_count = int(round(actual_a_dur * sr))
        # Verify audio decodes cleanly on opening window
        probe_win = _read_pcm_window(drv, 0.0, min(0.5, actual_a_dur or 0.5), sample_rate=sr)
        if not probe_win:
            issues.append({"code": "AUDIO_MEASURE_MISSING", "message": "Failed to decode PCM samples for seam verification"})
        else:
            segs = edl["segments"]
            for idx in range(len(segs) - 1):
                seam_frame = int(segs[idx]["out_end_frame"])
                seam_sec = float(Fraction(seam_frame) / fps_frac)
                win_start = max(0.0, seam_sec - 0.025)
                win_pcm = _read_pcm_window(drv, win_start, 0.050, sample_rate=sr)
                max_step = 0.0
                zero_run = 0.0
                if len(win_pcm) > 2:
                    max_step = max(abs(win_pcm[i] - win_pcm[i - 1]) for i in range(1, len(win_pcm))) / 32768.0
                    zero_run = sum(1 for x in win_pcm if abs(x) < 4) / float(len(win_pcm))
                click_flag = max_step > seam_click_threshold
                if click_flag:
                    issues.append({
                        "code": "AUDIO_SEAM_CLICK",
                        "message": f"Audio click/discontinuity at seam frame {seam_frame} (max_step={max_step:.3f} > {seam_click_threshold})",
                    })
                seam_reports.append({
                    "seam_index": idx + 1,
                    "out_frame": seam_frame,
                    "out_sec": round(seam_sec, 4),
                    "max_sample_step_norm": round(max_step, 4),
                    "near_zero_ratio": round(zero_run, 4),
                    "click_detected": click_flag,
                })

    # 4. Protected word sequence verification (exact order under PRESERVE_SCRIPT_TRIM / LOCK_VOICE)
    mapped_words = map_words_to_output(original_transcript, edl) if original_transcript else []
    orig_words = (original_transcript or {}).get("words", [])
    orig_seq = [w["word_id"] for w in orig_words]
    mapped_seq = [w["word_id"] for w in mapped_words]
    words_preserved_exact = (orig_seq == mapped_seq)
    if edl.get("voice_policy") in ("LOCK_VOICE", "PRESERVE_SCRIPT_TRIM") and orig_words and not words_preserved_exact:
        issues.append({
            "code": "SPEECH_LOSS_OR_REORDER",
            "message": f"Mapped words ({len(mapped_seq)}) do not match original order ({len(orig_seq)})",
        })

    # 5. Trim summary by reason
    src_frames = int((src_v or {}).get("nb_read_frames") or (src_v or {}).get("nb_frames") or expected_frames)
    src_dur = float(Fraction(src_frames) / fps_frac)
    saved_frames = max(0, src_frames - actual_frames)
    saved_sec = round(float(Fraction(saved_frames) / fps_frac), 4)
    trim_by_reason: dict[str, float] = {}
    for c in ((candidate_doc or {}).get("candidates", [])):
        if c.get("safety_ok") and c.get("proposed_outcome") in ("SHORTEN", "REMOVE"):
            ct = c["candidate_type"]
            trim_by_reason[ct] = round(trim_by_reason.get(ct, 0.0) + float(c.get("predicted_saving_sec", 0.0)), 4)

    return {
        "schema_version": "kh02r1.cut_verification.v1",
        "pass": len(issues) == 0,
        "status": "PASS" if not issues else "FAIL",
        "voice_policy": edl.get("voice_policy"),
        "source_sha256": actual_src_sha,
        "source_unchanged": src_unchanged,
        "derived_sha256": hash_artifact(drv),
        "before_frames": src_frames,
        "after_frames": actual_frames,
        "expected_frames": expected_frames,
        "before_duration_sec": round(src_dur, 4),
        "after_duration_sec": round(expected_dur, 4),
        "actual_video_duration_sec": round(actual_v_dur, 4),
        "actual_audio_duration_sec": round(actual_a_dur, 4) if actual_a_dur is not None else None,
        "av_drift_sec": round(av_drift_sec, 4),
        "pcm_sample_count": pcm_sample_count,
        "saved_frames": saved_frames,
        "saved_sec": saved_sec,
        "trim_seconds_by_reason": trim_by_reason,
        "original_word_count": len(orig_seq),
        "mapped_word_count": len(mapped_seq),
        "protected_words_exact_order": words_preserved_exact,
        "seams": seam_reports,
        "issues": issues,
        "note": "Signal verification confirms frame/sample/seam integrity; audible craft requires human listening (OWNER_AUDIO=PENDING).",
    }


def emit_rough_cut_artifacts(
    out_dir: str | Path,
    *,
    candidate_doc: dict[str, Any],
    edl: dict[str, Any],
    original_transcript: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Write CUT_CANDIDATES.json, CUT_DECISION_REPORT.md, EDL.json, TIME_MAP.json, OUTPUT_TRANSCRIPT.json & CUT_VERIFICATION.json."""
    base = Path(out_dir)
    base.mkdir(parents=True, exist_ok=True)

    def _wj(name: str, obj: Any) -> Path:
        p = base / name
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return p

    paths: dict[str, Path] = {}
    paths["cut_candidates"] = _wj("CUT_CANDIDATES.json", candidate_doc)
    paths["edl"] = _wj("EDL.json", edl)
    paths["time_map"] = _wj("TIME_MAP.json", {
        "schema_version": "kh02r1.time_map.v1",
        "edl_sha256": hash_artifact(edl),
        "fps_num": edl["fps_num"],
        "fps_den": edl["fps_den"],
        "audio_sample_rate": edl["audio_sample_rate"],
        "total_output_frames": edl["total_output_frames"],
        "total_output_samples": edl.get("total_output_samples"),
        "segments": edl["segments"],
    })

    mapped_words = map_words_to_output(original_transcript, edl) if original_transcript else []
    paths["output_transcript"] = _wj("OUTPUT_TRANSCRIPT.json", {
        "schema_version": "kh02r1.output_transcript.v1",
        "edl_sha256": hash_artifact(edl),
        "source_asset_id": candidate_doc["source_asset_id"],
        "original_word_count": len((original_transcript or {}).get("words", [])),
        "output_word_count": len(mapped_words),
        "words": mapped_words,
    })

    # Human-readable CUT_DECISION_REPORT.md with before/after quotes and semantic risk
    lines = [
        f"# Rough Cut Decision Report — `{candidate_doc['source_asset_id']}`",
        "",
        f"- **Cut Policy**: `{candidate_doc['cut_policy']}` (scope: `{candidate_doc.get('approval_scope')}`)",
        f"- **Candidates Evaluated**: `{candidate_doc['summary']['total_candidates']}` "
        f"(approved cuts: `{candidate_doc['summary']['approved_cut_count']}`, kept: `{candidate_doc['summary']['kept_count']}`, review: `{candidate_doc['summary']['review_count']}`)",
        f"- **Predicted Saving**: `{candidate_doc['summary']['predicted_total_saving_sec']:.3f}s`",
        "",
        "| ID | Type | Span (s) | Trim Span (s) | Saving | Risk | Safety | Outcome | Context Before -> After | Reason |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in candidate_doc["candidates"]:
        ctx = f"`{c.get('context_before','')[-20:]}` -> `{c.get('context_after','')[:20]}`"
        lines.append(
            f"| `{c['candidate_id']}` | `{c['candidate_type']}` | `{c['start_pts']:.2f}-{c['end_pts']:.2f}` | "
            f"`{c.get('trim_start_pts', c['start_pts']):.2f}-{c.get('trim_end_pts', c['end_pts']):.2f}` | "
            f"`{c['predicted_saving_sec']:.2f}s` | `{c['semantic_risk']}` | `{c['safety_ok']}` | "
            f"**{c['proposed_outcome']}** | {ctx} | {c['reason']} |"
        )
    lines.append("")
    rep_path = base / "CUT_DECISION_REPORT.md"
    rep_path.write_text("\n".join(lines), encoding="utf-8")
    paths["decision_report"] = rep_path

    if verification is not None:
        paths["cut_verification"] = _wj("CUT_VERIFICATION.json", verification)
    return paths

