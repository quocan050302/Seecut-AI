"""KH-03.R3 Bounded Async DAG Execution, Resource Budgets, Atomic Cache & True Streaming Decoder (CMI-10).

Eliminates unbounded full-video RGB buffering (`subprocess.run(stdout=PIPE)`) across QA and tracking,
enforces host-calibrated RAM/disk/worker budgets with fail-closed resource stops, provides deterministic
`asyncio.TaskGroup` + bounded `ThreadPoolExecutor` DAG scheduling (identical canonical outputs across
workers=1/2/4), content-addressed atomic caching, and platform-safe subprocess tree cleanup.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import resource
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from .contracts import assert_kh03_schema, hash_artifact, now_utc, parse_json_strict, sanitize_identifier, write_json_atomic

DEFAULT_DISK_RESERVE_GIB = 5.0
OWNERSHIP_MARKER = ".kh03_cache_owned"


class ResourceBudgetError(RuntimeError):
    """Raised fail-closed when disk, memory, or explicit resource budgets are exceeded."""

    def __init__(self, code: str, message: str, budget: dict[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.budget = budget or {}


def get_host_memory_mib() -> tuple[float, float]:
    """Return (total_ram_mib, available_ram_mib) cleanly across macOS, Linux, and Windows."""
    sys_name = platform.system().lower()
    total_mib = 8192.0
    avail_mib = 4096.0
    try:
        if sys_name == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, timeout=5).strip()
            total_mib = float(int(out)) / (1024.0 * 1024.0)
            # Estimate available RAM from vm_stat (free + inactive + speculative pages)
            vm = subprocess.check_output(["vm_stat"], text=True, timeout=5)
            page_sz = 16384 if "page size of 16384" in vm else 4096
            free_pages = 0
            for ln in vm.splitlines():
                if any(ln.startswith(k) for k in ("Pages free:", "Pages inactive:", "Pages speculative:", "Pages purgeable:")):
                    val = ln.split(":", 1)[1].strip().rstrip(".")
                    if val.isdigit():
                        free_pages += int(val)
            avail_mib = max(256.0, (free_pages * page_sz) / (1024.0 * 1024.0))
            return round(total_mib, 2), round(min(total_mib, avail_mib), 2)
        if sys_name == "linux" and Path("/proc/meminfo").is_file():
            txt = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
            kv: dict[str, int] = {}
            for ln in txt.splitlines():
                parts = ln.split(":")
                if len(parts) == 2:
                    toks = parts[1].strip().split()
                    if toks and toks[0].isdigit():
                        kv[parts[0].strip()] = int(toks[0])
            if "MemTotal" in kv:
                total_mib = kv["MemTotal"] / 1024.0
            if "MemAvailable" in kv:
                avail_mib = kv["MemAvailable"] / 1024.0
            return round(total_mib, 2), round(avail_mib, 2)
        if sys_name == "windows":
            import ctypes

            class _MEMSTAT(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = _MEMSTAT()
            st.dwLength = ctypes.sizeof(_MEMSTAT)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                total_mib = st.ullTotalPhys / (1024.0 * 1024.0)
                avail_mib = st.ullAvailPhys / (1024.0 * 1024.0)
                return round(total_mib, 2), round(avail_mib, 2)
    except Exception:
        pass
    return round(total_mib, 2), round(avail_mib, 2)


def get_process_rss_mib() -> float:
    """Measure current process high-water RSS in MiB (platform-calibrated ru_maxrss)."""
    try:
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS returns bytes; Linux returns KiB
        if platform.system().lower() == "darwin":
            return round(ru / (1024.0 * 1024.0), 2)
        return round(ru / 1024.0, 2)
    except Exception:
        return 0.0


def compute_resource_budget(
    target_dir: str | Path = ".",
    *,
    workers: int = 2,
    disk_reserve_gib: float = DEFAULT_DISK_RESERVE_GIB,
    max_disk_gib: float | None = None,
    projected_disk_gib: float = 0.0,
    max_memory_mib: float | None = None,
    queue_frames: int = 8,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Compute host-calibrated resource budget and validate RAM/disk headroom fail-closed."""
    cpu_cnt = max(1, os.cpu_count() or 2)
    eff_workers = max(1, min(4, int(workers)))
    net_workers = max(1, min(2, eff_workers))
    dec_pool = max(1, min(2, eff_workers))
    ff_threads = max(1, min(4, cpu_cnt // max(1, eff_workers)))
    q_frames = max(2, min(32, int(queue_frames)))

    chk = Path(target_dir).expanduser()
    while not chk.exists() and chk != chk.parent:
        chk = chk.parent
    du = shutil.disk_usage(str(chk))
    free_disk_gib = round(du.free / (1024.0 ** 3), 3)

    total_ram_mib, avail_ram_mib = get_host_memory_mib()
    cur_rss_mib = get_process_rss_mib()
    safe_cap_mib = round(max(256.0, min(total_ram_mib * 0.45, max(512.0, avail_ram_mib * 0.70))), 2)
    mem_budget_mib = round(min(float(max_memory_mib), safe_cap_mib), 2) if max_memory_mib is not None else safe_cap_mib

    status = "OK"
    reason = "Within calibrated host RAM, CPU, and disk reserve budgets."
    budget_ok = True

    if (free_disk_gib - float(projected_disk_gib)) < float(disk_reserve_gib):
        status = "BLOCKED_LOW_DISK"
        reason = (
            f"Free disk {free_disk_gib:.2f} GiB minus projected {projected_disk_gib:.2f} GiB "
            f"< reserve floor {disk_reserve_gib:.2f} GiB."
        )
        budget_ok = False
    elif max_disk_gib is not None and float(projected_disk_gib) > float(max_disk_gib):
        status = "RESOURCE_BUDGET_EXCEEDED"
        reason = f"Projected disk {projected_disk_gib:.2f} GiB exceeds max_disk_gib={max_disk_gib:.2f}."
        budget_ok = False
    elif max_memory_mib is not None and cur_rss_mib > float(max_memory_mib):
        status = "BLOCKED_MEMORY"
        reason = f"Current RSS {cur_rss_mib:.1f} MiB exceeds configured memory cap {float(max_memory_mib):.1f} MiB."
        budget_ok = False
    elif mem_budget_mib < 128.0:
        status = "BLOCKED_MEMORY"
        reason = f"Calibrated memory budget {mem_budget_mib:.1f} MiB below minimum 128 MiB floor."
        budget_ok = False

    rep = {
        "schema_version": "kh03.execution_budget.v1",
        "workers": eff_workers,
        "network_workers": net_workers,
        "heavy_render_concurrency": 1,
        "gpu_model_concurrency": 1,
        "decoder_pool": dec_pool,
        "queue_frames": q_frames,
        "ffmpeg_threads": ff_threads,
        "disk_reserve_gib": round(float(disk_reserve_gib), 3),
        "max_disk_gib": round(float(max_disk_gib), 3) if max_disk_gib is not None else None,
        "free_disk_gib": free_disk_gib,
        "host_ram_mib": total_ram_mib,
        "available_ram_mib": avail_ram_mib,
        "memory_budget_mib": mem_budget_mib,
        "current_rss_mib": cur_rss_mib,
        "budget_ok": budget_ok,
        "status": status,
        "reason": reason,
    }
    assert_kh03_schema(rep, "execution_budget")
    if run_dir is not None:
        write_json_atomic(run_dir / "manifests" / "RESOURCE_BUDGET.json", rep)
    return rep


def terminate_process_tree(proc: subprocess.Popen[Any] | None, *, grace_sec: float = 0.4) -> dict[str, Any]:
    """Terminate subprocess and its child process group safely on POSIX or Windows without leaking handles."""
    if proc is None:
        return {"pid": None, "clean": True, "already_exited": True, "method": "none"}
    pid = proc.pid
    if proc.poll() is not None:
        return {"pid": pid, "clean": True, "already_exited": True, "returncode": proc.returncode, "method": "exited"}
    method = "terminate"
    try:
        if os.name == "posix":
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
                method = "posix_killpg_sigterm"
            except Exception:
                proc.terminate()
                method = "posix_terminate"
        else:
            proc.terminate()
            method = "win_terminate"
        try:
            proc.wait(timeout=grace_sec)
            return {"pid": pid, "clean": True, "already_exited": False, "returncode": proc.returncode, "method": method}
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                    method = "posix_killpg_sigkill"
                except Exception:
                    proc.kill()
                    method = "posix_kill"
            else:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=5)
                method = "win_taskkill"
            try:
                proc.wait(timeout=1.5)
            except Exception:
                pass
            return {"pid": pid, "clean": proc.poll() is not None, "already_exited": False, "returncode": proc.poll(), "method": method}
    except Exception as ex:
        return {"pid": pid, "clean": proc.poll() is not None, "error": str(ex), "method": method}


class StreamingRGBFrameDecoder:
    """True O(1)-RAM streaming FFmpeg rawvideo RGB24 decoder with bounded stderr drain & process-tree cleanup.

    Never buffers the entire video in stdout (`subprocess.run(stdout=PIPE)` is forbidden for rawvideo).
    Reads exact `width * height * 3` byte chunks one frame at a time with explicit partial-read/EOF handling.
    """

    def __init__(
        self,
        media_path: str | Path,
        width: int,
        height: int,
        *,
        ffmpeg_threads: int = 2,
        timeout_sec: float = 300.0,
        fps_mode: str = "passthrough",
        scale_flags: str = "bilinear",
        extra_vf: str | None = None,
    ) -> None:
        self.media_path = Path(media_path).resolve()
        self.width = max(16, int(width))
        self.height = max(16, int(height))
        self.frame_bytes = self.width * self.height * 3
        self.ffmpeg_threads = max(1, min(4, int(ffmpeg_threads)))
        self.timeout_sec = float(timeout_sec)
        self.fps_mode = fps_mode
        self.scale_flags = scale_flags
        self.extra_vf = extra_vf

        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr_lines: deque[str] = deque(maxlen=64)
        self._stderr_thread: threading.Thread | None = None
        self._closed = False
        self.frames_decoded = 0
        self.truncated_tail_bytes = 0
        self.cleanup_report: dict[str, Any] = {}
        self.started_monotonic = 0.0

    def __enter__(self) -> "StreamingRGBFrameDecoder":
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close(force=(exc_type is not None))

    def open(self) -> None:
        if not self.media_path.is_file():
            raise FileNotFoundError(f"STREAMING_DECODER_MISSING_MEDIA: {self.media_path}")
        vf = f"scale={self.width}:{self.height}:flags={self.scale_flags}"
        if self.extra_vf:
            vf = f"{self.extra_vf},{vf}"
        cmd = [
            "ffmpeg", "-v", "error", "-nostdin",
            "-threads", str(self.ffmpeg_threads),
            "-i", str(self.media_path),
            "-vf", vf,
            "-fps_mode", self.fps_mode,
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]
        popen_kw: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "bufsize": self.frame_bytes * 2,
        }
        if os.name == "posix":
            popen_kw["start_new_session"] = True
        self.started_monotonic = time.monotonic()
        self._proc = subprocess.Popen(cmd, **popen_kw)

        def _drain_stderr(pipe: Any) -> None:
            try:
                while True:
                    chunk = pipe.readline(4096)
                    if not chunk:
                        break
                    self._stderr_lines.append(chunk.decode("utf-8", errors="replace").rstrip())
            except Exception:
                pass
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        self._stderr_thread = threading.Thread(target=_drain_stderr, args=(self._proc.stderr,), daemon=True)
        self._stderr_thread.start()

    def read_frame(self) -> bytes | None:
        """Read exact `frame_bytes` for the next decoded frame, or None on clean EOF. Raises on partial corrupt frame."""
        if self._proc is None or self._proc.stdout is None or self._closed:
            return None
        if (time.monotonic() - self.started_monotonic) > self.timeout_sec:
            self.close(force=True)
            raise TimeoutError(f"STREAMING_DECODER_TIMEOUT after {self.timeout_sec}s on {self.media_path.name}")

        buf = bytearray(self.frame_bytes)
        view = memoryview(buf)
        got = 0
        while got < self.frame_bytes:
            n = self._proc.stdout.readinto(view[got:])
            if not n:
                break
            got += n
        if got == 0:
            # Verify FFmpeg did not fail with an error before returning clean EOF
            if self._proc.poll() is None:
                try:
                    self._proc.wait(timeout=2.0)
                except Exception:
                    pass
            rc = self._proc.returncode
            if rc is not None and rc != 0 and self.frames_decoded == 0:
                self.close(force=False)
                raise RuntimeError(
                    f"STREAMING_DECODER_FAILED(rc={rc}) on {self.media_path.name}: {self.stderr_tail[:220]}"
                )
            return None
        if got < self.frame_bytes:
            self.truncated_tail_bytes = got
            raise ValueError(
                f"MALFORMED_PARTIAL_FRAME: expected {self.frame_bytes} bytes, got {got} bytes at frame {self.frames_decoded}"
            )
        self.frames_decoded += 1
        return bytes(buf)

    def skip_frames(self, count: int) -> int:
        """Advance and discard `count` frames in O(1) memory."""
        skipped = 0
        for _ in range(max(0, int(count))):
            raw = self.read_frame()
            if raw is None:
                break
            skipped += 1
        return skipped

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_lines).strip()

    def close(self, *, force: bool = False) -> dict[str, Any]:
        if self._closed:
            return self.cleanup_report
        self._closed = True
        proc = self._proc
        if proc is not None:
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
            if force:
                self.cleanup_report = terminate_process_tree(proc)
            else:
                try:
                    proc.wait(timeout=3.0)
                    self.cleanup_report = {"pid": proc.pid, "clean": True, "returncode": proc.returncode, "method": "clean_eof_wait"}
                except subprocess.TimeoutExpired:
                    self.cleanup_report = terminate_process_tree(proc)
        if self._stderr_thread is not None and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=1.5)
        self.cleanup_report["stderr_lines_captured"] = len(self._stderr_lines)
        self.cleanup_report["frames_decoded"] = self.frames_decoded
        return self.cleanup_report


def iter_edl_source_frames(edl: dict[str, Any] | None, fallback_total_frames: int) -> Iterator[tuple[int, int]]:
    """Lazy O(1)-memory iterator yielding `(out_frame_idx, expected_src_frame_idx)` across EDL segments."""
    if edl and edl.get("segments"):
        out_idx = 0
        for seg in edl["segments"]:
            s0 = int(seg["src_start_frame"])
            s1 = int(seg["src_end_frame"])
            for src_idx in range(s0, s1):
                yield out_idx, src_idx
                out_idx += 1
    else:
        for i in range(max(0, int(fallback_total_frames))):
            yield i, i


def count_edl_frames(edl: dict[str, Any] | None, fallback_total_frames: int) -> int:
    if edl and edl.get("segments"):
        return sum(max(0, int(seg["src_end_frame"]) - int(seg["src_start_frame"])) for seg in edl["segments"])
    return max(0, int(fallback_total_frames))


def stream_paired_frames(
    source_mp4: str | Path,
    final_mp4: str | Path,
    *,
    edl: dict[str, Any] | None,
    expected_total_frames: int,
    probe_w: int = 180,
    probe_h: int = 320,
    ffmpeg_threads: int = 2,
    timeout_sec: float = 300.0,
) -> Iterator[tuple[int, int, bytes | None, bytes | None, dict[str, Any]]]:
    """Stream source and final MP4s concurrently in O(1) RAM, advancing/discarding trimmed source frames on the fly.

    Yields `(out_idx, expected_src_idx, src_rgb_bytes, fin_rgb_bytes, stats_dict)` one frame at a time.
    Guarantees strict process-tree cleanup even on caller exception or early break.
    """
    src_dec = StreamingRGBFrameDecoder(source_mp4, probe_w, probe_h, ffmpeg_threads=ffmpeg_threads, timeout_sec=timeout_sec)
    fin_dec = StreamingRGBFrameDecoder(final_mp4, probe_w, probe_h, ffmpeg_threads=ffmpeg_threads, timeout_sec=timeout_sec)
    stats: dict[str, Any] = {
        "src_eof_early": False,
        "fin_eof_early": False,
        "fin_extra_frames": 0,
        "src_cleanup": {},
        "fin_cleanup": {},
    }
    cur_src_pos = -1
    cur_src_bytes: bytes | None = None
    errored = False
    try:
        src_dec.open()
        fin_dec.open()
        for out_idx, want_src_idx in iter_edl_source_frames(edl, expected_total_frames):
            fin_bytes = fin_dec.read_frame()
            if fin_bytes is None:
                stats["fin_eof_early"] = True
                yield out_idx, want_src_idx, None, None, stats
                break
            if want_src_idx < cur_src_pos:
                raise ValueError(
                    f"NON_MONOTONIC_EDL_SOURCE_FRAME: want {want_src_idx} < current stream pos {cur_src_pos}"
                )
            while cur_src_pos < want_src_idx:
                nxt = src_dec.read_frame()
                if nxt is None:
                    cur_src_bytes = None
                    stats["src_eof_early"] = True
                    break
                cur_src_pos += 1
                cur_src_bytes = nxt
            yield out_idx, want_src_idx, cur_src_bytes, fin_bytes, stats
        # Drain check: detect unexpected extra frames appended after expected EDL end
        if not stats["fin_eof_early"]:
            extra = 0
            while extra < 24 and fin_dec.read_frame() is not None:
                extra += 1
            stats["fin_extra_frames"] = extra
    except Exception:
        errored = True
        raise
    finally:
        stats["src_cleanup"] = src_dec.close(force=True)
        stats["fin_cleanup"] = fin_dec.close(force=(errored or stats["fin_extra_frames"] > 0))
        stats["src_stderr"] = src_dec.stderr_tail
        stats["fin_stderr"] = fin_dec.stderr_tail
        stats["src_frames_decoded"] = src_dec.frames_decoded
        stats["fin_frames_decoded"] = fin_dec.frames_decoded


def stream_audio_pcm_chunks(
    media_path: str | Path,
    *,
    sample_rate: int = 48000,
    channels: int = 1,
    chunk_sec: float = 2.0,
    timeout_sec: float = 180.0,
) -> Iterator[bytes]:
    """Stream decoded 16-bit signed PCM audio in bounded time chunks (never loads 30-min uncompressed PCM into RAM)."""
    mp = Path(media_path).resolve()
    chunk_bytes = max(1024, int(round(sample_rate * channels * 2 * chunk_sec)))
    cmd = [
        "ffmpeg", "-v", "error", "-nostdin", "-i", str(mp),
        "-vn", "-ac", str(channels), "-ar", str(sample_rate), "-f", "s16le", "pipe:1",
    ]
    popen_kw: dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "posix":
        popen_kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **popen_kw)
    t0 = time.monotonic()
    try:
        assert proc.stdout is not None
        while True:
            if (time.monotonic() - t0) > timeout_sec:
                raise TimeoutError(f"AUDIO_STREAM_TIMEOUT after {timeout_sec}s on {mp.name}")
            data = proc.stdout.read(chunk_bytes)
            if not data:
                break
            yield data
    finally:
        if proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass
        if proc.stderr:
            try:
                proc.stderr.close()
            except Exception:
                pass
        terminate_process_tree(proc)


class AtomicKeyedCache:
    """Content-addressed run-owned atomic cache with per-key lockfile, corruption recovery & bounded eviction."""

    def __init__(self, cache_root: str | Path, *, run_id: str, max_entries: int = 64) -> None:
        self.root = Path(cache_root).resolve()
        self.run_id = sanitize_identifier(run_id)
        self.max_entries = max(4, int(max_entries))
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / OWNERSHIP_MARKER
        if not marker.is_file():
            marker.write_text(self.run_id + "\n", encoding="utf-8")
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.invalidations = 0

    @staticmethod
    def build_key(identity: dict[str, Any]) -> str:
        return hash_artifact(identity)

    def _entry_path(self, key: str) -> Path:
        return self.root / f"{sanitize_identifier(key[:48])}.json"

    def get(self, key: str) -> Any | None:
        p = self._entry_path(key)
        with self._lock:
            if not p.is_file():
                self.misses += 1
                return None
            try:
                envelope = parse_json_strict(p)
                if envelope.get("cache_key") != key:
                    raise ValueError("cache_key mismatch")
                if hash_artifact(envelope.get("payload")) != envelope.get("payload_sha256"):
                    raise ValueError("corrupt payload sha256")
                self.hits += 1
                return envelope["payload"]
            except Exception:
                # Corrupt/stale cache entry -> atomically evict and treat as miss
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
                self.invalidations += 1
                self.misses += 1
                return None

    def put(self, key: str, payload: Any) -> Path:
        p = self._entry_path(key)
        envelope = {
            "schema_version": "kh03.cache_entry.v1",
            "cache_key": key,
            "run_id": self.run_id,
            "stored_at_utc": now_utc(),
            "payload_sha256": hash_artifact(payload),
            "payload": payload,
        }
        with self._lock:
            write_json_atomic(p, envelope)
            self._evict_if_needed()
        return p

    def get_or_compute(self, key: str, fn: Callable[[], Any]) -> tuple[Any, bool]:
        if not hasattr(self, "_key_locks"):
            self._key_locks: dict[str, threading.Lock] = {}
        with self._lock:
            klock = self._key_locks.setdefault(key, threading.Lock())
        with klock:
            cached = self.get(key)
            if cached is not None:
                return cached, True
            val = fn()
            self.put(key, val)
            return val, False

    def _evict_if_needed(self) -> None:
        marker = self.root / OWNERSHIP_MARKER
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != self.run_id:
            return
        files = sorted(
            [f for f in self.root.glob("*.json") if f.is_file()],
            key=lambda x: (x.stat().st_mtime, x.name),
        )
        while len(files) > self.max_entries:
            victim = files.pop(0)
            try:
                victim.unlink(missing_ok=True)
                self.invalidations += 1
            except Exception:
                break

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": "kh03.cache_integrity.v1",
            "run_id": self.run_id,
            "cache_root": str(self.root),
            "max_entries": self.max_entries,
            "hits": self.hits,
            "misses": self.misses,
            "invalidations": self.invalidations,
            "entries_on_disk": len(list(self.root.glob("*.json"))),
        }


@dataclass(frozen=True)
class DAGTask:
    task_id: str
    fn: Callable[[dict[str, Any]], Any]
    deps: tuple[str, ...] = ()
    pool: str = "cpu"  # "cpu" | "network" | "heavy" | "decoder"
    timeout_sec: float = 120.0
    retries: int = 0


class BoundedAsyncExecutor:
    """Deterministic bounded async DAG executor using asyncio.Semaphore + bounded ThreadPoolExecutor.

    Guarantees:
    - Tasks execute only after ALL dependencies succeed; any failure propagates immediately to dependents
      (dependent tasks are marked `BLOCKED_UPSTREAM_FAIL` and never execute).
    - Results, receipts, and trace are aggregated strictly sorted by `task_id` (NEVER completion order!),
      so workers=1, workers=2, and workers=4 yield identical canonical hashes!
    - Enforces per-pool semaphores (cpu/workers, network<=2, heavy=1, decoder<=2) and monitors RSS/disk high-water.
    """

    def __init__(
        self,
        *,
        run_id: str,
        budget: dict[str, Any],
        run_dir: Path | None = None,
    ) -> None:
        self.run_id = run_id
        self.budget = dict(budget)
        self.run_dir = run_dir
        self.workers = max(1, min(4, int(budget.get("workers", 2))))
        self.network_workers = max(1, min(2, int(budget.get("network_workers", 2))))
        self.heavy_concurrency = 1
        self.decoder_pool = max(1, min(2, int(budget.get("decoder_pool", 2))))
        self.peak_rss_mib = get_process_rss_mib()
        self.min_free_disk_gib = float(budget.get("free_disk_gib", 10.0))

    def _validate_dag(self, tasks: list[DAGTask]) -> dict[str, DAGTask]:
        by_id: dict[str, DAGTask] = {}
        for t in tasks:
            tid = sanitize_identifier(t.task_id)
            if tid in by_id:
                raise ValueError(f"DUPLICATE_DAG_TASK_ID: {tid}")
            by_id[tid] = t
        # Cycle & missing-dependency check (Kahn's algorithm)
        indeg = {tid: 0 for tid in by_id}
        adj: dict[str, list[str]] = {tid: [] for tid in by_id}
        for tid, t in by_id.items():
            for dep in t.deps:
                if dep not in by_id:
                    raise ValueError(f"MISSING_DAG_DEPENDENCY: {tid} depends on unknown {dep!r}")
                adj[dep].append(tid)
                indeg[tid] += 1
        q = deque(sorted(tid for tid, d in indeg.items() if d == 0))
        visited = 0
        while q:
            u = q.popleft()
            visited += 1
            for v in sorted(adj[u]):
                indeg[v] -= 1
                if indeg[v] == 0:
                    q.append(v)
        if visited != len(by_id):
            raise ValueError("DAG_CYCLE_DETECTED: task graph contains a dependency cycle.")
        return by_id

    async def _run_async(self, tasks: list[DAGTask]) -> dict[str, Any]:
        by_id = self._validate_dag(tasks)
        semaphores = {
            "cpu": asyncio.Semaphore(self.workers),
            "network": asyncio.Semaphore(self.network_workers),
            "heavy": asyncio.Semaphore(self.heavy_concurrency),
            "decoder": asyncio.Semaphore(self.decoder_pool),
        }
        results: dict[str, Any] = {}
        receipts: dict[str, dict[str, Any]] = {}
        trace_events: list[dict[str, Any]] = []
        done_events: dict[str, asyncio.Event] = {tid: asyncio.Event() for tid in by_id}
        t_start_all = time.monotonic()

        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="kh03_dag") as pool:
            loop = asyncio.get_running_loop()

            async def _run_one(t: DAGTask) -> None:
                tid = t.task_id
                try:
                    # Wait for all upstream dependencies
                    for dep in t.deps:
                        await done_events[dep].wait()
                    failed_deps = [d for d in t.deps if receipts.get(d, {}).get("status") != "PASS"]
                    if failed_deps:
                        receipts[tid] = {
                            "task_id": tid,
                            "deps": list(t.deps),
                            "pool": t.pool,
                            "status": "BLOCKED_UPSTREAM_FAIL",
                            "reason": f"Upstream failed/cancelled: {failed_deps}",
                            "output_sha256": None,
                            "duration_sec": 0.0,
                            "rss_mib": get_process_rss_mib(),
                        }
                        trace_events.append({"task_id": tid, "event": "blocked_upstream", "deps": failed_deps})
                        return

                    dep_outputs = {d: results[d] for d in sorted(t.deps)}
                    sem = semaphores.get(t.pool, semaphores["cpu"])
                    async with sem:
                        t0 = time.monotonic()
                        trace_events.append({"task_id": tid, "event": "start", "pool": t.pool})
                        last_err: Exception | None = None
                        for attempt in range(max(0, int(t.retries)) + 1):
                            try:
                                val = await asyncio.wait_for(
                                    loop.run_in_executor(pool, t.fn, dep_outputs),
                                    timeout=t.timeout_sec,
                                )
                                dur = round(time.monotonic() - t0, 4)
                                rss = get_process_rss_mib()
                                self.peak_rss_mib = max(self.peak_rss_mib, rss)
                                results[tid] = val
                                out_sha = hash_artifact(val) if isinstance(val, (dict, list, str, int, float, bool)) else None
                                receipts[tid] = {
                                    "task_id": tid,
                                    "deps": list(t.deps),
                                    "pool": t.pool,
                                    "status": "PASS",
                                    "attempts": attempt + 1,
                                    "output_sha256": out_sha,
                                    "duration_sec": dur,
                                    "rss_mib": rss,
                                }
                                trace_events.append({"task_id": tid, "event": "pass", "duration_sec": dur, "rss_mib": rss})
                                last_err = None
                                break
                            except asyncio.TimeoutError as ex:
                                last_err = TimeoutError(f"TASK_TIMEOUT({tid}): exceeded {t.timeout_sec}s")
                                break
                            except asyncio.CancelledError:
                                receipts[tid] = {
                                    "task_id": tid,
                                    "deps": list(t.deps),
                                    "pool": t.pool,
                                    "status": "CANCELLED",
                                    "reason": "Task cancelled cleanly.",
                                    "output_sha256": None,
                                    "duration_sec": round(time.monotonic() - t0, 4),
                                    "rss_mib": get_process_rss_mib(),
                                }
                                trace_events.append({"task_id": tid, "event": "cancelled"})
                                raise
                            except Exception as ex:
                                last_err = ex
                        if last_err is not None:
                            dur = round(time.monotonic() - t0, 4)
                            receipts[tid] = {
                                "task_id": tid,
                                "deps": list(t.deps),
                                "pool": t.pool,
                                "status": "TIMEOUT" if isinstance(last_err, TimeoutError) else "FAIL",
                                "reason": str(last_err)[:300],
                                "output_sha256": None,
                                "duration_sec": dur,
                                "rss_mib": get_process_rss_mib(),
                            }
                            trace_events.append({"task_id": tid, "event": "fail", "reason": str(last_err)[:200]})
                finally:
                    done_events[tid].set()

            async with asyncio.TaskGroup() as tg:
                for tid in sorted(by_id.keys()):
                    tg.create_task(_run_one(by_id[tid]))

        wall_sec = round(time.monotonic() - t_start_all, 4)
        # Canonical sorted aggregation (order-independent across workers=1/2/4!)
        ordered_ids = sorted(by_id.keys())
        ordered_receipts = [receipts[tid] for tid in ordered_ids]
        ordered_results = {tid: results[tid] for tid in ordered_ids if tid in results}
        all_ok = all(r["status"] == "PASS" for r in ordered_receipts)

        dag_doc = {
            "schema_version": "kh03.task_dag.v1",
            "run_id": self.run_id,
            "workers": self.workers,
            "nodes": [{"task_id": tid, "deps": list(by_id[tid].deps), "pool": by_id[tid].pool} for tid in ordered_ids],
            "all_pass": all_ok,
            "canonical_output_hashes": {r["task_id"]: r["output_sha256"] for r in ordered_receipts},
        }
        dag_doc["canonical_dag_sha256"] = hash_artifact(dag_doc["canonical_output_hashes"])

        if self.run_dir is not None:
            mdir = self.run_dir / "manifests"
            mdir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(mdir / "TASK_DAG.json", dag_doc)
            # Atomic JSONL write for receipts and trace
            rec_tmp = mdir / "TASK_RECEIPTS.jsonl.part"
            rec_tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in ordered_receipts) + "\n", encoding="utf-8")
            rec_tmp.replace(mdir / "TASK_RECEIPTS.jsonl")
            tr_tmp = mdir / "EXECUTION_TRACE.jsonl.part"
            tr_tmp.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in trace_events) + "\n", encoding="utf-8")
            tr_tmp.replace(mdir / "EXECUTION_TRACE.jsonl")
            du_end = shutil.disk_usage(str(self.run_dir))
            free_end = round(du_end.free / (1024.0 ** 3), 3)
            self.min_free_disk_gib = min(self.min_free_disk_gib, free_end)
            write_json_atomic(mdir / "DISK_HIGH_WATER.json", {
                "schema_version": "kh03.disk_high_water.v1",
                "run_id": self.run_id,
                "initial_free_disk_gib": self.budget.get("free_disk_gib"),
                "final_free_disk_gib": free_end,
                "min_free_disk_gib": self.min_free_disk_gib,
                "peak_rss_mib": self.peak_rss_mib,
                "memory_budget_mib": self.budget.get("memory_budget_mib"),
                "wall_sec": wall_sec,
            })

        return {
            "pass": all_ok,
            "workers": self.workers,
            "wall_sec": wall_sec,
            "peak_rss_mib": self.peak_rss_mib,
            "dag": dag_doc,
            "receipts": ordered_receipts,
            "results": ordered_results,
            "canonical_dag_sha256": dag_doc["canonical_dag_sha256"],
        }

    def run(self, tasks: list[DAGTask]) -> dict[str, Any]:
        """Run DAG synchronously via a dedicated event loop (safe inside sync CLI or existing event loops)."""
        try:
            asyncio.get_running_loop()
            # Inside an existing running loop: run cleanly on a helper thread
            with ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: asyncio.run(self._run_async(tasks))).result()
        except RuntimeError:
            return asyncio.run(self._run_async(tasks))
