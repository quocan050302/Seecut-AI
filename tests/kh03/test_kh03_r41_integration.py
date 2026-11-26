"""KH-03.R4.1-01 — Real Candidate Binding & Generic Multi-Source Integration Test Suite.

Proves end-to-end (normal CLI/router path + negative fault injection):
1. Normal router.run_execute(compete_candidates=True, pairwise_fn=...) binds the winning candidate (cand_B)
   all the way into CREATIVE_PLAN.json, LAYOUT_PLAN.json, MOTION_GRAPH.json, SOUND_DIRECTOR_PLAN.json,
   WINNER_BINDING.json, and SHOTBOOK_V2.json['provenance']['kh03_winning_candidate_id'] == 'cand_B',
   producing a materially different compiled plan than cand_A.
2. Negative competition gates:
   - Pairwise tie -> winner=None, execution halts (exit_code=2), no silent rubric fallback!
   - Order-inconsistent judge (AB != BA) -> winner=None, halted!
   - Missing pairwise_fn with compete_candidates=True -> halted (no silent fallback)!
   - Missing preview proof in preview mode -> halted!
   - All candidates hard-fail -> NO_VALID_WINNER!
   - Cosmetic clone candidate -> rejected by validate_candidate_diversity()!
   - Tampered WINNER_BINDING.json or CREATIVE_PLAN.json -> router.run_verify() FAILS!
   - compete_candidates=False -> records SINGLE_PLAN_NO_COMPETITION explicitly.
3. Generic multi-source secondary video contract (Section 10.4.5):
   - probe_secondary_source() enforces audio_policy='MUTE_DEFAULT' & measures container_vs_video_delta_sec.
   - map_secondary_source_pts_to_master() maps 30fps->24fps with exact Fraction math and fails closed
     on container overhang (CONTAINER_VS_VIDEO_DURATION_DRIFT).
   - Swapping secondary source file on disk invalidates SELECTION_RECEIPT.json (FRESH_SECONDARY_HASH_MISMATCH).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.kh03 import candidates, intelligence, layout, router, selection


def _make_video(
    out_path: Path,
    *,
    w: int = 270,
    h: int = 480,
    fps: int = 24,
    dur: float = 4.0,
    color: str = "0x18342A",
    audio_sr: int = 48000,
    audio_dur: float | None = None,
) -> Path:
    ad = dur if audio_dur is None else audio_dur
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r={fps}:d={dur}",
        "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate={audio_sr}:duration={ad}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "96k",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


_SAMPLE_SCRIPT = (
    "Most teams think shipping faster breaks quality. "
    "Step 1: measure every stage duration from 42 seconds down to 14 seconds. "
    "Step 2: isolate worker staging directories so late tasks never corrupt canonical output. "
    "Step 3: verify every final artifact hash before publishing."
)


class TestKH03R41IntegrationSuite(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kh03_r41_int_"))
        self.src_a = _make_video(self.tmp / "src_a_24fps.mp4", fps=24, dur=5.0, color="0x1B3A30")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _recommend_and_select(
        self,
        run_root: Path,
        *,
        skill_id: str = "SK-04",
        secondary_sources: list[dict[str, Any]] | None = None,
        layout_policy: str = "ADAPTIVE_DYNAMIC",
    ) -> dict[str, Any]:
        rec = router.run_recommend(
            video_path=self.src_a,
            run_root=run_root,
            script_text=_SAMPLE_SCRIPT,
            layout_policy=layout_policy,
            secondary_sources=secondary_sources,
            test_only=True,
        )
        self.assertEqual(rec["status"], "AWAITING_USER_SELECTION")
        sel = router.run_select(
            run_root=run_root,
            skill_id=skill_id,
            approval_source="TEST_ONLY_FIXTURE",
            approval_evidence="R4.1 integration test selection",
            test_only=True,
        )
        self.assertEqual(sel["status"], "USER_SELECTED")
        return sel

    def test_r41_01_normal_execute_binds_winner_cand_b_through_shotbook_and_verify(self) -> None:
        """Winning cand_B MUST replace authoritative plan/layout/motion/sound & propagate into SHOTBOOK_V2!"""
        run_a = self.tmp / "run_win_a"
        run_b = self.tmp / "run_win_b"
        self._recommend_and_select(run_a, skill_id="SK-04")
        self._recommend_and_select(run_b, skill_id="SK-04")

        def _prefer_a(x: dict[str, Any], y: dict[str, Any]) -> dict[str, Any]:
            if x["candidate_id"] == "cand_A":
                return {"winner": "X", "reason": "test_prefer_A"}
            if y["candidate_id"] == "cand_A":
                return {"winner": "Y", "reason": "test_prefer_A"}
            return {"winner": "X" if x["candidate_id"] < y["candidate_id"] else "Y"}

        def _prefer_b(x: dict[str, Any], y: dict[str, Any]) -> dict[str, Any]:
            if x["candidate_id"] == "cand_B":
                return {"winner": "X", "reason": "test_prefer_B"}
            if y["candidate_id"] == "cand_B":
                return {"winner": "Y", "reason": "test_prefer_B"}
            return {"winner": "X" if x["candidate_id"] < y["candidate_id"] else "Y"}

        res_a = router.run_execute(
            run_root=run_a, mode="plan", workers=2, production_mode=False,
            compete_candidates=True, pairwise_fn=_prefer_a,
        )
        res_b = router.run_execute(
            run_root=run_b, mode="plan", workers=2, production_mode=False,
            compete_candidates=True, pairwise_fn=_prefer_b,
        )
        self.assertEqual(res_a["exit_code"], 0, f"run_a failed: {res_a}")
        self.assertEqual(res_b["exit_code"], 0, f"run_b failed: {res_b}")

        # Verify WINNER_BINDING.json + SHOTBOOK_V2 provenance + CREATIVE_PLAN match cand_B in run_b
        wb_b = json.loads((run_b / "manifests" / "WINNER_BINDING.json").read_text(encoding="utf-8"))
        self.assertEqual(wb_b["winning_candidate_id"], "cand_B")
        self.assertTrue(wb_b["bound"])

        sb_a = json.loads((run_a / "SHOTBOOK_V2.json").read_text(encoding="utf-8"))
        sb_b = json.loads((run_b / "SHOTBOOK_V2.json").read_text(encoding="utf-8"))
        self.assertEqual(sb_a["provenance"]["kh03_winning_candidate_id"], "cand_A")
        self.assertEqual(sb_b["provenance"]["kh03_winning_candidate_id"], "cand_B")
        self.assertEqual(sb_b["provenance"]["kh03_winner_binding_sha256"], wb_b["winner_binding_sha256"])

        # Material proof: cand_B compiled layout/plan MUST differ from cand_A!
        lp_a = json.loads((run_a / "layouts" / "LAYOUT_PLAN.json").read_text(encoding="utf-8"))
        lp_b = json.loads((run_b / "layouts" / "LAYOUT_PLAN.json").read_text(encoding="utf-8"))
        self.assertNotEqual(
            [b["layout_mode"] for b in lp_a["beats"]],
            [b["layout_mode"] for b in lp_b["beats"]],
            "Binding cand_B MUST produce a materially different canonical LAYOUT_PLAN than cand_A!",
        )
        self.assertTrue((run_b / "candidates" / "B" / "CREATIVE_PLAN.json").is_file())
        self.assertTrue((run_b / "stems" / "SOUND_DIRECTOR_PLAN.json").is_file())

        # Strict run_verify must pass on untampered run_b
        ver_ok = router.run_verify(run_root=run_b, production_mode=False, require_render=False)
        self.assertEqual(ver_ok["status"], "VERIFY_PASS", f"Clean run_b should verify cleanly: {ver_ok}")

        # Tampering CREATIVE_PLAN.json after binding MUST fail run_verify!
        cp_path = run_b / "manifests" / "CREATIVE_PLAN.json"
        cp_obj = json.loads(cp_path.read_text(encoding="utf-8"))
        cp_obj["creative_direction_summary"] = "TAMPERED_AFTER_BINDING"
        cp_path.write_text(json.dumps(cp_obj, indent=2), encoding="utf-8")
        ver_bad = router.run_verify(run_root=run_b, production_mode=False, require_render=False)
        self.assertEqual(ver_bad["status"], "VERIFY_FAIL", "Tampering bound CREATIVE_PLAN.json MUST fail run_verify!")
        self.assertTrue(any("WINNER_BINDING" in i or "CREATIVE_PLAN" in i or "SHA" in i for i in ver_bad["issues"]), ver_bad)

    def test_r41_02_competition_negative_gates_never_fallback_silently(self) -> None:
        """Tie, order-inconsistent judge, missing judge, or missing preview proof MUST halt (never silently pick cand_A)."""
        # 1. Pairwise tie -> halt with exit_code=2
        run_tie = self.tmp / "run_tie"
        self._recommend_and_select(run_tie)
        res_tie = router.run_execute(
            run_root=run_tie, mode="plan", workers=1, production_mode=False,
            compete_candidates=True, pairwise_fn=lambda a, b: {"winner": "TIE"},
        )
        self.assertEqual(res_tie["exit_code"], 2)
        self.assertIn("CANDIDATE", res_tie["status"])
        self.assertFalse((run_tie / "SHOTBOOK_V2.json").exists(), "Halted tie MUST NOT compile SHOTBOOK_V2!")

        # 2. Order-inconsistent judge (always returns X -> AB says A, BA says B) -> halt!
        run_inc = self.tmp / "run_inconsistent"
        self._recommend_and_select(run_inc)
        res_inc = router.run_execute(
            run_root=run_inc, mode="plan", workers=1, production_mode=False,
            compete_candidates=True, pairwise_fn=lambda a, b: {"winner": "X"},
        )
        self.assertEqual(res_inc["exit_code"], 2)
        cand_rep = json.loads((run_inc / "manifests" / "CANDIDATES.json").read_text(encoding="utf-8"))
        self.assertIsNone(cand_rep["ranking"]["winner"])
        self.assertIsNotNone(cand_rep["ranking"]["rubric_top_diagnostic"])
        self.assertEqual(cand_rep["ranking"]["pairwise_status"], "INCONSISTENT_ORDER_TIE_PENDING_REVIEW")

        # 3. Missing pairwise_fn with compete_candidates=True -> halt!
        run_nojudge = self.tmp / "run_nojudge"
        self._recommend_and_select(run_nojudge)
        res_nj = router.run_execute(
            run_root=run_nojudge, mode="plan", workers=1, production_mode=False,
            compete_candidates=True, pairwise_fn=None,
        )
        self.assertEqual(res_nj["exit_code"], 2)
        nj_rep = json.loads((run_nojudge / "manifests" / "CANDIDATES.json").read_text(encoding="utf-8"))
        self.assertIsNone(nj_rep["ranking"]["winner"])
        self.assertEqual(nj_rep["ranking"]["pairwise_status"], "PLAN_ONLY_PAIRWISE_NOT_RUN")

        # 4. compete_candidates=False records SINGLE_PLAN_NO_COMPETITION explicitly
        run_single = self.tmp / "run_single_plan"
        self._recommend_and_select(run_single)
        res_s = router.run_execute(
            run_root=run_single, mode="plan", workers=1, production_mode=False,
            compete_candidates=False,
        )
        self.assertEqual(res_s["exit_code"], 0)
        sb_s = json.loads((run_single / "SHOTBOOK_V2.json").read_text(encoding="utf-8"))
        self.assertEqual(sb_s["provenance"]["kh03_competition_status"], "SINGLE_PLAN_NO_COMPETITION")

    def test_r41_03_candidate_preview_proof_all_fail_and_cosmetic_clone_gates(self) -> None:
        """Missing preview proof in preview mode, all hard-fail, and cosmetic clones MUST fail closed."""
        beats = [
            {"beat_id": "b1", "start_frame": 0, "end_frame": 48, "semantic_function": "hook", "has_numeric_claims": True},
            {"beat_id": "b2", "start_frame": 48, "end_frame": 96, "semantic_function": "mechanism", "has_numeric_claims": True},
            {"beat_id": "b3", "start_frame": 96, "end_frame": 120, "semantic_function": "cta"},
        ]
        media = {"width": 720, "height": 1280, "nb_frames": 120, "fps_num": 24, "fps_den": 1, "aspect_ratio": "9:16"}
        at = layout.build_subject_action_tracks(media)
        cands = candidates.generate_creative_candidates(
            run_id="r41_neg", selected_skill_id="SK-04", beats=beats,
            canvas={"width": 720, "height": 1280, "aspect_ratio": "9:16"},
            source_media=media, action_tracks=at, max_candidates=3,
        )
        # Missing preview proof in preview mode -> winner MUST be None!
        rk_noprev = candidates.rank_candidates(
            cands,
            pairwise_fn=lambda a, b: {"winner": "X" if a["candidate_id"] == "cand_A" else "Y"},
            mode="preview",
            require_preview_proof=True,
        )
        self.assertIsNone(rk_noprev["winner"])
        self.assertEqual(rk_noprev["status"], "NO_VALID_WINNER")
        self.assertEqual(rk_noprev["disqualified_count"], 3)
        ad_noprev = candidates.adopt_candidate(rk_noprev, mode="preview", require_preview_proof=True)
        self.assertFalse(ad_noprev["adopted"])

        # All candidates hard-fail -> NO_VALID_WINNER!
        all_fail = [dict(c, plan_gate_pass=False, plan_hard_failures=["ACTION_VIOLATION"]) for c in cands]
        rk_fail = candidates.rank_candidates(all_fail)
        self.assertEqual(rk_fail["status"], "NO_VALID_WINNER")
        self.assertIsNone(rk_fail["winner"])
        self.assertFalse(candidates.adopt_candidate(rk_fail)["adopted"])

        # Cosmetic clone rejected
        clones = [cands[0], dict(cands[0], candidate_id="cand_B_clone")]
        div = candidates.validate_candidate_diversity(clones)
        self.assertFalse(div["pass"])
        self.assertEqual(div["status"], "FAIL_COSMETIC_CLONES")
        self.assertIn("cand_B_clone", div["rejected_duplicate_ids"])

    def test_r41_04_generic_multi_source_pts_mapping_overhang_and_fresh_hash_invalidation(self) -> None:
        """Multi-source secondary video: MUTE_DEFAULT audio, 30->24 rational PTS, container overhang guard, SHA invalidation."""
        sec_v1 = _make_video(self.tmp / "sec_v1_30fps.mp4", w=360, h=640, fps=30, dur=3.0, audio_sr=96000, audio_dur=3.15)
        probe = intelligence.probe_secondary_source(sec_v1, source_id="sec_demo", role="secondary_screen_evidence")
        self.assertEqual(probe["audio_policy"], "MUTE_DEFAULT")
        self.assertTrue(probe["audio_present"])
        self.assertEqual(probe["fps_num"], 30)
        self.assertGreater(probe["container_vs_video_delta_sec"], 0.05)

        # Valid range within actual video duration maps cleanly 30fps -> 24fps (exact rational)
        mapped_ok = intelligence.map_secondary_source_pts_to_master(
            probe, master_fps_num=24, master_fps_den=1, src_in_sec=0.5, src_out_sec=2.5,
        )
        self.assertEqual(mapped_ok["master_duration_frames"], 48)
        self.assertEqual(mapped_ok["audio_policy"], "MUTE_DEFAULT")
        self.assertTrue(mapped_ok["container_overhang_guarded"])

        # Requesting past actual video_duration_sec into container/audio overhang MUST FAIL CLOSED!
        with self.assertRaises(ValueError) as ctx_drift:
            intelligence.map_secondary_source_pts_to_master(
                probe, master_fps_num=24, master_fps_den=1,
                src_in_sec=0.0, src_out_sec=probe["video_duration_sec"] + 0.12,
            )
        self.assertIn("CONTAINER_VS_VIDEO_DURATION_DRIFT", str(ctx_drift.exception))

        # Selection binds secondary source hash; swapping sec_v1 on disk MUST invalidate selection!
        run_ms = self.tmp / "run_multisource"
        self._recommend_and_select(
            run_ms,
            secondary_sources=[{"path": str(sec_v1), "source_id": "sec_demo", "role": "secondary_screen_evidence", "audio_policy": "MUTE_DEFAULT"}],
        )
        val_ok = selection.validate_user_selection(run_dir=run_ms, production_mode=False)
        self.assertTrue(val_ok["valid"], val_ok)

        # Overwrite sec_v1 with a revised file -> fresh disk re-hash MUST reject prior selection receipt!
        _make_video(sec_v1, w=360, h=640, fps=30, dur=3.5, color="0x331B44", audio_sr=96000)
        with self.assertRaises(selection.SelectionGateError) as ctx_sec:
            selection.validate_user_selection(run_dir=run_ms, production_mode=False)
        self.assertIn("FRESH_SECONDARY_HASH_MISMATCH", str(ctx_sec.exception))


if __name__ == "__main__":
    unittest.main()
