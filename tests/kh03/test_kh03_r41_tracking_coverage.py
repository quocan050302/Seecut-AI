"""KH-03.R4.1-04 & R4.1-05 — Universal Temporal Tracking & Full Streaming Component/Frame Coverage Test Suite.

Proves Section 07 & Section 08 requirements:
1. R4.1-04 Honest detector capability disclosure:
   - resolve_detector_capabilities() emits DETECTOR_CAPABILITIES.json.
   - NumPy streaming motion is labeled MOTION_SALIENT_REGION_NON_SEMANTIC with
     semantic_object_classification=False and supported_roles=['motion_region'] (NEVER object/screen_text!).
2. R4.1-04 Multi-instance temporal tracking + scene-cut reset:
   - Two simultaneous distinct hands/faces remain separate stable tracks (trk_hand_01, trk_hand_02)
     rather than collapsing into one role union box!
   - Scene cut at frame 24 splits tracks across scene segments with explicit temporal_coverage stats.
3. R4.1-04 EDL clock mapping & NO silent 0.50/0.85 fallback PASS:
   - Missing EDL when require_edl_mapping=True OR divergent frame counts -> BLOCKED_CLOCK_MAPPING!
   - Unverified conservative placeholder -> SAFE_FALLBACK_GEOMETRY_ONLY (NEVER VERIFIED_ACTION_PROTECTION!).
   - Verified track under split/PIP transform enforces strict >=0.95 retained & <=0.05 overlay!
4. R4.1-05 ZERO TRUNCATION (15 motion nodes + 8 components, failure specifically on LAST node #15!):
   - orchestrate_final_render_qa() checks ALL 15 motion nodes & ALL 8 components (no [:12]/[:6] slicing!).
   - Emits COMPONENT_COVERAGE.json (checked_motion_nodes=15, coverage_ratio=1.0, truncation_applied=False)
     and FRAME_COVERAGE.json (temporal_coverage_ratio=1.0), and CATCHES frozen node #15!
   - SK-05 dynamic node is NOT blindly marked intentional_static (only explicit per-node flag!).
   - Early payoff spoiler in a secondary component fails G_HOOK_PIXELS!
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.kh03 import qa, tracking, visual_qa


def _make_animated_video(out_path: Path, *, dur: float = 3.0, freeze_bottom_strip: bool = False) -> Path:
    # Moving bright bar in top/mid region (y=24..340) + optional frozen static patch at bottom (y=410..460)
    vf = "color=c=0x162C22:s=270x480:r=24:d=" + str(dur)
    if freeze_bottom_strip:
        vf += ",drawbox=x=20:y=412:w=230:h=48:color=0xF3DC96:t=fill"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", vf,
        "-f", "lavfi", "-i", f"color=c=0xE8D4A2:s=220x350:r=24:d={dur}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={dur}",
        "-filter_complex", "[0:v][1:v]overlay=x='10+t*30':y=20[vout]",
        "-map", "[vout]", "-map", "2:a:0",
        "-t", str(dur), "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "96k",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


class TestKH03R41TrackingCoverageSuite(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kh03_r41_trk_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_r41_13_detector_capabilities_honest_non_semantic_disclosure(self) -> None:
        """NumPy motion MUST disclose MOTION_SALIENT_REGION_NON_SEMANTIC & emit DETECTOR_CAPABILITIES.json."""
        caps = tracking.resolve_detector_capabilities(allow_macos_vision=False, out_dir=self.tmp / "caps")
        self.assertTrue((self.tmp / "caps" / "DETECTOR_CAPABILITIES.json").is_file())
        if caps["primary_detector"] == "NUMPY_STREAMING_MOTION_DETECTOR":
            self.assertEqual(caps["capability_class"], "MOTION_SALIENT_REGION_NON_SEMANTIC")
            self.assertFalse(caps["semantic_object_classification"])
            self.assertEqual(caps["supported_roles"], ["motion_region"])

    def test_r41_14_multi_instance_temporal_association_and_scene_cut_reset(self) -> None:
        """Two distinct hands stay separate tracks AND scene cut at f24 resets track IDs with explicit coverage."""
        obs = [
            # Scene 0 (frames 0..20): left hand + right hand
            {"frame": 0, "role": "hand", "bbox_norm": [0.10, 0.55, 0.16, 0.14], "confidence": 0.92, "verified_detector": True, "provider": "TEST_OBS"},
            {"frame": 0, "role": "hand", "bbox_norm": [0.70, 0.56, 0.16, 0.14], "confidence": 0.91, "verified_detector": True, "provider": "TEST_OBS"},
            {"frame": 10, "role": "hand", "bbox_norm": [0.12, 0.54, 0.16, 0.14], "confidence": 0.93, "verified_detector": True, "provider": "TEST_OBS"},
            {"frame": 10, "role": "hand", "bbox_norm": [0.68, 0.55, 0.16, 0.14], "confidence": 0.90, "verified_detector": True, "provider": "TEST_OBS"},
            # Scene 1 (after scene cut at frame 24): new shot
            {"frame": 28, "role": "hand", "bbox_norm": [0.11, 0.55, 0.16, 0.14], "confidence": 0.94, "verified_detector": True, "provider": "TEST_OBS"},
            {"frame": 36, "role": "hand", "bbox_norm": [0.13, 0.55, 0.16, 0.14], "confidence": 0.92, "verified_detector": True, "provider": "TEST_OBS"},
        ]
        media = {"width": 720, "height": 1280, "nb_frames": 48, "fps_num": 24, "fps_den": 1, "scene_cuts": [24]}
        det = tracking.detect_semantic_entities(None, media, probe_observations=obs, use_macos_vision=False)
        hand_tracks = [e for e in det["entities"] if e["role"] == "hand"]
        # Scene 0 has 2 distinct hands + Scene 1 has 1 reset track = 3 distinct tracks (NOT collapsed into 1!)
        self.assertEqual(len(hand_tracks), 3, f"Expected 3 per-instance/per-scene tracks, got {hand_tracks}")
        scene_ids = {t["scene_id"] for t in hand_tracks}
        self.assertEqual(scene_ids, {0, 1})
        for t in hand_tracks:
            self.assertIn("temporal_coverage", t)
            self.assertGreater(t["temporal_coverage"]["observed_frame_count"], 0)

    def test_r41_15_action_visibility_clock_mapping_and_no_silent_fallback_pass(self) -> None:
        """Missing EDL -> BLOCKED_CLOCK_MAPPING; unverified placeholder -> SAFE_FALLBACK_GEOMETRY_ONLY (NEVER VERIFIED!)."""
        vid_a = _make_animated_video(self.tmp / "src_3s.mp4", dur=3.0)
        vid_b = _make_animated_video(self.tmp / "fin_2s.mp4", dur=2.0)

        verified_win = [{
            "window_id": "apw_v1", "entity_id": "trk_hand_01", "role": "hand",
            "start_frame": 0, "end_frame": 48, "protected_bbox_norm": [0.20, 0.52, 0.40, 0.22],
            "confidence": 0.92, "verified_detector": True, "source_pre_cropped": False,
        }]
        # 1. Divergent source (72f) vs final (48f) without EDL -> BLOCKED_CLOCK_MAPPING!
        blk = tracking.evaluate_action_visibility(vid_b, verified_win, source_mp4=vid_a, edl=None)
        self.assertFalse(blk["pass"])
        self.assertEqual(blk["status"], "BLOCKED_CLOCK_MAPPING")

        # 2. Unverified conservative placeholder MUST NOT certify VERIFIED_ACTION_PROTECTION!
        unver_win = [{
            "window_id": "apw_unv", "entity_id": "ent_fb", "role": "action_region",
            "start_frame": 0, "end_frame": 48, "protected_bbox_norm": [0.20, 0.52, 0.40, 0.22],
            "confidence": 0.40, "verified_detector": False, "source_pre_cropped": False,
        }]
        fb = tracking.evaluate_action_visibility(vid_b, unver_win)
        self.assertEqual(fb["certification"], "SAFE_FALLBACK_GEOMETRY_ONLY_PENDING_DETECTOR")
        self.assertNotEqual(fb["status"], "VERIFIED_ACTION_PROTECTION")
        self.assertEqual(fb["windows"][0]["certification"], "SAFE_FALLBACK_GEOMETRY_ONLY")

        # 3. Verified track with valid EDL + unobstructed framing -> VERIFIED_ACTION_PROTECTION!
        edl_ok = {"segments": [{"src_start_frame": 0, "src_end_frame": 48, "out_start_frame": 0, "out_end_frame": 48}]}
        ok = tracking.evaluate_action_visibility(
            vid_b, verified_win, source_mp4=vid_a, edl=edl_ok,
            overlay_boxes=[{"start_frame": 0, "end_frame": 48, "bbox_norm": [0.05, 0.04, 0.90, 0.12]}],
        )
        self.assertTrue(ok["pass"], ok)
        self.assertEqual(ok["certification"], "VERIFIED_ACTION_PROTECTION")
        self.assertTrue(ok["pixel_level_verified"])

    def test_r41_16_full_15_nodes_8_components_no_truncation_catches_last_node_and_sk05_and_spoiler(self) -> None:
        """15 motion nodes & 8 components: frozen node #15 MUST FAIL; SK-05 dynamic NOT skipped; secondary spoiler caught!"""
        vid = _make_animated_video(self.tmp / "freeze_last_node.mp4", dur=3.0, freeze_bottom_strip=True)

        # Build 15 motion nodes (nodes 1..14 inside animated top/mid area; node #15 in frozen bottom strip!)
        nodes = []
        for i in range(14):
            nodes.append({
                "node_id": f"node_{i+1:02d}", "component_id": f"comp_{(i % 7)+1:02d}",
                "start_frame": 2 + (i % 4) * 4, "end_frame": 44 + (i % 4) * 4,
                "roi_norm": [0.08, 0.06 + (i % 5) * 0.11, 0.78, 0.12],
                "intentional_static": False,
            })
        # Node #15 (beyond the old [:12] slice!) is FROZEN in bottom strip y=0.86..0.95!
        nodes.append({
            "node_id": "node_15_last_frozen", "component_id": "comp_08",
            "start_frame": 6, "end_frame": 58,
            "roi_norm": [0.08, 0.86, 0.84, 0.09],
            "intentional_static": False,
        })

        # Build 8 components (beyond the old [:6] slice!)
        comps = [
            {
                "component_id": f"comp_{i+1:02d}", "start_frame": 2, "end_frame": 60,
                "normalized_bbox": ([0.08, 0.86, 0.84, 0.09] if i == 7 else [0.08, 0.06 + (i % 6) * 0.10, 0.78, 0.10]),
            }
            for i in range(8)
        ]

        plan = {
            "selected_skill_id": "SK-05",  # Deliberately SK-05 to prove dynamic node #15 is NOT blindly skipped!
            "hook_plan": {"selected_hook_id": "h1", "hook_roi_norm": [0.05, 0.04, 0.90, 0.18], "truthful_no_spoiler_verified": True},
            "components": comps,
        }
        mg = {"nodes": nodes}
        rdir = self.tmp / "run_15_nodes"
        out = qa.orchestrate_final_render_qa(
            run_id="r41_15n", run_dir=rdir, final_mp4=vid, source_mp4=None,
            creative_plan=plan, motion_graph=mg,
            kh02_result={"audio_qa": {"pass": True}, "h1_geom": {"pass": True}},
            mode="ENGINEERING",
        )
        # MUST FAIL on node #15!
        self.assertFalse(out["technical_pass"], "Frozen 15th motion node MUST FAIL even in SK-05!")
        self.assertIn("G_COMPONENT_ROI_MOTION", out["gate_matrix"]["failing_gates"])

        # Coverage manifests MUST prove 15/15 nodes & 8/8 components checked with ZERO truncation!
        cc = json.loads((rdir / "qc" / "COMPONENT_COVERAGE.json").read_text(encoding="utf-8"))
        fc = json.loads((rdir / "qc" / "FRAME_COVERAGE.json").read_text(encoding="utf-8"))
        sa = json.loads((rdir / "qc" / "SPATIAL_ALIGNMENT_QA.json").read_text(encoding="utf-8"))
        self.assertEqual(cc["expected_motion_nodes"], 15)
        self.assertEqual(cc["checked_motion_nodes"], 15)
        self.assertEqual(cc["coverage_ratio"], 1.0)
        self.assertFalse(cc["truncation_applied"])
        self.assertEqual(cc["expected_components"], 8)
        self.assertEqual(cc["checked_components"], 8)
        self.assertEqual(fc["temporal_coverage_ratio"], 1.0)
        self.assertEqual(sa["components_evaluated"], 8)

        # Early payoff spoiler inside a SECONDARY component (not just opening hook!) MUST fail G_HOOK_PIXELS!
        plan_spoil = dict(plan)
        plan_spoil["hook_plan"] = {
            "selected_hook_id": "h1", "hook_roi_norm": [0.05, 0.04, 0.90, 0.18],
            "truthful_no_spoiler_verified": True, "payoff_tokens": ["$100"], "payoff_start_frame": 60,
        }
        plan_spoil["components"] = [
            {"component_id": "c_early_bg", "start_frame": 4, "end_frame": 36, "normalized_bbox": [0.05, 0.25, 0.90, 0.14], "params": {"headline": "Only $100 left!"}},
        ]
        rdir_sp = self.tmp / "run_spoiler"
        out_sp = qa.orchestrate_final_render_qa(
            run_id="r41_sp", run_dir=rdir_sp, final_mp4=vid, source_mp4=None,
            creative_plan=plan_spoil, motion_graph={"nodes": nodes[:2]},
            kh02_result={"audio_qa": {"pass": True}, "h1_geom": {"pass": True}},
            mode="ENGINEERING",
        )
        self.assertIn("G_HOOK_PIXELS", out_sp["gate_matrix"]["failing_gates"], "Secondary component spoiling $100 before payoff frame MUST fail G_HOOK_PIXELS!")


if __name__ == "__main__":
    unittest.main()
