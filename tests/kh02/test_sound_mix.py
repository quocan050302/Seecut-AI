"""KH-02.R1 Executable Tests R1-15..R1-19: Multi-Layer Soundscape, Adaptive Density, Stem Render, Onset & Dry-vs-Mix QA."""

from __future__ import annotations

import math
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from scripts.kh02.audio_mix import (
    align_cue_to_event,
    compare_dry_and_mix,
    measure_stems,
    mix_audio_checked,
    plan_soundscape,
    render_stems,
    resolve_density_policy,
    schedule_ducking,
    validate_audio_graph,
    verify_mix_sync,
)
from scripts.kh02.contracts import hash_artifact
from scripts.kh02.sfx_assets import measure_onset, normalize_provider_result, probe_sfx


def _make_pcm_spans(
    path: Path,
    spans: list[tuple[float, float, float, float]],
    *,
    dur: float = 4.0,
    sr: int = 48000,
    channels: int = 2,
) -> Path:
    """Create deterministic sample-exact TEST_ONLY PCM WAV from (start, end, freq, amp) spans."""
    n = int(round(dur * sr))
    buf = bytearray()
    for i in range(n):
        t = i / float(sr)
        s = 0.0
        for a, b, freq, amp in spans:
            if a <= t <= b:
                s += amp * math.sin(2.0 * math.pi * freq * t)
        val = max(-32767, min(32767, int(s * 32767.0)))
        if channels == 2:
            buf += struct.pack("<hh", val, val)
        else:
            buf += struct.pack("<h", val)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))
    return path


def _make_dummy_video(path: Path, *, dur: float = 4.0, fps: int = 24) -> Path:
    pr = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s=320x568:r={fps}:d={dur}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if pr.returncode != 0:
        raise RuntimeError(f"Video fixture error: {pr.stderr}")
    return path


def _mock_shotbook(mode: str = "product_demo", profile: str = "editorial_pro", total_frames: int = 96) -> dict:
    return {
        "schema_version": "kh02.shotbook.v2",
        "run_id": "sound_test",
        "mode": mode,
        "profile": profile,
        "status": "TEST_ONLY",
        "source_sha_set": {"voice_01": "a" * 64},
        "output_spec": {"width": 720, "height": 1280, "fps_num": 24, "fps_den": 1, "total_frames": total_frames, "audio_sample_rate": 48000, "orientation": "portrait", "platform_safe_zone": {"top": 0.08, "bottom": 0.12, "left": 0.04, "right": 0.04}},
        "edit_decision_map": {"voice_policy": "LOCK_VOICE"},
        "beats": [
            {"beat_id": "b1", "start_frame": 0, "end_frame": 32, "semantic_function": "hook", "intentional_hold": False},
            {"beat_id": "b2", "start_frame": 32, "end_frame": 64, "semantic_function": "evidence", "intentional_hold": True if profile == "minimal_documentary" else False},
            {"beat_id": "b3", "start_frame": 64, "end_frame": 96, "semantic_function": "payoff", "intentional_hold": False},
        ],
        "visual_events": [
            {"event_id": "ev_01", "beat_id": "b1", "capability_id": "r1.kinetic_phrase_mask", "start_frame": 6, "end_frame": 30, "params": {"kind": "kinetic_phrase_mask"}},
            {"event_id": "ev_02", "beat_id": "b2", "capability_id": "r1.clip_mask_wipe", "start_frame": 36, "end_frame": 62, "params": {"kind": "clip_mask_wipe"}},
            {"event_id": "ev_03", "beat_id": "b3", "capability_id": "r1.light_sweep_badge", "start_frame": 72, "end_frame": 92, "params": {"kind": "light_sweep_badge"}},
        ],
        "approved_exceptions": [],
    }


def _wrap_test_asset(p: Path, cat: str, aid: str) -> dict:
    pr = probe_sfx(p)
    on = measure_onset(p)
    return normalize_provider_result(
        {"provider_asset_id": aid, "category": cat, "license_name": "CC0 1.0", "license_evidence": "TEST_ONLY_SYNTH_UNIT"},
        provider="test_synth", probe_info=pr, onset_info=on, relative_cache_path=p.name,
    )


class TestR1SoundAndMix(unittest.TestCase):
    def test_r1_15_cue_onset_alignment_prelap_clamp_stale_sha_and_dups(self) -> None:
        """R1-15: measured onset alignment, t=0 prelap clamp, stale plan SHA rejection, duplicate cue ID rejection."""
        fake_asset = {"asset_id": "sfx_w1", "sha256": "c" * 64, "license_status": "APPROVED_CC0", "license_evidence": "cc0", "duration_sec": 0.5, "onset_sec": 0.06, "peak_db": -6.0}
        cue0 = align_cue_to_event(
            cue_id="c_early", beat_id="b1", visual_event_id="ev_01", visual_trigger_frame=0,
            role="transition_whoosh", asset=fake_asset, fps_num=24, total_frames=96,
        )
        self.assertEqual(cue0["intended_onset_sample"], 0)
        self.assertTrue(cue0["intentionally_early"])
        self.assertAlmostEqual(cue0["trim_start_sec"], 0.06, places=3)

        plan = {"plan_sha256": "old_sha", "cues": [cue0, dict(cue0)]}
        errs = validate_audio_graph(plan, total_frames=96, fps_num=24, expected_plan_sha256="new_sha")
        codes = {e["code"] for e in errs}
        self.assertIn("STALE_SOUND_PLAN_SHA", codes)
        self.assertIn("DUPLICATE_CUE_ID", codes)

    def test_r1_16_adaptive_density_calm_vs_fast_and_upstream_conflict_logging(self) -> None:
        """R1-16: adaptive density across calm vs kinetic, zero artificial min-count, upstream 00-总纲 conflict documented."""
        d_calm = resolve_density_policy(mode="documentary", profile="minimal_documentary")
        d_fast_no_exc = resolve_density_policy(mode="product_demo", profile="editorial_pro", energy_mode="kinetic")
        d_fast_exc = resolve_density_policy(
            mode="product_demo", profile="editorial_pro", energy_mode="kinetic",
            approved_exceptions=[{"rule_id": "ALLOW_R1_ADAPTIVE_SFX_DENSITY", "reason": "Fast ad", "approved_by": "owner"}],
        )
        self.assertTrue(d_calm["no_artificial_min_count"])
        self.assertLess(d_calm["effective_max_per_3s"], d_fast_exc["effective_max_per_3s"])
        self.assertEqual(d_fast_no_exc["effective_max_per_3s"], 2)
        self.assertIsNotNone(d_fast_no_exc["upstream_legacy_conflict"])
        self.assertEqual(d_fast_exc["effective_max_per_3s"], 4)

        sb_calm = _mock_shotbook("documentary", "minimal_documentary")
        sp_calm = plan_soundscape(sb_calm, [], voice_asset_id="voice_01", sfx_policy="OPTIONAL_LICENSED")
        ev2_cov = next(c for c in sp_calm["event_coverage"] if c["event_id"] == "ev_02")
        self.assertEqual(ev2_cov["decision"], "SILENCE")

    def test_r1_17_voice_lock_ducking_true_peak_and_missing_analyzer(self) -> None:
        """R1-17: voice lock & ducking envelope attack/release bounds, true peak ceiling, missing analyzer -> NOT_VERIFIED."""
        duck = schedule_ducking([(0.2, 1.2), (2.2, 3.4)], total_duration_sec=4.0, attack_ms=45.0, release_ms=260.0)
        self.assertGreaterEqual(duck["attack_ms"], 20.0)
        self.assertLessEqual(duck["attack_ms"], 80.0)
        self.assertGreaterEqual(duck["release_ms"], 150.0)
        self.assertLessEqual(duck["release_ms"], 400.0)
        self.assertEqual(duck["merged_speech_windows"], 2)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            voice_wav = _make_pcm_spans(root / "voice.wav", [(0.2, 1.2, 220.0, 0.30), (2.2, 3.4, 260.0, 0.30)], dur=4.0)
            bgm_w = _make_pcm_spans(root / "bgm.wav", [(0.0, 2.0, 140.0, 0.18)], dur=2.0)
            a_bgm = _wrap_test_asset(bgm_w, "bgm", "bgm1")
            sb = _mock_shotbook("product_demo", "editorial_pro", total_frames=96)
            plan = plan_soundscape(
                sb, [], voice_asset_id="voice_01", speech_spans=[(0.2, 1.2), (2.2, 3.4)],
                sfx_policy="OFF", bgm_policy="OPTIONAL_LICENSED", bgm_asset=a_bgm, production_mode=False,
            )
            rendered = render_stems(
                plan, dry_voice_source=voice_wav, asset_paths={a_bgm["asset_id"]: bgm_w},
                stems_dir=root / "stems", total_frames=96, fps_num=24, production_mode=False,
            )
            metrics = measure_stems(rendered["stems"])
            self.assertTrue(metrics["mix"]["measured"])
            self.assertFalse(metrics["mix"]["clipping_detected"])
            self.assertLessEqual(metrics["mix"]["true_peak_dbtp"], -0.9)

            dvm = compare_dry_and_mix(rendered["stems"]["dry_voice"], rendered["stems"]["mix"], [(0.2, 1.2), (2.2, 3.4)])
            self.assertTrue(dvm["pass"], dvm["issues"])

            bad_qa = verify_mix_sync(plan, [], sfx_stem_wav=rendered["stems"]["sfx"], mix_wav=root / "nope.wav", fps_num=24)
            self.assertFalse(bad_qa["pass"])
            self.assertEqual(bad_qa["status"], "NOT_VERIFIED_MISSING_ANALYZER")

    def test_r1_18_real_pcm_multi_layer_stems_mux_and_onset_sync(self) -> None:
        """R1-18: real multi-layer TEST_ONLY PCM stem fixture (voice+sfx+ambience+music), MP4 mux & onset sync QA."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            voice_wav = _make_pcm_spans(root / "voice.wav", [(0.2, 1.2, 220.0, 0.30), (2.2, 3.4, 260.0, 0.30)], dur=4.0)
            sfx_w = _make_pcm_spans(root / "whoosh.wav", [(0.03, 0.25, 650.0, 0.40)], dur=0.35)
            sfx_c = _make_pcm_spans(root / "chime.wav", [(0.02, 0.28, 1100.0, 0.35)], dur=0.35)
            amb_w = _make_pcm_spans(root / "amb.wav", [(0.0, 2.0, 90.0, 0.02)], dur=2.0)
            bgm_w = _make_pcm_spans(root / "bgm.wav", [(0.0, 2.0, 140.0, 0.18)], dur=2.0)
            vid_mp4 = _make_dummy_video(root / "video.mp4", dur=4.0, fps=24)

            a_w = _wrap_test_asset(sfx_w, "whoosh", "w1")
            a_c = _wrap_test_asset(sfx_c, "chime", "c1")
            a_amb = _wrap_test_asset(amb_w, "ambience", "amb1")
            a_bgm = _wrap_test_asset(bgm_w, "bgm", "bgm1")

            sb = _mock_shotbook("product_demo", "editorial_pro", total_frames=96)
            plan = plan_soundscape(
                sb, [a_w, a_c],
                voice_asset_id="voice_01",
                speech_spans=[(0.2, 1.2), (2.2, 3.4)],
                sfx_policy="OPTIONAL_LICENSED",
                bgm_policy="OPTIONAL_LICENSED",
                bgm_asset=a_bgm,
                ambience_asset=a_amb,
                production_mode=False,
            )
            asset_paths = {a_w["asset_id"]: sfx_w, a_c["asset_id"]: sfx_c, a_amb["asset_id"]: amb_w, a_bgm["asset_id"]: bgm_w}
            rendered = render_stems(
                plan, dry_voice_source=voice_wav, asset_paths=asset_paths,
                stems_dir=root / "stems", total_frames=96, fps_num=24, production_mode=False,
            )
            onset_qa = verify_mix_sync(
                plan, rendered["applied_cues"],
                sfx_stem_wav=rendered["stems"]["sfx"], mix_wav=rendered["stems"]["mix"],
                fps_num=24, audio_sample_rate=48000,
            )
            self.assertTrue(onset_qa["pass"], onset_qa["issues"])
            self.assertGreaterEqual(len(onset_qa["cue_checks"]), 2)

            mux = mix_audio_checked(vid_mp4, rendered["stems"]["mix"], root / "final_mux.mp4", expected_duration_sec=4.0)
            self.assertTrue(mux["ok"])
            self.assertLess(mux["av_drift_sec"], 0.08)

    def test_r1_19_hash_mismatch_missing_asset_and_test_only_blocked_in_production(self) -> None:
        """R1-19: asset hash drift, missing asset, and TEST_ONLY fixtures strictly fail production graph validation."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = _make_pcm_spans(root / "sfx.wav", [(0.0, 0.25, 500.0, 0.2)], dur=0.3)
            plan = {
                "plan_sha256": "abc",
                "cues": [{
                    "cue_id": "c1", "kind": "sfx_pop", "frame_start": 5, "frame_end": 15,
                    "intended_onset_sample": 10000, "gain_db": -14.0,
                    "asset_id": "a1", "source_sha256": "deadbeef" * 8,
                    "license_ref": "APPROVED_CC0", "status": "VERIFIED_ASSET",
                }],
            }
            errs = validate_audio_graph(plan, total_frames=96, fps_num=24, asset_paths={"a1": p}, production_mode=True)
            self.assertTrue(any(e["code"] == "CUE_ASSET_HASH_MISMATCH" for e in errs))

            errs_miss = validate_audio_graph(plan, total_frames=96, fps_num=24, asset_paths={}, production_mode=True)
            self.assertTrue(any(e["code"] == "CUE_ASSET_MISSING" for e in errs_miss))

            plan_test = {
                "plan_sha256": "abc",
                "cues": [{
                    "cue_id": "c2", "kind": "sfx_pop", "frame_start": 5, "frame_end": 15,
                    "intended_onset_sample": 10000, "gain_db": -14.0,
                    "asset_id": "a1", "source_sha256": hash_artifact(p),
                    "license_ref": "TEST_ONLY_FIXTURE", "status": "VERIFIED_ASSET",
                }],
            }
            errs_test = validate_audio_graph(plan_test, total_frames=96, fps_num=24, asset_paths={"a1": p}, production_mode=True)
            self.assertTrue(any(e["code"] == "CUE_FORBIDDEN_LICENSE" for e in errs_test))


if __name__ == "__main__":
    unittest.main()
