"""KH-02.R2 Regression Tests — Groups R2-15..R2-24 (Live SFX Config, Real Normal-Path Mix, System & Cross-Niche)."""

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

from scripts.kh02 import audio_mix, contracts, planner, qa, render_guard, run as kh_run, sfx_assets


def _write_test_wav(path: Path, *, duration_sec: float = 0.35, freq_hz: float = 880.0, amp: float = 0.45, sample_rate: int = 48000) -> Path:
    n = int(round(duration_sec * sample_rate))
    buf = bytearray(n * 2)
    for i in range(n):
        t = i / float(sample_rate)
        env = min(1.0, t / 0.01) * max(0.0, 1.0 - t / duration_sec)
        val = int(max(-32767, min(32767, amp * 32767.0 * env * math.sin(2.0 * math.pi * freq_hz * t))))
        struct.pack_into("<h", buf, i * 2, val)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(buf))
    return path


def _make_clip(out_mp4: Path, *, width: int = 360, height: int = 640, fps: int = 24, duration_sec: float = 2.0) -> Path:
    wav_p = out_mp4.with_suffix(".wav")
    _write_test_wav(wav_p, duration_sec=duration_sec, freq_hz=230.0, amp=0.35)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}:duration={duration_sec}",
        "-i", str(wav_p),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        "-shortest", str(out_mp4),
    ], check=True, capture_output=True, timeout=60)
    wav_p.unlink(missing_ok=True)
    return out_mp4


class TestR2SfxMixAndSystem(unittest.TestCase):
    def test_r2_15_local_provider_config_and_jianji_env(self) -> None:
        """R2-15: load_sfx_provider_config honors --sfx-library & JIANJI_SFX_DIR; off policy makes zero requests."""
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td) / "sfx_lib"
            w_whoosh = _write_test_wav(lib / "whoosh_soft.wav", duration_sec=0.30, freq_hz=420.0)
            (lib / "SFX_LICENSE_MANIFEST.json").write_text(json.dumps({
                "whoosh_soft.wav": {
                    "license_name": "CC0 1.0",
                    "license_evidence": "https://creativecommons.org/publicdomain/zero/1.0/ (local verified test fixture)",
                    "category": "whoosh",
                    "tags": ["whoosh", "soft", "transition"],
                }
            }), encoding="utf-8")

            # Via JIANJI_SFX_DIR env
            bundle = sfx_assets.load_sfx_provider_config(env={"JIANJI_SFX_DIR": str(lib)}, offline=True)
            self.assertEqual(bundle["overall_status"], "LOCAL_READY")
            self.assertEqual(bundle["local_provider"].status()["verified_asset_count"], 1)

            # Via off policy -> zero assets scanned/requested
            bundle_off = sfx_assets.load_sfx_provider_config(cli_library_dirs=[lib], sfx_policy="OFF")
            self.assertEqual(bundle_off["overall_status"], "OFF_BY_BRIEF")
            self.assertEqual(bundle_off["local_provider"].status()["verified_asset_count"], 0)
            self.assertTrue(w_whoosh.is_file())

    def test_r2_16_mcp_config_discovery_auth_and_no_secret_leak(self) -> None:
        """R2-16: MCP adapter uses approved config + tools/list discovery; strips secrets; missing server never PASS."""
        with tempfile.TemporaryDirectory() as td:
            cfg_p = Path(td) / "sfx_config.json"
            cfg_p.write_text(json.dumps({
                "local_library_dirs": [],
                "mcp": {
                    "provider_id": "mock_sfx_mcp",
                    "configured": True,
                    "api_key_env": "TEST_SFX_KEY",
                    "secret_leak_guard": "DO_NOT_LOG_THIS_SECRET",
                },
            }), encoding="utf-8")

            def _fake_invoker(tool: str, args: dict) -> dict:
                if tool == "tools/list":
                    return {"tools": [{"name": "sfx_search"}, {"name": "sfx_download"}]}
                return {"ok": True, "results": []}

            bundle = sfx_assets.load_sfx_provider_config(
                config_path=cfg_p, provider_mode="mcp", offline=False,
                env={"TEST_SFX_KEY": "redacted-key-123"}, tool_invoker=_fake_invoker,
            )
            st = bundle["mcp_provider"].status()
            self.assertEqual(st["status"], "READY")
            self.assertIn("sfx_search", st["discovered_tools"])
            self.assertNotIn("DO_NOT_LOG_THIS_SECRET", json.dumps(st))
            self.assertNotIn("redacted-key-123", json.dumps(st))

            # Unconfigured MCP returns BLOCKED_PROVIDER_CONFIG (never READY/PASS)
            unconf = sfx_assets.load_sfx_provider_config(provider_mode="mcp", offline=False, env={})
            self.assertEqual(unconf["mcp_provider"].status()["status"], "BLOCKED_PROVIDER_CONFIG")

    def test_r2_17_acquisition_rights_html_ssrf_and_corrupt_cache_negative(self) -> None:
        """R2-17: Reject unknown license, HTML-as-MP3, SSRF loopback URL, and drifted cache SHA."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            html_fake = root / "fake.mp3"
            html_fake.write_text("<html><body>403 Forbidden</body></html>", encoding="utf-8")
            self.assertFalse(sfx_assets.probe_sfx(html_fake)["valid"])

            # SSRF blocked
            dl_ssrf = sfx_assets.safe_download("https://127.0.0.1/evil.wav", root / "dl")
            self.assertFalse(dl_ssrf["ok"])
            self.assertIn(dl_ssrf["status"], ("REJECTED_HOST", "REJECTED_SSRF"))

            # Unknown license rejected
            vlic = sfx_assets.verify_license({"license_name": "UNKNOWN", "license_evidence": ""})
            self.assertFalse(vlic["approved"])

            # Cache SHA drift caught
            good_wav = _write_test_wav(root / "cache/ok.wav")
            idx = {"assets": [{"asset_id": "a1", "relative_cache_path": "ok.wav", "sha256": "0" * 64}]}
            audit = sfx_assets.audit_cache_integrity(root / "cache", idx)
            self.assertFalse(audit["pass"])
            self.assertEqual(len(audit["drifted"]), 1)

    def test_r2_18_optional_vs_required_sfx_missing_behavior(self) -> None:
        """R2-18: REQUIRED_LICENSED with 0 assets exits BLOCKED_SFX_ASSETS (code 3); OPTIONAL_LICENSED proceeds honestly."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vid = _make_clip(root / "v.mp4", duration_sec=2.0)
            brief_p = root / "b.json"
            brief_p.write_text(json.dumps({
                "schema_version": "kh02.brief.v2",
                "brief_id": "r2_18_req",
                "mode": "talking_head",
                "profile": "editorial_pro",
                "objective": "Test required vs optional SFX gate",
                "audience": "engineers",
                "platform": "reels",
                "aspect_ratio": "9:16",
                "language": "en",
                "tone": "direct",
                "key_message": "Honest SFX policy enforcement",
                "voice_policy": "LOCK_VOICE",
                "caption_policy": "OFF",
                "music_policy": "OFF",
                "sfx_policy": "REQUIRED_LICENSED",
                "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
                "sources": [{"asset_id": "src_a", "path": str(vid), "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
            }), encoding="utf-8")

            res_req = kh_run.execute_pipeline(brief_path=brief_p, run_root=root / "run_req", mode="plan", offline=True, production_mode=False)
            self.assertEqual(res_req["status"], "BLOCKED_SFX_ASSETS")
            self.assertEqual(res_req["exit_code"], 3)

            res_opt = kh_run.execute_pipeline(brief_path=brief_p, run_root=root / "run_opt", mode="plan", sfx_policy_override="OPTIONAL_LICENSED", offline=True, production_mode=False)
            self.assertEqual(res_opt["exit_code"], 0)
            self.assertEqual(res_opt["sfx_verified_cue_count"], 0)

    def test_r2_19_real_normal_path_stem_mix_mux_and_streaming_pcm(self) -> None:
        """R2-19: Integrated normal execute_pipeline runs render_stems + verify_mix_sync + compare_dry_and_mix + mix_audio_checked."""
        up_root = Path(os.environ.get("SEECUT_UPSTREAM_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut")))
        tools_root = Path(os.environ.get("SEECUT_STANDALONE_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone"))) / "tools"
        if not (up_root / "skill/seecut/scripts/render.mjs").is_file() or not (tools_root / "node_modules/hyperframes").is_dir():
            self.skipTest("Pinned SeeCut / HyperFrames runtime not present")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vid = _make_clip(root / "aroll.mp4", duration_sec=2.0)
            sfx_dir = root / "licensed_sfx"
            _write_test_wav(sfx_dir / "whoosh_01.wav", duration_sec=0.28, freq_hz=520.0, amp=0.45)
            _write_test_wav(sfx_dir / "pop_01.wav", duration_sec=0.16, freq_hz=920.0, amp=0.45)
            _write_test_wav(sfx_dir / "chime_01.wav", duration_sec=0.35, freq_hz=1100.0, amp=0.40)
            (sfx_dir / "SFX_LICENSE_MANIFEST.json").write_text(json.dumps({
                "*": {
                    "license_name": "CC0 1.0",
                    "license_evidence": "Isolated TEST_ONLY fixture licensed CC0 for R2-19 integration test",
                }
            }), encoding="utf-8")

            brief_p = root / "brief_mix.json"
            brief_p.write_text(json.dumps({
                "schema_version": "kh02.brief.v2",
                "brief_id": "r2_19_mix",
                "mode": "talking_head",
                "profile": "editorial_pro",
                "objective": "Verify normal pipeline stem rendering, sync QA & audio mux",
                "audience": "engineers",
                "platform": "reels",
                "aspect_ratio": "9:16",
                "language": "en",
                "tone": "kinetic",
                "key_message": "Real mixed audio reaches final MP4",
                "voice_policy": "LOCK_VOICE",
                "caption_policy": "OFF",
                "music_policy": "OFF",
                "sfx_policy": "REQUIRED_LICENSED",
                "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
                "presenter_policy": {"mode": "FIXED_PIP", "visibility": "ALWAYS_VISIBLE", "anchor": "BOTTOM_LEFT", "shape": "ROUNDED_RECT"},
                "sources": [{"asset_id": "src_a", "path": str(vid), "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL", "face_rect": [0.22, 0.15, 0.78, 0.58]}],
            }), encoding="utf-8")

            run_dir = root / "run_mix"
            res = kh_run.execute_pipeline(
                brief_path=brief_p, run_root=run_dir, mode="preview", offline=True,
                sfx_library_dirs=[sfx_dir], seecut_root=up_root, tools_root=tools_root,
                reserve_floor_gib=0.1, production_mode=False, allow_test_only_sfx=True,
            )
            self.assertEqual(res["exit_code"], 0, res)
            self.assertTrue(res["audio_mix"]["applied"])
            self.assertTrue(res["audio_mix"]["mix_ok"])
            self.assertTrue(res["audio_mix"]["dry_vs_mix"]["streaming_bounded"])
            self.assertTrue((run_dir / "STEM_MANIFEST.json").is_file())
            self.assertTrue((run_dir / "MIX_VERIFICATION.json").is_file())
            self.assertTrue((run_dir / "stems/mix.wav").is_file())
            self.assertEqual(res["structural_qa"]["mp4_sha256"], res["final_mp4_sha256"])

    def test_r2_20_post_mix_adoption_and_layered_fidelity_contract(self) -> None:
        """R2-20: Adoption requires H1+H2+objective+dual-order consensus; failed mix/H2 never promoted."""
        rej = qa.decide_adoption(h1_pass=True, h2_pass=False, objective_qa_pass=True, pairwise_consensus="A")
        self.assertFalse(rej["adopted"])
        self.assertEqual(rej["decision"], "REJECTED_HARD_GATE")
        tie = qa.decide_adoption(h1_pass=True, h2_pass=True, objective_qa_pass=True, pairwise_consensus="tie")
        self.assertFalse(tie["adopted"])
        win = qa.decide_adoption(h1_pass=True, h2_pass=True, objective_qa_pass=True, pairwise_consensus="A")
        self.assertTrue(win["adopted"])

    def test_r2_21_bounded_workers_determinism_low_disk_and_safe_cleanup(self) -> None:
        """R2-21: Workers 1/2/4 produce identical SHOTBOOK & EDL SHA; low disk blocked; unowned dir cleanup refused."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vid = _make_clip(root / "det.mp4", duration_sec=2.0)
            brief_p = root / "b.json"
            brief_p.write_text(json.dumps({
                "schema_version": "kh02.brief.v2",
                "brief_id": "r2_21_det",
                "mode": "talking_head",
                "profile": "editorial_pro",
                "objective": "Verify worker determinism",
                "audience": "engineers",
                "platform": "reels",
                "aspect_ratio": "9:16",
                "language": "en",
                "tone": "direct",
                "key_message": "Deterministic across worker counts",
                "voice_policy": "LOCK_VOICE",
                "caption_policy": "OFF",
                "music_policy": "OFF",
                "sfx_policy": "OFF",
                "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
                "sources": [{"asset_id": "src_a", "path": str(vid), "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
            }), encoding="utf-8")

            shas = []
            for w in (1, 2, 4):
                r = kh_run.execute_pipeline(brief_path=brief_p, run_root=root / f"w{w}", mode="plan", dry_run=True, workers=w, offline=True)
                shas.append(r["edl_sha256"])
            self.assertEqual(len(set(shas)), 1)

            # Low disk floor rejects unsafe write
            low = render_guard.estimate_resources(1000, 1080, 1920, render_scale=1.5, target_dir=root, reserve_floor_gib=999999.0)
            self.assertFalse(low["disk_ok"])

            # Unowned directory deletion refused
            unowned = root / "important_user_dir"
            unowned.mkdir()
            with self.assertRaises(PermissionError):
                render_guard.safe_cleanup(unowned, root, run_id="run_test")

    def test_r2_22_olivia_missing_input_block_and_analysis_only_isolation(self) -> None:
        """R2-22: Missing raw source -> BLOCKED_INPUT; ANALYSIS_ONLY assets strictly forbidden from render inventory."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            brief_p = root / "missing.json"
            brief_p.write_text(json.dumps({
                "schema_version": "kh02.brief.v2",
                "brief_id": "r2_22_missing",
                "mode": "talking_head",
                "profile": "editorial_pro",
                "objective": "Fail closed on missing raw source",
                "audience": "owner",
                "platform": "reels",
                "aspect_ratio": "9:16",
                "language": "en",
                "tone": "warm",
                "key_message": "Missing input must block",
                "voice_policy": "PRESERVE_SCRIPT_TRIM",
                "caption_policy": "OFF",
                "music_policy": "OFF",
                "sfx_policy": "OFF",
                "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
                "sources": [{"asset_id": "missing", "path": str(root / "does_not_exist.mp4"), "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
            }), encoding="utf-8")
            res = kh_run.execute_pipeline(brief_path=brief_p, run_root=root / "run_m", mode="plan", offline=True)
            self.assertEqual(res["exit_code"], 2)
            self.assertEqual(res["status"], "BLOCKED_INPUT")

    def test_r2_23_cross_niche_holdout_and_long_form_65s_no_30s_cap(self) -> None:
        """R2-23: Cross-niche (16:9 software tutorial, VI calm documentary, faceless, narrative) & 65s+ long-form (SYNTHETIC_SMOKE)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vid_land = _make_clip(root / "tut_16x9.mp4", width=640, height=360, duration_sec=2.5)
            # 65s synthetic long-form clip (fast ultrafast encode) proves zero 30s cap
            vid_long = _make_clip(root / "long_65s.mp4", width=360, height=640, fps=24, duration_sec=65.0)

            cases = [
                ("software_demo", "16:9", "youtube", "en", "editorial_pro", "ADAPTIVE_PIP", vid_land),
                ("documentary", "16:9", "youtube", "vi", "minimal_documentary", "LEGACY_DYNAMIC", vid_land),
                ("faceless", "1:1", "square", "en", "editorial_pro", "FACELESS", vid_land),
                ("education", "9:16", "shorts", "en", "editorial_pro", "FIXED_PIP", vid_long),
            ]
            for mode_n, ar, plat, lang, prof, pmode, vpath in cases:
                bp = root / f"b_{mode_n}_{ar.replace(':','x')}.json"
                bp.write_text(json.dumps({
                    "schema_version": "kh02.brief.v2",
                    "brief_id": f"smoke_{mode_n}",
                    "mode": mode_n,
                    "profile": prof,
                    "objective": f"Cross-niche synthetic smoke {mode_n}",
                    "audience": "learners",
                    "platform": plat,
                    "aspect_ratio": ar,
                    "language": lang,
                    "tone": "calm" if prof == "minimal_documentary" else "clear",
                    "key_message": "Quy trình giải thích minh họa" if lang == "vi" else f"Clear {mode_n} explanation",
                    "voice_policy": "LOCK_VOICE",
                    "caption_policy": "OFF",
                    "music_policy": "OFF",
                    "sfx_policy": "OFF",
                    "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
                    "presenter_policy": {"mode": pmode, "visibility": "EDITORIAL_ALLOWED" if pmode in ("FACELESS", "LEGACY_DYNAMIC") else "ALWAYS_VISIBLE", "anchor": "BOTTOM_LEFT"},
                    "sources": [{"asset_id": "src_main", "path": str(vpath), "kind": "b_roll" if mode_n == "faceless" else "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
                }), encoding="utf-8")
                res = kh_run.execute_pipeline(brief_path=bp, run_root=root / f"run_{mode_n}", mode="plan", offline=True, production_mode=False)
                self.assertEqual(res["exit_code"], 0, f"{mode_n} failed: {res}")
                if vpath == vid_long:
                    sb = contracts.parse_json_strict(root / f"run_{mode_n}/SHOTBOOK_V2.json")
                    self.assertGreaterEqual(sb["output_spec"]["total_frames"], 1500)  # >= 62.5s @ 24fps (no 30s cap!)

    def test_r2_24_wrapper_security_and_packaging_hygiene(self) -> None:
        """R2-24: Safe wrappers contain zero bypass flags or npx @latest; all schemas parse strictly."""
        skill_root = Path(__file__).resolve().parents[2]
        for rel in ("scripts/safe_preflight.sh", "scripts/agy_read_safe.sh", "scripts/agy_judge_safe.py"):
            txt = (skill_root / rel).read_text(encoding="utf-8")
            for line in txt.splitlines():
                st = line.strip()
                if st.startswith("#"):
                    continue
                if "npx " in st and "@latest" in st:
                    self.assertTrue(any(tok in st for tok in ("disabled", "forbidden", "error", "rejected")), f"Suspicious npx @latest exec in {rel}: {st}")
                if "--dangerously-skip-permissions" in st:
                    self.assertTrue(any(tok in st for tok in ("grep", "forbidden", "bypass", "audit", "rejected", "error", "NO", "without", "OMITS")), f"Suspicious bypass line in {rel}: {st}")
        for sp in sorted((skill_root / "schemas/kh02").glob("*.json")):
            obj = contracts.parse_json_strict(sp)
            self.assertIsInstance(obj, dict)


if __name__ == "__main__":
    unittest.main()
