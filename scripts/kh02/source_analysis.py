"""KH-02 Source Analysis: read-only ffprobe inspection, chunked SHA-256 inventory, silence/motion detection & evidence sampling."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from .contracts import safe_path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_fraction(val: str | None) -> tuple[int | None, int | None]:
    if not val or val in ("0/0", "N/A"):
        return None, None
    try:
        f = Fraction(val)
        if f <= 0:
            return None, None
        return f.numerator, f.denominator
    except (ValueError, ZeroDivisionError):
        return None, None


def _is_link_or_junction(p: Path) -> bool:
    return p.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(str(p)))


def probe_source(path: str | Path, *, allow_symlinks: bool = False) -> dict[str, Any]:
    """Probe a media or still file read-only via ffprobe. Raises RuntimeError on malformed media or ffprobe failure."""
    p = Path(path)
    if not allow_symlinks and (_is_link_or_junction(p) or any(_is_link_or_junction(par) for par in p.parents)):
        raise ValueError(f"Symlink source rejected: {p}")
    if not p.is_file():
        raise FileNotFoundError(f"Source file not found: {p}")

    size_bytes = p.stat().st_size
    if size_bytes <= 0:
        raise ValueError(f"Empty source file: {p}")

    cmd = [
        "ffprobe", "-v", "error",
        "-show_format", "-show_streams",
        "-of", "json", str(p),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed (rc={proc.returncode}) for {p}: {proc.stderr.strip()[:240]}")

    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ffprobe returned invalid JSON for {p}: {e}") from e

    streams = info.get("streams") or []
    fmt = info.get("format") or {}
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if vstream is None and astream is None:
        raise RuntimeError(f"No video or audio streams found in {p}")

    is_still = p.suffix.lower() in IMAGE_EXTS and astream is None
    fps_num, fps_den = _parse_fraction(vstream.get("r_frame_rate") if vstream else None)
    avg_num, avg_den = _parse_fraction(vstream.get("avg_frame_rate") if vstream else None)
    is_vfr = False
    if vstream and not is_still and fps_num and avg_num and fps_den and avg_den:
        r_f = Fraction(fps_num, fps_den)
        a_f = Fraction(avg_num, avg_den)
        if abs(float(r_f) - float(a_f)) > 0.25:
            is_vfr = True
        # Prefer avg_frame_rate when r_frame_rate is wildly inflated (e.g. 90000/1)
        if float(r_f) > 240 and 1 <= float(a_f) <= 240:
            fps_num, fps_den = avg_num, avg_den

    # Extract rotation metadata: prefer Display Matrix side_data_list over legacy tags.rotate
    rotation = 0
    if vstream:
        found_sd = False
        for sd in vstream.get("side_data_list") or []:
            if "rotation" in sd:
                try:
                    rotation = int(float(sd["rotation"])) % 360
                    found_sd = True
                    break
                except ValueError:
                    pass
        if not found_sd:
            tags = vstream.get("tags") or {}
            if "rotate" in tags:
                try:
                    rotation = int(float(tags["rotate"])) % 360
                except ValueError:
                    rotation = 0

    dur_raw = fmt.get("duration") or (vstream.get("duration") if vstream else None) or (astream.get("duration") if astream else None)
    duration_sec = None
    if dur_raw is not None and not is_still:
        try:
            d = float(dur_raw)
            if d > 0:
                duration_sec = round(d, 4)
        except ValueError:
            pass

    nb_frames = None
    if vstream and not is_still:
        nf_raw = vstream.get("nb_frames")
        if nf_raw and str(nf_raw).isdigit():
            nb_frames = int(nf_raw)
        elif duration_sec and fps_num and fps_den:
            nb_frames = int(round(duration_sec * fps_num / fps_den))

    width = int(vstream["width"]) if vstream and vstream.get("width") else None
    height = int(vstream["height"]) if vstream and vstream.get("height") else None
    if rotation in (90, 270) and width and height:
        # Report display dimensions cleanly while keeping rotation_deg explicit
        display_w, display_h = height, width
    else:
        display_w, display_h = width, height

    sar = (vstream.get("sample_aspect_ratio") if vstream else None) or "1:1"
    if sar == "0:1":
        sar = "1:1"

    return {
        "canonical_path": os.path.realpath(str(p)),
        "size_bytes": size_bytes,
        "sha256": sha256_file(p),
        "media": {
            "has_video": vstream is not None,
            "has_audio": astream is not None,
            "is_still": is_still,
            "duration_sec": duration_sec,
            "width": display_w,
            "height": display_h,
            "coded_width": width,
            "coded_height": height,
            "fps_num": None if is_still else fps_num,
            "fps_den": None if is_still else fps_den,
            "nb_frames": nb_frames,
            "is_vfr": is_vfr,
            "rotation_deg": rotation,
            "sar": sar,
            "video_codec": vstream.get("codec_name") if vstream else None,
            "audio_codec": astream.get("codec_name") if astream else None,
            "audio_sample_rate": int(astream["sample_rate"]) if astream and astream.get("sample_rate") else None,
            "audio_channels": int(astream["channels"]) if astream and astream.get("channels") else None,
            "color_space": (vstream.get("color_space") or vstream.get("pix_fmt")) if vstream else None,
        },
    }


def detect_silence_and_motion(
    video_path: str | Path,
    *,
    silence_db: float = -35.0,
    min_silence_sec: float = 0.4,
    sample_motion: bool = True,
) -> dict[str, Any]:
    """Measure audio silence intervals and lightweight scene-cut / motion telemetry via ffmpeg."""
    p = Path(video_path)
    probed = probe_source(p)
    dur = probed["media"].get("duration_sec") or 0.0
    silences: list[dict[str, float]] = []
    speech_spans: list[dict[str, float]] = []

    if probed["media"]["has_audio"] and dur > 0:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats", "-i", str(p),
                "-af", f"silencedetect=noise={silence_db}dB:d={min_silence_sec}",
                "-vn", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=60,
        )
        cur_start: float | None = None
        for line in proc.stderr.splitlines():
            ms = re.search(r"silence_start:\s*(-?[\d.]+)", line)
            if ms:
                cur_start = max(0.0, float(ms.group(1)))
            me = re.search(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)", line)
            if me:
                end = float(me.group(1))
                sdur = float(me.group(2))
                st = cur_start if cur_start is not None else max(0.0, end - sdur)
                silences.append({"start_sec": round(st, 3), "end_sec": round(end, 3), "duration_sec": round(sdur, 3)})
                cur_start = None
        if cur_start is not None and dur > cur_start:
            silences.append({"start_sec": round(cur_start, 3), "end_sec": round(dur, 3), "duration_sec": round(dur - cur_start, 3)})

        # Derive non-silent speech/audio spans
        cursor = 0.0
        for s in silences:
            if s["start_sec"] > cursor + 0.05:
                speech_spans.append({"start_sec": round(cursor, 3), "end_sec": round(s["start_sec"], 3)})
            cursor = max(cursor, s["end_sec"])
        if dur > cursor + 0.05:
            speech_spans.append({"start_sec": round(cursor, 3), "end_sec": round(dur, 3)})

    scene_changes: list[dict[str, float]] = []
    if sample_motion and probed["media"]["has_video"] and not probed["media"]["is_still"] and dur > 0:
        proc_sc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-i", str(p),
                "-vf", "scale=320:-2,select='gt(scene,0.28)',showinfo",
                "-an", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=60,
        )
        for line in proc_sc.stderr.splitlines():
            m = re.search(r"pts_time:([\d.]+)", line)
            if m:
                scene_changes.append({"time_sec": round(float(m.group(1)), 3)})

    return {
        "duration_sec": dur,
        "silences": silences,
        "speech_spans": speech_spans,
        "scene_changes": scene_changes,
    }


def build_inventory(
    source_specs: list[dict[str, Any]],
    run_id: str,
    *,
    allowed_roots: dict[str, str | Path] | None = None,
    cache_dir: str | Path | None = None,
    analyze_audio_motion: bool = True,
) -> dict[str, Any]:
    """Build deterministic read-only source inventory supporting multi-root unique IDs (no basename collision)."""
    assets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    cache_root = Path(cache_dir) if cache_dir else None
    if cache_root:
        cache_root.mkdir(parents=True, exist_ok=True)

    for idx, spec in enumerate(source_specs):
        root_id = str(spec.get("root_id") or "default")
        raw_id = str(spec.get("asset_id") or f"{root_id}_asset_{idx:03d}")
        if raw_id in seen_ids:
            raise ValueError(f"Duplicate asset_id in source inventory: {raw_id!r}")
        seen_ids.add(raw_id)

        raw_path = Path(spec["path"])
        if allowed_roots and root_id in allowed_roots:
            resolved = safe_path(raw_path, allowed_roots[root_id], allow_symlinks=False, must_exist=True)
        else:
            if raw_path.is_symlink():
                raise ValueError(f"Symlink source rejected: {raw_path}")
            resolved = Path(os.path.realpath(str(raw_path.expanduser())))
            if not resolved.is_file():
                raise FileNotFoundError(f"Source asset missing: {resolved}")

        rights = str(spec.get("rights") or "UNLICENSED")
        kind = str(spec.get("kind") or "a_roll")
        usable = (kind != "analysis_only") and (rights not in ("ANALYSIS_ONLY", "UNLICENSED"))

        # Check SHA cache first
        file_sha = sha256_file(resolved)
        cached_file = (cache_root / f"probe_{file_sha}.json") if cache_root else None
        if cached_file and cached_file.is_file():
            probed = json.loads(cached_file.read_text(encoding="utf-8"))
            probed["canonical_path"] = str(resolved)
        else:
            probed = probe_source(resolved, allow_symlinks=False)
            if analyze_audio_motion and not probed["media"]["is_still"]:
                am = detect_silence_and_motion(resolved)
                probed["silences"] = am["silences"]
                probed["speech_spans"] = am["speech_spans"]
                probed["scene_changes"] = am["scene_changes"]
            else:
                probed["silences"] = []
                probed["speech_spans"] = []
                probed["scene_changes"] = []
            if cached_file:
                cached_file.write_text(json.dumps(probed, ensure_ascii=False, indent=2), encoding="utf-8")

        face_rect = spec.get("face_rect")
        if face_rect is None:
            face_rect = "UNKNOWN"

        assets.append({
            "asset_id": raw_id,
            "root_id": root_id,
            "source_key": f"{root_id}:{raw_id}",
            "basename": resolved.name,
            "label": spec.get("label"),
            "canonical_path": str(resolved),
            "kind": kind,
            "rights": rights,
            "usable_in_render": usable,
            "sha256": probed["sha256"],
            "size_bytes": probed["size_bytes"],
            "media": probed["media"],
            "face_rect": face_rect,
            "silences": probed.get("silences", []),
            "speech_spans": probed.get("speech_spans", []),
            "scene_changes": probed.get("scene_changes", []),
        })

    # Deterministic order by source_key
    assets.sort(key=lambda a: (a["root_id"], a["asset_id"]))
    return {
        "schema_version": "kh02.source_inventory.v1",
        "run_id": run_id,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "assets": assets,
    }


def sample_evidence(
    video_path: str | Path,
    out_dir: str | Path,
    timestamps_sec: list[float],
    *,
    width: int = 540,
) -> list[dict[str, Any]]:
    """Extract downscaled inspection stills at target timestamps without altering source."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    probed = probe_source(video_path)
    dur = probed["media"].get("duration_sec") or 0.0
    results: list[dict[str, Any]] = []
    for idx, t in enumerate(timestamps_sec):
        tc = min(max(0.0, float(t)), max(0.0, dur - 0.05)) if dur > 0 else max(0.0, float(t))
        dst = out / f"sample_{idx:03d}_{tc:.2f}s.jpg"
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{tc:.3f}", "-i", str(video_path),
             "-frames:v", "1", "-vf", f"scale={width}:-2", str(dst)],
            capture_output=True, text=True, timeout=30,
        )
        ok = proc.returncode == 0 and dst.is_file() and dst.stat().st_size > 16
        results.append({
            "index": idx,
            "requested_sec": float(t),
            "clamped_sec": round(tc, 3),
            "path": str(dst) if ok else None,
            "ok": ok,
        })
    return results


def verify_source_integrity(inventory: dict[str, Any]) -> dict[str, Any]:
    """Re-hash every source asset to prove zero mutation before/after pipeline execution."""
    records = []
    all_ok = True
    for a in inventory.get("assets", []):
        p = Path(a["canonical_path"])
        if not p.is_file():
            records.append({"asset_id": a["asset_id"], "status": "MISSING", "before": a["sha256"], "after": None})
            all_ok = False
            continue
        after = sha256_file(p)
        matched = (after == a["sha256"])
        if not matched:
            all_ok = False
        records.append({
            "asset_id": a["asset_id"],
            "status": "UNCHANGED" if matched else "DRIFT_DETECTED",
            "before": a["sha256"],
            "after": after,
        })
    return {"all_unchanged": all_ok, "assets": records}
