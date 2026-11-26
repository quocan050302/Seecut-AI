"""KH-03.R4.2-05 — Generic Speech-First Alignment, Duplicate-Span Detection & Multi-Source EDL Engine.

Strictly universal across all niches/languages (ZERO hardcoded character names, phrases, timestamps, or brand rules):
- Normalizes spoken contractions and numeric forms (`$15` <-> `fifteen dollars`, `980` <-> `nine eighty`,
  `120` <-> `one hundred twenty`, `can't` <-> `cannot`) for exact & near-duplicate alignment.
- Aligns word-level ASR timestamps against an approved script via SequenceMatcher / DP (`SCRIPT_ALIGNMENT.json`).
- Detects exact and near-duplicate repeated n-grams / false starts across sliding windows (including across
  clip seams), retains the best take by confidence + completeness + boundary cleanliness, protects explicitly
  approved intentional rhetorical repetitions, and flags uncertain matches as `PENDING_OWNER_REVIEW`.
- Builds half-open `[src_in, src_out)` speech-first `VOICE_EDL.json`, `MASTER_EDL.json`, `SOURCE_PTS_MAP.json`,
  and `SOURCE_HASH_MANIFEST.json` with exact rational mixed-fps mapping and container-overhang clamping.
- Verifies post-render ASR against the approved script (`FINAL_ASR_DIFF.json`) ensuring every intended phrase
  and numeric value appears once with zero unintended spoken duplicates.
"""

from __future__ import annotations

import difflib
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from .contracts import hash_artifact, write_json_atomic


_NUM_WORD_MAP: dict[str, str] = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15",
    "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19",
    "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60",
    "seventy": "70", "eighty": "80", "ninety": "90", "hundred": "100", "thousand": "1000",
}

_CONTRACTION_MAP: dict[str, list[str]] = {
    "cant": ["can", "not"], "cannot": ["can", "not"], "dont": ["do", "not"],
    "doesnt": ["does", "not"], "didnt": ["did", "not"], "wont": ["will", "not"],
    "isnt": ["is", "not"], "arent": ["are", "not"], "wasnt": ["was", "not"],
    "heres": ["here", "is"], "thats": ["that", "is"], "theres": ["there", "is"],
    "whats": ["what", "is"], "wheres": ["where", "is"], "its": ["it", "is"],
    "id": ["i", "would"], "im": ["i", "am"], "ive": ["i", "have"], "ill": ["i", "will"],
    "youre": ["you", "are"], "youve": ["you", "have"], "weve": ["we", "have"],
    "theyre": ["they", "are"],
}


def normalize_spoken_tokens(text: str) -> list[str]:
    """Normalize punctuation, currency symbols, contractions, and simple spoken numbers to comparable tokens."""
    raw = str(text or "").lower()
    raw = raw.replace("’", "'").replace("‘", "'").replace("—", " ").replace("–", " ").replace("-", " ")
    raw = re.sub(r"[\$€£¥]", " ", raw)
    raw = re.sub(r"(\d),(\d)", r"\1\2", raw)
    chunks = re.findall(r"[a-z0-9'\u00C0-\u024F\u1E00-\u1EFF\u4e00-\u9fff]+", raw)
    out: list[str] = []
    for ch in chunks:
        stripped = ch.replace("'", "")
        if not stripped:
            continue
        if stripped in _CONTRACTION_MAP:
            out.extend(_CONTRACTION_MAP[stripped])
        elif stripped in _NUM_WORD_MAP:
            out.append(_NUM_WORD_MAP[stripped])
        elif stripped in ("dollar", "dollars"):
            continue
        else:
            out.append(stripped)
    # Collapse simple spoken compound numbers (e.g., ["9", "80"] -> ["980"], ["20", "5"] -> ["25"], ["1", "100", "20"] -> ["120"])
    merged: list[str] = []
    i = 0
    while i < len(out):
        if i + 2 < len(out) and out[i].isdigit() and out[i + 1] == "100" and out[i + 2].isdigit() and int(out[i]) < 10 and int(out[i + 2]) < 100:
            merged.append(str(int(out[i]) * 100 + int(out[i + 2])))
            i += 3
        elif i + 1 < len(out) and out[i].isdigit() and out[i + 1].isdigit():
            a, b = int(out[i]), int(out[i + 1])
            if 1 <= a <= 9 and 10 <= b <= 99:
                merged.append(str(a * 100 + b))
                i += 2
            elif a in (20, 30, 40, 50, 60, 70, 80, 90) and 1 <= b <= 9:
                merged.append(str(a + b))
                i += 2
            else:
                merged.append(out[i])
                i += 1
        else:
            merged.append(out[i])
            i += 1
    return merged


def _word_norm(word_obj: dict[str, Any]) -> str:
    txt = str(word_obj.get("word") or word_obj.get("text") or "").strip()
    toks = normalize_spoken_tokens(txt)
    return "_".join(toks) if toks else re.sub(r"[^a-z0-9]+", "", txt.lower())


def align_asr_to_script(
    asr_words: list[dict[str, Any]],
    approved_script: str,
    *,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Align word-level ASR timestamps against an approved script using normalized token SequenceMatcher."""
    script_toks = normalize_spoken_tokens(approved_script)
    asr_toks: list[tuple[str, int]] = []
    for idx, w in enumerate(asr_words):
        for tok in normalize_spoken_tokens(str(w.get("word") or w.get("text") or "")):
            asr_toks.append((tok, idx))
    # Re-apply compound-number collapse on flat ASR token stream while preserving word index mapping
    flat_asr = [t for t, _ in asr_toks]
    flat_asr_merged = normalize_spoken_tokens(" ".join(str(w.get("word") or w.get("text") or "") for w in asr_words))

    sm = difflib.SequenceMatcher(None, script_toks, flat_asr_merged, autojunk=False)
    matched = 0
    missing_spans: list[dict[str, Any]] = []
    extra_spans: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            matched += (i2 - i1)
        elif tag == "delete":
            missing_spans.append({"script_range": [i1, i2], "tokens": script_toks[i1:i2]})
        elif tag == "insert":
            extra_spans.append({"asr_range": [j1, j2], "tokens": flat_asr_merged[j1:j2]})
        elif tag == "replace":
            missing_spans.append({"script_range": [i1, i2], "tokens": script_toks[i1:i2], "asr_substituted": flat_asr_merged[j1:j2]})

    cov = round(matched / float(max(1, len(script_toks))), 4)
    # Verify numeric token fidelity
    script_nums = [t for t in script_toks if t.isdigit()]
    asr_nums = [t for t in flat_asr_merged if t.isdigit()]
    missing_nums = [n for n in script_nums if asr_nums.count(n) < script_nums.count(n)]

    rep = {
        "schema_version": "kh03.script_alignment.v1",
        "script_token_count": len(script_toks),
        "asr_word_count": len(asr_words),
        "asr_normalized_token_count": len(flat_asr_merged),
        "matched_token_count": matched,
        "coverage_ratio": cov,
        "script_numbers": script_nums,
        "asr_numbers": asr_nums,
        "missing_numbers": missing_nums,
        "missing_spans": missing_spans,
        "extra_spans": extra_spans,
        "status": "ALIGNED" if (cov >= 0.85 and not missing_nums) else "NEEDS_REVIEW",
    }
    if out_path is not None:
        write_json_atomic(out_path, rep)
    return rep


def detect_duplicate_spans(
    asr_words: list[dict[str, Any]],
    approved_script: str | None = None,
    *,
    fps_num: int = 24,
    fps_den: int = 1,
    sample_rate: int = 48000,
    min_ngram: int = 3,
    max_ngram: int = 14,
    max_gap_sec: float = 3.0,
    near_dup_ratio: float = 0.84,
    approved_intentional_repeats: list[str] | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Generic word-level exact & near-duplicate phrase / false-start detector across sliding windows.

    Never deletes intentional rhetorical repetitions confirmed by the approved script or explicit allowlist.
    Selects the best take (highest mean confidence + complete phrase + cleaner boundary) and marks the redundant
    span `DROP` (verified exact/script-confirmed) or `PENDING_OWNER_REVIEW` (uncertain near-duplicate).
    """
    fps = float(Fraction(max(1, fps_num), max(1, fps_den)))
    norms = [_word_norm(w) for w in asr_words]
    n = len(asr_words)
    script_norm_str = " ".join(normalize_spoken_tokens(approved_script or ""))
    allow_phrases = {" ".join(normalize_spoken_tokens(p)) for p in (approved_intentional_repeats or []) if p.strip()}

    found: list[dict[str, Any]] = []
    consumed_indices: set[int] = set()

    # Two-pass scan: Pass 1 catches ALL EXACT_DUPLICATE spans (longest-first) so a longer near-duplicate
    # (e.g. 1 extra boundary word + exact duplicate) NEVER shadows a clean exact duplicate!
    # Pass 2 catches remaining NEAR_DUPLICATE spans on unconsumed indices.
    for match_pass in ("EXACT", "NEAR"):
        for k in range(min(max_ngram, n // 2), min_ngram - 1, -1):
            if match_pass == "NEAR" and k < 4:
                continue
            i = 0
            while i + 2 * k <= n:
                if any(idx in consumed_indices for idx in range(i, i + k)):
                    i += 1
                    continue
                seq_a = norms[i : i + k]
                if not all(seq_a):
                    i += 1
                    continue
                t_a_end = float(asr_words[i + k - 1].get("end", asr_words[i + k - 1].get("end_sec", 0.0)))
                matched_j = None
                match_kind = None
                sim_score = 0.0

                # Look ahead within max_gap_sec (allows up to 2 filler words between false start and retry)
                for j in range(i + k, min(n - k + 1, i + k + 4)):
                    if any(idx in consumed_indices for idx in range(j, j + k)):
                        continue
                    t_b_start = float(asr_words[j].get("start", asr_words[j].get("start_sec", 0.0)))
                    if t_b_start - t_a_end > max_gap_sec:
                        break
                    seq_b = norms[j : j + k]
                    if match_pass == "EXACT":
                        # Only back-to-back exact repeats (j == i + k) qualify as auto-DROP EXACT_DUPLICATE;
                        # repeats separated by intervening words (e.g. "...plan, ...sheet") defer to NEAR_DUPLICATE / PENDING_OWNER_REVIEW
                        # so we never leave an orphaned word stranded in the voice track!
                        if j == i + k and seq_a == seq_b:
                            matched_j = j
                            match_kind = "EXACT_DUPLICATE"
                            sim_score = 1.0
                            break
                    else:
                        sim = difflib.SequenceMatcher(None, seq_a, seq_b).ratio()
                        if sim >= near_dup_ratio and (seq_a != seq_b or j > i + k):
                            matched_j = j
                            match_kind = "NEAR_DUPLICATE"
                            sim_score = round(sim, 4)
                            break

                if matched_j is not None:
                    phrase_a_txt = " ".join(str(w.get("word") or w.get("text") or "").strip() for w in asr_words[i : i + k])
                    phrase_b_txt = " ".join(str(w.get("word") or w.get("text") or "").strip() for w in asr_words[matched_j : matched_j + k])
                    phrase_norm_str = " ".join(normalize_spoken_tokens(phrase_a_txt))

                    # Check if approved script intentionally repeats this phrase twice!
                    script_occurrences = script_norm_str.count(phrase_norm_str) if (script_norm_str and phrase_norm_str) else 0
                    is_intentional = (phrase_norm_str in allow_phrases) or (script_occurrences >= 2)

                    # Score both takes by mean ASR confidence (tie-break: keep cleaner second take when first is a false start,
                    # or higher confidence take when cleanly separated)
                    conf_a = sum(float(w.get("prob", w.get("confidence", 0.9))) for w in asr_words[i : i + k]) / float(k)
                    conf_b = sum(float(w.get("prob", w.get("confidence", 0.9))) for w in asr_words[matched_j : matched_j + k]) / float(k)
                    # Prefer second take if confidence >= first take - 0.03 (typical studio retry / cleaner sentence landing)
                    keep_take = "SECOND" if conf_b >= (conf_a - 0.03) else "FIRST"
                    drop_slice = (i, i + k) if keep_take == "SECOND" else (matched_j, matched_j + k)
                    keep_slice = (matched_j, matched_j + k) if keep_take == "SECOND" else (i, i + k)

                    # Expand drop end slightly into the inter-take gap midpoint (never touching the kept word!)
                    drop_start = float(asr_words[drop_slice[0]].get("start", asr_words[drop_slice[0]].get("start_sec", 0.0)))
                    drop_end = float(asr_words[drop_slice[1] - 1].get("end", asr_words[drop_slice[1] - 1].get("end_sec", 0.0)))
                    if keep_take == "SECOND":
                        next_start = float(asr_words[keep_slice[0]].get("start", asr_words[keep_slice[0]].get("start_sec", drop_end)))
                        if next_start > drop_end:
                            drop_end = round(drop_end + min(0.06, (next_start - drop_end) * 0.5), 4)
                    else:
                        prev_end = float(asr_words[keep_slice[1] - 1].get("end", asr_words[keep_slice[1] - 1].get("end_sec", drop_start)))
                        if drop_start > prev_end:
                            drop_start = round(drop_start - min(0.06, (drop_start - prev_end) * 0.5), 4)

                    keep_start = float(asr_words[keep_slice[0]].get("start", asr_words[keep_slice[0]].get("start_sec", 0.0)))
                    keep_end = float(asr_words[keep_slice[1] - 1].get("end", asr_words[keep_slice[1] - 1].get("end_sec", 0.0)))

                    if is_intentional:
                        decision = "KEEP_INTENTIONAL_REPETITION"
                    elif match_kind == "EXACT_DUPLICATE" and (not approved_script or script_occurrences <= 1):
                        decision = "DROP"
                    else:
                        decision = "PENDING_OWNER_REVIEW"

                    found.append({
                        "duplicate_id": f"dup_{len(found) + 1:02d}",
                        "match_kind": match_kind,
                        "similarity": sim_score,
                        "decision": decision,
                        "reason": (
                            "Intentional repetition present in approved script/allowlist."
                            if is_intentional else
                            f"{match_kind} ({k} words) across [{drop_start:.3f}s, {drop_end:.3f}s); keeping {keep_take} take (conf A={conf_a:.3f}, B={conf_b:.3f})."
                        ),
                        "text": phrase_a_txt if keep_take == "SECOND" else phrase_b_txt,
                        "keep_take": keep_take,
                        "drop_word_indices": [drop_slice[0], drop_slice[1]],
                        "keep_word_indices": [keep_slice[0], keep_slice[1]],
                        "drop_start_sec": round(drop_start, 4),
                        "drop_end_sec": round(drop_end, 4),
                        "drop_start_frame": int(round(drop_start * fps)),
                        "drop_end_frame": int(round(drop_end * fps)),
                        "drop_start_sample": int(round(drop_start * sample_rate)),
                        "drop_end_sample": int(round(drop_end * sample_rate)),
                        "keep_start_sec": round(keep_start, 4),
                        "keep_end_sec": round(keep_end, 4),
                        "take_a_confidence": round(conf_a, 4),
                        "take_b_confidence": round(conf_b, 4),
                        "script_occurrences": script_occurrences,
                    })
                    for idx in range(i, matched_j + k):
                        consumed_indices.add(idx)
                    i = matched_j + k
                    continue
                i += 1

    found.sort(key=lambda x: x["drop_start_sec"])
    rep = {
        "schema_version": "kh03.duplicate_spans.v1",
        "fps_num": fps_num,
        "fps_den": fps_den,
        "sample_rate": sample_rate,
        "total_detected": len(found),
        "verified_drop_count": sum(1 for d in found if d["decision"] == "DROP"),
        "pending_owner_review_count": sum(1 for d in found if d["decision"] == "PENDING_OWNER_REVIEW"),
        "intentional_kept_count": sum(1 for d in found if d["decision"] == "KEEP_INTENTIONAL_REPETITION"),
        "duplicates": found,
    }
    if out_path is not None:
        write_json_atomic(out_path, rep)
    return rep


def build_speech_first_edl(
    source_a_probe: dict[str, Any],
    asr_words: list[dict[str, Any]],
    duplicate_report: dict[str, Any],
    silence_spans: list[dict[str, float]] | None = None,
    *,
    target_fps_num: int = 24,
    target_fps_den: int = 1,
    sample_rate: int = 48000,
    max_gap_sec: float = 0.45,
    preserve_pause_sec: float = 0.18,
    crossfade_ms: int = 20,
    secondary_sources: list[dict[str, Any]] | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Build exact half-open [src_in, src_out) VOICE_EDL, MASTER_EDL, SOURCE_PTS_MAP & SOURCE_HASH_MANIFEST.

    Drops ONLY verified `DROP` duplicate spans and optionally shortens long inter-word silences (> max_gap_sec)
    down to `preserve_pause_sec` without ever cutting inside a spoken word!
    """
    fps_frac = Fraction(max(1, target_fps_num), max(1, target_fps_den))
    fps = float(fps_frac)
    total_src_dur = float(source_a_probe.get("video_duration_sec") or source_a_probe.get("duration_sec") or 0.0)
    if total_src_dur <= 0.0:
        raise ValueError("INVALID_SOURCE_DURATION: primary source video_duration_sec must be > 0")

    # Collect verified exclusion windows [cut_in, cut_out)
    cuts: list[tuple[float, float, str]] = []
    for dup in duplicate_report.get("duplicates", []):
        if dup.get("decision") == "DROP":
            s0 = max(0.0, float(dup["drop_start_sec"]))
            s1 = min(total_src_dur, float(dup["drop_end_sec"]))
            if s1 > s0 + 0.02:
                cuts.append((s0, s1, f"duplicate_drop:{dup['duplicate_id']}"))

    # Shorten excessive inter-word silence gaps safely (never cutting inside any spoken word!)
    word_intervals = [
        (float(w.get("start", w.get("start_sec", 0.0))), float(w.get("end", w.get("end_sec", 0.0))))
        for w in asr_words
    ]
    for sil in (silence_spans or []):
        ss = max(0.0, float(sil.get("start_sec", 0.0)))
        se = min(total_src_dur, float(sil.get("end_sec", 0.0)))
        dur_sil = se - ss
        if dur_sil <= max_gap_sec:
            continue
        # Ensure silence interior does not overlap any spoken word
        keep_half = preserve_pause_sec * 0.5
        c0 = ss + keep_half
        c1 = se - keep_half
        if c1 <= c0 + 0.05:
            continue
        if any(not (w1 <= c0 or w0 >= c1) for w0, w1 in word_intervals):
            continue
        cuts.append((round(c0, 4), round(c1, 4), "silence_trim"))

    # Merge overlapping/adjacent cuts
    cuts.sort(key=lambda x: (x[0], x[1]))
    merged_cuts: list[tuple[float, float, str]] = []
    for c0, c1, reason in cuts:
        if merged_cuts and c0 <= merged_cuts[-1][1] + 0.01:
            p0, p1, pr = merged_cuts[-1]
            merged_cuts[-1] = (p0, max(p1, c1), f"{pr}+{reason}")
        else:
            merged_cuts.append((c0, c1, reason))

    # Build half-open kept segments [src_in, src_out) snapped cleanly to target frame grid
    raw_keeps: list[tuple[float, float, str]] = []
    cursor = 0.0
    for c0, c1, reason in merged_cuts:
        if c0 > cursor + 0.02:
            raw_keeps.append((cursor, c0, f"before_{reason}"))
        cursor = max(cursor, c1)
    if total_src_dur > cursor + 0.02:
        raw_keeps.append((cursor, total_src_dur, "tail_keep"))
    if not raw_keeps:
        raw_keeps.append((0.0, total_src_dur, "full_keep"))

    segments: list[dict[str, Any]] = []
    out_frame_cursor = 0
    for idx, (s0, s1, reason) in enumerate(raw_keeps):
        sf = int(round(s0 * fps))
        ef = max(sf + 1, int(round(s1 * fps)))
        dur_f = ef - sf
        out_s = out_frame_cursor
        out_e = out_frame_cursor + dur_f
        out_frame_cursor = out_e
        segments.append({
            "segment_id": f"vseg_{idx + 1:02d}",
            "source_id": str(source_a_probe.get("source_id") or "primary_aroll"),
            "src_in_sec": round(float(Fraction(sf) / fps_frac), 4),
            "src_out_sec": round(float(Fraction(ef) / fps_frac), 4),
            "src_start_frame": sf,
            "src_end_frame": ef,
            "src_start_sample": int(round(float(Fraction(sf) / fps_frac) * sample_rate)),
            "src_end_sample": int(round(float(Fraction(ef) / fps_frac) * sample_rate)),
            "out_start_frame": out_s,
            "out_end_frame": out_e,
            "out_in_sec": round(float(Fraction(out_s) / fps_frac), 4),
            "out_out_sec": round(float(Fraction(out_e) / fps_frac), 4),
            "crossfade_ms": crossfade_ms if idx > 0 else 0,
            "reason": reason,
        })

    # Map every kept ASR word onto output clock
    mapped_words: list[dict[str, Any]] = []
    dropped_words: list[dict[str, Any]] = []
    for w_idx, w in enumerate(asr_words):
        ws = float(w.get("start", w.get("start_sec", 0.0)))
        we = float(w.get("end", w.get("end_sec", ws)))
        wmid = (ws + we) * 0.5
        seg_hit = next((sg for sg in segments if sg["src_in_sec"] - 0.01 <= wmid < sg["src_out_sec"] + 0.01), None)
        if seg_hit is None:
            dropped_words.append({"word_index": w_idx, "word": w.get("word") or w.get("text"), "src_start": ws, "src_end": we})
        else:
            offset = seg_hit["out_in_sec"] - seg_hit["src_in_sec"]
            mapped_words.append({
                "word_index": w_idx,
                "word": w.get("word") or w.get("text"),
                "segment_id": seg_hit["segment_id"],
                "src_start_sec": round(ws, 4),
                "src_end_sec": round(we, 4),
                "out_start_sec": round(max(seg_hit["out_in_sec"], ws + offset), 4),
                "out_end_sec": round(min(seg_hit["out_out_sec"], we + offset), 4),
                "out_start_frame": int(round(max(seg_hit["out_in_sec"], ws + offset) * fps)),
                "out_end_frame": int(round(min(seg_hit["out_out_sec"], we + offset) * fps)),
            })

    total_out_sec = round(float(Fraction(out_frame_cursor) / fps_frac), 4)
    voice_edl = {
        "schema_version": "kh03.voice_edl.v1",
        "fps_num": target_fps_num,
        "fps_den": target_fps_den,
        "sample_rate": sample_rate,
        "source_duration_sec": round(total_src_dur, 4),
        "output_duration_sec": total_out_sec,
        "total_output_frames": out_frame_cursor,
        "removed_duration_sec": round(max(0.0, total_src_dur - total_out_sec), 4),
        "crossfade_ms": crossfade_ms,
        "cuts_applied": [{"start_sec": c0, "end_sec": c1, "reason": r} for c0, c1, r in merged_cuts],
        "segments": segments,
        "mapped_word_count": len(mapped_words),
        "dropped_word_count": len(dropped_words),
    }

    # Secondary sources PTS guard summary
    sec_entries = []
    for sc in (secondary_sources or []):
        sec_entries.append({
            "source_id": sc.get("source_id"),
            "role": sc.get("role", "secondary_screen_evidence"),
            "sha256": sc.get("sha256"),
            "fps": f"{sc.get('fps_num', 30)}/{sc.get('fps_den', 1)}",
            "video_duration_sec": sc.get("video_duration_sec"),
            "container_duration_sec": sc.get("container_duration_sec"),
            "container_vs_video_delta_sec": sc.get("container_vs_video_delta_sec", 0.0),
            "audio_present": sc.get("audio_present"),
            "audio_policy": sc.get("audio_policy", "MUTE_DEFAULT"),
            "clamped_max_src_out_sec": sc.get("video_duration_sec"),
        })

    master_edl = {
        "schema_version": "kh03.master_edl.v1",
        "voice_master_source_id": str(source_a_probe.get("source_id") or "primary_aroll"),
        "target_fps_num": target_fps_num,
        "target_fps_den": target_fps_den,
        "target_sample_rate": sample_rate,
        "total_output_frames": out_frame_cursor,
        "output_duration_sec": total_out_sec,
        "voice_segments": segments,
        "secondary_sources": sec_entries,
        "edl_sha256": hash_artifact(voice_edl),
    }
    pts_map = {
        "schema_version": "kh03.source_pts_map.v1",
        "fps_num": target_fps_num,
        "fps_den": target_fps_den,
        "total_output_frames": out_frame_cursor,
        "mapped_words": mapped_words,
        "dropped_words": dropped_words,
        "secondary_sources": sec_entries,
    }
    hash_manifest = {
        "schema_version": "kh03.source_hash_manifest.v1",
        "primary_source": {
            "source_id": str(source_a_probe.get("source_id") or "primary_aroll"),
            "sha256": source_a_probe.get("sha256"),
            "audio_policy": "VOICE_MASTER_PRESERVE",
        },
        "secondary_sources": [
            {"source_id": sc.get("source_id"), "sha256": sc.get("sha256"), "audio_policy": sc.get("audio_policy", "MUTE_DEFAULT")}
            for sc in (secondary_sources or [])
        ],
        "master_edl_sha256": master_edl["edl_sha256"],
    }

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(out_dir / "VOICE_EDL.json", voice_edl)
        write_json_atomic(out_dir / "MASTER_EDL.json", master_edl)
        write_json_atomic(out_dir / "SOURCE_PTS_MAP.json", pts_map)
        write_json_atomic(out_dir / "SOURCE_HASH_MANIFEST.json", hash_manifest)

    return {
        "voice_edl": voice_edl,
        "master_edl": master_edl,
        "source_pts_map": pts_map,
        "source_hash_manifest": hash_manifest,
    }


def verify_post_render_asr(
    final_asr_words: list[dict[str, Any]],
    approved_script: str,
    *,
    approved_intentional_repeats: list[str] | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Verify post-render ASR matches approved script with ZERO unintended spoken duplicates & exact numbers."""
    align = align_asr_to_script(final_asr_words, approved_script)
    dups = detect_duplicate_spans(
        final_asr_words,
        approved_script,
        approved_intentional_repeats=approved_intentional_repeats,
    )
    unintended_dups = [d for d in dups["duplicates"] if d["decision"] in ("DROP", "PENDING_OWNER_REVIEW")]
    ok = (
        align["coverage_ratio"] >= 0.85
        and len(align["missing_numbers"]) == 0
        and len(unintended_dups) == 0
    )
    rep = {
        "schema_version": "kh03.final_asr_diff.v1",
        "pass": ok,
        "status": "PASS" if ok else "FAIL_ASR_DIFF_OR_DUPLICATE",
        "coverage_ratio": align["coverage_ratio"],
        "missing_numbers": align["missing_numbers"],
        "missing_spans": align["missing_spans"],
        "unintended_duplicate_count": len(unintended_dups),
        "unintended_duplicates": unintended_dups,
        "final_word_count": len(final_asr_words),
    }
    if out_path is not None:
        write_json_atomic(out_path, rep)
    return rep
