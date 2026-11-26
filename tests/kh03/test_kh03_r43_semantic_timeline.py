"""KH-03.R4.3-01 Tests — Semantic Timeline Compiler, N-Source Registry, EDL Remapping & Fail-Closed Validation."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.kh03.independent_renderer import render_independent_video
from scripts.kh03.semantic_timeline import (
    SPEC_V2_SCHEMA,
    build_source_registry,
    compile_semantic_timeline,
    remap_events_to_output_clock,
    validate_render_spec,
)


def _make_clip(path: Path, *, dur: float = 2.0, fps: int = 24, w: int = 360, h: int = 640, color: str = "0x203040", freq: int = 440) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:d={dur}:r={fps}",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}:sample_rate=48000",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-shortest", str(path),
        ],
        check=True, capture_output=True, timeout=45,
    )
    return path


class TestKH03R43SemanticTimelineSuite(unittest.TestCase):
    def test_r43_01_multisource_n_registry_and_voice_edl_remapping(self) -> None:
        """Arbitrary N=3 sources (24fps + 30fps + 24fps) register with MUTE_DEFAULT and Voice EDL trim reflects in output duration."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s1 = _make_clip(root / "src1.mp4", dur=3.0, fps=24, color="0x182838", freq=440)
            s2 = _make_clip(root / "src2.mp4", dur=2.0, fps=30, color="0x382818", freq=880)
            s3 = _make_clip(root / "src3.mp4", dur=2.0, fps=24, color="0x183828", freq=660)

            reg = build_source_registry(
                {"source_id": "main_cam", "path": str(s1)},
                [
                    {"source_id": "broll_30fps", "path": str(s2)},
                    {"source_id": "diagram_clip", "path": str(s3)},
                ],
            )
            self.assertEqual(reg["source_count"], 3)
            self.assertEqual(reg["by_id"]["main_cam"]["audio_policy"], "MASTER_VOICE")
            self.assertEqual(reg["by_id"]["broll_30fps"]["audio_policy"], "MUTE_DEFAULT")
            self.assertEqual(reg["by_id"]["diagram_clip"]["audio_policy"], "MUTE_DEFAULT")

            # Voice EDL trims 1.0s middle pause -> 2.0s output (48 frames @ 24fps)
            edl = {
                "fps_num": 24, "fps_den": 1, "total_output_frames": 48,
                "segments": [
                    {"segment_id": "v1", "source_id": "main_cam", "src_in_sec": 0.0, "src_out_sec": 1.0, "out_start_frame": 0, "out_end_frame": 24, "out_in_sec": 0.0, "out_out_sec": 1.0},
                    {"segment_id": "v2", "source_id": "main_cam", "src_in_sec": 2.0, "src_out_sec": 3.0, "out_start_frame": 24, "out_end_frame": 48, "out_in_sec": 1.0, "out_out_sec": 2.0},
                ],
            }
            spec = compile_semantic_timeline(
                job_id="test_n_sources",
                source_registry=reg,
                voice_edl=edl,
                semantic_overlays=[
                    {"event_id": "ov1", "kind": "context_chip", "timebase": "source", "start_sec": 2.1, "end_sec": 2.9, "kicker": "STEP", "title": "Remapped after cut"},
                ],
                width=360, height=640, fps_num=24, fps_den=1,
                x264_preset="ultrafast", crf=26, bgm_enabled=False,
            )
            self.assertEqual(spec["schema_version"], SPEC_V2_SCHEMA)
            self.assertEqual(spec["total_output_frames"], 48)
            # Source 2.1s -> output 1.1s after removing [1.0, 2.0)!
            self.assertAlmostEqual(spec["overlays"][0]["start_sec"], 1.1, delta=0.05)

            out_mp4 = root / "out.mp4"
            res = render_independent_video(spec, out_mp4=out_mp4, stems_dir=root / "stems", ffmpeg_threads=2)
            self.assertEqual(res["nb_frames"], 48)
            self.assertAlmostEqual(res["duration_sec"], 2.0, delta=0.08)

    def test_r43_02_plan_mutation_changes_rendered_pixels_and_spec_sha(self) -> None:
        """Mutating semantic overlay or camera plan produces a different spec_sha256 AND measurably different MP4 bytes."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _make_clip(root / "src.mp4", dur=1.5, fps=24)
            reg = build_source_registry(src)
            common = dict(job_id="mut_test", source_registry=reg, width=360, height=640, fps_num=24, fps_den=1, x264_preset="ultrafast", crf=24, bgm_enabled=False)

            spec_a = compile_semantic_timeline(
                **common,
                semantic_overlays=[{"event_id": "ov1", "kind": "context_chip", "start_sec": 0.1, "end_sec": 1.3, "title": "Alpha Topic", "position": "top"}],
            )
            spec_b = compile_semantic_timeline(
                **common,
                semantic_overlays=[{"event_id": "ov1", "kind": "context_chip", "start_sec": 0.1, "end_sec": 1.3, "title": "Beta Topic", "position": "bottom"}],
            )
            self.assertNotEqual(spec_a["spec_sha256"], spec_b["spec_sha256"])
            ra = render_independent_video(spec_a, out_mp4=root / "a.mp4", stems_dir=root / "sa", ffmpeg_threads=2)
            rb = render_independent_video(spec_b, out_mp4=root / "b.mp4", stems_dir=root / "sb", ffmpeg_threads=2)
            self.assertNotEqual(ra["final_mp4_sha256"], rb["final_mp4_sha256"])

    def test_r43_03_fail_closed_invalid_component_internal_label_and_conservation(self) -> None:
        """Unsupported kind, SK-01 internal label leak, non-conserving allocation, and dangling source_id MUST fail closed."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _make_clip(root / "src.mp4", dur=1.5, fps=24)
            reg = build_source_registry(src)

            # 1. Unsupported component kind
            with self.assertRaises(ValueError):
                compile_semantic_timeline(
                    job_id="bad_kind", source_registry=reg, width=360, height=640,
                    semantic_overlays=[{"event_id": "e1", "kind": "magic_unknown_widget", "start_sec": 0.1, "end_sec": 1.0}],
                )
            # 2. Internal skill ID leaked into visible text
            with self.assertRaises(ValueError):
                compile_semantic_timeline(
                    job_id="bad_label", source_registry=reg, width=360, height=640,
                    semantic_overlays=[{"event_id": "e1", "kind": "context_chip", "start_sec": 0.1, "end_sec": 1.0, "title": "SK-01 Debug Banner"}],
                )
            # 3. Non-conserving resource allocation (20 + 30 != 100 minutes)
            with self.assertRaises(ValueError):
                compile_semantic_timeline(
                    job_id="bad_alloc", source_registry=reg, width=360, height=640,
                    semantic_overlays=[{
                        "event_id": "e1", "kind": "resource_allocation", "start_sec": 0.1, "end_sec": 1.4,
                        "total": 100, "unit": "min",
                        "branches": [
                            {"label": "Warmup", "amount": 20, "reveal_sec": 0.2},
                            {"label": "Deep Work", "amount": 30, "reveal_sec": 0.5},
                        ],
                    }],
                )
            # 4. Dangling source_id in visual track
            base_spec = compile_semantic_timeline(job_id="ok", source_registry=reg, width=360, height=640, pass_through_allowed=True)
            base_spec["visual_tracks"][0]["source_id"] = "nonexistent_cam"
            v = validate_render_spec(base_spec)
            self.assertFalse(v["pass"])
            self.assertTrue(any("DANGLING_SOURCE_ID" in i for i in v["issues"]))


if __name__ == "__main__":
    unittest.main()
