"""KH-03.R4.3-07 Tests — Fail-Closed Independent QA, Forged Gate Matrix Detection & Profile Separation."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.kh03 import router
from scripts.kh03.independent_qa import run_independent_render_qa
from scripts.kh03.independent_renderer import render_independent_video
from scripts.kh03.qa import aggregate_gate_results, resolve_gate_profile
from scripts.kh03.semantic_timeline import build_source_registry, compile_semantic_timeline


def _make_clip(path: Path, *, dur: float = 2.0, color: str = "0x223344", freq: int = 440) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=360x640:d={dur}:r=24",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}:sample_rate=48000",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-shortest", str(path),
        ],
        check=True, capture_output=True, timeout=45,
    )
    return path


class TestKH03R43IndependentQASuite(unittest.TestCase):
    def test_r43_08_independent_qa_passes_valid_and_marks_native_only_gates_na(self) -> None:
        """Valid independent MP4 passes INDEPENDENT_FULL QA with native-only gates explicitly NOT_APPLICABLE."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _make_clip(root / "src.mp4", dur=2.0)
            reg = build_source_registry(src)
            spec = compile_semantic_timeline(
                job_id="qa_ok", source_registry=reg, width=360, height=640, fps_num=24, fps_den=1,
                semantic_overlays=[{
                    "event_id": "dep1", "kind": "resource_depletion", "start_sec": 0.2, "end_sec": 1.8,
                    "start_balance": 100, "unit": "pts", "full_frame": True,
                    "deltas": [{"label": "Used", "amount": 50, "reveal_sec": 0.9, "anim_sec": 0.3}],
                }],
                x264_preset="ultrafast", crf=24, bgm_enabled=True,
            )
            out_mp4 = root / "exports" / "ok.mp4"
            render_independent_video(spec, out_mp4=out_mp4, stems_dir=root / "stems", ffmpeg_threads=2)

            qa = run_independent_render_qa(
                run_id="qa_ok", run_dir=root, final_mp4=out_mp4, render_spec=spec,
                mode="INDEPENDENT_FULL", run_post_asr=False, ffmpeg_threads=2,
            )
            self.assertTrue(qa["technical_pass"], f"Expected PASS, got: {qa['gate_matrix']}")
            gates = qa["gate_matrix"]["gates"]
            self.assertEqual(gates["G_H1_GEOM"]["status"], "NOT_APPLICABLE")
            self.assertEqual(gates["G_H2_JUDGE"]["status"], "NOT_APPLICABLE")
            self.assertEqual(gates["G_NATIVE_PROOF"]["status"], "NOT_APPLICABLE")
            self.assertEqual(gates["G_COMPONENT_COVERAGE"]["status"], "PASS")
            self.assertEqual(gates["G_AUDIO_SYNC"]["status"], "PASS")
            self.assertTrue((root / "qc" / "OWNER_APPROVAL.json").is_file())

    def test_r43_09_independent_qa_rejects_corrupt_mp4_and_forged_gate_pass(self) -> None:
        """Corrupt MP4 fails QA; manually forging FINAL_GATE_MATRIX technical_pass=True is caught fail-closed."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _make_clip(root / "src.mp4", dur=1.5)
            reg = build_source_registry(src)
            spec = compile_semantic_timeline(job_id="qa_bad", source_registry=reg, width=360, height=640, pass_through_allowed=True)

            # Corrupt MP4 bytes -> must FAIL!
            bad_mp4 = root / "corrupt.mp4"
            bad_mp4.write_bytes(b"not a real mp4 file")
            with self.assertRaises(Exception):
                run_independent_render_qa(run_id="qa_bad", run_dir=root, final_mp4=bad_mp4, render_spec=spec, mode="INDEPENDENT_FULL", run_post_asr=False)

            # Missing mandatory gate in INDEPENDENT_FULL must NOT pass!
            prof = resolve_gate_profile("INDEPENDENT_FULL")
            partial = aggregate_gate_results({"G_LICENSE": {"status": "PASS", "applicable": True}}, mandatory_gates=prof["mandatory_gates"], mode=prof["mode"])
            self.assertFalse(partial["technical_pass"])
            self.assertIn("G_AUDIO_SYNC", partial["missing_gates"])


if __name__ == "__main__":
    unittest.main()
