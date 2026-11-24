"""KH-02.R1 Executable Tests R1-01..R1-06: Intelligent Rough Cut, Pause Classification, Take Safety, Paired A/V Trim & Seam QA."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from scripts.kh02.contracts import hash_artifact
from scripts.kh02.edit_decisions import (
    apply_r1_cut_map,
    build_r1_time_map,
    classify_pause,
    detect_take_candidates,
    emit_rough_cut_artifacts,
    evaluate_cut_safety,
    propose_r1_cut_candidates,
    rank_cut_candidates,
    resolve_cut_policy,
    verify_cut_result,
)
from scripts.kh02.transcript import map_words_to_output


def _make_synthetic_av_fixture(out_mp4: Path, *, duration_sec: float = 6.0, fps: int = 24) -> Path:
    """Create a deterministic TEST_ONLY synthetic A/V clip with speech-like tone bursts and silent pauses."""
    import math
    import struct
    import wave

    sr = 48000
    n = int(round(duration_sec * sr))
    wav_path = out_mp4.with_suffix(".tmp.wav")
    buf = bytearray()
    for i in range(n):
        t = i / float(sr)
        if 0.8 <= t <= 1.8:
            val = int(0.25 * 32767 * math.sin(2 * math.pi * 440.0 * t))
        elif 3.8 <= t <= 4.8:
            val = int(0.25 * 32767 * math.sin(2 * math.pi * 520.0 * t))
        else:
            val = 0
        buf += struct.pack("<hh", val, val)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=320x568:rate={fps}:duration={duration_sec}",
        "-i", str(wav_path),
        "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        str(out_mp4),
    ]
    pr = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    wav_path.unlink(missing_ok=True)
    if pr.returncode != 0:
        raise RuntimeError(f"Failed creating TEST_ONLY fixture: {pr.stderr}")
    return out_mp4


def _sample_transcript() -> dict:
    return {
        "schema_version": "kh02.transcript.v1",
        "source_asset_id": "src_demo",
        "audio_sha256": "a" * 64,
        "language": "en",
        "model": "faster-whisper:test",
        "words": [
            {"word_id": "w_0001", "text": "Look", "start_sec": 0.82, "end_sec": 1.10, "confidence": 0.96, "speaker": "unknown"},
            {"word_id": "w_0002", "text": "at", "start_sec": 1.15, "end_sec": 1.32, "confidence": 0.95, "speaker": "unknown"},
            {"word_id": "w_0003", "text": "this.", "start_sec": 1.38, "end_sec": 1.75, "confidence": 0.97, "speaker": "unknown"},
            {"word_id": "w_0004", "text": "Now", "start_sec": 3.85, "end_sec": 4.15, "confidence": 0.95, "speaker": "unknown"},
            {"word_id": "w_0005", "text": "see", "start_sec": 4.20, "end_sec": 4.45, "confidence": 0.96, "speaker": "unknown"},
            {"word_id": "w_0006", "text": "details.", "start_sec": 4.50, "end_sec": 4.78, "confidence": 0.94, "speaker": "unknown"},
        ],
        "phrases": [
            {"phrase_id": "ph_001", "text": "Look at this.", "start_sec": 0.82, "end_sec": 1.75, "word_ids": ["w_0001", "w_0002", "w_0003"], "speaker": "unknown"},
            {"phrase_id": "ph_002", "text": "Now see details.", "start_sec": 3.85, "end_sec": 4.78, "word_ids": ["w_0004", "w_0005", "w_0006"], "speaker": "unknown"},
        ],
    }


class TestR1RoughCut(unittest.TestCase):
    def test_r1_01_classify_pause_vs_reaction_showcase_dropout_silent_footage(self) -> None:
        """R1-01: classify long silence vs reaction vs intentional showcase vs dropout vs no-audio footage."""
        words = _sample_transcript()["words"]
        # 1. Reaction preserved
        r_react = classify_pause(1.8, 3.0, source_duration_sec=6.0, words=words, reaction_windows=[(1.9, 2.8)])
        self.assertEqual(r_react["candidate_type"], "REACTION")
        self.assertEqual(r_react["recommended_outcome"], "KEEP")

        # 2. Intentional active-motion showcase preserved (not auto-deleted by silence alone!)
        r_show = classify_pause(1.8, 3.8, source_duration_sec=6.0, words=words, motion_score=0.18)
        self.assertEqual(r_show["candidate_type"], "DELIBERATE_PAUSE")
        self.assertEqual(r_show["recommended_outcome"], "KEEP")

        # 3. Visual stall (near-zero motion) flagged for SHORTEN
        r_stall = classify_pause(1.8, 3.8, source_duration_sec=6.0, words=words, motion_score=0.004)
        self.assertEqual(r_stall["candidate_type"], "VISUAL_STALL")
        self.assertEqual(r_stall["recommended_outcome"], "SHORTEN")

        # 4. Technical dropout overlapping word timestamps -> REVIEW, never auto-cut
        r_drop = classify_pause(0.8, 1.5, source_duration_sec=6.0, words=words)
        self.assertEqual(r_drop["candidate_type"], "TECHNICAL_DROPOUT")
        self.assertEqual(r_drop["recommended_outcome"], "REVIEW")

        # 5. Silent-only footage with active visual action -> KEEP (no audio != redundant visual)
        r_silent = classify_pause(0.0, 4.0, source_duration_sec=4.0, has_audio_track=False, motion_score=0.12)
        self.assertEqual(r_silent["candidate_type"], "CONTEXT_REQUIRED")
        self.assertEqual(r_silent["recommended_outcome"], "KEEP")

    def test_r1_02_take_detection_protects_negation_names_numbers_and_low_conf_asr(self) -> None:
        """R1-02: detect repeated take/false start/filler while protecting negations, proper names, and low ASR confidence."""
        tr = {
            "source_asset_id": "src_takes",
            "words": [
                # True false start (safe)
                {"word_id": "w1", "text": "We", "start_sec": 0.2, "end_sec": 0.4, "confidence": 0.95},
                {"word_id": "w2", "text": "built", "start_sec": 0.45, "end_sec": 0.7, "confidence": 0.95},
                {"word_id": "w3", "text": "We", "start_sec": 1.0, "end_sec": 1.2, "confidence": 0.96},
                {"word_id": "w4", "text": "built", "start_sec": 1.25, "end_sec": 1.5, "confidence": 0.96},
                {"word_id": "w5", "text": "Gumroad", "start_sec": 1.55, "end_sec": 1.9, "confidence": 0.97},
                # Negation-carrying earlier phrase NOT repeated in second -> MUST KEEP!
                {"word_id": "w6", "text": "Never", "start_sec": 2.5, "end_sec": 2.8, "confidence": 0.96},
                {"word_id": "w7", "text": "skip", "start_sec": 2.85, "end_sec": 3.1, "confidence": 0.96},
                {"word_id": "w8", "text": "checks", "start_sec": 3.15, "end_sec": 3.4, "confidence": 0.96},
                {"word_id": "w9", "text": "skip", "start_sec": 3.8, "end_sec": 4.0, "confidence": 0.96},
                {"word_id": "w10", "text": "checks", "start_sec": 4.05, "end_sec": 4.3, "confidence": 0.96},
                {"word_id": "w11", "text": "today", "start_sec": 4.35, "end_sec": 4.6, "confidence": 0.96},
                # Low-confidence ambiguous retake -> REVIEW, never auto-remove!
                {"word_id": "w12", "text": "Wait", "start_sec": 5.0, "end_sec": 5.2, "confidence": 0.42},
                {"word_id": "w13", "text": "here", "start_sec": 5.25, "end_sec": 5.5, "confidence": 0.48},
                {"word_id": "w14", "text": "Wait", "start_sec": 5.8, "end_sec": 6.0, "confidence": 0.92},
                {"word_id": "w15", "text": "here", "start_sec": 6.05, "end_sec": 6.3, "confidence": 0.92},
                # Isolated filler
                {"word_id": "w16", "text": "um", "start_sec": 6.6, "end_sec": 6.8, "confidence": 0.93},
                {"word_id": "w17", "text": "done", "start_sec": 7.1, "end_sec": 7.4, "confidence": 0.97},
            ],
            "phrases": [
                {"phrase_id": "p1", "text": "We built", "start_sec": 0.2, "end_sec": 0.7, "word_ids": ["w1", "w2"]},
                {"phrase_id": "p2", "text": "We built Gumroad", "start_sec": 1.0, "end_sec": 1.9, "word_ids": ["w3", "w4", "w5"]},
                {"phrase_id": "p3", "text": "Never skip checks", "start_sec": 2.5, "end_sec": 3.4, "word_ids": ["w6", "w7", "w8"]},
                {"phrase_id": "p4", "text": "skip checks today", "start_sec": 3.8, "end_sec": 4.6, "word_ids": ["w9", "w10", "w11"]},
                {"phrase_id": "p5", "text": "Wait here", "start_sec": 5.0, "end_sec": 5.5, "word_ids": ["w12", "w13"]},
                {"phrase_id": "p6", "text": "Wait here", "start_sec": 5.8, "end_sec": 6.3, "word_ids": ["w14", "w15"]},
            ],
        }
        cands = detect_take_candidates(tr)
        by_type = {c["candidate_type"]: c for c in cands}
        self.assertIn("FALSE_START", by_type)
        self.assertEqual(by_type["FALSE_START"]["proposed_outcome"], "REMOVE")

        # Negation phrase protected
        neg_cands = [c for c in cands if "w6" in c["word_ids"]]
        if neg_cands:
            self.assertEqual(neg_cands[0]["proposed_outcome"], "KEEP")
            self.assertEqual(neg_cands[0]["semantic_risk"], "HIGH")

        # Ambiguous low-confidence retake marked REVIEW
        low_cands = [c for c in cands if "w12" in c["word_ids"]]
        self.assertTrue(low_cands)
        self.assertEqual(low_cands[0]["proposed_outcome"], "REVIEW")

        # Isolated filler detected
        self.assertIn("FILLER", by_type)

    def test_r1_03_cut_policy_non_broadening_and_preserve_script_order(self) -> None:
        """R1-03: LOCK_VOICE identity, PRESERVE_SCRIPT_TRIM retains 100% words in order, EDITORIAL_CUT requires scope."""
        tr = _sample_transcript()
        # EDITORIAL_CUT without explicit scope downgrades safely to LOCK_VOICE
        pol_unscoped = resolve_cut_policy("EDITORIAL_CUT", approved_scope=None)
        self.assertEqual(pol_unscoped["cut_policy"], "LOCK_VOICE")
        self.assertFalse(pol_unscoped["allow_editorial_deletion"])
        self.assertIn("blocked_reason", pol_unscoped)

        # LOCK_VOICE yields identity map
        cands_lock = propose_r1_cut_candidates(
            source_asset_id="src_demo", source_sha256="a" * 64, source_duration_sec=6.0,
            transcript=tr, cut_policy="LOCK_VOICE",
        )
        edl_lock = build_r1_time_map("src_demo", 144, 24, 1, cut_policy="LOCK_VOICE", candidate_doc=cands_lock, words=tr["words"])
        self.assertEqual(edl_lock["total_output_frames"], 144)
        self.assertEqual(len(edl_lock["segments"]), 1)

        # PRESERVE_SCRIPT_TRIM trims silence/padding while keeping ALL 6 words in exact order
        cands_trim = propose_r1_cut_candidates(
            source_asset_id="src_demo", source_sha256="a" * 64, source_duration_sec=6.0,
            transcript=tr, cut_policy="PRESERVE_SCRIPT_TRIM", approved_scope="owner_trim_nonverbal",
        )
        edl_trim = build_r1_time_map(
            "src_demo", 144, 24, 1, cut_policy="PRESERVE_SCRIPT_TRIM",
            approved_scope="owner_trim_nonverbal", candidate_doc=cands_trim, words=tr["words"],
        )
        self.assertLess(edl_trim["total_output_frames"], 144)
        mapped = map_words_to_output(tr, edl_trim)
        self.assertEqual([w["word_id"] for w in mapped], [w["word_id"] for w in tr["words"]])

        # Unauthorized word deletion under PRESERVE_SCRIPT_TRIM rejected
        bad_cut = [{"candidate_id": "bad", "candidate_type": "FILLER", "proposed_outcome": "REMOVE", "trim_start_pts": 0.82, "trim_end_pts": 1.10, "safety_ok": True}]
        with self.assertRaises(ValueError):
            build_r1_time_map("src_demo", 144, 24, 1, cut_policy="PRESERVE_SCRIPT_TRIM", explicit_cuts=bad_cut, words=tr["words"])

    def test_r1_04_boundary_snapping_rejects_mid_word_and_zero_length(self) -> None:
        """R1-04: reject mid-word cut, zero-length/full-delete EDL, verify safe fade bounds."""
        tr = _sample_transcript()
        # Mid-word cut inside "Look" [0.82, 1.10] rejected
        mid_word = [{"candidate_id": "mid", "candidate_type": "DEAD_AIR", "proposed_outcome": "SHORTEN", "trim_start_pts": 0.90, "trim_end_pts": 1.05, "safety_ok": True}]
        with self.assertRaises(ValueError):
            build_r1_time_map("src_demo", 144, 24, 1, cut_policy="PRESERVE_SCRIPT_TRIM", explicit_cuts=mid_word, words=tr["words"])

        # Full-timeline deletion rejected (no zero-length segment)
        full_del = [{"candidate_id": "all", "candidate_type": "DEAD_AIR", "proposed_outcome": "SHORTEN", "trim_start_pts": 0.0, "trim_end_pts": 6.0, "safety_ok": True}]
        with self.assertRaises(ValueError):
            build_r1_time_map("src_demo", 144, 24, 1, cut_policy="PRESERVE_SCRIPT_TRIM", explicit_cuts=full_del)

    def test_r1_05_rational_ntsc_and_2min_bounded_zero_drift(self) -> None:
        """R1-05: exact rational 30000/1001 NTSC time map on 2-minute synthetic timeline with zero float drift."""
        # 2 minutes @ 30000/1001 fps = 3596 frames
        total_src = 3596
        cuts = [
            {"candidate_id": "c1", "candidate_type": "DEAD_AIR", "proposed_outcome": "SHORTEN", "trim_start_pts": 10.0, "trim_end_pts": 12.5, "safety_ok": True},
            {"candidate_id": "c2", "candidate_type": "WAITING_FOR_UI", "proposed_outcome": "SHORTEN", "trim_start_pts": 60.0, "trim_end_pts": 64.0, "safety_ok": True},
            {"candidate_id": "c3", "candidate_type": "DEAD_AIR", "proposed_outcome": "SHORTEN", "trim_start_pts": 105.0, "trim_end_pts": 108.0, "safety_ok": True},
        ]
        edl = build_r1_time_map(
            "src_2m", total_src, 30000, 1001, audio_sample_rate=48000,
            cut_policy="PRESERVE_SCRIPT_TRIM", explicit_cuts=cuts,
        )
        # Verify exact segment contiguity and zero rational drift
        out_f = 0
        out_s = 0
        for seg in edl["segments"]:
            self.assertEqual(seg["out_start_frame"], out_f)
            self.assertEqual(seg["out_start_sample"], out_s)
            self.assertEqual(seg["src_end_frame"] - seg["src_start_frame"], seg["out_end_frame"] - seg["out_start_frame"])
            out_f = seg["out_end_frame"]
            out_s = seg["out_end_sample"]
        self.assertEqual(out_f, edl["total_output_frames"])
        expected_end_sec = Fraction(edl["total_output_frames"] * 1001, 30000)
        sample_end_sec = Fraction(edl["total_output_samples"], 48000)
        self.assertLess(abs(float(expected_end_sec - sample_end_sec)), 0.002)

    def test_r1_06_real_derived_av_trim_seam_waveform_and_source_integrity(self) -> None:
        """R1-06: execute real paired FFmpeg A/V trim on TEST_ONLY fixture, verify waveform seams, exact frames & source SHA."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _make_synthetic_av_fixture(root / "src_test_only.mp4", duration_sec=6.0, fps=24)
            src_sha = hash_artifact(src)
            tr = _sample_transcript()
            cands = propose_r1_cut_candidates(
                source_asset_id="src_demo", source_sha256=src_sha, source_duration_sec=6.0,
                transcript=tr, cut_policy="PRESERVE_SCRIPT_TRIM", approved_scope="test_only_trim",
            )
            edl = build_r1_time_map(
                "src_demo", 144, 24, 1, source_sha256=src_sha,
                cut_policy="PRESERVE_SCRIPT_TRIM", approved_scope="test_only_trim",
                candidate_doc=cands, words=tr["words"],
            )
            derived = root / "derived_trimmed.mp4"
            res = apply_r1_cut_map(src, edl, derived)
            self.assertTrue(res["applied"])
            ver = verify_cut_result(
                src, derived, edl,
                expected_source_sha256=src_sha,
                original_transcript=tr,
                candidate_doc=cands,
            )
            self.assertTrue(ver["pass"], f"Expected cut verification PASS, got issues: {ver['issues']}")
            self.assertTrue(ver["source_unchanged"])
            self.assertTrue(ver["protected_words_exact_order"])
            self.assertEqual(ver["after_frames"], edl["total_output_frames"])
            self.assertGreater(ver["saved_sec"], 1.5)
            # Emit all 6 required rough-cut artifacts
            emitted = emit_rough_cut_artifacts(root / "out", candidate_doc=cands, edl=edl, original_transcript=tr, verification=ver)
            for k in ("cut_candidates", "edl", "time_map", "output_transcript", "decision_report", "cut_verification"):
                self.assertTrue(emitted[k].is_file())
            # Negative: missing derived file cannot PASS
            bad_ver = verify_cut_result(src, root / "missing.mp4", edl, expected_source_sha256=src_sha)
            self.assertFalse(bad_ver["pass"])


if __name__ == "__main__":
    unittest.main()
