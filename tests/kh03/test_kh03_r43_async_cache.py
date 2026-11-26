"""KH-03.R4.3 Async Shared Ingest Cache, Single-Producer Concurrency, Corruption Rebuild & Long-Form Scaling."""

from __future__ import annotations

import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.kh03.ingest_cache import (
    SharedIngestCache,
    build_lowres_proxy_cached,
    extract_pcm_48k_cached,
    get_process_tree_rss_mib,
    probe_source_cached,
    single_pass_source_scan,
)


def _make_two_scene_clip(path: Path) -> Path:
    """Create a 2.0s clip with an explicit color/scene cut at t=1.0s."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=0x101820:s=360x640:d=1.0:r=24",
            "-f", "lavfi", "-i", "color=c=0xD8B979:s=360x640:d=1.0:r=24",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2.0:sample_rate=48000",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "2:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-shortest", str(path),
        ],
        check=True, capture_output=True, timeout=45,
    )
    return path


class TestKH03R43AsyncCacheSuite(unittest.TestCase):
    def test_r43_06_single_producer_cache_hit_miss_corruption_and_source_invalidation(self) -> None:
        """Concurrent threads invoke producer ONCE; corrupted cache entry rebuilds; source mutation invalidates key."""
        with tempfile.TemporaryDirectory() as td:
            cache = SharedIngestCache(Path(td) / "cache")
            calls = {"n": 0}
            lk = threading.Lock()

            def _slow_compute() -> dict[str, int]:
                time.sleep(0.08)
                with lk:
                    calls["n"] += 1
                return {"answer": 42}

            with ThreadPoolExecutor(max_workers=4) as ex:
                res = list(ex.map(lambda _: cache.get_or_compute_json("demo", {"sha": "abc123"}, _slow_compute), range(4)))

            self.assertEqual(calls["n"], 1, "Single-producer lock must prevent redundant cold computation!")
            self.assertEqual(sum(1 for _v, hit in res if not hit), 1)
            self.assertEqual(sum(1 for _v, hit in res if hit), 3)

            # Corrupt cache JSON on disk -> MUST rebuild cleanly instead of crashing or returning bad data
            key = cache.make_key("demo", {"sha": "abc123"})
            (cache.root / f"{key}.json").write_text("{corrupt_json", encoding="utf-8")
            val2, hit2 = cache.get_or_compute_json("demo", {"sha": "abc123"}, _slow_compute)
            self.assertFalse(hit2)
            self.assertEqual(val2["answer"], 42)
            self.assertEqual(calls["n"], 2)

            # Mutated source parameter -> cache MISS
            val3, hit3 = cache.get_or_compute_json("demo", {"sha": "mutated_xyz"}, _slow_compute)
            self.assertFalse(hit3)
            self.assertEqual(calls["n"], 3)

    def test_r43_07_single_pass_scan_pcm_proxy_cache_and_rss_measurement(self) -> None:
        """Single-pass scanner detects injected scene cut; PCM & low-res proxy cache hit on warm call; RSS measured honestly."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clip = _make_two_scene_clip(root / "cut_clip.mp4")
            cache = SharedIngestCache(root / "cache")

            p1 = probe_source_cached(clip, cache=cache)
            self.assertEqual(p1["nb_frames"], 48)

            scan1 = single_pass_source_scan(clip, cache=cache, scene_cut_mad_threshold=20.0)
            self.assertFalse(scan1["cached"])
            self.assertEqual(scan1["decoded_frames"], 48)
            self.assertGreaterEqual(scan1["scene_cut_count"], 1, "Scene cut at t=1.0s must be detected in single pass")
            scan2 = single_pass_source_scan(clip, cache=cache, scene_cut_mad_threshold=20.0)
            self.assertTrue(scan2["cached"])

            pcm1, m1 = extract_pcm_48k_cached(clip, sample_rate=48000, cache=cache)
            pcm2, m2 = extract_pcm_48k_cached(clip, sample_rate=48000, cache=cache)
            self.assertFalse(m1["cached"])
            self.assertTrue(m2["cached"])
            self.assertEqual(pcm1.size, pcm2.size)
            self.assertGreater(pcm1.size, 80000)

            prx1 = build_lowres_proxy_cached(clip, width=180, height=320, cache=cache)
            prx2 = build_lowres_proxy_cached(clip, width=180, height=320, cache=cache)
            self.assertFalse(prx1["cached"])
            self.assertTrue(prx2["cached"])
            self.assertTrue(prx1["proxy_only_never_final"])
            self.assertTrue(Path(prx1["proxy_path"]).is_file())

            rss = get_process_tree_rss_mib()
            self.assertTrue(rss["measured"])
            self.assertGreater(float(rss["tree_rss_mib"]), 5.0)

            man = cache.manifest()
            self.assertGreaterEqual(man["summary"]["hits"], 3)


if __name__ == "__main__":
    unittest.main()
