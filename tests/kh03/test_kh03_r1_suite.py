"""KH-03.R1 Targeted Integration, Security, Remapping, Fullscreen Frame QA & Regression Suite (K03R1-01..K03R1-12).

Exercises EVERY R1.01-R1.08 + P0 defect directly through the real CLI/pipeline entry points
(scripts.kh03.router & scripts.kh02.run.execute_pipeline), strictly isolated with TEST_ONLY fixtures.
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from scripts.kh02 import sound as kh02_sound  # noqa: F401
from scripts.kh02 import edit_decisions, rough_cut, run as kh02_run
from scripts.kh03 import (
    contracts,
    director,
    router,
    selection,
    visual_qa,
)


def _make_animated_fixture_mp4(
    out_mp4: Path,
    *,
    width: int = 360,
    height: int = 640,
    fps_str: str = "24",
    duration_sec: float = 6.0,
    with_pauses: bool = True,
) -> Path:
    """Create a deterministic moving-bar fixture with optional real silent pause gaps (for PRESERVE_SCRIPT_TRIM tests)."""
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    sr = 48000
    n = int(round(duration_sec * sr))
    wav_p = out_mp4.with_suffix(".wav")
    samples = []
    for i in range(n):
        t = i / float(sr)
        in_pause = with_pauses and ((1.7 <= t <= 2.7) or (4.1 <= t <= 5.0))
        if in_pause:
            val = 0
        else:
            val = int(0.18 * math.sin(2.0 * math.pi * 280.0 * t) * 32767)
        samples.append(val)
    with wave.open(str(wav_p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{n}h", *samples))

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps_str}:duration={duration_sec}",
        "-i", str(wav_p),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
        "-c:a", "aac", "-ar", str(sr), "-ac", "1", "-b:a", "128k",
        "-shortest", str(out_mp4),
    ]
    subprocess.run(cmd, check=True, timeout=60)
    wav_p.unlink(missing_ok=True)
    return out_mp4


def _make_cc0_sfx_lib(sfx_dir: Path) -> Path:
    sfx_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    sr = 48000
    for fn, cat, hz in [
        ("impact_01.wav", "impact", 140.0),
        ("whoosh_01.wav", "whoosh", 320.0),
        ("click_01.wav", "click", 1200.0),
        ("rustle_01.wav", "rustle", 520.0),
        ("chime_01.wav", "chime", 960.0),
    ]:
        n = int(0.32 * sr)
        vals = [int(0.28 * min(1.0, i / 160.0) * math.exp(-(i / sr) * 6.0) * math.sin(2 * math.pi * hz * i / sr) * 32767) for i in range(n)]
        with wave.open(str(sfx_dir / fn), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(struct.pack(f"<{n}h", *vals))
        manifest[fn] = {
            "category": cat,
            "tags": [cat, "clean", "editorial"],
            "license_name": "CC0-1.0",
            "license_evidence": "https://creativecommons.org/publicdomain/zero/1.0/ (TEST_ONLY unit fixture)",
            "author": "KH03 R1 Test",
            "provider_terms_note": "LOCAL_LIBRARY_CC0",
        }
    (sfx_dir / "SFX_LICENSE_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return sfx_dir


class TestKH03R1HardeningSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = Path(tempfile.mkdtemp(prefix="kh03_r1_"))
        cls.vid_6s = _make_animated_fixture_mp4(cls._tmp / "src_6s.mp4", duration_sec=6.0, with_pauses=True)
        cls.vid_ntsc = _make_animated_fixture_mp4(cls._tmp / "src_ntsc.mp4", fps_str="30000/1001", duration_sec=4.0, with_pauses=False)
        cls.sfx_dir = _make_cc0_sfx_lib(cls._tmp / "sfx_lib")
        cls.script_text = (
            "Just because you have $1,200 in your account doesn't mean you have $1,200 to spend. "
            "Let's say $800 is for bills, $200 for groceries, and $100 for gas. "
            "You actually have just $100 left. Link in my profile."
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _run_dir(self, name: str) -> Path:
        p = self._tmp / name
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
        return p

    def test_k03r1_01_p0_license_hardening_blocks_commercial_downgrade_and_fake_permission(self) -> None:
        """K03R1-01 (P0): Reject overriding BLOCKED_COMMERCIAL_LICENSE with NONCOMMERCIAL or unsubstantiated WRITTEN permission."""
        rdir = self._run_dir("r1_01_p0_license")
        router.run_recommend(
            video_path=self.vid_6s,
            run_root=rdir,
            script_text=self.script_text,
            intended_purpose="COMMERCIAL_MARKETING",
            license_decision="BLOCKED_COMMERCIAL_LICENSE",
            test_only=True,
        )
        router.run_select(run_root=rdir, skill_id="SK-04", approval_source="TEST_ONLY_FIXTURE", test_only=True)

        # Attempt 1: downgrade blocked commercial recommendation to NONCOMMERCIAL -> MUST FAIL CLOSED
        ex1 = router.run_execute(
            run_root=rdir, mode="plan",
            license_decision="NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
            production_mode=False,
        )
        self.assertEqual(ex1["exit_code"], 2)
        self.assertEqual(ex1["status"], "BLOCKED_COMMERCIAL_LICENSE")

        # Attempt 2: unsubstantiated WRITTEN_COMMERCIAL_PERMISSION_VERIFIED (no evidence) -> MUST FAIL CLOSED
        ex2 = router.run_execute(
            run_root=rdir, mode="plan",
            license_decision="WRITTEN_COMMERCIAL_PERMISSION_VERIFIED",
            commercial_permission_evidence="",
            production_mode=False,
        )
        self.assertEqual(ex2["exit_code"], 2)
        self.assertEqual(ex2["status"], "BLOCKED_COMMERCIAL_LICENSE")

        # Attempt 3: invalid/unknown license_decision -> MUST FAIL CLOSED
        ex3 = router.run_execute(
            run_root=rdir, mode="plan",
            license_decision="UNKNOWN_MAGIC_PASS",
            production_mode=False,
        )
        self.assertEqual(ex3["exit_code"], 2)
        self.assertEqual(ex3["status"], "BLOCKED_LICENSE_UNRESOLVED")

    def test_k03r1_02_authoritative_edl_remap_and_double_remap_rejection(self) -> None:
        """K03R1-02 (R1.01/B01): PRESERVE_SCRIPT_TRIM remaps beats/events/sound ONCE onto output clock; rejects double remap."""
        rdir = self._run_dir("r1_02_edl_remap")
        # Transcript with safe pause gap [1.7, 2.7] & [4.1, 5.0]
        tr = {
            "language": "en",
            "words": [
                {"word": "Just", "start": 0.1, "end": 0.4, "probability": 0.98},
                {"word": "twelve", "start": 0.45, "end": 0.9, "probability": 0.98},
                {"word": "hundred", "start": 0.95, "end": 1.45, "probability": 0.98},
                {"word": "bills", "start": 2.9, "end": 3.3, "probability": 0.98},
                {"word": "groceries", "start": 3.4, "end": 3.9, "probability": 0.98},
                {"word": "left", "start": 5.2, "end": 5.7, "probability": 0.98},
            ],
        }
        tr_p = self._tmp / "tr_pauses.json"
        tr_p.write_text(json.dumps(tr), encoding="utf-8")

        router.run_recommend(
            video_path=self.vid_6s, run_root=rdir, script_text=self.script_text,
            transcript_path=tr_p, cut_policy="PRESERVE_SCRIPT_TRIM", layout_lock="FULLSCREEN_9_16", test_only=True,
        )
        router.run_select(run_root=rdir, skill_id="SK-04", approval_source="TEST_ONLY_FIXTURE", test_only=True)
        ex = router.run_execute(run_root=rdir, mode="plan", sfx_library_dirs=[self.sfx_dir], production_mode=False)
        self.assertEqual(ex["exit_code"], 0, ex)
        self.assertEqual(ex["status"], "KH03_PLAN_PASS")

        edl = contracts.parse_json_strict(rdir / "EDL.json")
        sb = contracts.parse_json_strict(rdir / "SHOTBOOK_V2.json")
        remap_rep = contracts.parse_json_strict(rdir / "manifests" / "TIMELINE_REMAP_REPORT.json")
        self.assertEqual(remap_rep["clock_domain"], "OUTPUT_EDL_MAPPED")
        self.assertEqual(sb["provenance"]["kh03_clock_domain"], "OUTPUT_EDL_MAPPED")
        self.assertEqual(sb["provenance"]["kh03_time_map_sha256"], remap_rep["time_map_sha256"])

        total_out = int(edl["total_output_frames"])
        self.assertEqual(sb["output_spec"]["total_frames"], total_out)
        self.assertEqual(sb["beats"][0]["start_frame"], 0)
        self.assertEqual(sb["beats"][-1]["end_frame"], total_out)
        for ev in sb["visual_events"]:
            self.assertGreaterEqual(int(ev["start_frame"]), 0)
            self.assertLessEqual(int(ev["end_frame"]), total_out)

        # Negative test: double remap MUST fail closed!
        cp = contracts.parse_json_strict(rdir / "manifests" / "CREATIVE_PLAN.json")
        ov = director.compile_to_kh02_overrides(cp, fps=24.0)
        mapped_once = director.remap_kh03_plan_to_output_clock(ov, edl)
        with self.assertRaises(ValueError):
            director.remap_kh03_plan_to_output_clock(mapped_once, edl)

    def test_k03r1_03_fullscreen_camera_lock_and_decoded_frame_qa_catches_1frame_shrink(self) -> None:
        """K03R1-03 (R1.02/B02/B09): FULLSCREEN_9_16 locks camera 1.0x across ALL beats; decoded QA catches 1-frame PIP shrink."""
        rdir = self._run_dir("r1_03_fullscreen")
        router.run_recommend(
            video_path=self.vid_6s, run_root=rdir, script_text=self.script_text,
            layout_lock="FULLSCREEN_9_16", cut_policy="LOCK_VOICE", test_only=True,
        )
        router.run_select(run_root=rdir, skill_id="SK-04", approval_source="TEST_ONLY_FIXTURE", test_only=True)
        ex = router.run_execute(run_root=rdir, mode="plan", production_mode=False)
        self.assertEqual(ex["exit_code"], 0, ex)

        sb = contracts.parse_json_strict(rdir / "SHOTBOOK_V2.json")
        cam_qa = contracts.parse_json_strict(rdir / "qc" / "CAMERA_GEOMETRY_QA.json")
        self.assertTrue(cam_qa["pass"])
        self.assertGreaterEqual(len(sb["camera_events"]), 1)
        for ce in sb["camera_events"]:
            self.assertEqual(ce["target_region"], [0.0, 0.0, 1.0, 1.0])
            self.assertAlmostEqual(float(ce["scale_from"]), 1.0, places=3)
            self.assertAlmostEqual(float(ce["scale_to"]), 1.0, places=3)
            self.assertAlmostEqual(float(ce.get("presenter_opacity", 1.0)), 1.0, places=3)

        # Decoded frame-by-frame QA: identical video -> PASS; intentional 1-frame black/PIP defect -> FAIL!
        qa_pass = visual_qa.verify_fullscreen_frames(
            self.vid_6s, self.vid_6s, out_dir=rdir / "qc_pass",
        )
        self.assertTrue(qa_pass["pass"], qa_pass)
        self.assertEqual(qa_pass["status"], "DECODED_RENDER_PASS")

        # Create a 1-frame corrupted/black-flash copy at frame 10 -> MUST FAIL decoded fullscreen QA!
        bad_mp4 = rdir / "bad_flash.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-i", str(self.vid_6s),
            "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black@1.0:t=fill:enable='eq(n,10)'",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", "copy", str(bad_mp4),
        ], check=True, timeout=60)
        qa_fail = visual_qa.verify_fullscreen_frames(
            self.vid_6s, bad_mp4, out_dir=rdir / "qc_fail",
        )
        self.assertFalse(qa_fail["pass"])
        self.assertEqual(qa_fail["status"], "DECODED_RENDER_FAIL")
        self.assertGreaterEqual(qa_fail["fail_frame_count"], 1)

    def test_k03r1_04_missing_shotbook_fails_visual_qa_not_default_pass(self) -> None:
        """K03R1-04 (R1.02): audit_fullscreen_and_collisions never silently defaults to PASS when shotbook is absent."""
        cp_stub = {"layout_constraints": {"fullscreen_lock": True, "collision_ok": True}, "components": []}
        inp_stub = {"media": {"fps_num": 30000, "fps_den": 1001}, "safe_regions": {"protected_zones": [], "clear_overlay_zones": []}}
        res = visual_qa.audit_fullscreen_and_collisions(cp_stub, inp_stub, shotbook=None, require_shotbook=True)
        self.assertFalse(res["pass"])
        self.assertAlmostEqual(res["fps_used"], 29.97, places=1)

    def test_k03r1_05_sound_director_reaches_stage_g_and_caption_music_cli_propagates(self) -> None:
        """K03R1-05 (R1.03/B03/B10): KH-03 sound_plan propagates into KH-02 Stage G & RESOLVED_SOUND_PLAN.json; caption/music policies respected."""
        rdir = self._run_dir("r1_05_sound_stage_g")
        router.run_recommend(
            video_path=self.vid_6s, run_root=rdir, script_text=self.script_text,
            caption_policy="ON_REQUESTED", music_policy="OPTIONAL_LICENSED", test_only=True,
        )
        router.run_select(run_root=rdir, skill_id="SK-04", approval_source="TEST_ONLY_FIXTURE", test_only=True)
        ex = router.run_execute(
            run_root=rdir, mode="plan", sfx_library_dirs=[self.sfx_dir], production_mode=False,
        )
        self.assertEqual(ex["exit_code"], 0, ex)
        brief = contracts.parse_json_strict(rdir / "manifests" / "KH02_DERIVED_BRIEF.json")
        self.assertEqual(brief["caption_policy"], "ON_REQUESTED")
        self.assertEqual(brief["music_policy"], "OPTIONAL_LICENSED")
        res_sp = contracts.parse_json_strict(rdir / "stems" / "RESOLVED_SOUND_PLAN.json")
        sfx_app = contracts.parse_json_strict(rdir / "stems" / "SFX_APPLIED.json")
        self.assertGreaterEqual(res_sp["planned_add_events"], 1)
        self.assertGreaterEqual(sfx_app["count"], 1)

    def test_k03r1_06_hook_fidelity_preserved_in_stage_e_never_overwritten(self) -> None:
        """K03R1-06 (R1.04/B07): KH-03 selected_hook_override is authoritative in Stage E & SHOTBOOK_V2 (not overwritten)."""
        rdir = self._run_dir("r1_06_hook_fidelity")
        router.run_recommend(video_path=self.vid_6s, run_root=rdir, script_text=self.script_text, test_only=True)
        router.run_select(run_root=rdir, skill_id="SK-01", approval_source="TEST_ONLY_FIXTURE", test_only=True)
        ex = router.run_execute(run_root=rdir, mode="plan", production_mode=False)
        self.assertEqual(ex["exit_code"], 0, ex)
        cp = contracts.parse_json_strict(rdir / "manifests" / "CREATIVE_PLAN.json")
        sb = contracts.parse_json_strict(rdir / "SHOTBOOK_V2.json")
        self.assertEqual(sb["selected_hook"]["selected_option_id"], cp["hook_plan"]["selected_hook_id"])

    def test_k03r1_07_selection_receipt_rejects_modified_script_file_and_forged_direct_kh02_dict(self) -> None:
        """K03R1-07 (R1.06/B05): Modifying script file on disk OR passing forged dict directly to kh02.run.execute_pipeline fails closed."""
        rdir = self._run_dir("r1_07_receipt_hardening")
        script_file = rdir / "source_script.txt"
        script_file.parent.mkdir(parents=True, exist_ok=True)
        script_file.write_text(self.script_text, encoding="utf-8")

        router.run_recommend(video_path=self.vid_6s, run_root=rdir, script_path=script_file, test_only=True)
        sel = router.run_select(run_root=rdir, skill_id="SK-02", approval_source="TEST_ONLY_FIXTURE", test_only=True)
        self.assertEqual(sel["receipt"]["trust_model"], selection.TRUST_MODEL_NOTE)
        self.assertTrue(bool(sel["receipt"]["raw_script_sha256"]))
        self.assertTrue(bool(sel["receipt"]["constraints_sha256"]))

        # Negative 1: forged in-memory receipt dict passed directly to kh02.run.execute_pipeline -> REJECTED!
        brief_p = self._tmp / "direct_brief.json"
        brief_p.write_text(json.dumps({
            "schema_version": "kh02.brief.v2", "brief_id": "forged_test", "title": "Forged",
            "mode": "talking_head", "profile": "editorial_pro", "objective": "Test",
            "audience": "general", "platform": "reels", "aspect_ratio": "9:16",
            "language": "en", "tone": "calm", "key_message": "Test",
            "voice_policy": "LOCK_VOICE", "caption_policy": "OFF", "music_policy": "OFF",
            "sfx_policy": "OFF", "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
            "sources": [{"asset_id": "primary_aroll", "path": str(self.vid_6s), "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
        }), encoding="utf-8")
        forged_receipt = dict(sel["receipt"])
        forged_receipt["selected_skill_id"] = "SK-05"
        res_forged = kh02_run.execute_pipeline(
            brief_path=brief_p, run_root=rdir, mode="plan", production_mode=False,
            kh03_selection_receipt=forged_receipt,
            kh03_overrides={"selected_skill_id": "SK-05", "kh03_plan_sha256": "a" * 64},
        )
        self.assertEqual(res_forged["exit_code"], 2)
        self.assertEqual(res_forged["status"], "BLOCKED_KH03_SELECTION_INVALID")

        # Negative 2: mutate script file bytes on disk AFTER selection -> execute MUST FAIL CLOSED!
        script_file.write_text(self.script_text + " MUTATED AFTER SELECTION!", encoding="utf-8")
        ex_mut = router.run_execute(run_root=rdir, mode="plan", production_mode=False)
        self.assertEqual(ex_mut["exit_code"], 2)
        self.assertEqual(ex_mut["status"], "BLOCKED_SELECTION_GATE")
        self.assertIn("FRESH_SCRIPT_FILE_MISMATCH", ex_mut["reason"])

    def test_k03r1_08_status_truth_and_verify_fail_closed_on_missing_artifacts(self) -> None:
        """K03R1-08 (R1.07/B04/B06): mode=plan returns KH03_PLAN_PASS (never CANDIDATE_PENDING_OWNER); missing artifact fails verify."""
        rdir = self._run_dir("r1_08_status_truth")
        router.run_recommend(video_path=self.vid_6s, run_root=rdir, script_text=self.script_text, test_only=True)
        router.run_select(run_root=rdir, skill_id="SK-03", approval_source="TEST_ONLY_FIXTURE", test_only=True)
        ex = router.run_execute(run_root=rdir, mode="plan", production_mode=False)
        self.assertEqual(ex["exit_code"], 0, ex)
        self.assertEqual(ex["status"], "KH03_PLAN_PASS")
        self.assertIsNone(ex["final_mp4_path"])

        # verify(require_render=False) -> PASS; verify(require_render=True) -> FAIL (no MP4 rendered in plan mode!)
        self.assertEqual(router.run_verify(run_root=rdir, production_mode=False, require_render=False)["status"], "VERIFY_PASS")
        vf_ren = router.run_verify(run_root=rdir, production_mode=False, require_render=True)
        self.assertEqual(vf_ren["status"], "VERIFY_FAIL")

        # Delete SHOTBOOK_V2.json -> verify MUST FAIL CLOSED!
        (rdir / "SHOTBOOK_V2.json").unlink()
        vf_miss = router.run_verify(run_root=rdir, production_mode=False)
        self.assertEqual(vf_miss["status"], "VERIFY_FAIL")
        self.assertTrue(any("MISSING_REQUIRED_ARTIFACT" in i for i in vf_miss["issues"]))

    def test_k03r1_09_gallery_separates_native_gsap_from_offline_shim(self) -> None:
        """K03R1-09 (R1.05/B08): Gallery emits GALLERY_VERIFICATION.json separating native_gsap_verified vs shim_only_test_pass."""
        gdir = self._run_dir("r1_09_gallery")
        rep = visual_qa.run_kh03_component_gallery(gdir)
        self.assertTrue(rep["pass"])
        self.assertTrue((gdir / "GALLERY_VERIFICATION.json").is_file())
        self.assertTrue(bool(rep["gsap_vendor_sha256"]))
        self.assertTrue(bool(rep["browser_version"]))
        if rep["gsap_mode"] == "OFFLINE_DETERMINISTIC_SHIM_TEST_ONLY":
            self.assertFalse(rep["native_gsap_verified"])
            self.assertTrue(rep["shim_only_test_pass"])
            self.assertEqual(rep["production_gallery_status"], "SHIM_ONLY_TEST_PASS_BLOCKED_MISSING_NATIVE_GSAP")

    def test_k03r1_10_ffmpeg_9_fps_mode_cfr_real_paired_av_cut(self) -> None:
        """K03R1-10 (R1.08): rough_cut.apply_r1_cut_map uses -fps_mode cfr cleanly on FFmpeg 9+ and verifies exact frames."""
        rdir = self._run_dir("r1_10_ffmpeg9_cut")
        rdir.mkdir(parents=True, exist_ok=True)
        cands = edit_decisions.propose_r1_cut_candidates(
            source_asset_id="src_6s", source_sha256=contracts.hash_artifact(self.vid_6s),
            source_duration_sec=6.0, transcript=None, silences=[(1.7, 2.7)], cut_policy="PRESERVE_SCRIPT_TRIM",
        )
        edl = edit_decisions.build_r1_time_map(
            "src_6s", 144, 24, 1, cut_policy="PRESERVE_SCRIPT_TRIM", candidate_doc=cands,
        )
        out_mp4 = rdir / "trimmed.mp4"
        res = rough_cut.apply_r1_cut_map(self.vid_6s, edl, out_mp4)
        self.assertTrue(res["applied"])
        self.assertTrue(out_mp4.is_file())
        ver = rough_cut.verify_cut_result(
            self.vid_6s, out_mp4, edl, expected_source_sha256=contracts.hash_artifact(self.vid_6s),
        )
        self.assertTrue(ver["pass"], ver)

    def test_k03r1_11_workers1_vs_workers2_canonical_determinism_on_ntsc(self) -> None:
        """K03R1-11 (R1.08): workers=1 vs workers=2 produce identical canonical SHOTBOOK & sound plan on NTSC 30000/1001."""
        r1 = self._run_dir("r1_11_w1")
        r2 = self._run_dir("r1_11_w2")
        for rd, w in ((r1, 1), (r2, 2)):
            router.run_recommend(video_path=self.vid_ntsc, run_root=rd, script_text=self.script_text, layout_lock="FULLSCREEN_9_16", test_only=True)
            router.run_select(run_root=rd, skill_id="SK-04", approval_source="TEST_ONLY_FIXTURE", test_only=True)
            ex = router.run_execute(run_root=rd, mode="plan", sfx_library_dirs=[self.sfx_dir], workers=w, production_mode=False)
            self.assertEqual(ex["exit_code"], 0, ex)
        s1 = contracts.parse_json_strict(r1 / "SHOTBOOK_V2.json")
        s2 = contracts.parse_json_strict(r2 / "SHOTBOOK_V2.json")
        self.assertEqual(s1["beats"], s2["beats"])
        self.assertEqual(s1["visual_events"], s2["visual_events"])
        self.assertEqual(s1["camera_events"], s2["camera_events"])

    def test_k03r1_12_cli_entry_point_flags_and_offline_default(self) -> None:
        """K03R1-12 (R1.03/R1.07): Real CLI `python -m scripts.kh03` parses new R1 flags cleanly (offline default, online-opt-in)."""
        rdir = self._run_dir("r1_12_cli")
        rc_rec = router.main([
            "recommend", "--video", str(self.vid_6s), "--run-root", str(rdir),
            "--script-text", self.script_text, "--layout-lock", "FULLSCREEN_9_16",
            "--caption-policy", "ON_REQUESTED", "--music-policy", "OFF",
            "--purpose", "NONCOMMERCIAL_ENGINEERING",
        ])
        self.assertEqual(rc_rec, 0)
        rc_sel = router.main(["select", "--run-root", str(rdir), "--skill", "SK-05"])
        self.assertEqual(rc_sel, 0)
        rc_exe = router.main(["execute", "--run-root", str(rdir), "--mode", "plan", "--sfx-library", str(self.sfx_dir)])
        self.assertEqual(rc_exe, 0)
        rc_vf = router.main(["verify", "--run-root", str(rdir)])
        self.assertEqual(rc_vf, 0)


if __name__ == "__main__":
    unittest.main()
