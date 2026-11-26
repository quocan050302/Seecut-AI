"""KH-03.R4.2-03 & R4.2-04 — Real Native Candidate Preview Competition & Live Pairwise Adapter Tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.kh03 import candidates, router  # noqa: E402


def _make_noncommercial_clip(dst: Path, *, duration: float = 2.0, fps: int = 24, size: str = "360x640") -> Path:
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=320:sample_rate=48000:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst


class TestKH03R42CandidateNativeSuite(unittest.TestCase):
    def test_r42_06_live_pairwise_judge_fail_closed_when_offline_or_unavailable(self):
        """R4.2-04: Live pairwise judge never substitutes synthetic rubric when offline, missing agy, or missing MP4."""
        j_off = candidates.build_live_pairwise_judge(online_opt_in=False)
        r_off = j_off({"candidate_id": "cand_A"}, {"candidate_id": "cand_B"})
        self.assertIsNone(r_off["winner"])
        self.assertEqual(r_off["status"], "BLOCKED_OFFLINE_NO_ONLINE_OPT_IN")

        # Even with online_opt_in=True, missing preview MP4 or absent agy fails closed honestly
        old_agy = os.environ.get("AGY_BIN")
        try:
            os.environ.pop("AGY_BIN", None)
            j_on = candidates.build_live_pairwise_judge(online_opt_in=True)
            r_on = j_on({"candidate_id": "cand_A", "preview_mp4_path": None}, {"candidate_id": "cand_B", "preview_mp4_path": None})
            self.assertIsNone(r_on["winner"])
            self.assertIn(r_on["status"], ("BLOCKED_JUDGE_UNAVAILABLE", "BLOCKED_MISSING_PREVIEW_MP4"))
        finally:
            if old_agy is not None:
                os.environ["AGY_BIN"] = old_agy

    def test_r42_07_real_native_candidate_preview_competition_and_winner_binding(self):
        """R4.2-03: Real default native preview runner renders isolated MP4s via SeeCut render.mjs + binds winner cand_B."""
        pf = router.run_preflight(mode="preview")
        self.assertTrue(pf["readiness"]["NATIVE_PREVIEW_READY"], f"Native preview stack must be ready: {pf['blockers']}")

        with tempfile.TemporaryDirectory(prefix="kh03_r42_cand_") as td:
            td_p = Path(td)
            clip = _make_noncommercial_clip(td_p / "fixture_eng_2s.mp4", duration=2.0, fps=24, size="360x640")
            script_txt = "First check the baseline metrics. Second verify the visual shift. Third confirm output parity."

            # 1) Recommend + Explicit Selection (SK-02)
            run_dir = td_p / "run_compete"
            rec = router.run_recommend(
                video_path=clip,
                run_root=run_dir,
                script_text=script_txt,
                layout_policy="ADAPTIVE_DYNAMIC",
                intended_purpose="NONCOMMERCIAL_ENGINEERING",
                license_decision="NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
            )
            self.assertEqual(rec["exit_code"], 0)
            sel = router.run_select(
                run_root=run_dir,
                skill_id="SK-02",
                approval_source="EXPLICIT_OWNER_CLI",
                approval_evidence="Noncommercial R4.2-03 native candidate competition verification",
            )
            self.assertEqual(sel["exit_code"], 0)

            # 2) Run execute(mode='preview', compete_candidates=True) WITHOUT judge -> real native previews rendered, halts PENDING!
            res_no_judge = router.run_execute(
                run_root=run_dir,
                mode="preview",
                compete_candidates=True,
                max_candidates=2,
                sfx_policy="OFF",
                music_policy="OFF",
                enable_tracking=False,
                workers=1,
            )
            self.assertEqual(res_no_judge["exit_code"], 2)
            self.assertEqual(res_no_judge["status"], "CANDIDATE_BLOCKED_PENDING_REVIEW")
            receipts_p = run_dir / "manifests" / "CANDIDATE_PREVIEW_RECEIPTS.json"
            self.assertTrue(receipts_p.is_file(), "CANDIDATE_PREVIEW_RECEIPTS.json must be written")
            rec_doc = json.loads(receipts_p.read_text(encoding="utf-8"))
            self.assertEqual(len(rec_doc["receipts"]), 2)
            for r_item in rec_doc["receipts"]:
                self.assertTrue(r_item["preview_qa_pass"], f"Candidate preview failed native QA: {r_item}")
                self.assertTrue(r_item["h1_geom_pass"], f"Candidate H1 geom-check failed: {r_item}")
                self.assertTrue(Path(r_item["preview_mp4_path"]).is_file())
                self.assertEqual(len(r_item["preview_mp4_sha256"]), 64)
            # Confirm distinct preview MP4 SHAs (non-identical native renders!)
            self.assertNotEqual(
                rec_doc["receipts"][0]["preview_mp4_sha256"],
                rec_doc["receipts"][1]["preview_mp4_sha256"],
                "Distinct candidates A and B must yield distinct native preview MP4s",
            )

            # 3) Run fresh run with deterministic order-swapped pairwise consensus selecting cand_B!
            run_dir2 = td_p / "run_compete_win_b"
            router.run_recommend(
                video_path=clip, run_root=run_dir2, script_text=script_txt,
                layout_policy="ADAPTIVE_DYNAMIC",
                intended_purpose="NONCOMMERCIAL_ENGINEERING",
                license_decision="NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
            )
            router.run_select(
                run_root=run_dir2, skill_id="SK-02",
                approval_source="EXPLICIT_OWNER_CLI",
                approval_evidence="Noncommercial R4.2-03 winner binding test",
            )

            def _pair_pick_b(x: dict, y: dict) -> dict:
                if x["candidate_id"] == "cand_B":
                    return {"winner": "X", "reason": "B wins"}
                if y["candidate_id"] == "cand_B":
                    return {"winner": "Y", "reason": "B wins"}
                return {"winner": "TIE"}

            res_win_b = router.run_execute(
                run_root=run_dir2,
                mode="preview",
                compete_candidates=True,
                max_candidates=2,
                pairwise_fn=_pair_pick_b,
                sfx_policy="OFF",
                music_policy="OFF",
                enable_tracking=False,
                workers=1,
            )
            self.assertEqual(res_win_b["exit_code"], 0, res_win_b)
            self.assertEqual(res_win_b["winning_candidate_id"], "cand_B")
            self.assertTrue(Path(res_win_b["final_mp4_path"]).is_file())
            self.assertTrue((run_dir2 / "qc" / "PAIRWISE_AB_BA.json").is_file())

            # Verify WINNER_BINDING -> CREATIVE_PLAN -> SHOTBOOK_V2 -> run_verify(require_render=True)
            wb = json.loads((run_dir2 / "manifests" / "WINNER_BINDING.json").read_text(encoding="utf-8"))
            sb = json.loads((run_dir2 / "SHOTBOOK_V2.json").read_text(encoding="utf-8"))
            self.assertTrue(wb["bound"])
            self.assertEqual(wb["winning_candidate_id"], "cand_B")
            self.assertEqual(sb["provenance"]["kh03_winning_candidate_id"], "cand_B")
            self.assertEqual(sb["provenance"]["kh03_winner_binding_sha256"], wb["winner_binding_sha256"])

            vf = router.run_verify(run_root=run_dir2, production_mode=True, require_render=True)
            self.assertEqual(vf["exit_code"], 0, vf)
            self.assertEqual(vf["status"], "VERIFY_PASS")


if __name__ == "__main__":
    unittest.main()
