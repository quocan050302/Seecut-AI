"""KH-03.R4.2.HOTFIX Tests — Per-Renderer License Separation, Clean-Room Independent Renderer & Manual Editorial Review."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.kh03 import independent_renderer, renderer_routing, router, speech_edl  # noqa: E402


def _make_clip(dst: Path, *, size: str = "360x640", fps: int = 24, v_dur: float = 2.0, a_dur: float | None = None, freq: int = 330) -> Path:
    ad = a_dur if a_dur is not None else v_dur
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={v_dur}",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={ad}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
            str(dst),
        ],
        check=True, capture_output=True,
    )
    return dst


class TestKH03R42HotfixRendererSuite(unittest.TestCase):
    def test_hotfix_01_per_renderer_license_separation_and_clean_room_audit(self):
        """HOTFIX-01: Clean-room renderer passes AST provenance audit (zero SeeCut/PolyForm); native SeeCut stays blocked for commercial."""
        with tempfile.TemporaryDirectory(prefix="kh03_hf_prov_") as td:
            td_p = Path(td)
            aud = renderer_routing.audit_renderer_provenance(
                renderer_routing.RENDERER_INDEPENDENT_CLEAN_ROOM,
                out_path=td_p / "AUDIT.json",
            )
            self.assertTrue(aud["clean_room_verified"], aud)
            self.assertFalse(aud["uses_seecut_upstream_code"])
            self.assertFalse(aud["uses_seecut_assets"])
            self.assertEqual(aud["isolation_violations"], [])
            self.assertTrue(aud["commercial_eligible"])

            # Explicit native_seecut for COMMERCIAL_MARKETING without receipt STILL FAILS CLOSED!
            dec_nat = renderer_routing.select_renderer_backend(
                requested="native_seecut",
                intended_purpose="COMMERCIAL_MARKETING",
                commercial_permission_evidence=None,
            )
            self.assertFalse(dec_nat["allowed"])
            self.assertEqual(dec_nat["status"], "BLOCKED_COMMERCIAL_LICENSE")
            self.assertFalse(dec_nat["fake_permission_receipt_created"])

            # Auto mode for COMMERCIAL_MARKETING routes cleanly to INDEPENDENT_FFMPEG_PIL while recording NATIVE_SEECUT blocked!
            dec_auto = renderer_routing.select_renderer_backend(
                requested="auto",
                intended_purpose="COMMERCIAL_MARKETING",
                commercial_permission_evidence=None,
                out_dir=td_p / "routing",
            )
            self.assertTrue(dec_auto["allowed"], dec_auto)
            self.assertEqual(dec_auto["selected_renderer"], renderer_routing.RENDERER_INDEPENDENT_CLEAN_ROOM)
            self.assertEqual(dec_auto["per_renderer_rights"][renderer_routing.RENDERER_NATIVE_SEECUT]["status"], "BLOCKED_COMMERCIAL_LICENSE")
            self.assertFalse(dec_auto["fake_permission_receipt_created"])
            self.assertTrue((td_p / "routing" / "RENDERER_ROUTING_DECISION.json").is_file())
            self.assertTrue((td_p / "routing" / "RENDERER_PROVENANCE_AUDIT.json").is_file())

    def test_hotfix_02_manual_editorial_review_states_when_agy_absent(self):
        """HOTFIX-02: Distinguishes CANDIDATE_RENDERED, EDITORIAL_PENDING, and FINAL_APPROVED cleanly when AI judge absent."""
        lc_pend = renderer_routing.resolve_editorial_lifecycle(
            rendered_candidates=[
                {"candidate_id": "cand_A", "preview_mp4_sha256": "a" * 64, "preview_qa_pass": True},
                {"candidate_id": "cand_B", "preview_mp4_sha256": "b" * 64, "preview_qa_pass": True},
            ],
            pairwise_status="PENDING_REVIEW_MISSING_JUDGE",
            live_judge_available=False,
        )
        self.assertEqual(lc_pend["candidate_state"], "CANDIDATE_RENDERED")
        self.assertEqual(lc_pend["editorial_state"], "EDITORIAL_PENDING")
        self.assertEqual(lc_pend["final_state"], "PENDING_OWNER_APPROVAL")
        self.assertFalse(lc_pend["blocked_preview_due_to_missing_ai_judge"])
        self.assertEqual(lc_pend["valid_rendered_candidate_count"], 2)

        # Owner selection without visual+audio playback sign-off STILL does NOT mark FINAL_APPROVED!
        lc_sel = renderer_routing.resolve_editorial_lifecycle(
            rendered_candidates=[{"candidate_id": "cand_B", "preview_mp4_sha256": "b" * 64, "preview_qa_pass": True}],
            pairwise_status="PENDING_REVIEW_MISSING_JUDGE",
            owner_override_candidate_id="cand_B",
            owner_visual_approved=True,
            owner_audio_approved=False,
        )
        self.assertEqual(lc_sel["editorial_state"], "EDITORIAL_OWNER_SELECTED")
        self.assertEqual(lc_sel["final_state"], "PENDING_OWNER_APPROVAL")

        # ONLY explicit visual AND audio approval yields FINAL_APPROVED
        lc_fin = renderer_routing.resolve_editorial_lifecycle(
            rendered_candidates=[{"candidate_id": "cand_B", "preview_mp4_sha256": "b" * 64, "preview_qa_pass": True}],
            pairwise_status="PENDING_REVIEW_MISSING_JUDGE",
            owner_override_candidate_id="cand_B",
            owner_visual_approved=True,
            owner_audio_approved=True,
        )
        self.assertEqual(lc_fin["final_state"], "FINAL_APPROVED")

    def test_hotfix_03_phoneme_waveform_cut_refinement(self):
        """HOTFIX-03: Waveform refinement snaps cut boundaries into deepest local silence valleys avoiding clicks."""
        import numpy as np
        import wave

        with tempfile.TemporaryDirectory(prefix="kh03_hf_wf_") as td:
            wav_p = Path(td) / "speech_valley.wav"
            sr = 48000
            t = np.arange(int(3.0 * sr), dtype=np.float64) / sr
            sig = np.zeros_like(t)
            # Burst 1: 0.1..0.85s, silent valley at 0.88..1.05s, duplicate burst: 1.05..1.85s, valley 1.88..2.05s, burst 2: 2.05..2.8s
            sig[(t >= 0.10) & (t <= 0.85)] = 0.4 * np.sin(2 * np.pi * 240 * t[(t >= 0.10) & (t <= 0.85)])
            sig[(t >= 1.05) & (t <= 1.85)] = 0.4 * np.sin(2 * np.pi * 240 * t[(t >= 1.05) & (t <= 1.85)])
            sig[(t >= 2.05) & (t <= 2.80)] = 0.4 * np.sin(2 * np.pi * 240 * t[(t >= 2.05) & (t <= 2.80)])
            with wave.open(str(wav_p), "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
                wf.writeframes((sig * 32767).astype(np.int16).tobytes())

            ref = speech_edl.refine_cut_boundaries_waveform(
                wav_p,
                [{"start_sec": 0.90, "end_sec": 1.92, "reason": "dup"}],
                search_ms=140,
            )
            self.assertEqual(len(ref), 1)
            self.assertLess(ref[0]["start_valley_db"], -40.0)
            self.assertLess(ref[0]["end_valley_db"], -40.0)
            self.assertGreaterEqual(ref[0]["start_sec"], 0.85)
            self.assertLessEqual(ref[0]["end_sec"], 2.05)

    def test_hotfix_04_independent_renderer_multisource_24_30_mute_and_qa(self):
        """HOTFIX-04: Clean-room renderer combines 24fps A + 30fps B (with 0.11s overhang, muted B), overlays, BGM & SFX."""
        with tempfile.TemporaryDirectory(prefix="kh03_hf_rend_") as td:
            td_p = Path(td)
            src_a = _make_clip(td_p / "a_24fps.mp4", size="360x640", fps=24, v_dur=2.0, freq=280)
            src_b = _make_clip(td_p / "b_30fps.mp4", size="540x960", fps=30, v_dur=2.0, a_dur=2.12, freq=620)
            spec = independent_renderer.build_independent_render_spec_from_plan(
                primary_source_path=src_a,
                secondary_source_path=src_b,
                creative_plan={"selected_skill_id": "SK-05", "hook_plan": {"hook_headline": "CLEAN ROOM TEST"}},
                width=540,
                height=960,
                fps_num=24,
                fps_den=1,
                bgm_enabled=True,
            )
            spec["overlays"].append({
                "kind": "numerical_payoff_hero",
                "start_sec": 0.9,
                "end_sec": 1.9,
                "from_value": 60,
                "to_value": 120,
                "kicker": "ILLUSTRATIVE",
                "subtitle": "2x headroom",
            })
            out_mp4 = td_p / "out.mp4"
            res = independent_renderer.render_independent_video(spec, out_mp4=out_mp4, stems_dir=td_p / "stems")
            self.assertTrue(out_mp4.is_file())
            self.assertEqual(res["engine_kind"], "INDEPENDENT_FFMPEG_PIL")
            self.assertFalse(res["uses_seecut_upstream"])
            self.assertEqual(res["width"], 540)
            self.assertEqual(res["height"], 960)
            self.assertEqual(res["nb_frames"], 48)
            self.assertTrue(res["decode_qa_pass"])
            self.assertTrue(res["audio"]["secondary_audio_muted"])
            self.assertGreater(res["source_a_visible_frames"], 0)
            self.assertGreater(res["source_b_visible_frames"], 0)
            self.assertTrue((td_p / "stems" / "voice_master.wav").is_file())
            self.assertTrue((td_p / "stems" / "bgm.wav").is_file())
            self.assertTrue((td_p / "stems" / "sfx.wav").is_file())

    def test_hotfix_05_independent_renderer_cancellation_and_no_partial_mp4(self):
        """HOTFIX-05: Mid-render cancellation cleans up staging MP4 and leaves zero partial output file."""
        with tempfile.TemporaryDirectory(prefix="kh03_hf_can_") as td:
            td_p = Path(td)
            src_a = _make_clip(td_p / "a.mp4", size="360x640", fps=24, v_dur=1.5)
            spec = independent_renderer.build_independent_render_spec_from_plan(
                primary_source_path=src_a, width=360, height=640, fps_num=24, fps_den=1, bgm_enabled=False,
            )
            ev = threading.Event()
            ev.set()  # Trigger immediate cooperative cancel
            out_mp4 = td_p / "cancelled.mp4"
            with self.assertRaises(RuntimeError):
                independent_renderer.render_independent_video(spec, out_mp4=out_mp4, stems_dir=td_p / "stems", cancel_event=ev)
            self.assertFalse(out_mp4.exists())
            self.assertFalse(out_mp4.with_suffix(".staging.mp4").exists())

    def test_hotfix_06_router_commercial_clean_room_vs_native_seecut_separation(self):
        """HOTFIX-06: Commercial job blocked on native_seecut but succeeds cleanly on independent_clean_room with EDITORIAL_PENDING."""
        with tempfile.TemporaryDirectory(prefix="kh03_hf_rtr_") as td:
            td_p = Path(td)
            src_a = _make_clip(td_p / "a.mp4", size="360x640", fps=24, v_dur=1.5)
            src_b = _make_clip(td_p / "b.mp4", size="360x640", fps=30, v_dur=1.5)
            run_dir = td_p / "run_comm"
            router.run_recommend(
                video_path=src_a,
                run_root=run_dir,
                script_text="Simple clear explanation of two numbers moving from 60 to 120.",
                secondary_sources=[{"source_id": "sec_b", "path": str(src_b), "audio_policy": "MUTE_DEFAULT"}],
                layout_policy="ADAPTIVE_DYNAMIC",
                intended_purpose="COMMERCIAL_MARKETING",
                license_decision="BLOCKED_COMMERCIAL_LICENSE",
            )
            router.run_select(
                run_root=run_dir,
                skill_id="SK-05",
                approval_source="EXPLICIT_HOST_MESSAGE",
                approval_evidence="Explicit owner selection of SK-05 for commercial independent renderer test",
            )
            # 1) Explicit native_seecut MUST STILL FAIL CLOSED with BLOCKED_COMMERCIAL_LICENSE!
            r_nat = router.run_execute(
                run_root=run_dir, mode="preview", renderer="native_seecut",
                intended_purpose="COMMERCIAL_MARKETING", sfx_policy="OFF", music_policy="OFF", enable_tracking=False,
            )
            self.assertEqual(r_nat["exit_code"], 2)
            self.assertEqual(r_nat["status"], "BLOCKED_COMMERCIAL_LICENSE")

            # 2) Clean-room independent renderer SUCCEEDS cleanly and keeps FINAL_APPROVED=False & EDITORIAL_PENDING!
            r_ind = router.run_execute(
                run_root=run_dir, mode="preview", renderer="independent_clean_room",
                intended_purpose="COMMERCIAL_MARKETING", sfx_policy="OFF", music_policy="OPTIONAL_LICENSED", enable_tracking=False,
            )
            self.assertEqual(r_ind["exit_code"], 0, r_ind)
            self.assertEqual(r_ind["engine_kind"], renderer_routing.RENDERER_INDEPENDENT_CLEAN_ROOM)
            self.assertEqual(r_ind["lifecycle"]["candidate_state"], "CANDIDATE_RENDERED")
            self.assertEqual(r_ind["lifecycle"]["editorial_state"], "EDITORIAL_PENDING")
            self.assertEqual(r_ind["lifecycle"]["final_state"], "PENDING_OWNER_APPROVAL")
            self.assertFalse(r_ind["final_approved"])
            self.assertTrue(Path(r_ind["final_mp4_path"]).is_file())


if __name__ == "__main__":
    unittest.main()

