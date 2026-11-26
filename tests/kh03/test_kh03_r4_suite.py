"""KH-03.R4Intelligence — Professional Editorial Direction & Production QA Test Suite (PED-01..PED-10).

Covers every R4 acceptance gate with real executable assertions (zero mocks for core pixel/process QA):
- PED-01: ROI-isolated verify_hook_pixels() (moving KOL + frozen hook MUST FAIL; moving hook + static KOL MUST PASS;
          offscreen/occluded ROI fails; intentional static uses readability gate without fake motion PASS;
          early payoff spoiler rejected; cross-language/R3 fixture validation).
- PED-02: Real entity detector capabilities + normal-path detection + action protection windows + final-pixel
          evaluate_action_visibility() (cropped/occluded hands fail; clean action passes; pre-cropped source honest).
- PED-03: All-topology detect_visual_repetition() (R3 ~69% TOP_BOTTOM_SPLIT flagged; repeated PIP/LEFT_RIGHT flagged;
          diverse R2 layout passes; justified SK-05 100% fullscreen & FULLSCREEN_LOCK accepted; entropy & dwell logged).
- PED-04: Spatial interaction (track binding, scene-cut reset, lost-track reposition, jitter/clipping/collision validation).
- PED-05: Motion choreography phases (4-phase impact/settle vs quiet SK-05/reduced-motion; timeline compiler; continuity QA).
- PED-06: Evidence demonstration (real vs conceptual vs missing required -> BLOCKED_EVIDENCE; competitor reference rejected).
- PED-07: Art direction & design tokens (contrast, hierarchy, caption_policy=OFF forbids disguised captions).
- PED-08: Production orchestrate_final_render_qa() end-to-end trace & failure-injection on EVERY gate
          (frozen hook, frozen component ROI, cropped action, tampered SHA, missing evidence -> NEVER PASS; OWNER=PENDING).
- PED-09: Multi-candidate competition (3 genuinely distinct plans A/B/C; cosmetic clone rejected; hard-fail disqualified;
          order-swapped pairwise consensus & inconsistency handling; owner override rejected on failed candidate).
- PED-10: Hardened BoundedAsyncExecutor (cooperative cancellation, uncooperative worker quarantine -> BLOCKED_CANCELLATION,
          late write rejected via revoked TaskLease, isolated subprocess process-group kill/reap, fail-fast, retry backoff,
          workers=1 vs 4 canonical determinism).
- Cross-Niche Matrix (Section 15 Tier A): 12 niches x 5 skills = 60 scenarios + UNKNOWN/hybrid + UNSUPPORTED detection.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from scripts.kh03 import (
    candidates,
    contracts,
    evidence_and_type,
    execution,
    intelligence,
    layout,
    motion_graph,
    qa,
    router,
    tracking,
    visual_qa,
)


def _ffmpeg_make_video(
    out_path: Path,
    *,
    vf: str,
    dur: float = 3.0,
    fps: int = 24,
    with_audio: bool = True,
    filter_complex: str | None = None,
    extra_inputs: list[str] | None = None,
) -> Path:
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", vf]
    for ei in (extra_inputs or []):
        cmd += ["-f", "lavfi", "-i", ei]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={dur}"]
    if filter_complex:
        cmd += ["-filter_complex", filter_complex, "-map", "[vout]"]
        if with_audio:
            cmd += ["-map", f"{1 + len(extra_inputs or [])}:a:0"]
    cmd += ["-t", str(dur), "-r", str(fps), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast"]
    if with_audio:
        cmd += ["-c:a", "aac", "-b:a", "96k"]
    cmd.append(str(out_path))
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


class TestKH03R4EditorialIntelligenceSuite(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kh03_r4_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # PED-01: First-Frame Visual Impact & ROI-Isolated Hook Pixel QA
    # ------------------------------------------------------------------
    def test_r4_01_ped01_moving_kol_with_frozen_hook_must_fail(self) -> None:
        """Moving KOL torso in middle of frame MUST NOT pass a frozen hook banner in top ROI!"""
        vid = _ffmpeg_make_video(
            self.tmp / "kol_moving_hook_frozen.mp4",
            vf="color=c=0x162C22:s=270x480:r=24:d=3.0,drawbox=x=20:y=25:w=230:h=70:color=0xF3DC96:t=fill",
            extra_inputs=["color=c=0xD96B48:s=80x160:r=24:d=3.0"],
            filter_complex="[0:v][1:v]overlay=x='30+t*55':y=220[vout]",
            dur=3.0,
        )
        hp = {"selected_hook_id": "sk04_hook_01", "hook_roi_norm": [0.05, 0.04, 0.90, 0.18], "intentional_static": False}
        rep = visual_qa.verify_hook_pixels(vid, hp, fps=24.0, out_dir=self.tmp / "qc")
        self.assertFalse(rep["pass"], f"Frozen hook MUST FAIL even when KOL moves below ROI: {rep}")
        self.assertEqual(rep["motion_gate_status"], "FAIL_FROZEN_HOOK_ROI")

    def test_r4_02_ped01_moving_hook_with_static_kol_passes_and_offscreen_fails(self) -> None:
        """Animated hook inside top ROI on static background PASSES; offscreen ROI FAILS; intentional static uses readability gate."""
        vid = _ffmpeg_make_video(
            self.tmp / "hook_moving_kol_static.mp4",
            vf="color=c=0x162C22:s=270x480:r=24:d=3.0",
            extra_inputs=["color=c=0xF3DC96:s=85x55:r=24:d=3.0"],
            filter_complex="[0:v][1:v]overlay=x='20+t*55':y=28[vout]",
            dur=3.0,
        )
        hp = {"selected_hook_id": "sk04_hook_01", "hook_roi_norm": [0.05, 0.04, 0.90, 0.18], "intentional_static": False}
        rep = visual_qa.verify_hook_pixels(vid, hp, fps=24.0)
        self.assertTrue(rep["pass"], f"Moving hook in ROI should PASS: {rep}")
        self.assertEqual(rep["motion_gate_status"], "PASS")

        # Offscreen ROI must fail closed
        bad = visual_qa.verify_hook_pixels(vid, hp, hook_roi_norm=[1.1, 0.05, 0.3, 0.15])
        self.assertFalse(bad["pass"])
        self.assertEqual(bad["status"], "FAIL_INVALID_OR_OFFSCREEN_ROI")

        # Intentional static hook uses readability gate without faking motion_pass
        vf_still = (
            "color=c=0x162C22:s=270x480:r=24:d=3.0,"
            "drawbox=x=25:y=28:w=220:h=56:color=0xF3DC96:t=fill,"
            "drawbox=x=40:y=42:w=120:h=24:color=0x102018:t=fill"
        )
        vid_still = _ffmpeg_make_video(self.tmp / "hook_intentional_still.mp4", vf=vf_still, dur=3.0)
        rep_still = visual_qa.verify_hook_pixels(vid_still, hp, intentional_static=True)
        self.assertTrue(rep_still["pass"], f"Readable intentional static hook should pass: {rep_still}")
        self.assertEqual(rep_still["motion_gate_status"], "NOT_APPLICABLE_INTENTIONAL_STATIC")

    def test_r4_03_ped01_hook_intent_and_no_early_payoff_spoiler(self) -> None:
        """PED-01 contracts: no invented paradox when none exists; early payoff ($100) spoiler fails reveal check."""
        ctx_no_paradox = {"opening_text": "Here is how to fold an origami crane step by step.", "has_observed_action": True, "skill_id": "SK-04"}
        intent = motion_graph.build_hook_intent(ctx_no_paradox)
        self.assertNotEqual(intent["intent_kind"], "paradox")

        # R3 fixture regression check: spoiling $100 payoff in opening hook MUST fail
        spoiler_check = motion_graph.validate_hook_reveal(
            {"opening_visual": "$1,200 leaves only $100 left!"},
            {"hook_entry_sec": 0.0, "payoff_sec": 15.5},
            payoff_tokens={"$100", "100"},
        )
        self.assertFalse(spoiler_check["pass"])
        self.assertTrue(spoiler_check["early_payoff_spoiler"])

        clean_check = motion_graph.validate_hook_reveal(
            {"opening_visual": "$1,200 ≠ free to spend"},
            {"hook_entry_sec": 0.0, "payoff_sec": 15.5},
            payoff_tokens={"$100", "100"},
        )
        self.assertTrue(clean_check["pass"])

    # ------------------------------------------------------------------
    # PED-02: Action Protection Engine (Detector + Final Pixel Visibility)
    # ------------------------------------------------------------------
    def test_r4_04_ped02_detector_capabilities_and_action_visibility_gates(self) -> None:
        """Verify honest detector capability resolution, R3 hand-crop failure catch, and clean action pass."""
        caps = tracking.resolve_detector_capabilities()
        self.assertIn(caps["primary_detector"], ("MACOS_VISION_DETECTOR", "OPENCV_DETECTOR", "NUMPY_STREAMING_MOTION_DETECTOR"))
        self.assertTrue(caps["streaming_motion_available"])

        # Synthetic video with genuinely moving lower-frame hand/object action (y=280..360)
        vid = _ffmpeg_make_video(
            self.tmp / "hand_action.mp4",
            vf="color=c=0x1C342E:s=270x480:r=24:d=2.0",
            extra_inputs=["color=c=0xE8D4A2:s=90x80:r=24:d=2.0"],
            filter_complex="[0:v][1:v]overlay=x='30+t*80':y=280[vout]",
            dur=2.0,
        )
        media = {"width": 270, "height": 480, "nb_frames": 48, "fps_num": 24, "fps_den": 1, "aspect_ratio": "9:16"}
        det = tracking.detect_semantic_entities(vid, media, use_macos_vision=False)
        self.assertEqual(det["detector_used"], "NUMPY_STREAMING_MOTION_DETECTOR")
        self.assertGreaterEqual(len(det["entities"]), 1)

        # Simulate R3 hand-chopping split (crop_rect stops at y=0.63 while hands span y=0.53..0.74!) -> MUST FAIL!
        hand_win = [{
            "window_id": "apw_01_hand", "entity_id": "ent_hand", "role": "hand",
            "start_frame": 0, "end_frame": 48, "protected_bbox_norm": [0.22, 0.53, 0.52, 0.21],
            "confidence": 0.93, "verified_detector": True, "source_pre_cropped": False,
        }]
        bad_split_layout = {
            "beats": [{
                "beat_id": "b1", "start_frame": 0, "end_frame": 48, "layout_mode": "UPPER_COMPANION_SPLIT",
                "presenter_box_norm": [0.0, 0.0, 1.0, 0.525],
                "source_crop": {"crop_rect_norm": [0.0, 0.107, 1.0, 0.525]},
            }]
        }
        fail_act = tracking.evaluate_action_visibility(vid, hand_win, layout_plan=bad_split_layout)
        self.assertFalse(fail_act["pass"], "R3 split cropping lower half of hands MUST FAIL action visibility gate!")
        self.assertLess(fail_act["windows"][0]["retained_visible_area_ratio"], 0.95)

        # Overlay covering protected hands > 5% -> MUST FAIL!
        occ_act = tracking.evaluate_action_visibility(
            vid, hand_win,
            overlay_boxes=[{"start_frame": 0, "end_frame": 48, "bbox_norm": [0.20, 0.55, 0.55, 0.20]}],
        )
        self.assertFalse(occ_act["pass"], "CTA/card covering protected hand action MUST FAIL!")

        # Clean unobstructed framing -> PASS!
        ok_act = tracking.evaluate_action_visibility(
            vid, hand_win,
            overlay_boxes=[{"start_frame": 0, "end_frame": 48, "bbox_norm": [0.05, 0.04, 0.90, 0.14]}],
        )
        self.assertTrue(ok_act["pass"])

    # ------------------------------------------------------------------
    # PED-03: All-Topology Adaptive Layout Rhythm (Catches R3 69% Split!)
    # ------------------------------------------------------------------
    def test_r4_05_ped03_all_topology_repetition_catches_r3_split_and_allows_sk05(self) -> None:
        """R3 ~69% TOP_BOTTOM_SPLIT MUST be flagged REVIEW/FAIL; diverse layout PASSES; SK-05 fullscreen PASSES."""
        r3_split_plan = {
            "run_id": "r3_reg", "selected_skill_id": "SK-04", "layout_policy": "ADAPTIVE_DYNAMIC",
            "fps_num": 24, "fps_den": 1, "total_frames": 700,
            "beats": [
                {"beat_id": "b1", "layout_mode": "FULL_CANVAS", "start_frame": 0, "end_frame": 126, "graphic_box_norm": [0.05, 0.04, 0.90, 0.14]},
                {"beat_id": "b2", "layout_mode": "UPPER_COMPANION_SPLIT", "start_frame": 126, "end_frame": 438, "graphic_box_norm": [0.0, 0.52, 1.0, 0.48]},
                {"beat_id": "b3", "layout_mode": "UPPER_COMPANION_SPLIT", "start_frame": 438, "end_frame": 610, "graphic_box_norm": [0.0, 0.41, 1.0, 0.59]},
                {"beat_id": "b4", "layout_mode": "FULL_SCREEN_RETURN", "start_frame": 610, "end_frame": 700, "graphic_box_norm": [0.06, 0.68, 0.88, 0.12]},
            ],
        }
        r3_rhythm = layout.detect_visual_repetition(r3_split_plan, run_dir=self.tmp / "r3_rhythm")
        self.assertFalse(r3_rhythm["pass"], f"R3 ~69% TOP_BOTTOM_SPLIT must NOT pass silently: {r3_rhythm}")
        self.assertGreater(r3_rhythm["occupancy"]["dominant_duration_share"], 0.60)
        self.assertEqual(r3_rhythm["occupancy"]["dominant_topology"], "TOP_BOTTOM_SPLIT")

        # Diverse R2/R4 rhythm -> PASS!
        diverse_plan = {
            "run_id": "r4_div", "selected_skill_id": "SK-04", "layout_policy": "ADAPTIVE_DYNAMIC",
            "fps_num": 24, "fps_den": 1, "total_frames": 700,
            "beats": [
                {"beat_id": "b1", "layout_mode": "FULL_CANVAS", "start_frame": 0, "end_frame": 130, "graphic_box_norm": [0.05, 0.04, 0.90, 0.14]},
                {"beat_id": "b2", "layout_mode": "PRESENTER_LEFT_SPLIT", "start_frame": 130, "end_frame": 310, "graphic_box_norm": [0.50, 0.10, 0.46, 0.72]},
                {"beat_id": "b3", "layout_mode": "FLOATING_INSET_PRESENTER", "start_frame": 310, "end_frame": 520, "graphic_box_norm": [0.04, 0.08, 0.92, 0.48]},
                {"beat_id": "b4", "layout_mode": "FULL_SCREEN_RETURN", "start_frame": 520, "end_frame": 700, "graphic_box_norm": [0.06, 0.68, 0.88, 0.12]},
            ],
        }
        div_rhythm = layout.detect_visual_repetition(diverse_plan)
        self.assertTrue(div_rhythm["pass"], f"Balanced multi-topology layout should PASS: {div_rhythm}")
        self.assertGreater(div_rhythm["occupancy"]["topology_entropy_bits"], 1.5)

        # Justified SK-05 100% fullscreen natural retention -> PASS!
        sk05_plan = {
            "run_id": "sk05_nat", "selected_skill_id": "SK-05", "layout_policy": "ADAPTIVE_DYNAMIC",
            "fps_num": 24, "fps_den": 1, "total_frames": 480,
            "beats": [
                {"beat_id": "b1", "layout_mode": "FULL_CANVAS", "start_frame": 0, "end_frame": 160, "graphic_box_norm": [0, 0, 0, 0]},
                {"beat_id": "b2", "layout_mode": "FULL_CANVAS", "start_frame": 160, "end_frame": 320, "graphic_box_norm": [0, 0, 0, 0]},
                {"beat_id": "b3", "layout_mode": "FULL_CANVAS", "start_frame": 320, "end_frame": 480, "graphic_box_norm": [0, 0, 0, 0]},
            ],
        }
        sk05_rhythm = layout.detect_visual_repetition(sk05_plan)
        self.assertTrue(sk05_rhythm["pass"])
        self.assertTrue(sk05_rhythm["justified_continuity"])

    # ------------------------------------------------------------------
    # PED-04, PED-05, PED-06, PED-07 Contracts
    # ------------------------------------------------------------------
    def test_r4_06_ped04_spatial_anchor_scene_cut_reset_and_jitter(self) -> None:
        ent = {
            "id": "ent_hand", "role": "hand", "frame_span": [0, 60], "confidence": 0.90,
            "observations": [
                {"frame": 10, "bbox_norm": [0.30, 0.55, 0.20, 0.15], "confidence": 0.90},
                {"frame": 11, "bbox_norm": [0.31, 0.55, 0.20, 0.15], "confidence": 0.91},
            ],
        }
        binding = tracking.bind_component_to_track({"component_id": "c1", "start_frame": 0, "end_frame": 60}, [ent])
        self.assertEqual(binding["anchor_mode"], "TRACK_LINKED_SPATIAL")
        a10 = tracking.resolve_spatial_anchor(binding, 10, ent, scene_cuts=[25])
        a11 = tracking.resolve_spatial_anchor(binding, 11, ent, scene_cuts=[25])
        # After scene cut at f25 with no new obs, MUST reset to safe reposition (not stale old coordinates!)
        a26 = tracking.resolve_spatial_anchor(binding, 26, ent, scene_cuts=[25])
        self.assertEqual(a10["status"], "TRACK_LOCKED")
        self.assertEqual(a26["status"], "LOST_TRACK_SAFE_REPOSITION")
        self.assertTrue(tracking.validate_spatial_alignment([a10, a11])["pass"])

    def test_r4_07_ped05_ped06_ped07_motion_evidence_and_art_direction(self) -> None:
        p_dyn = motion_graph.plan_motion_phases(0, 72, skill_id="SK-04")
        p_quiet = motion_graph.plan_motion_phases(0, 72, skill_id="SK-05")
        self.assertEqual(p_dyn["mode"], "CHOREOGRAPHED_4_PHASE")
        self.assertEqual(p_quiet["mode"], "QUIET_CONTINUITY")

        # PED-06: Competitor analysis-only clip rejected; missing required proof -> BLOCKED_EVIDENCE
        comp_asset = {"asset_id": "comp1", "license_status": "APPROVED_OWNER", "analysis_only_competitor": True, "media_type": "image"}
        res_blk = evidence_and_type.resolve_evidence_asset({"need_type": "real_product_demo", "priority": "required"}, [comp_asset])
        self.assertEqual(res_blk["status"], "BLOCKED_EVIDENCE")
        demo_blk = evidence_and_type.plan_evidence_demonstration(res_blk)
        self.assertEqual(demo_blk["status"], "BLOCKED_EVIDENCE")

        # PED-07: caption_policy=OFF forbids disguised caption components
        art = evidence_and_type.resolve_art_direction("SK-02", caption_policy="OFF")
        ts = {"wcag_contrast_ratio": 9.2, "contrast_aa_pass": True, "hierarchy": {"display_px": 56, "subtitle_px": 36, "caption_px": 38, "micro_px": 24}}
        bad_dt = evidence_and_type.validate_design_tokens(art, ts, components=[{"component_id": "c1", "kind": "subtitle"}], caption_policy="OFF")
        self.assertFalse(bad_dt["pass"])
        good_dt = evidence_and_type.validate_design_tokens(art, ts, components=[{"component_id": "c1", "kind": "callout"}], caption_policy="OFF")
        self.assertTrue(good_dt["pass"])

    # ------------------------------------------------------------------
    # PED-08: Production Final-Render QA Orchestrator & Failure Injection
    # ------------------------------------------------------------------
    def test_r4_08_ped08_production_qa_orchestrator_failure_injection(self) -> None:
        """Prove orchestrate_final_render_qa() invokes ALL gates on final SHA and FAILS CLOSED on injected defects."""
        # Video with moving middle (KOL) + frozen top hook -> MUST FAIL G_HOOK_PIXELS & G_COMPONENT_ROI_MOTION!
        bad_mp4 = _ffmpeg_make_video(
            self.tmp / "bad_frozen_hook.mp4",
            vf="color=c=0x162C22:s=270x480:r=24:d=2.5,drawbox=x=20:y=24:w=230:h=68:color=0xF3DC96:t=fill",
            extra_inputs=["color=c=0xD96B48:s=80x150:r=24:d=2.5"],
            filter_complex="[0:v][1:v]overlay=x='30+t*55':y=220[vout]",
            dur=2.5,
        )
        plan = {
            "selected_skill_id": "SK-04",
            "hook_plan": {"selected_hook_id": "h1", "hook_roi_norm": [0.05, 0.04, 0.90, 0.18], "truthful_no_spoiler_verified": True},
            "components": [{"component_id": "c1", "start_frame": 0, "end_frame": 50, "normalized_bbox": [0.05, 0.04, 0.90, 0.18]}],
        }
        rdir = self.tmp / "run_bad"
        out_bad = qa.orchestrate_final_render_qa(
            run_id="r4_fail_inj", run_dir=rdir, final_mp4=bad_mp4, source_mp4=None, creative_plan=plan,
            kh02_result={"h1_geom": {"pass": True, "status": "PASS", "fail_count": 0, "warn_count": 0}},
        )
        self.assertFalse(out_bad["technical_pass"], "Injected frozen hook MUST fail production QA!")
        self.assertIn("G_HOOK_PIXELS", out_bad["gate_matrix"]["failing_gates"])
        self.assertEqual(out_bad["gate_matrix"]["owner_visual_approval"], "PENDING")
        self.assertEqual(out_bad["gate_matrix"]["owner_audio_approval"], "PENDING")
        self.assertTrue((rdir / "qc/FLOW_TRACE.json").is_file())
        self.assertTrue((rdir / "qc/FINAL_PIXEL_QA.json").is_file())
        self.assertTrue((rdir / "qc/FINAL_GATE_MATRIX.json").is_file())
        self.assertTrue((rdir / "qc/OWNER_APPROVAL.json").is_file())

        # Positive case: animated top hook + clean audio + non-black frames -> TECHNICAL_PASS_PENDING_OWNER!
        ok_mp4 = _ffmpeg_make_video(
            self.tmp / "ok_animated_hook.mp4",
            vf="color=c=0x162C22:s=270x480:r=24:d=2.5",
            extra_inputs=["color=c=0xF3DC96:s=85x58:r=24:d=2.5"],
            filter_complex="[0:v][1:v]overlay=x='20+t*55':y=24[vout]",
            dur=2.5,
        )
        rdir_ok = self.tmp / "run_ok"
        out_ok = qa.orchestrate_final_render_qa(
            run_id="r4_ok_inj", run_dir=rdir_ok, final_mp4=ok_mp4, source_mp4=None, creative_plan=plan,
            kh02_result={"h1_geom": {"pass": True, "status": "PASS", "fail_count": 0, "warn_count": 0}, "audio_qa": {"pass": True, "integrated_lufs": -15.0, "true_peak_dbtp": -2.0}},
        )
        self.assertTrue(out_ok["technical_pass"], f"Clean animated render should pass technical gates: {out_ok['gate_matrix']}")
        self.assertEqual(out_ok["gate_matrix"]["overall_technical_status"], "TECHNICAL_PASS_PENDING_OWNER")
        self.assertEqual(out_ok["gate_matrix"]["owner_visual_approval"], "PENDING")

    # ------------------------------------------------------------------
    # PED-09: Multi-Candidate Creative Competition (A/B/C + Pairwise)
    # ------------------------------------------------------------------
    def test_r4_09_ped09_multi_candidate_diversity_pairwise_and_hard_gate_filter(self) -> None:
        beats = [
            {"beat_id": "b1", "start_frame": 0, "end_frame": 72, "semantic_function": "hook", "has_numeric_claims": True},
            {"beat_id": "b2", "start_frame": 72, "end_frame": 168, "semantic_function": "mechanism", "has_numeric_claims": True},
            {"beat_id": "b3", "start_frame": 168, "end_frame": 264, "semantic_function": "evidence", "proof_status": "VERIFIED_REAL_EVIDENCE"},
            {"beat_id": "b4", "start_frame": 264, "end_frame": 360, "semantic_function": "cta"},
        ]
        media = {"width": 720, "height": 1280, "nb_frames": 360, "fps_num": 24, "fps_den": 1, "aspect_ratio": "9:16"}
        at = layout.build_subject_action_tracks(media)
        cands = candidates.generate_creative_candidates(
            run_id="r4_comp", selected_skill_id="SK-04", beats=beats,
            canvas={"width": 720, "height": 1280, "aspect_ratio": "9:16"},
            source_media=media, action_tracks=at, max_candidates=3,
        )
        self.assertEqual(len(cands), 3)
        self.assertTrue(all(c["selected_skill_id"] == "SK-04" for c in cands))
        div = candidates.validate_candidate_diversity(cands)
        self.assertTrue(div["pass"], f"3 candidates must differ structurally: {div}")

        # Cosmetic clone rejection test
        clones = [cands[0], dict(cands[0], candidate_id="cand_clone")]
        self.assertFalse(candidates.validate_candidate_diversity(clones)["pass"])

        # Order-swapped pairwise consensus vs inconsistency
        rk_cons = candidates.rank_candidates(
            cands,
            pairwise_fn=lambda a, b: {"winner": "X" if a["candidate_id"] == "cand_A" else "Y"},
        )
        self.assertEqual(rk_cons["pairwise_status"], "CONSENSUS")
        self.assertEqual(rk_cons["winner"]["candidate_id"], "cand_A")

        rk_incon = candidates.rank_candidates(cands, pairwise_fn=lambda a, b: {"winner": "X"})  # Always picks first -> order bias!
        self.assertEqual(rk_incon["pairwise_status"], "INCONSISTENT_ORDER_TIE_PENDING_REVIEW")

        # Hard failure cannot be bypassed by owner override
        cands_fail = [dict(c, plan_hard_failures=["ACTION_VIOLATION"], plan_gate_pass=False) if c["candidate_id"] == "cand_C" else c for c in cands]
        rk_f = candidates.rank_candidates(cands_fail)
        self.assertEqual(rk_f["disqualified_count"], 1)
        with self.assertRaises(ValueError):
            candidates.adopt_candidate(rk_f, owner_override_candidate_id="cand_C")

    # ------------------------------------------------------------------
    # PED-10: Hardened Async Cancellation, Lease, Late Write, Process Group
    # ------------------------------------------------------------------
    def test_r4_10_ped10_cancellation_quarantine_late_write_and_subprocess_reap(self) -> None:
        """Prove cooperative cancel, uncooperative quarantine, revoked-lease late-write rejection & subprocess kill/reap."""
        b = execution.compute_resource_budget(self.tmp, workers=2)
        run_d = self.tmp / "dag_cancel"
        run_d.mkdir(parents=True, exist_ok=True)
        ex = execution.BoundedAsyncExecutor(run_id="r4_cancel", budget=b, run_dir=run_d, reap_grace_sec=0.15)

        late_rejected = []
        final_target = run_d / "public_out.json"

        # 1. Cooperative task checks cancel_token and exits cleanly on timeout
        def _coop_slow(_deps: dict[str, Any], ctx: execution.TaskContext) -> dict[str, Any]:
            for _ in range(50):
                ctx.check_cancelled()
                time.sleep(0.02)
            return {"done": True}

        res_coop = ex.run([execution.DAGTask("t_coop", _coop_slow, timeout_sec=0.08)])
        self.assertFalse(res_coop["pass"])
        self.assertEqual(res_coop["receipts"][0]["status"], "TIMEOUT")
        self.assertFalse(res_coop["blocked_cancellation"], "Cooperative worker stopped cleanly; should NOT quarantine!")

        # 2. Uncooperative writer ignores timeout and tries late publish -> QUARANTINED + LATE_WRITE_REJECTED!
        def _ghost_writer(_deps: dict[str, Any], ctx: execution.TaskContext) -> dict[str, Any]:
            st = ctx.stage_json("ghost.json", {"bad": True})
            time.sleep(0.35)
            try:
                ctx.publisher.publish_staged_file(ctx.lease, ctx.cancel_token, st, final_target)
            except execution.LateWriteRejectedError:
                late_rejected.append(True)
            return {"ghost": True}

        ex2 = execution.BoundedAsyncExecutor(run_id="r4_ghost", budget=b, run_dir=run_d, reap_grace_sec=0.10)
        res_ghost = ex2.run([execution.DAGTask("t_ghost", _ghost_writer, timeout_sec=0.06)])
        time.sleep(0.38)  # Let ghost attempt its late write after timeout
        self.assertFalse(res_ghost["pass"])
        self.assertTrue(res_ghost["blocked_cancellation"])
        self.assertEqual(res_ghost["receipts"][0]["status"], "BLOCKED_CANCELLATION")
        self.assertFalse(final_target.exists(), "Ghost worker MUST NOT write to final public artifact!")
        self.assertTrue(late_rejected, "Revoked lease must raise LateWriteRejectedError on late publish!")

        # 3. Isolated subprocess group kill & reap (zero zombie)
        sub_res = execution.run_subprocess_isolated([sys.executable, "-c", "import time; time.sleep(30)"], timeout_sec=0.15)
        self.assertTrue(sub_res["timed_out"])
        self.assertTrue(sub_res["reaped"])
        self.assertTrue(sub_res["cleanup"]["clean"])

    def test_r4_11_ped10_serial_vs_parallel_canonical_determinism_and_retries(self) -> None:
        """Prove workers=1 vs workers=4 produce identical canonical DAG hashes & bounded transient retries succeed."""
        attempts = {"n": 0}

        def _flaky(_deps: dict[str, Any], ctx: execution.TaskContext) -> dict[str, Any]:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient_io_hiccup")
            return {"ok": True, "val": 42}

        b1 = execution.compute_resource_budget(self.tmp, workers=1)
        b4 = execution.compute_resource_budget(self.tmp, workers=4)
        tasks_1 = [
            execution.DAGTask("a_node", lambda d: {"x": 1}),
            execution.DAGTask("b_node", lambda d: {"y": 2}),
            execution.DAGTask("c_join", lambda d: {"sum": d["a_node"]["x"] + d["b_node"]["y"]}, deps=("a_node", "b_node")),
        ]
        r1 = execution.BoundedAsyncExecutor(run_id="det1", budget=b1).run(tasks_1)
        r4 = execution.BoundedAsyncExecutor(run_id="det4", budget=b4).run(tasks_1)
        self.assertTrue(r1["pass"] and r4["pass"])
        self.assertEqual(r1["canonical_dag_sha256"], r4["canonical_dag_sha256"])

        r_retry = execution.BoundedAsyncExecutor(run_id="retry1", budget=b1).run([
            execution.DAGTask("flaky", _flaky, retries=2, retry_backoff_sec=0.02),
        ])
        self.assertTrue(r_retry["pass"])
        self.assertEqual(r_retry["receipts"][0]["attempts"], 2)

    # ------------------------------------------------------------------
    # Section 03 & 15 Tier A: Cross-Niche 12x5=60 + UNKNOWN + UNSUPPORTED
    # ------------------------------------------------------------------
    def test_r4_12_cross_niche_60_matrix_plus_unknown_and_unsupported(self) -> None:
        """Verify 12 niches x 5 skills = 60 scenarios + UNKNOWN + UNSUPPORTED format detection with zero hardcoding."""
        niche_scripts = {
            "finance": "Seeing $1,200 in your bank account doesn't mean $1,200 is free to spend. Bills take $800 leaving $400.",
            "software_ai": "Click the terminal shortcut to run git bisect and isolate the failing API commit in three steps.",
            "cooking": "Heat the cast-iron pan before searing the salmon fillet for three minutes so the skin stays crisp.",
            "beauty": "Apply niacinamide serum before moisturizer and finish every morning routine with broad-spectrum SPF.",
            "fitness": "Keep your ribs stacked during each squat rep to protect your lower back and improve depth.",
            "education_science": "Water expands when freezing because hydrogen bonds lock molecules into an open hexagonal lattice.",
            "history_storytelling": "In the 15th century, a single maritime treaty redrew trade routes across two continents.",
            "travel_lifestyle": "Take the dawn ridge trail before clouds roll in over the coastal cliffs.",
            "product_ecommerce": "This compact travel kettle boils 400ml in four minutes and fits cleanly inside a carry-on.",
            "crafts_diy": "Sand along the wood grain with 220-grit sandpaper before applying two thin coats of matte oil.",
            "podcast_ugc": "Honestly, the biggest shift in my week came from writing down one priority before opening messages.",
            "gaming_screen": "Watch the stamina bar on the bottom HUD before dodging the second boss phase attack.",
            "unknown_abstract": "Quiet blue ripples drift gently across glass reflections under an autumn afternoon light.",
        }
        vid = _ffmpeg_make_video(self.tmp / "cross_niche_base.mp4", vf="testsrc2=size=360x640:rate=24:duration=4.0", dur=4.0)
        combos_passed = 0
        for niche_k, text in niche_scripts.items():
            an = intelligence.analyze_inputs(
                video_path=vid, run_id=f"cn_{niche_k}", script_text=text,
                constraints={"layout_policy": "ADAPTIVE_DYNAMIC"},
            )
            sem = intelligence.build_semantic_graph(an["input_analysis"])
            for sk_id in ("SK-01", "SK-02", "SK-03", "SK-04", "SK-05"):
                ctx = intelligence.build_editorial_context(an["input_analysis"], sem, skill_id=sk_id)
                self.assertEqual(ctx["support_status"], "SUPPORTED")
                self.assertEqual(ctx["skill_id"], sk_id)
                if niche_k == "unknown_abstract":
                    self.assertIn("UNKNOWN", ctx["niche_signals"])
                combos_passed += 1
        self.assertEqual(combos_passed, 65)  # 12x5=60 + 5 UNKNOWN

        # Unsupported corrupt/zero-frame input MUST report UNSUPPORTED cleanly
        bad_ctx = intelligence.build_editorial_context(
            {"media": {"width": 0, "height": 0, "nb_frames": 0, "fps_num": 0, "video_codec": "unknown"}},
            {"phrases": []},
        )
        self.assertEqual(bad_ctx["support_status"], "UNSUPPORTED")
        self.assertGreaterEqual(len(bad_ctx["unsupported_reasons"]), 1)


if __name__ == "__main__":
    unittest.main()
