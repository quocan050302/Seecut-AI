"""KH-02.R1 Executable Tests R1-20..R1-28: Effect Registry, Real Chromium Gallery, Camera/Seam/Typo, Holdouts & Determinism."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.kh02.camera import build_camera_path, validate_camera_continuity
from scripts.kh02.contracts import hash_artifact, validate_references
from scripts.kh02.effects_gallery import run_effect_gallery
from scripts.kh02.motion import emit_effect_event, sample_effect_pose
from scripts.kh02.planner import build_editor_brief, resolve_editorial_plan, resolve_pacing_budget
from scripts.kh02.qa import decide_adoption
from scripts.kh02.registry import (
    audit_effect_variety,
    build_registry,
    choose_capabilities_for_beat,
    mark_gallery_verified,
    validate_effect_params,
)
from scripts.kh02.run import execute_pipeline
from scripts.kh02.transition import plan_transition_seam, solve_anchor_handoff, validate_continuity
from scripts.kh02.typography import measure_layout_mobile, plan_text_events, validate_readability


class TestR1EffectsAndSystem(unittest.TestCase):
    def test_r1_20_effect_registry_availability_and_variety_audit(self) -> None:
        """R1-20: unsupported capability rejected/fallback; shipped R1 families validated & variety audited."""
        reg = build_registry()
        ok_3d, errs_3d = validate_effect_params(reg, "experimental.true_3d_camera")
        self.assertFalse(ok_3d)
        self.assertTrue(errs_3d)

        # Beat capability selector varies capabilities cleanly instead of repeating one template
        b1 = {"beat_id": "b1", "start_frame": 0, "end_frame": 48, "semantic_function": "hook"}
        b2 = {"beat_id": "b2", "start_frame": 48, "end_frame": 96, "semantic_function": "evidence"}
        b3 = {"beat_id": "b3", "start_frame": 96, "end_frame": 144, "semantic_function": "payoff"}
        c1 = choose_capabilities_for_beat(reg, b1)
        c2 = choose_capabilities_for_beat(reg, b2, previously_used=[c1["selected_id"]])
        c3 = choose_capabilities_for_beat(reg, b3, previously_used=[c1["selected_id"], c2["selected_id"]])
        self.assertEqual(len({c1["selected_id"], c2["selected_id"], c3["selected_id"]}), 3)

        evs = [
            emit_effect_event(event_id="e1", beat_id="b1", capability_id=c1["selected_id"], kind="kinetic_phrase_mask", start_frame=0, end_frame=48, normalized_bbox=[0.06, 0.65, 0.88, 0.18]),
            emit_effect_event(event_id="e2", beat_id="b2", capability_id=c2["selected_id"], kind="clip_mask_wipe", start_frame=48, end_frame=96, normalized_bbox=[0.06, 0.30, 0.88, 0.40]),
            emit_effect_event(event_id="e3", beat_id="b3", capability_id=c3["selected_id"], kind="light_sweep_badge", start_frame=96, end_frame=144, normalized_bbox=[0.06, 0.65, 0.88, 0.18]),
        ]
        # Deterministic pose sampler
        self.assertEqual(sample_effect_pose(evs[1], 47)["visible"], 0.0)
        self.assertEqual(sample_effect_pose(evs[1], 60)["clip_progress"], 1.0)

        var = audit_effect_variety(evs, registry=reg)
        self.assertTrue(var["pass"])
        self.assertEqual(var["unique_capabilities"], 3)
        self.assertEqual(var["meaningful_visual_changes"], 3)

    def test_r1_21_real_browser_effect_gallery(self) -> None:
        """R1-21: real headless Chromium Effect Gallery proves direct/reverse/repeat/cold seek determinism & visible rendering."""
        with tempfile.TemporaryDirectory() as td:
            rep = run_effect_gallery(Path(td) / "gallery")
            self.assertTrue(rep["pass"], f"Chromium Effect Gallery failed: {rep}")
            self.assertEqual(rep["browser_errors"], [])
            self.assertGreaterEqual(len(rep["capabilities"]), 6)
            for cid, cinfo in rep["capabilities"].items():
                self.assertTrue(cinfo["pass"], f"Capability {cid} failed gallery: {cinfo}")
                self.assertEqual(cinfo["reverse_seek_diff"], 0.0)
                self.assertEqual(cinfo["repeat_seek_diff"], 0.0)
                self.assertEqual(cinfo["cold_restart_diff"], 0.0)
                self.assertGreaterEqual(cinfo["visible_region_delta_vs_t0"], 2.0)

            reg_v = mark_gallery_verified(build_registry(), rep["capabilities"])
            verified_ids = {c["capability_id"] for c in reg_v["capabilities"] if c["availability"] == "IMPLEMENTED_VERIFIED"}
            self.assertIn("r1.clip_mask_wipe", verified_ids)
            self.assertIn("r1.spotlight_focus", verified_ids)
            self.assertIn("r1.kinetic_phrase_mask", verified_ids)
            self.assertIn("r1.light_sweep_badge", verified_ids)
            self.assertIn("r1.freeze_callout", verified_ids)

    def test_r1_22_camera_rotation_bounds_and_hard_cut_exception(self) -> None:
        """R1-22: rotation-aware camera crop bounds, zero black overscan, face safety & hard-cut continuity exception."""
        beats = [
            {"beat_id": "b1", "start_frame": 0, "end_frame": 48, "semantic_function": "hook", "visual_mode": "person_full"},
            {"beat_id": "b2", "start_frame": 48, "end_frame": 96, "semantic_function": "payoff", "visual_mode": "person_full"},
        ]
        # 1920x1080 rotated 90deg -> effective 1080x1920 portrait coverage
        cams = build_camera_path(
            beats, mode="talking_head", source_width=1920, source_height=1080,
            output_width=1080, output_height=1920, rotation_deg=90, face_rect=[0.25, 0.18, 0.75, 0.58],
        )
        self.assertEqual(validate_camera_continuity(cams, face_rect=[0.25, 0.18, 0.75, 0.58]), [])
        # Intentional scale jump at cut frame passes ONLY when listed in intentional_cut_frames
        cams_jump = [dict(cams[0], scale_to=1.02, pos_to=[0.0, 0.0]), dict(cams[1], scale_from=1.14, scale_to=1.14, pos_from=[0.0, 0.0], pos_to=[0.0, 0.0])]
        self.assertTrue(validate_camera_continuity(cams_jump))
        self.assertEqual(validate_camera_continuity(cams_jump, intentional_cut_frames={48}), [])

    def test_r1_23_transition_anchor_handoff_orphan_layer_and_audio_off_seam(self) -> None:
        """R1-23: anchor handoff continuity, reject orphan layer & audio cue far off transition seam."""
        b1 = {"beat_id": "b1", "start_frame": 0, "end_frame": 48, "semantic_function": "hook", "continuity_out": "editorial_spine", "visual_mode": "a"}
        b2 = {"beat_id": "b2", "start_frame": 48, "end_frame": 96, "semantic_function": "evidence", "continuity_in": "editorial_spine", "visual_mode": "b"}
        ho = solve_anchor_handoff(b1, b2, from_pose={"x": 0.2, "y": 0.5}, to_pose={"x": 0.8, "y": 0.5})
        self.assertTrue(ho["has_shared_anchor"])
        self.assertEqual(ho["direction"], "horizontal_slide")

        tr = plan_transition_seam(b1, b2, fps=24.0, cue_id="sfx_tr_01")
        bad_sb = {
            "profile": "editorial_pro",
            "beats": [b1, b2],
            "transitions": [tr],
            "visual_events": [
                {"event_id": "orphan_1", "beat_id": "b1", "start_frame": 60, "end_frame": 80, "params": {"kind": "card"}},
            ],
            "audio_cues": [
                {"cue_id": "sfx_tr_01", "frame_start": 80, "frame_end": 90},  # 32 frames off seam!
            ],
        }
        issues = validate_continuity(bad_sb, fps=24.0)
        codes = {i["code"] for i in issues}
        self.assertIn("ORPHAN_LAYER_RANGE", codes)
        self.assertIn("SEAM_AUDIO_OFF_SEAM", codes)

    def test_r1_24_typography_mobile_landscape_vi_cjk_and_caption_zorder(self) -> None:
        """R1-24: mobile 360px & landscape layout readability for EN/VI/CJK, safe zone & caption top-layer z-order."""
        vi_lay = measure_layout_mobile("Chỉnh sửa video chuyên nghiệp 2026", font_size_px=34, normalized_box=[0.06, 0.65, 0.88, 0.22], canvas_width=720, canvas_height=1280, language="vi")
        self.assertTrue(vi_lay["has_vietnamese_diacritics"])
        self.assertTrue(vi_lay["mobile_readable"])
        self.assertFalse(vi_lay["overflow"])

        cjk_lay = measure_layout_mobile("专业自动剪辑流程验证", font_size_px=42, normalized_box=[0.06, 0.65, 0.88, 0.22], canvas_width=1280, canvas_height=720, language="zh")
        self.assertTrue(cjk_lay["has_cjk"])
        self.assertTrue(cjk_lay["mobile_readable"])

        # Caption beneath visual overlay z-order flagged
        bad_cap = [{
            "text_id": "cap_bad", "beat_id": "b1", "text": "Hello", "word_ids": [], "role": "caption",
            "hierarchy": 4, "style": {"font_size_px": 30, "z_order": 50}, "font_ref": "system-sans-bold",
            "start_frame": 0, "end_frame": 24, "minimum_read_frames": 12, "position": [0.08, 0.78, 0.84, 0.10],
            "caption_policy": "ON_REQUESTED", "contrast_intent": "high",
        }]
        errs = validate_readability(bad_cap, caption_policy="ON_REQUESTED", max_visual_z_order=100)
        self.assertTrue(any(e["code"] == "TYPO_CAPTION_NOT_TOP_LAYER" for e in errs))

    def test_r1_25_adoption_gate_rejects_tie_invalid_and_failed_h2(self) -> None:
        """R1-25: tie/invalid pairwise or failed H1/H2 cannot adopt candidate."""
        self.assertFalse(decide_adoption(h1_pass=True, h2_pass=True, objective_qa_pass=True, pairwise_consensus="tie")["adopted"])
        self.assertFalse(decide_adoption(h1_pass=True, h2_pass=True, objective_qa_pass=True, pairwise_consensus="invalid")["adopted"])
        self.assertFalse(decide_adoption(h1_pass=True, h2_pass=False, objective_qa_pass=True, pairwise_consensus="A")["adopted"])
        self.assertTrue(decide_adoption(h1_pass=True, h2_pass=True, objective_qa_pass=True, pairwise_consensus="A")["adopted"])

    def test_r1_26_olivia_contract_forbids_analysis_only_sc01_as_raw_source(self) -> None:
        """R1-26: referencing ANALYSIS_ONLY flattened SC-01 MP4 in SHOTBOOK V2 fails validate_references()."""
        inv = {
            "schema_version": "kh02.source_inventory.v1",
            "run_id": "olivia_neg",
            "assets": [
                {"asset_id": "olivia_raw", "canonical_path": "/tmp/raw.mp4", "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL", "usable_in_render": True, "sha256": "a" * 64, "media": {"width": 1080, "height": 1920, "fps_num": 24, "fps_den": 1, "nb_frames": 476, "duration_sec": 19.83, "has_audio": True}},
                {"asset_id": "sc01_flattened", "canonical_path": "/tmp/sc01.mp4", "kind": "analysis_only", "rights": "ANALYSIS_ONLY", "usable_in_render": False, "sha256": "b" * 64, "media": {"width": 1080, "height": 1920, "fps_num": 24, "fps_den": 1, "nb_frames": 476, "duration_sec": 19.83, "has_audio": True}},
            ],
        }
        brief = build_editor_brief({
            "brief_id": "olivia_neg", "mode": "talking_head", "profile": "editorial_pro",
            "objective": "Test raw isolation", "audience": "Creators", "platform": "reels",
            "aspect_ratio": "9:16", "language": "en", "tone": "warm", "key_message": "Real storefront",
            "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
            "sources": [{"asset_id": "olivia_raw", "path": "raw.mp4", "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
        })
        sb = resolve_editorial_plan(brief, inv, run_id="olivia_neg")
        # Inject ANALYSIS_ONLY asset into beat -> must be rejected!
        sb["beats"][0]["content_refs"].append("sc01_flattened")
        errs = validate_references(sb, inventory=inv)
        self.assertTrue(any(e["code"] == "RIGHTS_DENIED_ASSET" for e in errs))

    def test_r1_27_multi_niche_holdout_and_long_timeline(self) -> None:
        """R1-27: 5 unseen niche holdouts + 2-minute long-form timeline produce distinct valid plans without hardcode."""
        niches = [
            ("interview", "minimal_documentary", "16:9", 1440),   # 60s calm interview
            ("product_demo", "editorial_pro", "9:16", 432),       # 18s fast product ad
            ("tutorial", "editorial_pro", "16:9", 1200),          # 50s software tutorial
            ("education", "editorial_pro", "16:9", 960),          # 40s education explainer
            ("documentary", "minimal_documentary", "16:9", 2880), # 120s long-form documentary stress
        ]
        themes = set()
        energies = set()
        for mode, prof, aspect, frames in niches:
            w, h = (1920, 1080) if aspect == "16:9" else (1080, 1920)
            inv = {
                "schema_version": "kh02.source_inventory.v1",
                "run_id": f"holdout_{mode}",
                "assets": [{
                    "asset_id": "src_01", "canonical_path": "/tmp/src.mp4", "kind": "a_roll",
                    "rights": "OWNER_SUPPLIED_NONCOMMERCIAL", "usable_in_render": True, "sha256": "f" * 64,
                    "media": {"width": w, "height": h, "fps_num": 24, "fps_den": 1, "nb_frames": frames, "duration_sec": frames / 24.0, "has_audio": True},
                }],
            }
            brief = build_editor_brief({
                "brief_id": f"ho_{mode}", "mode": mode, "profile": prof,
                "objective": f"Unseen holdout for {mode}", "audience": "Viewers",
                "platform": "youtube" if aspect == "16:9" else "reels",
                "aspect_ratio": aspect, "language": "en", "tone": "measured",
                "key_message": f"Holdout key message for {mode}",
                "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
                "sources": [{"asset_id": "src_01", "path": "src.mp4", "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
            })
            sb = resolve_editorial_plan(brief, inv, run_id=f"ho_{mode}")
            self.assertEqual(sb["output_spec"]["total_frames"], frames)
            themes.add(sb["story_arc"]["theme"])
            pb = resolve_pacing_budget(brief, total_duration_sec=frames / 24.0)
            energies.add(pb["energy_mode"])
            if frames >= 2880:
                self.assertTrue(pb["chaptered_arcs"])
        self.assertGreaterEqual(len(themes), 3)
        self.assertGreaterEqual(len(energies), 3)

    def test_r1_28_cli_dry_run_offline_workers_1_vs_2_determinism(self) -> None:
        """R1-28: CLI --dry-run --offline --cut-policy produces identical shotbook SHA across workers=1 and workers=2 with 0 downloads."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "clip.mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=navy:s=360x640:r=24:d=3",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)],
                check=True, capture_output=True,
            )
            brief_path = root / "brief.json"
            brief_path.write_text(json.dumps({
                "schema_version": "kh02.brief.v1",
                "brief_id": "det_cli_r1",
                "mode": "talking_head",
                "profile": "editorial_pro",
                "objective": "Verify R1 CLI determinism",
                "audience": "QA",
                "platform": "reels",
                "aspect_ratio": "9:16",
                "language": "en",
                "tone": "clear",
                "key_message": "Deterministic offline dry-run",
                "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
                "sources": [{"asset_id": "main", "path": str(src), "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
            }), encoding="utf-8")

            r1 = execute_pipeline(brief_path=brief_path, run_root=root / "run_same", mode="plan", dry_run=True, workers=1, cut_policy_override="PRESERVE_SCRIPT_TRIM", offline=True)
            r2 = execute_pipeline(brief_path=brief_path, run_root=root / "run_same", mode="plan", dry_run=True, workers=2, cut_policy_override="PRESERVE_SCRIPT_TRIM", offline=True)
            self.assertEqual(r1["status"], "DRY_RUN_PASS")
            self.assertEqual(r2["status"], "DRY_RUN_PASS")
            self.assertEqual(r1["shotbook_sha256"], r2["shotbook_sha256"])
            self.assertEqual(r1["sfx_downloads_performed"], 0)
            # Dry-run must NOT create run_same directory on disk!
            self.assertFalse((root / "run_same").exists())


if __name__ == "__main__":
    unittest.main()
