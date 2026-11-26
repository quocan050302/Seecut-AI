"""KH-03.R4.2-05 — Generic Speech-First Alignment, Duplicate-Span Detection & Post-Render ASR Diff Tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.kh03 import speech_edl  # noqa: E402


class TestKH03R42SpeechEdlSuite(unittest.TestCase):
    def test_r42_05_generic_contraction_numeric_alignment_and_duplicate_removal(self):
        """Generic de-dup handles contractions/numbers, drops verified duplicates, protects intentional repeats & silences."""
        script = (
            "You can't ignore the numbers. Fifteen dollars plus twenty-five equals forty dollars. "
            "Watch when we remove these items. Planned cost drops from nine eighty to nine twenty, "
            "and margin doubles from sixty to one hundred twenty dollars. Same tool, same tool, better clarity."
        )
        # Synthetic generic ASR words containing:
        # 1) Exact duplicate across clip seam ("when we remove these items" repeated twice, take 2 cleaner!)
        # 2) Near-duplicate ("margin doubles from sixty" vs "margins double from sixty") -> PENDING_OWNER_REVIEW
        # 3) Intentional script-repeated phrase ("same tool") -> KEEP_INTENTIONAL_REPETITION
        words = [
            {"word": "You", "start": 0.0, "end": 0.2, "prob": 0.96},
            {"word": "can't", "start": 0.2, "end": 0.5, "prob": 0.95},
            {"word": "ignore", "start": 0.5, "end": 0.8, "prob": 0.97},
            {"word": "the", "start": 0.8, "end": 0.9, "prob": 0.98},
            {"word": "numbers.", "start": 0.9, "end": 1.3, "prob": 0.96},
            # Long silence gap 1.30 -> 2.30 (1.0s) safe to trim to ~0.18s!
            {"word": "$15", "start": 2.3, "end": 2.7, "prob": 0.95},
            {"word": "plus", "start": 2.7, "end": 2.9, "prob": 0.97},
            {"word": "25", "start": 2.9, "end": 3.2, "prob": 0.96},
            {"word": "equals", "start": 3.2, "end": 3.5, "prob": 0.95},
            {"word": "$40.", "start": 3.5, "end": 3.9, "prob": 0.96},
            {"word": "Watch", "start": 4.0, "end": 4.3, "prob": 0.95},
            # Take 1 (lower conf): 4.35 -> 5.65
            {"word": "when", "start": 4.35, "end": 4.55, "prob": 0.85},
            {"word": "we", "start": 4.55, "end": 4.70, "prob": 0.86},
            {"word": "remove", "start": 4.70, "end": 5.05, "prob": 0.87},
            {"word": "these", "start": 5.05, "end": 5.30, "prob": 0.88},
            {"word": "items,", "start": 5.30, "end": 5.65, "prob": 0.84},
            # Take 2 (higher conf): 5.85 -> 7.15
            {"word": "when", "start": 5.85, "end": 6.05, "prob": 0.97},
            {"word": "we", "start": 6.05, "end": 6.20, "prob": 0.98},
            {"word": "remove", "start": 6.20, "end": 6.55, "prob": 0.97},
            {"word": "these", "start": 6.55, "end": 6.80, "prob": 0.98},
            {"word": "items.", "start": 6.80, "end": 7.15, "prob": 0.97},
            # Unique adjacent clause MUST be preserved!
            {"word": "Planned", "start": 7.3, "end": 7.6, "prob": 0.96},
            {"word": "cost", "start": 7.6, "end": 7.9, "prob": 0.96},
            {"word": "drops", "start": 7.9, "end": 8.2, "prob": 0.95},
            {"word": "from", "start": 8.2, "end": 8.4, "prob": 0.97},
            {"word": "980", "start": 8.4, "end": 8.9, "prob": 0.96},
            {"word": "to", "start": 8.9, "end": 9.05, "prob": 0.97},
            {"word": "920,", "start": 9.05, "end": 9.55, "prob": 0.96},
            {"word": "and", "start": 9.6, "end": 9.8, "prob": 0.97},
            {"word": "margin", "start": 9.8, "end": 10.1, "prob": 0.96},
            {"word": "doubles", "start": 10.1, "end": 10.45, "prob": 0.95},
            {"word": "from", "start": 10.45, "end": 10.65, "prob": 0.97},
            {"word": "$60", "start": 10.65, "end": 11.0, "prob": 0.96},
            {"word": "to", "start": 11.0, "end": 11.15, "prob": 0.97},
            {"word": "$120.", "start": 11.15, "end": 11.7, "prob": 0.97},
            # Intentional script-approved repeat (3-word pattern test via explicit allowlist too)
            {"word": "Same", "start": 11.9, "end": 12.15, "prob": 0.96},
            {"word": "tool,", "start": 12.15, "end": 12.45, "prob": 0.96},
            {"word": "same", "start": 12.50, "end": 12.75, "prob": 0.96},
            {"word": "tool,", "start": 12.75, "end": 13.05, "prob": 0.96},
            {"word": "better", "start": 13.10, "end": 13.40, "prob": 0.96},
            {"word": "clarity.", "start": 13.40, "end": 13.85, "prob": 0.97},
        ]

        # 1) Normalization parity for contractions & spoken numbers
        self.assertEqual(speech_edl.normalize_spoken_tokens("nine eighty to nine twenty"), ["980", "to", "920"])
        self.assertEqual(speech_edl.normalize_spoken_tokens("one hundred twenty dollars"), ["120"])
        self.assertEqual(speech_edl.normalize_spoken_tokens("can't"), ["can", "not"])

        # 2) Alignment matches every mandatory numeric value
        al = speech_edl.align_asr_to_script(words, script)
        self.assertEqual(al["missing_numbers"], [])
        self.assertGreaterEqual(al["coverage_ratio"], 0.90)

        # 3) Exact duplicate detected, take 2 kept, take 1 dropped with exact frame & 48kHz sample spans
        dups = speech_edl.detect_duplicate_spans(words, script, fps_num=24, fps_den=1, sample_rate=48000)
        self.assertEqual(dups["verified_drop_count"], 1)
        d0 = dups["duplicates"][0]
        self.assertEqual(d0["decision"], "DROP")
        self.assertEqual(d0["keep_take"], "SECOND")
        self.assertEqual(d0["drop_word_indices"], [11, 16])
        self.assertGreater(d0["drop_end_frame"], d0["drop_start_frame"])
        self.assertGreater(d0["drop_end_sample"], d0["drop_start_sample"])

        # 4) Intentional repetition (3+ words) protected when present twice in approved script
        rep_script = "Keep moving forward. Keep moving forward. Finish strong."
        rep_words = [
            {"word": "Keep", "start": 0.0, "end": 0.2, "prob": 0.95},
            {"word": "moving", "start": 0.2, "end": 0.5, "prob": 0.95},
            {"word": "forward.", "start": 0.5, "end": 0.9, "prob": 0.95},
            {"word": "Keep", "start": 1.0, "end": 1.2, "prob": 0.96},
            {"word": "moving", "start": 1.2, "end": 1.5, "prob": 0.96},
            {"word": "forward.", "start": 1.5, "end": 1.9, "prob": 0.96},
            {"word": "Finish", "start": 2.0, "end": 2.3, "prob": 0.97},
            {"word": "strong.", "start": 2.3, "end": 2.7, "prob": 0.97},
        ]
        rep_dups = speech_edl.detect_duplicate_spans(rep_words, rep_script)
        self.assertEqual(rep_dups["verified_drop_count"], 0)
        self.assertEqual(rep_dups["intentional_kept_count"], 1)

        # 5) Near-duplicate flagged PENDING_OWNER_REVIEW (never silently auto-dropped!)
        near_words = [
            {"word": "check", "start": 0.0, "end": 0.2, "prob": 0.9},
            {"word": "your", "start": 0.2, "end": 0.4, "prob": 0.9},
            {"word": "monthly", "start": 0.4, "end": 0.7, "prob": 0.9},
            {"word": "budget", "start": 0.7, "end": 1.0, "prob": 0.9},
            {"word": "plan", "start": 1.0, "end": 1.3, "prob": 0.9},
            {"word": "check", "start": 1.5, "end": 1.7, "prob": 0.95},
            {"word": "your", "start": 1.7, "end": 1.9, "prob": 0.95},
            {"word": "monthly", "start": 1.9, "end": 2.2, "prob": 0.95},
            {"word": "budget", "start": 2.2, "end": 2.5, "prob": 0.95},
            {"word": "sheet", "start": 2.5, "end": 2.8, "prob": 0.95},
        ]
        near_dups = speech_edl.detect_duplicate_spans(near_words, "Check your monthly budget sheet.", near_dup_ratio=0.75)
        self.assertEqual(near_dups["verified_drop_count"], 0)
        self.assertEqual(near_dups["pending_owner_review_count"], 1)

        # 6) Speech-first EDL trims verified duplicate + safe silence gap, preserves all unique words & outputs 4 manifests
        with tempfile.TemporaryDirectory(prefix="kh03_r42_edl_") as td:
            out_dir = Path(td)
            edl_bundle = speech_edl.build_speech_first_edl(
                {"source_id": "primary_aroll", "video_duration_sec": 14.0, "sha256": "a" * 64},
                words,
                dups,
                silence_spans=[{"start_sec": 1.32, "end_sec": 2.28}],
                target_fps_num=24,
                target_fps_den=1,
                out_dir=out_dir,
            )
            for fname in ("VOICE_EDL.json", "MASTER_EDL.json", "SOURCE_PTS_MAP.json", "SOURCE_HASH_MANIFEST.json"):
                self.assertTrue((out_dir / fname).is_file(), f"Missing {fname}")
            vedl = edl_bundle["voice_edl"]
            self.assertEqual(vedl["dropped_word_count"], 5)  # exact 5 duplicated words dropped
            self.assertLess(vedl["output_duration_sec"], 14.0)
            self.assertGreaterEqual(len(vedl["cuts_applied"]), 2)  # duplicate + safe silence

            # Verify mapped words pass post-render ASR check cleanly!
            post_words = [
                {"word": mw["word"], "start": mw["out_start_sec"], "end": mw["out_end_sec"], "prob": 0.96}
                for mw in edl_bundle["source_pts_map"]["mapped_words"]
            ]
            asr_ok = speech_edl.verify_post_render_asr(post_words, script, out_path=out_dir / "FINAL_ASR_DIFF.json")
            self.assertTrue(asr_ok["pass"], asr_ok)

            # Negative check: dropping a mandatory number ($120) MUST fail verify_post_render_asr!
            missing_num_words = [w for w in post_words if "120" not in str(w["word"])]
            asr_fail_num = speech_edl.verify_post_render_asr(missing_num_words, script)
            self.assertFalse(asr_fail_num["pass"])
            self.assertIn("120", asr_fail_num["missing_numbers"])

            # Negative check: keeping the raw undeduplicated words MUST fail verify_post_render_asr!
            asr_fail_dup = speech_edl.verify_post_render_asr(words, script)
            self.assertFalse(asr_fail_dup["pass"])
            self.assertGreaterEqual(asr_fail_dup["unintended_duplicate_count"], 1)


if __name__ == "__main__":
    unittest.main()
