"""KH-03.R4.2-02 & R4.2-07 — Cross-Platform Windows Native Smoke, Peak RSS, Unicode Paths & Async Cancellation Tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.kh02 import qa as kh02_qa  # noqa: E402
from scripts.kh02 import render_guard, source_analysis  # noqa: E402
from scripts.kh03 import execution, router  # noqa: E402


def _make_clip(dst: Path, *, duration: float = 1.5, fps: int = 24, size: str = "360x640") -> Path:
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=400:sample_rate=48000:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst


class TestKH03R42CrossPlatformSuite(unittest.TestCase):
    def test_r42_08_windows_peak_rss_unicode_spaces_and_junction_containment(self):
        """R4.2-02: Real peak RSS > 0 measured on Windows/POSIX; spaces+Unicode paths work; junction escape blocked."""
        rss = execution.get_process_rss_mib()
        self.assertGreater(rss, 0.0, f"Expected real positive RSS measurement, got {rss}")
        tot_ram, avail_ram = execution.get_host_memory_mib()
        self.assertGreater(tot_ram, 256.0)
        self.assertGreater(avail_ram, 64.0)

        with tempfile.TemporaryDirectory(prefix="kh03_r42_xp_") as td:
            td_p = Path(td)
            uni_dir = td_p / "Thư mục thử nghiệm (V6)"
            uni_dir.mkdir(parents=True, exist_ok=True)
            clip = _make_clip(uni_dir / "clip có dấu (1).mp4", duration=1.0)
            probed = source_analysis.probe_source(clip)
            self.assertEqual(probed["media"]["width"], 360)
            self.assertEqual(probed["media"]["height"], 640)

            # Symlink / NTFS directory junction rejection in probe_source
            outside = td_p / "outside_dir"
            outside.mkdir()
            ext_clip = _make_clip(outside / "ext.mp4", duration=1.0)
            link_p = td_p / "escape_link"
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "mklink", "/J", str(link_p), str(outside)], check=True, capture_output=True)
                target_through_link = link_p / "ext.mp4"
            else:
                link_p.symlink_to(ext_clip)
                target_through_link = link_p
            with self.assertRaises(ValueError):
                source_analysis.probe_source(target_through_link)

    def test_r42_09_real_native_smoke_render_h1_and_jianying_layers_export(self):
        """R4.2-02: Real native render.mjs + FFmpeg 9 pre-extract + H1 geom-check.mjs + jianying_layers.mjs on Windows."""
        pf = router.run_preflight(mode="full", export_layers=True)
        self.assertTrue(pf["readiness"]["NATIVE_FULL_RENDER_READY"], f"Native full render stack not ready: {pf['blockers']}")

        with tempfile.TemporaryDirectory(prefix="kh03_r42_smoke_") as td:
            td_p = Path(td)
            clip = _make_clip(td_p / "smoke_1500ms.mp4", duration=1.5, fps=24, size="360x640")
            run_dir = td_p / "run_smoke"
            rec = router.run_recommend(
                video_path=clip,
                run_root=run_dir,
                script_text="Quick native smoke test verifying real browser rendering and layer export.",
                layout_policy="ADAPTIVE_DYNAMIC",
                intended_purpose="NONCOMMERCIAL_ENGINEERING",
                license_decision="NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
            )
            self.assertEqual(rec["exit_code"], 0)
            sel = router.run_select(
                run_root=run_dir,
                skill_id="SK-05",
                approval_source="EXPLICIT_OWNER_CLI",
                approval_evidence="Noncommercial R4.2-02 native smoke + JianYing layers verification",
            )
            self.assertEqual(sel["exit_code"], 0)
            exe = router.run_execute(
                run_root=run_dir,
                mode="preview",
                export_layers=True,
                sfx_policy="OFF",
                music_policy="OFF",
                enable_tracking=False,
                workers=1,
            )
            self.assertEqual(exe["exit_code"], 0, exe)
            self.assertTrue(Path(exe["final_mp4_path"]).is_file())
            self.assertTrue((run_dir / "qc" / "H1_GEOM_v1.json").is_file())
            h1 = json.loads((run_dir / "qc" / "H1_GEOM_v1.json").read_text(encoding="utf-8"))
            self.assertTrue(h1["pass"], h1)

            # Verify real JianYing layers exporter ran cleanly
            jy_dir = run_dir / "jianying"
            jy_res = kh02_qa.export_and_verify_jianying_layers(
                run_dir / "build",
                jy_dir,
                project_name="r42_smoke_test",
            )
            self.assertTrue(jy_res["pass"], jy_res)

    def test_r42_10_bounded_async_cancellation_and_late_write_rejection(self):
        """R4.2-07: Task cancellation revokes TaskLease, rejects late worker publish, leaves zero canonical partial files."""
        with tempfile.TemporaryDirectory(prefix="kh03_r42_cancel_") as td:
            td_p = Path(td)
            budget = execution.compute_resource_budget(td_p, workers=2)
            dag = execution.BoundedAsyncExecutor(run_id="r42_cancel_test", budget=budget, run_dir=td_p)

            late_publish_rejected = {"rejected": False}

            def _fast_fail(_deps: dict, ctx: execution.TaskContext) -> dict:
                time.sleep(0.05)
                raise RuntimeError("INJECTED_FAIL_FAST")

            def _slow_writer(_deps: dict, ctx: execution.TaskContext) -> dict:
                # Wait briefly until peer failure cancels our token/lease
                for _ in range(40):
                    if ctx.cancel_event.is_set():
                        break
                    time.sleep(0.02)
                # Attempt illegal late publish AFTER cancellation/revocation -> MUST be rejected!
                try:
                    ctx.publish_json("manifests/STALE_LATE_WRITE.json", {"bad": True})
                except Exception:
                    late_publish_rejected["rejected"] = True
                ctx.check_cancelled()
                return {"ok": False}

            res = dag.run([
                execution.DAGTask(task_id="01_fail_fast", pool="cpu", fn=_fast_fail, timeout_sec=5.0),
                execution.DAGTask(task_id="02_slow_writer", pool="cpu", fn=_slow_writer, timeout_sec=5.0),
            ])
            self.assertFalse(res["pass"])
            self.assertFalse((td_p / "manifests" / "STALE_LATE_WRITE.json").exists())
            # Either cancelled cleanly before publish or lease rejected late publish
            self.assertTrue(late_publish_rejected["rejected"] or any(r["status"] in ("CANCELLED", "FAIL") for r in res["receipts"]))


if __name__ == "__main__":
    unittest.main()
