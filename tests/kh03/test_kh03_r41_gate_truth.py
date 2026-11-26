"""KH-03.R4.1-02 — Fail-Closed Production QA, Mode-Aware Gate Registry & Commercial Receipt Hardening Suite.

Covers every negative and truthfulness contract in Section 05 & Section 02:
1. Mode-aware GATE_PROFILES (PLAN, ENGINEERING, NATIVE_PREVIEW, NATIVE_FULL, RELEASE).
2. aggregate_gate_results():
   - Missing mandatory gate -> BLOCKED_MISSING_GATE & technical_pass=False (NEVER skipped!).
   - Unjustified applicable=False on mandatory gate (G_FRAME_AUDIT, G_AUDIO_SYNC, G_LICENSE) -> FAIL!
   - Unknown/malformed gate status -> FAIL!
   - strict_evidence=True with missing evidence_sha256 -> BLOCKED!
   - Layout rhythm PENDING_REVIEW separates editorial_status='PENDING_REVIEW' & release_status='RELEASE_BLOCKED'.
3. orchestrate_final_render_qa():
   - Missing final MP4 returns complete non-KeyError schema with technical_pass=False.
   - Missing audio stream when expected FAILS G_AUDIO_SYNC (zero .get('pass', True) defaults!).
   - audio_mix.applied=True without mix_sync FAILS G_AUDIO_SYNC!
   - export_layers_required=True without jianying evidence FAILS G_EXPORT_QA!
   - mode='NATIVE_FULL' without H1/H2/Native proof -> BLOCKED (NEVER NOT_APPLICABLE!).
4. Commercial permission evidence hardening (PROBLEM-01):
   - Arbitrary free-form string (len >= 8) REJECTED!
   - Expired receipt, mismatched upstream pin, test_only=True, tampered receipt_sha256 REJECTED!
   - Valid structured non-expired signed receipt ACCEPTED!
5. run_verify(require_render=True) raw-gate recomputation:
   - Forging FINAL_GATE_MATRIX.json['technical_pass']=True when raw HOOK_PIXEL_QA.json failed OR
     dropping a mandatory gate MUST be caught by run_verify() (FORGED_GATE_PASS / VERIFY_FAIL)!
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.kh03 import UPSTREAM_SEECUT_SHA, contracts, qa, router


def _make_video(out_path: Path, *, with_audio: bool = True, dur: float = 2.0) -> Path:
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=0x162C22:s=270x480:r=24:d={dur}",
        "-f", "lavfi", "-i", f"color=c=0xF3DC96:s=85x58:r=24:d={dur}",
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={dur}"]
    cmd += ["-filter_complex", "[0:v][1:v]overlay=x='20+t*55':y=24[vout]", "-map", "[vout]"]
    if with_audio:
        cmd += ["-map", "2:a:0"]
    cmd += ["-t", str(dur), "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast"]
    if with_audio:
        cmd += ["-c:a", "aac", "-b:a", "96k"]
    cmd.append(str(out_path))
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def _signed_receipt(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "kh03.commercial_permission_receipt.v1",
        "licensor": "YeJe-cpu <aiesecxuanli@gmail.com>",
        "scope": "COMMERCIAL_MARKETING_SINGLE_CAMPAIGN",
        "upstream_pin": UPSTREAM_SEECUT_SHA,
        "issued_at_utc": "2026-11-01T00:00:00Z",
        "expires_at_utc": "2027-12-31T23:59:59Z",
        "written_authorization_ref": "EMAIL-AUTH-2026-1101-SIGNED",
        "verified_by_owner": True,
        "test_only": False,
    }
    if overrides:
        body.update(overrides)
    canonical = json.dumps({k: v for k, v in sorted(body.items()) if k != "receipt_sha256"}, sort_keys=True, separators=(",", ":"))
    body["receipt_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return body


class TestKH03R41GateTruthSuite(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kh03_r41_gate_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_r41_05_aggregate_gate_results_fail_closed_on_missing_or_waived_mandatory_gates(self) -> None:
        """Missing mandatory gate, unjustified applicable=False, unknown status, and missing evidence hash MUST NEVER PASS."""
        # 1. Missing mandatory gate G_FRAME_AUDIT in ENGINEERING mode -> BLOCKED_MISSING_GATE!
        partial_gates = {
            "G_LICENSE": {"status": "PASS", "applicable": True},
            "G_HOOK_PIXELS": {"status": "PASS", "applicable": True},
        }
        agg = qa.aggregate_gate_results(partial_gates, mode="ENGINEERING")
        self.assertFalse(agg["technical_pass"])
        self.assertIn("G_FRAME_AUDIT", agg["missing_gates"])
        self.assertEqual(agg["gates"]["G_FRAME_AUDIT"]["status"], "BLOCKED_MISSING_GATE")

        # 2. Unjustified applicable=False on G_AUDIO_SYNC (not in allow_na_gates!) -> FAIL!
        all_eng = {gid: {"status": "PASS", "applicable": True, "evidence_sha256": "a" * 64} for gid in qa.GATE_PROFILES["ENGINEERING"]["mandatory_gates"]}
        all_eng["G_AUDIO_SYNC"] = {"status": "NOT_APPLICABLE", "applicable": False, "reason": "skipped"}
        agg_na = qa.aggregate_gate_results(all_eng, mode="ENGINEERING")
        self.assertFalse(agg_na["technical_pass"], "Self-waiving G_AUDIO_SYNC must FAIL!")
        self.assertIn("G_AUDIO_SYNC", agg_na["failing_gates"])

        # 3. Unknown/malformed status -> FAIL!
        all_eng["G_AUDIO_SYNC"] = {"status": "MAYBE_OK", "applicable": True}
        agg_bad = qa.aggregate_gate_results(all_eng, mode="ENGINEERING")
        self.assertFalse(agg_bad["technical_pass"])
        self.assertIn("G_AUDIO_SYNC", agg_bad["failing_gates"])

        # 4. strict_evidence=True without evidence_sha256 -> BLOCKED!
        all_eng["G_AUDIO_SYNC"] = {"status": "PASS", "applicable": True}
        agg_ev = qa.aggregate_gate_results(all_eng, mode="ENGINEERING", strict_evidence=True)
        self.assertFalse(agg_ev["technical_pass"])
        self.assertIn("G_AUDIO_SYNC", agg_ev["blocked_gates"])

        # 5. Layout rhythm PENDING_REVIEW separates editorial & release status
        all_eng["G_AUDIO_SYNC"] = {"status": "PASS", "applicable": True, "evidence_sha256": "b" * 64}
        all_eng["G_LAYOUT_RHYTHM"] = {"status": "PENDING_REVIEW", "applicable": True, "evidence_sha256": "c" * 64}
        agg_ed = qa.aggregate_gate_results(all_eng, mode="ENGINEERING")
        self.assertEqual(agg_ed["editorial_status"], "PENDING_REVIEW")
        self.assertEqual(agg_ed["release_status"], "RELEASE_BLOCKED")

    def test_r41_06_orchestrate_final_render_qa_negative_and_mode_enforcement(self) -> None:
        """Verify missing MP4 schema, silent/missing audio fail, mix_sync required, export layers required, NATIVE_FULL H1/H2/Native block."""
        plan = {
            "selected_skill_id": "SK-04",
            "hook_plan": {"selected_hook_id": "h1", "hook_roi_norm": [0.05, 0.04, 0.90, 0.18], "truthful_no_spoiler_verified": True},
            "components": [{"component_id": "c1", "start_frame": 0, "end_frame": 40, "normalized_bbox": [0.05, 0.04, 0.90, 0.18]}],
        }

        # 1. Missing final MP4 returns complete consistent schema (no KeyError in callers!)
        res_miss = qa.orchestrate_final_render_qa(
            run_id="r41_miss", run_dir=self.tmp / "miss", final_mp4=self.tmp / "nonexistent.mp4",
            source_mp4=None, creative_plan=plan, mode="NATIVE_FULL",
        )
        self.assertFalse(res_miss["technical_pass"])
        self.assertIn("gate_matrix", res_miss)
        self.assertIn("flow_trace", res_miss)
        self.assertIn("render_proof", res_miss)
        self.assertIn("owner_approval", res_miss)
        self.assertEqual(res_miss["gate_matrix"]["owner_visual_approval"], "PENDING")

        # 2. Video with NO audio stream when audio expected MUST FAIL G_AUDIO_SYNC (no default pass=True!)
        no_audio_mp4 = _make_video(self.tmp / "no_audio.mp4", with_audio=False)
        res_noa = qa.orchestrate_final_render_qa(
            run_id="r41_noa", run_dir=self.tmp / "noa", final_mp4=no_audio_mp4,
            source_mp4=None, creative_plan=plan, kh02_result={}, mode="ENGINEERING",
        )
        self.assertFalse(res_noa["technical_pass"])
        self.assertIn("G_AUDIO_SYNC", res_noa["gate_matrix"]["failing_gates"])

        # 3. audio_mix.applied=True without mix_sync MUST FAIL; export_layers_required=True without jianying MUST FAIL!
        ok_mp4 = _make_video(self.tmp / "with_audio.mp4", with_audio=True)
        res_mix = qa.orchestrate_final_render_qa(
            run_id="r41_mix", run_dir=self.tmp / "mix", final_mp4=ok_mp4,
            source_mp4=None, creative_plan=plan,
            kh02_result={"audio_mix": {"applied": True}, "h1_geom": {"pass": True}},
            mode="ENGINEERING", export_layers_required=True,
        )
        self.assertFalse(res_mix["technical_pass"])
        self.assertIn("G_AUDIO_SYNC", res_mix["gate_matrix"]["failing_gates"])
        self.assertIn("G_EXPORT_QA", res_mix["gate_matrix"]["failing_gates"])

        # 4. NATIVE_FULL without H1, H2, or upstream seecut_root MUST mark H1/H2/Native BLOCKED (NOT NOT_APPLICABLE!)
        res_nat = qa.orchestrate_final_render_qa(
            run_id="r41_nat", run_dir=self.tmp / "nat", final_mp4=ok_mp4,
            source_mp4=None, creative_plan=plan,
            kh02_result={"audio_qa": {"pass": True}},
            mode="NATIVE_FULL", seecut_root=self.tmp / "missing_seecut",
        )
        self.assertFalse(res_nat["technical_pass"])
        gates_nat = res_nat["gate_matrix"]["gates"]
        self.assertEqual(gates_nat["G_H1_GEOM"]["status"], "BLOCKED")
        self.assertEqual(gates_nat["G_H2_JUDGE"]["status"], "BLOCKED")
        self.assertEqual(gates_nat["G_NATIVE_PROOF"]["status"], "BLOCKED")
        self.assertEqual(res_nat["gate_matrix"]["native_status"], "BLOCKED")

    def test_r41_07_commercial_permission_receipt_rejects_arbitrary_string_and_forged_receipts(self) -> None:
        """PROBLEM-01: Arbitrary string len>=8, expired receipt, wrong pin, test_only, tampered SHA MUST FAIL CLOSED."""
        # Arbitrary string (previously bypassed weak len>=8 check!)
        v_str = contracts.verify_commercial_permission_evidence("written_permission_ok_12345")
        self.assertFalse(v_str["verified"], "Arbitrary string must NEVER pass commercial permission verification!")

        # Expired receipt
        rec_exp = _signed_receipt({"expires_at_utc": "2020-01-01T00:00:00Z"})
        self.assertFalse(contracts.verify_commercial_permission_evidence(rec_exp)["verified"])

        # Wrong upstream pin
        rec_pin = _signed_receipt({"upstream_pin": "0000000000000000000000000000000000000000"})
        self.assertFalse(contracts.verify_commercial_permission_evidence(rec_pin)["verified"])

        # test_only=True receipt rejected
        rec_to = _signed_receipt({"test_only": True})
        self.assertFalse(contracts.verify_commercial_permission_evidence(rec_to)["verified"])

        # Tampered receipt_sha256 rejected
        rec_tamp = _signed_receipt()
        rec_tamp["scope"] = "EXPANDED_UNLIMITED_SCOPE"
        self.assertFalse(contracts.verify_commercial_permission_evidence(rec_tamp)["verified"])

        # Valid structured non-expired receipt passes
        rec_ok = _signed_receipt()
        v_ok = contracts.verify_commercial_permission_evidence(rec_ok)
        self.assertTrue(v_ok["verified"], v_ok)

        # Router run_recommend with COMMERCIAL_MARKETING + arbitrary string -> BLOCKED_COMMERCIAL_LICENSE!
        vid = _make_video(self.tmp / "comm_src.mp4")
        comm_dir = self.tmp / "comm_run"
        router.run_recommend(
            video_path=vid, run_root=comm_dir,
            script_text="Buy our digital planner today for better budgeting.",
            intended_purpose="COMMERCIAL_MARKETING",
            license_decision="WRITTEN_COMMERCIAL_PERMISSION_VERIFIED",
            commercial_permission_evidence="arbitrary_unverified_string_999",
            test_only=True,
        )
        inp_comm = json.loads((comm_dir / "analysis" / "INPUT_ANALYSIS.json").read_text(encoding="utf-8"))
        self.assertEqual(inp_comm["constraints"]["license_decision"], "BLOCKED_COMMERCIAL_LICENSE")
        # Even after selecting a skill, run_execute MUST block commercial execution with arbitrary string!
        router.run_select(
            run_root=comm_dir, skill_id="SK-04",
            approval_source="TEST_ONLY_FIXTURE", approval_evidence="R4.1 commercial block test", test_only=True,
        )
        ex_comm = router.run_execute(
            run_root=comm_dir, mode="plan", workers=1, production_mode=False,
            intended_purpose="COMMERCIAL_MARKETING",
            license_decision="WRITTEN_COMMERCIAL_PERMISSION_VERIFIED",
            commercial_permission_evidence="arbitrary_unverified_string_999",
        )
        self.assertEqual(ex_comm["status"], "BLOCKED_COMMERCIAL_LICENSE")
        self.assertNotEqual(ex_comm["exit_code"], 0)

    def test_r41_08_run_verify_recomputes_raw_gates_and_catches_forged_gate_matrix(self) -> None:
        """Manually flipping FINAL_GATE_MATRIX.json['technical_pass']=True when raw QA failed MUST fail run_verify!"""
        vid = _make_video(self.tmp / "verify_src.mp4", with_audio=True)
        run_d = self.tmp / "run_verify_tamper"
        router.run_recommend(
            video_path=vid, run_root=run_d,
            script_text="Step 1: measure. Step 2: verify every hash.",
            test_only=True,
        )
        sel = router.run_select(
            run_root=run_d, skill_id="SK-04",
            approval_source="TEST_ONLY_FIXTURE", approval_evidence="R4.1 gate truth tamper test selection", test_only=True,
        )
        self.assertEqual(sel["status"], "USER_SELECTED")
        ex = router.run_execute(run_root=run_d, mode="plan", workers=1, production_mode=False)
        self.assertEqual(ex["exit_code"], 0, ex)

        # Run real orchestrate_final_render_qa on a video that FAILS audio (no audio stream)
        (run_d / "exports").mkdir(parents=True, exist_ok=True)
        bad_mp4 = _make_video(run_d / "exports" / "final_bad.mp4", with_audio=False)
        cp = json.loads((run_d / "manifests" / "CREATIVE_PLAN.json").read_text(encoding="utf-8"))
        qa_res = qa.orchestrate_final_render_qa(
            run_id=run_d.name, run_dir=run_d, final_mp4=bad_mp4, source_mp4=vid,
            creative_plan=cp, kh02_result={}, mode="ENGINEERING",
        )
        # Bind STAGE_RECEIPTS/KH03_04_execute_and_qa.json to bad_mp4 so run_verify checks raw gates
        rec4_p = run_d / "STAGE_RECEIPTS" / "KH03_04_execute_and_qa.json"
        rec4 = json.loads(rec4_p.read_text(encoding="utf-8"))
        rec4.setdefault("output_hashes", {})["final_mp4_sha256"] = qa_res["final_mp4_sha256"]
        rec4.setdefault("evidence_paths", []).append(str(bad_mp4))
        rec4_p.write_text(json.dumps(rec4, indent=2), encoding="utf-8")

        # Now FORGE FINAL_GATE_MATRIX.json claiming technical_pass=True and all gates PASS!
        gm_p = run_d / "qc" / "FINAL_GATE_MATRIX.json"
        gm = json.loads(gm_p.read_text(encoding="utf-8"))
        gm["technical_pass"] = True
        gm["overall_technical_status"] = "TECHNICAL_PASS_PENDING_OWNER"
        gm["failing_gates"] = []
        gm["blocked_gates"] = []
        gm["gates"]["G_AUDIO_SYNC"] = {"status": "PASS", "applicable": True}
        gm_p.write_text(json.dumps(gm, indent=2), encoding="utf-8")

        ver = router.run_verify(run_root=run_d, production_mode=False, require_render=True)
        self.assertEqual(ver["status"], "VERIFY_FAIL", f"Forged FINAL_GATE_MATRIX.json MUST be caught by run_verify: {ver}")
        self.assertEqual(ver["exit_code"], 1)
        self.assertTrue(
            any("FORGED_GATE_PASS" in i or "TAMPERED_GATE_MATRIX" in i for i in ver["issues"]),
            ver["issues"],
        )


if __name__ == "__main__":
    unittest.main()
