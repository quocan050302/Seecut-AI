"""DX-01 Universal Motion Graphics & Hybrid Remotion Engine — Comprehensive Unit & Integration Test Suite."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.kh03 import (  # noqa: E402
    effect_registry,
    footage_effects,
    independent_qa,
    independent_renderer,
    motion_director,
    remotion_adapter,
    renderer_routing,
    router,
    semantic_timeline,
)
from scripts.kh03.contracts import hash_artifact  # noqa: E402
from scripts.kh03.ingest_cache import SharedIngestCache  # noqa: E402


def _make_clip(
    dst: Path,
    *,
    size: str = "360x640",
    fps: int = 24,
    dur: float = 2.0,
    pattern: str = "testsrc2",
    freq: int | None = 330,
) -> Path:
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{pattern}=size={size}:rate={fps}:duration={dur}"]
    if freq is not None:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={dur}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if freq is not None:
        cmd += ["-c:a", "aac", "-b:a", "96k", "-shortest"]
    else:
        cmd += ["-an"]
    cmd.append(str(dst))
    subprocess.run(cmd, check=True, capture_output=True, timeout=45)
    return dst


class TestKH03DX01MotionRemotionSuite(unittest.TestCase):
    def test_dx01_01_registry_validation_bounds_security_and_caption_gate(self) -> None:
        """DX01-01: All 8 families & 26 DX-01 effects cataloged; fail-closed on NaN, bounds, eval/URL, internal labels, caption gate."""
        reg = effect_registry.EffectRegistry(remotion_available=True)
        snap = reg.snapshot()
        self.assertEqual(snap["family_count"], 8)
        self.assertGreaterEqual(snap["dx01_effect_count"], 24)
        self.assertEqual(snap["legacy_component_count"], 11)
        for fam in effect_registry.MOTION_FAMILIES:
            self.assertGreaterEqual(len(snap["by_family"][fam]), 2, f"Family {fam} needs >=2 effects")

        # Valid invocation passes
        ok_ev = {
            "event_id": "ok1",
            "effect_id": "dx01.typo.kinetic_headline",
            "start_sec": 0.1,
            "end_sec": 2.0,
            "title": "Heat Transfer Explained",
            "font_size_scale": 1.1,
        }
        res_ok = reg.validate_invocation(ok_ev)
        self.assertTrue(res_ok["pass"], res_ok)

        # NaN / Inf rejected fail-closed
        bad_nan = {**ok_ev, "font_size_scale": float("nan")}
        self.assertFalse(reg.validate_invocation(bad_nan)["pass"])
        bad_inf = {**ok_ev, "end_sec": float("inf")}
        self.assertFalse(reg.validate_invocation(bad_inf)["pass"])

        # Out-of-bounds parameter rejected
        bad_bound = {**ok_ev, "font_size_scale": 9.5}
        self.assertFalse(reg.validate_invocation(bad_bound)["pass"])

        # Security / remote URL / eval / internal skill-ID leak rejected
        for bad_txt in ("eval('1+1')", "https://evil.example/a.js", "SK-04 Internal Demo", "<script>alert(1)</script>"):
            bad_sec = {**ok_ev, "title": bad_txt}
            chk = reg.validate_invocation(bad_sec)
            self.assertFalse(chk["pass"], f"Expected rejection for {bad_txt!r}")

        # Caption policy OFF blocks karaoke_cue fail-closed; ON_REQUESTED allows it
        kar_ev = {
            "event_id": "k1",
            "effect_id": "dx01.typo.karaoke_cue",
            "start_sec": 0.2,
            "end_sec": 1.8,
            "word_cues": [{"word": "Alpha", "start_sec": 0.2, "end_sec": 1.0}],
        }
        self.assertFalse(reg.validate_invocation(kar_ev, caption_policy="OFF")["pass"])
        self.assertTrue(reg.validate_invocation(kar_ev, caption_policy="ON_REQUESTED")["pass"])

        # Unknown source reference rejected
        bad_src = {**ok_ev, "source_id": "nonexistent_src"}
        self.assertFalse(reg.validate_invocation(bad_src, known_source_ids={"primary_aroll"})["pass"])

        # Remotion unavailable -> deterministic fallback resolution recorded honestly
        reg_no_rem = effect_registry.EffectRegistry(remotion_available=False)
        self.assertEqual(reg_no_rem.status_of("dx01.data.kinetic_counter"), "BLOCKED")
        fb_id, fb_st, fb_why = reg_no_rem.resolve_with_fallback("dx01.data.kinetic_counter")
        self.assertEqual(fb_id, "legacy.numerical_payoff_hero")
        self.assertEqual(fb_st, "FALLBACK_SELECTED")
        self.assertTrue(bool(fb_why))

        # CLI capability discovery reflects real runtime
        caps = router.run_capabilities()
        self.assertEqual(caps["effect_registry"]["family_count"], 8)
        self.assertIn("remotion_runtime", caps)

    def test_dx01_02_motion_director_intents_antirepetition_and_zero_core_hardcode(self) -> None:
        """DX01-02 + UNI-01: Semantic intent classification, anti-repetition, clean-shot SK-05 choices, ZERO niche hardcoding in core."""
        reg = effect_registry.EffectRegistry(remotion_available=True)
        beats = [
            {"beat_id": "b1", "start_frame": 0, "end_frame": 60, "function": "hook", "text": "Why do bridges sway in the wind?"},
            {"beat_id": "b2", "start_frame": 60, "end_frame": 132, "function": "evidence", "text": "Look closely at the resonance wave on this sensor."},
            {"beat_id": "b3", "start_frame": 132, "end_frame": 204, "function": "step", "text": "Step one, step two, step three tighten the damper."},
            {"beat_id": "b4", "start_frame": 204, "end_frame": 276, "function": "payoff", "text": "Amplitude drops from 45 cm down to 8 cm."},
            {"beat_id": "b5", "start_frame": 276, "end_frame": 336, "function": "context", "text": "Quiet engineering keeps everyone safe."},
        ]
        plan_sk04 = motion_director.direct_motion_plan(
            beats=beats, selected_skill_id="SK-04", media_type="documentary",
            aspect_ratio="9:16", caption_policy="OFF", has_secondary_sources=True, registry=reg,
        )
        self.assertEqual(len(plan_sk04["decisions"]), 5)
        self.assertGreaterEqual(len(plan_sk04["distinct_families"]), 2)
        for d in plan_sk04["decisions"]:
            self.assertIn("reason", d)
            self.assertIn("alternatives_considered", d)
            self.assertIn("fallback_id", d)

        # SK-05 must legitimately choose clean shots (`none` / no added effect) on calm beats
        plan_sk05 = motion_director.direct_motion_plan(
            beats=beats, selected_skill_id="SK-05", media_type="talking_head",
            aspect_ratio="9:16", caption_policy="OFF", has_secondary_sources=False, registry=reg,
        )
        self.assertGreater(plan_sk05["zero_effect_beat_count"], 0, "SK-05 must allow legitimate clean shots without graphics.")

        # UNI-01 Hardcode audit across ALL DX-01 core Python & TS modules (ZERO creator/product/finance strings!)
        forbidden = ("olivia", "nateliang", "nate liang", "notion", "paycheck plan", "sickday", "luxury ramen")
        core_files = [
            REPO_ROOT / "scripts" / "kh03" / "effect_registry.py",
            REPO_ROOT / "scripts" / "kh03" / "motion_director.py",
            REPO_ROOT / "scripts" / "kh03" / "footage_effects.py",
            REPO_ROOT / "scripts" / "kh03" / "remotion_adapter.py",
            REPO_ROOT / "scripts" / "kh03" / "semantic_timeline.py",
            REPO_ROOT / "scripts" / "kh03" / "independent_renderer.py",
            REPO_ROOT / "scripts" / "kh03" / "independent_qa.py",
            *list((REPO_ROOT / "remotion_bridge" / "src").rglob("*.ts*")),
        ]
        for cf in core_files:
            txt = cf.read_text(encoding="utf-8").lower()
            for tok in forbidden:
                self.assertNotIn(tok, txt, f"UNI-01 HARDCODE VIOLATION: {tok!r} found in {cf.name}")

    def test_dx01_03_semantic_timeline_plan_parity_noncard_derivation_and_fail_closed(self) -> None:
        """DX01-03: Consumes layout_plan/motion_graph into non-card DX-01 effects; preview/final normalized SHA matches; rejects dangling source."""
        with tempfile.TemporaryDirectory(prefix="dx01_st_") as td:
            td_p = Path(td)
            src = _make_clip(td_p / "a.mp4", dur=4.0)
            sreg = semantic_timeline.build_source_registry({"source_id": "primary_aroll", "path": str(src)})
            cp = {
                "selected_skill_id": "SK-04",
                "hook_plan": {"hook_headline": "How Turbines Cool"},
                "components": [
                    {
                        "component_id": "c_counter",
                        "beat_id": "b2",
                        "capability_id": "rolling_counter_payoff",
                        "start_frame": 56,
                        "end_frame": 92,
                        "params": {"headline": "Core Temp Drop", "start_value": "900", "end_value": "420", "unit": "C", "kicker": "COOLING"},
                    },
                ],
            }
            mg = {
                "nodes": [
                    {"component_id": "c_counter", "primitive": "exact_decimal_counter"},
                ]
            }
            lp = {
                "beat_layouts": [
                    {"beat_id": "b2", "graphic_zone": {"x": 0.08, "y": 0.14, "w": 0.84, "h": 0.22}},
                ]
            }
            spec_prev = semantic_timeline.compile_semantic_timeline(
                job_id="job_A", source_registry=sreg, creative_plan=cp, layout_plan=lp, motion_graph=mg,
                width=540, height=960, x264_preset="veryfast", crf=22, use_proxy_sources=True,
                prefer_dx01_non_card=True,
            )
            spec_full = semantic_timeline.compile_semantic_timeline(
                job_id="job_A", source_registry=sreg, creative_plan=cp, layout_plan=lp, motion_graph=mg,
                width=1080, height=1920, x264_preset="medium", crf=18, use_proxy_sources=False,
                prefer_dx01_non_card=True,
            )
            self.assertGreaterEqual(len(spec_prev["effect_invocations"]), 2, "Must derive non-card DX-01 effects from creative_plan + layout_plan + motion_graph!")
            self.assertIn("dx01.data.kinetic_counter", [fx["effect_id"] for fx in spec_prev["effect_invocations"]])
            self.assertEqual(
                spec_prev["normalized_creative_plan_sha256"],
                spec_full["normalized_creative_plan_sha256"],
                "Normalized creative plan SHA must be invariant across preview vs final geometry/codec!",
            )

            # Dangling source_id in visual_tracks fails closed!
            bad_spec = dict(spec_full)
            bad_spec["visual_tracks"] = [{
                "track_id": "bad", "source_id": "ghost_src", "mode": "source",
                "out_start_frame": 0, "out_end_frame": 48, "src_in_sec": 0.0, "src_out_sec": 2.0,
            }]
            self.assertFalse(semantic_timeline.validate_render_spec(bad_spec)["pass"])

    def test_dx01_04_footage_pullback_tilt_nlayers_and_dual_moving_transitions(self) -> None:
        """DX01-04/05: <1.0 canvas pullback, 2.5D tilt, N=3 layers, true dual-moving transitions + handle clamp fallback."""
        img_a = Image.new("RGB", (360, 640), (30, 120, 210))
        img_b = Image.new("RGB", (360, 640), (220, 80, 40))

        # True <1.0 pullback shows background canvas border rather than crop-zooming!
        pb = footage_effects.render_pullback_or_transform_frame(
            img_a, 360, 640, scale=0.65, tilt_y_deg=8.0, rotation_deg=-3.0, border_radius_px=24,
        )
        pb_arr = np.asarray(pb)
        self.assertNotEqual(tuple(int(x) for x in pb_arr[4, 4]), (30, 120, 210), "Corner must show canvas backdrop on <1.0 pullback!")
        self.assertGreater(float(np.mean(pb_arr[300:340, 160:200, 2])), 150.0, "Center must retain pulled-back subject footage!")

        # Dual-moving transitions produce distinct intermediate blends across progress
        arr_a1 = np.full((160, 90, 3), (20, 100, 220), dtype=np.uint8)
        arr_a2 = np.full((160, 90, 3), (80, 160, 240), dtype=np.uint8)  # moving outgoing stream!
        arr_b = np.full((160, 90, 3), (230, 70, 30), dtype=np.uint8)
        for tkind in ("dx01.trans.dual_crossfade", "dx01.trans.mask_wipe", "dx01.trans.whip_blur", "dx01.trans.match_reveal"):
            m1 = footage_effects.render_dual_moving_transition(arr_a1, arr_b, progress=0.4, kind=tkind)
            m2 = footage_effects.render_dual_moving_transition(arr_a2, arr_b, progress=0.4, kind=tkind)
            self.assertGreater(float(np.mean(np.abs(m1.astype(np.float32) - m2.astype(np.float32)))), 2.0,
                               f"Transition {tkind} MUST reflect live moving outgoing frame (not frozen)!")

        # Handle validation: insufficient outgoing handle falls back cleanly to cut
        tr_short_tail = {"source_id": "A", "src_in_sec": 0.0, "src_out_sec": 1.98, "out_start_frame": 0, "out_end_frame": 48}
        tr_next = {"source_id": "B", "src_in_sec": 0.2, "src_out_sec": 1.8, "out_start_frame": 48, "out_end_frame": 96}
        hv = footage_effects.validate_transition_handles(tr_short_tail, tr_next, requested_frames=8, source_durations_sec={"A": 2.0, "B": 2.0}, fps=24.0)
        self.assertTrue(hv["fallback_to_cut"], hv)
        self.assertEqual(hv["effective_frames"], 0)

        # Dialogue-safe speed ramp locks master dialogue strictly linear
        self.assertAlmostEqual(
            footage_effects.resolve_speed_ramp_pts(u_norm=0.25, src_in_sec=0.0, src_out_sec=4.0, ramp_curve="ease_in_out", is_master_dialogue=True),
            1.0, places=5,
        )

    def test_dx01_05_remotion_typecheck_real_alpha_render_cache_and_fallback(self) -> None:
        """DX01-06: Strict tsc --noEmit, real transparent ProRes 4444 Remotion render + cache hit + honest disabled fallback."""
        tc = remotion_adapter.typecheck_remotion_bridge()
        self.assertTrue(tc["pass"], tc)
        probe = remotion_adapter.probe_remotion_runtime()
        self.assertTrue(probe["ready"], f"Expected local offline Remotion runtime ready: {probe}")

        with tempfile.TemporaryDirectory(prefix="dx01_rem_") as td:
            td_p = Path(td)
            src = _make_clip(td_p / "a.mp4", dur=1.5)
            sreg = semantic_timeline.build_source_registry({"source_id": "primary_aroll", "path": str(src)})
            fx = [{
                "event_id": "rem_test_01",
                "effect_id": "dx01.typo.kinetic_headline",
                "start_sec": 0.05,
                "end_sec": 1.35,
                "title": "Measured Flow",
                "emphasis_words": ["Flow"],
                "x_norm": 0.08, "y_norm": 0.14, "w_norm": 0.84,
            }]
            spec = semantic_timeline.compile_semantic_timeline(
                job_id="dx01_rem_test", source_registry=sreg,
                effect_invocations=fx, semantic_overlays=[],
                width=360, height=640, fps_num=24, fps_den=1,
                bgm_enabled=False, sfx_enabled=False, x264_preset="veryfast", crf=22,
                pass_through_allowed=True,
            )
            # 1) Hybrid Remotion real render + isolated alpha QA
            shared_cache = Path(tempfile.gettempdir()) / "seecut_dx01_remotion_test_cache"
            out_hyb = td_p / "hybrid.mp4"
            res_hyb = independent_renderer.render_independent_video(
                spec, out_mp4=out_hyb, stems_dir=td_p / "stems_hyb",
                enable_remotion=True, remotion_cache_root=shared_cache, remotion_concurrency=1,
            )
            self.assertEqual(res_hyb["engine_kind"], "HYBRID_REMOTION_FFMPEG")
            self.assertTrue(res_hyb["remotion_used"])
            self.assertEqual(res_hyb["remotion_span_count"], 1)
            qa_hyb = independent_qa.run_independent_render_qa(
                run_id="dx01_rem_qa", run_dir=td_p / "qa_hyb", final_mp4=out_hyb,
                render_spec=spec, render_receipt=res_hyb,
                mode="INDEPENDENT_PREVIEW", license_status="AUTHORIZED_HYBRID_REMOTION",
                run_post_asr=False,
            )
            self.assertTrue(qa_hyb["technical_pass"], qa_hyb["gate_matrix"])
            self.assertTrue(qa_hyb["remotion_verified"])
            self.assertTrue(qa_hyb["isolated_motion_proof"]["pass"])

            # 2) Cache hit on identical second span render
            rec2 = remotion_adapter.render_remotion_effect_span(
                {
                    "span_id": res_hyb["remotion_receipts"][0]["span_id"],
                    "width": 360, "height": 640, "fps": 24,
                    "duration_in_frames": res_hyb["remotion_receipts"][0]["duration_in_frames"],
                    "span_start_sec": 0.0, "transparent": True,
                    "palette": {}, "events": spec["effect_invocations"], "seed": 20261126,
                },
                cache_root=shared_cache, concurrency=1,
            )
            self.assertTrue(Path(rec2["output_path"]).is_file())

            # 3) Remotion disabled -> honest fallback to INDEPENDENT_FFMPEG_PIL (never claims HYBRID_REMOTION!)
            out_fb = td_p / "fallback.mp4"
            res_fb = independent_renderer.render_independent_video(
                spec, out_mp4=out_fb, stems_dir=td_p / "stems_fb",
                enable_remotion=False, allow_remotion_fallback=True,
            )
            self.assertEqual(res_fb["engine_kind"], "INDEPENDENT_FFMPEG_PIL")
            self.assertFalse(res_fb["remotion_used"])
            self.assertGreaterEqual(len(res_fb["fallback_events"]), 1)

    def test_dx01_06_negative_isolated_motion_tampered_receipt_and_seam_failure(self) -> None:
        """DX01-10 Negative Tests: tampered Remotion receipt SHA fails; static/missing PIL fails; unverified seam FAILS CONTINUITY_QA."""
        with tempfile.TemporaryDirectory(prefix="dx01_neg_") as td:
            td_p = Path(td)
            fake_mov = td_p / "fake.mov"
            fake_mov.write_bytes(b"not a real video")
            spec_min = {"fps_num": 24, "fps_den": 1, "overlays": [], "art_tokens": {}}

            # Tampered Remotion receipt SHA must fail closed!
            iso_bad_sha = independent_qa.verify_isolated_effect_motion(
                render_spec=spec_min,
                remotion_receipts=[{"span_id": "s1", "output_path": str(fake_mov), "output_sha256": "0" * 64, "duration_in_frames": 12}],
            )
            self.assertFalse(iso_bad_sha["pass"])
            self.assertTrue(any("TAMPERED_REMOTION_RECEIPT_SHA" in f for f in iso_bad_sha["failures"]))

            # Missing Remotion span file must fail closed!
            iso_miss = independent_qa.verify_isolated_effect_motion(
                render_spec=spec_min,
                remotion_receipts=[{"span_id": "s2", "output_path": str(td_p / "missing.mov"), "output_sha256": "a" * 64, "duration_in_frames": 12}],
            )
            self.assertFalse(iso_miss["pass"])

            # Unverified continuity seam MUST fail CONTINUITY_QA.pass (no longer ignored!)
            src = _make_clip(td_p / "a.mp4", dur=1.5)
            sreg = semantic_timeline.build_source_registry({"source_id": "primary_aroll", "path": str(src)})
            spec_seam = semantic_timeline.compile_semantic_timeline(
                job_id="neg_seam", source_registry=sreg, semantic_overlays=[],
                continuity_seams=[{"seam_id": "unverified_cut_01", "out_time_sec": 0.75, "treatment": "UNVERIFIED_HARD_JUMP"}],
                width=360, height=640, bgm_enabled=False, sfx_enabled=False, x264_preset="veryfast", crf=22,
                pass_through_allowed=True,
            )
            out_s = td_p / "seam.mp4"
            res_s = independent_renderer.render_independent_video(spec_seam, out_mp4=out_s, stems_dir=td_p / "stems", enable_remotion=False)
            qa_s = independent_qa.run_independent_render_qa(
                run_id="neg_seam_qa", run_dir=td_p / "qa_seam", final_mp4=out_s,
                render_spec=spec_seam, render_receipt=res_s, mode="INDEPENDENT_FULL",
                run_post_asr=False,
            )
            self.assertFalse(qa_s["continuity_qa"]["pass"], "Unverified seam MUST fail CONTINUITY_QA!")
            self.assertFalse(qa_s["technical_pass"])
            self.assertEqual(qa_s["audio_qa"]["post_render_asr_status"], "NOT_VERIFIED")

    def test_dx01_07_candidate_structural_dedup_distinct_defaults_and_plan_binding(self) -> None:
        """DX01-08: Color-only duplicate candidate is skipped with reason; distinct defaults render & bind normalized SHA cleanly."""
        with tempfile.TemporaryDirectory(prefix="dx01_cand_") as td:
            root = Path(td)
            src = _make_clip(root / "a.mp4", dur=1.5)
            rdir = root / "run_dedup"
            router.run_recommend(
                video_path=src, run_root=rdir, script_text="Test candidate structural dedup.",
                layout_policy="ADAPTIVE_DYNAMIC", intended_purpose="COMMERCIAL_MARKETING",
                license_decision="BLOCKED_COMMERCIAL_LICENSE",
            )
            router.run_select(run_root=rdir, skill_id="SK-04", approval_source="EXPLICIT_OWNER_CLI", approval_evidence="DX01 cand test")
            # Supply cand_A and cand_B that differ ONLY in event_id & color palette -> cand_B must be skipped as duplicate!
            ov = {
                "run_post_asr": False,
                "bgm_enabled": False,
                "sfx_enabled": False,
                "candidate_variants": [
                    {
                        "candidate_id": "cand_A", "label": "A", "strategy": "S1",
                        "art_tokens": {"accent_hex": "#FFAA00"},
                        "overlays": [{"event_id": "e1", "kind": "context_chip", "start_sec": 0.2, "end_sec": 1.3, "position": "top", "title": "Identical Geometry"}],
                    },
                    {
                        "candidate_id": "cand_B", "label": "B", "strategy": "S2_COLOR_CLONE",
                        "art_tokens": {"accent_hex": "#00FFAA"},
                        "overlays": [{"event_id": "e2", "kind": "context_chip", "start_sec": 0.2, "end_sec": 1.3, "position": "top", "title": "Identical Geometry"}],
                    },
                    {
                        "candidate_id": "cand_C", "label": "C", "strategy": "S3_DISTINCT",
                        "overlays": [{"event_id": "e3", "kind": "context_chip", "start_sec": 0.2, "end_sec": 1.3, "position": "bottom", "title": "Bottom Geometry"}],
                    },
                ],
            }
            exe = router.run_execute(
                run_root=rdir, mode="full", renderer="independent_clean_room",
                compete_candidates=True, max_candidates=3, workers=2,
                independent_spec_overrides=ov, enable_tracking=False,
            )
            self.assertEqual(exe["exit_code"], 0, exe)
            self.assertEqual(len(exe["candidates"]), 2, "Duplicate cand_B must be deduplicated!")
            self.assertEqual(len(exe["skipped_duplicate_candidates"]), 1)
            self.assertEqual(exe["skipped_duplicate_candidates"][0]["candidate_id"], "cand_B")
            self.assertEqual(
                exe["provisional_binding"]["normalized_creative_plan_sha256"],
                exe["normalized_creative_plan_sha256"],
            )
            vf = router.run_verify(run_root=rdir, require_render=True)
            self.assertEqual(vf["exit_code"], 0, vf)

    def test_dx01_08_silent_policy_and_multi_aspect_ratios(self) -> None:
        """UNI-01: Supports 16:9 landscape, 1:1 square, and explicit silent_policy (no-audio) media cleanly."""
        with tempfile.TemporaryDirectory(prefix="dx01_uni_") as td:
            root = Path(td)
            for tag, sz, w, h, freq in (
                ("landscape_16x9", "640x360", 640, 360, 440),
                ("square_1x1_silent", "480x480", 480, 480, None),
            ):
                src = _make_clip(root / f"{tag}.mp4", size=sz, dur=1.2, freq=freq)
                sreg = semantic_timeline.build_source_registry({"source_id": "primary_aroll", "path": str(src)})
                spec = semantic_timeline.compile_semantic_timeline(
                    job_id=f"uni_{tag}", source_registry=sreg,
                    semantic_overlays=[{"event_id": "ov1", "kind": "context_chip", "start_sec": 0.15, "end_sec": 1.0, "position": "top", "title": tag.replace("_", " ").title()}],
                    width=w, height=h, bgm_enabled=False, sfx_enabled=False,
                    x264_preset="veryfast", crf=22,
                )
                if freq is None:
                    spec["silent_policy"] = True
                    spec["spec_sha256"] = hash_artifact({k: v for k, v in spec.items() if k != "spec_sha256"})
                out_p = root / f"{tag}_out.mp4"
                res = independent_renderer.render_independent_video(spec, out_mp4=out_p, stems_dir=root / f"stems_{tag}", enable_remotion=False)
                qa = independent_qa.run_independent_render_qa(
                    run_id=f"qa_{tag}", run_dir=root / f"qa_{tag}", final_mp4=out_p,
                    render_spec=spec, render_receipt=res, mode="INDEPENDENT_PREVIEW", run_post_asr=False,
                )
                self.assertTrue(qa["technical_pass"], f"{tag} failed: {qa['gate_matrix']}")
                self.assertEqual(res["width"], w)
                self.assertEqual(res["height"], h)


if __name__ == "__main__":
    unittest.main()
