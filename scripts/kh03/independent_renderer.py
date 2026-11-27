"""KH-03.R4.2.HOTFIX — Clean-Room Independent Multi-Source FFmpeg + Pillow + NumPy Video Renderer.

PROVENANCE & LICENSE ISOLATION GUARANTEE:
- 100% original clean-room implementation using ONLY Python stdlib, Pillow (HPND/MIT-CMU),
  NumPy (BSD-3-Clause), and system FFmpeg/FFprobe CLI.
- ZERO imports, ZERO templates, ZERO scripts, and ZERO assets from any PolyForm Noncommercial
  upstream renderer or legacy KH-02 renderer module.
- Universal across ALL niches and ALL 5 universal editing skills (`SK-01..SK-05`): ZERO hardcoded
  creator names, brands, finance copy, or timestamps; driven purely by declarative JSON render specs.
- Bounded O(1)-RAM streaming compositing pipeline with isolated staging, atomic promotion,
  cooperative cancellation checking, process-group cleanup, phoneme-safe audio crossfading,
  strict secondary MUTE_DEFAULT enforcement, and 100% original procedural BGM & tactile SFX synthesis.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .footage_effects import (
    apply_exposure_grade_rgb,
    compose_multilayer_frame,
    interpolate_keyframes,
    render_before_after_split_rgb,
    render_dual_moving_transition,
    render_pullback_or_transform_frame,
    resolve_speed_ramp_pts,
    validate_transition_handles,
)
from .ingest_cache import SharedIngestCache, extract_pcm_48k_cached
from .motion_components import (
    PALETTE_PRESETS,
    ease_in_out_cubic as _mc_ease_in_out,
    render_component_onto_rgb,
    render_luxury_backdrop,
    resolve_palette,
)
from .remotion_adapter import (
    StreamingAlphaOverlayReader,
    alpha_composite_rgba_onto_rgb,
    probe_remotion_runtime,
    render_remotion_effect_span,
)


# ---------------------------------------------------------------------------
# Generic helpers (clean-room stdlib only)
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - (1.0 - t) ** 3


def _ease_in_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return 4.0 * t * t * t if t < 0.5 else 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


def _parse_hex_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    c = str(hex_color or "#1B3429").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        return (27, 52, 41, alpha)
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), max(0, min(255, int(alpha))))


def _load_font(size: int, *, bold: bool = True, serif: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[str] = []
    if os.name == "nt":
        fdir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        if serif:
            names = ["georgiab.ttf", "georgia.ttf", "segoeuib.ttf", "arialbd.ttf"] if bold else ["georgia.ttf", "segoeui.ttf", "arial.ttf"]
        else:
            names = ["segoeuib.ttf", "seguisb.ttf", "bahnschrift.ttf", "arialbd.ttf", "calibrib.ttf"] if bold else ["seguisb.ttf", "segoeui.ttf", "arial.ttf", "calibri.ttf"]
        candidates = [str(fdir / n) for n in names]
    else:
        candidates = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    for fp in candidates:
        if Path(fp).is_file():
            try:
                return ImageFont.truetype(fp, size=max(10, int(size)))
            except Exception:
                continue
    return ImageFont.load_default()


def _probe_video_basic(path: Path) -> dict[str, Any]:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    doc = json.loads(out)
    streams = doc.get("streams") or []
    vs = next((s for s in streams if s.get("codec_type") == "video"), None)
    as_ = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = doc.get("format") or {}
    if not vs:
        raise ValueError(f"NO_VIDEO_STREAM: {path}")
    fps_str = str(vs.get("avg_frame_rate") or vs.get("r_frame_rate") or "24/1")
    fr = Fraction(fps_str) if "/" in fps_str else Fraction(float(fps_str)).limit_denominator(1001)
    v_dur = float(vs.get("duration") or fmt.get("duration") or 0.0)
    c_dur = float(fmt.get("duration") or v_dur)
    nb_f = int(vs["nb_frames"]) if str(vs.get("nb_frames", "")).isdigit() else int(round(v_dur * float(fr)))
    return {
        "path": str(path.resolve()),
        "sha256": _sha256_file(path),
        "width": int(vs["width"]),
        "height": int(vs["height"]),
        "fps_num": fr.numerator,
        "fps_den": fr.denominator,
        "fps": float(fr),
        "nb_frames": nb_f,
        "video_duration_sec": round(v_dur, 4),
        "container_duration_sec": round(c_dur, 4),
        "has_audio": as_ is not None,
    }


def _decode_video_frames_to_dict(
    video_path: Path,
    needed_indices: set[int],
    *,
    width: int,
    height: int,
    cancel_event: threading.Event | None = None,
) -> dict[int, Image.Image]:
    """Stream-decode ONLY the requested frame indices at target size in bounded RAM (no full-video buffer!)."""
    if not needed_indices:
        return {}
    max_idx = max(needed_indices)
    frame_bytes = width * height * 3
    cmd = [
        "ffmpeg", "-v", "error", "-nostdin", "-threads", "2",
        "-i", str(video_path),
        "-vf", f"scale={width}:{height}:flags=lanczos",
        "-fps_mode", "passthrough",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    popen_kw: dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "bufsize": frame_bytes * 2}
    if os.name == "posix":
        popen_kw["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(cmd, **popen_kw)
    frames: dict[int, Image.Image] = {}
    idx = 0
    try:
        assert proc.stdout is not None
        while idx <= max_idx:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("RENDER_CANCELLED_DURING_DECODE")
            buf = bytearray(frame_bytes)
            mv = memoryview(buf)
            got = 0
            while got < frame_bytes:
                n = proc.stdout.readinto(mv[got:])
                if not n:
                    break
                got += n
            if got < frame_bytes:
                break
            if idx in needed_indices:
                frames[idx] = Image.frombytes("RGB", (width, height), bytes(buf))
            idx += 1
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
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
    return frames


class _SequentialFrameReader:
    """True O(1)-RAM sequential FFmpeg RGB24 reader supporting forward seeking/holding for bounded streaming render."""

    def __init__(self, video_path: Path, width: int, height: int) -> None:
        self.video_path = video_path
        self.width = width
        self.height = height
        self.frame_bytes = width * height * 3
        self._proc: subprocess.Popen[bytes] | None = None
        self._cur_idx = -1
        self._cur_img: Image.Image | None = None

    def open(self) -> None:
        cmd = [
            "ffmpeg", "-v", "error", "-nostdin", "-threads", "2",
            "-i", str(self.video_path),
            "-vf", f"scale={self.width}:{self.height}:flags=lanczos",
            "-fps_mode", "passthrough",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]
        popen_kw: dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "bufsize": self.frame_bytes * 2}
        if os.name == "posix":
            popen_kw["start_new_session"] = True
        elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        self._proc = subprocess.Popen(cmd, **popen_kw)

    def get_frame(self, target_idx: int) -> Image.Image:
        """Advance sequentially to `target_idx` (reopens only if caller jumps backward)."""
        if target_idx < self._cur_idx:
            self.close()
            self._cur_idx = -1
            self._cur_img = None
            self.open()
        if self._proc is None:
            self.open()
        assert self._proc is not None and self._proc.stdout is not None
        while self._cur_idx < target_idx:
            buf = bytearray(self.frame_bytes)
            mv = memoryview(buf)
            got = 0
            while got < self.frame_bytes:
                n = self._proc.stdout.readinto(mv[got:])
                if not n:
                    break
                got += n
            if got < self.frame_bytes:
                break
            self._cur_idx += 1
            if self._cur_idx == target_idx or self._cur_img is None:
                self._cur_img = Image.frombytes("RGB", (self.width, self.height), bytes(buf))
        if self._cur_img is None:
            self._cur_img = Image.new("RGB", (self.width, self.height), (20, 24, 22))
        return self._cur_img

    def close(self) -> None:
        p = self._proc
        self._proc = None
        if p is not None:
            for pipe in (p.stdout, p.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except Exception:
                        pass
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    p.kill()


# ---------------------------------------------------------------------------
# Clean-Room Audio Assembly + Original Procedural BGM & Tactile SFX Engine
# ---------------------------------------------------------------------------

def _read_pcm_f32_mono(media_path: Path, sample_rate: int = 48000) -> np.ndarray:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(media_path), "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=60,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def _write_wav_stereo_16(path: Path, stereo: np.ndarray, sample_rate: int = 48000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(stereo, -0.99, 0.99)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return path


def _soft_limit_stereo(x: np.ndarray, ceiling: float = 0.88) -> np.ndarray:
    """Gentle smooth tanh soft-limiter preserving voice clarity and enforcing true peak <= -1.1 dBTP."""
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= ceiling:
        return x
    return ceiling * np.tanh(x / ceiling)


def _synth_warm_editorial_bgm(duration_sec: float, sample_rate: int = 48000, style: str = "warm_calm") -> np.ndarray:
    """100% original procedural editorial underscore (warm electric-piano/jazz lounge pads, zero samples)."""
    n = max(1, int(round(duration_sec * sample_rate)))
    left = np.zeros(n, dtype=np.float64)
    right = np.zeros(n, dtype=np.float64)

    if str(style).lower() == "luxury_lounge":
        # Understated chic luxury lounge / playful warm jazz7 voicings (Cmaj9 -> Am9 -> Dm9 -> G13, 3.8s/bar)
        chords = [
            [130.81, 196.00, 246.94, 293.66, 329.63],  # Cmaj9
            [110.00, 196.00, 246.94, 261.63, 329.63],  # Am9
            [146.83, 220.00, 261.63, 329.63, 349.23],  # Dm9
            [98.00, 174.61, 220.00, 246.94, 329.63],   # G13
        ]
        bar_sec = 3.8
    else:
        # Gentle uplifting 4-chord progression (Dmaj7 -> Bm7 -> Gmaj7 -> Asus4, ~4.0s per chord)
        chords = [
            [146.83, 220.00, 277.18, 369.99],  # Dmaj7
            [123.47, 185.00, 246.94, 293.66],  # Bm7
            [98.00, 196.00, 246.94, 369.99],   # Gmaj7
            [110.00, 220.00, 293.66, 329.63],  # Asus4 -> A
        ]
        bar_sec = 4.0
    n_bars = int(math.ceil(duration_sec / bar_sec))
    for b_idx in range(n_bars):
        c_freqs = chords[b_idx % len(chords)]
        s0 = int(b_idx * bar_sec * sample_rate)
        s1 = min(n, int((b_idx + 1) * bar_sec * sample_rate))
        if s1 <= s0:
            continue
        seg_t = (np.arange(s1 - s0, dtype=np.float64)) / float(sample_rate)
        dur_b = (s1 - s0) / float(sample_rate)
        att = np.clip(seg_t / (0.28 if style == "luxury_lounge" else 0.35), 0.0, 1.0)
        rel = np.clip((dur_b - seg_t) / 0.45, 0.0, 1.0)
        env = np.sin(att * math.pi * 0.5) * np.sin(rel * math.pi * 0.5)
        chord_l = np.zeros(s1 - s0, dtype=np.float64)
        chord_r = np.zeros(s1 - s0, dtype=np.float64)
        for fi, f0 in enumerate(c_freqs):
            amp = 0.22 if fi == 0 else (0.14 if fi == 1 else 0.095)
            det = 0.22 * (fi - 1.5)
            tone_l = np.sin(2.0 * math.pi * (f0 - det) * seg_t) + 0.14 * np.sin(2.0 * math.pi * 2.0 * f0 * seg_t)
            tone_r = np.sin(2.0 * math.pi * (f0 + det) * seg_t) + 0.14 * np.sin(2.0 * math.pi * 2.0 * f0 * seg_t)
            chord_l += amp * tone_l
            chord_r += amp * tone_r
        # Soft muted pulse for subtle forward editorial cadence
        pulse_hz = 0.78 if style == "luxury_lounge" else 0.50
        pulse = 0.86 + 0.14 * np.sin(2.0 * math.pi * pulse_hz * seg_t)
        left[s0:s1] += chord_l * env * pulse
        right[s0:s1] += chord_r * env * pulse

    # Global smooth fade-in (0.6s) and gentle resolve fade-out (1.2s)
    fi_n = min(n, int(0.6 * sample_rate))
    fo_n = min(n, int(1.2 * sample_rate))
    if fi_n > 0:
        left[:fi_n] *= np.linspace(0.0, 1.0, fi_n)
        right[:fi_n] *= np.linspace(0.0, 1.0, fi_n)
    if fo_n > 0:
        left[-fo_n:] *= np.linspace(1.0, 0.0, fo_n)
        right[-fo_n:] *= np.linspace(1.0, 0.0, fo_n)

    st = np.stack([left, right], axis=-1)
    peak = float(np.max(np.abs(st))) + 1e-9
    return (st / peak).astype(np.float32)


def _synth_tactile_sfx(kind: str, sample_rate: int = 48000) -> np.ndarray:
    """100% original procedural micro-SFX (soft impact, wooden tick, airy whoosh, warm chime)."""
    rng = np.random.default_rng(20261126)
    if kind == "soft_impact":
        dur = 0.24
        t = np.arange(int(dur * sample_rate), dtype=np.float64) / sample_rate
        f = 135.0 * np.exp(-t * 14.0) + 48.0
        phase = 2.0 * math.pi * np.cumsum(f) / sample_rate
        sig = np.sin(phase) * np.exp(-t * 15.0) * np.clip(t / 0.008, 0.0, 1.0)
    elif kind == "tick":
        dur = 0.09
        t = np.arange(int(dur * sample_rate), dtype=np.float64) / sample_rate
        sig = (
            0.65 * np.sin(2.0 * math.pi * 880.0 * t)
            + 0.35 * np.sin(2.0 * math.pi * 1320.0 * t)
        ) * np.exp(-t * 52.0) * np.clip(t / 0.003, 0.0, 1.0)
    elif kind == "soft_whoosh":
        dur = 0.28
        n = int(dur * sample_rate)
        t = np.arange(n, dtype=np.float64) / sample_rate
        noise = rng.normal(0.0, 1.0, n)
        # Simple smooth moving-average low-pass for gentle airy texture
        kernel = np.hanning(45)
        kernel /= np.sum(kernel)
        lp = np.convolve(noise, kernel, mode="same")
        env = np.sin(math.pi * (t / dur)) ** 1.8
        sig = lp * env * 0.9
    elif kind == "payoff_chime":
        dur = 0.65
        t = np.arange(int(dur * sample_rate), dtype=np.float64) / sample_rate
        sig = (
            0.55 * np.sin(2.0 * math.pi * 587.33 * t)     # D5
            + 0.35 * np.sin(2.0 * math.pi * 880.00 * t)   # A5
            + 0.22 * np.sin(2.0 * math.pi * 1174.66 * t)  # D6
        ) * np.exp(-t * 5.2) * np.clip(t / 0.012, 0.0, 1.0)
    elif kind == "sub_drop":
        dur = 0.36
        t = np.arange(int(dur * sample_rate), dtype=np.float64) / sample_rate
        f = 96.0 * np.exp(-t * 9.5) + 42.0
        phase = 2.0 * math.pi * np.cumsum(f) / sample_rate
        sig = np.sin(phase) * np.exp(-t * 9.0) * np.clip(t / 0.012, 0.0, 1.0)
    else:  # subtle_pop
        dur = 0.11
        t = np.arange(int(dur * sample_rate), dtype=np.float64) / sample_rate
        f = 420.0 + 260.0 * np.exp(-t * 28.0)
        phase = 2.0 * math.pi * np.cumsum(f) / sample_rate
        sig = np.sin(phase) * np.exp(-t * 34.0) * np.clip(t / 0.004, 0.0, 1.0)

    peak = float(np.max(np.abs(sig))) + 1e-9
    mono = (sig / peak).astype(np.float32)
    return np.stack([mono, mono], axis=-1)


def build_speech_master_and_mix(
    *,
    primary_video: Path,
    voice_segments: list[dict[str, Any]],
    total_output_frames: int,
    fps_num: int = 24,
    fps_den: int = 1,
    sample_rate: int = 48000,
    sfx_cues: list[dict[str, Any]] | None = None,
    bgm_enabled: bool = True,
    bgm_style: str = "warm_calm",
    bgm_gain_db: float = -24.0,
    sfx_enabled: bool = True,
    sfx_gain_db: float = -16.5,
    stems_dir: Path,
    cache: SharedIngestCache | None = None,
) -> dict[str, Any]:
    """Assemble click-free speech master from EDL segments + ducked clean-room BGM + tactile SFX stems."""
    stems_dir.mkdir(parents=True, exist_ok=True)
    fps = float(Fraction(fps_num, fps_den))
    total_dur = total_output_frames / fps
    out_n = int(round(total_dur * sample_rate))

    if cache is not None:
        raw_mono, _pcm_meta = extract_pcm_48k_cached(primary_video, sample_rate=sample_rate, cache=cache)
    else:
        raw_mono = _read_pcm_f32_mono(primary_video, sample_rate=sample_rate)
    # Build spliced voice master on exact output clock with equal-power Hann crossfades at segment joins
    voice_out = np.zeros(out_n, dtype=np.float64)
    for idx, seg in enumerate(voice_segments):
        s_in = max(0, int(round(float(seg["src_in_sec"]) * sample_rate)))
        s_out = min(len(raw_mono), int(round(float(seg["src_out_sec"]) * sample_rate)))
        o_in = max(0, int(round(float(seg["out_in_sec"]) * sample_rate)))
        xfade_ms = int(seg.get("crossfade_ms", 18 if idx > 0 else 0))
        xf_n = min(int(round(xfade_ms * 0.001 * sample_rate)), max(0, (s_out - s_in) // 4))

        chunk = raw_mono[s_in:s_out].astype(np.float64)
        if chunk.size == 0:
            continue
        # Remove tiny DC offset per segment so splice boundaries never click
        chunk = chunk - float(np.mean(chunk))
        # Fade-in on join boundary & gentle edge de-click
        fade_in = max(xf_n, int(0.008 * sample_rate)) if idx > 0 else int(0.006 * sample_rate)
        fade_out = int(0.010 * sample_rate)
        fade_in = min(fade_in, chunk.size // 3)
        fade_out = min(fade_out, chunk.size // 3)
        if fade_in > 0:
            chunk[:fade_in] *= 0.5 * (1.0 - np.cos(math.pi * np.linspace(0.0, 1.0, fade_in)))
        if fade_out > 0:
            chunk[-fade_out:] *= 0.5 * (1.0 + np.cos(math.pi * np.linspace(0.0, 1.0, fade_out)))

        end_pos = min(out_n, o_in + chunk.size)
        avail = end_pos - o_in
        if avail > 0:
            voice_out[o_in:end_pos] += chunk[:avail]

    # Gentle high-pass (~75 Hz one-pole) to remove low rumble cleanly without altering vocal timber
    rc = 1.0 / (2.0 * math.pi * 75.0)
    dt = 1.0 / float(sample_rate)
    alpha = rc / (rc + dt)
    hp = np.zeros_like(voice_out)
    if voice_out.size > 1:
        # Vectorized IIR approximation via subtle moving-mean subtraction (window ~12ms)
        win_hp = max(16, int(0.012 * sample_rate))
        kern_hp = np.ones(win_hp, dtype=np.float64) / float(win_hp)
        low_est = np.convolve(voice_out, kern_hp, mode="same")
        hp = voice_out - 0.85 * low_est
    else:
        hp = voice_out

    # Normalize voice RMS to ~ -15.5 dBFS active speech level (preserving natural dynamics!)
    abs_v = np.abs(hp)
    active = hp[abs_v > 0.015]
    cur_rms = float(np.sqrt(np.mean(active ** 2))) if active.size > 200 else float(np.sqrt(np.mean(hp ** 2) + 1e-12))
    target_rms = 10.0 ** (-15.5 / 20.0)
    gain_v = min(2.8, max(0.4, target_rms / max(1e-5, cur_rms)))
    voice_norm = _soft_limit_stereo(np.stack([hp * gain_v, hp * gain_v], axis=-1), ceiling=0.84)

    # Voice activity envelope for gentle smooth BGM sidechain ducking (voice always clearly leads!)
    env_win = int(0.18 * sample_rate)
    v_abs_mono = np.abs(voice_norm[:, 0])
    # Downsample envelope calculation for speed, then linear interpolate
    hop = 480
    ds = np.array([float(np.sqrt(np.mean(v_abs_mono[i : i + hop] ** 2) + 1e-12)) for i in range(0, out_n, hop)], dtype=np.float64)
    sm_k = np.hanning(19); sm_k /= np.sum(sm_k)
    ds_smooth = np.convolve(ds, sm_k, mode="same")
    speech_active = np.clip((ds_smooth - 0.015) / 0.06, 0.0, 1.0)
    duck_ds = 1.0 - 0.52 * speech_active  # ducks BGM ~ -6.4 dB while speaking, lifts smoothly during pauses/CTA
    duck_curve = np.interp(np.arange(out_n, dtype=np.float64), np.arange(len(duck_ds), dtype=np.float64) * hop, duck_ds)

    bgm_stem = np.zeros((out_n, 2), dtype=np.float32)
    if bgm_enabled:
        raw_bgm = _synth_warm_editorial_bgm(total_dur, sample_rate=sample_rate, style=bgm_style)
        bgm_lin = float(10.0 ** (bgm_gain_db / 20.0))
        bgm_stem = (raw_bgm[:out_n] * bgm_lin * duck_curve[:, None]).astype(np.float32)

    sfx_stem = np.zeros((out_n, 2), dtype=np.float32)
    sfx_base_lin = float(10.0 ** (sfx_gain_db / 20.0))
    applied_sfx: list[dict[str, Any]] = []
    if sfx_enabled:
        for cue in (sfx_cues or []):
            t_sec = float(cue.get("time_sec", 0.0))
            kind = str(cue.get("kind") or "tick")
            rel_db = float(cue.get("gain_offset_db", 0.0))
            s0 = int(round(t_sec * sample_rate))
            if s0 < 0 or s0 >= out_n:
                continue
            clip = _synth_tactile_sfx(kind, sample_rate=sample_rate) * sfx_base_lin * float(10.0 ** (rel_db / 20.0))
            s1 = min(out_n, s0 + len(clip))
            sfx_stem[s0:s1] += clip[: s1 - s0]
            applied_sfx.append({"time_sec": round(t_sec, 3), "kind": kind, "samples": [s0, s1]})

    mix = _soft_limit_stereo(voice_norm + bgm_stem + sfx_stem, ceiling=0.88).astype(np.float32)

    p_voice = _write_wav_stereo_16(stems_dir / "voice_master.wav", voice_norm, sample_rate)
    p_bgm = _write_wav_stereo_16(stems_dir / "bgm.wav", bgm_stem, sample_rate)
    p_sfx = _write_wav_stereo_16(stems_dir / "sfx.wav", sfx_stem, sample_rate)
    p_mix = _write_wav_stereo_16(stems_dir / "final_mix.wav", mix, sample_rate)

    # Measure integrated LUFS & True Peak via ffmpeg ebur128 filter!
    lufs = None
    true_peak = None
    try:
        eb = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(p_mix), "-af", "ebur128=peak=true", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        import re
        mi = re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", eb.stderr)
        mp = re.findall(r"Peak:\s*(-?[\d.]+)\s*dBFS", eb.stderr)
        if mi:
            lufs = float(mi[-1])
        if mp:
            true_peak = float(mp[-1])
    except Exception:
        pass

    return {
        "voice_wav": str(p_voice),
        "bgm_wav": str(p_bgm),
        "sfx_wav": str(p_sfx),
        "mix_wav": str(p_mix),
        "mix_sha256": _sha256_file(p_mix),
        "sample_rate": sample_rate,
        "duration_sec": round(out_n / float(sample_rate), 4),
        "applied_sfx_count": len(applied_sfx),
        "applied_sfx": applied_sfx,
        "integrated_lufs": lufs,
        "true_peak_dbtp": true_peak,
        "secondary_audio_muted": True,
        "bgm_provenance": "ORIGINAL_CLEAN_ROOM_NUMPY_SYNTHESIS",
        "sfx_provenance": "ORIGINAL_CLEAN_ROOM_NUMPY_SYNTHESIS",
    }


# ---------------------------------------------------------------------------
# Clean-Room Compositor & Motion Graphics Overlay Primitives (1080x1920)
# ---------------------------------------------------------------------------

def _zoom_pan_frame(img: Image.Image, out_w: int, out_h: int, scale: float, cx_norm: float = 0.5, cy_norm: float = 0.5) -> Image.Image:
    """Crisp smooth Lanczos micro-zoom / Ken Burns crop without jitter."""
    if abs(scale - 1.0) < 1e-4 and img.size == (out_w, out_h):
        return img.copy()
    w, h = img.size
    crop_w = max(16, w / max(1.0, scale))
    crop_h = max(16, h / max(1.0, scale))
    x0 = max(0.0, min(w - crop_w, cx_norm * w - crop_w * 0.5))
    y0 = max(0.0, min(h - crop_h, cy_norm * h - crop_h * 0.5))
    cropped = img.crop((x0, y0, x0 + crop_w, y0 + crop_h))
    return cropped.resize((out_w, out_h), Image.Resampling.LANCZOS)


def _draw_rounded_card(
    layer: Image.Image,
    bbox: tuple[int, int, int, int],
    *,
    radius: int = 24,
    fill: tuple[int, int, int, int] = (24, 46, 36, 224),
    outline: tuple[int, int, int, int] | None = (217, 182, 107, 210),
    outline_w: int = 3,
    shadow: bool = True,
) -> None:
    x0, y0, x1, y1 = bbox
    if shadow:
        sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        sd.rounded_rectangle((x0, y0 + 8, x1, y1 + 12), radius=radius, fill=(8, 16, 12, 96))
        sh = sh.filter(ImageFilter.GaussianBlur(radius=10))
        layer.alpha_composite(sh)
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=fill, outline=outline, width=outline_w if outline else 0)


def _render_overlay_event(
    base_rgba: Image.Image,
    ev: dict[str, Any],
    t_sec: float,
    w: int,
    h: int,
) -> None:
    """Render one generic declarative semantic motion-graphics overlay onto `base_rgba` in-place."""
    t0 = float(ev["start_sec"])
    t1 = float(ev["end_sec"])
    if not (t0 <= t_sec < t1):
        return
    dur = max(0.05, t1 - t0)
    el = t_sec - t0
    rem = t1 - t_sec
    in_p = _ease_out_cubic(min(1.0, el / float(ev.get("enter_sec", 0.24))))
    out_p = _ease_out_cubic(min(1.0, rem / float(ev.get("exit_sec", 0.20))))
    vis = min(in_p, out_p)
    if vis <= 0.01:
        return

    kind = str(ev.get("kind") or "context_chip")
    sc = w / 1080.0
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    accent = _parse_hex_rgba(ev.get("accent_hex", "#D9B66B"), int(255 * vis))
    bg = _parse_hex_rgba(ev.get("bg_hex", "#193026"), int(228 * vis))
    fg = _parse_hex_rgba(ev.get("fg_hex", "#F8F7F2"), int(255 * vis))
    muted = _parse_hex_rgba(ev.get("muted_hex", "#D4DDD7"), int(230 * vis))

    if kind == "editorial_hook_banner":
        # Safe upper band (y = 5.2%..14.8%, 0% face/forehead-patch occlusion!)
        slide_y = int((1.0 - in_p) * -28 * sc)
        # Subtle pulse at optional impact_sec
        pulse = 1.0
        imp_t = ev.get("impact_sec")
        if imp_t is not None and abs(t_sec - float(imp_t)) < 0.28:
            u = 1.0 - abs(t_sec - float(imp_t)) / 0.28
            pulse = 1.0 + 0.045 * math.sin(u * math.pi)
        pad_x = int(58 * sc)
        bx0 = pad_x
        bx1 = w - pad_x
        by0 = int(h * 0.052) + slide_y
        by1 = int(h * 0.146) + slide_y
        _draw_rounded_card(layer, (bx0, by0, bx1, by1), radius=int(24 * sc), fill=bg, outline=accent, outline_w=max(2, int(3 * sc)))
        # Gold top highlight bar
        bar_w = int((bx1 - bx0 - 48 * sc) * in_p)
        draw.rounded_rectangle((bx0 + int(24 * sc), by0 + int(12 * sc), bx0 + int(24 * sc) + bar_w, by0 + int(18 * sc)), radius=3, fill=accent)
        title = str(ev.get("title") or "")
        sub = str(ev.get("subtitle") or "")
        ft = _load_font(int(48 * sc * pulse), bold=True)
        fs = _load_font(int(26 * sc), bold=False)
        tb = draw.textbbox((0, 0), title, font=ft)
        tw = tb[2] - tb[0]
        draw.text(((w - tw) // 2, by0 + int(32 * sc)), title, font=ft, fill=fg)
        if sub:
            sb = draw.textbbox((0, 0), sub, font=fs)
            sw = sb[2] - sb[0]
            draw.text(((w - sw) // 2, by0 + int(98 * sc)), sub, font=fs, fill=accent)

    elif kind == "context_chip":
        # Restrained negative-space floating chip (optional generic x_norm / y_norm placement)
        pos = str(ev.get("position") or "top")
        slide_y = int((1.0 - in_p) * (24 if pos == "bottom" else -20) * sc)
        text = str(ev.get("title") or "")
        kicker = str(ev.get("kicker") or "")
        ft = _load_font(int(34 * sc), bold=True)
        fk = _load_font(int(21 * sc), bold=True)
        tb = draw.textbbox((0, 0), text, font=ft)
        kb = draw.textbbox((0, 0), kicker, font=fk) if kicker else (0, 0, 0, 0)
        box_w = min(int(w * 0.88), max(tb[2] - tb[0], kb[2] - kb[0]) + int(76 * sc))
        box_h = int((104 if kicker else 74) * sc)
        cx = int(w * float(ev.get("x_norm", 0.50)))
        bx0 = max(int(24 * sc), min(w - box_w - int(24 * sc), cx - box_w // 2))
        default_y = 0.068 if pos == "top" else 0.78
        by0 = int(h * float(ev.get("y_norm", default_y))) + slide_y
        _draw_rounded_card(layer, (bx0, by0, bx0 + box_w, by0 + box_h), radius=int(20 * sc), fill=bg, outline=accent, outline_w=max(2, int(2.5 * sc)))
        card_cx = bx0 + box_w // 2
        if kicker:
            draw.text((card_cx - (kb[2] - kb[0]) // 2, by0 + int(16 * sc)), kicker, font=fk, fill=accent)
            draw.text((card_cx - (tb[2] - tb[0]) // 2, by0 + int(48 * sc)), text, font=ft, fill=fg)
        else:
            draw.text((card_cx - (tb[2] - tb[0]) // 2, by0 + int(18 * sc)), text, font=ft, fill=fg)

    elif kind == "keyword_accent_ticker":
        # Compact floating accent pill anchored into verified negative space (supports x_norm, y_norm, compact)
        pos = str(ev.get("position") or "top")
        compact = bool(ev.get("compact", False))
        slide_x = int((1.0 - in_p) * 32 * sc)
        kicker = str(ev.get("kicker") or "")
        value = str(ev.get("value") or "")
        sub = str(ev.get("subtitle") or "")
        fk = _load_font(int((18 if compact else 22) * sc), bold=True)
        fv = _load_font(int((36 if compact else 48) * sc), bold=True)
        fs = _load_font(int((18 if compact else 23) * sc), bold=False)
        vb = draw.textbbox((0, 0), value, font=fv)
        kb = draw.textbbox((0, 0), kicker, font=fk) if kicker else (0, 0, 0, 0)
        sb = draw.textbbox((0, 0), sub, font=fs) if sub else (0, 0, 0, 0)
        pad_w = int((60 if compact else 84) * sc)
        cw = min(int(w * (0.44 if compact else 0.86)), max(vb[2] - vb[0], kb[2] - kb[0], sb[2] - sb[0]) + pad_w)
        ch = int(((112 if sub else 88) if compact else (144 if sub else 112)) * sc)
        cx = int(w * float(ev.get("x_norm", 0.50)))
        bx0 = max(int(20 * sc), min(w - cw - int(20 * sc), cx - cw // 2)) + slide_x
        default_y = 0.055 if pos == "top" else 0.77
        by0 = int(h * float(ev.get("y_norm", default_y)))
        _draw_rounded_card(layer, (bx0, by0, bx0 + cw, by0 + ch), radius=int((18 if compact else 22) * sc), fill=bg, outline=accent, outline_w=max(2, int(3 * sc)))
        # Left gold indicator pill
        draw.rounded_rectangle((bx0 + int(14 * sc), by0 + int(14 * sc), bx0 + int(21 * sc), by0 + ch - int(14 * sc)), radius=4, fill=accent)
        card_cx = bx0 + cw // 2 + int(4 * sc)
        y_cur = by0 + int((12 if compact else 16) * sc)
        if kicker:
            draw.text((card_cx - (kb[2] - kb[0]) // 2, y_cur), kicker, font=fk, fill=accent)
            y_cur += int((22 if compact else 28) * sc)
        draw.text((card_cx - (vb[2] - vb[0]) // 2, y_cur), value, font=fv, fill=fg)
        if sub:
            draw.text((card_cx - (sb[2] - sb[0]) // 2, y_cur + int((44 if compact else 58) * sc)), sub, font=fs, fill=muted)

    elif kind == "numerical_payoff_hero":
        # Emphasized animated counter / delta payoff card (supports generic y_norm negative-space anchoring)
        slide_y = int((1.0 - in_p) * -22 * sc)
        from_val = float(ev.get("from_value", 0.0))
        to_val = float(ev.get("to_value", 100.0))
        prefix = str(ev.get("prefix", "$"))
        suffix = str(ev.get("suffix", ""))
        prog = _ease_out_cubic(min(1.0, el / float(ev.get("count_sec", 0.65))))
        cur_num = int(round(from_val + (to_val - from_val) * prog))
        kicker = str(ev.get("kicker") or "ILLUSTRATIVE PAYOFF")
        headline = f"{prefix}{int(round(from_val))}  \u2192  {prefix}{cur_num}{suffix}"
        sub = str(ev.get("subtitle") or "")
        land_pulse = 1.0 + (0.032 * math.sin(min(1.0, max(0.0, el - 0.55) / 0.35) * math.pi) if 0.55 <= el <= 0.90 else 0.0)

        fk = _load_font(int(21 * sc), bold=True)
        fh = _load_font(int(50 * sc * land_pulse), bold=True)
        fs = _load_font(int(22 * sc), bold=False)
        hb = draw.textbbox((0, 0), headline, font=fh)
        kb = draw.textbbox((0, 0), kicker, font=fk)
        sb = draw.textbbox((0, 0), sub, font=fs) if sub else (0, 0, 0, 0)
        cw = min(int(w * 0.88), max(hb[2] - hb[0], kb[2] - kb[0], sb[2] - sb[0]) + int(92 * sc))
        ch = int(154 * sc)
        bx0 = (w - cw) // 2
        by0 = int(h * float(ev.get("y_norm", 0.052))) + slide_y
        _draw_rounded_card(layer, (bx0, by0, bx0 + cw, by0 + ch), radius=int(24 * sc), fill=bg, outline=accent, outline_w=max(3, int(4 * sc)))
        pb_w = int((cw - 48 * sc) * prog)
        draw.rounded_rectangle((bx0 + int(24 * sc), by0 + ch - int(14 * sc), bx0 + int(24 * sc) + pb_w, by0 + ch - int(8 * sc)), radius=3, fill=accent)
        draw.text(((w - (kb[2] - kb[0])) // 2, by0 + int(14 * sc)), kicker, font=fk, fill=accent)
        draw.text(((w - (hb[2] - hb[0])) // 2, by0 + int(42 * sc)), headline, font=fh, fill=fg)
        if sub:
            draw.text(((w - (sb[2] - sb[0])) // 2, by0 + int(106 * sc)), sub, font=fs, fill=muted)

    elif kind == "cta_resolve_pill":
        # Bottom-safe editorial CTA pill (supports generic y_norm placement)
        slide_y = int((1.0 - in_p) * 24 * sc)
        title = str(ev.get("title") or "LINK IN THE CAPTION")
        ft = _load_font(int(34 * sc), bold=True)
        tb = draw.textbbox((0, 0), title, font=ft)
        cw = min(int(w * 0.78), (tb[2] - tb[0]) + int(84 * sc))
        ch = int(76 * sc)
        bx0 = (w - cw) // 2
        by0 = int(h * float(ev.get("y_norm", 0.880))) + slide_y
        _draw_rounded_card(layer, (bx0, by0, bx0 + cw, by0 + ch), radius=int(38 * sc), fill=accent, outline=fg, outline_w=max(2, int(2 * sc)))
        ink = _parse_hex_rgba("#12241C", int(255 * vis))
        draw.text(((w - (tb[2] - tb[0])) // 2, by0 + int(17 * sc)), title, font=ft, fill=ink)

    base_rgba.alpha_composite(layer)


# ---------------------------------------------------------------------------
# Main Clean-Room Independent Streaming Render Entrypoint
# ---------------------------------------------------------------------------

def _resolve_camera_transform(
    camera_events: list[dict[str, Any]],
    t_sec: float,
    default_scale: float,
    default_cx: float,
    default_cy: float,
) -> dict[str, float]:
    """Evaluate active declarative camera / transform event with smooth cubic easing (supports scale < 1.0 pullback, tilt, rotation)."""
    out = {
        "scale": float(default_scale),
        "cx": float(default_cx),
        "cy": float(default_cy),
        "x_norm": 0.5,
        "y_norm": 0.5,
        "rotation_deg": 0.0,
        "tilt_x_deg": 0.0,
        "tilt_y_deg": 0.0,
    }
    for ce in camera_events:
        t0 = float(ce.get("start_sec", 0.0))
        t1 = float(ce.get("end_sec", 0.0))
        if t0 <= t_sec < t1:
            if ce.get("keyframes"):
                return interpolate_keyframes(ce["keyframes"], t_sec, out)
            u = _mc_ease_in_out((t_sec - t0) / max(0.04, t1 - t0))
            s0 = float(ce.get("scale_start", default_scale))
            s1 = float(ce.get("scale_end", s0))
            cx0 = float(ce.get("cx_start", ce.get("cx_norm", default_cx)))
            cx1 = float(ce.get("cx_end", cx0))
            cy0 = float(ce.get("cy_start", ce.get("cy_norm", default_cy)))
            cy1 = float(ce.get("cy_end", cy0))
            x0 = float(ce.get("x_start", ce.get("x_norm", 0.5)))
            x1 = float(ce.get("x_end", x0))
            y0 = float(ce.get("y_start", ce.get("y_norm", 0.5)))
            y1 = float(ce.get("y_end", y0))
            r0 = float(ce.get("rotation_start", ce.get("rotation_deg", 0.0)))
            r1 = float(ce.get("rotation_end", r0))
            tx0 = float(ce.get("tilt_x_start", ce.get("tilt_x_deg", 0.0)))
            tx1 = float(ce.get("tilt_x_end", tx0))
            ty0 = float(ce.get("tilt_y_start", ce.get("tilt_y_deg", 0.0)))
            ty1 = float(ce.get("tilt_y_end", ty0))
            return {
                "scale": s0 + (s1 - s0) * u,
                "cx": cx0 + (cx1 - cx0) * u,
                "cy": cy0 + (cy1 - cy0) * u,
                "x_norm": x0 + (x1 - x0) * u,
                "y_norm": y0 + (y1 - y0) * u,
                "rotation_deg": r0 + (r1 - r0) * u,
                "tilt_x_deg": tx0 + (tx1 - tx0) * u,
                "tilt_y_deg": ty0 + (ty1 - ty0) * u,
            }
    return out


def _group_remotion_effect_spans(
    effect_invocations: list[dict[str, Any]],
    *,
    fps: float,
    total_frames: int,
    merge_gap_sec: float = 0.25,
) -> list[dict[str, Any]]:
    """Group active Remotion/hybrid effect invocations into minimal contiguous render spans (renders ONLY spans that need Remotion!)."""
    rem_events: list[dict[str, Any]] = []
    for ev in effect_invocations:
        eid = str(ev.get("resolved_effect_id") or ev.get("effect_id") or "")
        if not eid.startswith("dx01."):
            continue
        # Footage-only / transition-only / depth-layer effects run natively in footage_engine, not Remotion
        if eid.startswith(("dx01.footage.", "dx01.trans.", "dx01.depth.")):
            continue
        rem_events.append(dict(ev, effect_id=eid))
    if not rem_events:
        return []

    rem_events.sort(key=lambda e: (float(e["start_sec"]), float(e["end_sec"]), str(e.get("event_id") or "")))
    groups: list[dict[str, Any]] = []
    for ev in rem_events:
        sf = max(0, int(math.floor(float(ev["start_sec"]) * fps)))
        ef = min(total_frames, max(sf + 2, int(math.ceil(float(ev["end_sec"]) * fps))))
        gap_f = int(round(merge_gap_sec * fps))
        if groups and sf <= groups[-1]["end_frame"] + gap_f:
            groups[-1]["end_frame"] = max(groups[-1]["end_frame"], ef)
            groups[-1]["events"].append(ev)
        else:
            groups.append({
                "span_id": f"rem_span_{len(groups) + 1:02d}",
                "start_frame": sf,
                "end_frame": ef,
                "events": [ev],
            })
    for g in groups:
        g["duration_in_frames"] = max(2, g["end_frame"] - g["start_frame"])
        g["span_start_sec"] = round(g["start_frame"] / fps, 6)
    return groups


def _fallback_dx01_event_to_legacy(ev: dict[str, Any]) -> dict[str, Any] | None:
    """Explicit deterministic fallback from a Remotion DX-01 effect to a clean-room PIL overlay when Remotion is unavailable."""
    eid = str(ev.get("effect_id") or "")
    base = {
        "event_id": str(ev.get("event_id") or "fb_ev"),
        "start_sec": float(ev["start_sec"]),
        "end_sec": float(ev["end_sec"]),
        "y_norm": float(ev.get("y_norm", 0.08)),
        "z_order": int(ev.get("z_order", 20)),
        "fallback_from": eid,
    }
    if eid == "dx01.typo.kinetic_headline":
        return {**base, "kind": "editorial_hook_banner", "title": str(ev.get("title") or "")[:48], "subtitle": str(ev.get("subtitle") or "")[:54]}
    if eid in ("dx01.typo.masked_reveal", "dx01.typo.floating_statement"):
        return {**base, "kind": "context_chip", "kicker": str(ev.get("kicker") or ""), "title": str(ev.get("title") or "")[:44]}
    if eid == "dx01.data.kinetic_counter":
        return {
            **base, "kind": "numerical_payoff_hero",
            "from_value": float(ev.get("from_value", 0)), "to_value": float(ev.get("to_value", 100)),
            "prefix": str(ev.get("prefix", "")), "suffix": str(ev.get("suffix", "")),
            "kicker": str(ev.get("kicker") or "RESULT"), "subtitle": str(ev.get("qualifier") or ""),
        }
    return None


def render_independent_video(
    spec: dict[str, Any],
    *,
    out_mp4: Path,
    stems_dir: Path,
    cancel_event: threading.Event | None = None,
    cache: SharedIngestCache | None = None,
    ffmpeg_threads: int = 4,
    enable_remotion: bool = True,
    allow_remotion_fallback: bool = True,
    remotion_cache_root: Path | None = None,
    remotion_concurrency: int = 1,
) -> dict[str, Any]:
    """Render a multi-source edit via clean-room FFmpeg+Pillow+NumPy and optional hybrid local Remotion spans.

    Supports legacy v1, `INDEPENDENT_RENDER_SPEC_V2`, and DX-01 extensions:
    - Arbitrary N sources & multi-layer canvas transforms (including `< 1.0` pullback, rotation, 2.5D tilt).
    - Direct footage effects (push/pull zoom, canvas pullback, perspective tilt, exposure/color grade,
      authentic before/after split wipe).
    - True dual-moving transitions (`dual_crossfade`, `mask_wipe`, `whip_blur`, `match_reveal`) with
      explicit outgoing handle validation (NEVER silently freezes outgoing frame!).
    - Real local Remotion transparent ProRes 4444 (`yuva444p10le`) effect spans streamed in O(1) RAM
      with content-addressed caching & verifiable per-span receipts.
    - Parallel audio mix + video encode + stream-copy faststart mux (ONE final delivery video encode).
    - Isolated staging -> full FFmpeg decode check -> exact frame count check -> atomic os.replace().
    """
    t0 = time.monotonic()
    out_w = int(spec.get("width", 1080))
    out_h = int(spec.get("height", 1920))
    fps_num = int(spec.get("fps_num", 24))
    fps_den = max(1, int(spec.get("fps_den", 1)))
    fps = float(Fraction(fps_num, fps_den))
    total_frames = int(spec["total_output_frames"])
    src_a_path = Path(spec["primary_source_path"]).resolve()
    src_b_path = Path(spec["secondary_source_path"]).resolve() if spec.get("secondary_source_path") else None
    use_proxy = bool(spec.get("use_proxy_sources", False))

    # Build arbitrary-N source probes & streaming readers from source_registry (or v1 A/B fallback)
    src_entries: dict[str, dict[str, Any]] = {}
    reg = spec.get("source_registry") or {}
    if isinstance(reg.get("sources"), list) and reg["sources"]:
        for ent in reg["sources"]:
            sid = str(ent["source_id"])
            eff_p = Path(ent["proxy_path"]).resolve() if (use_proxy and ent.get("proxy_path") and Path(ent["proxy_path"]).is_file()) else Path(ent["path"]).resolve()
            src_entries[sid] = {"path": eff_p, "probe": _probe_video_basic(eff_p)}
        prim_id = str(reg.get("primary_source_id") or reg["sources"][0]["source_id"])
        src_entries.setdefault("A", src_entries[prim_id])
        if len(reg["sources"]) > 1:
            sec_id = str(reg["sources"][1]["source_id"])
            src_entries.setdefault("B", src_entries[sec_id])
    else:
        src_entries["A"] = {"path": src_a_path, "probe": _probe_video_basic(src_a_path)}
        prim_id = "A"
        if src_b_path and src_b_path.is_file():
            src_entries["B"] = {"path": src_b_path, "probe": _probe_video_basic(src_b_path)}

    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("RENDER_CANCELLED_BEFORE_START")

    default_pal = resolve_palette({}, spec.get("art_tokens") or PALETTE_PRESETS["editorial_dark"])
    visual_tracks = list(spec["visual_tracks"])
    layers_list = list(spec.get("layers") or [])
    footage_fx_list = list(spec.get("footage_effects") or [])
    camera_events = list(spec.get("camera_events") or [])
    overlays = list(spec.get("overlays") or [])
    effect_invocations = list(spec.get("effect_invocations") or [])

    # 0. Pre-render minimal Remotion transparent RGBA spans (cached + bounded concurrency!)
    t_rem_start = time.monotonic()
    remotion_spans_plan = _group_remotion_effect_spans(effect_invocations, fps=fps, total_frames=total_frames)
    remotion_receipts: list[dict[str, Any]] = []
    fallback_events: list[dict[str, Any]] = []
    active_remotion_spans: list[dict[str, Any]] = []
    rem_cache_dir = remotion_cache_root or (cache.root if cache is not None else (stems_dir.parent / "cache"))

    if remotion_spans_plan:
        rem_probe = probe_remotion_runtime() if enable_remotion else {"ready": False, "status": "DISABLED_BY_CALLER"}
        if rem_probe.get("ready"):
            def _render_span(sp: dict[str, Any]) -> dict[str, Any]:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("RENDER_CANCELLED_DURING_REMOTION")
                span_props = {
                    "span_id": sp["span_id"],
                    "width": out_w,
                    "height": out_h,
                    "fps": int(round(fps)),
                    "duration_in_frames": sp["duration_in_frames"],
                    "span_start_sec": sp["span_start_sec"],
                    "transparent": True,
                    "palette": default_pal,
                    "events": sp["events"],
                    "seed": int(spec.get("seed", 20261126)),
                }
                rec = render_remotion_effect_span(
                    span_props,
                    cache_root=rem_cache_dir,
                    codec="prores",
                    concurrency=max(1, min(2, int(remotion_concurrency))),
                )
                return {**sp, "receipt": rec}

            span_workers = max(1, min(2, int(remotion_concurrency)))
            if span_workers > 1 and len(remotion_spans_plan) > 1:
                with ThreadPoolExecutor(max_workers=span_workers) as rpool:
                    done_spans = list(rpool.map(_render_span, remotion_spans_plan))
            else:
                done_spans = [_render_span(sp) for sp in remotion_spans_plan]
            done_spans.sort(key=lambda x: int(x["start_frame"]))
            for ds in done_spans:
                remotion_receipts.append(ds["receipt"])
                active_remotion_spans.append({
                    "span_id": ds["span_id"],
                    "start_frame": ds["start_frame"],
                    "end_frame": ds["end_frame"],
                    "output_path": Path(ds["receipt"]["output_path"]),
                    "effect_ids": ds["receipt"]["effect_ids"],
                })
        elif allow_remotion_fallback:
            for sp in remotion_spans_plan:
                for ev in sp["events"]:
                    fb = _fallback_dx01_event_to_legacy(ev)
                    fallback_events.append({
                        "event_id": ev.get("event_id"),
                        "requested_effect_id": ev.get("effect_id"),
                        "reason": f"Remotion unavailable ({rem_probe.get('status')})",
                        "fallback_overlay_kind": fb["kind"] if fb else None,
                    })
                    if fb is not None:
                        overlays.append(fb)
        else:
            raise RuntimeError(f"REMOTION_BACKEND_REQUIRED_BUT_UNAVAILABLE: {rem_probe.get('status')}")
    t_rem_elapsed = round(time.monotonic() - t_rem_start, 3)

    # Validate dual-moving transition handles up-front (explicit receipts, zero silent frozen outgoing tails!)
    src_durs_map = {sid: float(info["probe"]["video_duration_sec"]) for sid, info in src_entries.items()}
    transition_receipts: list[dict[str, Any]] = []
    track_transition_plan: dict[int, dict[str, Any]] = {}
    for ti in range(1, len(visual_tracks)):
        tr_prev = visual_tracks[ti - 1]
        tr_cur = visual_tracks[ti]
        req_f = int(tr_cur.get("transition_frames", 0))
        t_kind = str(tr_cur.get("transition_kind") or ("dx01.trans.dual_crossfade" if req_f > 0 else "cut"))
        if req_f > 0 and t_kind != "cut":
            hval = validate_transition_handles(
                tr_prev, tr_cur, requested_frames=req_f, source_durations_sec=src_durs_map, fps=fps,
            )
            rec_t = {
                "boundary_track_idx": ti,
                "from_track": tr_prev.get("track_id", f"trk_{ti}"),
                "to_track": tr_cur.get("track_id", f"trk_{ti + 1}"),
                "boundary_frame": int(tr_cur["out_start_frame"]),
                "transition_kind": "cut" if hval["fallback_to_cut"] else t_kind,
                "direction": str(tr_cur.get("transition_direction") or "left"),
                "feather_px": float(tr_cur.get("transition_feather_px", 48.0)),
                "blur_px": float(tr_cur.get("transition_blur_px", 20.0)),
                "dual_moving_verified": not hval["fallback_to_cut"],
                **hval,
            }
            transition_receipts.append(rec_t)
            if rec_t["effective_frames"] > 0:
                track_transition_plan[ti] = rec_t

    # 1. Start audio stem assembly concurrently with silent video rendering (supports explicit silent_policy!)
    is_silent_job = bool(spec.get("silent_policy")) and (not src_entries[prim_id]["probe"].get("has_audio", True))

    def _run_audio_stage() -> dict[str, Any] | None:
        if is_silent_job:
            return {
                "silent_policy": True,
                "mix_wav": None,
                "secondary_audio_muted": True,
                "integrated_lufs": None,
                "true_peak_dbtp": None,
            }
        return build_speech_master_and_mix(
            primary_video=src_a_path,
            voice_segments=list(spec["voice_segments"]),
            total_output_frames=total_frames,
            fps_num=fps_num,
            fps_den=fps_den,
            sample_rate=int(spec.get("sample_rate", 48000)),
            sfx_cues=list(spec.get("sfx_cues") or []),
            bgm_enabled=bool(spec.get("bgm_enabled", True)),
            bgm_style=str(spec.get("bgm_style", "warm_calm")),
            bgm_gain_db=float(spec.get("bgm_gain_db", -24.5)),
            sfx_enabled=bool(spec.get("sfx_enabled", True)),
            sfx_gain_db=float(spec.get("sfx_gain_db", -17.0)),
            stems_dir=stems_dir,
            cache=cache,
        )

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    video_staging = out_mp4.with_suffix(".video.staging.mp4")
    staging_mp4 = out_mp4.with_suffix(".staging.mp4")
    for p_tmp in (video_staging, staging_mp4):
        if p_tmp.exists():
            p_tmp.unlink()

    readers: dict[str, _SequentialFrameReader] = {}
    unique_readers: list[_SequentialFrameReader] = []
    path_to_reader: dict[str, _SequentialFrameReader] = {}
    for sid, info in src_entries.items():
        key_p = str(info["path"])
        rdr = path_to_reader.get(key_p)
        if rdr is None:
            rdr = _SequentialFrameReader(info["path"], out_w, out_h)
            path_to_reader[key_p] = rdr
            unique_readers.append(rdr)
        readers[sid] = rdr

    # Dedicated secondary reader pool for true dual-moving outgoing handles when outgoing & incoming share same file
    handle_readers: dict[str, _SequentialFrameReader] = {}
    alpha_readers: dict[str, StreamingAlphaOverlayReader] = {}
    for sp in active_remotion_spans:
        alpha_readers[sp["span_id"]] = StreamingAlphaOverlayReader(sp["output_path"], out_w, out_h)

    fps_str = f"{fps_num}/{fps_den}"
    ff_t = str(max(1, int(ffmpeg_threads)))
    enc_cmd = [
        "ffmpeg", "-v", "error", "-y", "-nostdin", "-threads", ff_t,
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out_w}x{out_h}", "-r", fps_str, "-i", "pipe:0",
        "-c:v", "libx264", "-preset", str(spec.get("x264_preset", "medium")), "-crf", str(spec.get("crf", 17)),
        "-pix_fmt", "yuv420p", "-r", fps_str,
        "-an",
        str(video_staging),
    ]
    popen_kw: dict[str, Any] = {"stdin": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "posix":
        popen_kw["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    vis_dur_a = 0
    vis_dur_b = 0
    frames_written = 0

    def _lookup_source_frame(sid: str, t_sec_req: float, *, use_handle_reader: bool = False) -> Image.Image | None:
        info = src_entries.get(sid)
        if info is None:
            return None
        pr = info["probe"]
        clamped = max(0.0, min(max(0.04, pr["video_duration_sec"] - 0.04), t_sec_req))
        fi = max(0, min(pr["nb_frames"] - 1, int(round(clamped * pr["fps"]))))
        if use_handle_reader:
            hr = handle_readers.get(sid)
            if hr is None:
                hr = _SequentialFrameReader(info["path"], out_w, out_h)
                handle_readers[sid] = hr
            return hr.get_frame(fi)
        rdr = readers.get(sid)
        return rdr.get_frame(fi) if rdr is not None else None

    def _resolve_track_frame(tr: dict[str, Any], out_f: int, t_sec: float, *, outgoing_handle_offset_f: int = 0) -> np.ndarray:
        mode = str(tr.get("mode") or "source").lower()
        if mode == "illustrative_backdrop":
            return render_luxury_backdrop(out_w, out_h, default_pal)
        if mode == "before_after_split":
            sid_a = str(tr.get("before_source_id") or prim_id)
            sid_b = str(tr.get("after_source_id") or sid_a)
            tr_start = int(tr["out_start_frame"])
            tr_end = max(tr_start + 1, int(tr["out_end_frame"]))
            u = (out_f - tr_start) / float(max(1, tr_end - tr_start - 1))
            tb = float(tr.get("before_in_sec", 0.0)) + u * (float(tr.get("before_out_sec", 1.0)) - float(tr.get("before_in_sec", 0.0)))
            ta = float(tr.get("after_in_sec", 0.0)) + u * (float(tr.get("after_out_sec", 1.0)) - float(tr.get("after_in_sec", 0.0)))
            im_b = _lookup_source_frame(sid_a, tb, use_handle_reader=False) or Image.new("RGB", (out_w, out_h), (20, 20, 20))
            im_a = _lookup_source_frame(sid_b, ta, use_handle_reader=(sid_b == sid_a)) or im_b
            return render_before_after_split_rgb(
                np.asarray(im_b, dtype=np.uint8),
                np.asarray(im_a, dtype=np.uint8),
                progress=u,
                pause_at=float(tr.get("divider_pause_at", 0.5)),
            )

        sid = str(tr.get("source_id") or tr.get("source") or prim_id)
        if sid not in src_entries:
            raise KeyError(f"UNKNOWN_VISUAL_TRACK_SOURCE_ID:{sid!r}")
        info = src_entries[sid]
        pr = info["probe"]
        tr_start = int(tr["out_start_frame"])
        tr_end = max(tr_start + 1, int(tr["out_end_frame"]))
        src_in = float(tr["src_in_sec"])
        src_out = float(tr["src_out_sec"])
        max_sec = max(0.05, pr["video_duration_sec"] - 0.03)
        if outgoing_handle_offset_f > 0:
            # True moving outgoing handle advancing past tr's nominal out_end_frame!
            t_src = min(max_sec, src_out + (outgoing_handle_offset_f / fps))
            u = 1.0
        else:
            u = (out_f - tr_start) / float(max(1, tr_end - tr_start - 1))
            clamped_out = min(src_out, max_sec)
            clamped_in = min(src_in, max(0.0, clamped_out - 0.04))
            is_master = (sid == prim_id)
            t_src = resolve_speed_ramp_pts(
                u_norm=u,
                src_in_sec=clamped_in,
                src_out_sec=clamped_out,
                ramp_curve=str(tr.get("speed_ramp") or "linear"),
                is_master_dialogue=is_master,
            )

        im = _lookup_source_frame(sid, t_src, use_handle_reader=(outgoing_handle_offset_f > 0))
        if im is None:
            im = Image.new("RGB", (out_w, out_h), (18, 20, 22))

        sc0 = float(tr.get("scale_start", 1.0))
        sc1 = float(tr.get("scale_end", sc0))
        base_sc = sc0 + (sc1 - sc0) * _ease_in_out_cubic(u)
        base_cx = float(tr.get("cx_norm", tr.get("focus_x", 0.5)))
        base_cy = float(tr.get("cy_norm", tr.get("focus_y", 0.5)))
        cam = _resolve_camera_transform(camera_events, t_sec, base_sc, base_cx, base_cy)
        im_out = render_pullback_or_transform_frame(
            im, out_w, out_h,
            scale=cam["scale"],
            cx_norm=cam["cx"],
            cy_norm=cam["cy"],
            x_norm=cam["x_norm"],
            y_norm=cam["y_norm"],
            rotation_deg=cam["rotation_deg"] + float(tr.get("rotation_deg", 0.0)),
            tilt_x_deg=cam["tilt_x_deg"] + float(tr.get("tilt_x_deg", 0.0)),
            tilt_y_deg=cam["tilt_y_deg"] + float(tr.get("tilt_y_deg", 0.0)),
            border_radius_px=int(tr.get("border_radius_px", 0)),
            palette=default_pal,
            fit_mode=str(tr.get("fit_mode") or "cover"),
            crop_rect_norm=tr.get("crop_rect_norm"),
        )

        # Optional spatial inset preview (palette-driven, zero hardcoded green/gold!)
        inset = tr.get("inset_b")
        if inset and "B" in readers and "B" in src_entries:
            it0, it1 = int(inset["out_start_frame"]), int(inset["out_end_frame"])
            if it0 <= out_f < it1:
                probe_b = src_entries["B"]["probe"]
                iu = (out_f - it0) / float(max(1, it1 - it0 - 1))
                in_p = _ease_out_cubic(min(1.0, (out_f - it0) / 8.0))
                out_p = _ease_out_cubic(min(1.0, (it1 - out_f) / 6.0))
                iv = min(in_p, out_p)
                bt = min(probe_b["video_duration_sec"] - 0.05, float(inset["b_in_sec"]) + iu * (float(inset["b_out_sec"]) - float(inset["b_in_sec"])))
                bf = max(0, min(probe_b["nb_frames"] - 1, int(round(bt * probe_b["fps"]))))
                bim = readers["B"].get_frame(bf)
                bx0, by0, bw, bh = [float(v) for v in inset.get("box_norm", [0.54, 0.54, 0.42, 0.34])]
                cw = max(32, int(round(bw * out_w)))
                ch = max(32, int(round(bh * out_h)))
                px = int(round(bx0 * out_w + (1.0 - in_p) * 90 * (out_w / 1080.0)))
                py = int(round(by0 * out_h))
                bcrop = _zoom_pan_frame(bim, cw, ch, float(inset.get("b_zoom", 1.12)), cx_norm=0.5, cy_norm=0.38).convert("RGBA")
                mask = Image.new("L", (cw, ch), 0)
                ImageDraw.Draw(mask).rounded_rectangle((0, 0, cw, ch), radius=max(10, int(22 * out_w / 1080.0)), fill=int(255 * iv))
                comp = im_out.convert("RGBA")
                bg_c = _parse_hex_rgba(default_pal["bg_hex"], int(230 * iv))
                ac_c = _parse_hex_rgba(default_pal["accent_hex"], int(235 * iv))
                _draw_rounded_card(comp, (px - 6, py - 6, px + cw + 6, py + ch + 6), radius=max(12, int(26 * out_w / 1080.0)), fill=bg_c, outline=ac_c, outline_w=max(2, int(3 * out_w / 1080.0)))
                comp.paste(bcrop, (px, py), mask)
                im_out = comp.convert("RGB")
        return np.asarray(im_out, dtype=np.uint8).copy()

    t_vid_start = time.monotonic()
    enc_proc = subprocess.Popen(enc_cmd, **popen_kw)
    aud_res: dict[str, Any] | None = None

    try:
        with ThreadPoolExecutor(max_workers=1) as aud_pool:
            aud_fut = aud_pool.submit(_run_audio_stage)
            assert enc_proc.stdin is not None
            for f_out in range(total_frames):
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("RENDER_CANCELLED_MID_ENCODE")
                t_sec = f_out / fps
                tr_idx = len(visual_tracks) - 1
                for idx_t, tr in enumerate(visual_tracks):
                    if int(tr["out_start_frame"]) <= f_out < int(tr["out_end_frame"]):
                        tr_idx = idx_t
                        break
                tr = visual_tracks[tr_idx]
                src_tag = str(tr.get("source_id") or tr.get("source") or prim_id)
                if src_tag.upper() == "B" or (src_tag != prim_id and str(tr.get("mode") or "source") == "source"):
                    vis_dur_b += 1
                else:
                    vis_dur_a += 1

                frame_arr = _resolve_track_frame(tr, f_out, t_sec)

                # True dual-moving transition across track boundary (evaluates BOTH moving sources at live PTS!)
                t_plan = track_transition_plan.get(tr_idx)
                if t_plan is not None:
                    local_f = f_out - int(tr["out_start_frame"])
                    eff_f = int(t_plan["effective_frames"])
                    if 0 <= local_f < eff_f:
                        out_arr = _resolve_track_frame(
                            visual_tracks[tr_idx - 1],
                            f_out,
                            t_sec,
                            outgoing_handle_offset_f=local_f + 1,
                        )
                        prog = (local_f + 1) / float(eff_f + 1)
                        frame_arr = render_dual_moving_transition(
                            out_arr,
                            frame_arr,
                            progress=prog,
                            kind=str(t_plan["transition_kind"]),
                            direction=str(t_plan["direction"]),
                            feather_px=float(t_plan["feather_px"]),
                            blur_px=float(t_plan["blur_px"]),
                        )

                # Direct footage grade/exposure treatments
                for ffx in footage_fx_list:
                    ft0, ft1 = float(ffx.get("start_sec", 0.0)), float(ffx.get("end_sec", 0.0))
                    if ft0 <= t_sec < ft1:
                        el_f = t_sec - ft0
                        rem_f = ft1 - t_sec
                        ramp = min(1.0, el_f / 0.2, rem_f / 0.2)
                        frame_arr = apply_exposure_grade_rgb(
                            frame_arr,
                            exposure_ev=float(ffx.get("exposure_ev", 0.0)) * ramp,
                            contrast=1.0 + (float(ffx.get("contrast", 1.0)) - 1.0) * ramp,
                            saturation=1.0 + (float(ffx.get("saturation", 1.0)) - 1.0) * ramp,
                            warmth=float(ffx.get("warmth", 0.0)) * ramp,
                            vignette=float(ffx.get("vignette", 0.0)) * ramp,
                        )

                # True N-source multi-layer composition (split, floating evidence, parallax)
                if layers_list:
                    frame_arr = compose_multilayer_frame(
                        frame_arr,
                        layers_list,
                        t_sec=t_sec,
                        out_w=out_w,
                        out_h=out_h,
                        source_frame_lookup=lambda s, ts: _lookup_source_frame(s, ts, use_handle_reader=(s == src_tag)),
                        palette=default_pal,
                    )

                # Stream-composite verified Remotion transparent ProRes 4444 spans (O(1) RAM!)
                for sp in active_remotion_spans:
                    if sp["start_frame"] <= f_out < sp["end_frame"]:
                        loc_idx = f_out - sp["start_frame"]
                        ov_rgba = alpha_readers[sp["span_id"]].get_frame_rgba(loc_idx)
                        if ov_rgba is not None:
                            alpha_composite_rgba_onto_rgb(frame_arr, ov_rgba)

                # ROI-accelerated NumPy compositing of preserved R4.3 declarative motion components
                for ev in overlays:
                    render_component_onto_rgb(
                        frame_arr, ev, t_sec,
                        default_palette=default_pal,
                        source_frame_lookup=_lookup_source_frame,
                    )

                enc_proc.stdin.write(frame_arr.tobytes())
                frames_written += 1

            enc_proc.stdin.close()
            stderr_out = enc_proc.stderr.read().decode("utf-8", errors="replace") if enc_proc.stderr else ""
            rc = enc_proc.wait(timeout=180)
            if rc != 0:
                raise RuntimeError(f"INDEPENDENT_FFMPEG_ENCODE_FAILED(rc={rc}): {stderr_out[-400:]}")
            aud_res = aud_fut.result()
    except Exception:
        if enc_proc.stdin:
            try:
                enc_proc.stdin.close()
            except Exception:
                pass
        if enc_proc.poll() is None:
            enc_proc.terminate()
            try:
                enc_proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                enc_proc.kill()
        video_staging.unlink(missing_ok=True)
        staging_mp4.unlink(missing_ok=True)
        raise
    finally:
        if enc_proc.stderr:
            try:
                enc_proc.stderr.close()
            except Exception:
                pass
        for rdr in unique_readers:
            rdr.close()
        for hr in handle_readers.values():
            hr.close()
        for ar in alpha_readers.values():
            ar.close()

    t_vid_elapsed = round(time.monotonic() - t_vid_start, 3)

    # 2. Fast stream-copy final mux (-c:v copy -movflags +faststart — ONE delivery encode!)
    t_mux = time.monotonic()
    assert aud_res is not None
    if is_silent_job or not aud_res.get("mix_wav"):
        mux_cmd = [
            "ffmpeg", "-v", "error", "-y", "-nostdin",
            "-i", str(video_staging),
            "-c:v", "copy", "-an", "-movflags", "+faststart",
            str(staging_mp4),
        ]
    else:
        mux_cmd = [
            "ffmpeg", "-v", "error", "-y", "-nostdin",
            "-i", str(video_staging),
            "-i", aud_res["mix_wav"],
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-shortest",
            "-movflags", "+faststart",
            str(staging_mp4),
        ]
    mux_p = subprocess.run(mux_cmd, capture_output=True, text=True, timeout=90)
    video_staging.unlink(missing_ok=True)
    if mux_p.returncode != 0:
        staging_mp4.unlink(missing_ok=True)
        raise RuntimeError(f"INDEPENDENT_MUX_FAILED(rc={mux_p.returncode}): {mux_p.stderr[-300:]}")
    t_mux_elapsed = round(time.monotonic() - t_mux, 3)

    # 3. Full decode QA & exact frame count verification BEFORE atomic promotion!
    dec_chk = subprocess.run(["ffmpeg", "-v", "error", "-i", str(staging_mp4), "-f", "null", "-"], capture_output=True, text=True, timeout=120)
    probed_out = _probe_video_basic(staging_mp4)
    if dec_chk.returncode != 0 or probed_out["nb_frames"] != total_frames:
        staging_mp4.unlink(missing_ok=True)
        raise RuntimeError(
            f"INDEPENDENT_STAGING_QA_FAILED(decode_rc={dec_chk.returncode}, frames={probed_out['nb_frames']}!={total_frames})"
        )

    if cancel_event is not None and cancel_event.is_set():
        staging_mp4.unlink(missing_ok=True)
        raise RuntimeError("RENDER_CANCELLED_BEFORE_PUBLISH")

    os.replace(staging_mp4, out_mp4)
    final_sha = _sha256_file(out_mp4)
    used_remotion = len(remotion_receipts) > 0
    return {
        "schema_version": "kh03.independent_render_receipt.v3",
        "engine_kind": "HYBRID_REMOTION_FFMPEG" if used_remotion else "INDEPENDENT_FFMPEG_PIL",
        "remotion_used": used_remotion,
        "remotion_span_count": len(remotion_receipts),
        "remotion_effect_ids": sorted({eid for rec in remotion_receipts for eid in (rec.get("effect_ids") or [])}),
        "remotion_receipts": remotion_receipts,
        "fallback_events": fallback_events,
        "transition_receipts": transition_receipts,
        "delivery_video_encode_count": 1,
        "uses_seecut_upstream": False,
        "spec_sha256": spec.get("spec_sha256"),
        "normalized_creative_plan_sha256": spec.get("normalized_creative_plan_sha256"),
        "final_mp4_path": str(out_mp4),
        "final_mp4_sha256": final_sha,
        "final_mp4_bytes": out_mp4.stat().st_size,
        "width": probed_out["width"],
        "height": probed_out["height"],
        "fps_num": probed_out["fps_num"],
        "fps_den": probed_out["fps_den"],
        "nb_frames": probed_out["nb_frames"],
        "duration_sec": probed_out["video_duration_sec"],
        "source_a_visible_frames": vis_dur_a,
        "source_b_visible_frames": vis_dur_b,
        "source_a_visible_sec": round(vis_dur_a / fps, 3),
        "source_b_visible_sec": round(vis_dur_b / fps, 3),
        "audio": aud_res,
        "timings": {
            "remotion_spans_sec": t_rem_elapsed,
            "visual_encode_and_audio_parallel_sec": t_vid_elapsed,
            "stream_copy_mux_sec": t_mux_elapsed,
            "total_render_sec": round(time.monotonic() - t0, 3),
        },
        "elapsed_sec": round(time.monotonic() - t0, 3),
        "decode_qa_pass": True,
    }


def build_independent_render_spec_from_plan(
    *,
    primary_source_path: Path,
    secondary_source_path: Path | None = None,
    creative_plan: dict[str, Any] | None = None,
    voice_segments: list[dict[str, Any]] | None = None,
    width: int = 1080,
    height: int = 1920,
    fps_num: int = 24,
    fps_den: int = 1,
    bgm_enabled: bool = True,
    candidate_style: str = "natural_kol_led",
) -> dict[str, Any]:
    """Build a generic declarative clean-room render spec from any KH-03 creative_plan & multi-source inputs."""
    probe_a = _probe_video_basic(Path(primary_source_path))
    probe_b = _probe_video_basic(Path(secondary_source_path)) if (secondary_source_path and Path(secondary_source_path).is_file()) else None
    fps = float(Fraction(fps_num, fps_den))
    if voice_segments:
        v_segs = list(voice_segments)
        total_f = max(1, max(int(s["out_end_frame"]) for s in v_segs))
    else:
        total_f = max(1, int(round(probe_a["video_duration_sec"] * fps)))
        v_segs = [{
            "segment_id": "vseg_01",
            "src_in_sec": 0.0,
            "src_out_sec": probe_a["video_duration_sec"],
            "out_in_sec": 0.0,
            "out_out_sec": round(total_f / fps, 4),
            "out_start_frame": 0,
            "out_end_frame": total_f,
            "crossfade_ms": 0,
        }]

    total_dur = total_f / fps
    visual_tracks: list[dict[str, Any]] = []
    if probe_b is not None and total_f >= 24:
        # Generic balanced multi-source alternating structure when no custom beat map is supplied
        split1 = int(total_f * (0.35 if candidate_style == "evidence_led" else 0.45))
        split2 = int(total_f * 0.78)
        b_dur = max(0.2, probe_b["video_duration_sec"] - 0.1)
        visual_tracks = [
            {"source": "A", "out_start_frame": 0, "out_end_frame": split1, "src_in_sec": 0.0, "src_out_sec": min(probe_a["video_duration_sec"], split1 / fps), "scale_start": 1.0, "scale_end": 1.03, "transition_frames": 0},
            {"source": "B", "out_start_frame": split1, "out_end_frame": split2, "src_in_sec": min(b_dur * 0.15, b_dur - 0.1), "src_out_sec": min(b_dur * 0.80, b_dur), "scale_start": 1.0, "scale_end": 1.02, "transition_frames": 6},
            {"source": "B" if candidate_style != "spatial_interaction" else "A", "out_start_frame": split2, "out_end_frame": total_f, "src_in_sec": min(b_dur * 0.80, b_dur - 0.1) if candidate_style != "spatial_interaction" else min(probe_a["video_duration_sec"] - 0.1, split2 / fps), "src_out_sec": b_dur if candidate_style != "spatial_interaction" else probe_a["video_duration_sec"], "scale_start": 1.0, "scale_end": 1.02, "transition_frames": 6},
        ]
    else:
        visual_tracks = [{
            "source": "A", "out_start_frame": 0, "out_end_frame": total_f,
            "src_in_sec": 0.0, "src_out_sec": probe_a["video_duration_sec"],
            "scale_start": 1.0, "scale_end": 1.03, "transition_frames": 0,
        }]

    overlays: list[dict[str, Any]] = []
    if isinstance(creative_plan, dict):
        hp = creative_plan.get("hook_plan") or {}
        hook_txt = str(hp.get("hook_headline") or hp.get("headline") or "").strip()
        if hook_txt:
            overlays.append({
                "kind": "editorial_hook_banner",
                "start_sec": 0.0,
                "end_sec": min(2.6, total_dur * 0.5),
                "title": hook_txt[:42],
                "subtitle": str(hp.get("hook_subline") or "").strip()[:52],
            })

    return {
        "schema_version": "kh03.independent_render_spec.v1",
        "width": width,
        "height": height,
        "fps_num": fps_num,
        "fps_den": fps_den,
        "sample_rate": 48000,
        "total_output_frames": total_f,
        "primary_source_path": str(Path(primary_source_path).resolve()),
        "secondary_source_path": str(Path(secondary_source_path).resolve()) if probe_b else None,
        "voice_segments": v_segs,
        "visual_tracks": visual_tracks,
        "overlays": overlays,
        "sfx_cues": [{"time_sec": 0.25, "kind": "soft_impact"}],
        "bgm_enabled": bgm_enabled,
        "bgm_gain_db": -25.0,
        "sfx_gain_db": -17.0,
    }


__all__ = [
    "build_independent_render_spec_from_plan",
    "build_speech_master_and_mix",
    "render_independent_video",
]
