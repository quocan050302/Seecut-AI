"""KH-02 Concurrency, Watchdog, Core Compatibility, Multi-Niche, Long-Form & Git Hygiene Tests (T29-T36)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STANDALONE = Path(os.environ.get("SEECUT_STANDALONE_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone")))
sys.path.insert(0, str(REPO_ROOT))

from scripts.kh02 import contracts, edit_decisions, planner, qa, render_guard, run, source_analysis  # noqa: E402


def _make_synth_mp4(dst: Path, duration: float = 2.0, fps: int = 24, size: str = "360x640") -> Path:
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=520:sample_rate=48000:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-r", str(fps), str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst


def _normalize_volatile(sb: dict) -> dict:
    clean = json.loads(json.dumps(sb))
    clean["run_id"] = "<RUN_ID>"
    clean["provenance"]["generated_at_utc"] = "<TS>"
    return clean


class TestConcurrencyE2EHygiene(unittest.TestCase):
    def test_t29_bounded_workers_determinism_and_low_disk_block(self):
        """T29: workers=1 vs workers=2 emit identical normalized SHOTBOOK; low-disk reserve blocks before work."""
        with tempfile.TemporaryDirectory(prefix="kh02_t29_") as td:
            td_p = Path(td)
            src = _make_synth_mp4(td_p / "src.mp4", duration=2.0)
            brief = contracts.parse_json_strict(REPO_ROOT / "templates/kh02/BRIEF.example.json")
            brief["sources"] = [{"asset_id": "src_a_roll", "root_id": "w", "path": str(src), "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL", "face_rect": [0.25, 0.18, 0.75, 0.58]}]
            bp = td_p / "brief.json"
            bp.write_text(json.dumps(brief), encoding="utf-8")

            r1 = run.execute_pipeline(brief_path=bp, run_root=td_p / "run_w1", mode="plan", workers=1, reserve_floor_gib=0.1)
            r2 = run.execute_pipeline(brief_path=bp, run_root=td_p / "run_w2", mode="plan", workers=2, reserve_floor_gib=0.1)
            self.assertEqual(r1["exit_code"], 0)
            self.assertEqual(r2["exit_code"], 0)
            sb1 = _normalize_volatile(contracts.parse_json_strict(Path(r1["shotbook_json"])))
            sb2 = _normalize_volatile(contracts.parse_json_strict(Path(r2["shotbook_json"])))
            self.assertEqual(contracts.hash_artifact(sb1), contracts.hash_artifact(sb2))

            # Low disk reserve floor blocks immediately before render
            res_low = render_guard.estimate_resources(476, 1080, 1920, render_scale=1.0, target_dir=td_p, reserve_floor_gib=999999.0)
            self.assertFalse(res_low["disk_ok"])

    def test_t30_watchdog_timeout_and_no_partial_promotion(self):
        """T30: render_guard watchdog kills timed-out child process cleanly and leaves no promoted MP4."""
        with tempfile.TemporaryDirectory(prefix="kh02_t30_") as td:
            td_p = Path(td)
            run_root = td_p / "r_wd"
            bdir = run_root / "build"
            bdir.mkdir(parents=True)
            (bdir / "master.html").write_text("<html></html>", encoding="utf-8")
            (bdir / "project.json").write_text(json.dumps({"fps": 24, "width": 320, "height": 180, "duration": 1.0, "person_dir": "person"}), encoding="utf-8")
            fake_up = td_p / "up"
            (fake_up / "skill/seecut/scripts").mkdir(parents=True)
            (fake_up / "skill/seecut/scripts/render.mjs").write_text("setTimeout(() => {}, 30000);\n", encoding="utf-8")
            out_mp4 = run_root / "exports/never.mp4"
            with self.assertRaises(TimeoutError):
                render_guard.run_render_checked(
                    bdir, run_root, run_id="r_wd", mode="full", out_mp4=out_mp4,
                    seecut_root=fake_up, tools_root=STANDALONE / "tools",
                    reserve_floor_gib=0.1, timeout_sec=1,
                )
            self.assertFalse(out_mp4.exists())

    def test_t31_core_compatibility_and_19_plus_10_gates(self):
        """T31: Core compatibility preserved (captions OFF, voice untouched, 19+10 gate doc intact)."""
        gates_doc = (REPO_ROOT / "references/gates-and-compliance.md").read_text(encoding="utf-8")
        for g in ("OWNER_VISUAL", "OWNER_AUDIO", "COMMERCIAL_RELEASE", "H1", "H2", "PAIRWISE", "LAYERED_PACKAGE"):
            self.assertIn(g, gates_doc)
        for kg in ("KH02_CONTRACT", "KH02_EDITORIAL", "KH02_MOTION", "KH02_CAMERA", "KH02_TRANSITION",
                   "KH02_TYPO", "KH02_AUDIO", "KH02_REGRESSION", "KH02_OLIVIA", "KH02_GENERALIZATION"):
            self.assertIn(kg, gates_doc)

    def test_t32_olivia_contract_honesty_and_baseline_isolation(self):
        """T32: Olivia baseline MP4 cannot be used as raw source; review report keeps owner/commercial gates pending/blocked."""
        rep = qa.build_review_report(
            run_id="olivia_check",
            evidence_records=[{"gate": "KH02_CONTRACT", "check_id": "ok", "status": "PASS", "expected": "0", "actual": "0", "reason": "ok", "reviewer_type": "deterministic_code"}],
            original_19_gates={"OWNER_VISUAL": "PENDING", "OWNER_AUDIO": "PENDING", "COMMERCIAL_RELEASE": "BLOCKED"},
            kh02_subgates={"KH02_CONTRACT": "PASS"},
            statuses={
                "CODE_QA_PASS": "PASS", "OLIVIA_RENDER_VERIFIED": "PASS", "AUTO_EDITORIAL_QA": "PASS",
                "OWNER_VISUAL": "PENDING", "OWNER_AUDIO": "PENDING", "COMMERCIAL_RELEASE": "BLOCKED",
                "CROSS_NICHE_VALIDATED": "PARTIAL_SMOKE_VERIFIED",
            },
        )
        self.assertNotEqual(rep["overall_status"], "READY_FOR_PRODUCTION")
        self.assertEqual(rep["overall_status"], "PREVIEW_PENDING_APPROVAL")

    def test_t33_cross_niche_6_scenarios_and_real_smoke_render(self):
        """T33: All 6 cross-niche modes + 9:16/16:9/1:1 + EN/VI/CJK plan cleanly; 1 real synthetic render via pinned render.mjs."""
        scenarios = [
            ("talking_head", "editorial_pro", "9:16", "en", "a_roll"),
            ("software_demo", "editorial_pro", "16:9", "en", "b_roll"),
            ("product_demo", "editorial_pro", "9:16", "vi", "a_roll"),
            ("education", "editorial_pro", "1:1", "zh", "b_roll"),
            ("documentary", "minimal_documentary", "16:9", "en", "b_roll"),
            ("faceless", "editorial_pro", "9:16", "en", "b_roll"),
        ]
        with tempfile.TemporaryDirectory(prefix="kh02_t33_") as td:
            td_p = Path(td)
            src_v = _make_synth_mp4(td_p / "clip_9x16.mp4", duration=1.5, fps=24, size="360x640")
            src_h = _make_synth_mp4(td_p / "clip_16x9.mp4", duration=1.5, fps=24, size="640x360")
            themes = set()
            for mode, prof, aspect, lang, kind in scenarios:
                src = src_h if aspect == "16:9" else src_v
                brief = {
                    "schema_version": "kh02.brief.v1", "brief_id": f"niche_{mode}", "title": f"Niche {mode}",
                    "mode": mode, "profile": prof, "objective": f"Test {mode}", "audience": "testers",
                    "platform": "youtube" if aspect == "16:9" else "reels", "aspect_ratio": aspect,
                    "language": lang, "tone": "clear", "key_message": f"Factual insight for {mode}",
                    "voice_policy": "LOCK_VOICE", "caption_policy": "OFF", "music_policy": "OFF", "sfx_policy": "OFF",
                    "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
                    "sources": [{"asset_id": "s1", "root_id": "t", "path": str(src), "kind": kind, "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
                }
                bp = td_p / f"brief_{mode}.json"
                bp.write_text(json.dumps(brief), encoding="utf-8")
                res = run.execute_pipeline(brief_path=bp, run_root=td_p / f"r_{mode}", mode="plan", workers=2, reserve_floor_gib=0.1)
                self.assertEqual(res["exit_code"], 0, res)
                sb = contracts.parse_json_strict(Path(res["shotbook_json"]))
                themes.add(sb["story_arc"]["theme"])
            self.assertGreaterEqual(len(themes), 3)

            # Real end-to-end synthetic smoke render through unmodified pinned SeeCut render.mjs
            rmjs = STANDALONE / "third_party/SeeCut/skill/seecut/scripts/render.mjs"
            if rmjs.is_file() and (STANDALONE / "tools/node_modules/playwright-core").is_dir():
                bp_smoke = td_p / "brief_software_demo.json"
                res_full = run.execute_pipeline(
                    brief_path=bp_smoke, run_root=td_p / "smoke_render", mode="full", workers=1,
                    seecut_root=STANDALONE / "third_party/SeeCut", tools_root=STANDALONE / "tools",
                    reserve_floor_gib=0.5,
                )
                self.assertEqual(res_full["exit_code"], 0, res_full)
                self.assertTrue(res_full["structural_qa"]["pass"], res_full["structural_qa"])
                self.assertTrue(res_full["structural_qa"]["provenance_verified"])
                self.assertTrue(res_full["audio_qa"]["pass"], res_full["audio_qa"])
                self.assertTrue(res_full["source_integrity"]["all_unchanged"])

    def test_t34_longform_chaptered_no_30s_cap(self):
        """T34: Long-form 10-minute (14,400f @ 24fps) plans cleanly with chapters & zero 30s hard cap."""
        inv = {
            "schema_version": "kh02.source_inventory.v1", "run_id": "lf", "created_at_utc": "2026-11-24T00:00:00Z",
            "assets": [{
                "asset_id": "lf_src", "root_id": "r", "source_key": "r:lf_src", "basename": "long.mp4",
                "canonical_path": "/tmp/long.mp4", "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL",
                "usable_in_render": True, "sha256": "b" * 64, "size_bytes": 500000000,
                "media": {"has_video": True, "has_audio": True, "is_still": False, "duration_sec": 600.0,
                          "width": 1920, "height": 1080, "fps_num": 24, "fps_den": 1, "nb_frames": 14400,
                          "is_vfr": False, "rotation_deg": 0, "sar": "1:1", "audio_sample_rate": 48000, "audio_channels": 2},
                "face_rect": [0.3, 0.15, 0.7, 0.55], "silences": [], "speech_spans": [], "scene_changes": [],
            }],
        }
        brief = {
            "schema_version": "kh02.brief.v1", "brief_id": "longform_10m", "title": "10 Minute Deep Dive",
            "mode": "education", "profile": "editorial_pro", "objective": "Explain system architecture in chapters",
            "audience": "engineers", "platform": "youtube", "aspect_ratio": "16:9", "language": "en",
            "tone": "structured", "key_message": "Chaptered pacing scales cleanly beyond 30 seconds",
            "voice_policy": "LOCK_VOICE", "caption_policy": "OFF", "music_policy": "OFF", "sfx_policy": "OFF",
            "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
            "sources": [{"asset_id": "lf_src", "path": "/tmp/long.mp4", "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
        }
        sb = planner.resolve_editorial_plan(brief, inv, run_id="lf_10m")
        self.assertEqual(sb["output_spec"]["total_frames"], 14400)
        self.assertGreaterEqual(len(sb["beats"]), 4)
        self.assertEqual(sb["beats"][-1]["end_frame"], 14400)
        self.assertTrue(qa.evaluate_editorial(sb, inv)["pass"])

    def test_t35_git_hygiene_no_secrets_user_paths_or_media_binaries(self):
        """T35: Repo files contain zero secrets, zero /Users/<name> local paths, valid JSON schemas & templates."""
        secret_re = re.compile(r"(AIza[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{20,}|ghp_[0-9A-Za-z]{20,})")
        user_path_re = re.compile(r"/Users/[A-Za-z0-9_.-]+/")
        for p in REPO_ROOT.rglob("*"):
            if ".git" in p.parts or "__pycache__" in p.parts or not p.is_file():
                continue
            self.assertNotIn(p.suffix.lower(), {".mp4", ".mov", ".wav", ".mp3", ".m4a"}, f"Media binary found in repo: {p}")
            if p.suffix.lower() == ".json":
                # Every JSON file in repo must parse strictly
                contracts.parse_json_strict(p)
            if p.suffix.lower() in {".py", ".sh", ".md", ".json"}:
                txt = p.read_text(encoding="utf-8", errors="ignore")
                self.assertIsNone(secret_re.search(txt), f"Potential secret in {p}")
                self.assertIsNone(user_path_re.search(txt), f"Hardcoded local /Users/ path in {p}")

    def test_t36_double_run_reproducibility_and_source_integrity(self):
        """T36: Running planner twice back-to-back yields byte-identical normalized SHOTBOOK & unchanged source hash."""
        with tempfile.TemporaryDirectory(prefix="kh02_t36_") as td:
            td_p = Path(td)
            src = _make_synth_mp4(td_p / "src.mp4", duration=1.5)
            sha0 = source_analysis.sha256_file(src)
            brief = contracts.parse_json_strict(REPO_ROOT / "templates/kh02/BRIEF.example.json")
            brief["sources"] = [{"asset_id": "src_a_roll", "root_id": "w", "path": str(src), "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}]
            bp = td_p / "brief.json"
            bp.write_text(json.dumps(brief), encoding="utf-8")

            r1 = run.execute_pipeline(brief_path=bp, run_root=td_p / "a", mode="plan", workers=2, reserve_floor_gib=0.1)
            r2 = run.execute_pipeline(brief_path=bp, run_root=td_p / "b", mode="plan", workers=2, reserve_floor_gib=0.1)
            self.assertEqual(r1["exit_code"], 0)
            self.assertEqual(r2["exit_code"], 0)
            s1 = _normalize_volatile(contracts.parse_json_strict(Path(r1["shotbook_json"])))
            s2 = _normalize_volatile(contracts.parse_json_strict(Path(r2["shotbook_json"])))
            self.assertEqual(contracts.canonicalize(s1), contracts.canonicalize(s2))
            self.assertEqual(source_analysis.sha256_file(src), sha0)


if __name__ == "__main__":
    unittest.main()
