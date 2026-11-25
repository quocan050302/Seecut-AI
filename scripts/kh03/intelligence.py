"""KH-03 Input Intelligence & Semantic Graph Builder.

Reads actual video/audio via ffprobe + optional local faster-whisper ASR, reconciles supplied
script vs spoken dialogue (preserving spoken audio as truth), extracts numeric/unit claims,
negations, causal/temporal relations, source action windows, and safe regions across any niche.
"""

from __future__ import annotations

import difflib
import json
import math
import re
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from scripts.kh02.source_analysis import probe_source
from scripts.kh02.transcript import normalize_faster_whisper
from scripts.kh02.typography import CJK_RE, VI_DIACRITIC_RE

from .contracts import (
    assert_kh03_schema,
    hash_artifact,
    sanitize_untrusted_text,
    write_json_atomic,
)

_NUM_RE = re.compile(
    r"(?P<prefix>\$|€|£|¥)?(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<unit>%|percent|dollars?|usd|degrees?\s*(?:celsius|fahrenheit|c|f)|pages?|page|seconds?|sec|minutes?|min|hours?|months?|years?|weeks?|days?|units?|x|k|m|b)?\b",
    re.IGNORECASE,
)

_NEGATION_WORDS = frozenset({
    "doesn't", "dont", "don't", "not", "never", "cannot", "can't", "no", "without", "instead", "neither", "nor", "actually",
})
_CAUSAL_WORDS = frozenset({
    "because", "since", "therefore", "means", "causes", "leads", "results", "so", "when", "if", "why", "produces", "develops",
})
_STEP_WORDS = frozenset({
    "first", "second", "third", "step", "next", "then", "finally", "before", "after", "spread", "wait", "combine",
})
_COMPARISON_WORDS = frozenset({
    "vs", "versus", "than", "instead", "while", "compare", "comparison", "beats", "difference", "cooler", "faster", "more", "less",
})
_CTA_WORDS = frozenset({
    "link", "profile", "bio", "download", "subscribe", "follow", "try", "plan", "get", "start", "comment", "join",
})
_PRODUCT_WORDS = frozenset({
    "product", "dashboard", "workbook", "template", "app", "tool", "system", "plan", "workflow", "plugin", "kit", "notion", "bundle",
})
_ACTION_LOOP_WORDS = frozenset({
    "pour", "pouring", "fold", "folding", "stack", "stacking", "sort", "sorting", "envelope", "envelopes",
    "cash", "cook", "searing", "pan", "walk", "walking", "open", "opening", "write", "notebook", "typing", "keyboard",
})


def _infer_aspect(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "9:16"
    r = width / float(height)
    if abs(r - 1.0) <= 0.08:
        return "1:1"
    if r >= 1.25:
        return "16:9"
    if 0.72 <= r <= 0.88:
        return "4:5"
    return "9:16"


def _detect_language(text: str, explicit: str | None = None) -> str:
    if explicit and len(explicit.strip()) >= 2:
        return explicit.strip().lower()[:8]
    if not text.strip():
        return "en"
    if CJK_RE.search(text):
        return "zh"
    if VI_DIACRITIC_RE.search(text):
        return "vi"
    return "en"


def _split_sentences(text: str) -> list[str]:
    clean = " ".join(str(text or "").strip().split())
    if not clean:
        return []
    parts = re.split(r"(?<=[.!?。！？])\s+", clean)
    return [p.strip() for p in parts if p.strip()]


def _extract_numeric_claims(text: str) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    low = text.lower()
    is_hypothetical = any(k in low for k in ("let's say", "for example", "suppose", "imagine", "example", "roughly"))
    for idx, m in enumerate(_NUM_RE.finditer(text)):
        prefix = (m.group("prefix") or "").strip()
        raw_num = m.group("num").replace(",", "")
        unit_raw = (m.group("unit") or "").strip()
        try:
            dec_val = str(Decimal(raw_num))
        except InvalidOperation:
            continue
        unit = "USD" if prefix == "$" else (prefix or unit_raw.lower() or "count")
        claims.append({
            "claim_id": f"num_{idx + 1:02d}",
            "raw_text": m.group(0).strip(),
            "value": dec_val,
            "unit": unit,
            "illustrative": bool(is_hypothetical and unit in ("USD", "count")),
        })
    return claims


def _try_local_asr(video_path: Path, language: str | None = None) -> dict[str, Any] | None:
    """Attempt local faster-whisper word-level ASR if available (never fails hard on missing model)."""
    # Check current Python or known local faster-whisper venv if explicitly present
    py_candidates = []
    import sys
    py_candidates.append(Path(sys.executable))
    venv_py = Path.home() / ".faceless-shorts" / "venv" / "Scripts" / "python.exe"
    if venv_py.is_file():
        py_candidates.append(venv_py)
    venv_py_unix = Path.home() / ".faceless-shorts" / "venv" / "bin" / "python"
    if venv_py_unix.is_file():
        py_candidates.append(venv_py_unix)

    asr_code = (
        "import json, subprocess, sys, numpy as np\n"
        "from faster_whisper import WhisperModel\n"
        "vp = sys.argv[1]\n"
        "lang = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != 'auto' else None\n"
        "p = subprocess.run(['ffmpeg','-v','error','-i',vp,'-ac','1','-ar','16000','-f','f32le','pipe:1'], stdout=subprocess.PIPE, check=True)\n"
        "audio = np.frombuffer(p.stdout, dtype=np.float32)\n"
        "model = WhisperModel('small.en' if (lang in (None,'en')) else 'small', device='cpu', compute_type='int8', local_files_only=True)\n"
        "segs, info = model.transcribe(audio, language=lang, word_timestamps=True, beam_size=5, vad_filter=False)\n"
        "words = []\n"
        "for s in segs:\n"
        "  for w in (s.words or []):\n"
        "    words.append({'word': w.word.strip(), 'start': round(float(w.start), 3), 'end': round(float(w.end), 3), 'probability': round(float(w.probability), 3)})\n"
        "print(json.dumps({'model': 'faster-whisper-small.en', 'language': getattr(info, 'language', lang or 'en'), 'words': words}))\n"
    )
    for py in py_candidates:
        try:
            proc = subprocess.run(
                [str(py), "-c", asr_code, str(video_path), language or "auto"],
                capture_output=True, text=True, timeout=90,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception:
            continue
    return None


def _reconcile_script_and_asr(script_text: str, asr_words: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare supplied script against actual spoken ASR words; spoken audio remains audio truth."""
    asr_text = " ".join(str(w.get("text") or w.get("word") or "").strip() for w in asr_words).strip()
    if not asr_words and not script_text:
        return {
            "status": "NO_SPEECH_OR_SCRIPT",
            "similarity_ratio": 0.0,
            "uncertain_words": [],
            "mismatches": [],
            "audio_truth_text": "",
            "supplied_script_text": "",
        }
    if not asr_words:
        return {
            "status": "SCRIPT_ONLY_UNVERIFIED_AGAINST_AUDIO",
            "similarity_ratio": 1.0,
            "uncertain_words": [],
            "mismatches": ["Local ASR not executed; spoken audio verification pending."],
            "audio_truth_text": "",
            "supplied_script_text": script_text,
        }
    low_conf = [
        {"word": w.get("text") or w.get("word"), "start": w.get("start_sec", w.get("start")), "confidence": w.get("confidence", w.get("probability", 1.0))}
        for w in asr_words
        if float(w.get("confidence", w.get("probability", 1.0))) < 0.72
    ]
    if not script_text:
        return {
            "status": "ASR_ONLY_FALLBACK",
            "similarity_ratio": 1.0,
            "uncertain_words": low_conf,
            "mismatches": [],
            "audio_truth_text": asr_text,
            "supplied_script_text": "",
        }

    norm_s = re.sub(r"[^a-z0-9\u00C0-\u024F\u1E00-\u1EFF\u4e00-\u9fff]+", " ", script_text.lower()).split()
    norm_a = re.sub(r"[^a-z0-9\u00C0-\u024F\u1E00-\u1EFF\u4e00-\u9fff]+", " ", asr_text.lower()).split()
    sim = round(difflib.SequenceMatcher(None, norm_s, norm_a).ratio(), 4)
    mismatches: list[str] = []
    sm = difflib.SequenceMatcher(None, norm_s, norm_a)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            mismatches.append(f"{tag}: script='{' '.join(norm_s[i1:i2])}' vs audio='{' '.join(norm_a[j1:j2])}'")
    return {
        "status": "ALIGNED" if sim >= 0.85 else "MISMATCH_DETECTED_AUDIO_TRUTH_PRESERVED",
        "similarity_ratio": sim,
        "uncertain_words": low_conf,
        "mismatches": mismatches[:12],
        "audio_truth_text": asr_text,
        "supplied_script_text": script_text,
    }


def _synthesize_word_timing_from_script(script_text: str, duration_sec: float) -> list[dict[str, Any]]:
    """Fallback deterministic word timing when script is supplied without live ASR timestamps."""
    toks = [t for t in script_text.split() if t.strip()]
    if not toks:
        return []
    step = max(0.08, duration_sec / max(1, len(toks)))
    out: list[dict[str, Any]] = []
    for idx, tok in enumerate(toks):
        t0 = round(idx * step, 3)
        t1 = round(min(duration_sec, (idx + 1) * step), 3)
        out.append({
            "word_id": f"w_{idx + 1:04d}",
            "text": tok,
            "start_sec": t0,
            "end_sec": max(t0 + 0.04, t1),
            "confidence": 0.90,
            "synthetic_timing": True,
        })
    return out


def analyze_inputs(
    *,
    video_path: str | Path,
    run_id: str,
    script_text: str | None = None,
    script_path: str | Path | None = None,
    transcript_json: dict[str, Any] | None = None,
    video_rights: str = "OWNER_SUPPLIED_NONCOMMERCIAL",
    language: str | None = None,
    constraints: dict[str, Any] | None = None,
    run_asr: bool = False,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Probe real video/audio, reconcile script & ASR, extract semantic/numeric/action signals & safe zones."""
    vp = Path(video_path).resolve()
    if not vp.is_file():
        raise FileNotFoundError(f"Primary source video missing: {vp}")

    probed = probe_source(vp)
    m = probed["media"]
    width = int(m.get("width") or 720)
    height = int(m.get("height") or 1280)
    fps_num = int(m.get("fps_num") or 24)
    fps_den = int(m.get("fps_den") or 1)
    fps = fps_num / float(max(1, fps_den))
    dur = float(m.get("duration_sec") or 10.0)
    nb_frames = int(m.get("nb_frames") or max(1, int(round(dur * fps))))
    rot = int(m.get("rotation_deg") or 0)
    eff_w, eff_h = (height, width) if abs(rot) in (90, 270) else (width, height)
    aspect = _infer_aspect(eff_w, eff_h)

    raw_script = script_text or ""
    script_src_path_str: str | None = None
    raw_script_sha: str | None = None
    if script_path and Path(script_path).is_file():
        sp_res = Path(script_path).resolve()
        script_src_path_str = str(sp_res)
        raw_bytes = sp_res.read_bytes()
        import hashlib
        raw_script_sha = hashlib.sha256(raw_bytes).hexdigest()
        if not raw_script:
            raw_script = raw_bytes.decode("utf-8", errors="replace")
    elif raw_script:
        import hashlib
        raw_script_sha = hashlib.sha256(raw_script.encode("utf-8")).hexdigest()
    clean_script = sanitize_untrusted_text(raw_script, max_len=12000)

    # Transcript / ASR resolution (audio truth preserved whenever available)
    warnings: list[str] = []
    norm_transcript: dict[str, Any] | None = None
    asr_status = "SCRIPT_ONLY_NO_ASR"
    if transcript_json is not None:
        norm_transcript = normalize_faster_whisper(
            transcript_json,
            source_asset_id="primary_aroll",
            audio_sha256=probed["sha256"],
            language=language or _detect_language(clean_script),
        )
        asr_status = "TRANSCRIPT_SUPPLIED"
    elif run_asr and bool(m.get("has_audio")):
        raw_asr = _try_local_asr(vp, language=language)
        if raw_asr and raw_asr.get("words"):
            norm_transcript = normalize_faster_whisper(
                raw_asr,
                source_asset_id="primary_aroll",
                audio_sha256=probed["sha256"],
                language=raw_asr.get("language") or language or "en",
            )
            asr_status = "LOCAL_ASR_VERIFIED"
        else:
            warnings.append("Local faster-whisper unavailable or returned 0 words; fell back to supplied script.")
    elif not m.get("has_audio") and not clean_script:
        asr_status = "SILENT_VISUAL_ONLY"

    asr_words = list((norm_transcript or {}).get("words", []))
    recon = _reconcile_script_and_asr(clean_script, asr_words)
    if recon["uncertain_words"]:
        warnings.append(f"{len(recon['uncertain_words'])} low-confidence ASR word(s) flagged for review.")
    if recon["status"] == "MISMATCH_DETECTED_AUDIO_TRUTH_PRESERVED":
        warnings.append("Supplied script differs from spoken audio; audio transcript kept as spoken truth.")
        if asr_status in ("LOCAL_ASR_VERIFIED", "TRANSCRIPT_SUPPLIED") and recon["similarity_ratio"] < 0.65:
            asr_status = "ASR_UNCERTAIN"

    # Authoritative text for semantic understanding prefers actual spoken ASR words when present
    effective_text = recon["audio_truth_text"] if recon["audio_truth_text"] else clean_script
    if not effective_text and asr_status != "SILENT_VISUAL_ONLY":
        warnings.append("No intelligible script or ASR transcript found; visual-only cues limited.")

    lang = _detect_language(effective_text, explicit=language)
    low = effective_text.lower()
    toks = set(re.findall(r"[a-z0-9\u00C0-\u024F\u1E00-\u1EFF\u4e00-\u9fff']+", low))
    num_claims = _extract_numeric_claims(effective_text)

    has_neg = bool(toks & _NEGATION_WORDS) or ("!=" in effective_text) or ("≠" in effective_text)
    has_causal = bool(toks & _CAUSAL_WORDS)
    has_steps = bool(toks & _STEP_WORDS) or bool(re.search(r"\bstep\s*\d", low))
    has_comp = bool(toks & _COMPARISON_WORDS)
    has_cta = bool(toks & _CTA_WORDS)
    has_product = bool(toks & _PRODUCT_WORDS)
    action_loops = sorted(toks & _ACTION_LOOP_WORDS)
    has_question = ("?" in effective_text) or bool(re.match(r"^(why|how|what|if|when|just because|stop)\b", low))

    # Content-derived explanation burden (0..1, zero hardcoded niche names!)
    exp_burden = min(
        1.0,
        round(
            0.22 * min(3, len(num_claims)) / 3.0
            + (0.25 if has_causal else 0.0)
            + (0.22 if has_steps else 0.0)
            + (0.18 if has_comp else 0.0)
            + (0.15 if has_neg else 0.0)
            + (0.10 if len(effective_text.split()) >= 45 else 0.0),
            3,
        ),
    )

    # Action continuity score based on physical verbs & continuous A-roll presence
    action_cont = min(
        1.0,
        round(0.35 + 0.14 * min(4, len(action_loops)) + (0.15 if aspect == "9:16" else 0.05), 3),
    )

    # Infer high-level communication intent purely from linguistic signals
    if has_product and has_cta and exp_burden >= 0.45:
        intent = "mixed"
    elif has_product and has_cta:
        intent = "persuade_product"
    elif exp_burden >= 0.50:
        intent = "educate"
    elif has_steps and not has_product:
        intent = "demonstrate"
    elif has_neg and has_question:
        intent = "entertain_hook"
    elif any(k in toks for k in ("1440", "century", "history", "forever", "story", "years", "trade", "reshaped")):
        intent = "storytell"
    else:
        intent = "document"

    # Generic topic signal keywords (top informative content tokens — never hardcoded niche enums)
    stop = {
        "the", "and", "for", "that", "with", "this", "your", "you", "have", "are", "from", "into",
        "just", "only", "when", "what", "will", "they", "them", "their", "about", "there", "here",
        "very", "much", "more", "some", "each", "than", "does", "mean", "actually", "make", "made",
    }
    freq: dict[str, int] = {}
    for t in re.findall(r"[a-z\u00C0-\u024F\u1E00-\u1EFF\u4e00-\u9fff]{4,}", low):
        if t not in stop:
            freq[t] = freq.get(t, 0) + 1
    topic_signals = [k for k, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]

    evidence_needs_summary: list[str] = []
    if num_claims:
        evidence_needs_summary.append(f"Verify and visualize {len(num_claims)} numeric/unit claim(s) accurately.")
    if has_product:
        evidence_needs_summary.append("Display only owner-verified product/UI captures (zero fabricated mockups).")
    if has_causal or has_steps:
        evidence_needs_summary.append("Illustrate step sequence or causal mechanism clearly beyond raw captions.")
    if has_comp:
        evidence_needs_summary.append("Provide labeled side-by-side comparison grounded in comparable attributes.")
    if not evidence_needs_summary:
        evidence_needs_summary.append("Support spoken narrative with relevant rights-cleared visual context.")

    # Conservative safe regions honoring aspect ratio & explicit full-screen lock
    eff_constraints = dict(constraints or {})
    if aspect == "9:16":
        protected = [
            {"zone_id": "face_hair_mouth", "bbox_norm": [0.26, 0.20, 0.48, 0.28], "basis": "conservative_portrait_center_head"},
            {"zone_id": "hands_tabletop_action", "bbox_norm": [0.04, 0.52, 0.92, 0.44], "basis": "conservative_portrait_action_zone"},
            {"zone_id": "platform_right_rail", "bbox_norm": [0.88, 0.35, 0.12, 0.55], "basis": "tiktok_reels_right_rail_heuristic"},
        ]
        clear = [
            {"zone_id": "top_clear_stage", "bbox_norm": [0.055, 0.042, 0.820, 0.112], "basis": "upper_negative_space_above_head"},
        ]
    elif aspect == "16:9":
        protected = [
            {"zone_id": "center_subject", "bbox_norm": [0.28, 0.16, 0.44, 0.68], "basis": "conservative_landscape_subject"},
        ]
        clear = [
            {"zone_id": "left_editorial_rail", "bbox_norm": [0.04, 0.08, 0.22, 0.78], "basis": "landscape_left_negative_space"},
            {"zone_id": "right_editorial_rail", "bbox_norm": [0.74, 0.08, 0.22, 0.78], "basis": "landscape_right_negative_space"},
            {"zone_id": "top_banner_strip", "bbox_norm": [0.05, 0.05, 0.90, 0.14], "basis": "landscape_top_safe_strip"},
        ]
    else:
        protected = [
            {"zone_id": "center_subject", "bbox_norm": [0.22, 0.20, 0.56, 0.56], "basis": "conservative_square_subject"},
        ]
        clear = [
            {"zone_id": "top_clear_stage", "bbox_norm": [0.05, 0.05, 0.90, 0.15], "basis": "square_top_safe_strip"},
        ]

    mean_conf = (
        round(sum(float(w.get("confidence", 1.0)) for w in asr_words) / len(asr_words), 4)
        if asr_words else None
    )

    analysis = {
        "schema_version": "kh03.input_analysis.v1",
        "run_id": run_id,
        "video_path": str(vp),
        "video_sha256": probed["sha256"],
        "video_rights": video_rights,
        "media": {
            "width": width,
            "height": height,
            "fps_num": fps_num,
            "fps_den": fps_den,
            "nb_frames": nb_frames,
            "duration_sec": round(dur, 4),
            "has_audio": bool(m.get("has_audio")),
            "audio_sample_rate": int(m["audio_sample_rate"]) if m.get("audio_sample_rate") else None,
            "rotation_deg": rot,
            "aspect_ratio": aspect,
        },
        "script_supplied": bool(clean_script),
        "script_sha256": hash_artifact({"normalized_script": effective_text}),
        "raw_script_sha256": raw_script_sha,
        "script_source_path": script_src_path_str,
        "asr_sha256": hash_artifact(norm_transcript) if norm_transcript is not None else None,
        "constraints_sha256": hash_artifact(eff_constraints),
        "normalized_script": effective_text,
        "language": lang,
        "asr_status": asr_status,
        "asr_confidence_mean": mean_conf,
        "topic_signals": topic_signals,
        "intent": intent,
        "audience_profile": "general_mobile_viewers" if aspect == "9:16" else "general_widescreen_viewers",
        "narrative_structure": {
            "has_hook_tension": bool(has_question or has_neg),
            "has_payoff": bool(len(_split_sentences(effective_text)) >= 2),
            "has_cta": bool(has_cta),
            "has_step_sequence": bool(has_steps),
            "has_causal_chain": bool(has_causal),
            "explanation_burden": exp_burden,
            "action_continuity_score": action_cont,
        },
        "numeric_claims": num_claims,
        "evidence_needs_summary": evidence_needs_summary,
        "action_open_loops": action_loops,
        "safe_regions": {
            "protected_zones": protected,
            "clear_overlay_zones": clear,
        },
        "constraints": eff_constraints,
        "warnings": warnings,
    }
    assert_kh03_schema(analysis, "input_analysis")

    # Save optional analysis artifacts if run_dir supplied
    action_map = {
        "schema_version": "kh03.source_action_map.v1",
        "run_id": run_id,
        "video_sha256": probed["sha256"],
        "detected_action_tokens": action_loops,
        "protected_zones": protected,
        "clear_overlay_zones": clear,
        "tracking_method": "conservative_aspect_safe_zones_no_fake_ml_claim",
    }
    if run_dir is not None:
        write_json_atomic(run_dir / "analysis" / "INPUT_ANALYSIS.json", analysis)
        write_json_atomic(run_dir / "analysis" / "ASR_RECONCILIATION.json", recon)
        write_json_atomic(run_dir / "analysis" / "SOURCE_ACTION_MAP.json", action_map)

    return {
        "input_analysis": analysis,
        "asr_reconciliation": recon,
        "source_action_map": action_map,
        "transcript": norm_transcript,
        "probed": probed,
    }


def build_semantic_graph(
    input_analysis: dict[str, Any],
    *,
    transcript: dict[str, Any] | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Build phrase/word/claim semantic graph with typed relations, protected semantics & action windows."""
    media = input_analysis["media"]
    fps = media["fps_num"] / float(max(1, media["fps_den"]))
    dur = float(media["duration_sec"])
    total_frames = int(media["nb_frames"])
    text = input_analysis.get("normalized_script") or ""

    words = list((transcript or {}).get("words", []))
    if not words and text:
        words = _synthesize_word_timing_from_script(text, dur)

    phrases_raw: list[dict[str, Any]] = []
    if transcript and transcript.get("phrases"):
        for idx, ph in enumerate(transcript["phrases"]):
            wids = list(ph.get("word_ids") or [])
            w_objs = [w for w in words if w["word_id"] in wids]
            t0 = float(w_objs[0]["start_sec"]) if w_objs else (idx * dur / max(1, len(transcript["phrases"])))
            t1 = float(w_objs[-1]["end_sec"]) if w_objs else min(dur, t0 + 1.5)
            phrases_raw.append({"text": ph["text"], "word_ids": wids, "start_sec": t0, "end_sec": t1})
    else:
        sents = _split_sentences(text) or ["Visual showcase sequence"]
        cursor = 0
        for idx, sent in enumerate(sents):
            n_toks = max(1, len(sent.split()))
            slice_w = words[cursor : cursor + n_toks]
            cursor += n_toks
            if slice_w:
                t0 = float(slice_w[0]["start_sec"])
                t1 = float(slice_w[-1]["end_sec"])
                wids = [w["word_id"] for w in slice_w]
            else:
                t0 = round(idx * dur / max(1, len(sents)), 3)
                t1 = round((idx + 1) * dur / max(1, len(sents)), 3)
                wids = []
            phrases_raw.append({"text": sent, "word_ids": wids, "start_sec": t0, "end_sec": t1})

    phrases: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    protected_tokens: list[str] = []

    for idx, ph in enumerate(phrases_raw):
        pid = f"ph_{idx + 1:02d}"
        ptxt = ph["text"]
        low = ptxt.lower()
        ptoks = set(re.findall(r"[a-z0-9\u00C0-\u024F\u1E00-\u1EFF\u4e00-\u9fff']+", low))
        sf = 0 if idx == 0 else phrases[-1]["end_frame"]
        ef = total_frames if idx == len(phrases_raw) - 1 else max(
            sf + 1, min(total_frames - (len(phrases_raw) - 1 - idx), int(round(ph["end_sec"] * fps)))
        )

        # Classify semantic role strictly from phrase semantics (NOT simple first=hook/last=cta alone!)
        p_nums = _extract_numeric_claims(ptxt)
        has_neg = bool(ptoks & _NEGATION_WORDS) or ("≠" in ptxt) or ("!=" in ptxt)
        has_cau = bool(ptoks & _CAUSAL_WORDS)
        has_stp = bool(ptoks & _STEP_WORDS) or bool(re.search(r"\bstep\s*\d", low))
        has_cmp = bool(ptoks & _COMPARISON_WORDS)
        has_prd = bool(ptoks & _PRODUCT_WORDS)
        has_cta = bool(ptoks & _CTA_WORDS) and ("link" in ptoks or "profile" in ptoks or "bio" in ptoks or "plan" in ptoks or "try" in ptoks)
        has_q = ("?" in ptxt) or bool(re.match(r"^(why|how|what|if|just because|stop)\b", low))

        if has_cta and idx >= max(1, len(phrases_raw) - 2):
            role = "cta"
        elif has_prd and not has_q:
            role = "evidence"
        elif has_neg and (has_q or idx == 0):
            role = "hook"
        elif has_cmp:
            role = "comparison"
        elif has_stp:
            role = "step"
        elif has_cau and p_nums:
            role = "mechanism"
        elif p_nums and ("left" in ptoks or "actually" in ptoks or "reaches" in ptoks or "saves" in ptoks):
            role = "payoff"
        elif p_nums:
            role = "demonstration"
        elif has_cau:
            role = "mechanism"
        elif has_q:
            role = "hook" if idx == 0 else "question"
        elif idx == len(phrases_raw) - 1:
            role = "payoff"
        else:
            role = "context"

        ev_need = (
            "verified_product_capture_or_cover" if role == "evidence"
            else ("numeric_proportional_visualization" if p_nums
                  else ("side_by_side_comparison" if role == "comparison"
                        else ("step_or_causal_diagram" if role in ("step", "mechanism")
                              else ("contextual_broll_or_annotation" if role in ("context", "hook", "question") else "clean_payoff_focus"))))
        )

        # Protect numbers, negations, product names, CTAs from unsafe trimming
        if p_nums or has_neg or has_prd or has_cta:
            protected_tokens.extend(ph["word_ids"])

        phrases.append({
            "phrase_id": pid,
            "text": ptxt,
            "word_ids": ph["word_ids"],
            "start_sec": round(sf / fps, 4),
            "end_sec": round(ef / fps, 4),
            "start_frame": sf,
            "end_frame": ef,
            "semantic_role": role,
            "has_negation": has_neg,
            "has_causal": has_cau,
            "has_comparison": has_cmp,
            "numeric_claims": p_nums,
            "evidence_need": ev_need,
            "confidence": 0.92 if not input_analysis["warnings"] else 0.80,
            "protected_semantics": bool(p_nums or has_neg or has_prd or has_cta),
        })

        cid = f"clm_{idx + 1:02d}"
        claims.append({
            "claim_id": cid,
            "phrase_id": pid,
            "text": ptxt,
            "claim_kind": "quantitative" if p_nums else ("causal" if has_cau else ("product" if has_prd else "narrative")),
            "requires_verification": bool(p_nums or has_prd or has_cau),
            "illustrative_marker": any(n["illustrative"] for n in p_nums),
        })

        if idx > 0:
            prev_pid = phrases[idx - 1]["phrase_id"]
            rel = (
                "contrasts" if (has_neg or has_cmp)
                else ("causes" if has_cau
                      else ("quantifies" if p_nums
                            else ("enables" if has_prd else "precedes")))
            )
            edges.append({
                "from_phrase": prev_pid,
                "to_phrase": pid,
                "relation": rel,
                "confidence": 0.88,
            })

    graph = {
        "schema_version": "kh03.semantic_graph.v1",
        "run_id": input_analysis["run_id"],
        "video_sha256": input_analysis["video_sha256"],
        "script_sha256": input_analysis["script_sha256"],
        "fps_num": media["fps_num"],
        "fps_den": media["fps_den"],
        "total_frames": total_frames,
        "words": words,
        "phrases": phrases,
        "claims": claims,
        "edges": edges,
        "protected_word_ids": sorted(set(protected_tokens)),
        "action_windows": [
            {
                "window_id": f"act_{i + 1:02d}",
                "action_cue": cue,
                "start_frame": 0,
                "end_frame": total_frames,
            }
            for i, cue in enumerate(input_analysis.get("action_open_loops", [])[:4])
        ],
    }
    if run_dir is not None:
        write_json_atomic(run_dir / "analysis" / "SEMANTIC_GRAPH.json", graph)
    return graph


def build_editorial_context(
    input_analysis: dict[str, Any],
    semantic_graph: dict[str, Any],
    *,
    skill_id: str | None = None,
    tracked_entities: list[dict[str, Any]] | None = None,
    available_assets: list[dict[str, Any]] | None = None,
    constraints: dict[str, Any] | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Section 03 universal cross-niche versioned context schema (supports UNKNOWN/multi-label & UNSUPPORTED detection)."""
    media = dict(input_analysis.get("media") or {})
    w = int(media.get("width") or 0)
    h = int(media.get("height") or 0)
    nb_f = int(media.get("nb_frames") or 0)
    fps_num = int(media.get("fps_num") or 0)
    fps_den = max(1, int(media.get("fps_den") or 1))
    v_codec = str(media.get("video_codec") or media.get("codec") or media.get("vcodec") or "").lower()

    unsupported_reasons: list[str] = []
    if w < 64 or h < 64 or nb_f <= 0 or fps_num <= 0:
        unsupported_reasons.append(f"INVALID_MEDIA_GEOMETRY_OR_TIMING({w}x{h}, frames={nb_f}, fps={fps_num}/{fps_den})")
    if v_codec in ("unknown", "unsupported"):
        unsupported_reasons.append(f"UNSUPPORTED_VIDEO_CODEC({v_codec!r})")

    phrases = list(semantic_graph.get("phrases") or [])
    txt_all = " ".join(str(p.get("text") or "") for p in phrases).lower()
    # Multi-label semantic domain signals (advisory signal ONLY — never hardcoded trigger for skill/effects!)
    signal_map = {
        "finance": ("paycheck", "budget", "bills", "bank", "savings", "debt", "invest", "tax", "$"),
        "software_ai": ("code", "terminal", "api", "app", "browser", "dashboard", "prompt", "workflow", "git", "python"),
        "cooking": ("cook", "pan", "sear", "chop", "recipe", "boil", "bake", "ingredient", "sauce", "kitchen"),
        "beauty": ("skin", "serum", "makeup", "spf", "routine", "cleanser", "shade"),
        "fitness": ("rep", "squat", "form", "workout", "stretch", "muscle", "cardio", "posture"),
        "education_science": ("experiment", "physics", "molecule", "equation", "formula", "cell", "energy", "theorem"),
        "history_storytelling": ("century", "empire", "battle", "expedition", "treaty", "archive", "ancient", "history"),
        "travel_lifestyle": ("city", "flight", "mountain", "trail", "hotel", "itinerary", "coast", "sunset"),
        "product_ecommerce": ("bundle", "workbook", "kit", "review", "unboxing", "price", "link in"),
        "crafts_diy": ("glue", "wood", "stitch", "paint", "sandpaper", "build", "craft", "fold"),
        "podcast_ugc": ("honestly", "story", "guest", "episode", "question", "opinion", "talk"),
        "gaming_screen": ("level", "boss", "fps", "hud", "match", "spawn", "controller", "gameplay"),
    }
    matched_signals = sorted(k for k, kws in signal_map.items() if any(kw in txt_all for kw in kws))
    niche_labels = matched_signals if matched_signals else ["UNKNOWN"]

    missing_inputs: list[str] = []
    if not phrases:
        missing_inputs.append("transcript_or_script")
    if not available_assets:
        missing_inputs.append("external_evidence_assets")
    if not tracked_entities:
        missing_inputs.append("verified_entity_tracks")

    ctx = {
        "schema_version": "kh03.editorial_context.v1",
        "support_status": "UNSUPPORTED" if unsupported_reasons else "SUPPORTED",
        "unsupported_reasons": unsupported_reasons,
        "format": media.get("aspect_ratio") or "unknown",
        "language": input_analysis.get("language") or "unknown",
        "audience_goal": input_analysis.get("audience_profile") or "general",
        "skill_id": skill_id,
        "duration_sec": round(nb_f / max(1.0, fps_num / float(fps_den)), 4) if nb_f > 0 and fps_num > 0 else 0.0,
        "fps_num": fps_num,
        "fps_den": fps_den,
        "canvas": {"width": w, "height": h, "aspect_ratio": media.get("aspect_ratio")},
        "niche_signals": niche_labels,
        "speech_spans": [
            {"phrase_id": p.get("phrase_id"), "start_frame": p.get("start_frame"), "end_frame": p.get("end_frame")}
            for p in phrases
        ],
        "semantic_beats": [
            {"phrase_id": p.get("phrase_id"), "role": p.get("semantic_role"), "has_numbers": bool(p.get("numeric_claims"))}
            for p in phrases
        ],
        "entities": list(tracked_entities or []),
        "available_assets": [a.get("asset_id") for a in (available_assets or []) if isinstance(a, dict)],
        "constraints": dict(constraints or input_analysis.get("constraints") or {}),
        "provenance": {
            "video_sha256": input_analysis.get("video_sha256"),
            "script_sha256": input_analysis.get("script_sha256"),
            "analysis_source": "local_media_and_semantic_parser",
        },
        "confidence": round(0.85 if (not unsupported_reasons and phrases) else 0.45, 3),
        "missing_inputs": missing_inputs,
    }
    if run_dir is not None:
        write_json_atomic(run_dir / "analysis" / "EDITORIAL_CONTEXT.json", ctx)
    return ctx
