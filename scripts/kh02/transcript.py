"""KH-02 Transcript Normalization & Phrase Packing.

Selective adaptation of phrase-grouping and compact markdown packing concepts from:
  browser-use/video-use @ 43cfc566833548093455c9d4a46bd69913b7e552
  helpers/pack_transcripts.py (MIT License, Copyright (c) 2026 Browser Use).
Adapted to local faster-whisper word timestamps (no ElevenLabs Scribe API calls).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from .contracts import is_strict_number

FILLER_WORDS = {
    "um", "uh", "er", "ah", "hmm", "mm", "uhm", "like",
    "ờ", "à", "ừm", "ơ", "kiểu",
}


def _clean_word_text(w: dict[str, Any]) -> str:
    txt = w.get("word") if "word" in w else w.get("text", "")
    return str(txt or "").strip()


def normalize_faster_whisper(
    raw_data: dict[str, Any] | list[dict[str, Any]],
    *,
    source_asset_id: str,
    audio_sha256: str,
    language: str = "en",
    model: str = "faster-whisper:small",
    pause_threshold_sec: float = 0.5,
) -> dict[str, Any]:
    """Normalize faster-whisper output (segments[].words[] OR flat list) into canonical KH-02 transcript."""
    raw_words: list[dict[str, Any]] = []
    lang = language
    lang_prob = None
    dur_sec = 0.0

    if isinstance(raw_data, list):
        raw_words = raw_data
    elif isinstance(raw_data, dict):
        lang = str(raw_data.get("language") or language)
        lp = raw_data.get("language_probability")
        lang_prob = float(lp) if is_strict_number(lp) else None
        ds = raw_data.get("duration_sec")
        if is_strict_number(ds) and ds >= 0:
            dur_sec = float(ds)
        if isinstance(raw_data.get("segments"), list) and any(
            isinstance(seg, dict) and isinstance(seg.get("words"), list) for seg in raw_data["segments"]
        ):
            for seg in raw_data["segments"]:
                speaker = str(seg.get("speaker") or "UNKNOWN")
                for w in seg.get("words") or []:
                    item = dict(w)
                    item.setdefault("speaker", speaker)
                    raw_words.append(item)
        elif isinstance(raw_data.get("words"), list):
            raw_words = list(raw_data["words"])
        else:
            raise ValueError("Unsupported faster-whisper payload: expected segments[].words[] or words[]")
    else:
        raise ValueError(f"Unsupported transcript payload type: {type(raw_data).__name__}")

    words: list[dict[str, Any]] = []
    warnings: list[str] = []
    prev_end = 0.0

    for idx, rw in enumerate(raw_words):
        if not isinstance(rw, dict):
            warnings.append(f"raw_word[{idx}] skipped: not an object")
            continue
        text = _clean_word_text(rw)
        if not text:
            continue
        st = rw.get("start", rw.get("start_sec"))
        en = rw.get("end", rw.get("end_sec"))
        if not is_strict_number(st) or not is_strict_number(en):
            raise ValueError(f"Invalid or missing timestamp on word {idx} ({text!r}): start={st!r}, end={en!r}")
        st_f, en_f = float(st), float(en)
        if st_f < 0 or en_f < st_f:
            raise ValueError(f"Invalid word timing range on word {idx} ({text!r}): [{st_f}, {en_f}]")
        if st_f + 1e-6 < prev_end:
            warnings.append(
                f"overlap_detected word[{idx}] ({text!r}) start={st_f:.3f} < prev_end={prev_end:.3f}; preserved raw timing"
            )
        prev_end = max(prev_end, en_f)
        dur_sec = max(dur_sec, en_f)

        prob = rw.get("probability", rw.get("confidence"))
        conf = round(float(prob), 4) if is_strict_number(prob) else None
        speaker = str(rw.get("speaker") or "UNKNOWN")
        bare = re.sub(r"[^\w\u00C0-\u024F\u1E00-\u1EFF\u4e00-\u9fff]+", "", text).lower()

        words.append({
            "word_id": f"w{len(words) + 1:04d}",
            "text": text,
            "start_sec": round(st_f, 4),
            "end_sec": round(en_f, 4),
            "speaker": speaker,
            "confidence": conf,
            "is_filler": bare in FILLER_WORDS,
        })

    phrases = group_phrases(words, pause_threshold_sec=pause_threshold_sec)
    return {
        "schema_version": "kh02.transcript.v1",
        "source_asset_id": source_asset_id,
        "audio_sha256": audio_sha256,
        "language": lang,
        "language_probability": lang_prob,
        "model": model,
        "duration_sec": round(dur_sec, 4),
        "warnings": warnings,
        "words": words,
        "phrases": phrases,
    }


def group_phrases(words: list[dict[str, Any]], *, pause_threshold_sec: float = 0.5) -> list[dict[str, Any]]:
    """Group normalized words into time-bounded phrases by pause threshold or speaker switch.

    Adapted from browser-use/video-use helpers/pack_transcripts.py::group_into_phrases (MIT).
    """
    phrases: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    prev_end = 0.0

    def flush(gap_before: float) -> None:
        if not cur:
            return
        # Join CJK cleanly or space-join Latin/Vietnamese
        parts = [w["text"] for w in cur]
        joined = " ".join(parts)
        joined = re.sub(r"\s+([,.;:!?])", r"\1", joined)
        phrases.append({
            "phrase_id": f"ph{len(phrases) + 1:03d}",
            "start_sec": cur[0]["start_sec"],
            "end_sec": cur[-1]["end_sec"],
            "speaker": cur[0]["speaker"],
            "word_ids": [w["word_id"] for w in cur],
            "text": joined.strip(),
            "gap_before_sec": round(max(0.0, gap_before), 3),
        })

    phrase_gap = 0.0
    for w in words:
        if not cur:
            phrase_gap = max(0.0, w["start_sec"] - prev_end)
            cur = [w]
            prev_end = w["end_sec"]
            continue
        gap = w["start_sec"] - cur[-1]["end_sec"]
        speaker_changed = w["speaker"] != cur[-1]["speaker"]
        if gap >= pause_threshold_sec or speaker_changed:
            flush(phrase_gap)
            phrase_gap = max(0.0, gap)
            cur = [w]
        else:
            cur.append(w)
        prev_end = max(prev_end, w["end_sec"])

    flush(phrase_gap)
    return phrases


def build_packed_transcript(transcript: dict[str, Any], *, title: str | None = None) -> str:
    """Render compact human- and model-readable TAKES_PACKED.md (adapted from video-use MIT)."""
    lines: list[str] = []
    hdr = title or transcript.get("source_asset_id", "source")
    lines.append(f"# Packed Transcript — {hdr}")
    lines.append("")
    lines.append(f"- **Source Asset**: `{transcript['source_asset_id']}`")
    lines.append(f"- **Audio SHA-256**: `{transcript['audio_sha256']}`")
    lines.append(f"- **Language**: `{transcript['language']}` | **Model**: `{transcript['model']}`")
    lines.append(f"- **Duration**: `{transcript.get('duration_sec', 0.0):.3f}s` | **Words**: `{len(transcript['words'])}` | **Phrases**: `{len(transcript['phrases'])}`")
    lines.append("")
    lines.append("## Phrases")
    lines.append("")
    for ph in transcript.get("phrases", []):
        gap_note = f" *(pause {ph['gap_before_sec']:.2f}s)*" if ph["gap_before_sec"] >= 0.4 else ""
        spk = f"[{ph['speaker']}] " if ph["speaker"] != "UNKNOWN" else ""
        w_span = f"{ph['word_ids'][0]}..{ph['word_ids'][-1]}"
        lines.append(f"- `{ph['phrase_id']}` `[{ph['start_sec']:06.2f}s -> {ph['end_sec']:06.2f}s]` ({w_span}){gap_note}: {spk}{ph['text']}")
    lines.append("")
    if transcript.get("warnings"):
        lines.append("## Warnings")
        for w in transcript["warnings"]:
            lines.append(f"- {w}")
        lines.append("")
    return "\n".join(lines)


def transcript_cache_key(audio_sha256: str, model: str, language: str, options: dict[str, Any] | None = None) -> str:
    payload = json.dumps(
        {"audio_sha256": audio_sha256, "model": model, "language": language, "options": options or {}},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_transcript(
    cache_dir: str | Path,
    audio_sha256: str,
    model: str,
    language: str,
    *,
    transcript: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Read or write deterministic transcript cache keyed by audio SHA + model + language + options."""
    key = transcript_cache_key(audio_sha256, model, language, options)
    cdir = Path(cache_dir)
    cdir.mkdir(parents=True, exist_ok=True)
    path = cdir / f"transcript_{key[:24]}.json"
    if transcript is not None:
        to_save = dict(transcript)
        to_save["cache_key"] = key
        path.write_text(json.dumps(to_save, ensure_ascii=False, indent=2), encoding="utf-8")
        return key, to_save
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if loaded.get("cache_key") == key and loaded.get("audio_sha256") == audio_sha256:
            return key, loaded
    return key, None


def map_words_to_output(transcript: dict[str, Any], time_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Map normalized source words onto output timeline seconds/frames via piecewise EDL time_map."""
    fps_num = int(time_map["fps_num"])
    fps_den = int(time_map["fps_den"])
    fps = fps_num / fps_den
    mapped: list[dict[str, Any]] = []
    segments = time_map.get("segments", [])
    src_id = transcript["source_asset_id"]

    for w in transcript.get("words", []):
        ws = float(w["start_sec"])
        we = float(w["end_sec"])
        for seg in segments:
            if seg["source_asset_id"] != src_id:
                continue
            seg_s0 = seg["src_start_frame"] / fps
            seg_s1 = seg["src_end_frame"] / fps
            if we <= seg_s0 + 1e-6 or ws >= seg_s1 - 1e-6:
                continue
            clamped_s = max(ws, seg_s0)
            clamped_e = min(we, seg_s1)
            out_base = seg["out_start_frame"] / fps
            out_s = out_base + (clamped_s - seg_s0)
            out_e = out_base + (clamped_e - seg_s0)
            out_sf = int(round(out_s * fps))
            out_ef = max(out_sf + 1, int(round(out_e * fps)))
            mapped.append({
                "word_id": w["word_id"],
                "text": w["text"],
                "segment_id": seg["segment_id"],
                "src_start_sec": w["start_sec"],
                "src_end_sec": w["end_sec"],
                "out_start_sec": round(out_s, 4),
                "out_end_sec": round(out_e, 4),
                "out_start_frame": out_sf,
                "out_end_frame": out_ef,
                "confidence": w.get("confidence", 0.95),
                "speaker": w.get("speaker"),
            })
            break
    mapped.sort(key=lambda x: (x["out_start_frame"], x["word_id"]))
    return mapped


def run_local_faster_whisper(
    media_path: str | Path,
    *,
    venv_python: str | Path,
    model_name: str = "small",
    language: str | None = None,
    cpu_threads: int = 2,
) -> dict[str, Any]:
    """Run local faster-whisper via safe ffmpeg 16kHz float32 PCM pipe (immune to PyAV 19 metadata_errors)."""
    script = r'''
import json, subprocess, sys
import numpy as np
from faster_whisper import WhisperModel

src, model_name, lang, threads = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
proc = subprocess.run(
    ["ffmpeg", "-v", "error", "-i", src, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
    capture_output=True, check=True
)
audio = np.frombuffer(proc.stdout, dtype=np.float32)
model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=threads)
kwargs = {"word_timestamps": True}
if lang and lang != "auto":
    kwargs["language"] = lang
segs_iter, info = model.transcribe(audio, **kwargs)
segments = []
for i, s in enumerate(segs_iter):
    words = [
        {"word": w.word, "start": round(float(w.start), 4), "end": round(float(w.end), 4), "probability": round(float(w.probability), 4)}
        for w in (s.words or [])
    ]
    segments.append({"id": i, "start": round(float(s.start), 4), "end": round(float(s.end), 4), "text": s.text.strip(), "words": words})
print(json.dumps({
    "language": info.language,
    "language_probability": round(float(info.language_probability), 4),
    "duration_sec": round(float(info.duration), 4),
    "model": f"faster-whisper:{model_name}",
    "segments": segments,
}, ensure_ascii=False))
'''
    proc = subprocess.run(
        [str(venv_python), "-c", script, str(media_path), model_name, language or "auto", str(cpu_threads)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"faster-whisper failed (rc={proc.returncode}): {proc.stderr[-300:]}")
    return json.loads(proc.stdout)
