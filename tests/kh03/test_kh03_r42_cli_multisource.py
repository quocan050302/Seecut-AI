"""KH-03.R4.2-05 — Multi-Source CLI Exposure, Secondary Source Binding & Mixed-FPS Overhang Tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.kh03 import intelligence, router, selection  # noqa: E402


def _make_video(dst: Path, *, size: str = "360x640", fps: int = 24, v_dur: float = 2.0, a_dur: float | None = None, freq: int = 330) -> Path:
    ad = a_dur if a_dur is not None else v_dur
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={v_dur}",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={ad}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst


class TestKH03R42CliMultiSourceSuite(unittest.TestCase):
    def test_r42_03_cli_recommend_secondary_video_and_sources_manifest(self):
        """R4.2-05: CLI recommend binds --secondary-video and --sources-manifest with MUTE_DEFAULT & drift invalidation."""
        with tempfile.TemporaryDirectory(prefix="kh03_r42_ms_") as td:
            td_p = Path(td)
            src_a = _make_video(td_p / "primary_24fps.mp4", fps=24, v_dur=2.0, freq=260)
            src_b = _make_video(td_p / "secondary_30fps.mp4", fps=30, v_dur=2.0, freq=520)
            script_f = td_p / "script.txt"
            script_f.write_text("Step one check your dashboard. Step two compare both numbers.\n", encoding="utf-8")

            # 1) CLI --secondary-video repeatable flag
            run1 = td_p / "run_cli_flag"
            p_rec = subprocess.run(
                [
                    sys.executable, "-m", "scripts.kh03", "recommend",
                    "--video", str(src_a),
                    "--secondary-video", str(src_b),
                    "--script", str(script_f),
                    "--run-root", str(run1),
                    "--layout-policy", "ADAPTIVE_DYNAMIC",
                ],
                cwd=str(REPO_ROOT),
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(p_rec.returncode, 0, p_rec.stderr)
            inp1 = json.loads((run1 / "analysis" / "INPUT_ANALYSIS.json").read_text(encoding="utf-8"))
            self.assertEqual(len(inp1["secondary_sources"]), 1)
            self.assertEqual(inp1["secondary_sources"][0]["audio_policy"], "MUTE_DEFAULT")
            self.assertEqual(inp1["secondary_sources"][0]["fps_num"], 30)
            self.assertTrue(inp1.get("secondary_sources_sha256"))

            # Select skill via CLI -> verify SELECTION_RECEIPT binds secondary_sources_sha256
            p_sel = subprocess.run(
                [sys.executable, "-m", "scripts.kh03", "select", "--run-root", str(run1), "--skill", "SK-05"],
                cwd=str(REPO_ROOT),
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(p_sel.returncode, 0, p_sel.stderr)
            sel_doc = json.loads((run1 / "selection" / "SELECTION_RECEIPT.json").read_text(encoding="utf-8"))
            self.assertEqual(sel_doc["secondary_sources_sha256"], inp1["secondary_sources_sha256"])

            # Mutating secondary source B on disk MUST invalidate selection fail-closed!
            src_b.write_bytes(src_b.read_bytes() + b"\x00")
            with self.assertRaises(selection.SelectionGateError) as ctx:
                selection.validate_user_selection(run_dir=run1, production_mode=True)
            self.assertIn("FRESH_SECONDARY_HASH_MISMATCH", str(ctx.exception))

            # 2) CLI --sources-manifest JSON
            src_b2 = _make_video(td_p / "secondary_manifest.mp4", fps=30, v_dur=2.0, freq=600)
            manifest_f = td_p / "sources.json"
            manifest_f.write_text(json.dumps({
                "secondary_sources": [{
                    "source_id": "screen_demo_01",
                    "path": str(src_b2),
                    "role": "secondary_screen_evidence",
                    "audio_policy": "MUTE_DEFAULT",
                }]
            }), encoding="utf-8")
            run2 = td_p / "run_cli_manifest"
            p_rec2 = subprocess.run(
                [
                    sys.executable, "-m", "scripts.kh03", "recommend",
                    "--video", str(src_a),
                    "--sources-manifest", str(manifest_f),
                    "--script", str(script_f),
                    "--run-root", str(run2),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(p_rec2.returncode, 0, p_rec2.stderr)
            inp2 = json.loads((run2 / "analysis" / "INPUT_ANALYSIS.json").read_text(encoding="utf-8"))
            self.assertEqual(inp2["secondary_sources"][0]["source_id"], "screen_demo_01")
            self.assertEqual(inp2["secondary_sources"][0]["audio_policy"], "MUTE_DEFAULT")

    def test_r42_04_mixed_fps_rational_pts_and_audio_overhang_clamp(self):
        """R4.2-05: Detects 0.105s audio container overhang on 30fps secondary source and maps rational PTS to 24fps."""
        with tempfile.TemporaryDirectory(prefix="kh03_r42_pts_") as td:
            td_p = Path(td)
            # Generate 30fps video of 2.000s with 2.120s audio stream (intentional container/audio overhang!)
            src_b = _make_video(td_p / "overhang_30fps.mp4", fps=30, v_dur=2.0, a_dur=2.12, freq=440)
            probed = intelligence.probe_secondary_source(src_b, source_id="sec_overhang")
            self.assertEqual(probed["fps_num"], 30)
            self.assertEqual(probed["audio_policy"], "MUTE_DEFAULT")
            self.assertGreater(probed["container_vs_video_delta_sec"], 0.05)

            # Requesting src_out_sec into the audio overhang beyond video_duration_sec MUST fail closed!
            with self.assertRaises(ValueError) as ctx:
                intelligence.map_secondary_source_pts_to_master(
                    probed,
                    src_in_sec=0.5,
                    src_out_sec=probed["video_duration_sec"] + 0.08,
                    master_fps_num=24,
                    master_fps_den=1,
                )
            self.assertIn("CONTAINER_VS_VIDEO_DURATION_DRIFT", str(ctx.exception))

            # Valid half-open [0.5, 1.5) at 30fps maps cleanly to 24 frames at 24fps master with zero drift
            mapped = intelligence.map_secondary_source_pts_to_master(
                probed,
                src_in_sec=0.5,
                src_out_sec=1.5,
                master_fps_num=24,
                master_fps_den=1,
            )
            self.assertEqual(mapped["src_frame_span"], [15, 45])
            self.assertEqual(mapped["master_duration_frames"], 24)
            self.assertEqual(mapped["audio_policy"], "MUTE_DEFAULT")


if __name__ == "__main__":
    unittest.main()
