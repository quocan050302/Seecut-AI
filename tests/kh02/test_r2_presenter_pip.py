"""KH-02.R2 Regression Tests — Groups R2-07..R2-14 (Presenter Policy, Real Fixed-PIP Render, Collisions, Effects & Seams)."""

from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from scripts.kh02 import camera, compile_plan, contracts, planner, qa, registry, run as kh_run, typography


def _make_moving_presenter_clip(out_mp4: Path, *, width: int = 360, height: int = 640, fps: int = 24, duration_sec: float = 2.0) -> Path:
    """Create a deterministic TEST_ONLY clip with active visual motion & voice tone for real Chromium PIP render tests."""
    wav_p = out_mp4.with_suffix(".wav")
    sr = 48000
    n = int(round(duration_sec * sr))
    buf = bytearray(n * 2)
    for i in range(n):
        t = i / float(sr)
        struct.pack_into("<h", buf, i * 2, int(9000 * math.sin(2.0 * math.pi * 220.0 * t)))
    with wave.open(str(wav_p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}:duration={duration_sec}",
        "-i", str(wav_p),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(sr),
        "-shortest", str(out_mp4),
    ], check=True, capture_output=True, timeout=60)
    wav_p.unlink(missing_ok=True)
    return out_mp4


def _make_real_png(path: Path, width: int = 480, height: int = 320) -> Path:
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=1:duration=1",
        "-frames:v", "1", str(path),
    ], check=True, capture_output=True, timeout=30)
    return path


class TestR2PresenterPipAndEffects(unittest.TestCase):
    def test_r2_07_presenter_schema_migration_and_policy_conflicts(self) -> None:
        """R2-07: v1 brief migrates to LEGACY_DYNAMIC; unknown/conflicting/missing-source presenter policies rejected."""
        base_v1 = {
            "schema_version": "kh02.brief.v1",
            "brief_id": "v1_compat",
            "mode": "talking_head",
            "profile": "editorial_pro",
            "objective": "Verify v1 migration",
            "audience": "general",
            "platform": "reels",
            "aspect_ratio": "9:16",
            "language": "en",
            "tone": "warm",
            "key_message": "Legacy default stays dynamic",
            "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
            "sources": [{"asset_id": "a1", "path": "/tmp/a.mp4", "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
        }
        b_v1 = planner.build_editor_brief(base_v1)
        norm_def = camera.normalize_presenter_policy(b_v1.get("presenter_policy"), brief_mode=b_v1["mode"])
        self.assertEqual(norm_def["mode"], "LEGACY_DYNAMIC")

        # Unknown presenter mode rejected by schema
        bad_mode = dict(base_v1, presenter_policy={"mode": "MAGIC_HOLOGRAM"})
        with self.assertRaises(ValueError):
            planner.build_editor_brief(bad_mode)

        # FIXED_PIP + allow_fullscreen=True rejected
        bad_fs = dict(base_v1, presenter_policy={"mode": "FIXED_PIP", "visibility": "ALWAYS_VISIBLE", "allow_fullscreen": True})
        with self.assertRaises(ValueError):
            planner.build_editor_brief(bad_fs)

        # FIXED_PIP + missing presenter source -> BLOCKED_PRESENTER_SOURCE (never silent faceless fallback!)
        no_aroll = dict(
            base_v1,
            presenter_policy={"mode": "FIXED_PIP", "visibility": "ALWAYS_VISIBLE", "anchor": "BOTTOM_LEFT"},
            sources=[{"asset_id": "img1", "path": "/tmp/img.png", "kind": "still_asset", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
        )
        with self.assertRaises(ValueError) as ctx:
            planner.build_editor_brief(no_aroll)
        self.assertIn("BLOCKED_PRESENTER_SOURCE", str(ctx.exception))

        # FACELESS mode unaffected
        faceless = dict(base_v1, mode="faceless", sources=[{"asset_id": "img1", "path": "/tmp/img.png", "kind": "still_asset", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}])
        fb = planner.build_editor_brief(faceless)
        fp = camera.normalize_presenter_policy(fb.get("presenter_policy"), brief_mode="faceless", has_presenter_source=False)
        self.assertEqual(fp["mode"], "FACELESS")

    def test_r2_08_real_fixed_pip_bottom_left_and_bottom_right_render(self) -> None:
        """R2-08: Real Chromium + render.mjs + H1 + decoded frame QA on FIXED_PIP BOTTOM_LEFT and BOTTOM_RIGHT."""
        up_root = Path(os.environ.get("SEECUT_UPSTREAM_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut")))
        tools_root = Path(os.environ.get("SEECUT_STANDALONE_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone"))) / "tools"
        if not (up_root / "skill/seecut/scripts/render.mjs").is_file() or not (tools_root / "node_modules/hyperframes").is_dir():
            self.skipTest("Pinned SeeCut / HyperFrames runtime not present in this environment")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vid = _make_moving_presenter_clip(root / "aroll.mp4", duration_sec=2.0)
            ev_img = _make_real_png(root / "ui.png")
            tr_p = root / "words.json"
            tr_p.write_text(json.dumps([
                {"word": " Look", "start": 0.1, "end": 0.5, "probability": 0.99},
                {"word": " at", "start": 0.52, "end": 0.70, "probability": 0.99},
                {"word": " this", "start": 0.72, "end": 1.00, "probability": 0.99},
                {"word": " result.", "start": 1.20, "end": 1.80, "probability": 0.99},
            ]), encoding="utf-8")

            for anchor in ("BOTTOM_LEFT", "BOTTOM_RIGHT"):
                brief_p = root / f"brief_{anchor}.json"
                brief_p.write_text(json.dumps({
                    "schema_version": "kh02.brief.v2",
                    "brief_id": f"r2_08_{anchor.lower()}",
                    "mode": "talking_head",
                    "profile": "editorial_pro",
                    "objective": f"Verify {anchor} FIXED_PIP rendered frames",
                    "audience": "engineers",
                    "platform": "reels",
                    "aspect_ratio": "9:16",
                    "language": "en",
                    "tone": "crisp",
                    "key_message": "Fixed PIP stays visible every frame",
                    "proof_points": ["Clean frame 0 hook", "Real UI capture", "Zero EOF fade"],
                    "voice_policy": "LOCK_VOICE",
                    "caption_policy": "OFF",
                    "music_policy": "OFF",
                    "sfx_policy": "OFF",
                    "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
                    "presenter_policy": {
                        "mode": "FIXED_PIP",
                        "visibility": "ALWAYS_VISIBLE",
                        "anchor": anchor,
                        "shape": "ROUNDED_RECT",
                        "width_ratio": 0.30,
                    },
                    "sources": [
                        {"asset_id": "src_a", "path": str(vid), "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL", "face_rect": [0.22, 0.15, 0.78, 0.58]},
                        {"asset_id": "ev_ui", "path": str(ev_img), "kind": "real_ui", "rights": "CAPTURED_REAL_EVIDENCE_MASKED", "label": "Look at UI result"},
                    ],
                }), encoding="utf-8")

                run_dir = root / f"run_{anchor.lower()}"
                res = kh_run.execute_pipeline(
                    brief_path=brief_p, run_root=run_dir, mode="preview", offline=True,
                    transcript_json_path=tr_p, seecut_root=up_root, tools_root=tools_root,
                    reserve_floor_gib=0.1, production_mode=False,
                )
                self.assertEqual(res["exit_code"], 0, f"{anchor} failed: {res}")
                self.assertTrue(res["h1_geom"]["pass"], f"{anchor} H1 failed: {res['h1_geom']}")
                pqa = res["presenter_frame_qa"]
                self.assertTrue(pqa["pass"], f"{anchor} presenter QA failed: {pqa}")
                self.assertEqual(pqa["pip_presence_coverage_ratio"], 1.0)
                self.assertTrue(pqa["frame_0_ok"])
                self.assertTrue(pqa["eof_frame_ok"])
                self.assertEqual(pqa["occluded_frame_count"], 0)
                if anchor == "BOTTOM_LEFT":
                    self.assertLess(pqa["pip_box_norm"][0], 0.15)
                else:
                    self.assertGreater(pqa["pip_box_norm"][0], 0.45)

    def test_r2_09_pip_crop_rotation_circle_and_tracking_honesty(self) -> None:
        """R2-09: Portrait/landscape/90deg rotation crop math, circle clip detection, safe zoom cap & honest tracking status."""
        # Portrait with verified face_rect
        crop_p = camera.fit_face_safe_crop(1080, 1920, 324, 432, face_rect=[0.14, 0.12, 0.56, 0.48], shape="ROUNDED_RECT", extra_zoom=1.15)
        self.assertTrue(crop_p["face_safe"])
        self.assertEqual(crop_p["tracking_status"], "FALLBACK_STATIC_VERIFIED")
        self.assertLessEqual(crop_p["scale"], crop_p["max_safe_zoom"])

        # Rotated 90deg source swaps effective dimensions cleanly
        crop_rot = camera.fit_face_safe_crop(1920, 1080, 324, 432, rotation_deg=90, face_rect=[0.25, 0.15, 0.72, 0.52])
        self.assertEqual(crop_rot["source_effective_size"], [1080, 1920])
        self.assertTrue(crop_rot["face_safe"])

        # Landscape 16:9 presenter region adapts cleanly without canvas stretch
        pol = camera.normalize_presenter_policy({"mode": "FIXED_PIP", "anchor": "BOTTOM_LEFT", "shape": "CIRCLE", "width_ratio": 0.24})
        reg_land = camera.resolve_presenter_region(pol, output_width=1280, output_height=720, source_width=1920, source_height=1080, platform="youtube", aspect_ratio="16:9")
        self.assertEqual(reg_land["pixel_box"][2], reg_land["pixel_box"][3])  # exact square for circle!

        # Extreme wide face rect in small circle flags clipping honestly
        crop_bad = camera.fit_face_safe_crop(1080, 1920, 200, 200, face_rect=[0.02, 0.02, 0.98, 0.98], shape="CIRCLE")
        self.assertTrue(crop_bad["circle_clipped"] or crop_bad["face_clipped"])
        self.assertFalse(crop_bad["face_safe"])

    def test_r2_10_layout_collision_solver_and_negative_occlusion_detection(self) -> None:
        """R2-10: Swept bounding box reflows away from presenter; unsolvable returns LAYOUT_CONFLICT; one-frame occlusion caught."""
        reserved = [0.03, 0.56, 0.35, 0.27]
        ev_collide = [{
            "event_id": "ev_bad", "beat_id": "b1", "start_frame": 0, "end_frame": 48,
            "layer_cat": "card", "normalized_bbox": [0.05, 0.60, 0.45, 0.18],
            "transform": {"scale_from": 0.6, "scale_to": 1.0, "y_offset_px": 18}, "easing": "back.out(2.2)",
            "params": {"kind": "editorial_headline"},
        }]
        txt_collide = [{
            "text_id": "cap_01", "beat_id": "b1", "role": "caption", "start_frame": 0, "end_frame": 48,
            "position": [0.06, 0.62, 0.40, 0.12],
        }]
        # Reflow enabled -> moves cleanly outside reserved presenter zone
        solved = typography.resolve_layout_avoiding_presenter(
            ev_collide, txt_collide, presenter_reserved_region=reserved, allow_reflow=True,
        )
        self.assertTrue(solved["ok"], solved)
        self.assertGreaterEqual(len(solved["reflows"]), 2)
        sw_ev = typography.compute_swept_bbox(solved["visual_events"][0]["normalized_bbox"], transform=ev_collide[0]["transform"], easing="back.out(2.2)")
        ov, _ = typography.boxes_overlap(sw_ev, reserved)
        self.assertFalse(ov)

        # Strict no-reflow -> reports LAYOUT_CONFLICT
        strict_fail = typography.resolve_layout_avoiding_presenter(
            ev_collide, txt_collide, presenter_reserved_region=reserved, allow_reflow=False,
        )
        self.assertFalse(strict_fail["ok"])
        self.assertEqual(strict_fail["status"], "LAYOUT_CONFLICT")

    def test_r2_11_adaptive_hybrid_and_legacy_compatibility(self) -> None:
        """R2-11: ADAPTIVE_PIP, DYNAMIC_HYBRID (ALWAYS_VISIBLE never hides), and LEGACY_DYNAMIC compatibility."""
        beats = [
            {"beat_id": "b1", "start_frame": 0, "end_frame": 24, "semantic_function": "hook", "proof_status": "SOURCE_FOOTAGE_DIRECT"},
            {"beat_id": "b2", "start_frame": 24, "end_frame": 48, "semantic_function": "step", "proof_status": "CONCEPT_ILLUSTRATION"},
            {"beat_id": "b3", "start_frame": 48, "end_frame": 72, "semantic_function": "payoff", "proof_status": "SOURCE_FOOTAGE_DIRECT"},
        ]
        # ADAPTIVE_PIP with allow_anchor_switch=True shifts smoothly
        pol_ad = camera.normalize_presenter_policy({"mode": "ADAPTIVE_PIP", "visibility": "ALWAYS_VISIBLE", "anchor": "BOTTOM_LEFT", "allow_anchor_switch": True})
        cams_ad = camera.plan_camera_path(beats, presenter_policy=pol_ad, output_width=720, output_height=1280)
        self.assertEqual(camera.validate_presenter_path(cams_ad, pol_ad, total_frames=72), [])
        self.assertNotEqual(cams_ad[0]["target_region"], cams_ad[1]["target_region"])

        # Unauthorized anchor switch when allow_anchor_switch=False -> FAIL
        pol_no_sw = camera.normalize_presenter_policy({"mode": "ADAPTIVE_PIP", "visibility": "ALWAYS_VISIBLE", "anchor": "BOTTOM_LEFT", "allow_anchor_switch": False})
        errs_sw = camera.validate_presenter_path(cams_ad, pol_no_sw, total_frames=72)
        self.assertTrue(any(e["code"] == "PRESENTER_UNAUTHORIZED_ANCHOR_SWITCH" for e in errs_sw))

        # DYNAMIC_HYBRID with ALWAYS_VISIBLE never hides
        pol_hyb = camera.normalize_presenter_policy({"mode": "DYNAMIC_HYBRID", "visibility": "ALWAYS_VISIBLE", "allow_fullscreen": True, "allow_hide": False})
        cams_hyb = camera.plan_camera_path(beats, presenter_policy=pol_hyb, output_width=720, output_height=1280)
        self.assertEqual(cams_hyb[0]["target_region"], [0.0, 0.0, 1.0, 1.0])
        self.assertLess(cams_hyb[1]["target_region"][2], 0.5)
        self.assertTrue(all(c.get("presenter_opacity", 1.0) >= 0.99 for c in cams_hyb))

    def test_r2_12_decoded_presenter_occlusion_frozen_and_eof_mismatch_fail(self) -> None:
        """R2-12: Decoded presenter QA fails on one-frame black occlusion, frozen presenter, or frame count mismatch."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Construct actively hue-cycling moving source so every pixel in PIP moves frame-to-frame
            src = root / "src.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=24:duration=1,hue=H=2*PI*t",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", str(src),
            ], check=True, capture_output=True, timeout=60)
            # Construct MP4 with black box painted over bottom-left PIP on frames 10..12
            occ = root / "occluded.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-y", "-i", str(src),
                "-vf", "drawbox=x=18:y=370:w=115:h=155:color=black:t=fill:enable='between(n,10,12)'",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "copy", str(occ),
            ], check=True, capture_output=True, timeout=60)
            sb = {
                "output_spec": {"width": 360, "height": 640, "fps_num": 24, "fps_den": 1, "total_frames": 24},
                "presenter_policy": {"mode": "FIXED_PIP", "visibility": "ALWAYS_VISIBLE"},
                "presenter_layout": {
                    "region": {"normalized_box": [0.055, 0.585, 0.30, 0.225]},
                    "crop": {"source_crop_norm": [0.055, 0.585, 0.355, 0.81], "tracking_status": "FALLBACK_STATIC_VERIFIED"},
                },
                "beats": [{"beat_id": "b1", "start_frame": 0, "end_frame": 24}],
            }
            res_occ = qa.verify_presenter_frames(occ, src, sb, root / "qa_occ", emit_crop_evidence=False)
            self.assertFalse(res_occ["pass"])
            self.assertGreaterEqual(res_occ["occluded_frame_count"], 3)

            # Construct frozen MP4 (static frame 0 repeated 24 times) vs moving source -> must fail!
            frz = root / "frozen.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-y", "-i", str(src),
                "-vf", "select=eq(n\\,0),tpad=stop_mode=clone:stop_duration=1",
                "-frames:v", "24", "-r", "24", "-c:v", "libx264", "-preset", "ultrafast", "-an", str(frz),
            ], check=True, capture_output=True, timeout=60)
            res_frz = qa.verify_presenter_frames(frz, src, sb, root / "qa_frz", emit_crop_evidence=False)
            self.assertFalse(res_frz["pass"])

    def test_r2_13_effect_ledger_and_registry_validation(self) -> None:
        """R2-13: EFFECT_APPLICATION_LEDGER distinguishes eligible/selected/compiled/rendered; unknown capability rejected."""
        reg = registry.build_registry()
        sb = {
            "visual_events": [
                {"event_id": "ev_01", "capability_id": "hf.kinetic_phrase_mask"},
                {"event_id": "ev_02", "capability_id": "hf.clip_mask_wipe"},
            ]
        }
        cm = {"plan_sha256": "a" * 64, "compiled_events": [{"event_id": "ev_01"}, {"event_id": "ev_02"}]}
        with tempfile.TemporaryDirectory() as td:
            led = qa.build_effect_application_ledger(sb, cm, registry=reg, rendered_mp4_sha="b" * 64, out_path=Path(td) / "LEDGER.json")
            self.assertEqual(led["selected_capabilities_count"], 2)
            km_row = next(r for r in led["capabilities"] if r["capability_id"] == "hf.kinetic_phrase_mask")
            self.assertTrue(km_row["rendered_verified"])
            unused = [r for r in led["capabilities"] if not r["selected_event_ids"]]
            self.assertTrue(all(r["not_used_reason"] is not None for r in unused))

    def test_r2_14_transition_seam_and_mobile_typography_unicode(self) -> None:
        """R2-14: Transition seam audit catches black flash; mobile 360px readability checks EN/VI/CJK and top captions."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _make_moving_presenter_clip(root / "clean.mp4", duration_sec=2.0)
            sb = {
                "output_spec": {"width": 360, "height": 640, "fps_num": 24, "fps_den": 1, "total_frames": 48},
                "transitions": [{"transition_id": "tr_01", "from_beat": "b1", "to_beat": "b2", "boundary_frame": 24, "technique": "motion_bridge", "reason": "beat shift"}],
                "beats": [
                    {"beat_id": "b1", "start_frame": 0, "end_frame": 24, "semantic_function": "hook", "intentional_hold": False},
                    {"beat_id": "b2", "start_frame": 24, "end_frame": 48, "semantic_function": "payoff", "intentional_hold": False},
                ],
            }
            sr = qa.audit_transition_seams(sb, src, out_path=root / "SEAMS.json")
            self.assertTrue(sr["pass"])
            ha = qa.audit_visual_holds(sb, src, out_path=root / "HOLDS.json")
            self.assertEqual(len(ha["beats"]), 2)

            # Vietnamese diacritics & CJK 360px mobile readability
            vi = typography.measure_layout_mobile("Tối ưu hóa quy trình dựng video tự động", font_size_px=30, normalized_box=[0.06, 0.12, 0.88, 0.24], canvas_width=720, canvas_height=1280, language="vi")
            self.assertTrue(vi["mobile_readable"])
            self.assertTrue(vi["has_vietnamese_diacritics"])
            cjk = typography.measure_layout_mobile("自动化专业剪辑工作流", font_size_px=32, normalized_box=[0.06, 0.12, 0.88, 0.24], canvas_width=720, canvas_height=1280, language="zh")
            self.assertTrue(cjk["mobile_readable"])
            self.assertTrue(cjk["has_cjk"])


if __name__ == "__main__":
    unittest.main()
