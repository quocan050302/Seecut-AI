"""KH-03.R4.2-01 & R4.2-06 — Commercial License Hardening & Mode-Aware Preflight Readiness Tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.kh03 import UPSTREAM_SEECUT_SHA, contracts, router  # noqa: E402


def _make_receipt(overrides: dict | None = None) -> dict:
    doc = {
        "licensor": "YeJe-cpu (aiesecxuanli@gmail.com)",
        "scope": "COMMERCIAL_MARKETING_SEECUT_RENDERER_SINGLE_CAMPAIGN",
        "upstream_pin": UPSTREAM_SEECUT_SHA,
        "issued_at_utc": "2026-01-01T00:00:00Z",
        "expires_at_utc": "2029-12-31T23:59:59Z",
        "written_authorization_ref": "EMAIL-2026-01-01-YEJE-SIGNED-AUTH-001",
        "verified_by_owner": True,
    }
    if overrides:
        doc.update(overrides)
    doc["receipt_sha256"] = contracts.hash_artifact({k: v for k, v in doc.items() if k != "receipt_sha256"})
    return doc


class TestKH03R42ReadinessSuite(unittest.TestCase):
    def test_r42_01_license_verifier_strict_pin_dates_scope_attachment(self):
        """R4.2-01: Full 40-char pin, strict UTC dates, concrete scope, attachment SHA & manual verification honesty."""
        # Valid receipt with real attachment passes and explicitly discloses manual verification (not crypto signature!)
        with tempfile.TemporaryDirectory(prefix="kh03_r42_lic_") as td:
            td_p = Path(td)
            att = td_p / "yeje_written_auth.txt"
            att.write_text("Written commercial authorization email from aiesecxuanli@gmail.com\n", encoding="utf-8")
            att_sha = contracts.hash_artifact(att)

            good = _make_receipt({
                "authorization_document_path": str(att),
                "authorization_document_sha256": att_sha,
            })
            r_good = contracts.verify_commercial_permission_evidence(good)
            self.assertTrue(r_good["verified"], r_good)
            self.assertEqual(r_good["verification_method"], "MANUAL_OWNER_VERIFIED_RECEIPT_NOT_CRYPTOGRAPHIC_SIGNATURE")

            # Short 7-char upstream pin rejected when full 40-char pin is expected!
            r_short_pin = contracts.verify_commercial_permission_evidence(_make_receipt({"upstream_pin": UPSTREAM_SEECUT_SHA[:7]}))
            self.assertFalse(r_short_pin["verified"])
            self.assertIn("40-char", r_short_pin["reason"])

            # Invalid calendar date rejected (strict strptime)
            r_bad_date = contracts.verify_commercial_permission_evidence(_make_receipt({"issued_at_utc": "2026-02-31T00:00:00Z"}))
            self.assertFalse(r_bad_date["verified"])

            # expires <= issued rejected
            r_rev_dates = contracts.verify_commercial_permission_evidence(_make_receipt({
                "issued_at_utc": "2026-06-01T00:00:00Z",
                "expires_at_utc": "2026-06-01T00:00:00Z",
            }))
            self.assertFalse(r_rev_dates["verified"])

            # Vague scope without commercial/marketing/renderer rejected
            r_vague = contracts.verify_commercial_permission_evidence(_make_receipt({"scope": "general_video_project_usage"}))
            self.assertFalse(r_vague["verified"])

            # Attachment hash mismatch rejected
            r_att_bad = contracts.verify_commercial_permission_evidence(_make_receipt({
                "authorization_document_path": str(att),
                "authorization_document_sha256": "0" * 64,
            }))
            self.assertFalse(r_att_bad["verified"])

            # Missing attachment file rejected
            r_att_miss = contracts.verify_commercial_permission_evidence(_make_receipt({
                "authorization_document_path": str(td_p / "nonexistent.pdf"),
            }))
            self.assertFalse(r_att_miss["verified"])

            # Unverified owner rejected
            r_no_owner = contracts.verify_commercial_permission_evidence(_make_receipt({"verified_by_owner": False}))
            self.assertFalse(r_no_owner["verified"])

            # audit_license_and_dependencies emits all 4 required manifests and blocks commercial without receipt
            out_dir = td_p / "audit"
            aud = contracts.audit_license_and_dependencies(
                intended_purpose="COMMERCIAL_MARKETING",
                commercial_permission_evidence=None,
                out_dir=out_dir,
            )
            for fname in ("LICENSE_AUDIT.json", "DEPENDENCY_PROVENANCE.json", "RIGHTS_LEDGER.json", "COMMERCIAL_READINESS.json"):
                self.assertTrue((out_dir / fname).is_file(), f"Missing {fname}")
            self.assertEqual(aud["commercial_readiness"]["status"], "BLOCKED_COMMERCIAL_LICENSE")
            self.assertFalse(aud["commercial_readiness"]["path_b_bypass_permitted"])

    def test_r42_02_mode_aware_preflight_plan_preview_full_release(self):
        """R4.2-06: Preflight distinguishes PLAN_READY / NATIVE_PREVIEW_READY / NATIVE_FULL_RENDER_READY / RELEASE_READY."""
        with tempfile.TemporaryDirectory(prefix="kh03_r42_pf_") as td:
            td_p = Path(td)
            fake_up = td_p / "missing_seecut"
            fake_tools = td_p / "missing_tools"

            # Mode=plan passes when ffmpeg+ffprobe+disk present, even if native tools absent
            pf_plan = router.run_preflight(
                run_dir=td_p / "run_plan",
                mode="plan",
                seecut_root=fake_up,
                tools_root=fake_tools,
            )
            self.assertTrue(pf_plan["readiness"]["PLAN_READY"])
            self.assertFalse(pf_plan["readiness"]["NATIVE_PREVIEW_READY"])
            self.assertFalse(pf_plan["readiness"]["RELEASE_READY"])
            self.assertTrue(pf_plan["pass"])

            # Mode=preview with missing native tools + COMMERCIAL_MARKETING lists BOTH blockers simultaneously!
            pf_prev = router.run_preflight(
                mode="preview",
                intended_purpose="COMMERCIAL_MARKETING",
                seecut_root=fake_up,
                tools_root=fake_tools,
            )
            self.assertFalse(pf_prev["pass"])
            codes = {b["code"] for b in pf_prev["blockers"]}
            self.assertIn("MISSING_OR_DRIFTED_UPSTREAM_SEECUT", codes)
            self.assertIn("MISSING_NATIVE_RENDER_DEPS", codes)
            self.assertIn("BLOCKED_COMMERCIAL_LICENSE", codes)
            self.assertGreaterEqual(len(pf_prev["next_actions"]), 2)

            # Mode=release ALWAYS fails at preflight (requires post-render QA + owner playback approval!)
            pf_rel = router.run_preflight(mode="release")
            self.assertFalse(pf_rel["readiness"]["RELEASE_READY"])
            self.assertFalse(pf_rel["pass"])

            # Real CLI subprocess exits non-zero when requested mode is blocked
            proc = subprocess.run(
                [
                    sys.executable, "-m", "scripts.kh03", "preflight",
                    "--mode", "preview",
                    "--purpose", "COMMERCIAL_MARKETING",
                    "--seecut-root", str(fake_up),
                    "--tools-root", str(fake_tools),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(proc.returncode, 1, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertFalse(payload["pass"])
            self.assertFalse(payload["commercial_license_ok"])


if __name__ == "__main__":
    unittest.main()
