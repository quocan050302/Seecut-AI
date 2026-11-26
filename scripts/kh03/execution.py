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
try:
    import resource as _resource
except ImportError:
    _resource = None
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
    """Measure current process high-water RSS in MiB cleanly across macOS, Linux, and Windows."""
    try:
        if _resource is not None:
            ru = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
            # macOS returns bytes; Linux returns KiB
            if platform.system().lower() == "darwin":
                return round(ru / (1024.0 * 1024.0), 2)
            return round(ru / 1024.0, 2)
        if platform.system().lower() == "windows":
            import ctypes

            class _PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            hproc = k32.GetCurrentProcess()
            fn = getattr(k32, "K32GetProcessMemoryInfo", None)
            if fn is None:
                fn = ctypes.windll.psapi.GetProcessMemoryInfo
            fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
            fn.restype = ctypes.c_int
            if fn(hproc, ctypes.byref(pmc), pmc.cb):
                val = pmc.PeakWorkingSetSize or pmc.WorkingSetSize
                return round(val / (1024.0 * 1024.0), 2)
    except Exception:
        pass
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
        elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
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


class CancellationToken:
    """Thread-safe cooperative cancellation token with monotonic timestamp & reason (PED-10)."""

    def __init__(self) -> None:
        self._ev = threading.Event()
        self._lock = threading.Lock()
        self._reason: str | None = None
        self._cancelled_at: str | None = None

    @property
    def is_cancelled(self) -> bool:
        return self._ev.is_set()

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    @property
    def cancelled_at_utc(self) -> str | None:
        with self._lock:
            return self._cancelled_at

    def cancel(self, reason: str = "CANCELLED") -> None:
        with self._lock:
            if not self._ev.is_set():
                self._reason = str(reason)
                self._cancelled_at = now_utc()
                self._ev.set()

    def check(self) -> None:
        if self._ev.is_set():
            raise CancelledTaskError(self.reason or "CANCELLED")


class CancelledTaskError(RuntimeError):
    """Raised cooperatively when a task's CancellationToken or lease is revoked."""


class LateWriteRejectedError(PermissionError):
    """Raised when a cancelled/expired worker attempts to publish to coordinator-owned outputs."""


class TaskLease:
    """Coordinator-owned generation lease binding a single task attempt to an isolated staging dir."""

    def __init__(self, *, task_id: str, attempt: int, lease_id: str, staging_dir: Path) -> None:
        self.task_id = sanitize_identifier(task_id)
        self.attempt = int(attempt)
        self.lease_id = str(lease_id)
        self.staging_dir = staging_dir
        self._lock = threading.Lock()
        self._active = True
        self._revoked_reason: str | None = None

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def revoked_reason(self) -> str | None:
        with self._lock:
            return self._revoked_reason

    def revoke(self, reason: str = "LEASE_REVOKED") -> None:
        with self._lock:
            if self._active:
                self._active = False
                self._revoked_reason = str(reason)


class CoordinatorPublisher:
    """Single-coordinator atomic publisher enforcing active lease, no cancellation, path containment, and schema/SHA validation."""

    def __init__(self, *, allowed_root: Path | None = None) -> None:
        self._lock = threading.Lock()
        self.allowed_root = Path(os.path.realpath(str(allowed_root))) if allowed_root is not None else None
        self.published: list[dict[str, Any]] = []
        self.rejected: list[dict[str, Any]] = []

    def _validate_target_path(self, final_path: Path) -> Path:
        # Reject symlink components & path traversal outside allowed_root
        raw_parts = Path(final_path).parts
        if ".." in raw_parts:
            raise ValueError(f"PATH_TRAVERSAL_REJECTED: '..' forbidden in target {final_path}")
        if final_path.is_symlink():
            raise ValueError(f"SYMLINK_TARGET_REJECTED: {final_path}")
        cur = final_path.parent
        checked_ancestors = 0
        while cur != cur.parent and checked_ancestors < 8:
            if cur.is_symlink():
                raise ValueError(f"SYMLINK_ANCESTOR_REJECTED: {cur}")
            cur = cur.parent
            checked_ancestors += 1
        real_final = Path(os.path.realpath(str(final_path)))
        if self.allowed_root is not None:
            root_s = str(self.allowed_root)
            fin_s = str(real_final)
            if os.name == "nt":
                root_cmp, fin_cmp = root_s.lower(), fin_s.lower()
            else:
                root_cmp, fin_cmp = root_s, fin_s
            if fin_cmp != root_cmp and not fin_cmp.startswith(root_cmp + os.sep):
                raise ValueError(f"OUTSIDE_ALLOWED_ROOT_REJECTED: {real_final} outside {self.allowed_root}")
        return real_final

    def publish_staged_file(
        self,
        lease: TaskLease,
        cancel_token: CancellationToken,
        staged_path: Path,
        final_path: Path,
        *,
        schema_name: str | None = None,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if not lease.is_active or cancel_token.is_cancelled:
                rec = {
                    "task_id": lease.task_id,
                    "attempt": lease.attempt,
                    "lease_id": lease.lease_id,
                    "final_path": str(final_path),
                    "status": "LATE_WRITE_REJECTED",
                    "reason": lease.revoked_reason or cancel_token.reason or "LEASE_INACTIVE",
                    "at_utc": now_utc(),
                }
                self.rejected.append(rec)
                raise LateWriteRejectedError(
                    f"LATE_WRITE_REJECTED({lease.task_id}#{lease.attempt}): {rec['reason']}"
                )
            if staged_path.is_symlink():
                raise ValueError(f"SYMLINK_STAGED_PATH_REJECTED: {staged_path}")
            st_real = Path(os.path.realpath(str(staged_path)))
            stage_root = Path(os.path.realpath(str(lease.staging_dir)))
            st_cmp = str(st_real).lower() if os.name == "nt" else str(st_real)
            sr_cmp = str(stage_root).lower() if os.name == "nt" else str(stage_root)
            if not st_cmp.startswith(sr_cmp + os.sep) or not st_real.is_file():
                raise ValueError(f"INVALID_STAGED_PATH: {staged_path} outside {lease.staging_dir}")
            target_real = self._validate_target_path(final_path)
            if schema_name is not None:
                doc = parse_json_strict(st_real)
                assert_kh03_schema(doc, schema_name)
            sha = hash_artifact(st_real)
            if expected_sha256 is not None and sha != expected_sha256:
                raise ValueError(f"STAGED_SHA_MISMATCH: {sha[:12]} != {expected_sha256[:12]}")
            target_real.parent.mkdir(parents=True, exist_ok=True)
            tmp_dst = target_real.parent / f".{target_real.name}.{lease.lease_id}.part"
            shutil.copy2(st_real, tmp_dst)
            for attempt_r in range(5):
                try:
                    os.replace(tmp_dst, target_real)
                    break
                except PermissionError:
                    if attempt_r == 4:
                        raise
                    time.sleep(0.03 * (attempt_r + 1))
            rec = {
                "task_id": lease.task_id,
                "attempt": lease.attempt,
                "lease_id": lease.lease_id,
                "final_path": str(target_real),
                "sha256": sha,
                "status": "PUBLISHED",
                "at_utc": now_utc(),
            }
            self.published.append(rec)
            return rec


def run_subprocess_isolated(
    cmd: list[str],
    *,
    cancel_token: CancellationToken | None = None,
    timeout_sec: float = 120.0,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    poll_interval_sec: float = 0.05,
    grace_sec: float = 0.35,
) -> dict[str, Any]:
    """Run external process in isolated OS process group with hard kill/reap on timeout or cancellation (PED-10)."""
    t0 = time.monotonic()
    popen_kw: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "cwd": str(cwd) if cwd else None,
        "env": env,
    }
    if os.name == "posix":
        popen_kw["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(cmd, **popen_kw)
    timed_out = False
    cancelled = False
    cleanup: dict[str, Any] = {"pid": proc.pid, "clean": True, "already_exited": False, "method": "normal_exit"}
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _drain(pipe: Any, sink: list[str]) -> None:
        if pipe is None:
            return
        try:
            sink.append(pipe.read())
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
    t_out.start()
    t_err.start()

    try:
        while proc.poll() is None:
            if cancel_token is not None and cancel_token.is_cancelled:
                cancelled = True
                cleanup = terminate_process_tree(proc, grace_sec=grace_sec)
                break
            if (time.monotonic() - t0) >= float(timeout_sec):
                timed_out = True
                cleanup = terminate_process_tree(proc, grace_sec=grace_sec)
                break
            time.sleep(max(0.01, float(poll_interval_sec)))
    finally:
        if proc.poll() is None:
            cleanup = terminate_process_tree(proc, grace_sec=grace_sec)
        t_out.join(timeout=1.5)
        t_err.join(timeout=1.5)

    rc = proc.returncode if proc.returncode is not None else -9
    return {
        "cmd": list(cmd),
        "pid": proc.pid,
        "returncode": rc,
        "stdout": "".join(stdout_chunks),
        "stderr": "".join(stderr_chunks),
        "timed_out": timed_out,
        "cancelled": cancelled,
        "wall_sec": round(time.monotonic() - t0, 4),
        "cleanup": cleanup,
        "reaped": proc.poll() is not None,
    }


@dataclass
class TaskContext:
    """Execution context handed to cooperative 2-arg DAG tasks `fn(deps, ctx)`."""

    task_id: str
    attempt: int
    lease: TaskLease
    cancel_token: CancellationToken
    staging_dir: Path
    publisher: CoordinatorPublisher

    def check_cancelled(self) -> None:
        if self.cancel_token.is_cancelled or not self.lease.is_active:
            raise CancelledTaskError(self.cancel_token.reason or self.lease.revoked_reason or "CANCELLED")

    def stage_json(self, rel_name: str, payload: Any) -> Path:
        self.check_cancelled()
        safe_rel = Path(rel_name).name
        dst = self.staging_dir / safe_rel
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(dst, payload)
        return dst

    def stage_text(self, rel_name: str, content: str) -> Path:
        self.check_cancelled()
        safe_rel = Path(rel_name).name
        dst = self.staging_dir / safe_rel
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
        return dst

    def stage_bytes(self, rel_name: str, data: bytes) -> Path:
        self.check_cancelled()
        safe_rel = Path(rel_name).name
        dst = self.staging_dir / safe_rel
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return dst

    def publish_json(self, rel_name: str, payload: Any, final_path: Path, *, schema_name: str | None = None) -> dict[str, Any]:
        st = self.stage_json(rel_name, payload)
        return self.publisher.publish_staged_file(self.lease, self.cancel_token, st, final_path, schema_name=schema_name)

    def publish_text(self, rel_name: str, content: str, final_path: Path) -> dict[str, Any]:
        st = self.stage_text(rel_name, content)
        return self.publisher.publish_staged_file(self.lease, self.cancel_token, st, final_path)

    def publish_file(self, staged_path: Path, final_path: Path, *, schema_name: str | None = None) -> dict[str, Any]:
        self.check_cancelled()
        return self.publisher.publish_staged_file(self.lease, self.cancel_token, staged_path, final_path, schema_name=schema_name)

    def run_subprocess(self, cmd: list[str], *, timeout_sec: float = 120.0, cwd: str | Path | None = None) -> dict[str, Any]:
        self.check_cancelled()
        return run_subprocess_isolated(cmd, cancel_token=self.cancel_token, timeout_sec=timeout_sec, cwd=cwd)


@dataclass(frozen=True)
class DAGTask:
    task_id: str
    fn: Callable[..., Any]
    deps: tuple[str, ...] = ()
    pool: str = "cpu"  # "cpu" | "network" | "heavy" | "decoder"
    timeout_sec: float = 120.0
    retries: int = 0
    retry_backoff_sec: float = 0.08
    fail_fast_global: bool = False


class BoundedAsyncExecutor:
    """Hardened deterministic bounded async DAG executor (CMI-10 + PED-10).

    Guarantees:
    - Dependency DAG enforcement with cycle/missing-dep rejection and upstream fail-fast propagation.
    - Cooperative CancellationToken + per-attempt TaskLease & isolated staging directory.
    - Coordinator-only atomic publishing rejecting late/ghost writes (`LATE_WRITE_REJECTED`).
    - Worker termination verification & quarantine (`BLOCKED_CANCELLATION`) when an uncooperative
      thread refuses to stop within grace after timeout/cancel — never spawns overlapping retry.
    - Mid-flight disk reserve check (`BLOCKED_LOW_DISK`), bounded retries with exponential backoff,
      and canonical sorted aggregation (`workers=1 == 2 == 4`).
    """

    def __init__(
        self,
        *,
        run_id: str,
        budget: dict[str, Any],
        run_dir: Path | None = None,
        reap_grace_sec: float = 0.35,
    ) -> None:
        self.run_id = run_id
        self.budget = dict(budget)
        self.run_dir = run_dir
        self.workers = max(1, min(4, int(budget.get("workers", 2))))
        self.network_workers = max(1, min(2, int(budget.get("network_workers", 2))))
        self.heavy_concurrency = 1
        self.decoder_pool = max(1, min(2, int(budget.get("decoder_pool", 2))))
        self.reap_grace_sec = max(0.05, float(reap_grace_sec))
        self.peak_rss_mib = get_process_rss_mib()
        self.min_free_disk_gib = float(budget.get("free_disk_gib", 10.0))
        self.disk_reserve_gib = float(budget.get("disk_reserve_gib", DEFAULT_DISK_RESERVE_GIB))
        self.publisher = CoordinatorPublisher(allowed_root=run_dir)
        self.global_cancel = CancellationToken()

    def _validate_dag(self, tasks: list[DAGTask]) -> dict[str, DAGTask]:
        by_id: dict[str, DAGTask] = {}
        for t in tasks:
            tid = sanitize_identifier(t.task_id)
            if tid in by_id:
                raise ValueError(f"DUPLICATE_DAG_TASK_ID: {tid}")
            by_id[tid] = t
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

    @staticmethod
    def _accepts_context(fn: Callable[..., Any]) -> bool:
        import inspect
        try:
            sig = inspect.signature(fn)
            params = [
                p for p in sig.parameters.values()
                if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            has_var = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
            return has_var or len(params) >= 2
        except Exception:
            return False

    def _check_disk_ok(self) -> tuple[bool, float]:
        if self.run_dir is None:
            return True, self.min_free_disk_gib
        try:
            chk = self.run_dir if self.run_dir.exists() else self.run_dir.parent
            free_gib = round(shutil.disk_usage(str(chk)).free / (1024.0 ** 3), 3)
            self.min_free_disk_gib = min(self.min_free_disk_gib, free_gib)
            return (free_gib >= self.disk_reserve_gib), free_gib
        except Exception:
            return True, self.min_free_disk_gib

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
        cancellation_events: list[dict[str, Any]] = []
        quarantined_workers: list[dict[str, Any]] = []
        done_events: dict[str, asyncio.Event] = {tid: asyncio.Event() for tid in by_id}
        t_start_all = time.monotonic()

        staging_base = (self.run_dir / "staging" / "dag_attempts") if self.run_dir is not None else None
        if staging_base is not None:
            staging_base.mkdir(parents=True, exist_ok=True)
            (staging_base / OWNERSHIP_MARKER).write_text(f"{self.run_id}\n", encoding="utf-8")

        # Cooperative SIGINT/SIGTERM wiring when running on main thread
        prev_sigint = None
        prev_sigterm = None
        installed_sigs = False
        if threading.current_thread() is threading.main_thread():
            def _on_sig(signum: int, _frame: Any) -> None:
                self.global_cancel.cancel(f"SIGNAL_{signum}")
            try:
                prev_sigint = signal.getsignal(signal.SIGINT)
                prev_sigterm = signal.getsignal(signal.SIGTERM)
                signal.signal(signal.SIGINT, _on_sig)
                signal.signal(signal.SIGTERM, _on_sig)
                installed_sigs = True
            except Exception:
                installed_sigs = False

        async def _run_one(t: DAGTask) -> None:
            tid = t.task_id
            takes_ctx = self._accepts_context(t.fn)
            try:
                for dep in t.deps:
                    await done_events[dep].wait()

                if self.global_cancel.is_cancelled:
                    receipts[tid] = {
                        "task_id": tid, "deps": list(t.deps), "pool": t.pool,
                        "status": "CANCELLED", "reason": self.global_cancel.reason or "GLOBAL_CANCEL",
                        "output_sha256": None, "duration_sec": 0.0, "rss_mib": get_process_rss_mib(),
                    }
                    trace_events.append({"task_id": tid, "event": "cancelled_before_start", "reason": self.global_cancel.reason})
                    return

                failed_deps = [d for d in t.deps if receipts.get(d, {}).get("status") != "PASS"]
                if failed_deps:
                    receipts[tid] = {
                        "task_id": tid, "deps": list(t.deps), "pool": t.pool,
                        "status": "BLOCKED_UPSTREAM_FAIL", "reason": f"Upstream failed/cancelled: {failed_deps}",
                        "output_sha256": None, "duration_sec": 0.0, "rss_mib": get_process_rss_mib(),
                    }
                    trace_events.append({"task_id": tid, "event": "blocked_upstream", "deps": failed_deps})
                    return

                disk_ok, free_gib = self._check_disk_ok()
                if not disk_ok:
                    self.global_cancel.cancel(f"BLOCKED_LOW_DISK({free_gib}GiB<{self.disk_reserve_gib}GiB)")
                    receipts[tid] = {
                        "task_id": tid, "deps": list(t.deps), "pool": t.pool,
                        "status": "BLOCKED_LOW_DISK",
                        "reason": f"Mid-flight free disk {free_gib:.2f} GiB below reserve {self.disk_reserve_gib:.2f} GiB.",
                        "output_sha256": None, "duration_sec": 0.0, "rss_mib": get_process_rss_mib(),
                    }
                    trace_events.append({"task_id": tid, "event": "blocked_low_disk", "free_disk_gib": free_gib})
                    return

                dep_outputs = {d: results[d] for d in sorted(t.deps)}
                t_queued = time.monotonic()
                sem = semaphores.get(t.pool, semaphores["cpu"])
                async with sem:
                    t0 = time.monotonic()
                    wait_sec = round(max(0.0, t0 - t_queued), 4)
                    trace_events.append({"task_id": tid, "event": "start", "pool": t.pool, "rel_start_sec": round(t0 - t_start_all, 4), "wait_sec": wait_sec, "ctx_aware": takes_ctx})
                    last_err: Exception | None = None
                    final_status: str | None = None
                    max_attempts = max(0, int(t.retries)) + 1

                    for attempt in range(1, max_attempts + 1):
                        if self.global_cancel.is_cancelled:
                            final_status = "CANCELLED"
                            last_err = CancelledTaskError(self.global_cancel.reason or "GLOBAL_CANCEL")
                            break

                        lease_id = f"{sanitize_identifier(tid)}_a{attempt}_{hash_artifact([self.run_id, tid, attempt])[:8]}"
                        stage_dir = (staging_base / lease_id) if staging_base is not None else Path(os.path.join(os.path.expanduser("~"), ".kh03_tmp_noop", lease_id))
                        if staging_base is not None:
                            stage_dir.mkdir(parents=True, exist_ok=True)
                        lease = TaskLease(task_id=tid, attempt=attempt, lease_id=lease_id, staging_dir=stage_dir)
                        tok = CancellationToken()
                        ctx = TaskContext(
                            task_id=tid, attempt=attempt, lease=lease,
                            cancel_token=tok, staging_dir=stage_dir, publisher=self.publisher,
                        )
                        box: dict[str, Any] = {"done": False, "val": None, "err": None}

                        def _worker_entry() -> None:
                            try:
                                if self.global_cancel.is_cancelled:
                                    tok.cancel(self.global_cancel.reason or "GLOBAL_CANCEL")
                                res_v = t.fn(dep_outputs, ctx) if takes_ctx else t.fn(dep_outputs)
                                box["val"] = res_v
                            except Exception as exc:
                                box["err"] = exc
                            finally:
                                box["done"] = True

                        th = threading.Thread(target=_worker_entry, name=f"kh03_{tid}_a{attempt}", daemon=True)
                        th.start()
                        deadline = time.monotonic() + float(t.timeout_sec)
                        timed_out = False
                        while th.is_alive():
                            if self.global_cancel.is_cancelled:
                                lease.revoke(self.global_cancel.reason or "GLOBAL_CANCEL")
                                tok.cancel(self.global_cancel.reason or "GLOBAL_CANCEL")
                                break
                            if time.monotonic() >= deadline:
                                timed_out = True
                                lease.revoke(f"TASK_TIMEOUT({tid}:{t.timeout_sec}s)")
                                tok.cancel(f"TASK_TIMEOUT({tid}:{t.timeout_sec}s)")
                                break
                            await asyncio.sleep(0.015)

                        if timed_out or self.global_cancel.is_cancelled:
                            # Wait bounded grace for worker thread to exit cleanly; NEVER silently reuse staging if still alive!
                            grace_end = time.monotonic() + self.reap_grace_sec
                            while th.is_alive() and time.monotonic() < grace_end:
                                await asyncio.sleep(0.015)
                            stopped_cleanly = not th.is_alive()
                            ev_rec = {
                                "task_id": tid,
                                "attempt": attempt,
                                "lease_id": lease_id,
                                "reason": lease.revoked_reason or ("TIMEOUT" if timed_out else "CANCELLED"),
                                "lease_revoked": not lease.is_active,
                                "worker_stopped_cleanly": stopped_cleanly,
                                "quarantined": not stopped_cleanly,
                                "at_utc": now_utc(),
                            }
                            cancellation_events.append(ev_rec)
                            if not stopped_cleanly:
                                quarantined_workers.append(ev_rec)
                                final_status = "BLOCKED_CANCELLATION"
                                last_err = TimeoutError(f"BLOCKED_CANCELLATION({tid}): uncooperative worker quarantined after {t.timeout_sec}s")
                                trace_events.append({"task_id": tid, "event": "quarantined", "attempt": attempt})
                                # Do NOT spawn overlapping retry while old worker is still alive!
                                break
                            # Clean cooperative stop -> safe to clean staging dir
                            if staging_base is not None and stage_dir.is_dir():
                                shutil.rmtree(stage_dir, ignore_errors=True)
                            final_status = "TIMEOUT" if timed_out else "CANCELLED"
                            last_err = TimeoutError(f"TASK_TIMEOUT({tid}): exceeded {t.timeout_sec}s") if timed_out else CancelledTaskError("CANCELLED")
                            if timed_out and attempt < max_attempts and not self.global_cancel.is_cancelled:
                                trace_events.append({"task_id": tid, "event": "retry_after_clean_timeout", "attempt": attempt})
                                await asyncio.sleep(min(1.0, t.retry_backoff_sec * (2 ** (attempt - 1))))
                                continue
                            break

                        th.join(timeout=0.05)
                        if box["err"] is not None:
                            err_obj = box["err"]
                            lease.revoke(f"TASK_ERROR({type(err_obj).__name__})")
                            if staging_base is not None and stage_dir.is_dir():
                                shutil.rmtree(stage_dir, ignore_errors=True)
                            last_err = err_obj
                            if isinstance(err_obj, (CancelledTaskError, LateWriteRejectedError, PermissionError, ValueError)):
                                final_status = "CANCELLED" if isinstance(err_obj, CancelledTaskError) else "FAIL"
                                break
                            if attempt < max_attempts:
                                trace_events.append({"task_id": tid, "event": "retry_transient", "attempt": attempt, "error": str(err_obj)[:160]})
                                await asyncio.sleep(min(1.0, t.retry_backoff_sec * (2 ** (attempt - 1))))
                                continue
                            final_status = "FAIL"
                            break

                        # Worker succeeded cleanly -> verify lease still active before committing result
                        if not lease.is_active or self.global_cancel.is_cancelled:
                            final_status = "CANCELLED"
                            last_err = LateWriteRejectedError(f"LATE_RESULT_DISCARDED({tid})")
                            break

                        val = box["val"]
                        t_end = time.monotonic()
                        dur = round(t_end - t0, 4)
                        rss = get_process_rss_mib()
                        self.peak_rss_mib = max(self.peak_rss_mib, rss)
                        results[tid] = val
                        out_sha = hash_artifact(val) if isinstance(val, (dict, list, str, int, float, bool)) else None
                        receipts[tid] = {
                            "task_id": tid, "deps": list(t.deps), "pool": t.pool,
                            "status": "PASS", "attempts": attempt, "lease_id": lease_id,
                            "ctx_aware": takes_ctx,
                            "rel_start_sec": round(t0 - t_start_all, 4),
                            "rel_end_sec": round(t_end - t_start_all, 4),
                            "wait_sec": wait_sec,
                            "output_sha256": out_sha, "duration_sec": dur, "rss_mib": rss,
                        }
                        trace_events.append({"task_id": tid, "event": "pass", "attempts": attempt, "rel_end_sec": round(t_end - t_start_all, 4), "duration_sec": dur, "rss_mib": rss})
                        lease.revoke("COMPLETED_OK")
                        if staging_base is not None and stage_dir.is_dir():
                            shutil.rmtree(stage_dir, ignore_errors=True)
                        last_err = None
                        final_status = "PASS"
                        break

                    if final_status != "PASS":
                        dur = round(time.monotonic() - t0, 4)
                        if t.fail_fast_global:
                            self.global_cancel.cancel(f"FAIL_FAST({tid}): {last_err}")
                        receipts[tid] = {
                            "task_id": tid, "deps": list(t.deps), "pool": t.pool,
                            "status": final_status or "FAIL",
                            "reason": str(last_err)[:300] if last_err else "FAILED",
                            "output_sha256": None, "duration_sec": dur, "rss_mib": get_process_rss_mib(),
                        }
                        trace_events.append({"task_id": tid, "event": (final_status or "fail").lower(), "reason": str(last_err)[:200]})
            finally:
                done_events[tid].set()

        try:
            async with asyncio.TaskGroup() as tg:
                for tid in sorted(by_id.keys()):
                    tg.create_task(_run_one(by_id[tid]))
        finally:
            if installed_sigs:
                try:
                    signal.signal(signal.SIGINT, prev_sigint)
                    signal.signal(signal.SIGTERM, prev_sigterm)
                except Exception:
                    pass

        wall_sec = round(time.monotonic() - t_start_all, 4)
        ordered_ids = sorted(by_id.keys())
        ordered_receipts = [receipts[tid] for tid in ordered_ids]
        ordered_results = {tid: results[tid] for tid in ordered_ids if tid in results}
        blocked_cancellation = len(quarantined_workers) > 0
        all_ok = all(r["status"] == "PASS" for r in ordered_receipts) and not blocked_cancellation

        dag_doc = {
            "schema_version": "kh03.task_dag.v1",
            "run_id": self.run_id,
            "workers": self.workers,
            "nodes": [{"task_id": tid, "deps": list(by_id[tid].deps), "pool": by_id[tid].pool} for tid in ordered_ids],
            "all_pass": all_ok,
            "blocked_cancellation": blocked_cancellation,
            "quarantined_worker_count": len(quarantined_workers),
            "late_writes_rejected": len(self.publisher.rejected),
            "canonical_output_hashes": {r["task_id"]: r["output_sha256"] for r in ordered_receipts},
        }
        dag_doc["canonical_dag_sha256"] = hash_artifact(dag_doc["canonical_output_hashes"])

        cancel_proof = {
            "schema_version": "kh03.cancellation_proof.v1",
            "run_id": self.run_id,
            "global_cancelled": self.global_cancel.is_cancelled,
            "global_cancel_reason": self.global_cancel.reason,
            "cancellation_events": cancellation_events,
            "quarantined_workers": quarantined_workers,
            "blocked_cancellation": blocked_cancellation,
            "published_artifacts": self.publisher.published,
            "late_writes_rejected": self.publisher.rejected,
            "all_workers_reaped": len(quarantined_workers) == 0,
        }

        # Measure actual independent task overlap & critical path
        spans = [
            (r["task_id"], float(r.get("rel_start_sec", 0.0)), float(r.get("rel_end_sec", 0.0)))
            for r in ordered_receipts if r.get("status") == "PASS" and "rel_start_sec" in r
        ]
        overlap_pairs: list[list[str]] = []
        for i in range(len(spans)):
            for j in range(i + 1, len(spans)):
                if spans[i][1] < spans[j][2] and spans[j][1] < spans[i][2]:
                    overlap_pairs.append([spans[i][0], spans[j][0]])
        sum_task_sec = round(sum(float(r.get("duration_sec", 0.0)) for r in ordered_receipts), 4)
        # Longest dependency chain duration (critical path)
        cp_memo: dict[str, float] = {}
        def _cp(tid_k: str) -> float:
            if tid_k in cp_memo:
                return cp_memo[tid_k]
            d_self = float(receipts[tid_k].get("duration_sec", 0.0))
            d_deps = max((_cp(d) for d in by_id[tid_k].deps), default=0.0)
            cp_memo[tid_k] = d_self + d_deps
            return cp_memo[tid_k]
        critical_path_sec = round(max((_cp(tid) for tid in ordered_ids), default=0.0), 4)

        async_metrics = {
            "schema_version": "kh03.async_metrics.v1",
            "run_id": self.run_id,
            "workers": self.workers,
            "network_workers": self.network_workers,
            "heavy_concurrency": self.heavy_concurrency,
            "decoder_pool": self.decoder_pool,
            "task_count": len(ordered_receipts),
            "passed_tasks": sum(1 for r in ordered_receipts if r["status"] == "PASS"),
            "ctx_aware_task_count": sum(1 for r in ordered_receipts if r.get("ctx_aware")),
            "wall_sec": wall_sec,
            "sum_task_sec": sum_task_sec,
            "critical_path_sec": critical_path_sec,
            "effective_parallel_ratio": round(sum_task_sec / max(0.0001, wall_sec), 3),
            "overlap_detected": len(overlap_pairs) > 0,
            "overlap_pair_count": len(overlap_pairs),
            "peak_rss_mib": self.peak_rss_mib,
            "min_free_disk_gib": self.min_free_disk_gib,
            "canonical_dag_sha256": dag_doc["canonical_dag_sha256"],
            "blocked_cancellation": blocked_cancellation,
        }
        dag_trace = {
            "schema_version": "kh03.dag_trace.v1",
            "run_id": self.run_id,
            "workers": self.workers,
            "wall_sec": wall_sec,
            "critical_path_sec": critical_path_sec,
            "overlap_detected": len(overlap_pairs) > 0,
            "overlap_pairs": overlap_pairs[:32],
            "tasks": ordered_receipts,
            "events": trace_events,
        }

        if self.run_dir is not None:
            mdir = self.run_dir / "manifests"
            mdir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(mdir / "TASK_DAG.json", dag_doc)
            write_json_atomic(mdir / "CANCELLATION_PROOF.json", cancel_proof)
            write_json_atomic(mdir / "ASYNC_METRICS.json", async_metrics)
            write_json_atomic(mdir / "DAG_TRACE.json", dag_trace)
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
            # Clean empty staging dir ONLY when zero quarantined workers remain!
            if staging_base is not None and len(quarantined_workers) == 0:
                try:
                    for ch in list(staging_base.iterdir()):
                        if ch.is_dir():
                            shutil.rmtree(ch, ignore_errors=True)
                except Exception:
                    pass

        return {
            "pass": all_ok,
            "workers": self.workers,
            "wall_sec": wall_sec,
            "peak_rss_mib": self.peak_rss_mib,
            "blocked_cancellation": blocked_cancellation,
            "quarantined_workers": quarantined_workers,
            "late_writes_rejected": self.publisher.rejected,
            "cancellation_proof": cancel_proof,
            "async_metrics": async_metrics,
            "dag": dag_doc,
            "receipts": ordered_receipts,
            "results": ordered_results,
            "canonical_dag_sha256": dag_doc["canonical_dag_sha256"],
        }

    def run(self, tasks: list[DAGTask]) -> dict[str, Any]:
        """Run DAG synchronously via a dedicated event loop (safe inside sync CLI or existing event loops)."""
        try:
            asyncio.get_running_loop()
            with ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: asyncio.run(self._run_async(tasks))).result()
        except RuntimeError:
            return asyncio.run(self._run_async(tasks))
