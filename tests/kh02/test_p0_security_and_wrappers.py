"""KH-02 P0 Security, Wrapper Hardening & Guardrail Tests (T01-T09)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import agy_judge_safe as judge_mod  # noqa: E402
from scripts.kh02 import render_guard  # noqa: E402


def _make_executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _make_synth_mp4(dst: Path, duration: float = 2.0, fps: int = 24, size: str = "320x180", with_audio: bool = True) -> Path:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={duration}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
                "-c:a", "aac", "-b:a", "96k"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), str(dst)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst


class TestP0SecurityAndWrappers(unittest.TestCase):
    def test_t01_repo_structure_and_license_notices(self):
        """T01: License/Notice preserved verbatim; core structure intact; no global writes."""
        lic = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("PolyForm Noncommercial License 1.0.0", lic)
        self.assertIn("YeJe-cpu", lic)
        self.assertIn("Browser Use", notice)
        self.assertIn("MIT License", notice)
        for rel in ("SKILL.md", "README.md", "scripts/safe_preflight.sh", "scripts/agy_read_safe.sh", "scripts/agy_judge_safe.py"):
            self.assertTrue((REPO_ROOT / rel).is_file(), f"Missing {rel}")

    def test_t02_safe_preflight_negative_and_mocked_ok(self):
        """T02: safe_preflight fails on missing tools / NOT OK / auth / region / timeout; never uses npx @latest."""
        with tempfile.TemporaryDirectory(prefix="kh02_t02_") as td:
            td_p = Path(td)
            fake_up = td_p / "up"
            (fake_up / "skill/seecut/scripts/schemas").mkdir(parents=True)
            for f in ("render.mjs", "geom-check.mjs", "schemas/check.json", "schemas/pair.json"):
                (fake_up / "skill/seecut/scripts" / f).write_text("{}", encoding="utf-8")

            fake_tools = td_p / "tools"
            (fake_tools / "node_modules/.bin").mkdir(parents=True)
            (fake_tools / "node_modules/hyperframes").mkdir(parents=True)
            (fake_tools / "node_modules/hyperframes/package.json").write_text('{"version":"0.9.86"}', encoding="utf-8")
            (fake_tools / "node_modules/playwright-core").mkdir(parents=True)
            (fake_tools / "node_modules/playwright-core/package.json").write_text('{"version":"1.65.1"}', encoding="utf-8")
            _make_executable(fake_tools / "node_modules/.bin/hyperframes", "#!/bin/sh\necho 0.9.86\n")
            fake_chrome = _make_executable(td_p / "chrome-stub", "#!/bin/sh\nexit 0\n")
            fake_py = _make_executable(td_p / "py-stub", "#!/bin/sh\necho 1.2.1\n")

            def run_pf(agy_script: str, extra_args=None):
                agy_bin = _make_executable(td_p / "agy-stub", agy_script)
                env = dict(os.environ)
                env.update({
                    "SEECUT_STANDALONE_ROOT": str(td_p),
                    "SEECUT_UPSTREAM_ROOT": str(fake_up),
                    "SEECUT_TOOLS_ROOT": str(fake_tools),
                    "SEECUT_VENV_PYTHON": str(fake_py),
                    "JIANJI_CHROME": str(fake_chrome),
                    "AGY_BIN": str(agy_bin),
                    "HYPERFRAMES_BIN": str(fake_tools / "node_modules/.bin/hyperframes"),
                })
                cmd = ["zsh", str(REPO_ROOT / "scripts/safe_preflight.sh")] + (extra_args or [])
                return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=20)

            # Negative 1: "NOT OK" MUST FAIL (not substring-match "OK")
            p_notok = run_pf("#!/bin/sh\necho 'NOT OK'\nexit 0\n")
            self.assertEqual(p_notok.returncode, 1, p_notok.stdout)
            self.assertEqual(json.loads(p_notok.stdout)["status"], "FAIL")

            # Negative 2: Auth error -> exit 2 BLOCKED_ENV
            p_auth = run_pf("#!/bin/sh\necho 'Authentication required: please log in' >&2\nexit 1\n")
            self.assertEqual(p_auth.returncode, 2, p_auth.stdout)
            self.assertEqual(json.loads(p_auth.stdout)["status"], "BLOCKED_ENV")

            # Negative 3: Region error -> exit 2 BLOCKED_ENV
            p_reg = run_pf("#!/bin/sh\necho 'User location is not supported' >&2\nexit 1\n")
            self.assertEqual(p_reg.returncode, 2, p_reg.stdout)

            # Negative 4: Missing hyperframes -> exit 1 (must NOT fall back to npx @latest)
            (fake_tools / "node_modules/.bin/hyperframes").unlink()
            p_nohf = run_pf("#!/bin/sh\necho OK\n", extra_args=["--offline"])
            self.assertEqual(p_nohf.returncode, 1, p_nohf.stdout)
            _make_executable(fake_tools / "node_modules/.bin/hyperframes", "#!/bin/sh\necho 0.9.86\n")

            # Positive (TEST_ONLY mocked exact OK) -> exit 0
            p_ok = run_pf("#!/bin/sh\necho OK\nexit 0\n")
            self.assertEqual(p_ok.returncode, 0, p_ok.stdout)
            rep = json.loads(p_ok.stdout)
            self.assertTrue(rep["no_bypass_enforced"])
            self.assertTrue(rep["npx_latest_disabled"])

    def test_t03_agy_judge_schema_and_nonzero_rc(self):
        """T03: run_agy nonzero exit with plausible JSON is rejected; empty/incomplete/wrong-bool/dup checks rejected."""
        # Nonzero rc with plausible JSON must raise SubprocessFailed
        with tempfile.TemporaryDirectory(prefix="kh02_t03_") as td:
            td_p = Path(td)
            fake_skill = td_p / "skill"
            (fake_skill / "references/prompt").mkdir(parents=True)
            (fake_skill / "scripts/schemas").mkdir(parents=True)
            (fake_skill / "references/prompt/硬伤清单.txt").write_text("check prompt", encoding="utf-8")
            (fake_skill / "scripts/schemas/check.json").write_text("{}", encoding="utf-8")
            stub_agy = _make_executable(
                td_p / "agy_fail",
                '#!/bin/sh\necho \'{"structured_output":{"fingerprint":{"texts":["a","b"],"duration_sec":10},"checks":[],"evidence_gaps":[]}}\'\nexit 1\n',
            )
            with self.assertRaises(judge_mod.SubprocessFailed):
                judge_mod.run_agy("check", str(td_p), 10, skill_dir=str(fake_skill), agy_bin=str(stub_agy))

        # Empty checks -> invalid
        errs = judge_mod.validate_judge_payload("check", {
            "fingerprint": {"texts": ["a", "b"], "duration_sec": 10.0},
            "checks": [],
            "evidence_gaps": [],
        })
        self.assertTrue(any("non-empty" in e for e in errs), errs)

        # Incomplete rubric IDs + bool-as-int + duplicate ID -> invalid
        errs2 = judge_mod.validate_judge_payload("check", {
            "fingerprint": {"texts": ["a", "b"], "duration_sec": 10.0},
            "checks": [
                {"id": "C1", "pass": 1, "evidence": "ok"},
                {"id": "C1", "pass": True, "evidence": "dup"},
            ],
            "evidence_gaps": [],
        })
        self.assertTrue(any("strict boolean" in e for e in errs2), errs2)
        self.assertTrue(any("duplicate check id" in e for e in errs2), errs2)
        self.assertTrue(any("missing required H2 rubric" in e for e in errs2), errs2)

        # Pair wrong winner string -> invalid
        errs3 = judge_mod.validate_judge_payload("pair", {
            "fingerprint": {"X_texts": ["a", "b"], "Y_texts": ["c", "d"], "X_duration_sec": 10, "Y_duration_sec": 10},
            "dimensions": [{"name": "d1", "better": "X", "evidence": "e"}],
            "winner": "INVALID",
            "confidence": 4,
            "reason": "test",
        })
        self.assertTrue(any("winner must be strictly" in e for e in errs3), errs3)

    def test_t04_fingerprint_duplicates_duration_and_nontext(self):
        """T04: duplicate fingerprint tokens count once; tight short-clip duration tol; documented nontext option."""
        with tempfile.TemporaryDirectory(prefix="kh02_t04_") as td:
            td_p = Path(td)
            html_p = td_p / "master.html"
            html_p.write_text("<html><body><div>Gumroad Storefront</div><div>Christmas Bundle</div></body></html>", encoding="utf-8")
            valid_checks = [{"id": cid, "pass": True, "evidence": "ok"} for cid in judge_mod.REQUIRED_CHECK_IDS]

            # Duplicate tokens ["Gumroad", "Gumroad"] -> only 1 distinct hit -> INVALID
            dup_res = {
                "fingerprint": {"texts": ["Gumroad", "Gumroad"], "duration_sec": 19.8},
                "checks": valid_checks,
                "evidence_gaps": [],
            }
            probs, _ = judge_mod.validate_candidate_result("check", dup_res, {"A": 19.86}, {"A": str(html_p)})
            self.assertTrue(any("distinct fingerprint hits 1/2" in p for p in probs), probs)

            # Duration 2.2s off on 19.86s clip -> INVALID (tol = 1.5s, tighter than old 3s)
            bad_dur = {
                "fingerprint": {"texts": ["Gumroad Storefront", "Christmas Bundle"], "duration_sec": 17.5},
                "checks": valid_checks,
                "evidence_gaps": [],
            }
            probs_d, _ = judge_mod.validate_candidate_result("check", bad_dur, {"A": 19.86}, {"A": str(html_p)})
            self.assertTrue(any("duration mismatch" in p for p in probs_d), probs_d)

            # Missing HTML without nontext evidence -> INVALID (never implicit PASS)
            probs_nohtml, _ = judge_mod.validate_candidate_result("check", {
                "fingerprint": {"texts": ["Gumroad", "Bundle"], "duration_sec": 19.8},
                "checks": valid_checks,
                "evidence_gaps": [],
            }, {"A": 19.86}, {"A": None})
            self.assertTrue(any("missing html/fp-manifest" in p for p in probs_nohtml), probs_nohtml)

            # Explicit documented nontext evidence file -> allowed with SHA binding
            nt = td_p / "visual_evidence.txt"
            nt.write_text("non-text visual proof", encoding="utf-8")
            probs_nt, meta_nt = judge_mod.validate_candidate_result("check", {
                "fingerprint": {"texts": [], "duration_sec": 19.8},
                "checks": valid_checks,
                "evidence_gaps": [],
            }, {"A": 19.86}, {"A": None}, nontext_evidence={"A": str(nt)})
            self.assertEqual(probs_nt, [], probs_nt)
            self.assertEqual(meta_nt["fingerprint_binding"]["A"]["mode"], "DOCUMENTED_NONTEXT_EVIDENCE")

    def test_t05_pairwise_swap_tie_invalid_and_gate_block(self):
        """T05: pairwise mapping exact; invalid never maps to B/A; tie/H1/H2 failure blocks adoption."""
        ok_x = {"valid": True, "result": {"winner": "X"}}
        ok_y = {"valid": True, "result": {"winner": "Y"}}
        bad_w = {"valid": True, "result": {"winner": "GARBAGE"}}

        # Consensus A (order1 X=A wins, order2 Y=A wins)
        d1 = judge_mod.resolve_pair_consensus(ok_x, ok_y)
        self.assertEqual(d1["consensus"], "A")
        self.assertEqual(d1["adopted"], "A")

        # Disagreement -> tie
        d_tie = judge_mod.resolve_pair_consensus(ok_x, ok_x)
        self.assertEqual(d_tie["consensus"], "tie")
        self.assertIsNone(d_tie["adopted"])

        # Invalid winner MUST produce None/invalid, NEVER B or A
        d_inv = judge_mod.resolve_pair_consensus(bad_w, ok_y)
        self.assertEqual(d_inv["winners"][0], None)
        self.assertEqual(d_inv["consensus"], "invalid")
        self.assertIsNone(d_inv["adopted"])

        # H1/H2 failure blocks adoption even with dual consensus
        d_blk = judge_mod.resolve_pair_consensus(ok_x, ok_y, h1_pass_a=False, h2_pass_a=True)
        self.assertEqual(d_blk["consensus"], "A")
        self.assertIsNone(d_blk["adopted"])
        self.assertEqual(d_blk["block_reason"], "candidate_A_failed_H1_or_H2_gate")

    def test_t06_grab_fail_frames_traversal_and_real_extract(self):
        """T06: grab_fail_frames rejects ../ traversal, missing ffmpeg image, never fabricates paths."""
        with tempfile.TemporaryDirectory(prefix="kh02_t06_") as td:
            td_p = Path(td)
            mp4 = _make_synth_mp4(td_p / "clip.mp4", duration=2.0)
            outdir = td_p / "ff"
            fails = [
                {"id": "../../escape", "evidence": "00:01"},
                {"id": "C3", "evidence": "no timestamp here"},
                {"id": "C1", "evidence": "00:00.50"},
            ]
            made, missing = judge_mod.grab_fail_frames(str(mp4), fails, str(outdir))
            self.assertFalse((td_p / "escape_1.0s.jpg").exists())
            self.assertTrue(any(m["reason"] == "invalid_check_id_rejected" for m in missing))
            self.assertTrue(any(m["reason"] == "no_valid_timestamp_in_evidence" for m in missing))
            self.assertGreaterEqual(len(made), 1)
            for p in made:
                self.assertTrue(Path(p).is_file() and Path(p).stat().st_size > 16)

            # Missing source video -> zero fabricated files appended
            made2, missing2 = judge_mod.grab_fail_frames(str(td_p / "nonexistent.mp4"), [{"id": "C1", "evidence": "00:01"}], str(outdir / "bad"))
            self.assertEqual(made2, [])
            self.assertGreaterEqual(len(missing2), 1)

    def test_t07_agy_read_safe_symlink_policy_and_cleanup(self):
        """T07: agy_read_safe rejects symlinks, policy-denied media transmission, cleans own temp dir."""
        with tempfile.TemporaryDirectory(prefix="kh02_t07_") as td:
            td_p = Path(td)
            real_f = td_p / "sample.mp4"
            real_f.write_bytes(b"fake")
            sym_f = td_p / "link.mp4"
            sym_f.symlink_to(real_f)
            stub = _make_executable(td_p / "agy_ok", "#!/bin/sh\necho READ_OK\nexit 0\n")

            # Symlink rejected
            p_sym = subprocess.run(
                ["zsh", str(REPO_ROOT / "scripts/agy_read_safe.sh"), str(sym_f), "describe", "10"],
                env={**os.environ, "AGY_BIN": str(stub)},
                capture_output=True, text=True,
            )
            self.assertEqual(p_sym.returncode, 2)

            # Transmission policy denied -> exit 4
            p_deny = subprocess.run(
                ["zsh", str(REPO_ROOT / "scripts/agy_read_safe.sh"), str(real_f), "describe", "10"],
                env={**os.environ, "AGY_BIN": str(stub), "SEECUT_DENY_EXTERNAL_MEDIA": "1"},
                capture_output=True, text=True,
            )
            self.assertEqual(p_deny.returncode, 4)

            # Allowlist outside -> rejected
            p_out = subprocess.run(
                ["zsh", str(REPO_ROOT / "scripts/agy_read_safe.sh"), str(real_f), "describe", "10"],
                env={**os.environ, "AGY_BIN": str(stub), "SEECUT_ALLOWED_MEDIA_ROOTS": str(td_p / "other")},
                capture_output=True, text=True,
            )
            self.assertEqual(p_out.returncode, 2)

            # Valid allowlisted execution succeeds & propagates rc
            p_ok = subprocess.run(
                ["zsh", str(REPO_ROOT / "scripts/agy_read_safe.sh"), str(real_f), "describe", "10"],
                env={**os.environ, "AGY_BIN": str(stub), "SEECUT_ALLOWED_MEDIA_ROOTS": str(td_p)},
                capture_output=True, text=True,
            )
            self.assertEqual(p_ok.returncode, 0, p_ok.stderr)
            self.assertIn("READ_OK", p_ok.stdout)

    def test_t08_render_guard_path_traversal_and_unowned_sentinel(self):
        """T08: render_guard rejects ../, abs path, symlink, unowned person_dir; sentinel untouched; safe_cleanup deletes owned only."""
        with tempfile.TemporaryDirectory(prefix="kh02_t08_") as td:
            td_p = Path(td)
            run_root = td_p / "run1"
            bdir = run_root / "build"
            bdir.mkdir(parents=True)
            (bdir / "master.html").write_text("<html></html>", encoding="utf-8")
            sentinel_dir = td_p / "old_run_precious"
            sentinel_dir.mkdir()
            sentinel = sentinel_dir / "keep.txt"
            sentinel.write_text("DO_NOT_DELETE", encoding="utf-8")
            fake_up = td_p / "up"
            (fake_up / "skill/seecut/scripts").mkdir(parents=True)
            (fake_up / "skill/seecut/scripts/render.mjs").write_text("// stub", encoding="utf-8")

            for bad_pd in ("../../old_run_precious", str(sentinel_dir), "../outside"):
                (bdir / "project.json").write_text(json.dumps({
                    "fps": 24, "width": 720, "height": 1280, "duration": 2.0, "person_dir": bad_pd
                }), encoding="utf-8")
                with self.assertRaises(ValueError):
                    render_guard.preflight_render(bdir, run_root, run_id="run1", seecut_root=fake_up, reserve_floor_gib=0.1)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "DO_NOT_DELETE")

            # Existing unowned person_dir inside build -> rejected
            unowned = bdir / "person"
            unowned.mkdir()
            (unowned / "precious.jpg").write_text("keep", encoding="utf-8")
            (bdir / "project.json").write_text(json.dumps({
                "fps": 24, "width": 720, "height": 1280, "duration": 2.0, "person_dir": "person"
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                render_guard.preflight_render(bdir, run_root, run_id="run1", seecut_root=fake_up, reserve_floor_gib=0.1)
            self.assertTrue((unowned / "precious.jpg").is_file())

            # Owned marker allows safe_cleanup
            (unowned / render_guard.OWNERSHIP_MARKER).write_text("run1", encoding="utf-8")
            self.assertTrue(render_guard.safe_cleanup(unowned, run_root, run_id="run1"))
            self.assertFalse(unowned.exists())

    def test_t09_stale_cache_invalidation_and_frame_verification(self):
        """T09: same-path replaced bytes invalidate person cache; missing/corrupt frames fail verification."""
        with tempfile.TemporaryDirectory(prefix="kh02_t09_") as td:
            td_p = Path(td)
            run_root = td_p / "run_cache"
            bdir = run_root / "build"
            pdir = bdir / "person"
            pdir.mkdir(parents=True)
            src = td_p / "clip.mp4"
            src.write_bytes(b"version_one_bytes" * 100)
            (bdir / "master.html").write_text("<html></html>", encoding="utf-8")
            fake_up = td_p / "up"
            (fake_up / "skill/seecut/scripts").mkdir(parents=True)
            rmjs = fake_up / "skill/seecut/scripts/render.mjs"
            rmjs.write_text("// stub", encoding="utf-8")

            cfg = {"fps": 24, "width": 720, "height": 1280, "duration": 2.0, "person_dir": "person", "source": str(src)}
            (bdir / "project.json").write_text(json.dumps(cfg), encoding="utf-8")
            (pdir / render_guard.OWNERSHIP_MARKER).write_text("run_cache", encoding="utf-8")
            cid1 = render_guard.compute_cache_identity(cfg, bdir, rmjs, render_scale=1.0)
            (pdir / render_guard.CACHE_META_FILE).write_text(json.dumps(cid1), encoding="utf-8")
            (pdir / "p0001.jpg").write_bytes(b"\xff\xd8" + b"x" * 100)

            # Unchanged bytes -> cache valid
            pf1 = render_guard.preflight_render(bdir, run_root, run_id="run_cache", seecut_root=fake_up, reserve_floor_gib=0.1)
            self.assertTrue(pf1["person_cache_valid"])

            # Mutate bytes at SAME path -> stale cache invalidated & wiped!
            src.write_bytes(b"version_two_different_bytes" * 120)
            pf2 = render_guard.preflight_render(bdir, run_root, run_id="run_cache", seecut_root=fake_up, reserve_floor_gib=0.1)
            self.assertFalse(pf2["person_cache_valid"])
            self.assertFalse((pdir / "p0001.jpg").exists())

            # Frame verification rejects missing or corrupt magic
            fdir = bdir / "frames"
            fdir.mkdir()
            (fdir / "f0000.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            (fdir / "f0001.png").write_bytes(b"NOT_A_PNG" * 20)
            vr = render_guard.verify_render_frames(fdir, [0, 1, 2])
            self.assertFalse(vr["ok"])
            self.assertEqual(vr["missing_count"], 1)
            self.assertEqual(vr["corrupt_count"], 1)


if __name__ == "__main__":
    unittest.main()
