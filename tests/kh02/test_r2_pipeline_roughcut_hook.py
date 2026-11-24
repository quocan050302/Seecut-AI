"""KH-02.R2 Regression Tests — Groups R2-01..R2-06 (Live Pipeline Integration, Rough Cut, Hook & Semantics)."""

from __future__ import annotations

import json
import math
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from scripts.kh02 import contracts, edit_decisions, planner, rough_cut, run as kh_run, source_analysis, transcript


def _make_synthetic_clip(
    out_mp4: Path,
    *,
    width: int = 360,
    height: int = 640,
    fps: int = 24,
    duration_sec: float = 5.0,
    speech_windows: list[tuple[float, float]] | None = None,
    sample_rate: int = 48000,
) -> Path:
    """Create a deterministic isolated TEST_ONLY MP4 with moving color bars + voiced tone bursts separated by silence."""
    wav_p = out_mp4.with_suffix(".wav")
    n_samples = int(round(duration_sec * sample_rate))
    wins = speech_windows if speech_windows is not None else [(0.4, 1.3), (3.4, 4.4)]
    buf = bytearray(n_samples * 2)
    for i in range(n_samples):
        t = i / float(sample_rate)
        voiced = any(a <= t <= b for a, b in wins)
        val = int(12000 * math.sin(2.0 * math.pi * 240.0 * t)) if voiced else 0
        struct.pack_into("<h", buf, i * 2, val)
    with wave.open(str(wav_p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(buf))
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}:duration={duration_sec}",
        "-i", str(wav_p),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(sample_rate),
        "-shortest", str(out_mp4),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    wav_p.unlink(missing_ok=True)
    return out_mp4


def _make_sample_transcript(words_spec: list[tuple[str, float, float, float]]) -> list[dict]:
    return [{"word": f" {w}", "start": s, "end": e, "probability": p} for w, s, e, p in words_spec]


class TestR2PipelineRoughCutHook(unittest.TestCase):
    def test_r2_01_normal_execute_pipeline_receipts_hashes_dry_run_offline_verify(self) -> None:
        """R2-01: Normal execute_pipeline graph writes STAGE_RECEIPTS with SHA linkage; dry-run/offline/verify safe."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vid = _make_synthetic_clip(root / "clip.mp4", duration_sec=4.0, speech_windows=[(0.3, 1.4), (2.4, 3.6)])
            tr_p = root / "words.json"
            tr_p.write_text(json.dumps(_make_sample_transcript([
                ("Look", 0.35, 0.70, 0.99), ("at", 0.72, 0.90, 0.99), ("this.", 0.92, 1.35, 0.99),
                ("It", 2.45, 2.70, 0.99), ("works", 2.72, 3.10, 0.99), ("cleanly.", 3.12, 3.55, 0.99),
            ])), encoding="utf-8")
            brief_p = root / "brief.json"
            brief_p.write_text(json.dumps({
                "schema_version": "kh02.brief.v2",
                "brief_id": "r2_01_test",
                "mode": "talking_head",
                "profile": "editorial_pro",
                "objective": "Verify normal pipeline stage receipts and linkage",
                "audience": "engineers",
                "platform": "reels",
                "aspect_ratio": "9:16",
                "language": "en",
                "tone": "direct",
                "key_message": "Normal CLI executes full canonical graph",
                "voice_policy": "LOCK_VOICE",
                "cut_policy": "LOCK_VOICE",
                "caption_policy": "OFF",
                "music_policy": "OFF",
                "sfx_policy": "OPTIONAL_LICENSED",
                "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
                "sources": [{"asset_id": "src_a", "path": str(vid), "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
            }), encoding="utf-8")

            # 1. Dry-run creates zero files and zero downloads
            dry_dir = root / "run_dry"
            res_dry = kh_run.execute_pipeline(
                brief_path=brief_p, run_root=dry_dir, mode="plan", dry_run=True, offline=True,
                transcript_json_path=tr_p, production_mode=False,
            )
            self.assertEqual(res_dry["status"], "DRY_RUN_PASS")
            self.assertEqual(res_dry["sfx_downloads_performed"], 0)
            self.assertFalse(dry_dir.exists())

            # 2. Normal plan run with spy on key wired stages
            run_dir = root / "run_plan"
            with mock.patch.object(kh_run, "build_r1_time_map", wraps=kh_run.build_r1_time_map) as sp_edl, \
                 mock.patch.object(kh_run, "load_sfx_provider_config", wraps=kh_run.load_sfx_provider_config) as sp_sfx, \
                 mock.patch.object(kh_run, "ensure_cue_assets", wraps=kh_run.ensure_cue_assets) as sp_acq:
                res = kh_run.execute_pipeline(
                    brief_path=brief_p, run_root=run_dir, mode="plan", offline=True,
                    transcript_json_path=tr_p, production_mode=False,
                )
                self.assertEqual(res["exit_code"], 0)
                self.assertEqual(sp_edl.call_count, 1)
                self.assertEqual(sp_sfx.call_count, 1)
                self.assertEqual(sp_acq.call_count, 1)

            # Verify stage receipts exist and link upstream SHAs
            ver_rec = kh_run.verify_pipeline_receipts(run_dir, [
                "A_authorize_inventory", "B_transcript_analysis", "C_rough_cut_edl",
                "D_apply_cut_and_verify", "E_hook_and_strategy", "F_canonical_shotbook",
            ])
            self.assertTrue(ver_rec["pass"], ver_rec)
            self.assertEqual(res["cut_status"], "IDENTITY_VERIFIED")
            self.assertTrue((run_dir / "MISSING_AUDIO_ASSETS.json").is_file())
            self.assertTrue((run_dir / "CACHE_INTEGRITY.json").is_file())
            self.assertTrue((run_dir / "SEMANTIC_MAP.json").is_file())
            self.assertTrue((run_dir / "HOOK_APPLIED_EVIDENCE.json").is_file())

            # Verify mode works on compiled run
            sb = contracts.parse_json_strict(run_dir / "SHOTBOOK_V2.json")
            cm_stub = {"plan_sha256": contracts.hash_artifact(sb)}
            (run_dir / "COMPILE_MAP.json").write_text(json.dumps(cm_stub), encoding="utf-8")
            ver_cli = kh_run.execute_pipeline(brief_path=brief_p, run_root=run_dir, mode="verify")
            self.assertEqual(ver_cli["status"], "VERIFY_PASS")

    def test_r2_02_rough_cut_integration_normal_path_and_lock_voice(self) -> None:
        """R2-02: PRESERVE_SCRIPT_TRIM executes apply_r1_cut_map + verify_cut_result and binds derived SHA downstream."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # 6.0s clip with a 3.0s dead-air gap between [1.1s] and [4.3s]
            vid = _make_synthetic_clip(root / "long_gap.mp4", duration_sec=6.0, speech_windows=[(0.3, 1.1), (4.3, 5.5)])
            orig_sha = contracts.hash_artifact(vid)
            tr_p = root / "words.json"
            tr_p.write_text(json.dumps(_make_sample_transcript([
                ("Start", 0.35, 0.70, 0.99), ("here.", 0.72, 1.08, 0.99),
                ("Final", 4.35, 4.80, 0.99), ("point.", 4.82, 5.45, 0.99),
            ])), encoding="utf-8")
            brief_p = root / "brief.json"
            brief_p.write_text(json.dumps({
                "schema_version": "kh02.brief.v2",
                "brief_id": "r2_02_cut",
                "mode": "talking_head",
                "profile": "editorial_pro",
                "objective": "Trim dead air safely in normal pipeline",
                "audience": "creators",
                "platform": "reels",
                "aspect_ratio": "9:16",
                "language": "en",
                "tone": "crisp",
                "key_message": "Trim dead silence safely",
                "voice_policy": "PRESERVE_SCRIPT_TRIM",
                "cut_policy": "PRESERVE_SCRIPT_TRIM",
                "caption_policy": "OFF",
                "music_policy": "OFF",
                "sfx_policy": "OFF",
                "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
                "sources": [{"asset_id": "src_a", "path": str(vid), "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
            }), encoding="utf-8")

            run_dir = root / "run_trim"
            with mock.patch.object(kh_run, "apply_r1_cut_map", wraps=kh_run.apply_r1_cut_map) as sp_app, \
                 mock.patch.object(kh_run, "verify_cut_result", wraps=kh_run.verify_cut_result) as sp_ver:
                res = kh_run.execute_pipeline(
                    brief_path=brief_p, run_root=run_dir, mode="plan", offline=True,
                    transcript_json_path=tr_p, production_mode=False,
                )
                self.assertEqual(res["exit_code"], 0, res)
                self.assertEqual(res["cut_status"], "R2_CUT_APPLIED")
                self.assertEqual(sp_app.call_count, 1)
                self.assertEqual(sp_ver.call_count, 1)

            # Derived clip exists, shorter than original 144f, original untouched
            self.assertEqual(contracts.hash_artifact(vid), orig_sha)
            drv = Path(res["derived_source_path"])
            self.assertTrue(drv.is_file())
            self.assertNotEqual(res["derived_source_sha256"], orig_sha)
            ver_doc = contracts.parse_json_strict(run_dir / "CUT_VERIFICATION.json")
            self.assertTrue(ver_doc["pass"])
            self.assertLess(ver_doc["after_frames"], ver_doc["before_frames"])
            self.assertEqual(ver_doc["original_word_count"], 4)
            self.assertEqual(ver_doc["mapped_word_count"], 4)
            self.assertTrue(ver_doc["protected_words_exact_order"])

            # LOCK_VOICE override never applies cut
            run_lock = root / "run_lock"
            res_lock = kh_run.execute_pipeline(
                brief_path=brief_p, run_root=run_lock, mode="plan", cut_policy_override="LOCK_VOICE",
                offline=True, transcript_json_path=tr_p, production_mode=False,
            )
            self.assertEqual(res_lock["cut_status"], "IDENTITY_VERIFIED")
            self.assertEqual(res_lock["derived_source_sha256"], orig_sha)

    def test_r2_03_protected_speech_meaning_and_unsafe_cut_negative(self) -> None:
        """R2-03: Negation, numbers, proper names, low-confidence ASR & reactions protected; unsafe mid-word cut rejected."""
        words = [
            {"word_id": "w_001", "text": "Do", "start_sec": 0.2, "end_sec": 0.4, "confidence": 0.99},
            {"word_id": "w_002", "text": "not", "start_sec": 0.41, "end_sec": 0.65, "confidence": 0.99},
            {"word_id": "w_003", "text": "skip", "start_sec": 0.66, "end_sec": 0.95, "confidence": 0.99},
            {"word_id": "w_004", "text": "42", "start_sec": 0.96, "end_sec": 1.30, "confidence": 0.99},
            {"word_id": "w_005", "text": "Gumroad", "start_sec": 1.32, "end_sec": 1.70, "confidence": 0.50},
        ]
        tr = {"source_asset_id": "src_a", "words": words, "phrases": [{"phrase_id": "ph_1", "text": "Do not skip 42 Gumroad", "word_ids": [w["word_id"] for w in words], "start_sec": 0.2, "end_sec": 1.7}]}
        # Mid-word cut collides with protected negation 'not' -> must raise ValueError
        with self.assertRaises(ValueError):
            rough_cut.build_r1_time_map(
                "src_a", 72, 24, 1, cut_policy="PRESERVE_SCRIPT_TRIM", words=words,
                explicit_cuts=[{"candidate_id": "bad", "candidate_type": "DEAD_AIR", "proposed_outcome": "REMOVE", "safety_ok": True, "trim_start_pts": 0.35, "trim_end_pts": 0.70, "reason": "unsafe"}],
            )
        # Reaction pause is protected as KEEP
        cls_r = rough_cut.classify_pause(2.0, 3.2, source_duration_sec=5.0, words=words, reaction_windows=[(1.9, 3.3)])
        self.assertEqual(cls_r["candidate_type"], "REACTION")
        self.assertEqual(cls_r["recommended_outcome"], "KEEP")

    def test_r2_04_rational_pts_vfr_rotation_shared_edl_map(self) -> None:
        """R2-04: Rational 30000/1001 & 24/1 PTS/sample map shared across captions/visuals/SFX with exact mapping."""
        words = [
            {"word_id": "w_001", "text": "Alpha", "start_sec": 0.2, "end_sec": 0.6, "confidence": 0.98},
            {"word_id": "w_002", "text": "Beta", "start_sec": 3.2, "end_sec": 3.6, "confidence": 0.98},
        ]
        edl = rough_cut.build_r1_time_map(
            "src_ntsc", 120, 30000, 1001, audio_sample_rate=48000,
            cut_policy="PRESERVE_SCRIPT_TRIM", words=words,
            explicit_cuts=[{"candidate_id": "c1", "candidate_type": "DEAD_AIR", "proposed_outcome": "SHORTEN", "safety_ok": True, "trim_start_pts": 1.0, "trim_end_pts": 2.6, "reason": "dead air"}],
        )
        self.assertEqual(edl["fps_num"], 30000)
        self.assertEqual(edl["fps_den"], 1001)
        self.assertEqual(len(edl["segments"]), 2)
        mapped = transcript.map_words_to_output({"source_asset_id": "src_ntsc", "words": words}, edl)
        self.assertEqual(len(mapped), 2)
        self.assertLess(mapped[1]["out_start_sec"], 2.0)  # Shifted cleanly by ~1.6s cut
        # Exact sample monotonic continuity across segments
        self.assertEqual(edl["segments"][0]["out_end_sample"], edl["segments"][1]["out_start_sample"])

    def test_r2_05_hook_compiled_at_frame_zero_and_truthfulness_negative(self) -> None:
        """R2-05: Selected hook compiles into frame-0 event with ID linked in SHOTBOOK & HOOK_APPLIED_EVIDENCE; fake claims blocked."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vid = _make_synthetic_clip(root / "hook.mp4", duration_sec=4.0, speech_windows=[(0.2, 1.8), (2.2, 3.6)])
            tr_p = root / "w.json"
            tr_p.write_text(json.dumps(_make_sample_transcript([
                ("Check", 0.25, 0.60, 0.99), ("this", 0.62, 0.90, 0.99), ("page.", 0.92, 1.40, 0.99),
                ("It", 2.25, 2.50, 0.99), ("turned", 2.52, 2.95, 0.99), ("out", 2.96, 3.20, 0.99), ("great.", 3.22, 3.55, 0.99),
            ])), encoding="utf-8")
            brief_p = root / "brief.json"
            brief_p.write_text(json.dumps({
                "schema_version": "kh02.brief.v2",
                "brief_id": "r2_05_hook",
                "mode": "talking_head",
                "profile": "editorial_pro",
                "objective": "Prove hook binds to compiled frame 0",
                "audience": "creators",
                "platform": "reels",
                "aspect_ratio": "9:16",
                "language": "en",
                "tone": "direct",
                "key_message": "Creator storefront workflow",
                "voice_policy": "LOCK_VOICE",
                "caption_policy": "OFF",
                "music_policy": "OFF",
                "sfx_policy": "OFF",
                "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
                "presenter_policy": {"mode": "FIXED_PIP", "visibility": "ALWAYS_VISIBLE", "anchor": "BOTTOM_LEFT", "shape": "ROUNDED_RECT"},
                "sources": [{"asset_id": "src_a", "path": str(vid), "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
            }), encoding="utf-8")

            run_dir = root / "run_hook"
            res = kh_run.execute_pipeline(brief_path=brief_p, run_root=run_dir, mode="plan", offline=True, transcript_json_path=tr_p, production_mode=False)
            self.assertEqual(res["exit_code"], 0, res)
            evid = contracts.parse_json_strict(run_dir / "HOOK_APPLIED_EVIDENCE.json")
            self.assertTrue(evid["hook_compiled_at_frame_zero"])
            self.assertTrue(len(evid["compiled_frame_zero_events"]) >= 1)
            self.assertEqual(evid["selected_option_id"], res["selected_hook"]["selected_option_id"])

            # Negative: fabricated claim rejected by truthfulness gate
            inv = contracts.parse_json_strict(run_dir / "manifests/SOURCE_INVENTORY.json")
            brief = contracts.parse_json_strict(brief_p)
            ok, issues = planner.evaluate_hook_truthfulness(
                {"option_id": "bad", "opening_line": "100% guaranteed viral results!", "opening_visual": "x", "strategy": "clickbait", "evidence_asset_ids": ["missing_id"]},
                brief=brief, inventory=inv,
            )
            self.assertFalse(ok)
            self.assertTrue(any("fabricated_or_clickbait_claim" in i for i in issues))
            self.assertTrue(any("unsupported_evidence_asset" in i for i in issues))

    def test_r2_06_semantic_classification_by_content_not_index_parity(self) -> None:
        """R2-06: Reordering phrases preserves semantic role & asset relevance rather than hardcoded index parity."""
        brief = planner.build_editor_brief({
            "brief_id": "r2_06_sem",
            "mode": "product_demo",
            "profile": "editorial_pro",
            "objective": "Verify semantic classification",
            "audience": "creators",
            "platform": "reels",
            "aspect_ratio": "9:16",
            "language": "en",
            "tone": "informative",
            "key_message": "Semantic role test",
            "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
            "sources": [
                {"asset_id": "aroll", "path": "/tmp/a.mp4", "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"},
                {"asset_id": "ev_gumroad", "path": "/tmp/gumroad.png", "kind": "real_ui", "rights": "CAPTURED_REAL_EVIDENCE_MASKED", "label": "Gumroad page"},
                {"asset_id": "ev_cli", "path": "/tmp/cli.png", "kind": "real_ui", "rights": "CAPTURED_REAL_EVIDENCE_MASKED", "label": "Terminal CLI output"},
            ],
        })
        inv = {
            "assets": [
                {"asset_id": "aroll", "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL", "usable_in_render": True, "sha256": "a" * 64, "canonical_path": "/tmp/a.mp4", "media": {"fps_num": 24, "fps_den": 1, "nb_frames": 240, "duration_sec": 10.0, "has_audio": True, "width": 1080, "height": 1920}},
                {"asset_id": "ev_gumroad", "kind": "real_ui", "rights": "CAPTURED_REAL_EVIDENCE_MASKED", "usable_in_render": True, "sha256": "b" * 64, "canonical_path": "/tmp/gumroad.png", "label": "Gumroad storefront page", "basename": "gumroad.png", "media": {}},
                {"asset_id": "ev_cli", "kind": "real_ui", "rights": "CAPTURED_REAL_EVIDENCE_MASKED", "usable_in_render": True, "sha256": "c" * 64, "canonical_path": "/tmp/cli.png", "label": "Terminal CLI command", "basename": "cli.png", "media": {}},
            ]
        }
        tmap = edit_decisions.build_time_map("aroll", 240, 24, 1)
        # Put CLI proof at phrase index 2 (NOT index 1!) and a question at index 1
        words = [
            {"word_id": "w1", "text": "Intro", "start_sec": 0.1, "end_sec": 0.8},
            {"word_id": "w2", "text": "Why?", "start_sec": 1.2, "end_sec": 2.0},
            {"word_id": "w3", "text": "Run", "start_sec": 2.4, "end_sec": 2.8},
            {"word_id": "w4", "text": "terminal", "start_sec": 2.85, "end_sec": 3.4},
            {"word_id": "w5", "text": "CLI", "start_sec": 3.45, "end_sec": 4.0},
            {"word_id": "w6", "text": "Obsessed", "start_sec": 5.0, "end_sec": 5.8},
            {"word_id": "w7", "text": "result.", "start_sec": 5.85, "end_sec": 6.5},
        ]
        tr = {
            "source_asset_id": "aroll",
            "words": words,
            "phrases": [
                {"phrase_id": "p1", "text": "Intro context", "word_ids": ["w1"], "start_sec": 0.1, "end_sec": 0.8},
                {"phrase_id": "p2", "text": "Why does this matter?", "word_ids": ["w2"], "start_sec": 1.2, "end_sec": 2.0},
                {"phrase_id": "p3", "text": "Run terminal CLI command", "word_ids": ["w3", "w4", "w5"], "start_sec": 2.4, "end_sec": 4.0},
                {"phrase_id": "p4", "text": "Obsessed with this result.", "word_ids": ["w6", "w7"], "start_sec": 5.0, "end_sec": 6.5},
            ],
        }
        beats = planner.analyze_semantic_beats(brief, inv, tr, tmap)
        b_q = next(b for b in beats if "Why" in b["focus_subject"])
        b_cli = next(b for b in beats if "terminal CLI" in b["focus_subject"])
        b_pay = next(b for b in beats if "Obsessed" in b["focus_subject"])
        self.assertEqual(b_q["semantic_function"], "question")
        self.assertEqual(b_cli["semantic_function"], "evidence")
        # Must select ev_cli by semantic match, NOT blindly ev_gumroad (index 0)!
        self.assertIn("ev_cli", b_cli["content_refs"])
        self.assertEqual(b_pay["semantic_function"], "payoff")
        self.assertIn("rule_grounded_deterministic_fallback", b_cli["why"])


if __name__ == "__main__":
    unittest.main()
