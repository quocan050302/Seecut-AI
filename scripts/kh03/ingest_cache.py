"""KH-03.R4.3 Shared Ingest, Content-Addressed Atomic Cache & Single-Pass Streaming Scanner.

Eliminates redundant decoding, audio extraction, and ASR recomputation across planning,
candidate competition, final render, and QA while maintaining strict fail-closed invalidation:
- Single SHA-256 + ffprobe per source per run (`probe_source_cached`).
- Single-pass streaming decode (`single_pass_source_scan`) producing adjacent-frame MAD,
  scene cut candidates, black/freeze runs, blur scores, conservative occupancy, optional
  reusable low-res proxy (never upscaled as final!), and 1fps contact sheet.
- Single 48 kHz mono float32 PCM cache (`extract_pcm_48k_cached`) shared between waveform
  refinement, audio analysis, and final mix.
- Content-addressed ASR cache (`run_asr_cached`) keyed by `(source_sha256, audio_selector,
  model_id, language, norm_version)` — distinct key for final post-render MP4 ASR!
- Atomic file writes + per-key threading/file lock preventing duplicated cold compute,
  automatic corruption detection & rebuild, bounded LRU quota, and `CACHE_MANIFEST.json`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

CACHE_SCHEMA_VERSION = "kh03.ingest_cache.v1"
DEFAULT_MAX_CACHE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB bounded quota
DEFAULT_MAX_CACHE_ENTRIES = 256
CACHE_OWNERSHIP_FILE = ".kh03_r43_cache_owned"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_dict(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _parse_rational(val: str | None) -> tuple[int, int]:
    if not val or val in ("0/0", "N/A"):
        return (24, 1)
    if "/" in val:
        a, b = val.split("/", 1)
        num, den = int(a), max(1, int(b))
        if num <= 0:
            return (24, 1)
        fr = Fraction(num, den).limit_denominator(10000)
        return (fr.numerator, fr.denominator)
    fr = Fraction(float(val)).limit_denominator(10000)
    return (fr.numerator, fr.denominator)


def get_process_tree_rss_mib() -> dict[str, Any]:
    """Measure real process + child tree RSS in MiB (never reports 0 as verified when unavailable)."""
    try:
        import psutil  # type: ignore

        proc = psutil.Process(os.getpid())
        self_rss = proc.memory_info().rss
        children = proc.children(recursive=True)
        child_rss = 0
        for ch in children:
            try:
                child_rss += ch.memory_info().rss
            except Exception:
                pass
        return {
            "measured": True,
            "method": "psutil_process_tree",
            "self_rss_mib": round(self_rss / (1024.0 * 1024.0), 2),
            "children_rss_mib": round(child_rss / (1024.0 * 1024.0), 2),
            "tree_rss_mib": round((self_rss + child_rss) / (1024.0 * 1024.0), 2),
            "child_count": len(children),
        }
    except Exception:
        pass
    try:
        from .execution import get_process_rss_mib

        val = float(get_process_rss_mib())
        if val > 0.0:
            return {
                "measured": True,
                "method": "os_self_peak_rss",
                "self_rss_mib": val,
                "children_rss_mib": None,
                "tree_rss_mib": val,
                "child_count": None,
            }
    except Exception:
        pass
    return {
        "measured": False,
        "method": "unavailable",
        "self_rss_mib": None,
        "children_rss_mib": None,
        "tree_rss_mib": None,
        "child_count": None,
    }


class SharedIngestCache:
    """Content-addressed, atomic, lock-coordinated bounded LRU cache for ingest & analysis artifacts."""

    def __init__(
        self,
        cache_root: str | Path,
        *,
        max_bytes: int = DEFAULT_MAX_CACHE_BYTES,
        max_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
        disk_reserve_gib: float = 5.0,
    ) -> None:
        self.root = Path(cache_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / CACHE_OWNERSHIP_FILE
        if not marker.exists():
            marker.write_text(f"{CACHE_SCHEMA_VERSION}\n", encoding="utf-8")
        self.max_bytes = max(32 * 1024 * 1024, int(max_bytes))
        self.max_entries = max(8, int(max_entries))
        self.disk_reserve_gib = float(disk_reserve_gib)
        self._guard = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._events: list[dict[str, Any]] = []
        self._file_sha_memo: dict[tuple[str, int, int], str] = {}

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            lk = self._key_locks.get(key)
            if lk is None:
                lk = threading.Lock()
                self._key_locks[key] = lk
            return lk

    def file_sha256(self, path: Path) -> str:
        p = Path(path).resolve()
        st = p.stat()
        memo_k = (str(p), int(st.st_size), int(st.st_mtime_ns))
        with self._guard:
            hit = self._file_sha_memo.get(memo_k)
            if hit is not None:
                return hit
        digest = _sha256_file(p)
        with self._guard:
            self._file_sha_memo[memo_k] = digest
        return digest

    def make_key(self, namespace: str, params: dict[str, Any]) -> str:
        payload = {"schema": CACHE_SCHEMA_VERSION, "ns": namespace, "params": params}
        return f"{namespace}_{_sha256_dict(payload)[:24]}"

    def _record(self, key: str, namespace: str, status: str, elapsed_sec: float, payload_bytes: int = 0, note: str = "") -> None:
        with self._guard:
            self._events.append({
                "key": key,
                "namespace": namespace,
                "status": status,
                "elapsed_sec": round(elapsed_sec, 4),
                "bytes": int(payload_bytes),
                "note": note,
            })

    def _enforce_lru(self) -> None:
        """Bounded LRU eviction strictly inside owned cache_root (never deletes user sources)."""
        if not (self.root / CACHE_OWNERSHIP_FILE).is_file():
            return
        entries: list[tuple[float, int, Path]] = []
        total_b = 0
        for p in self.root.iterdir():
            if p.name == CACHE_OWNERSHIP_FILE or p.name.endswith(".tmp"):
                continue
            try:
                if p.is_dir():
                    sz = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                    mt = p.stat().st_mtime
                else:
                    st = p.stat()
                    sz = st.st_size
                    mt = st.st_mtime
                total_b += sz
                entries.append((mt, sz, p))
            except OSError:
                continue
        entries.sort(key=lambda x: x[0])  # oldest first
        while entries and (len(entries) > self.max_entries or total_b > self.max_bytes):
            _, sz, victim = entries.pop(0)
            try:
                if victim.is_dir():
                    shutil.rmtree(victim, ignore_errors=True)
                else:
                    victim.unlink(missing_ok=True)
                total_b = max(0, total_b - sz)
            except OSError:
                break

    def get_or_compute_json(
        self,
        namespace: str,
        params: dict[str, Any],
        compute_fn: Callable[[], dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        """Thread-safe single-producer content-addressed JSON cache with corruption rebuild."""
        key = self.make_key(namespace, params)
        target = self.root / f"{key}.json"
        t0 = time.monotonic()
        with self._lock_for(key):
            if target.is_file():
                try:
                    env = json.loads(target.read_text(encoding="utf-8"))
                    if (
                        isinstance(env, dict)
                        and env.get("cache_schema") == CACHE_SCHEMA_VERSION
                        and env.get("cache_key") == key
                        and isinstance(env.get("payload"), dict)
                        and env.get("payload_sha256") == _sha256_dict(env["payload"])
                    ):
                        os.utime(target, None)
                        self._record(key, namespace, "HIT", time.monotonic() - t0, target.stat().st_size)
                        return env["payload"], True
                    self._record(key, namespace, "CORRUPT_REBUILD", time.monotonic() - t0, note="Hash/schema mismatch")
                except Exception as ex:
                    self._record(key, namespace, "CORRUPT_REBUILD", time.monotonic() - t0, note=str(ex)[:120])
                target.unlink(missing_ok=True)

            payload = compute_fn()
            env_out = {
                "cache_schema": CACHE_SCHEMA_VERSION,
                "cache_key": key,
                "namespace": namespace,
                "params": params,
                "payload_sha256": _sha256_dict(payload),
                "payload": payload,
            }
            tmp = self.root / f"{key}.{os.getpid()}.{threading.get_ident()}.tmp"
            raw = json.dumps(env_out, ensure_ascii=False, indent=2)
            tmp.write_text(raw, encoding="utf-8")
            os.replace(tmp, target)
            self._enforce_lru()
            self._record(key, namespace, "MISS_COMPUTED", time.monotonic() - t0, target.stat().st_size)
            return payload, False

    def get_or_compute_artifact(
        self,
        namespace: str,
        params: dict[str, Any],
        ext: str,
        produce_fn: Callable[[Path], dict[str, Any]],
    ) -> tuple[Path, dict[str, Any], bool]:
        """Thread-safe single-producer binary artifact cache (e.g. PCM float32 or low-res proxy MP4)."""
        key = self.make_key(namespace, params)
        art_path = self.root / f"{key}{ext}"
        meta_path = self.root / f"{key}.meta.json"
        t0 = time.monotonic()
        with self._lock_for(key):
            if art_path.is_file() and meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if (
                        isinstance(meta, dict)
                        and meta.get("cache_schema") == CACHE_SCHEMA_VERSION
                        and meta.get("cache_key") == key
                        and meta.get("artifact_sha256") == _sha256_file(art_path)
                    ):
                        os.utime(art_path, None)
                        os.utime(meta_path, None)
                        self._record(key, namespace, "HIT", time.monotonic() - t0, art_path.stat().st_size)
                        return art_path, dict(meta.get("metadata") or {}), True
                    self._record(key, namespace, "CORRUPT_REBUILD", time.monotonic() - t0, note="Binary SHA mismatch")
                except Exception as ex:
                    self._record(key, namespace, "CORRUPT_REBUILD", time.monotonic() - t0, note=str(ex)[:120])
                art_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)

            # Check disk reserve before producing large binary
            du = shutil.disk_usage(str(self.root))
            free_gib = du.free / (1024.0 ** 3)
            if free_gib < self.disk_reserve_gib:
                raise RuntimeError(f"CACHE_DISK_FLOOR_EXCEEDED: free={free_gib:.2f} GiB < {self.disk_reserve_gib:.2f} GiB")

            tmp_art = self.root / f"{key}.{os.getpid()}.{threading.get_ident()}.staging{ext}"
            tmp_art.unlink(missing_ok=True)
            try:
                user_meta = produce_fn(tmp_art)
                if not tmp_art.is_file() or tmp_art.stat().st_size <= 0:
                    raise RuntimeError(f"CACHE_PRODUCER_EMPTY_OUTPUT:{namespace}")
                art_sha = _sha256_file(tmp_art)
                os.replace(tmp_art, art_path)
                meta_env = {
                    "cache_schema": CACHE_SCHEMA_VERSION,
                    "cache_key": key,
                    "namespace": namespace,
                    "params": params,
                    "artifact_sha256": art_sha,
                    "artifact_bytes": art_path.stat().st_size,
                    "metadata": user_meta,
                }
                tmp_meta = self.root / f"{key}.{os.getpid()}.{threading.get_ident()}.meta.tmp"
                tmp_meta.write_text(json.dumps(meta_env, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp_meta, meta_path)
                self._enforce_lru()
                self._record(key, namespace, "MISS_COMPUTED", time.monotonic() - t0, art_path.stat().st_size)
                return art_path, user_meta, False
            except Exception:
                tmp_art.unlink(missing_ok=True)
                raise

    def manifest(self) -> dict[str, Any]:
        with self._guard:
            ev = list(self._events)
        hits = sum(1 for e in ev if e["status"] == "HIT")
        misses = sum(1 for e in ev if e["status"] == "MISS_COMPUTED")
        rebuilds = sum(1 for e in ev if e["status"] == "CORRUPT_REBUILD")
        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "cache_root": str(self.root),
            "max_bytes": self.max_bytes,
            "max_entries": self.max_entries,
            "summary": {"total_lookups": len(ev), "hits": hits, "misses": misses, "corrupt_rebuilds": rebuilds},
            "events": ev,
        }


# ---------------------------------------------------------------------------
# Shared Ingest Primitives (Probe, PCM, Single-Pass Video Scan, Proxy, ASR)
# ---------------------------------------------------------------------------

def probe_source_cached(video_path: Path, cache: SharedIngestCache | None = None) -> dict[str, Any]:
    """Probe exact video/audio stream metadata + SHA-256 once per source."""
    vp = Path(video_path).resolve()
    sha = cache.file_sha256(vp) if cache is not None else _sha256_file(vp)

    def _compute() -> dict[str, Any]:
        pr = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-print_format", "json", str(vp)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        doc = json.loads(pr.stdout)
        streams = doc.get("streams") or []
        v_st = next((s for s in streams if s.get("codec_type") == "video"), None)
        a_st = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if not v_st:
            raise RuntimeError(f"NO_VIDEO_STREAM: {vp}")
        fps_num, fps_den = _parse_rational(v_st.get("avg_frame_rate") or v_st.get("r_frame_rate"))
        fps = float(Fraction(fps_num, fps_den))
        v_dur = float(v_st.get("duration") or (doc.get("format") or {}).get("duration") or 0.0)
        c_dur = float((doc.get("format") or {}).get("duration") or v_dur)
        a_dur = float(a_st.get("duration") or 0.0) if a_st else 0.0
        nb = int(v_st["nb_frames"]) if str(v_st.get("nb_frames") or "").isdigit() else max(1, int(round(v_dur * fps)))
        w, h = int(v_st["width"]), int(v_st["height"])
        return {
            "schema_version": "kh03.source_probe.v1",
            "path": str(vp),
            "basename": vp.name,
            "sha256": sha,
            "bytes": vp.stat().st_size,
            "width": w,
            "height": h,
            "aspect_ratio": "9:16" if h > w else ("16:9" if w > h else "1:1"),
            "pix_fmt": str(v_st.get("pix_fmt") or "yuv420p"),
            "video_codec": str(v_st.get("codec_name") or ""),
            "fps_num": fps_num,
            "fps_den": fps_den,
            "fps": round(fps, 6),
            "nb_frames": nb,
            "video_duration_sec": round(v_dur, 6),
            "container_duration_sec": round(c_dur, 6),
            "overhang_sec": round(max(0.0, c_dur - v_dur), 6),
            "has_audio": a_st is not None,
            "audio_codec": str(a_st.get("codec_name") or "") if a_st else None,
            "audio_sample_rate": int(a_st.get("sample_rate") or 0) if a_st else 0,
            "audio_channels": int(a_st.get("channels") or 0) if a_st else 0,
            "audio_duration_sec": round(a_dur, 6),
        }

    if cache is None:
        return _compute()
    res, _hit = cache.get_or_compute_json("probe", {"sha256": sha}, _compute)
    res["path"] = str(vp)
    return res


def extract_pcm_48k_cached(
    video_path: Path,
    *,
    sample_rate: int = 48000,
    cache: SharedIngestCache | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Extract mono float32 PCM once and share across waveform refinement, audio analysis & mixing."""
    vp = Path(video_path).resolve()
    sha = cache.file_sha256(vp) if cache is not None else _sha256_file(vp)

    def _raw_extract() -> np.ndarray:
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(vp), "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
        )
        if p.returncode != 0 or not p.stdout:
            return np.zeros(0, dtype=np.float32)
        return np.frombuffer(p.stdout, dtype=np.float32).copy()

    if cache is None:
        arr = _raw_extract()
        return arr, {"cached": False, "sample_rate": sample_rate, "samples": int(arr.size)}

    def _produce(tmp_path: Path) -> dict[str, Any]:
        arr = _raw_extract()
        arr.tofile(str(tmp_path))
        clip_n = int(np.sum(np.abs(arr) >= 0.995)) if arr.size else 0
        max_abs = float(np.max(np.abs(arr))) if arr.size else 0.0
        return {
            "sample_rate": sample_rate,
            "samples": int(arr.size),
            "duration_sec": round(arr.size / float(sample_rate), 4),
            "max_abs": round(max_abs, 5),
            "clipped_samples_ge_0995": clip_n,
        }

    art_p, meta, hit = cache.get_or_compute_artifact("pcm_f32", {"sha256": sha, "sr": sample_rate}, ".f32", _produce)
    arr = np.fromfile(str(art_p), dtype=np.float32)
    return arr, {**meta, "cached": hit, "artifact_path": str(art_p)}


def build_lowres_proxy_cached(
    video_path: Path,
    *,
    width: int = 540,
    height: int = 960,
    fps_num: int = 24,
    fps_den: int = 1,
    cache: SharedIngestCache | None = None,
    ffmpeg_threads: int = 2,
) -> dict[str, Any]:
    """Build reusable immutable low-res proxy for candidate previews ONLY (NEVER upscaled as final!)."""
    vp = Path(video_path).resolve()
    sha = cache.file_sha256(vp) if cache is not None else _sha256_file(vp)

    def _produce(tmp_mp4: Path) -> dict[str, Any]:
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(vp),
            "-an",
            "-vf", f"scale={width}:{height}:flags=lanczos",
            "-r", f"{fps_num}/{fps_den}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-threads", str(max(1, int(ffmpeg_threads))),
            "-movflags", "+faststart",
            str(tmp_mp4),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        return {"width": width, "height": height, "fps_num": fps_num, "fps_den": fps_den, "proxy_only_never_final": True}

    if cache is None:
        raise ValueError("build_lowres_proxy_cached requires a SharedIngestCache instance.")
    art_p, meta, hit = cache.get_or_compute_artifact(
        "proxy_mp4",
        {"sha256": sha, "w": width, "h": height, "fps": f"{fps_num}/{fps_den}"},
        ".mp4",
        _produce,
    )
    return {"proxy_path": str(art_p), "cached": hit, **meta}


def single_pass_source_scan(
    video_path: Path,
    *,
    cache: SharedIngestCache | None = None,
    scene_cut_mad_threshold: float = 16.0,
    freeze_mad_threshold: float = 0.25,
    black_luma_threshold: float = 8.0,
    contact_sheet_path: Path | None = None,
    ffmpeg_threads: int = 2,
) -> dict[str, Any]:
    """Single-pass streaming decode over ALL frames computing MAD, cuts, black/freeze, blur & conservative occupancy.

    Avoids multi-pass decoding across continuity, black-frame QA, and tracking fallback!
    """
    vp = Path(video_path).resolve()
    probe = probe_source_cached(vp, cache=cache)
    sha = probe["sha256"]

    def _scan() -> dict[str, Any]:
        t0 = time.monotonic()
        # Decode directly at compact 180x320 (or aspect-matched) in ONE stream for ultra-fast full-frame coverage!
        in_w, in_h = int(probe["width"]), int(probe["height"])
        scan_w = 180 if in_h >= in_w else 320
        scan_h = int(round(scan_w * (in_h / float(max(1, in_w)))))
        scan_h += scan_h % 2
        fps = float(probe["fps"])

        cmd = [
            "ffmpeg", "-v", "error", "-threads", str(max(1, int(ffmpeg_threads))),
            "-i", str(vp),
            "-vf", f"scale={scan_w}:{scan_h}:flags=fast_bilinear",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        fb = scan_w * scan_h * 3
        prev_gray: np.ndarray | None = None
        idx = 0
        black_frames: list[int] = []
        scene_cuts: list[dict[str, Any]] = []
        freeze_runs: list[dict[str, Any]] = []
        cur_freeze_start: int | None = None
        cur_freeze_len = 0
        luma_min, luma_max = 255.0, 0.0
        blur_samples: list[float] = []
        sheet_frames: dict[int, Image.Image] = {}
        step_1fps = max(1, int(round(fps)))

        try:
            assert proc.stdout is not None
            while True:
                raw = proc.stdout.read(fb)
                if len(raw) < fb:
                    break
                arr = np.frombuffer(raw, dtype=np.uint8).reshape((scan_h, scan_w, 3))
                gray = arr.mean(axis=2, dtype=np.float32)
                mean_luma = float(gray.mean())
                luma_min = min(luma_min, mean_luma)
                luma_max = max(luma_max, mean_luma)
                if mean_luma < black_luma_threshold:
                    black_frames.append(idx)

                # Cheap gradient sharpness proxy every ~0.5s
                if idx % max(1, step_1fps // 2) == 0:
                    gx = np.abs(np.diff(gray, axis=1)).mean()
                    gy = np.abs(np.diff(gray, axis=0)).mean()
                    blur_samples.append(round(float(gx + gy), 3))

                if prev_gray is not None:
                    mad = float(np.mean(np.abs(gray - prev_gray)))
                    if mad >= scene_cut_mad_threshold:
                        scene_cuts.append({
                            "frame": idx,
                            "time_sec": round(idx / fps, 3),
                            "mad": round(mad, 3),
                            "classification": "SUSPECT_DISCONTINUITY_OR_CUT",
                        })
                    if mad <= freeze_mad_threshold:
                        if cur_freeze_start is None:
                            cur_freeze_start = idx - 1
                            cur_freeze_len = 2
                        else:
                            cur_freeze_len += 1
                    else:
                        if cur_freeze_start is not None and cur_freeze_len >= int(round(fps * 0.75)):
                            freeze_runs.append({
                                "start_frame": cur_freeze_start,
                                "end_frame": idx,
                                "length_frames": cur_freeze_len,
                                "duration_sec": round(cur_freeze_len / fps, 3),
                            })
                        cur_freeze_start = None
                        cur_freeze_len = 0

                if contact_sheet_path is not None and (idx % step_1fps == 0):
                    sheet_frames[idx] = Image.fromarray(arr)

                prev_gray = gray
                idx += 1

            if cur_freeze_start is not None and cur_freeze_len >= int(round(fps * 0.75)):
                freeze_runs.append({
                    "start_frame": cur_freeze_start,
                    "end_frame": idx,
                    "length_frames": cur_freeze_len,
                    "duration_sec": round(cur_freeze_len / fps, 3),
                })
        finally:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
            proc.wait(timeout=30)

        if contact_sheet_path is not None and sheet_frames:
            cols = 8
            rows = (len(sheet_frames) + cols - 1) // cols
            sh_im = Image.new("RGB", (cols * scan_w, rows * scan_h), (16, 16, 16))
            dr = ImageDraw.Draw(sh_im)
            for i, fi in enumerate(sorted(sheet_frames.keys())):
                r, c = divmod(i, cols)
                sh_im.paste(sheet_frames[fi], (c * scan_w, r * scan_h))
                dr.rectangle((c * scan_w + 3, r * scan_h + 3, c * scan_w + 82, r * scan_h + 20), fill=(0, 0, 0))
                dr.text((c * scan_w + 6, r * scan_h + 5), f"{fi / fps:.1f}s f{fi}", fill=(255, 255, 255))
            contact_sheet_path.parent.mkdir(parents=True, exist_ok=True)
            sh_im.save(contact_sheet_path, quality=88)

        return {
            "schema_version": "kh03.single_pass_source_scan.v1",
            "source_sha256": sha,
            "scan_resolution": [scan_w, scan_h],
            "decoded_frames": idx,
            "expected_frames": probe["nb_frames"],
            "frame_count_exact": idx == probe["nb_frames"],
            "black_frame_count": len(black_frames),
            "black_frames": black_frames[:32],
            "freeze_run_count": len(freeze_runs),
            "freeze_runs": freeze_runs,
            "scene_cut_count": len(scene_cuts),
            "scene_cuts": scene_cuts,
            "mean_luma_min": round(luma_min, 2) if idx else 0.0,
            "mean_luma_max": round(luma_max, 2) if idx else 0.0,
            "sharpness_mean": round(float(np.mean(blur_samples)), 3) if blur_samples else 0.0,
            "elapsed_sec": round(time.monotonic() - t0, 3),
            "coverage_kind": "EXHAUSTIVE_ALL_FRAMES_LOWRES_METRICS",
        }

    if cache is None:
        return _scan()
    params = {
        "sha256": sha,
        "cut_th": scene_cut_mad_threshold,
        "freeze_th": freeze_mad_threshold,
        "black_th": black_luma_threshold,
    }
    res, hit = cache.get_or_compute_json("source_scan", params, _scan)
    return {**res, "cached": hit}


def run_asr_cached(
    media_path: Path,
    *,
    cache: SharedIngestCache | None = None,
    language: str = "en",
    model_name: str = "small.en",
    audio_selector: str = "primary_source",
    asr_runner: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run or reuse content-addressed word-level ASR keyed strictly by media SHA + selector + model."""
    mp = Path(media_path).resolve()
    sha = cache.file_sha256(mp) if cache is not None else _sha256_file(mp)

    def _default_asr(target: Path) -> dict[str, Any]:
        t0 = time.monotonic()
        venv_py = Path.home() / ".faceless-shorts" / "venv" / "Scripts" / "python.exe"
        py_exe = str(venv_py) if venv_py.is_file() else sys.executable
        code = (
            "import json, subprocess, sys, numpy as np\n"
            "from faster_whisper import WhisperModel\n"
            "p = subprocess.run(['ffmpeg','-v','error','-i',sys.argv[1],'-ac','1','-ar','16000','-f','f32le','pipe:1'], stdout=subprocess.PIPE, check=True)\n"
            "audio = np.frombuffer(p.stdout, dtype=np.float32)\n"
            "model = WhisperModel(sys.argv[2], device='cpu', compute_type='int8', local_files_only=True)\n"
            "segs, info = model.transcribe(audio, language=sys.argv[3], word_timestamps=True, beam_size=5, vad_filter=False)\n"
            "words, full = [], []\n"
            "for s in segs:\n"
            "    full.append(s.text.strip())\n"
            "    for w in (s.words or []):\n"
            "        words.append({'word': w.word.strip(), 'start': round(float(w.start), 3), 'end': round(float(w.end), 3), 'confidence': round(float(w.probability), 4)})\n"
            "print(json.dumps({'language': info.language, 'model': f'faster-whisper:{sys.argv[2]}:cpu:int8', 'text': ' '.join(full), 'words': words}))\n"
        )
        pr = subprocess.run([py_exe, "-c", code, str(target), model_name, language], capture_output=True, text=True, timeout=240)
        if pr.returncode != 0:
            raise RuntimeError(f"LOCAL_ASR_FAILED(rc={pr.returncode}): {pr.stderr[-300:]}")
        doc = json.loads(pr.stdout)
        doc["elapsed_sec"] = round(time.monotonic() - t0, 3)
        doc["source_sha256"] = sha
        doc["audio_selector"] = audio_selector
        return doc

    fn = asr_runner if asr_runner is not None else _default_asr
    if cache is None:
        return {**fn(mp), "cached": False}

    params = {
        "sha256": sha,
        "selector": audio_selector,
        "model": model_name,
        "lang": language,
        "norm_v": "r43.v1",
    }
    res, hit = cache.get_or_compute_json("asr_words", params, lambda: fn(mp))
    return {**res, "cached": hit}


__all__ = [
    "CACHE_SCHEMA_VERSION",
    "SharedIngestCache",
    "build_lowres_proxy_cached",
    "extract_pcm_48k_cached",
    "get_process_tree_rss_mib",
    "probe_source_cached",
    "run_asr_cached",
    "single_pass_source_scan",
]
