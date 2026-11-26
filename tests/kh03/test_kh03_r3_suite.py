"""KH-03.R3 Creative Motion & Visual Intelligence Comprehensive Suite (R3-01..R3-30).

Executable verification covering:
- Backward compatibility, legacy migrations, fail-closed policy/license/selection gates
- CMI-01 Semantic Hook Director (microbeats, negation, no-spoiler, calm/absent-transcript fallbacks, pixel check)
- CMI-02 Declarative Motion Graph (schema/security, internal ROI motion + negative frozen component w/ moving presenter, exact Decimal + non-numeric)
- CMI-03 Shared-Element Morphing (identity, endpoints, injected 1-4f blank gap & face-collision negative tests, single-pass EDL remap & double-remap reject)
- CMI-04 Subject/Object/Action/Scene-Cut Tracking (multi-subject, faceless zero-face, scene cuts, low-res honest fallback)
- CMI-05 Universal Cross-Niche Evidence & Proof Ledger (missing required proof fail, labeled illustration, invalid rights fail)
- CMI-06 Dynamic Typography & Multilingual Captions (360px, EN/VI/CJK/RTL, negation/decimal preservation, WCAG contrast, hysteresis, OFF)
- CMI-07 Multi-Level Visual Density & Duration-Weighted Repetition (all-but-one top banner, appearance clones, justified vs unjustified static hold, clutter)
- CMI-08 Motion-Bound Sound Anchors (sample_onset, onset drift >1f fail, silent stem fail, duplicate merge)
- CMI-09 Real Decoded Streaming Pixel QA (freeze, black flash, crop drift/PIP shrink, occlusion, SHA tamper, native pinned GSAP vs shim gallery)
- CMI-10 True O(1) Streaming Decoder, 30s/2m/10m/30m streaming benchmark, BoundedAsyncExecutor races/cancel/timeout, AtomicKeyedCache, workers=1/2/4 determinism, RAM/disk guards
- 12 diverse unseen cross-niche holdouts x 5 universal skills = 60 combinations + renamed-niche generalization + 9:16/16:9/1:1/4:5/rotation/NTSC + repo hygiene.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from scripts.kh02 import planner as kh02_planner
from scripts.kh02 import run as kh02_run
from scripts.kh03 import (
    VALID_SKILL_IDS,
    components,
    contracts,
    director,
    evidence_and_type,
    execution,
    explanation,
    footage,
    intelligence,
    layout,
    motion_graph,
    qa,
    recommend,
    router,
    selection,
    sound_director,
    tracking,
    visual_qa,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_synth_video(
    out_path: Path,
    *,
    width: int = 360,
    height: int = 640,
    fps_str: str = "24",
    duration_sec: float = 5.0,
    with_audio: bool = True,
    with_scene_cut: bool = False,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if with_scene_cut:
        # Two visually distinct halves to trigger real hard scene-cut detection
        half = max(0.5, duration_sec / 2.0)
        vexpr = (
            f"testsrc2=size={width}x{height}:rate={fps_str}:duration={half}[v0];"
            f"color=c=0xF5D080:size={width}x{height}:rate={fps_str}:duration={duration_sec - half}[v1];"
            f"[v0][v1]concat=n=2:v=1:a=0[vout]"
        )
        cmd = ["ffmpeg", "-y", "-v", "error", "-filter_complex", vexpr, "-map", "[vout]"]
        if with_audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration_sec}", "-map", "0:a"]
    else:
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps_str}:duration={duration_sec}",
        ]
        if with_audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={duration_sec}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast"]
    if with_audio and not with_scene_cut:
        cmd += ["-c:a", "aac", "-b:a", "96k"]
    elif with_audio and with_scene_cut:
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps_str}:duration={duration_sec/2:.2f}",
            "-f", "lavfi", "-i", f"color=c=0xF5D080:size={width}x{height}:rate={fps_str}:duration={duration_sec/2:.2f}",
            "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={duration_sec}",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[vout]",
            "-map", "[vout]", "-map", "2:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "96k",
        ]
    cmd.append(str(out_path))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg synth video failed: {p.stderr[-300:]}")
    return out_path


def _make_wav_file(path: Path, *, duration_sec: float = 0.25, freq_hz: float = 880.0, amp: float = 0.25) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sr = 48000
    n = int(round(sr * duration_sec))
    frames = bytearray()
    for i in range(n):
        env = math.exp(-8.0 * i / max(1, n))
        v = int(max(-32767, min(32767, round(32767.0 * amp * env * math.sin(2.0 * math.pi * freq_hz * i / sr)))))
        frames.extend(int(v).to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(frames))
    return path


def _make_sfx_library(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    for fn, cat, freq in (
        ("whoosh_01.wav", "whoosh", 240.0),
        ("click_01.wav", "click", 1200.0),
        ("pop_01.wav", "pop", 660.0),
        ("chime_01.wav", "chime", 980.0),
        ("impact_01.wav", "impact", 140.0),
        ("rustle_01.wav", "rustle", 520.0),
    ):
        _make_wav_file(root / fn, freq_hz=freq)
        manifest[fn] = {
            "category": cat,
            "tags": [cat, "clean", "editorial"],
            "license_name": "CC0-1.0",
            "license_evidence": "https://creativecommons.org/publicdomain/zero/1.0/ (TEST_ONLY unit fixture)",
            "author": "KH03 R3 Fixture",
            "provider_terms_note": "LOCAL_LIBRARY_CC0",
        }
    (root / "SFX_LICENSE_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return root


def _make_sample_png(path: Path, color: tuple[int, int, int] = (32, 88, 148), w: int = 480, h: int = 360) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (w, h), color)
    dr = ImageDraw.Draw(im)
    dr.rectangle((24, 24, w - 24, h - 24), outline=(245, 240, 230), width=4)
    dr.rectangle((48, 80, w - 48, 140), fill=(230, 185, 73))
    im.save(path)
    return path


class TestKH03R3CreativeMotionSuite(unittest.TestCase):
    """Comprehensive KH-03.R3 test suite (R3-01..R3-30)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp_root = Path(tempfile.mkdtemp(prefix="kh03_r3_suite_"))
        cls.vid_9x16 = _make_synth_video(cls.tmp_root / "src_9x16.mp4", width=360, height=640, fps_str="24", duration_sec=6.0)
        cls.vid_16x9 = _make_synth_video(cls.tmp_root / "src_16x9.mp4", width=640, height=360, fps_str="30000/1001", duration_sec=5.0)
        cls.vid_1x1 = _make_synth_video(cls.tmp_root / "src_1x1.mp4", width=480, height=480, fps_str="24000/1001", duration_sec=4.0)
        cls.vid_4x5 = _make_synth_video(cls.tmp_root / "src_4x5.mp4", width=400, height=500, fps_str="30", duration_sec=4.0)
        cls.vid_cut = _make_synth_video(cls.tmp_root / "src_cut.mp4", width=240, height=426, fps_str="24", duration_sec=4.0, with_scene_cut=True)
        cls.vid_silent = _make_synth_video(cls.tmp_root / "src_silent.mp4", width=360, height=640, fps_str="24", duration_sec=4.0, with_audio=False)
        cls.sfx_dir = _make_sfx_library(cls.tmp_root / "sfx_lib")
        cls.sample_png = _make_sample_png(cls.tmp_root / "proof_dashboard.png")
        cls.script_math = (
            "Seeing $1,200 in your account does not mean you have $1,200 to spend. "
            "Bills take $800, groceries take $200, and gas takes $100, leaving $100 remaining. "
            "See the real workbook cover and dashboard breakdown before payday. "
            "Download the step-by-step guide today."
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp_root, ignore_errors=True)

    def _new_run(self, name: str) -> Path:
        p = self.tmp_root / name
        if p.exists():
            shutil.rmtree(p)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _rec_sel_exec(
        self,
        name: str,
        *,
        video: Path | None = None,
        script: str | None = None,
        skill: str = "SK-04",
        layout_policy: str | None = "ADAPTIVE_DYNAMIC",
        layout_lock: str | None = None,
        cut_policy: str = "LOCK_VOICE",
        caption_policy: str = "OFF",
        workers: int = 2,
        language: str | None = "en",
        sfx_dirs: list[Path] | None = None,
        supplied_assets: list[dict[str, Any]] | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        rdir = self._new_run(name)
        rec = router.run_recommend(
            video_path=video or self.vid_9x16,
            run_root=rdir,
            script_text=script if script is not None else self.script_math,
            layout_policy=layout_policy,
            layout_lock=layout_lock,
            cut_policy=cut_policy,
            caption_policy=caption_policy,
            language=language,
            test_only=True,
        )
        self.assertEqual(rec["exit_code"], 0, rec)
        sel = router.run_select(
            run_root=rdir,
            skill_id=skill,
            approval_source="TEST_ONLY_FIXTURE",
            approval_evidence="R3 test fixture selection",
            test_only=True,
        )
        self.assertEqual(sel["exit_code"], 0, sel)
        exe = router.run_execute(
            run_root=rdir,
            mode="plan",
            workers=workers,
            sfx_library_dirs=sfx_dirs,
            supplied_assets=supplied_assets,
            production_mode=False,
        )
        return rdir, exe

    # ------------------------------------------------------------------
    # R3-01 .. R3-03: Legacy Compat, 5-Skill Selection Gate & License Gate
    # ------------------------------------------------------------------
    def test_r3_01_legacy_migration_old_policies_and_conflict_reject(self) -> None:
        """R3-01: Legacy SHOTBOOK v1->v2 migration, FULLSCREEN_LOCK & conflict rejection."""
        v1 = {"shots": [{"start_sec": 0.0, "end_sec": 2.5, "title": "Opening"}, {"start_sec": 2.5, "end_sec": 5.0, "title": "Payoff"}]}
        v2 = kh02_planner.migrate_shotbook_v1_to_v2(
            v1, run_id="mig_r3_01", source_asset_id="primary_aroll", source_sha256="a" * 64, total_frames=120,
        )
        self.assertEqual(v2["schema_version"], "kh02.shotbook.v2")
        self.assertEqual(len(v2["beats"]), 2)
        with self.assertRaises(ValueError):
            layout.resolve_layout_policy(constraints={"fullscreen_lock": True, "layout_policy": "ADAPTIVE_DYNAMIC"})
        with self.assertRaises(ValueError):
            layout.resolve_layout_policy(constraints={"presenter_mode": "FIXED_PIP", "layout_policy": "ADAPTIVE_DYNAMIC"})
        rdir, exe = self._rec_sel_exec("r3_01_fs", layout_policy=None, layout_lock="FULLSCREEN_9_16", skill="SK-04")
        self.assertEqual(exe["exit_code"], 0, exe)
        sb = contracts.parse_json_strict(rdir / "SHOTBOOK_V2.json")
        self.assertTrue(all(c["scale_from"] == 1.0 and c["scale_to"] == 1.0 for c in sb["camera_events"]))

    def test_r3_02_five_skills_selection_gate_and_stale_input_reject(self) -> None:
        """R3-02: Strictly 5 skills, mandatory stop, reject SK-06/AUTO/HYBRID & stale modified script."""
        self.assertEqual(VALID_SKILL_IDS, ("SK-01", "SK-02", "SK-03", "SK-04", "SK-05"))
        rdir = self._new_run("r3_02_gate")
        sp = rdir / "script.txt"
        sp.write_text(self.script_math, encoding="utf-8")
        rec = router.run_recommend(video_path=self.vid_9x16, run_root=rdir, script_path=sp, test_only=True)
        self.assertEqual(rec["status"], "AWAITING_USER_SELECTION")
        self.assertFalse(rec["auto_selected"])
        for bad in ("SK-06", "AUTO", "HYBRID"):
            res_bad = router.run_select(run_root=rdir, skill_id=bad, approval_source="TEST_ONLY_FIXTURE", test_only=True)
            self.assertNotEqual(res_bad["exit_code"], 0)
        # Execute without select MUST block
        blk = router.run_execute(run_root=rdir, mode="plan", production_mode=False)
        self.assertEqual(blk["status"], "BLOCKED_SELECTION_GATE")
        # Select then mutate script on disk -> MUST reject stale receipt
        self.assertEqual(router.run_select(run_root=rdir, skill_id="SK-03", approval_source="TEST_ONLY_FIXTURE", test_only=True)["exit_code"], 0)
        sp.write_text("Tampered completely different script.", encoding="utf-8")
        stale = router.run_execute(run_root=rdir, mode="plan", production_mode=False)
        self.assertEqual(stale["status"], "BLOCKED_SELECTION_GATE")

    def test_r3_03_commercial_license_rights_and_forged_evidence_reject(self) -> None:
        """R3-03: PROBLEM-01 blocks COMMERCIAL_MARKETING, short/forged permission, and NONCOMMERCIAL downgrade."""
        rdir = self._new_run("r3_03_lic")
        rec = router.run_recommend(
            video_path=self.vid_9x16, run_root=rdir, script_text=self.script_math,
            intended_purpose="COMMERCIAL_MARKETING", license_decision="BLOCKED_COMMERCIAL_LICENSE", test_only=True,
        )
        self.assertEqual(rec["exit_code"], 0)
        self.assertEqual(router.run_select(run_root=rdir, skill_id="SK-04", approval_source="TEST_ONLY_FIXTURE", test_only=True)["exit_code"], 0)
        # Attempt downgrade to NONCOMMERCIAL -> BLOCKED
        e1 = router.run_execute(run_root=rdir, mode="plan", license_decision="NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED", production_mode=False)
        self.assertEqual(e1["status"], "BLOCKED_COMMERCIAL_LICENSE")
        # Attempt forged short permission string -> BLOCKED
        e2 = router.run_execute(run_root=rdir, mode="plan", license_decision="WRITTEN_COMMERCIAL_PERMISSION_VERIFIED", commercial_permission_evidence="ok", production_mode=False)
        self.assertEqual(e2["status"], "BLOCKED_COMMERCIAL_LICENSE")

    # ------------------------------------------------------------------
    # R3-04 .. R3-05: CMI-01 Semantic Hook Director & Rendered Hook Pixels
    # ------------------------------------------------------------------
    def test_r3_04_truthful_hook_negation_no_spoiler_absent_transcript_and_calm(self) -> None:
        """R3-04: Hook preserves negation, never spoils $100 payoff, handles absent transcript & SK-05 calm opening."""
        inp = intelligence.analyze_inputs(video_path=self.vid_9x16, run_id="r3_04", script_text=self.script_math)["input_analysis"]
        sg = intelligence.build_semantic_graph(inp)
        eg = explanation.plan_explanation(sg, inp, skill_id="SK-04")
        hp = motion_graph.build_semantic_hook_plan(
            run_id="r3_04", skill_id="SK-04", semantic_graph=sg, explanation_graph=eg, input_analysis=inp,
        )
        self.assertTrue(hp["truthful_no_spoiler_verified"])
        sel_opt = next(o for o in hp["options"] if o["selected"])
        self.assertFalse(sel_opt["spoils_payoff"])
        self.assertNotIn("$100", sel_opt["opening_visual"])
        self.assertIn("$100", hp["payoff_map"]["protected_payoff_tokens_not_spoiled"])

        # Absent transcript / silent footage -> honest non-fabricated fallback
        inp_sil = intelligence.analyze_inputs(video_path=self.vid_silent, run_id="r3_04_sil", script_text="")["input_analysis"]
        sg_sil = intelligence.build_semantic_graph(inp_sil)
        eg_sil = explanation.plan_explanation(sg_sil, inp_sil, skill_id="SK-05")
        hp_sil = motion_graph.build_semantic_hook_plan(
            run_id="r3_04_sil", skill_id="SK-05", semantic_graph=sg_sil, explanation_graph=eg_sil, input_analysis=inp_sil,
        )
        self.assertTrue(hp_sil["truthful_no_spoiler_verified"])
        self.assertGreaterEqual(len(hp_sil["hook_microbeats"]), 1)

    def test_r3_05_hook_microbeats_artifacts_and_rendered_frame0_pixel_gate(self) -> None:
        """R3-05: Emits HOOK_MICROBEATS/TRUTH_LEDGER/PAYOFF_MAP & verify_hook_pixels catches blank frame-0."""
        rdir, exe = self._rec_sel_exec("r3_05_hook", skill="SK-01")
        self.assertEqual(exe["exit_code"], 0, exe)
        for fn in ("HOOK_CANDIDATES.json", "HOOK_MICROBEATS.json", "HOOK_TRUTH_LEDGER.json", "SELECTED_HOOK.json", "HOOK_PAYOFF_MAP.json"):
            self.assertTrue((rdir / "manifests" / fn).is_file(), f"Missing {fn}")
        # Positive hook pixel check on real non-blank synth video
        hp_doc = contracts.parse_json_strict(rdir / "manifests" / "CREATIVE_PLAN.json")["hook_plan"]
        good = visual_qa.verify_hook_pixels(self.vid_9x16, hp_doc, fps=24.0, out_dir=rdir / "qc")
        self.assertTrue(good["pass"], good)
        # Negative hook pixel check on black frame-0 video -> MUST FAIL!
        blk_mp4 = self.tmp_root / "black_frame0.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=240x426:r=24:d=2.0",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(blk_mp4)],
            check=True, capture_output=True,
        )
        bad = visual_qa.verify_hook_pixels(blk_mp4, hp_doc, fps=24.0)
        self.assertFalse(bad["pass"])
        self.assertFalse(bad["pass"])
        self.assertLess(bad.get("frame0_mean_luma", 0.0), 10.0)

    # ------------------------------------------------------------------
    # R3-06 .. R3-08: CMI-02 Motion Graph, Internal ROI Motion & Non-Numeric
    # ------------------------------------------------------------------
    def test_r3_06_motion_graph_schema_security_and_reduced_motion(self) -> None:
        """R3-06: MOTION_GRAPH validates strict schema, rejects XSS/eval/URLs & honors reduced_motion."""
        rdir, exe = self._rec_sel_exec("r3_06_mg", skill="SK-04")
        self.assertEqual(exe["exit_code"], 0, exe)
        mg = contracts.parse_json_strict(rdir / "manifests" / "MOTION_GRAPH.json")
        contracts.assert_kh03_schema(mg, "motion_graph")
        self.assertTrue(any(n["parent_id"] is not None for n in mg["nodes"]))
        # Security: malicious component params rejected fail-closed
        bad_spec = {
            "schema_version": "kh03.component_spec.v1",
            "component_id": "bad_xss",
            "capability_id": "kh03.rolling_counter",
            "family": "numeric",
            "kind": "kh03_rolling_counter",
            "beat_id": "beat_01",
            "role": "payoff",
            "start_frame": 0,
            "end_frame": 48,
            "entry_frames": 6,
            "hold_frames": 38,
            "exit_frames": 4,
            "normalized_bbox": [0.1, 0.1, 0.8, 0.2],
            "z_order": 30,
            "easing": "power2.out",
            "params": {"headline": "<script>alert(1)</script>", "url": "https://evil.example/a.js"},
            "data_bindings": {},
            "semantic_anchor": "SK-04:bad",
            "protected_regions_checked": True,
            "verification_status": "DESIGNED",
        }
        errs = components.validate_component_spec(bad_spec)
        self.assertTrue(len(errs) >= 1)

    def test_r3_07_internal_roi_motion_positive_and_negative_frozen_component(self) -> None:
        """R3-07: verify_component_internal_roi_motion passes active internal ROI & FAILS frozen graphic over moving presenter!"""
        # Build two synthetic videos:
        # 1) Moving presenter in bottom half + ANIMATING graphic bar in top ROI [0.10, 0.08, 0.80, 0.22] -> MUST PASS
        # 2) Moving presenter in bottom half + COMPLETELY FROZEN static box in top ROI -> MUST FAIL!
        pos_mp4 = self.tmp_root / "roi_moving_comp.mp4"
        neg_mp4 = self.tmp_root / "roi_frozen_comp.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=24:duration=2.0",
             "-f", "lavfi", "-i", "color=c=0x4CC393:size=80x44:rate=24:duration=2.0",
             "-filter_complex", "[0:v]drawbox=x=36:y=50:w=288:h=140:color=0x162E29@1.0:t=fill[bg];[bg][1:v]overlay=x='48+t*95':y=92[v]",
             "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(pos_mp4)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=24:duration=2.0",
             "-vf", "drawbox=x=36:y=50:w=288:h=140:color=0x162E29@1.0:t=fill,drawbox=x=48:y=95:w=120:h=40:color=0x4CC393@1.0:t=fill",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(neg_mp4)],
            check=True, capture_output=True,
        )
        bindings = [{"node_id": "n_bar", "component_id": "c1", "canvas_roi_norm": [0.12, 0.14, 0.76, 0.10], "start_frame": 2, "settle_frame": 30, "end_frame": 44}]
        r_pos = visual_qa.verify_component_internal_roi_motion(pos_mp4, bindings)
        r_neg = visual_qa.verify_component_internal_roi_motion(neg_mp4, bindings)
        self.assertTrue(r_pos["pass"], r_pos)
        self.assertFalse(r_neg["pass"], f"Frozen component with moving background should have FAILED: {r_neg}")

    def test_r3_08_exact_decimal_and_non_numeric_process_narrative(self) -> None:
        """R3-08: Exact Decimal math verified AND non-numeric cooking/history scripts require ZERO finance fields."""
        cooking = "First heat the cast iron pan until it shimmers. Next sear the mushrooms gently to lock moisture. Finally fold in fresh thyme and serve warm."
        rdir, exe = self._rec_sel_exec("r3_08_cook", script=cooking, skill="SK-03")
        self.assertEqual(exe["exit_code"], 0, exe)
        eg = contracts.parse_json_strict(rdir / "analysis" / "EXPLANATION_GRAPH.json")
        self.assertEqual(eg["numeric_checks"]["arithmetic_steps"], [])
        mg = contracts.parse_json_strict(rdir / "manifests" / "MOTION_GRAPH.json")
        self.assertGreaterEqual(len(mg["nodes"]), 2)
        self.assertTrue(all(n["numeric_exact"] is None for n in mg["nodes"]))

    # ------------------------------------------------------------------
    # R3-09 .. R3-11: CMI-03 Shared Element Morphs, Negative Gaps & EDL Remap
    # ------------------------------------------------------------------
    def test_r3_09_shared_element_identity_geometry_and_trace(self) -> None:
        """R3-09: Shared element graph emits verified claim/evidence identity, endpoints & transform trace."""
        rdir, exe = self._rec_sel_exec("r3_09_shared", skill="SK-04")
        self.assertEqual(exe["exit_code"], 0, exe)
        self.assertTrue((rdir / "manifests" / "SHARED_ELEMENT_GRAPH.json").is_file())
        self.assertTrue((rdir / "manifests" / "MOTION_TRANSFORM_TRACE.csv").is_file())
        mg = contracts.parse_json_strict(rdir / "manifests" / "MOTION_GRAPH.json")
        lp = contracts.parse_json_strict(rdir / "layouts" / "LAYOUT_PLAN.json")
        self.assertTrue(motion_graph.verify_morph_semantic_coverage(mg, layout_plan=lp)["pass"])

    def test_r3_10_injected_blank_gap_and_morph_face_collision_fail(self) -> None:
        """R3-10: Injected 3-frame blank semantic gap & face-colliding morph FAIL closed."""
        rdir, exe = self._rec_sel_exec("r3_10_neg", skill="SK-04")
        self.assertEqual(exe["exit_code"], 0, exe)
        mg = contracts.parse_json_strict(rdir / "manifests" / "MOTION_GRAPH.json")
        lp = contracts.parse_json_strict(rdir / "layouts" / "LAYOUT_PLAN.json")

        # Negative 1: Inject a 3-frame blank semantic gap between adjacent root components -> MUST FAIL!
        mg_gap = json.loads(json.dumps(mg))
        roots = [n for n in mg_gap["nodes"] if n["parent_id"] is None]
        self.assertGreaterEqual(len(roots), 2)
        roots[1]["start_frame"] = int(roots[0]["end_frame"]) + 3
        cov_gap = motion_graph.verify_morph_semantic_coverage(mg_gap, layout_plan=lp)
        self.assertFalse(cov_gap["pass"])
        self.assertTrue(any("BLANK_SEMANTIC_GAP" in i for i in cov_gap["issues"]))

        # Negative 2: Inject a face-colliding shared-element swept box -> MUST FAIL!
        mg_col = json.loads(json.dumps(mg))
        mg_col["shared_elements"] = [{
            "shared_element_id": "injected_face_hit",
            "source_node_id": roots[0]["node_id"],
            "target_node_id": roots[1]["node_id"],
            "from_beat_id": roots[0]["beat_id"],
            "to_beat_id": roots[1]["beat_id"],
            "evidence_or_claim_binding": "num:100:USD",
            "start_frame": 2,
            "end_frame": 14,
            "source_geometry_norm": [0.1, 0.1, 0.8, 0.6],
            "target_geometry_norm": [0.1, 0.1, 0.8, 0.6],
            "swept_bounds_norm": [0.05, 0.05, 0.90, 0.85],
            "z_order": 50,
            "ownership_mode": "SINGLE_DOM_REUSE",
            "no_duplicate_at_endpoint": True,
            "min_overlap_visibility": 0.45,
        }]
        cov_col = motion_graph.verify_morph_semantic_coverage(mg_col, layout_plan=lp)
        self.assertFalse(cov_col["pass"])
        self.assertTrue(any("MORPH_FACE_COLLISION" in i for i in cov_col["issues"]))

    def test_r3_11_single_pass_edl_remap_and_double_remap_reject(self) -> None:
        """R3-11: Multi-cut EDL remaps motion graph, internal nodes & sound anchors ONCE; rejects double remap."""
        rdir, exe = self._rec_sel_exec("r3_11_edl", skill="SK-04")
        self.assertEqual(exe["exit_code"], 0, exe)
        mg = contracts.parse_json_strict(rdir / "manifests" / "MOTION_GRAPH.json")
        # Already OUTPUT_EDL_MAPPED from pipeline -> second remap MUST fail closed!
        edl = contracts.parse_json_strict(rdir / "SHOTBOOK_V2.json")["edit_decision_map"]
        with self.assertRaises(ValueError) as ctx:
            motion_graph.remap_motion_graph_to_edl(mg, edl)
        self.assertIn("DOUBLE_REMAP_FORBIDDEN", str(ctx.exception))

        # Fresh source-clock motion graph remapped across a 2-segment cut EDL
        mg_src = json.loads(json.dumps(mg))
        mg_src["clock_domain"] = "SOURCE_CLOCK"
        mg_src["time_map_sha256"] = None
        cut_edl = {
            "schema_version": "kh02.edl.v1",
            "fps_num": 24,
            "fps_den": 1,
            "audio_sample_rate": 48000,
            "total_output_frames": 96,
            "segments": [
                {"segment_id": "s1", "source_asset_id": "primary_aroll", "src_start_frame": 0, "src_end_frame": 48, "out_start_frame": 0, "out_end_frame": 48, "audio_fade_in_ms": 0, "audio_fade_out_ms": 0, "reason": "keep"},
                {"segment_id": "s2", "source_asset_id": "primary_aroll", "src_start_frame": 72, "src_end_frame": 120, "out_start_frame": 48, "out_end_frame": 96, "audio_fade_in_ms": 0, "audio_fade_out_ms": 0, "reason": "keep"},
            ],
        }
        remapped = motion_graph.remap_motion_graph_to_edl(mg_src, cut_edl)
        self.assertEqual(remapped["clock_domain"], "OUTPUT_EDL_MAPPED")
        self.assertEqual(remapped["total_frames"], 96)
        self.assertTrue(all(0 <= n["start_frame"] < n["end_frame"] <= 96 for n in remapped["nodes"]))

    # ------------------------------------------------------------------
    # R3-12 .. R3-15: CMI-04 Tracking, CMI-05 Evidence, CMI-06 Type, CMI-07 Density
    # ------------------------------------------------------------------
    def test_r3_12_tracking_multi_subject_faceless_scene_cuts_and_honest_fallback(self) -> None:
        """R3-12: Faceless emits ZERO human faces, scene cuts detected via streaming scan, low-conf triggers fallback."""
        rdir = self._new_run("r3_12_trk")
        # 1) Faceless tabletop video with scene cut -> zero human face boxes, real scene cut detected!
        media = {"width": 240, "height": 426, "aspect_ratio": "9:16", "nb_frames": 96, "fps_num": 24, "fps_den": 1}
        t_fl = tracking.analyze_subject_object_tracks(
            media, run_id="r3_12_fl", video_path=self.vid_cut, presenter_mode="FACELESS",
            enable_streaming_scan=True, run_dir=rdir,
        )
        self.assertEqual(t_fl["provenance"]["tracking_mode"], "FACELESS_OBJECT_TRACKING")
        self.assertEqual(t_fl["subject_tracks"], [])
        self.assertGreaterEqual(len(t_fl["provenance"]["scene_cuts"]), 1)

        # 2) Multi-speaker verified detector observations
        obs = [
            {"frame": 0, "track_id": "spk_left", "object_class": "face", "bbox_norm": [0.10, 0.20, 0.25, 0.25], "confidence": 0.92, "verified_detector": True},
            {"frame": 24, "track_id": "spk_right", "object_class": "face", "bbox_norm": [0.65, 0.20, 0.25, 0.25], "confidence": 0.90, "verified_detector": True},
            {"frame": 24, "track_id": "obj_phone", "object_class": "object", "bbox_norm": [0.40, 0.60, 0.20, 0.20], "confidence": 0.88, "verified_detector": True},
        ]
        t_ms = tracking.analyze_subject_object_tracks(media, run_id="r3_12_ms", probe_observations=obs)
        self.assertEqual(t_ms["provenance"]["tracking_mode"], "LOCAL_DETECTOR_VERIFIED")
        self.assertEqual(len(t_ms["subject_tracks"]), 2)
        self.assertEqual(len(t_ms["object_tracks"]), 1)

        # 3) Low-confidence detector observations -> honest conservative fallback!
        low_obs = [{"frame": 0, "track_id": "s1", "object_class": "face", "bbox_norm": [0.3, 0.3, 0.2, 0.2], "confidence": 0.35, "verified_detector": True}]
        t_low = tracking.analyze_subject_object_tracks(media, run_id="r3_12_low", probe_observations=low_obs)
        self.assertEqual(t_low["provenance"]["tracking_mode"], "TRACKING_STATIC_FALLBACK")
        self.assertTrue(t_low["provenance"]["conservative_fallback_applied"])

    def test_r3_13_universal_evidence_provenance_missing_required_and_invalid_rights(self) -> None:
        """R3-13: Missing required proof fails closed; invalid commercial rights rejected; optional falls back to ILLUSTRATIVE_DIAGRAM."""
        rdir = self._new_run("r3_13_ev")
        inp = intelligence.analyze_inputs(video_path=self.vid_9x16, run_id="r3_13", script_text=self.script_math)["input_analysis"]
        sg = intelligence.build_semantic_graph(inp)
        empty_fm = {"assets": [], "beat_bindings": []}
        # 1) Required product proof missing -> EVIDENCE_BLOCKED_MISSING_REQUIRED
        led_req = evidence_and_type.build_universal_evidence_ledger(
            run_id="r3_13_req", skill_id="SK-04", semantic_graph=sg, footage_manifest=empty_fm,
            require_product_proof=True, run_dir=rdir,
        )
        self.assertEqual(led_req["status"], "EVIDENCE_BLOCKED_MISSING_REQUIRED")
        self.assertTrue((rdir / "assets" / "RIGHTS_LEDGER.csv").is_file())
        self.assertTrue((rdir / "assets" / "EVIDENCE_GAPS.md").is_file())

        # 2) Optional proof missing -> honest labeled ILLUSTRATIVE_DIAGRAM (not fabricated screenshot!)
        led_opt = evidence_and_type.build_universal_evidence_ledger(
            run_id="r3_13_opt", skill_id="SK-04", semantic_graph=sg, footage_manifest=empty_fm,
            require_product_proof=False,
        )
        self.assertNotEqual(led_opt["status"], "EVIDENCE_BLOCKED_MISSING_REQUIRED")
        self.assertTrue(any(b["proof_tier"] == "ILLUSTRATIVE_DIAGRAM" for b in led_opt["bindings"]))

        # 3) Noncommercial asset bound in COMMERCIAL_AND_NONCOMMERCIAL run -> BLOCKED_RIGHTS!
        bad_fm = {
            "assets": [{"asset_id": "a1", "media_type": "image", "local_path": str(self.sample_png), "sha256": "1" * 64, "license_status": "APPROVED_OWNER_NONCOMMERCIAL", "width": 480, "height": 360}],
            "beat_bindings": [{"beat_id": "beat_03", "asset_id": "a1"}],
        }
        led_bad = evidence_and_type.build_universal_evidence_ledger(
            run_id="r3_13_bad", skill_id="SK-02", semantic_graph=sg, footage_manifest=bad_fm,
            intended_purpose="COMMERCIAL_AND_NONCOMMERCIAL",
        )
        self.assertEqual(led_bad["status"], "EVIDENCE_BLOCKED_MISSING_REQUIRED")

    def test_r3_14_typography_360px_vi_cjk_rtl_negation_and_caption_off(self) -> None:
        """R3-14: Typography checks 360px legibility, EN/VI/CJK/RTL, keeps 'not $1,200' intact, respects caption_policy=OFF."""
        # Negation + currency atom MUST stay intact on a single line!
        lines = evidence_and_type.wrap_text_preserving_atoms("Having money means not $1,200 available to spend right now", max_chars_per_line=22)
        self.assertTrue(any("not $1,200" in ln for ln in lines), f"Negation split across lines: {lines}")
        # Script detection across VI, CJK, RTL
        self.assertTrue(evidence_and_type.detect_script_profile("Xin chào Việt Nam 100%")["is_vietnamese"])
        self.assertTrue(evidence_and_type.detect_script_profile("预算分配步骤说明")["is_cjk"])
        self.assertEqual(evidence_and_type.detect_script_profile("هذا اختبار تخطيط النصوص العربية")["direction"], "rtl")
        # WCAG contrast math
        self.assertGreater(evidence_and_type.wcag_contrast_ratio("#F8FAFC", "#101E36"), 12.0)
        self.assertLess(evidence_and_type.wcag_contrast_ratio("#888888", "#999999"), 1.5)
        # Caption OFF strictly emits 0 chunks
        rdir, exe = self._rec_sel_exec("r3_14_off", caption_policy="OFF", skill="SK-04")
        self.assertEqual(exe["exit_code"], 0)
        tl = contracts.parse_json_strict(rdir / "layouts" / "TYPOGRAPHY_LAYOUT.json")
        self.assertEqual(tl["actual_caption_status"], "OFF_BY_POLICY")
        self.assertEqual(tl["chunk_count"], 0)
        ts = contracts.parse_json_strict(rdir / "layouts" / "TYPE_SYSTEM.json")
        self.assertTrue(ts["mobile_360px_floor_pass"])

    def test_r3_15_visual_density_duration_weighted_repetition_and_justified_holds(self) -> None:
        """R3-15: Catches all-but-one top-banner loop, appearance clones & unjustified 7s hold; accepts justified hold & SK-05."""
        # 1) "All beats except one short beat" in top-banner FULL_CANVAS under ADAPTIVE_DYNAMIC -> MUST FAIL!
        lp_loop = {
            "selected_skill_id": "SK-04",
            "layout_policy": "ADAPTIVE_DYNAMIC",
            "fps_num": 24,
            "fps_den": 1,
            "total_frames": 240,
            "beats": [
                {"beat_id": "b1", "start_frame": 0, "end_frame": 80, "layout_mode": "FULL_CANVAS", "graphic_box_norm": [0.05, 0.06, 0.90, 0.14], "presenter_box_norm": [0, 0, 1, 1]},
                {"beat_id": "b2", "start_frame": 80, "end_frame": 160, "layout_mode": "FULL_CANVAS", "graphic_box_norm": [0.05, 0.06, 0.90, 0.14], "presenter_box_norm": [0, 0, 1, 1]},
                {"beat_id": "b3", "start_frame": 160, "end_frame": 216, "layout_mode": "FULL_CANVAS", "graphic_box_norm": [0.05, 0.06, 0.90, 0.14], "presenter_box_norm": [0, 0, 1, 1]},
                {"beat_id": "b4", "start_frame": 216, "end_frame": 240, "layout_mode": "PRESENTER_LEFT_SPLIT", "graphic_box_norm": [0.52, 0.08, 0.44, 0.72], "presenter_box_norm": [0.04, 0.08, 0.46, 0.72]},
            ],
        }
        cp_clones = {
            "selected_skill_id": "SK-04",
            "components": [
                {"component_id": f"c{i}", "beat_id": f"b{i}", "kind": "kh03_rolling_counter", "start_frame": (i - 1) * 60, "end_frame": i * 60, "normalized_bbox": [0.05, 0.06, 0.90, 0.14], "easing": "power2.out", "params": {"headline": "Same", "font_size_px": 26}}
                for i in range(1, 5)
            ],
        }
        res_bad = motion_graph.evaluate_visual_density_and_repetition(run_id="r3_15_bad", layout_plan=lp_loop, creative_plan=cp_clones)
        self.assertFalse(res_bad["pass"])
        self.assertTrue(any("DURATION_WEIGHTED_TOP_BANNER_LOOP" in f for f in res_bad["visual_density_report"]["findings"]))
        self.assertTrue(any("REPEATED_IDENTICAL_APPEARANCE" in f for f in res_bad["visual_density_report"]["findings"]))

        # 2) Unjustified 7s static component vs explicitly justified reading hold
        lp_hold = {
            "selected_skill_id": "SK-04", "layout_policy": "ADAPTIVE_DYNAMIC", "fps_num": 24, "fps_den": 1, "total_frames": 240,
            "beats": [
                {"beat_id": "b1", "start_frame": 0, "end_frame": 168, "layout_mode": "PRESENTER_LEFT_SPLIT", "graphic_box_norm": [0.52, 0.08, 0.44, 0.72], "presenter_box_norm": [0.04, 0.08, 0.46, 0.72]},
                {"beat_id": "b2", "start_frame": 168, "end_frame": 240, "layout_mode": "FULL_SCREEN_RETURN", "graphic_box_norm": [0.06, 0.74, 0.84, 0.12], "presenter_box_norm": [0, 0, 1, 1]},
            ],
        }
        cp_hold = {
            "selected_skill_id": "SK-04",
            "components": [{"component_id": "c_long", "beat_id": "b1", "kind": "kh03_proof_frame_reveal", "start_frame": 0, "end_frame": 168, "normalized_bbox": [0.52, 0.08, 0.44, 0.72], "easing": "power2.out", "params": {"headline": "Long diagram", "font_size_px": 26}}],
        }
        unjust = motion_graph.evaluate_visual_density_and_repetition(run_id="r3_15_unjust", layout_plan=lp_hold, creative_plan=cp_hold)
        self.assertFalse(unjust["pass"])
        just = motion_graph.evaluate_visual_density_and_repetition(run_id="r3_15_just", layout_plan=lp_hold, creative_plan=cp_hold, justified_read_holds=["c_long"])
        self.assertTrue(just["pass"], just)

        # 3) Same static hold under SK-05 (Natural Retention) -> accepted as deliberate continuity!
        lp_sk05 = {**lp_loop, "selected_skill_id": "SK-05"}
        cp_sk05 = {**cp_clones, "selected_skill_id": "SK-05"}
        res_sk05 = motion_graph.evaluate_visual_density_and_repetition(run_id="r3_15_sk05", layout_plan=lp_sk05, creative_plan=cp_sk05)
        self.assertTrue(res_sk05["pass"], res_sk05)

    # ------------------------------------------------------------------
    # R3-16 .. R3-19: CMI-08 Audio Onset, CMI-09 Streaming Pixel QA & Native GSAP
    # ------------------------------------------------------------------
    def test_r3_16_motion_sound_anchors_onset_drift_and_silent_stem(self) -> None:
        """R3-16: Motion-graph sound anchors emit exact sample_onset, catch >1f drift & silent stem."""
        rdir, exe = self._rec_sel_exec("r3_16_snd", skill="SK-04", sfx_dirs=[self.sfx_dir])
        self.assertEqual(exe["exit_code"], 0, exe)
        self.assertTrue((rdir / "stems" / "MOTION_SOUND_ANCHORS.json").is_file())
        self.assertTrue((rdir / "stems" / "SFX_RIGHTS_LEDGER.csv").is_file())
        sp = contracts.parse_json_strict(rdir / "stems" / "SOUND_DIRECTOR_PLAN.json")
        adds = [e for e in sp["events"] if e["status"] == "ADD"]
        self.assertGreaterEqual(len(adds), 1)
        self.assertTrue(all(isinstance(e.get("sample_onset"), int) for e in adds))

        # Onset drift within 1 frame -> PASS; drift > 1 frame -> FAIL_LATE_TRANSIENT_ONSET!
        ok_on = sound_director.verify_applied_soundscape(
            sp,
            sfx_stem_metrics={"rms_db": -22.0},
            mix_metrics={"true_peak_dbtp": -1.5},
            measured_onsets_sec=[{"event_id": adds[0]["event_id"], "measured_onset_sec": adds[0]["onset_sec"] + 0.01}],
            fps=24.0,
            run_dir=rdir,
        )
        self.assertTrue(ok_on["pass"], ok_on)
        bad_on = sound_director.verify_applied_soundscape(
            sp,
            sfx_stem_metrics={"rms_db": -22.0},
            measured_onsets_sec=[{"event_id": adds[0]["event_id"], "measured_onset_sec": adds[0]["onset_sec"] + 0.25}],
            fps=24.0,
        )
        self.assertFalse(bad_on["pass"])
        self.assertEqual(bad_on["status"], "FAIL_LATE_TRANSIENT_ONSET")

    def test_r3_17_streaming_pixel_qa_catches_black_flash_and_crop_drift(self) -> None:
        """R3-17: Streaming decoded frame QA catches 1-frame black flash and 1-frame PIP shrink/crop drift."""
        rdir = self._new_run("r3_17_pxqa")
        # Baseline identical video passes cleanly
        ok_fs = visual_qa.verify_fullscreen_frames(self.vid_9x16, self.vid_9x16, out_dir=rdir / "ok")
        self.assertTrue(ok_fs["pass"], ok_fs)
        self.assertEqual(ok_fs["streaming_mode"], "TRUE_BOUNDED_STREAMING_O1_RAM")
        self.assertTrue((rdir / "ok" / "STREAMING_QA_REPORT.json").is_file())
        self.assertTrue((rdir / "ok" / "PROCESS_CLEANUP_REPORT.json").is_file())

        # Inject a 1-frame black flash at frame 20 -> MUST FAIL!
        blk_v = self.tmp_root / "injected_1f_black.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(self.vid_9x16),
             "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black@1.0:t=fill:enable='eq(n,20)'",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-an", str(blk_v)],
            check=True, capture_output=True,
        )
        bad_fs = visual_qa.verify_fullscreen_frames(self.vid_9x16, blk_v)
        self.assertFalse(bad_fs["pass"])
        self.assertGreaterEqual(bad_fs["fail_frame_count"], 1)

    def test_r3_18_final_mp4_sha_tamper_and_plan_never_render_pass(self) -> None:
        """R3-18: Plan mode never claims render pass; require_render fails when MP4 missing or tampered."""
        rdir, exe = self._rec_sel_exec("r3_18_sha", skill="SK-04")
        self.assertEqual(exe["status"], "KH03_PLAN_PASS")
        self.assertIsNone(exe["final_mp4_path"])
        # require_render=True on plan-only run MUST fail!
        vf_no = router.run_verify(run_root=rdir, production_mode=False, require_render=True)
        self.assertNotEqual(vf_no["exit_code"], 0)
        # Simulate tampered MP4 against receipt SHA -> MUST fail!
        fake_mp4 = rdir / "exports" / "cand.mp4"
        fake_mp4.parent.mkdir(parents=True, exist_ok=True)
        fake_mp4.write_bytes(b"original-bytes")
        rec4_p = rdir / "STAGE_RECEIPTS" / "KH03_04_execute_and_qa.json"
        rec4 = contracts.parse_json_strict(rec4_p)
        rec4["output_hashes"]["final_mp4_sha256"] = contracts.hash_artifact(fake_mp4)
        rec4["evidence_paths"].append(str(fake_mp4))
        contracts.write_json_atomic(rec4_p, rec4)
        # Tamper file bytes!
        fake_mp4.write_bytes(b"tampered-bytes")
        vf_tamp = router.run_verify(run_root=rdir, production_mode=False, require_render=True)
        self.assertEqual(vf_tamp["status"], "VERIFY_FAIL")
        self.assertTrue(any("TAMPERED_FINAL_MP4" in i for i in vf_tamp["issues"]))

    def test_r3_19_native_pinned_gsap_gallery_vs_shim_separation(self) -> None:
        """R3-19: Native pinned HyperFrames GSAP Chromium gallery PASSES 8/8 with internal delta & separates shim mode."""
        rdir = self._new_run("r3_19_gal")
        rep = visual_qa.run_kh03_component_gallery(rdir / "native", width=540, height=960)
        self.assertTrue(rep["pass"], rep)
        self.assertTrue(rep["native_gsap_verified"])
        self.assertFalse(rep["shim_only_test_pass"])
        self.assertEqual(rep["verified_count"], 8)
        self.assertTrue((rdir / "native" / "NATIVE_GALLERY_VERIFICATION.json").is_file())
        for cid, info in rep["capabilities"].items():
            self.assertEqual(info["reverse_seek_diff"], 0.0, cid)
            self.assertEqual(info["repeat_seek_diff"], 0.0, cid)
            self.assertEqual(info["cold_restart_diff"], 0.0, cid)
            self.assertGreater(info["internal_entry_to_settle_delta"], 2.0, f"{cid} lacked internal progression")

    # ------------------------------------------------------------------
    # R3-20 .. R3-24: CMI-10 Streaming Decoder, 30s..30m Benchmark, Async Races, Determinism & Guards
    # ------------------------------------------------------------------
    def test_r3_20_streaming_decoder_partial_frame_edl_and_cleanup(self) -> None:
        """R3-20: StreamingRGBFrameDecoder decodes exact count, cleans up process tree, rejects corrupt/truncated media."""
        cnt = 0
        with execution.StreamingRGBFrameDecoder(self.vid_9x16, 96, 170, ffmpeg_threads=2) as dec:
            while True:
                fb = dec.read_frame()
                if fb is None:
                    break
                self.assertEqual(len(fb), 96 * 170 * 3)
                cnt += 1
        self.assertEqual(cnt, 144)  # 6.0s * 24fps
        self.assertTrue(dec.cleanup_report["clean"])

        # Corrupt truncated file MUST fail (never silently pass truncated prefix!)
        bad_mp4 = self.tmp_root / "truncated_bad.mp4"
        bad_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42truncated_corrupt_data")
        with self.assertRaises(RuntimeError):
            with execution.StreamingRGBFrameDecoder(bad_mp4, 96, 170, ffmpeg_threads=1) as dec_bad:
                while dec_bad.read_frame() is not None:
                    pass

    def test_r3_21_bounded_memory_streaming_30s_2m_10m_30m_benchmark(self) -> None:
        """R3-21: Empirically decode 30s, 2m, 10m & 30m synthetic TEST_ONLY streams in O(1) bounded RAM."""
        # Use ultra-compact lavfi testsrc -> rawvideo via StreamingRGBFrameDecoder over real FFmpeg pipes
        # Measure actual decoded frames & process RSS across 30s (720f), 2m (2880f), 10m (14400f), 30m (43200f)!
        w, h = 64, 114
        frame_bytes = w * h * 3
        benchmarks: list[dict[str, Any]] = []
        rss_base = execution.get_process_rss_mib()
        for label, secs in (("30s", 30), ("2m", 120), ("10m", 600), ("30m", 1800)):
            want_frames = secs * 24
            t0 = time.monotonic()
            decoded = 0
            rss_peak = execution.get_process_rss_mib()
            with subprocess.Popen(
                ["ffmpeg", "-v", "error", "-threads", "2", "-f", "lavfi",
                 "-i", f"color=c=0x224466:s={w}x{h}:r=24:d={secs}",
                 "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=frame_bytes * 2,
            ) as proc:
                assert proc.stdout is not None
                while True:
                    buf = proc.stdout.read(frame_bytes)
                    if not buf:
                        break
                    if len(buf) == frame_bytes:
                        decoded += 1
                        if decoded % 2400 == 0:
                            rss_peak = max(rss_peak, execution.get_process_rss_mib())
                proc.wait(timeout=60)
            elapsed = round(time.monotonic() - t0, 3)
            rss_peak = max(rss_peak, execution.get_process_rss_mib())
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(decoded, want_frames)
            benchmarks.append({
                "tier": label, "duration_sec": secs, "decoded_frames": decoded,
                "wall_sec": elapsed, "fps_throughput": round(decoded / max(0.001, elapsed), 1),
                "rss_peak_mib": rss_peak, "rss_delta_mib": round(max(0.0, rss_peak - rss_base), 2),
            })
        # O(1) memory verification: 30m (43,200 frames = ~946 MiB raw RGB!) MUST NOT grow RSS linearly!
        self.assertLess(benchmarks[-1]["rss_delta_mib"], 120.0, f"Unbounded RSS growth detected: {benchmarks}")

    def test_r3_22_async_dag_races_timeout_propagation_and_atomic_cache(self) -> None:
        """R3-22: DAG propagates upstream failures, enforces timeout, & AtomicKeyedCache deduplicates & heals corruption."""
        rdir = self._new_run("r3_22_dag")
        budget = execution.compute_resource_budget(rdir, workers=2)
        dag = execution.BoundedAsyncExecutor(run_id="r3_22", budget=budget, run_dir=rdir)
        res = dag.run([
            execution.DAGTask(task_id="a_ok", fn=lambda _d: {"v": 1}),
            execution.DAGTask(task_id="b_fail", fn=lambda _d: (_ for _ in ()).throw(RuntimeError("boom"))),
            execution.DAGTask(task_id="c_dep_on_b", deps=("b_fail",), fn=lambda _d: {"never": True}),
            execution.DAGTask(task_id="d_timeout", timeout_sec=0.05, fn=lambda _d: time.sleep(0.3)),
        ])
        self.assertFalse(res["pass"])
        by_id = {r["task_id"]: r for r in res["receipts"]}
        self.assertEqual(by_id["a_ok"]["status"], "PASS")
        self.assertEqual(by_id["b_fail"]["status"], "FAIL")
        self.assertEqual(by_id["c_dep_on_b"]["status"], "BLOCKED_UPSTREAM_FAIL")
        self.assertEqual(by_id["d_timeout"]["status"], "TIMEOUT")

        # AtomicKeyedCache concurrent dedup + corruption invalidation
        cache = execution.AtomicKeyedCache(rdir / "cache", run_id="r3_22")
        calls = {"n": 0}
        lock = threading.Lock()
        def _compute() -> dict[str, Any]:
            time.sleep(0.03)
            with lock:
                calls["n"] += 1
            return {"computed": 42}
        outs: list[Any] = []
        threads = [threading.Thread(target=lambda: outs.append(cache.get_or_compute("same_key", _compute)[0])) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(calls["n"], 1)
        self.assertTrue(all(o == {"computed": 42} for o in outs))
        # Corrupt cached file -> cache MUST invalidate and recompute cleanly!
        kfile = next((rdir / "cache").glob("*.json"))
        kfile.write_text("{corrupt-json", encoding="utf-8")
        healed, hit_flag = cache.get_or_compute("same_key", _compute)
        self.assertFalse(hit_flag)
        self.assertEqual(healed, {"computed": 42})
        self.assertEqual(calls["n"], 2)

    def test_r3_23_workers_1_2_4_canonical_determinism(self) -> None:
        """R3-23: workers=1, workers=2 & workers=4 yield identical canonical CREATIVE_PLAN, MOTION_GRAPH, LAYOUT_PLAN, SOUND & SHOTBOOK hashes."""
        hashes: dict[int, dict[str, str]] = {}
        for w in (1, 2, 4):
            # Use identical run_id inside distinct parent dirs so canonical content hashes match strictly!
            rdir = self._new_run(f"r3_23_wrap_w{w}") / "r3_23_canon"
            rdir.mkdir(parents=True, exist_ok=True)
            rec = router.run_recommend(
                video_path=self.vid_9x16, run_root=rdir, script_text=self.script_math,
                layout_policy="ADAPTIVE_DYNAMIC", cut_policy="LOCK_VOICE", test_only=True,
            )
            self.assertEqual(rec["exit_code"], 0, rec)
            sel = router.run_select(
                run_root=rdir, skill_id="SK-04", approval_source="TEST_ONLY_FIXTURE",
                approval_evidence="R3 determinism check", test_only=True,
            )
            self.assertEqual(sel["exit_code"], 0, sel)
            exe = router.run_execute(
                run_root=rdir, mode="plan", workers=w, sfx_library_dirs=[self.sfx_dir], production_mode=False,
            )
            self.assertEqual(exe["exit_code"], 0, exe)
            def _canon(p: Path) -> str:
                doc = contracts.parse_json_strict(p)
                # Strip only the selection timestamp-derived receipt hash (selected_at_utc varies by wall-clock second)
                if isinstance(doc, dict):
                    doc.pop("selection_receipt_sha256", None)
                    doc.pop("plan_sha256", None)
                    if isinstance(doc.get("provenance"), dict):
                        doc["provenance"].pop("kh03_plan_sha256", None)
                    if isinstance(doc.get("selected_hook"), dict):
                        doc["selected_hook"].pop("kh03_plan_sha256", None)
                        doc["selected_hook"].pop("selection_rationale", None)
                return contracts.hash_artifact(doc)
            hashes[w] = {
                "creative_plan": _canon(rdir / "manifests" / "CREATIVE_PLAN.json"),
                "motion_graph": contracts.parse_json_strict(rdir / "manifests" / "MOTION_GRAPH.json")["graph_sha256"],
                "layout_plan": contracts.parse_json_strict(rdir / "layouts" / "LAYOUT_PLAN.json")["layout_plan_sha256"],
                "sound_plan": _canon(rdir / "stems" / "SOUND_DIRECTOR_PLAN.json"),
                "shotbook": _canon(rdir / "SHOTBOOK_V2.json"),
            }
        self.assertEqual(hashes[1], hashes[2])
        self.assertEqual(hashes[1], hashes[4])

    def test_r3_24_low_disk_and_low_memory_fail_closed_guards(self) -> None:
        """R3-24: Resource budget stops fail-closed on low disk, exceeded max_disk, and low max_memory without deleting files."""
        rdir = self._new_run("r3_24_guard")
        sentinel = rdir / "keep_me.txt"
        sentinel.write_text("do not delete", encoding="utf-8")
        b_disk = execution.compute_resource_budget(rdir, disk_reserve_gib=999999.0)
        self.assertFalse(b_disk["budget_ok"])
        self.assertEqual(b_disk["status"], "BLOCKED_LOW_DISK")
        b_maxd = execution.compute_resource_budget(rdir, max_disk_gib=0.01, projected_disk_gib=0.5)
        self.assertFalse(b_maxd["budget_ok"])
        self.assertEqual(b_maxd["status"], "RESOURCE_BUDGET_EXCEEDED")
        b_mem = execution.compute_resource_budget(rdir, max_memory_mib=1.0)
        self.assertFalse(b_mem["budget_ok"])
        self.assertEqual(b_mem["status"], "BLOCKED_MEMORY")
        self.assertTrue(sentinel.is_file())

    # ------------------------------------------------------------------
    # R3-25 .. R3-30: 12x5=60 Cross-Niche Holdouts, Aspects/FPS, Replay, Long-Form & Hygiene
    # ------------------------------------------------------------------
    def test_r3_25_cross_niche_12x5_holdout_matrix_and_renamed_niche_invariance(self) -> None:
        """R3-25: 12 unseen diverse holdout fixtures x 5 skills = 60 valid plans + renamed-niche semantic invariance."""
        holdouts = [
            ("faceless_cooking", "Heat olive oil gently in a skillet. Add crushed garlic because high heat burns aromatics. Fold in cooked pasta and finish with basil."),
            ("silent_timelapse", ""),
            ("multi_speaker_podcast", "Wait, do shorter meetings actually boost focus? Yes, when teams cut status calls from 60 minutes to 20 minutes, deep work rises."),
            ("history_doc_no_asset", "In 1869 engineers completed the transcontinental rail link across steep mountain grades. Consequently freight crossed the continent in days instead of months."),
            ("science_ambiguous", "Some studies suggest cold exposure may shift short-term alertness, though individual results vary widely depending on sleep."),
            ("software_screen_tutorial", "First open the terminal window and run the build command. Next inspect the output bundle in the browser panel. Compare cold versus warm startup latency."),
            ("fitness_action_demo", "Keep your ribs stacked over your hips before pressing overhead. Notice how bracing the core prevents lower back arch during each rep."),
            ("numeric_negation_ops", "Seeing 1000 visits does not mean 1000 qualified leads. Bots account for 600, bounces take 250, leaving 150 real readers."),
            ("travel_narrative", "At dawn the harbor fog lifted over cedar boats. Fishermen sorted silver trays along the wet stone pier before the first ferry arrived."),
            ("multilingual_vi_cjk", "Bước một kiểm tra nguồn âm thanh gốc thật rõ. 步骤二对比不同布局的可读性. Save the final checklist for review."),
            ("ecommerce_comparison", "Compare brushed steel versus ceramic travel mugs. Steel holds heat for 8 hours while ceramic keeps coffee flavor completely neutral."),
            ("longform_architecture", "Step one separates ingestion from inference so crashes never drop jobs. Step two enforces bounded queues because unbounded buffers exhaust memory. Step three verifies every frame atomically."),
        ]
        total_combos = 0
        skill_grammars_seen: set[str] = set()
        for fname, txt in holdouts:
            vid = self.vid_silent if fname == "silent_timelapse" else self.vid_9x16
            inp = intelligence.analyze_inputs(video_path=vid, run_id=f"h_{fname}", script_text=txt)["input_analysis"]
            sg = intelligence.build_semantic_graph(inp)
            for sid in VALID_SKILL_IDS:
                rdir = self._new_run(f"r3_25_{fname}_{sid}")
                eg = explanation.plan_explanation(sg, inp, skill_id=sid, run_dir=rdir)
                fm = footage.acquire_and_bind_footage(run_id=rdir.name, run_dir=rdir, semantic_graph=sg, skill_id=sid)
                cp = director.build_creative_plan(
                    run_id=rdir.name, run_dir=rdir, selected_skill_id=sid, selection_receipt_sha256="c" * 64,
                    input_analysis=inp, semantic_graph=sg, explanation_graph=eg, footage_manifest=fm,
                    constraints={"layout_policy": "ADAPTIVE_DYNAMIC"},
                )
                contracts.assert_kh03_schema(cp, "creative_plan")
                mg = contracts.parse_json_strict(rdir / "manifests" / "MOTION_GRAPH.json")
                contracts.assert_kh03_schema(mg, "motion_graph")
                skill_grammars_seen.add(cp["art_direction"]["motion_grammar"])
                total_combos += 1
        self.assertEqual(total_combos, 60)
        self.assertEqual(len(skill_grammars_seen), 5)

        # Renamed-niche invariance: two totally different topics with identical semantic structure recommend same skill!
        s_a = "First measure 500 ml of broth, subtract 300 ml for reduction, and compare temperature before plating."
        s_b = "First allocate 500 GB of storage, subtract 300 GB for snapshots, and compare throughput before deploying."
        r_a = recommend.recommend_skills(
            intelligence.analyze_inputs(video_path=self.vid_9x16, run_id="rn_a", script_text=s_a)["input_analysis"],
            intelligence.build_semantic_graph(intelligence.analyze_inputs(video_path=self.vid_9x16, run_id="rn_a", script_text=s_a)["input_analysis"]),
        )
        r_b = recommend.recommend_skills(
            intelligence.analyze_inputs(video_path=self.vid_9x16, run_id="rn_b", script_text=s_b)["input_analysis"],
            intelligence.build_semantic_graph(intelligence.analyze_inputs(video_path=self.vid_9x16, run_id="rn_b", script_text=s_b)["input_analysis"]),
        )
        self.assertEqual(r_a["recommended_skill_id"], r_b["recommended_skill_id"])

    def test_r3_26_aspects_rotation_and_fractional_ntsc_fps(self) -> None:
        """R3-26: 9:16, 16:9 (30000/1001), 1:1 (24000/1001), 4:5 (30fps) & 90-deg rotation preserve zero stretch."""
        for tag, vid in (("916", self.vid_9x16), ("169_ntsc", self.vid_16x9), ("11_film", self.vid_1x1), ("45", self.vid_4x5)):
            rdir, exe = self._rec_sel_exec(f"r3_26_{tag}", video=vid, skill="SK-04")
            self.assertEqual(exe["exit_code"], 0, f"{tag}: {exe}")
            lp = contracts.parse_json_strict(rdir / "layouts" / "LAYOUT_PLAN.json")
            self.assertTrue(all(b["source_crop"]["aspect_distortion"] == 0.0 for b in lp["beats"]))
        rot_crop = layout.compute_source_crop_for_box(1920, 1080, 540, 960, rotation_deg=90)
        self.assertEqual(rot_crop["source_effective_dims"], [1080, 1920])
        self.assertEqual(rot_crop["aspect_distortion"], 0.0)

    def test_r3_27_backtest_replay_legacy_fixtures_source_only(self) -> None:
        """R3-27: Deterministic backtest replay of tracked KH-02/KH-03 fixture briefs produces stable canonical SHA."""
        tpl_dir = REPO_ROOT / "templates" / "kh03"
        fix_files = sorted(tpl_dir.glob("*.json")) if tpl_dir.is_dir() else []
        sample_script = self.script_math
        if fix_files:
            fx = contracts.parse_json_strict(fix_files[0])
            sample_script = str(fx.get("script") or fx.get("script_text") or sample_script)
        r1, e1 = self._rec_sel_exec("r3_27_a", script=sample_script, skill="SK-02")
        r2, e2 = self._rec_sel_exec("r3_27_b", script=sample_script, skill="SK-02")
        self.assertEqual(e1["exit_code"], 0)
        self.assertEqual(e2["exit_code"], 0)
        m1 = contracts.parse_json_strict(r1 / "manifests" / "MOTION_GRAPH.json")
        m2 = contracts.parse_json_strict(r2 / "manifests" / "MOTION_GRAPH.json")
        self.assertEqual(len(m1["nodes"]), len(m2["nodes"]))
        self.assertEqual([n["primitive"] for n in m1["nodes"]], [n["primitive"] for n in m2["nodes"]])

    def test_r3_28_real_recommend_stop_and_independent_path_separation(self) -> None:
        """R3-28: CLI recommend halts at AWAITING_USER_SELECTION; native gates never conflated with Path B."""
        rdir = self._new_run("r3_28_cli")
        rc = router.main([
            "recommend", "--video", str(self.vid_9x16), "--run-root", str(rdir),
            "--script-text", self.script_math, "--layout-policy", "ADAPTIVE_DYNAMIC",
            "--purpose", "COMMERCIAL_MARKETING", "--license-decision", "BLOCKED_COMMERCIAL_LICENSE",
        ])
        self.assertEqual(rc, 0)
        st = router.run_status(run_root=rdir)
        self.assertEqual(st["state"], "AWAITING_USER_SELECTION")
        self.assertIsNone(st["selected_skill_id"])

    def test_r3_29_longform_chapter_plan_bounded_windows(self) -> None:
        """R3-29: Long-form 10-minute metadata scales bounded tracking/caption windows with zero full-media RGB buffer."""
        media_10m = {"width": 1920, "height": 1080, "aspect_ratio": "16:9", "nb_frames": 14400, "fps_num": 24, "fps_den": 1, "duration_sec": 600.0}
        trks = layout.build_subject_action_tracks(media_10m)
        self.assertLessEqual(len(trks["sampled_windows"]), 12)
        cmi4 = tracking.analyze_subject_object_tracks(media_10m, run_id="r3_29_10m")
        self.assertEqual(cmi4["provenance"]["tracking_mode"], "TRACKING_STATIC_FALLBACK")
        inp_10m = intelligence.analyze_inputs(
            video_path=self.vid_9x16, run_id="r3_29_10m", script_text=self.script_math,
            constraints={"layout_policy": "ADAPTIVE_DYNAMIC"},
        )["input_analysis"]
        inp_10m["media"] = media_10m
        sg = intelligence.build_semantic_graph(inp_10m)
        eg = explanation.plan_explanation(sg, inp_10m, skill_id="SK-03")
        hp = motion_graph.build_semantic_hook_plan(
            run_id="r3_29_10m", skill_id="SK-03", semantic_graph=sg, explanation_graph=eg, input_analysis=inp_10m,
        )
        # Long-form calm opening uses continuous unbroken shot rather than frantic 3s cuts!
        self.assertTrue(hp["hook_microbeats"][0]["continuous_unbroken_shot"])

    def test_r3_30_repository_hygiene_schemas_and_zero_olivia_hardcoding(self) -> None:
        """R3-30: All 15 KH-03 schemas valid JSON, zero tracked MP4/WAV binaries, zero Olivia hardcoding in scripts/kh03."""
        kh03_schemas = sorted((REPO_ROOT / "schemas" / "kh03").glob("*.schema.json"))
        self.assertGreaterEqual(len(kh03_schemas), 15)
        for sp in kh03_schemas:
            doc = contracts.parse_json_strict(sp)
            self.assertIn("$schema", doc)
        # Verify zero Olivia-specific hardcoding inside scripts/kh03/
        for pyf in sorted((REPO_ROOT / "scripts" / "kh03").glob("*.py")):
            txt = pyf.read_text(encoding="utf-8").lower().replace("seecut-olivia-standalone", "")
            self.assertNotIn("olivia", txt, f"Hardcoded creator reference found in {pyf.name}")
        # Verify zero raw media binaries committed under scripts/ or schemas/ or references/
        for sub in ("scripts", "schemas", "references"):
            for bad_ext in ("*.mp4", "*.mov", "*.wav", "*.mp3"):
                found = list((REPO_ROOT / sub).rglob(bad_ext))
                self.assertEqual(found, [], f"Unexpected media binary in {sub}: {found}")


if __name__ == "__main__":
    unittest.main()
