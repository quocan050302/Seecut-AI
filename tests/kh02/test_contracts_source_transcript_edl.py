"""KH-02 Contract, Rights, Source Inventory, Transcript & Rational EDL Tests (T10-T15)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.kh02 import contracts, edit_decisions, run, sound, source_analysis, transcript, typography  # noqa: E402


def _make_synth_mp4(dst: Path, duration: float = 2.0, fps: int = 24, size: str = "320x180", rotate: int = 0) -> Path:
    tmp = dst.with_suffix(".pre_rot.mp4") if rotate else dst
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
        str(tmp),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    if rotate:
        r_dm = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-display_rotation:v:0", str(rotate), "-i", str(tmp), "-c", "copy", str(dst)],
            capture_output=True,
        )
        if r_dm.returncode != 0:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-c", "copy", "-metadata:s:v:0", f"rotate={rotate}", str(dst)],
                check=True, capture_output=True,
            )
        tmp.unlink(missing_ok=True)
    return dst


class TestContractsSourceTranscriptEDL(unittest.TestCase):
    def test_t10_strict_json_schema_and_negative_shotbook(self):
        """T10: rejects duplicate JSON keys, NaN/Inf, bool-as-number, dangling IDs & invalid negative template."""
        with self.assertRaises(ValueError):
            contracts.parse_json_strict('{"a": 1, "a": 2}')
        with self.assertRaises(ValueError):
            contracts.parse_json_strict('{"x": NaN}')
        with self.assertRaises(ValueError):
            contracts.parse_json_strict('{"x": Infinity}')

        # Bool masquerading as number in schema
        bad_edl = {
            "schema_version": "kh02.edl.v1",
            "voice_policy": "LOCK_VOICE",
            "fps_num": True,
            "fps_den": 1,
            "audio_sample_rate": 48000,
            "total_output_frames": 24,
            "segments": [],
        }
        errs = contracts.validate_schema(bad_edl, "edl")
        self.assertTrue(any(e["code"] == "SCHEMA_TYPE" and "fps_num" in e["location"] for e in errs), errs)

        # Positive example passes, negative example fails cleanly with structured errors
        pos = contracts.parse_json_strict(REPO_ROOT / "templates/kh02/SHOTBOOK_V2.example.json")
        self.assertEqual(contracts.validate_references(pos), [])
        neg = contracts.parse_json_strict(REPO_ROOT / "templates/kh02/SHOTBOOK_V2.negative.example.json")
        neg_errs = contracts.validate_references(neg)
        self.assertGreaterEqual(len(neg_errs), 4, neg_errs)
        codes = {e["code"] for e in neg_errs}
        self.assertIn("DANGLING_BEAT", codes)
        self.assertIn("DANGLING_ASSET", codes)
        self.assertIn("UNSAFE_LAYER_ID", codes)

    def test_t11_rights_analysis_only_and_license_gate(self):
        """T11: ANALYSIS_ONLY denied in visual/audio; commercial blocked license halts pipeline; unlicensed SFX fails."""
        pos = contracts.parse_json_strict(REPO_ROOT / "templates/kh02/SHOTBOOK_V2.example.json")
        fake_inv = {
            "assets": [
                {"asset_id": "src_a_roll", "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL", "usable_in_render": True},
                {"asset_id": "evid_masked_01", "kind": "analysis_only", "rights": "ANALYSIS_ONLY", "usable_in_render": False},
            ]
        }
        errs = contracts.validate_references(pos, inventory=fake_inv)
        self.assertTrue(any(e["code"] == "RIGHTS_DENIED_ASSET" for e in errs), errs)

        # Commercial blocked license halts CLI execute_pipeline before render
        with tempfile.TemporaryDirectory(prefix="kh02_t11_") as td:
            td_p = Path(td)
            brief = contracts.parse_json_strict(REPO_ROOT / "templates/kh02/BRIEF.example.json")
            brief["license_decision"] = "BLOCKED_COMMERCIAL_LICENSE"
            bp = td_p / "brief.json"
            bp.write_text(json.dumps(brief), encoding="utf-8")
            res = run.execute_pipeline(brief_path=bp, run_root=td_p / "run", mode="full")
            self.assertEqual(res["exit_code"], 2)
            self.assertEqual(res["status"], "BLOCKED_COMMERCIAL_LICENSE")

        # Unlicensed audio cue fails validate_cues
        bad_cue = [{"cue_id": "c1", "kind": "sfx_pop", "asset_ref": "a1", "frame_start": 0, "frame_end": 10, "gain_db": -12.0, "license_ref": "UNLICENSED"}]
        c_errs, _ = sound.validate_cues(bad_cue, total_frames=100)
        self.assertTrue(any(e["code"] == "AUDIO_UNLICENSED" for e in c_errs))

    def test_t12_source_inventory_multiroot_rotation_malformed_and_drift(self):
        """T12: duplicate basenames across roots have unique keys; symlinks & corrupt media fail; rotation & drift detected."""
        with tempfile.TemporaryDirectory(prefix="kh02_t12_") as td:
            td_p = Path(td)
            r1, r2 = td_p / "rootA", td_p / "rootB"
            r1.mkdir(); r2.mkdir()
            c1 = _make_synth_mp4(r1 / "clip.mp4", duration=1.5, size="320x180", rotate=0)
            c2 = _make_synth_mp4(r2 / "clip.mp4", duration=1.5, size="320x180", rotate=90)
            bad = r1 / "corrupt.mp4"
            bad.write_bytes(b"not a valid mp4")
            sym = r1 / "sym.mp4"
            try:
                sym.symlink_to(c1)
            except OSError:
                if os.name == "nt":
                    junc = r1 / "junc_escape"
                    subprocess.run(["cmd", "/c", "mklink", "/J", str(junc), str(r2)], check=True, capture_output=True)
                    sym = junc / "clip.mp4"
                else:
                    raise

            inv = source_analysis.build_inventory(
                [
                    {"asset_id": "a_main", "root_id": "rootA", "path": "clip.mp4", "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"},
                    {"asset_id": "b_rot", "root_id": "rootB", "path": "clip.mp4", "kind": "b_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"},
                ],
                run_id="t12",
                allowed_roots={"rootA": r1, "rootB": r2},
                analyze_audio_motion=False,
            )
            self.assertEqual(len(inv["assets"]), 2)
            self.assertNotEqual(inv["assets"][0]["source_key"], inv["assets"][1]["source_key"])
            rot_asset = next(a for a in inv["assets"] if a["asset_id"] == "b_rot")
            self.assertEqual(rot_asset["media"]["rotation_deg"], 90)
            self.assertEqual(rot_asset["media"]["width"], 180)
            self.assertEqual(rot_asset["media"]["height"], 320)

            # Symlink & corrupt media rejected
            with self.assertRaises(ValueError):
                source_analysis.probe_source(sym)
            with self.assertRaises(RuntimeError):
                source_analysis.probe_source(bad)

            # Integrity check passes before mutation and catches drift after mutation
            self.assertTrue(source_analysis.verify_source_integrity(inv)["all_unchanged"])
            c1.write_bytes(c1.read_bytes() + b"x")
            self.assertFalse(source_analysis.verify_source_integrity(inv)["all_unchanged"])

    def test_t13_transcript_variants_unicode_and_cache(self):
        """T13: faster-whisper segments & flat formats, EN/VI/CJK Unicode, overlap warnings, phrase pack & cache invalidation."""
        sha1 = "1" * 64
        sha2 = "2" * 64
        seg_payload = {
            "language": "vi",
            "duration_sec": 4.2,
            "segments": [{
                "id": 0, "speaker": "UNKNOWN",
                "words": [
                    {"word": "Xin", "start": 0.1, "end": 0.4, "probability": 0.99},
                    {"word": "chào", "start": 0.4, "end": 0.8, "probability": 0.98},
                    {"word": "ờ", "start": 0.85, "end": 1.0, "probability": None},
                    {"word": "世界", "start": 2.0, "end": 2.6, "probability": 0.95},
                ],
            }],
        }
        tr = transcript.normalize_faster_whisper(seg_payload, source_asset_id="src1", audio_sha256=sha1, language="vi")
        self.assertEqual(len(tr["words"]), 4)
        self.assertTrue(tr["words"][2]["is_filler"])
        self.assertIsNone(tr["words"][2]["confidence"])
        self.assertEqual(len(tr["phrases"]), 2)  # Split at 1.0s gap before 世界
        md = transcript.build_packed_transcript(tr, title="Multilingual Test")
        self.assertIn("Xin chào", md)
        self.assertIn("世界", md)

        # Flat list + invalid negative timing rejected
        flat = [{"word": "Hello", "start": 0.0, "end": 0.5}]
        self.assertEqual(len(transcript.normalize_faster_whisper(flat, source_asset_id="s", audio_sha256=sha1)["words"]), 1)
        with self.assertRaises(ValueError):
            transcript.normalize_faster_whisper([{"word": "Bad", "start": 1.0, "end": 0.5}], source_asset_id="s", audio_sha256=sha1)

        # Cache hit & invalidation on SHA change
        with tempfile.TemporaryDirectory(prefix="kh02_t13_") as td:
            _, saved = transcript.cache_transcript(td, sha1, tr["model"], "vi", transcript=tr)
            _, hit = transcript.cache_transcript(td, sha1, tr["model"], "vi")
            _, miss = transcript.cache_transcript(td, sha2, tr["model"], "vi")
            self.assertIsNotNone(hit)
            self.assertEqual(hit["cache_key"], saved["cache_key"])
            self.assertIsNone(miss)

    def test_t14_word_boundary_edl_lock_voice_and_midword_reject(self):
        """T14: LOCK_VOICE identity default; TRIM_APPROVED cuts silence/retakes safely; mid-word cut rejected."""
        words = [
            {"word_id": "w0001", "text": "Look", "start_sec": 0.2, "end_sec": 0.6, "speaker": "UNKNOWN", "confidence": 0.99},
            {"word_id": "w0002", "text": "here.", "start_sec": 0.6, "end_sec": 1.0, "speaker": "UNKNOWN", "confidence": 0.99},
            {"word_id": "w0003", "text": "Next", "start_sec": 2.4, "end_sec": 2.9, "speaker": "UNKNOWN", "confidence": 0.99},
            {"word_id": "w0004", "text": "detail.", "start_sec": 2.9, "end_sec": 3.4, "speaker": "UNKNOWN", "confidence": 0.99},
        ]
        tr = {"source_asset_id": "s1", "words": words, "phrases": transcript.group_phrases(words)}

        # LOCK_VOICE keeps silence
        cands_lock = edit_decisions.propose_cut_candidates(tr, voice_policy="LOCK_VOICE")
        self.assertTrue(all(c["decision"] == "KEEP" for c in cands_lock))
        tmap_lock = edit_decisions.build_time_map("s1", 96, 24, 1, voice_policy="LOCK_VOICE", approved_cuts=cands_lock)
        self.assertEqual(tmap_lock["total_output_frames"], 96)
        self.assertEqual(edit_decisions.validate_edl(tmap_lock, words=words, source_total_frames={"s1": 96}), [])

        # TRIM_APPROVED cuts >= 0.4s silence safely with padding & preserves intentional showcase if marked
        cands_trim = edit_decisions.propose_cut_candidates(tr, voice_policy="TRIM_APPROVED")
        self.assertTrue(any(c["decision"] == "CUT_APPROVED" for c in cands_trim))
        tmap_trim = edit_decisions.build_time_map("s1", 96, 24, 1, voice_policy="TRIM_APPROVED", approved_cuts=cands_trim)
        self.assertLess(tmap_trim["total_output_frames"], 96)
        self.assertEqual(edit_decisions.validate_edl(tmap_trim, words=words, source_total_frames={"s1": 96}), [])

        # Deliberate mid-word cut (cutting inside "Look" [0.2, 0.6] at frame 10 = 0.416s) MUST fail validate_edl
        bad_edl = {
            "voice_policy": "TRIM_APPROVED", "fps_num": 24, "fps_den": 1, "total_output_frames": 86,
            "segments": [{"segment_id": "s1", "source_asset_id": "s1", "src_start_frame": 10, "src_end_frame": 96, "out_start_frame": 0, "out_end_frame": 86, "audio_fade_in_ms": 20, "audio_fade_out_ms": 0, "reason": "bad"}],
        }
        errs = edit_decisions.validate_edl(bad_edl, words=words, source_total_frames={"s1": 96})
        self.assertTrue(any(e["code"] == "EDL_MID_WORD_CUT" for e in errs), errs)

    def test_t15_rational_ntsc_fps_and_long_timeline_zero_drift(self):
        """T15: Rational 30000/1001, 24, 25, 30 fps maintain exact frame arithmetic with zero drift over 10+ minutes."""
        for fps_num, fps_den in ((30000, 1001), (24, 1), (25, 1), (30, 1)):
            fps = Fraction(fps_num, fps_den)
            total_frames = int(600 * fps_num // fps_den)  # ~10 minutes
            cuts = [{"decision": "CUT_APPROVED", "start_sec": 100.0, "end_sec": 110.0, "reason": "long pause"}]
            tmap = edit_decisions.build_time_map("src", total_frames, fps_num, fps_den, voice_policy="TRIM_APPROVED", approved_cuts=cuts)
            self.assertEqual(edit_decisions.validate_edl(tmap, source_total_frames={"src": total_frames}), [])
            # Exact sum of segment lengths equals total_output_frames with 0 drift
            sum_len = sum(s["out_end_frame"] - s["out_start_frame"] for s in tmap["segments"])
            self.assertEqual(sum_len, tmap["total_output_frames"])
            # Event after cut shifts by exact integer frames removed
            cut_frames = tmap["segments"][1]["src_start_frame"] - tmap["segments"][0]["src_end_frame"]
            ev_frame = total_frames - 100
            remapped = edit_decisions.remap_events([{"start_frame": ev_frame, "end_frame": ev_frame + 24}], tmap)
            self.assertEqual(remapped[0]["start_frame"], ev_frame - cut_frames)

            # Word & SRT remapping after cut
            tr = {
                "source_asset_id": "src",
                "words": [
                    {"word_id": "w1", "text": "Before", "start_sec": 10.0, "end_sec": 10.5, "confidence": 0.9, "speaker": "UNKNOWN"},
                    {"word_id": "w2", "text": "After", "start_sec": 120.0, "end_sec": 120.6, "confidence": 0.9, "speaker": "UNKNOWN"},
                ],
            }
            mapped = transcript.map_words_to_output(tr, tmap)
            self.assertEqual(len(mapped), 2)
            expected_after_sec = 120.0 - float(Fraction(cut_frames) / fps)
            self.assertAlmostEqual(mapped[1]["out_start_sec"], expected_after_sec, places=2)


if __name__ == "__main__":
    unittest.main()
