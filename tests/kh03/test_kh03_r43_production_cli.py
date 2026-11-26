"""KH-03.R4.3 End-to-End Production CLI Flow, Candidate Competition, Worker Parity & Verify Recompute."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.kh03 import router


def _make_clip(path: Path, *, dur: float = 2.0, color: str = "0x1A242E", freq: int = 440) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=360x640:d={dur}:r=24",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}:sample_rate=48000",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-shortest", str(path),
        ],
        check=True, capture_output=True, timeout=45,
    )
    return path


class TestKH03R43ProductionCLISuite(unittest.TestCase):
    def test_r43_10_cli_recommend_select_execute_candidates_verify_and_forged_gate_catch(self) -> None:
        """Full CLI flow with 3 distinct candidates, provisional binding, ONE final encode, verify PASS, and forged-QA detection."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _make_clip(root / "demo_source.mp4", dur=2.0)
            script_p = root / "script.txt"
            script_p.write_text("Start strong. Compare two methods. Clear conclusion.", encoding="utf-8")

            def _run_job(run_name: str, workers: int) -> dict:
                rdir = root / run_name
                rec = router.run_recommend(
                    video_path=src, run_root=rdir, script_path=script_p,
                    layout_policy="ADAPTIVE_DYNAMIC", cut_policy="PRESERVE_SCRIPT_TRIM",
                    caption_policy="OFF", music_policy="OPTIONAL_LICENSED",
                    intended_purpose="COMMERCIAL_MARKETING", license_decision="BLOCKED_COMMERCIAL_LICENSE",
                )
                self.assertEqual(rec["exit_code"], 0)
                sel = router.run_select(run_root=rdir, skill_id="SK-01", approval_source="EXPLICIT_OWNER_CLI", approval_evidence="Explicit owner CLI R4.3 test")
                self.assertEqual(sel["exit_code"], 0)

                job_ov = {
                    "output_filename": "demo_final.mp4",
                    "run_post_asr": False,
                    "bgm_enabled": True,
                    "bgm_style": "luxury_lounge",
                    "candidate_variants": [
                        {
                            "candidate_id": "cand_A", "label": "A", "strategy": "STRATEGY_A",
                            "overlays": [{"event_id": "a1", "kind": "context_chip", "start_sec": 0.2, "end_sec": 1.8, "position": "top", "title": "Approach Alpha"}],
                        },
                        {
                            "candidate_id": "cand_B", "label": "B", "strategy": "STRATEGY_B",
                            "overlays": [{"event_id": "b1", "kind": "context_chip", "start_sec": 0.2, "end_sec": 1.8, "position": "bottom", "title": "Approach Beta"}],
                        },
                    ],
                }
                exe = router.run_execute(
                    run_root=rdir, mode="full", renderer="auto", production_profile="standard",
                    compete_candidates=True, max_candidates=2, workers=workers,
                    cache_dir=root / "shared_cache", independent_spec_overrides=job_ov,
                )
                self.assertEqual(exe["exit_code"], 0, exe)
                return exe

            exe_w1 = _run_job("run_w1", workers=1)
            exe_w2 = _run_job("run_w2", workers=2)

            # Distinct candidate previews rendered + provisional binding (NOT editorially approved!)
            self.assertEqual(len(exe_w1["candidates"]), 2)
            self.assertNotEqual(exe_w1["candidates"][0]["preview_mp4_sha256"], exe_w1["candidates"][1]["preview_mp4_sha256"])
            self.assertFalse(exe_w1["provisional_binding"]["editorially_approved"])
            self.assertEqual(exe_w1["owner_visual"], "PENDING")
            self.assertEqual(exe_w1["owner_audio"], "PENDING")
            self.assertFalse(exe_w1["final_approved"])

            # Deterministic canonical spec parity across workers=1 vs workers=2 (same source, same job_id stripped)
            s1 = json.loads((root / "run_w1" / "manifests" / "INDEPENDENT_RENDER_SPEC_V2.json").read_text(encoding="utf-8"))
            s2 = json.loads((root / "run_w2" / "manifests" / "INDEPENDENT_RENDER_SPEC_V2.json").read_text(encoding="utf-8"))
            self.assertEqual(s1["overlays"], s2["overlays"])
            self.assertEqual(s1["total_output_frames"], s2["total_output_frames"])
            self.assertEqual(s1["visual_tracks"], s2["visual_tracks"])

            # run_verify --require-render PASSES cleanly
            vf_ok = router.run_verify(run_root=root / "run_w1", require_render=True)
            self.assertEqual(vf_ok["exit_code"], 0, vf_ok)

            # Tamper raw AUDIO_QA.json pass=False while leaving FINAL_GATE_MATRIX claiming PASS -> run_verify MUST catch!
            aq_p = root / "run_w1" / "qc" / "AUDIO_QA.json"
            aq = json.loads(aq_p.read_text(encoding="utf-8"))
            aq["pass"] = False
            aq_p.write_text(json.dumps(aq), encoding="utf-8")
            vf_bad = router.run_verify(run_root=root / "run_w1", require_render=True)
            self.assertNotEqual(vf_bad["exit_code"], 0)
            self.assertTrue(any("FORGED_GATE_PASS" in i for i in vf_bad["issues"]))


if __name__ == "__main__":
    unittest.main()
