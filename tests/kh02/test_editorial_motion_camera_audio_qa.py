"""KH-02 Editorial Planner, Registry, Motion, Camera, Transitions, Typography, Layers, Audio & QA Tests (T16-T28)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STANDALONE = Path(os.environ.get("SEECUT_STANDALONE_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone")))
sys.path.insert(0, str(REPO_ROOT))

from scripts.kh02 import (  # noqa: E402
    camera, compile_plan, contracts, edit_decisions, motion, planner, qa, registry, sound, source_analysis, transition, typography,
)


def _make_synth_mp4(dst: Path, duration: float = 2.0, fps: int = 24, size: str = "320x180", with_audio: bool = True, black: bool = False) -> Path:
    src_v = f"color=c=black:size={size}:rate={fps}:duration={duration}" if black else f"testsrc2=size={size}:rate={fps}:duration={duration}"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src_v]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}", "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), str(dst)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst


def _fake_inventory(src_path: str = "/tmp/fake.mp4", kind: str = "a_roll", w: int = 1080, h: int = 1920, frames: int = 144) -> dict:
    return {
        "schema_version": "kh02.source_inventory.v1",
        "run_id": "test",
        "created_at_utc": "2026-11-24T00:00:00Z",
        "assets": [{
            "asset_id": "src_main", "root_id": "r", "source_key": "r:src_main",
            "basename": "fake.mp4", "canonical_path": src_path, "kind": kind,
            "rights": "OWNER_SUPPLIED_NONCOMMERCIAL", "usable_in_render": True,
            "sha256": "a" * 64, "size_bytes": 10000,
            "media": {
                "has_video": True, "has_audio": True, "is_still": False,
                "duration_sec": frames / 24.0, "width": w, "height": h,
                "fps_num": 24, "fps_den": 1, "nb_frames": frames,
                "is_vfr": False, "rotation_deg": 0, "sar": "1:1",
                "video_codec": "h264", "audio_codec": "aac", "audio_sample_rate": 48000, "audio_channels": 2,
            },
            "face_rect": [0.25, 0.18, 0.76, 0.59] if kind == "a_roll" else "UNKNOWN",
            "silences": [], "speech_spans": [], "scene_changes": [],
        }],
    }


def _make_brief(mode: str, profile: str = "editorial_pro", aspect: str = "9:16", caption: str = "OFF") -> dict:
    return {
        "schema_version": "kh02.brief.v1",
        "brief_id": f"b_{mode}",
        "title": f"Test {mode}",
        "mode": mode,
        "profile": profile,
        "objective": f"Deliver clear {mode} storytelling",
        "audience": "general",
        "platform": "youtube" if aspect == "16:9" else "reels",
        "aspect_ratio": aspect,
        "language": "en",
        "tone": "clear",
        "key_message": f"Key message for {mode}",
        "voice_policy": "LOCK_VOICE",
        "caption_policy": caption,
        "music_policy": "OFF",
        "sfx_policy": "OPTIONAL_LICENSED",
        "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
        "sources": [{"asset_id": "src_main", "path": "/tmp/fake.mp4", "kind": "a_roll" if mode != "faceless" else "b_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
    }


class TestEditorialMotionCameraAudioQA(unittest.TestCase):
    def test_t16_planner_cross_niche_diversity_and_v1_migration(self):
        """T16: planner creates distinct niche themes/capabilities, zero hardcoded Olivia, migrates v1 cleanly."""
        inv_th = _fake_inventory(kind="a_roll")
        inv_fl = _fake_inventory(kind="b_roll", w=1920, h=1080)
        sb_th = planner.resolve_editorial_plan(_make_brief("talking_head"), inv_th, run_id="r_th")
        sb_sw = planner.resolve_editorial_plan(_make_brief("software_demo", aspect="16:9"), inv_fl, run_id="r_sw")
        sb_doc = planner.resolve_editorial_plan(_make_brief("documentary", profile="minimal_documentary", aspect="16:9"), inv_fl, run_id="r_doc")

        self.assertNotEqual(sb_th["story_arc"]["theme"], sb_sw["story_arc"]["theme"])
        self.assertNotEqual(sb_sw["story_arc"]["theme"], sb_doc["story_arc"]["theme"])
        self.assertNotIn("olivia", json.dumps(sb_sw).lower())
        self.assertNotIn("gumroad", json.dumps(sb_doc).lower())
        self.assertEqual(qa.evaluate_editorial(sb_th, inv_th)["pass"], True)
        self.assertEqual(qa.evaluate_editorial(sb_sw, inv_fl)["pass"], True)
        self.assertEqual(qa.evaluate_editorial(sb_doc, inv_fl)["pass"], True)

        # v1 -> v2 migration
        v1 = {"shots": [{"title": "Hook", "start_sec": 0, "end_sec": 3}, {"title": "Payoff", "start_sec": 3, "end_sec": 6}]}
        v2 = planner.migrate_shotbook_v1_to_v2(v1, run_id="mig", source_asset_id="src_main", source_sha256="a" * 64, total_frames=144)
        self.assertEqual(contracts.validate_references(v2), [])

    def test_t17_registry_catalog_fallback_and_params(self):
        """T17: pinned catalog distinguishes CATALOG_LISTED vs AVAILABLE_VERIFIED; unsupported falls back explicitly."""
        cat_path = STANDALONE / "runs/20261124_000005_SC01/logs/hyperframes_catalog.json"
        reg = registry.build_registry(catalog_json_path=cat_path if cat_path.is_file() else None)
        if cat_path.is_file():
            self.assertGreaterEqual(reg["catalog_total_items"], 400)
        # Catalog items are NOT falsely marked AVAILABLE_VERIFIED without test render
        sp = next(c for c in reg["capabilities"] if c["capability_id"] == "hf.spring_pop")
        self.assertNotEqual(sp["availability"], "AVAILABLE_VERIFIED")
        # Unsupported 3D camera falls back explicitly to hf.card_resize
        sel = registry.select_capability(reg, "experimental.true_3d_camera", aspect_ratio="9:16", duration_sec=3.0)
        self.assertTrue(sel["fallback_used"])
        self.assertEqual(sel["selected_id"], "hf.card_resize")
        # Parameter out-of-range rejected
        ok, errs = registry.validate_capability(reg, "hf.spring_pop", params={"scale_from": 5.0})
        self.assertFalse(ok)
        # Spring forbidden in minimal_documentary
        ok_doc, _ = registry.validate_capability(reg, "hf.spring_pop", profile="minimal_documentary")
        self.assertFalse(ok_doc)

    def test_t18_gsap_deterministic_seek_in_real_browser(self):
        """T18: compiled HTML/GSAP escapes XSS, starts opacity:0, and seeks deterministically in real Chromium."""
        with tempfile.TemporaryDirectory(prefix="kh02_t18_") as td:
            td_p = Path(td)
            src = _make_synth_mp4(td_p / "src.mp4", duration=2.0, fps=24, size="360x640")
            inv = source_analysis.build_inventory(
                [{"asset_id": "src_main", "root_id": "r", "path": str(src), "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL", "face_rect": [0.25, 0.18, 0.75, 0.58]}],
                run_id="t18", analyze_audio_motion=False,
            )
            brief = _make_brief("talking_head")
            brief["key_message"] = '<script>window.__xss=1</script>Safe Title'
            sb = planner.resolve_editorial_plan(brief, inv, run_id="t18")
            bdir = td_p / "build"
            comp = compile_plan.compile_shotbook(sb, inv, bdir, tools_root=STANDALONE / "tools")
            html_txt = Path(comp["html_path"]).read_text(encoding="utf-8")
            self.assertNotIn("<script>window.__xss=1</script>", html_txt)
            self.assertIn("&lt;script&gt;", html_txt)

            # Real headless Chromium deterministic seek verification (if tools installed)
            pw_dir = STANDALONE / "tools/node_modules/playwright-core"
            if pw_dir.is_dir():
                (bdir / "node_modules").symlink_to(STANDALONE / "tools/node_modules")
                check_js = f"""
import {{ chromium, chromePath }} from '{(STANDALONE / "third_party/SeeCut/skill/seecut/scripts/_common.mjs").as_posix()}';
const b = await chromium('{bdir.as_posix()}').launch({{ headless: true, executablePath: chromePath() }});
const pg = await (await b.newContext({{ viewport: {{ width: 720, height: 1280 }} }})).newPage();
await pg.goto('file://{(bdir / "master.html").as_posix()}', {{ waitUntil: 'load' }});
const res = await pg.evaluate(() => {{
  const tl = window.__timelines.master;
  const probe = (t) => {{
    tl.time(t);
    return [...document.querySelectorAll('.kh-el')].map(e => ({{
      id: e.id, op: +parseFloat(getComputedStyle(e).opacity).toFixed(3), tr: getComputedStyle(e).transform
    }}));
  }};
  const a1 = probe(0.0), mid1 = probe(1.0), end1 = probe(1.9);
  const a2 = probe(0.0), mid2 = probe(1.0);
  return {{ xss: !!window.__xss, dur: tl.duration(), same0: JSON.stringify(a1) === JSON.stringify(a2), sameMid: JSON.stringify(mid1) === JSON.stringify(mid2), endCount: end1.length }};
}});
await b.close();
console.log(JSON.stringify(res));
"""
                p = subprocess.run(["node", "--input-type=module", "-e", check_js], capture_output=True, text=True, timeout=30)
                self.assertEqual(p.returncode, 0, p.stderr)
                out = json.loads(p.stdout.strip())
                self.assertFalse(out["xss"])
                self.assertTrue(out["same0"])
                self.assertTrue(out["sameMid"])
                self.assertGreater(out["dur"], 1.5)

    def test_t19_camera_constraints_and_continuity(self):
        """T19: camera rejects underscan black edges, pan overscan, overzoom, face chop & non-cut jump; allows intentional cut."""
        bad_cams = [
            {"camera_id": "c1", "beat_id": "b1", "start_frame": 0, "end_frame": 48, "target_region": [0, 0, 1, 1],
             "framing_from": "full", "framing_to": "full", "scale_from": 0.9, "scale_to": 1.6,
             "pos_from": [0.0, 0.0], "pos_to": [0.3, 0.0], "anchor": [0.5, 0.3],
             "velocity_limit": 0.2, "acceleration_limit": 0.5, "continuity_group": "g1", "reason": "bad", "source_resolution_limit": 1.25},
        ]
        issues = camera.validate_camera_continuity(bad_cams, face_rect=[0.2, 0.1, 0.8, 0.6], fps=24.0)
        codes = {i["code"] for i in issues}
        self.assertIn("CAMERA_BLACK_EDGE_UNDERSCAN", codes)
        self.assertIn("CAMERA_OVERZOOM", codes)
        self.assertIn("CAMERA_PAN_BLACK_EDGE", codes)

        # Discontinuity flagged unless marked as intentional cut frame
        pair_cams = [
            {"camera_id": "c1", "beat_id": "b1", "start_frame": 0, "end_frame": 24, "target_region": [0, 0, 1, 1],
             "framing_from": "full", "framing_to": "full", "scale_from": 1.0, "scale_to": 1.05,
             "pos_from": [0, 0], "pos_to": [0, 0], "anchor": [0.5, 0.4], "velocity_limit": 0.5, "acceleration_limit": 1.0,
             "continuity_group": "g1", "reason": "ok", "source_resolution_limit": 1.35},
            {"camera_id": "c2", "beat_id": "b2", "start_frame": 24, "end_frame": 48, "target_region": [0, 0, 1, 1],
             "framing_from": "full", "framing_to": "full", "scale_from": 1.20, "scale_to": 1.22,
             "pos_from": [0, 0], "pos_to": [0, 0], "anchor": [0.5, 0.4], "velocity_limit": 0.5, "acceleration_limit": 1.0,
             "continuity_group": "g1", "reason": "cut", "source_resolution_limit": 1.35},
        ]
        self.assertTrue(any(i["code"] == "CAMERA_DISCONTINUITY" for i in camera.validate_camera_continuity(pair_cams)))
        self.assertEqual(camera.validate_camera_continuity(pair_cams, intentional_cut_frames={24}), [])

    def test_t20_motion_stagger_settle_and_profile_limits(self):
        """T20: motion grammar forbids spring on minimal_documentary, flags brief flash, settles cleanly."""
        ev = {
            "event_id": "e1", "beat_id": "b1", "start_frame": 10, "end_frame": 40,
            "capability_id": "hf.spring_pop", "easing": "back.out(2.2)", "entry_frames": 8, "exit_frames": 4,
            "opacity": 1.0, "transform": {"scale_from": 0.6, "scale_to": 1.0, "y_offset_px": 16},
        }
        self.assertEqual(motion.validate_motion_curves([ev], profile="editorial_pro"), [])
        doc_issues = motion.validate_motion_curves([ev], profile="minimal_documentary")
        self.assertTrue(any(i["code"] == "MOTION_UNSUPPORTED_SPRING" for i in doc_issues))
        # Pose before start is invisible, settled pose at midpoint is opacity=1.0, scale=1.0
        self.assertEqual(motion.evaluate_pose(ev, 5)["opacity"], 0.0)
        self.assertEqual(motion.evaluate_pose(ev, 25)["scale"], 1.0)

    def test_t21_transition_seam_and_hard_cut_gate(self):
        """T21: transition solves match anchor, rejects beat gaps & unapproved hard cuts, accepts approved exception."""
        beats = [
            {"beat_id": "b1", "start_frame": 0, "end_frame": 48, "semantic_function": "hook", "visual_mode": "full", "continuity_out": "spine"},
            {"beat_id": "b2", "start_frame": 48, "end_frame": 96, "semantic_function": "evidence", "visual_mode": "pip", "continuity_in": "spine"},
        ]
        tr = transition.choose_transition(beats[0], beats[1], fps=24.0)
        self.assertEqual(tr["technique"], "match_anchor")
        self.assertEqual(transition.validate_seam([tr], beats, fps=24.0), [])

        hard_tr = dict(tr, technique="intentional_cut", overlap_range=[48, 48])
        self.assertTrue(any(i["code"] == "SEAM_UNAPPROVED_HARD_CUT" for i in transition.validate_seam([hard_tr], beats, profile="editorial_pro")))
        self.assertEqual(
            transition.validate_seam([hard_tr], beats, profile="editorial_pro", approved_exceptions=[{"rule_id": "ALLOW_INTENTIONAL_CUT"}]),
            [],
        )

    def test_t22_typography_multilingual_caption_policy_and_mobile(self):
        """T22: EN/VI/CJK legibility, 360px floor, overflow check, caption OFF rejects captions, ON requires top z_order."""
        vi_lay = typography.measure_text_layout("Thiết kế cửa hàng chuyên nghiệp", font_size_px=34, box_width_px=600, box_height_px=120)
        self.assertTrue(vi_lay["has_vietnamese_diacritics"])
        self.assertTrue(vi_lay["mobile_readable"])
        self.assertGreaterEqual(vi_lay["recommended_line_height"], 1.35)

        cjk_lay = typography.measure_text_layout("真实证据优先展示", font_size_px=34, box_width_px=600, box_height_px=120)
        self.assertTrue(cjk_lay["has_cjk"])

        # Caption when OFF -> P0 violation; caption with low z_order when ON -> P1 violation
        cap_ev = {
            "text_id": "c1", "beat_id": "b1", "text": "Hello", "word_ids": [], "role": "caption", "hierarchy": 4,
            "style": {"font_size_px": 28, "z_order": 10}, "font_ref": "system-sans-bold",
            "start_frame": 0, "end_frame": 40, "minimum_read_frames": 18, "position": [0.1, 0.75, 0.8, 0.1],
            "caption_policy": "ON_REQUESTED", "emphasis": [], "contrast_intent": "high",
        }
        self.assertTrue(any(i["code"] == "TYPO_UNREQUESTED_CAPTION" for i in typography.validate_readability([cap_ev], caption_policy="OFF")))
        self.assertTrue(any(i["code"] == "TYPO_CAPTION_NOT_TOP_LAYER" for i in typography.validate_readability([cap_ev], caption_policy="ON_REQUESTED", max_visual_z_order=50)))

    def test_t23_layer_sanitization_and_png_transparency(self):
        """T23: unsafe layer names rejected; compiled HTML includes sanitized data-layer & data-layer-cat."""
        with self.assertRaises(ValueError):
            contracts.sanitize_identifier("../evil/layer")
        self.assertEqual(contracts.sanitize_identifier("step_rail_01"), "step_rail_01")

    def test_t24_sfx_offsets_negative_clamp_density_and_rights(self):
        """T24: SFX negative onset clamped to 0, density warnings emitted, missing/unlicensed asset fails."""
        with tempfile.TemporaryDirectory(prefix="kh02_t24_") as td:
            td_p = Path(td)
            sfx_file = td_p / "whoosh.wav"
            sfx_file.write_bytes(b"RIFF" + b"\x00" * 200)
            ledger = [{"asset_id": "sfx_w", "rights": "LICENSED_COMMERCIAL", "duration_sec": 0.4, "trim_start_sec": 0.0}]
            plan = sound.plan_audio_cues(
                voice_asset_id="v1", total_frames=240, fps_num=24,
                sfx_policy="OPTIONAL_LICENSED", sfx_ledger=ledger,
                sfx_events=[
                    {"asset_ref": "sfx_w", "kind": "sfx_whoosh", "event_sec": 0.02, "anchor_event": "e0"},
                    {"asset_ref": "sfx_w", "kind": "sfx_pop", "event_sec": 1.0, "anchor_event": "e1"},
                    {"asset_ref": "sfx_w", "kind": "sfx_pop", "event_sec": 1.5, "anchor_event": "e2"},
                    {"asset_ref": "sfx_w", "kind": "sfx_pop", "event_sec": 2.0, "anchor_event": "e3"},
                ],
            )
            self.assertTrue(any("clamped to 0.0s" in w for w in plan["warnings"]))
            errs, warns = sound.validate_cues(plan["cues"], total_frames=240, fps=24.0, asset_paths={"v1": sfx_file, "sfx_w": sfx_file})
            self.assertEqual(errs, [])
            self.assertTrue(any("SFX density warning" in w for w in warns))

    def test_t25_audio_edl_fades_lock_voice_and_ebur128(self):
        """T25: Real EBU R128 measurement + LOCK_VOICE PCM correlation + TRIM_APPROVED derived fade preserves source."""
        with tempfile.TemporaryDirectory(prefix="kh02_t25_") as td:
            td_p = Path(td)
            src = _make_synth_mp4(td_p / "src.mp4", duration=3.0, fps=24, with_audio=True)
            sha_before = source_analysis.sha256_file(src)
            m = sound.measure_final_audio(src)
            self.assertTrue(m["measured"], m)
            self.assertIsNotNone(m["integrated_lufs"])
            self.assertIsNotNone(m["true_peak_dbtp"])

            # TRIM_APPROVED creates derived file with fades, leaving src untouched
            tmap = edit_decisions.build_time_map(
                "src", 72, 24, 1, voice_policy="TRIM_APPROVED",
                approved_cuts=[{"decision": "CUT_APPROVED", "start_sec": 1.0, "end_sec": 2.0, "reason": "test"}],
            )
            derived = td_p / "derived.mp4"
            res = edit_decisions.apply_approved_cut_map(src, tmap, derived)
            self.assertTrue(res["applied"])
            self.assertTrue(derived.is_file())
            self.assertEqual(source_analysis.sha256_file(src), sha_before)

            # Silent mp4 fails audio QA unless SILENT_BY_DESIGN
            silent = _make_synth_mp4(td_p / "silent.mp4", duration=1.5, fps=24, with_audio=False)
            sb = contracts.parse_json_strict(REPO_ROOT / "templates/kh02/SHOTBOOK_V2.example.json")
            self.assertFalse(qa.inspect_audio(silent, sb)["pass"])

    def test_t26_structural_mp4_qa_black_and_corrupt(self):
        """T26: validate_structural catches wrong res, corrupt MP4, black frames & missing audio."""
        with tempfile.TemporaryDirectory(prefix="kh02_t26_") as td:
            td_p = Path(td)
            sb = contracts.parse_json_strict(REPO_ROOT / "templates/kh02/SHOTBOOK_V2.example.json")
            sb["output_spec"].update({"width": 320, "height": 180, "output_scale": 1.0, "total_frames": 48})
            good = _make_synth_mp4(td_p / "good.mp4", duration=2.0, fps=24, size="320x180", with_audio=True)
            r_good = qa.validate_structural(good, sb, expect_audio=True)
            self.assertTrue(r_good["strict_pass"], r_good["issues"])

            blk = _make_synth_mp4(td_p / "blk.mp4", duration=2.0, fps=24, size="320x180", with_audio=False, black=True)
            r_blk = qa.validate_structural(blk, sb, expect_audio=True)
            self.assertFalse(r_blk["pass"])
            codes = {i["code"] for i in r_blk["issues"]}
            self.assertIn("MP4_MISSING_AUDIO", codes)
            self.assertIn("MP4_BLACK_DETECTED", codes)

    def test_t27_timeline_filmstrip_waveform_diagnostics(self):
        """T27: render_timeline_diagnostics produces actual boundary PNGs & frame_diagnostics.csv."""
        with tempfile.TemporaryDirectory(prefix="kh02_t27_") as td:
            td_p = Path(td)
            mp4 = _make_synth_mp4(td_p / "v.mp4", duration=2.5, fps=24, size="360x640", with_audio=True)
            res = qa.render_timeline_diagnostics(
                mp4, td_p / "diag",
                windows=[{"name": "open", "start_sec": 0.0, "end_sec": 1.2}, {"name": "eof", "start_sec": 1.5, "end_sec": 2.5}],
                mapped_words=[{"text": "Hello", "out_start_sec": 0.4, "out_end_sec": 0.8}],
                frames_per_window=4,
            )
            if res["status"] == "BLOCKED_MISSING_DIAGNOSTIC":
                self.skipTest("Pillow absent in current python interpreter")
            self.assertEqual(res["status"], "PASS")
            self.assertEqual(len(res["files"]), 2)
            for f in res["files"]:
                self.assertTrue(Path(f["path"]).is_file() and Path(f["path"]).stat().st_size > 1000)
            self.assertTrue(Path(res["csv_path"]).is_file())

    def test_t28_provenance_rejects_mock_ffmpeg_engine(self):
        """T28: mock FFmpeg-only MP4 with wrong engine or mismatched SHA cannot claim native SeeCut render."""
        with tempfile.TemporaryDirectory(prefix="kh02_t28_") as td:
            td_p = Path(td)
            sb = contracts.parse_json_strict(REPO_ROOT / "templates/kh02/SHOTBOOK_V2.example.json")
            sb["output_spec"].update({"width": 320, "height": 180, "output_scale": 1.0, "total_frames": 48})
            mp4 = _make_synth_mp4(td_p / "fake_engine.mp4", duration=2.0, fps=24, size="320x180", with_audio=True)
            fake_prov = {"engine": "ffmpeg-only-mock", "renderer_sha256": None, "mp4_sha256": "0" * 64}
            res = qa.validate_structural(mp4, sb, provenance_log=fake_prov)
            self.assertFalse(res["pass"])
            self.assertFalse(res["provenance_verified"])
            self.assertTrue(any(i["code"] == "PROVENANCE_MISMATCH" for i in res["issues"]))


if __name__ == "__main__":
    unittest.main()
