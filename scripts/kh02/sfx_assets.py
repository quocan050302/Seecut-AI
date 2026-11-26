"""KH-02.R1 Licensed SFX Discovery, Provider Adapter (Local/MCP/API), Security & Provenance Engine (R1.03).

Strict rules:
- MCP is a retrieval protocol, NOT a grant of license rights.
- Provider software/API terms (e.g., Freesound noncommercial API tier, SFXMint download quotas) are tracked
  separately from individual audio asset rights (CC0 vs CC-BY vs NC/ND/UNKNOWN).
- Zero hardcoded fake URLs, zero unauthorized global MCP installs, zero TEST_ONLY audio in production renders.
"""

from __future__ import annotations

import abc
import csv
import hashlib
import ipaddress
import json
import math
import os
import re
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .contracts import hash_artifact, is_strict_number, safe_path, sanitize_identifier, validate_schema

AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".aif", ".aiff"})

DEFAULT_ALLOWED_HTTPS_HOSTS = frozenset({
    "sfxmint.com",
    "cdn.sfxmint.com",
    "freesound.org",
    "cdn.freesound.org",
})

# Upstream sfx_ledger.py compatibility categories
SFX_CATEGORIES = ("whoosh", "pop", "click", "impact", "ding", "chime", "riser", "rustle", "foley", "ambience", "texture", "bgm")

MAX_DECODE_DURATION_SEC = 180.0
MAX_SCAN_ENTRIES_DEFAULT = 2000
MAX_SCAN_BYTES_DEFAULT = 2 * 1024 * 1024 * 1024  # 2 GiB
MAX_SCAN_DEPTH_DEFAULT = 5
MAX_DOWNLOAD_BYTES_DEFAULT = 25 * 1024 * 1024  # 25 MiB per file


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _is_private_or_loopback_host(hostname: str) -> bool:
    """Block SSRF against loopback, private RFC1918, link-local, and multicast targets."""
    if not hostname:
        return True
    low = hostname.lower().strip(".")
    if low in ("localhost", "ip6-localhost", "metadata.google.internal", "169.254.169.254"):
        return True
    try:
        ip = ipaddress.ip_address(low)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(low, None)
        for fam, _, _, _, sockaddr in infos:
            addr = sockaddr[0]
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                return True
    except OSError:
        # Unresolvable host cannot be proven safe
        return True
    return False


def _sanitize_metadata_text(value: Any, max_len: int = 240) -> str:
    """Strip control chars / prompt-injection patterns from untrusted provider metadata."""
    s = str(value or "")
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", s).strip()
    return s[:max_len]


def probe_sfx(audio_path: str | Path, *, max_duration_sec: float = MAX_DECODE_DURATION_SEC) -> dict[str, Any]:
    """Decode-verify real audio file with ffprobe; reject HTML/corrupt/empty/oversized/non-audio files."""
    p = Path(audio_path)
    if not p.is_file():
        return {"valid": False, "error": f"File missing: {p}"}
    size = p.stat().st_size
    if size <= 0:
        return {"valid": False, "error": "Empty 0-byte file"}

    # Quick signature check against HTML/text masquerading as audio
    head = p.read_bytes()[:64].lstrip()
    if head.startswith((b"<!DOCTYPE", b"<html", b"<HTML", b"<?xml", b"{\"error", b"#!/")):
        return {"valid": False, "error": "Non-audio HTML/JSON/executable payload rejected"}

    pr = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels,duration:format=format_name,duration",
            "-of", "json", str(p),
        ],
        capture_output=True, text=True, timeout=30,
    )
    if pr.returncode != 0:
        return {"valid": False, "error": f"ffprobe failed (rc={pr.returncode}): {pr.stderr.strip()[:160]}"}
    try:
        info = json.loads(pr.stdout or "{}")
    except json.JSONDecodeError:
        return {"valid": False, "error": "Malformed ffprobe JSON"}

    streams = info.get("streams") or []
    fmt = info.get("format") or {}
    if not streams:
        return {"valid": False, "error": "No valid audio stream found"}
    st = streams[0]
    codec = str(st.get("codec_name") or "").lower()
    if not codec or codec in ("bin_data", "ansi"):
        return {"valid": False, "error": f"Unsupported audio codec: {codec!r}"}
    try:
        sr = int(st.get("sample_rate") or 0)
        ch = int(st.get("channels") or 0)
        dur = float(st.get("duration") or fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        return {"valid": False, "error": "Non-numeric audio stream properties"}
    if sr < 8000 or ch < 1 or not math.isfinite(dur) or dur < 0.01:
        return {"valid": False, "error": f"Invalid audio geometry sr={sr}, ch={ch}, dur={dur}"}
    if dur > max_duration_sec:
        return {"valid": False, "error": f"Audio duration {dur:.2f}s exceeds cap {max_duration_sec:.1f}s"}

    return {
        "valid": True,
        "byte_size": size,
        "sha256": hash_artifact(p),
        "codec": codec,
        "container": str(fmt.get("format_name") or p.suffix.lstrip(".")),
        "sample_rate": sr,
        "channels": ch,
        "duration_sec": round(dur, 4),
    }


def measure_onset(
    audio_path: str | Path,
    *,
    sample_rate: int = 48000,
    window_ms: float = 10.0,
    onset_ratio: float = 0.10,
    max_decode_sec: float = 30.0,
) -> dict[str, Any]:
    """Measure real audio first transient onset, time-to-peak, first_hit_len, peak_db & rms_db via bounded PCM decode.

    Matches upstream sfx_ledger.py 10ms RMS envelope + 10% peak threshold convention.
    """
    p = Path(audio_path)
    if not p.is_file():
        return {"measured": False, "error": f"File missing: {p}"}

    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-t", str(max_decode_sec), "-i", str(p),
            "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-",
        ],
        capture_output=True, timeout=30,
    )
    if proc.returncode != 0 or len(proc.stdout) < 4:
        return {"measured": False, "error": f"PCM decode failed rc={proc.returncode}"}

    n = len(proc.stdout) // 2
    samples = struct.unpack(f"<{n}h", proc.stdout[: n * 2])
    if n == 0:
        return {"measured": False, "error": "Zero decoded samples"}

    peak_abs = max(abs(x) for x in samples)
    if peak_abs == 0:
        return {
            "measured": True,
            "onset_sec": 0.0,
            "onset_sample": 0,
            "time_to_peak_sec": 0.0,
            "first_hit_len_sec": round(n / float(sample_rate), 4),
            "peak_db": -96.0,
            "rms_db": -96.0,
        }

    peak_idx = next(i for i, x in enumerate(samples) if abs(x) == peak_abs)
    win = max(1, int(round((window_ms / 1000.0) * sample_rate)))
    rms_vals: list[float] = []
    for i in range(0, n, win):
        chunk = samples[i : i + win]
        mean_sq = sum(x * x for x in chunk) / max(1, len(chunk))
        rms_vals.append(math.sqrt(mean_sq) / 32768.0)

    max_rms = max(rms_vals) if rms_vals else 0.0
    thresh = max(1e-4, max_rms * onset_ratio)
    onset_bin = next((idx for idx, v in enumerate(rms_vals) if v >= thresh), 0)
    onset_sample = onset_bin * win
    onset_sec = onset_sample / float(sample_rate)

    # First hit length: from onset until envelope drops below thresh again (or peak + 80ms)
    end_bin = len(rms_vals)
    peak_bin = min(len(rms_vals) - 1, peak_idx // win)
    for b in range(peak_bin + 1, len(rms_vals)):
        if rms_vals[b] < thresh * 0.8:
            end_bin = b
            break
    first_hit_len = max(0.02, ((end_bin - onset_bin) * win) / float(sample_rate))

    overall_rms = math.sqrt(sum(x * x for x in samples) / float(n)) / 32768.0
    peak_db = round(20.0 * math.log10(max(1e-6, peak_abs / 32768.0)), 2)
    rms_db = round(20.0 * math.log10(max(1e-6, overall_rms)), 2)

    return {
        "measured": True,
        "onset_sec": round(onset_sec, 4),
        "onset_sample": int(onset_sample),
        "time_to_peak_sec": round(peak_idx / float(sample_rate), 4),
        "first_hit_len_sec": round(min(first_hit_len, n / float(sample_rate)), 4),
        "peak_db": peak_db,
        "rms_db": rms_db,
    }


def verify_license(
    metadata: dict[str, Any],
    *,
    intended_purpose: str = "NONCOMMERCIAL_ONLY",
) -> dict[str, Any]:
    """Verify individual asset rights AND provider API terms separately. Never self-certify from filename alone."""
    raw_lic = str(metadata.get("license_name") or "").strip()
    lic_up = raw_lic.upper()
    evidence = str(metadata.get("license_evidence") or "").strip()
    provider_terms = str(metadata.get("provider_terms_note") or "").strip().upper()
    author = str(metadata.get("author") or "").strip()
    attr_text = str(metadata.get("attribution_text") or "").strip()
    is_test = bool(metadata.get("test_only"))

    if is_test:
        return {
            "approved": False,
            "license_status": "TEST_ONLY_FIXTURE",
            "approved_purpose": "TEST_ONLY",
            "attribution_required": False,
            "restrictions": ["TEST_ONLY_FORBIDDEN_IN_PRODUCTION"],
            "reason": "Generated or mock TEST_ONLY fixture cannot enter production renders.",
        }

    # Block missing license metadata or filename-only claims without evidence
    if not raw_lic or lic_up in ("UNKNOWN", "UNSPECIFIED", "NONE", "COPYRIGHTED", "ALL RIGHTS RESERVED"):
        return {
            "approved": False,
            "license_status": "REJECTED_UNKNOWN_LICENSE",
            "approved_purpose": "BLOCKED",
            "attribution_required": False,
            "restrictions": ["unknown_license"],
            "reason": f"Missing or unknown license ({raw_lic!r}); cannot self-certify from filename.",
        }
    if not evidence:
        return {
            "approved": False,
            "license_status": "REJECTED_UNKNOWN_LICENSE",
            "approved_purpose": "BLOCKED",
            "attribution_required": False,
            "restrictions": ["missing_license_evidence"],
            "reason": "No verifiable license evidence/URL/manifest record provided.",
        }

    # Reject ND (NoDerivatives — cannot be remixed/trimmed into composite video soundtrack)
    if "ND" in re.split(r"[^A-Z0-9]+", lic_up) or "NO DERIV" in lic_up or "NODERIV" in lic_up:
        return {
            "approved": False,
            "license_status": "REJECTED_ND_INCOMPATIBLE",
            "approved_purpose": "BLOCKED",
            "attribution_required": True,
            "restrictions": ["no_derivatives_incompatible_with_video_mix"],
            "reason": f"License {raw_lic!r} forbids derivatives (incompatible with video soundtrack mixing).",
        }

    # Check NC restriction vs intended purpose
    is_nc = ("NC" in re.split(r"[^A-Z0-9]+", lic_up)) or ("NONCOMMERCIAL" in lic_up)
    if is_nc and intended_purpose == "COMMERCIAL_AND_NONCOMMERCIAL":
        return {
            "approved": False,
            "license_status": "REJECTED_NC_INCOMPATIBLE",
            "approved_purpose": "BLOCKED",
            "attribution_required": True,
            "restrictions": ["noncommercial_only"],
            "reason": f"Noncommercial asset license {raw_lic!r} blocked for commercial release.",
        }

    # Check provider API contractual terms (e.g. Freesound free API is noncommercial even for CC0 files!)
    api_noncommercial_only = (
        "NONCOMMERCIAL_API_ONLY" in provider_terms
        or "FREESOUND_FREE_API_NONCOMMERCIAL" in provider_terms
    )
    if api_noncommercial_only and intended_purpose == "COMMERCIAL_AND_NONCOMMERCIAL":
        return {
            "approved": False,
            "license_status": "REJECTED_PROVIDER_TERMS",
            "approved_purpose": "BLOCKED",
            "attribution_required": False,
            "restrictions": ["provider_api_noncommercial_only"],
            "reason": "Provider API terms restrict free tier to noncommercial use even when individual file is CC0.",
        }

    # CC0 / Public Domain
    if any(k in lic_up for k in ("CC0", "CC-0", "PUBLIC DOMAIN", "UNLICENSE")):
        purpose_out = "NONCOMMERCIAL_ONLY" if api_noncommercial_only else "COMMERCIAL_AND_NONCOMMERCIAL"
        restrictions = ["provider_api_noncommercial_only"] if api_noncommercial_only else []
        restrictions.append("seecut_polyform_noncommercial_gate_separate")
        return {
            "approved": True,
            "license_status": "APPROVED_CC0",
            "approved_purpose": purpose_out,
            "attribution_required": False,
            "restrictions": restrictions,
            "reason": f"Verified CC0 asset with evidence ({evidence[:80]}).",
        }

    # Owner-supplied local verified library
    if lic_up in ("OWNER_SUPPLIED_NONCOMMERCIAL", "OWNER_OWNED_LICENSED"):
        if lic_up == "OWNER_SUPPLIED_NONCOMMERCIAL" and intended_purpose == "COMMERCIAL_AND_NONCOMMERCIAL":
            return {
                "approved": False,
                "license_status": "REJECTED_NC_INCOMPATIBLE",
                "approved_purpose": "BLOCKED",
                "attribution_required": False,
                "restrictions": ["owner_noncommercial_only"],
                "reason": "Owner local sound marked noncommercial only.",
            }
        return {
            "approved": True,
            "license_status": "APPROVED_OWNER_LOCAL",
            "approved_purpose": "NONCOMMERCIAL_ONLY" if "NONCOMMERCIAL" in lic_up else "COMMERCIAL_AND_NONCOMMERCIAL",
            "attribution_required": False,
            "restrictions": ["seecut_polyform_noncommercial_gate_separate"],
            "reason": f"Verified owner-supplied local SFX ({evidence[:80]}).",
        }

    # CC-BY (requires explicit author + attribution text!)
    if "CC-BY" in lic_up or "CC BY" in lic_up or "ATTRIBUTION" in lic_up:
        if not author and not attr_text:
            return {
                "approved": False,
                "license_status": "REJECTED_MISSING_ATTRIBUTION",
                "approved_purpose": "BLOCKED",
                "attribution_required": True,
                "restrictions": ["missing_cc_by_attribution"],
                "reason": f"{raw_lic!r} requires author/attribution_text metadata before acceptance.",
            }
        purpose_out = "NONCOMMERCIAL_ONLY" if (is_nc or api_noncommercial_only) else "COMMERCIAL_AND_NONCOMMERCIAL"
        return {
            "approved": True,
            "license_status": "APPROVED_CC_BY",
            "approved_purpose": purpose_out,
            "attribution_required": True,
            "restrictions": ["attribution_required_in_credits", "seecut_polyform_noncommercial_gate_separate"],
            "reason": f"Verified {raw_lic} asset with attribution metadata ({author or attr_text}).",
        }

    return {
        "approved": False,
        "license_status": "REJECTED_UNKNOWN_LICENSE",
        "approved_purpose": "BLOCKED",
        "attribution_required": False,
        "restrictions": ["unrecognized_license"],
        "reason": f"Unrecognized license {raw_lic!r} rejected by fail-closed policy.",
    }


def assert_not_test_only_in_production(assets: list[dict[str, Any]], *, production_mode: bool = True) -> None:
    """Hard runtime guard blocking TEST_ONLY generated/mock fixtures from entering production renders."""
    if not production_mode:
        return
    for a in assets:
        if a.get("test_only") or a.get("approved_purpose") == "TEST_ONLY" or a.get("license_status") == "TEST_ONLY_FIXTURE":
            raise ValueError(f"TEST_ONLY audio asset {a.get('asset_id')!r} forbidden in production render")


def normalize_provider_result(
    raw: dict[str, Any],
    *,
    provider: str,
    probe_info: dict[str, Any] | None = None,
    onset_info: dict[str, Any] | None = None,
    relative_cache_path: str | None = None,
    intended_purpose: str = "NONCOMMERCIAL_ONLY",
) -> dict[str, Any]:
    """Normalize heterogeneous provider/local metadata + real probe/onset measurements into canonical SFX asset schema."""
    pid = sanitize_identifier(str(raw.get("provider_asset_id") or raw.get("id") or "unknown"))
    lic = verify_license(raw, intended_purpose=intended_purpose)
    pr = probe_info or {}
    on = onset_info or {}
    sha = str(pr.get("sha256") or raw.get("sha256") or "00000000")
    asset_id = sanitize_identifier(f"{provider}_{pid}_{sha[:10]}")

    cat_raw = _sanitize_metadata_text(raw.get("category") or "foley", 32).lower()
    cat = cat_raw if cat_raw in SFX_CATEGORIES else "foley"
    tags = [_sanitize_metadata_text(t, 32).lower() for t in (raw.get("tags") or []) if str(t).strip()][:16]

    return {
        "asset_id": asset_id,
        "provider": sanitize_identifier(provider, 32),
        "provider_asset_id": pid,
        "source_url": _sanitize_metadata_text(raw.get("source_url"), 300) or None,
        "license_name": _sanitize_metadata_text(raw.get("license_name") or "UNKNOWN", 64),
        "license_version": _sanitize_metadata_text(raw.get("license_version"), 24) or None,
        "license_url": _sanitize_metadata_text(raw.get("license_url"), 300) or None,
        "license_evidence": _sanitize_metadata_text(raw.get("license_evidence"), 300) or None,
        "provider_terms_note": _sanitize_metadata_text(raw.get("provider_terms_note"), 200) or None,
        "author": _sanitize_metadata_text(raw.get("author"), 120) or None,
        "attribution_required": bool(lic["attribution_required"]),
        "attribution_text": _sanitize_metadata_text(raw.get("attribution_text"), 240) or None,
        "download_permitted": bool(raw.get("download_permitted", True)) and lic["approved"],
        "retrieved_at_utc": raw.get("retrieved_at_utc") or _now_utc(),
        "request_id": _sanitize_metadata_text(raw.get("request_id"), 80) or None,
        "approved_purpose": lic["approved_purpose"],
        "license_status": lic["license_status"],
        "relative_cache_path": relative_cache_path,
        "sha256": sha,
        "normalized_wav_sha256": raw.get("normalized_wav_sha256"),
        "byte_size": int(pr.get("byte_size") or raw.get("byte_size") or 1),
        "codec": str(pr.get("codec") or raw.get("codec") or "unknown"),
        "container": str(pr.get("container") or raw.get("container") or "unknown"),
        "sample_rate": int(pr.get("sample_rate") or raw.get("sample_rate") or 48000),
        "channels": int(pr.get("channels") or raw.get("channels") or 1),
        "duration_sec": float(pr.get("duration_sec") or raw.get("duration_sec") or 0.01),
        "onset_sec": float(on.get("onset_sec", 0.0)),
        "time_to_peak_sec": float(on.get("time_to_peak_sec", 0.0)),
        "first_hit_len_sec": float(on.get("first_hit_len_sec", pr.get("duration_sec") or 0.1)),
        "peak_db": float(on.get("peak_db", -20.0)),
        "rms_db": float(on.get("rms_db", -26.0)),
        "category": cat,
        "tags": tags,
        "restrictions": list(lic["restrictions"]),
        "test_only": bool(raw.get("test_only", False)),
    }


def discover_local_library(
    root_dirs: list[str | Path],
    *,
    license_manifest_name: str = "SFX_LICENSE_MANIFEST.json",
    intended_purpose: str = "NONCOMMERCIAL_ONLY",
    max_entries: int = MAX_SCAN_ENTRIES_DEFAULT,
    max_bytes: int = MAX_SCAN_BYTES_DEFAULT,
    max_depth: int = MAX_SCAN_DEPTH_DEFAULT,
    max_walltime_sec: float = 15.0,
) -> dict[str, Any]:
    """Bounded, read-only local SFX library discovery rejecting symlinks, hidden paths, traversal & unlicensed files."""
    t_start = time.monotonic()
    scanned_entries = 0
    scanned_bytes = 0
    rejected: list[dict[str, str]] = []
    assets: list[dict[str, Any]] = []
    seen_dirs: list[str] = []
    truncated = False

    for raw_root in root_dirs:
        root = Path(raw_root).expanduser()
        if not root.exists() or not root.is_dir():
            seen_dirs.append(f"{root} (ABSENT)")
            continue
        root_real = Path(os.path.realpath(str(root)))
        # Guard against scanning HOME or filesystem root
        if root_real in (Path("/"), Path.home().resolve()):
            rejected.append({"path": str(root), "reason": "Refusing full HOME or root scan"})
            continue
        seen_dirs.append(str(root_real))

        # Load optional per-root explicit license manifest (maps relative path or '*' -> license metadata)
        manifest_map: dict[str, dict[str, Any]] = {}
        mpath = root_real / license_manifest_name
        if mpath.is_file() and not mpath.is_symlink():
            try:
                mdata = json.loads(mpath.read_text(encoding="utf-8"))
                if isinstance(mdata, dict):
                    manifest_map = mdata
            except Exception as ex:
                rejected.append({"path": str(mpath), "reason": f"Invalid manifest JSON: {ex}"})

        # Iterative BFS bounded by depth, entries, bytes, and walltime
        queue: list[tuple[Path, int]] = [(root_real, 0)]
        while queue:
            if (time.monotonic() - t_start) > max_walltime_sec:
                truncated = True
                break
            cur_dir, depth = queue.pop(0)
            if depth > max_depth:
                continue
            try:
                children = sorted(cur_dir.iterdir(), key=lambda x: x.name)
            except OSError as ex:
                rejected.append({"path": str(cur_dir), "reason": f"Unreadable directory: {ex}"})
                continue

            for entry in children:
                if entry.name.startswith("."):
                    continue
                scanned_entries += 1
                if scanned_entries > max_entries:
                    truncated = True
                    break
                if entry.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(str(entry))):
                    rejected.append({"path": str(entry), "reason": "Symlink rejected during bounded local SFX scan"})
                    continue
                try:
                    safe_path(entry, root_real, allow_symlinks=False)
                except ValueError as ex:
                    rejected.append({"path": str(entry), "reason": str(ex)})
                    continue

                if entry.is_dir():
                    if depth < max_depth:
                        queue.append((entry, depth + 1))
                    continue

                if entry.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                try:
                    fsize = entry.stat().st_size
                except OSError as ex:
                    rejected.append({"path": str(entry), "reason": f"stat failed: {ex}"})
                    continue
                scanned_bytes += fsize
                if scanned_bytes > max_bytes:
                    truncated = True
                    break

                pr = probe_sfx(entry)
                if not pr.get("valid"):
                    rejected.append({"path": str(entry), "reason": str(pr.get("error"))})
                    continue
                on = measure_onset(entry)
                if not on.get("measured"):
                    rejected.append({"path": str(entry), "reason": str(on.get("error"))})
                    continue

                rel = str(entry.relative_to(root_real))
                meta = dict(manifest_map.get(rel) or manifest_map.get(entry.name) or manifest_map.get("*") or {})
                meta.setdefault("provider_asset_id", entry.stem)
                # Category hint from subfolder or filename (never license!)
                if "category" not in meta:
                    low_name = rel.lower()
                    meta["category"] = next((c for c in SFX_CATEGORIES if c in low_name), "foley")
                meta.setdefault("tags", [w for w in re.split(r"[^a-z0-9]+", entry.stem.lower()) if len(w) >= 2])

                norm = normalize_provider_result(
                    meta,
                    provider="local",
                    probe_info=pr,
                    onset_info=on,
                    relative_cache_path=rel,
                    intended_purpose=intended_purpose,
                )
                norm["local_abs_path"] = str(entry)
                if norm["approved_purpose"] == "BLOCKED":
                    rejected.append({"path": rel, "reason": f"License rejected: {norm['license_status']}"})
                    continue
                assets.append(norm)
            if truncated:
                break

    deduped, dup_report = dedup_assets(assets)
    return {
        "scanned_dirs": seen_dirs,
        "scanned_entries": scanned_entries,
        "scanned_bytes": scanned_bytes,
        "truncated_by_bounds": truncated,
        "assets": deduped,
        "duplicates": dup_report,
        "rejected": rejected,
    }


def dedup_assets(assets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deduplicate identical bytes by SHA-256 while preserving per-source provenance (never merging incompatible rights)."""
    by_sha: dict[str, dict[str, Any]] = {}
    dups: list[dict[str, Any]] = []
    for a in sorted(assets, key=lambda x: (x.get("sha256", ""), x.get("asset_id", ""))):
        sha = a["sha256"]
        if sha not in by_sha:
            item = dict(a)
            item["alternate_sources"] = []
            by_sha[sha] = item
        else:
            existing = by_sha[sha]
            if existing["license_status"] == a["license_status"] and existing["approved_purpose"] == a["approved_purpose"]:
                existing["alternate_sources"].append({
                    "provider": a["provider"],
                    "provider_asset_id": a["provider_asset_id"],
                    "source_url": a.get("source_url"),
                })
                dups.append({"sha256": sha, "kept": existing["asset_id"], "deduped": a["asset_id"], "status": "MERGED_COMPATIBLE_RIGHTS"})
            else:
                # Incompatible rights must remain distinct records, never silently merged
                key = f"{sha}:{a['asset_id']}"
                by_sha[key] = dict(a)
                dups.append({"sha256": sha, "kept": existing["asset_id"], "separate": a["asset_id"], "status": "KEPT_SEPARATE_INCOMPATIBLE_RIGHTS"})
    return list(by_sha.values()), dups


def safe_download(
    url: str,
    dest_dir: str | Path,
    *,
    allowed_hosts: frozenset[str] = DEFAULT_ALLOWED_HTTPS_HOSTS,
    allow_local_test_http: bool = False,
    max_bytes: int = MAX_DOWNLOAD_BYTES_DEFAULT,
    timeout_sec: float = 15.0,
    max_redirects: int = 3,
    headers: dict[str, str] | None = None,
    opener: Any = None,
) -> dict[str, Any]:
    """Hardened streaming download: HTTPS host allowlist, SSRF/redirect/size/time bounds, .part cleanup, ffprobe check & hash rename."""
    dest_root = Path(dest_dir).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)

    cur_url = str(url).strip()
    redirects = 0
    while True:
        parsed = urllib.parse.urlparse(cur_url)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        if allow_local_test_http and scheme == "http" and host in ("127.0.0.1", "localhost"):
            pass
        else:
            if scheme != "https":
                return {"ok": False, "status": "REJECTED_SCHEME", "error": f"Only HTTPS downloads permitted (got {scheme!r})"}
            if host not in allowed_hosts:
                return {"ok": False, "status": "REJECTED_HOST", "error": f"Host {host!r} not in approved allowlist"}
            if _is_private_or_loopback_host(host):
                return {"ok": False, "status": "REJECTED_SSRF", "error": f"Blocked private/loopback/unresolvable host {host!r}"}

        # Manual redirect handler so every hop is re-validated against SSRF & allowlist
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                return None

        req = urllib.request.Request(cur_url, headers=headers or {"User-Agent": "seecut-ai-kh02r1/1.0"})
        op = opener or urllib.request.build_opener(_NoRedirect)
        try:
            resp = op.open(req, timeout=timeout_sec)
            status_code = getattr(resp, "status", 200)
            break
        except urllib.error.HTTPError as he:
            if he.code in (301, 302, 303, 307, 308):
                redirects += 1
                if redirects > max_redirects:
                    return {"ok": False, "status": "REJECTED_REDIRECT_LIMIT", "error": f"Exceeded max_redirects={max_redirects}"}
                loc = he.headers.get("Location")
                if not loc:
                    return {"ok": False, "status": "ERROR_REDIRECT_NO_LOCATION", "error": "Redirect missing Location header"}
                cur_url = urllib.parse.urljoin(cur_url, loc)
                continue
            retry_after = he.headers.get("Retry-After") if he.headers else None
            if he.code == 429:
                return {"ok": False, "status": "RATE_LIMITED_429", "http_status": 429, "retry_after": retry_after, "error": "Provider quota/rate limit (429); honoring Retry-After without bypass"}
            if he.code in (401, 403):
                return {"ok": False, "status": "AUTH_FAILED", "http_status": he.code, "error": f"Provider auth/permission denied (HTTP {he.code})"}
            return {"ok": False, "status": "HTTP_ERROR", "http_status": he.code, "error": f"HTTP error {he.code}"}
        except (urllib.error.URLError, TimeoutError, socket.timeout) as ex:
            return {"ok": False, "status": "NETWORK_OR_TIMEOUT", "error": f"Network/timeout error: {ex}"}

    # Stream to owned .part file with hard byte cap
    ext = Path(urllib.parse.urlparse(cur_url).path).suffix.lower()
    if ext not in AUDIO_EXTENSIONS:
        ext = ".wav"
    part_name = f"dl_{os.getpid()}_{threading.get_ident()}_{int(time.time() * 1000)}.part"
    part_path = safe_path(part_name, dest_root)
    h = hashlib.sha256()
    total = 0
    try:
        with resp, part_path.open("wb") as out_f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"Download exceeded max_bytes={max_bytes}")
                h.update(chunk)
                out_f.write(chunk)
    except Exception as ex:
        part_path.unlink(missing_ok=True)
        return {"ok": False, "status": "DOWNLOAD_FAILED_OR_OVERSIZED", "error": str(ex)}

    if total == 0:
        part_path.unlink(missing_ok=True)
        return {"ok": False, "status": "EMPTY_PAYLOAD", "error": "Downloaded 0 bytes"}

    # Decode-verify audio BEFORE promoting from .part
    pr = probe_sfx(part_path)
    if not pr.get("valid"):
        part_path.unlink(missing_ok=True)
        return {"ok": False, "status": "INVALID_AUDIO_PAYLOAD", "error": str(pr.get("error"))}

    sha = h.hexdigest()
    final_name = f"{sha[:24]}{ext}"
    final_path = safe_path(final_name, dest_root)
    part_path.replace(final_path)
    on = measure_onset(final_path)
    return {
        "ok": True,
        "status": "DOWNLOADED_VERIFIED",
        "http_status": status_code,
        "final_path": str(final_path),
        "relative_cache_path": final_name,
        "sha256": sha,
        "byte_size": total,
        "probe": pr,
        "onset": on,
    }


class SfxProvider(abc.ABC):
    """Unified provider adapter contract for local library, MCP server, or approved HTTPS API."""

    provider_id: str
    kind: str  # "local" | "mcp" | "api"

    @abc.abstractmethod
    def capabilities(self) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    def search(self, query: str, filters: dict[str, Any] | None = None, limit: int = 5) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    def inspect(self, asset_id: str) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    def download(self, asset_id: str, approved_destination: str | Path) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    def status(self) -> dict[str, Any]:
        ...


class LocalProvider(SfxProvider):
    """Read-only provider backed by approved local directories and/or persistent workspace SFX cache."""

    def __init__(self, root_dirs: list[str | Path], *, intended_purpose: str = "NONCOMMERCIAL_ONLY") -> None:
        self.provider_id = "local_library"
        self.kind = "local"
        self.root_dirs = [Path(d) for d in root_dirs]
        self.intended_purpose = intended_purpose
        self._scan = discover_local_library(self.root_dirs, intended_purpose=intended_purpose)
        self._by_id = {a["asset_id"]: a for a in self._scan["assets"]}

    def capabilities(self) -> dict[str, Any]:
        return {"can_search": True, "can_inspect": True, "can_download": False, "offline_capable": True}

    def search(self, query: str, filters: dict[str, Any] | None = None, limit: int = 5) -> dict[str, Any]:
        q_toks = set(re.split(r"[^a-z0-9]+", query.lower())) - {""}
        cat_f = (filters or {}).get("category")
        scored: list[tuple[float, dict[str, Any]]] = []
        for a in self._scan["assets"]:
            if cat_f and a["category"] != cat_f:
                continue
            hay = set(a.get("tags", [])) | {a["category"], a["provider_asset_id"].lower()}
            overlap = len(q_toks & hay)
            if overlap > 0 or not q_toks:
                scored.append((overlap + (1.0 if a["category"] in q_toks else 0.0), a))
        scored.sort(key=lambda x: (-x[0], x[1]["asset_id"]))
        return {"ok": True, "status": "OK", "results": [a for _, a in scored[:limit]]}

    def inspect(self, asset_id: str) -> dict[str, Any]:
        a = self._by_id.get(asset_id)
        return {"ok": a is not None, "asset": a, "status": "OK" if a else "NOT_FOUND"}

    def download(self, asset_id: str, approved_destination: str | Path) -> dict[str, Any]:
        a = self._by_id.get(asset_id)
        if not a:
            return {"ok": False, "status": "NOT_FOUND", "error": f"Local asset {asset_id!r} not found"}
        return {"ok": True, "status": "LOCAL_READY", "final_path": a.get("local_abs_path"), "sha256": a["sha256"]}

    def status(self) -> dict[str, Any]:
        cnt = len(self._scan["assets"])
        return {
            "provider_id": self.provider_id,
            "kind": self.kind,
            "status": "READY" if cnt > 0 else "EMPTY_LIBRARY",
            "can_search": True,
            "can_download": False,
            "discovered_tools": [],
            "verified_asset_count": cnt,
            "quota_remaining": None,
            "reason": (
                f"{cnt} verified local SFX asset(s) discovered across {len(self.root_dirs)} approved dir(s)."
                if cnt > 0 else
                f"0 verified local SFX assets in approved dirs ({self._scan['scanned_dirs']})."
            ),
        }


class McpProvider(SfxProvider):
    """MCP SFX adapter using real discovered server tools/schemas (never hardcodes imaginary tools or bypasses auth)."""

    def __init__(
        self,
        provider_id: str = "sfx_mcp",
        *,
        server_config: dict[str, Any] | None = None,
        tool_invoker: Any = None,
        offline: bool = False,
        intended_purpose: str = "NONCOMMERCIAL_ONLY",
    ) -> None:
        self.provider_id = sanitize_identifier(provider_id, 32)
        self.kind = "mcp"
        self.server_config = server_config or {}
        self.tool_invoker = tool_invoker
        self.offline = offline
        self.intended_purpose = intended_purpose
        self._tools: list[dict[str, Any]] = []
        self._tool_names: list[str] = []
        self._auth_ok = bool(self.server_config.get("api_key_present", False))
        self._quota_remaining: int | None = self.server_config.get("quota_remaining")
        self._discover_tools()

    def _discover_tools(self) -> None:
        if self.offline or not self.server_config.get("configured"):
            return
        if self.tool_invoker is not None:
            try:
                listed = self.tool_invoker("tools/list", {})
                self._tools = list(listed.get("tools", []))
                self._tool_names = [str(t.get("name", "")) for t in self._tools if t.get("name")]
            except Exception:
                self._tools = []
                self._tool_names = []
        elif isinstance(self.server_config.get("tools"), list):
            self._tools = list(self.server_config["tools"])
            self._tool_names = [str(t.get("name", "")) for t in self._tools if t.get("name")]

    def _find_tool(self, *keywords: str) -> str | None:
        for kw in keywords:
            for nm in self._tool_names:
                if kw in nm.lower():
                    return nm
        return None

    def capabilities(self) -> dict[str, Any]:
        has_search = self._find_tool("search", "find", "list") is not None
        has_dl = self._find_tool("download", "fetch", "get_audio") is not None and self._auth_ok
        return {
            "can_search": has_search and not self.offline,
            "can_inspect": self._find_tool("inspect", "detail", "get", "info") is not None and not self.offline,
            "can_download": has_dl and not self.offline,
            "discovered_tools": list(self._tool_names),
        }

    def search(self, query: str, filters: dict[str, Any] | None = None, limit: int = 5) -> dict[str, Any]:
        if self.offline:
            return {"ok": False, "status": "OFFLINE", "results": [], "error": "Offline mode enabled"}
        tool = self._find_tool("search", "find")
        if not tool or not self.tool_invoker:
            return {"ok": False, "status": "BLOCKED_PROVIDER_CONFIG", "results": [], "error": "No authorized MCP search tool discovered"}
        # Never transmit user video/audio; send generic sanitized sound description only
        clean_q = _sanitize_metadata_text(query, 100)
        try:
            resp = self.tool_invoker(tool, {"query": clean_q, "limit": int(limit), **(filters or {})})
        except TimeoutError:
            return {"ok": False, "status": "TIMEOUT", "results": [], "error": "MCP search timed out (bounded, no endless retry)"}
        except Exception as ex:
            return {"ok": False, "status": "ERROR", "results": [], "error": str(ex)}

        code = int(resp.get("http_status", 200))
        if code in (401, 403):
            return {"ok": False, "status": "BLOCKED_AUTH", "http_status": code, "results": [], "error": f"MCP auth error {code}"}
        if code == 429:
            self._quota_remaining = 0
            return {"ok": False, "status": "BLOCKED_QUOTA", "http_status": 429, "retry_after": resp.get("retry_after"), "results": [], "error": "MCP quota exhausted (429)"}
        return {"ok": True, "status": "OK", "results": list(resp.get("results", []))}

    def inspect(self, asset_id: str) -> dict[str, Any]:
        if self.offline:
            return {"ok": False, "status": "OFFLINE", "error": "Offline mode"}
        tool = self._find_tool("inspect", "detail", "info", "get")
        if not tool or not self.tool_invoker:
            return {"ok": False, "status": "BLOCKED_PROVIDER_CONFIG", "error": "No MCP inspect tool"}
        return dict(self.tool_invoker(tool, {"asset_id": asset_id}))

    def download(self, asset_id: str, approved_destination: str | Path) -> dict[str, Any]:
        if self.offline:
            return {"ok": False, "status": "OFFLINE", "error": "Offline mode blocks remote downloads"}
        if not self._auth_ok:
            return {"ok": False, "status": "BLOCKED_AUTH", "error": "Provider download requires verified account API key"}
        if self._quota_remaining is not None and self._quota_remaining <= 0:
            return {"ok": False, "status": "BLOCKED_QUOTA", "error": "Per-run/provider download quota exhausted"}
        tool = self._find_tool("download", "fetch", "get_audio")
        if not tool or not self.tool_invoker:
            return {"ok": False, "status": "BLOCKED_PROVIDER_CONFIG", "error": "No authorized MCP download tool discovered"}
        try:
            resp = self.tool_invoker(tool, {"asset_id": asset_id})
        except TimeoutError:
            return {"ok": False, "status": "TIMEOUT", "error": "MCP download timed out"}
        code = int(resp.get("http_status", 200))
        if code in (401, 403):
            return {"ok": False, "status": "BLOCKED_AUTH", "http_status": code, "error": f"Auth error {code}"}
        if code == 429:
            self._quota_remaining = 0
            return {"ok": False, "status": "BLOCKED_QUOTA", "http_status": 429, "retry_after": resp.get("retry_after"), "error": "Download quota exhausted (429)"}

        # If provider returns an HTTPS URL, download via hardened safe_download; never trust provider-supplied file paths
        dl_url = resp.get("download_url")
        if dl_url:
            dl = safe_download(
                dl_url,
                approved_destination,
                allowed_hosts=frozenset(self.server_config.get("allowed_hosts") or DEFAULT_ALLOWED_HTTPS_HOSTS),
                allow_local_test_http=bool(self.server_config.get("allow_local_test_http", False)),
                max_bytes=int(self.server_config.get("max_download_bytes", MAX_DOWNLOAD_BYTES_DEFAULT)),
            )
            if dl.get("ok") and self._quota_remaining is not None:
                self._quota_remaining = max(0, self._quota_remaining - 1)
            return dl
        return {"ok": False, "status": "ERROR", "error": "MCP download response missing download_url"}

    def status(self) -> dict[str, Any]:
        if self.offline:
            st, rsn = "OFFLINE", "Offline mode enabled; remote MCP calls disabled."
        elif not self.server_config.get("configured"):
            st, rsn = "BLOCKED_PROVIDER_CONFIG", "No SFX MCP server installed/configured (owner approval required before installing external MCP servers)."
        elif not self._tool_names:
            st, rsn = "BLOCKED_PROVIDER_CONFIG", "MCP server configured but tools/list returned zero usable SFX tools."
        elif self._quota_remaining is not None and self._quota_remaining <= 0:
            st, rsn = "BLOCKED_QUOTA", "Provider download quota exhausted."
        elif not self._auth_ok:
            st, rsn = "SEARCH_ONLY", "MCP search available without key, but downloads require an authorized API key (0 download quota)."
        else:
            st, rsn = "READY", f"Authorized MCP provider with {len(self._tool_names)} discovered tool(s)."
        caps = self.capabilities()
        return {
            "provider_id": self.provider_id,
            "kind": self.kind,
            "status": st,
            "can_search": caps["can_search"],
            "can_download": caps["can_download"],
            "discovered_tools": list(self._tool_names),
            "verified_asset_count": 0,
            "quota_remaining": self._quota_remaining,
            "reason": rsn,
        }


def rank_assets_for_cue(
    cue_request: dict[str, Any],
    assets: list[dict[str, Any]],
    *,
    allow_test_only: bool = False,
) -> list[dict[str, Any]]:
    """Deterministically rank verified SFX assets by semantic fit, rights preference (CC0 > CC-BY), duration & diversity."""
    want_cat = str(cue_request.get("category") or "").lower()
    want_tags = {t.lower() for t in (cue_request.get("tags") or [])}
    min_d = float(cue_request.get("min_duration_sec", 0.05))
    max_d = float(cue_request.get("max_duration_sec", 4.0))
    avoid_ids = set(cue_request.get("recently_used_asset_ids") or [])

    scored: list[tuple[float, str, dict[str, Any]]] = []
    for a in assets:
        if not allow_test_only and (a.get("test_only") or a.get("approved_purpose") in ("TEST_ONLY", "BLOCKED")):
            continue
        if a.get("approved_purpose") == "BLOCKED":
            continue
        score = 0.0
        if a.get("category") == want_cat:
            score += 10.0
        tag_hits = len(want_tags & set(a.get("tags", [])))
        score += tag_hits * 2.5
        if a.get("license_status") == "APPROVED_CC0":
            score += 2.0
        elif a.get("license_status") == "APPROVED_OWNER_LOCAL":
            score += 1.8
        elif a.get("license_status") == "APPROVED_CC_BY":
            score += 1.0
        dur = float(a.get("duration_sec", 0.0))
        if min_d <= dur <= max_d:
            score += 2.0
        else:
            score -= min(3.0, abs(dur - max_d))
        if a["asset_id"] in avoid_ids:
            score -= 2.5
        scored.append((round(score, 4), a["asset_id"], a))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [a for _, _, a in scored]


def ensure_cue_assets(
    cue_requests: list[dict[str, Any]],
    *,
    local_provider: LocalProvider | None = None,
    remote_provider: SfxProvider | None = None,
    cache_dir: str | Path,
    max_downloads: int = 3,
    max_download_bytes: int = MAX_DOWNLOAD_BYTES_DEFAULT,
    workers: int = 2,
    offline: bool = False,
    intended_purpose: str = "NONCOMMERCIAL_ONLY",
    allow_test_only: bool = False,
) -> dict[str, Any]:
    """Resolve verified licensed assets for needed cues with bounded concurrency, shared quota & single-download dedup."""
    cdir = Path(cache_dir).resolve()
    cdir.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(2, int(workers)))

    local_assets = list(local_provider._scan["assets"]) if local_provider else []
    acquired_assets: dict[str, dict[str, Any]] = {a["sha256"]: a for a in local_assets}
    acquisition_log: list[dict[str, Any]] = []
    query_plan: list[dict[str, Any]] = []
    resolved_by_cue: dict[str, dict[str, Any]] = {}
    missing_categories: list[str] = []

    quota_lock = threading.Lock()
    state = {"downloads_used": 0, "bytes_used": 0, "inflight_remote_ids": set(), "downloaded_remote": {}}

    # 1. Try local library/cache first deterministically
    unresolved: list[dict[str, Any]] = []
    for req in sorted(cue_requests, key=lambda r: r["cue_id"]):
        ranked_local = rank_assets_for_cue(req, list(acquired_assets.values()), allow_test_only=allow_test_only)
        if ranked_local and (ranked_local[0]["category"] == req.get("category") or not req.get("strict_category", True)):
            resolved_by_cue[req["cue_id"]] = {"status": "RESOLVED_LOCAL", "asset": ranked_local[0]}
            acquisition_log.append({
                "ts": _now_utc(), "cue_id": req["cue_id"], "action": "local_hit",
                "asset_id": ranked_local[0]["asset_id"], "sha256": ranked_local[0]["sha256"],
            })
        else:
            unresolved.append(req)
            query_plan.append({
                "cue_id": req["cue_id"],
                "category": req.get("category", "foley"),
                "query": req.get("query") or f"{req.get('category', 'foley')} {' '.join(req.get('tags', []))}".strip(),
                "min_duration_sec": req.get("min_duration_sec", 0.05),
                "max_duration_sec": req.get("max_duration_sec", 2.0),
                "status": "PENDING_REMOTE" if (remote_provider and not offline) else "UNRESOLVED_OFFLINE_OR_NO_PROVIDER",
            })

    # 2. Bounded concurrent remote search & deduplicated acquisition (never exceeds max_downloads / max_download_bytes)
    def _acquire_one(req: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        cid = req["cue_id"]
        if offline or remote_provider is None:
            return cid, {"status": "UNAVAILABLE", "reason": "Offline or no configured remote SFX provider"}
        q = req.get("query") or req.get("category", "whoosh")
        sres = remote_provider.search(q, filters={"category": req.get("category")}, limit=5)
        with quota_lock:
            acquisition_log.append({"ts": _now_utc(), "cue_id": cid, "action": "remote_search", "query": q, "status": sres.get("status")})
        if not sres.get("ok"):
            return cid, {"status": sres.get("status", "ERROR"), "reason": sres.get("error", "Remote search failed")}

        # Filter candidates strictly by verified license BEFORE downloading
        candidates = []
        for cand in sres.get("results", []):
            vlic = verify_license(cand, intended_purpose=intended_purpose)
            if vlic["approved"]:
                candidates.append(cand)
        if not candidates:
            return cid, {"status": "REJECTED_LICENSE", "reason": "All remote search results failed license verification"}

        # Deterministically pick first valid candidate by provider_asset_id
        candidates.sort(key=lambda c: str(c.get("provider_asset_id") or c.get("id") or ""))
        chosen = candidates[0]
        rem_id = str(chosen.get("provider_asset_id") or chosen.get("id"))

        # Deduplicate concurrent download for identical remote_id
        wait_needed = False
        with quota_lock:
            if rem_id in state["downloaded_remote"]:
                return cid, {"status": "RESOLVED_REMOTE_DEDUP", "asset": state["downloaded_remote"][rem_id]}
            if rem_id in state["inflight_remote_ids"]:
                wait_needed = True
            else:
                if state["downloads_used"] >= max_downloads:
                    return cid, {"status": "BLOCKED_QUOTA", "reason": f"Per-run max_downloads={max_downloads} reached"}
                state["inflight_remote_ids"].add(rem_id)
                state["downloads_used"] += 1

        if wait_needed:
            # Bounded wait for in-flight duplicate
            for _ in range(100):
                time.sleep(0.02)
                with quota_lock:
                    if rem_id in state["downloaded_remote"]:
                        return cid, {"status": "RESOLVED_REMOTE_DEDUP", "asset": state["downloaded_remote"][rem_id]}
                    if rem_id not in state["inflight_remote_ids"]:
                        break
            return cid, {"status": "UNAVAILABLE", "reason": "Concurrent download of identical asset did not complete"}

        try:
            dl = remote_provider.download(rem_id, cdir)
            if not dl.get("ok"):
                with quota_lock:
                    acquisition_log.append({"ts": _now_utc(), "cue_id": cid, "action": "remote_download_fail", "remote_id": rem_id, "status": dl.get("status"), "error": dl.get("error")})
                return cid, {"status": dl.get("status", "DOWNLOAD_FAILED"), "reason": dl.get("error", "Download failed")}

            with quota_lock:
                state["bytes_used"] += int(dl.get("byte_size", 0))
                if state["bytes_used"] > max_download_bytes:
                    Path(dl["final_path"]).unlink(missing_ok=True)
                    return cid, {"status": "BLOCKED_BYTE_BUDGET", "reason": f"Exceeded per-run max_download_bytes={max_download_bytes}"}

            norm = normalize_provider_result(
                chosen,
                provider=remote_provider.provider_id,
                probe_info=dl["probe"],
                onset_info=dl["onset"],
                relative_cache_path=dl["relative_cache_path"],
                intended_purpose=intended_purpose,
            )
            norm["local_abs_path"] = dl["final_path"]
            with quota_lock:
                acquired_assets[norm["sha256"]] = norm
                state["downloaded_remote"][rem_id] = norm
                acquisition_log.append({
                    "ts": _now_utc(), "cue_id": cid, "action": "remote_download_ok",
                    "remote_id": rem_id, "asset_id": norm["asset_id"], "sha256": norm["sha256"],
                })
            return cid, {"status": "RESOLVED_REMOTE", "asset": norm}
        finally:
            with quota_lock:
                state["inflight_remote_ids"].discard(rem_id)

    if unresolved:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_acquire_one, req) for req in unresolved]
            for fut in futs:
                cid, res = fut.result()
                resolved_by_cue[cid] = res

    for req in cue_requests:
        r = resolved_by_cue.get(req["cue_id"], {})
        if not r.get("asset"):
            cat = str(req.get("category", "foley"))
            if cat not in missing_categories:
                missing_categories.append(cat)

    # Sort assets deterministically regardless of thread completion order
    final_assets = sorted(acquired_assets.values(), key=lambda a: (a["sha256"], a["asset_id"]))
    return {
        "resolved_by_cue": resolved_by_cue,
        "assets": final_assets,
        "missing_categories": sorted(missing_categories),
        "acquisition_log": acquisition_log,
        "query_plan": query_plan,
        "downloads_used": state["downloads_used"],
        "bytes_used": state["bytes_used"],
    }


def build_upstream_ledger(
    assets: list[dict[str, Any]],
    *,
    production_mode: bool = True,
) -> list[dict[str, Any]]:
    """Build exact upstream SeeCut sfx_ledger.py-compatible entries strictly from verified licensed assets."""
    assert_not_test_only_in_production(assets, production_mode=production_mode)
    entries: list[dict[str, Any]] = []
    for a in sorted(assets, key=lambda x: x["asset_id"]):
        if a.get("approved_purpose") == "BLOCKED":
            continue
        entries.append({
            "file": a.get("relative_cache_path") or f"{a['asset_id']}.wav",
            "duration": round(float(a["duration_sec"]), 3),
            "trim_start": round(float(a.get("onset_sec", 0.0)), 3),
            "first_hit_len": round(float(a.get("first_hit_len_sec", a["duration_sec"])), 3),
            "hits": 1,
            "category": a["category"],
            "koubo_use": f"verified_{a['category']}_{a['license_status']}",
            "desc": f"{a['provider']}:{a['provider_asset_id']} ({a['license_name']}, sha256={a['sha256'][:12]})",
            "asset_id": a["asset_id"],
            "sha256": a["sha256"],
            "peak_db": a["peak_db"],
            "license_status": a["license_status"],
        })
    return entries


def audit_cache_integrity(
    cache_dir: str | Path,
    asset_index: dict[str, Any],
) -> dict[str, Any]:
    """Re-verify SHA-256 and file presence for every cached SFX asset; fail closed on any hash drift."""
    cdir = Path(cache_dir).resolve()
    drifted: list[dict[str, str]] = []
    missing: list[str] = []
    verified_count = 0

    for a in asset_index.get("assets", []):
        rel = a.get("relative_cache_path")
        p = Path(a["local_abs_path"]) if a.get("local_abs_path") else (cdir / rel if rel else None)
        if not p or not p.is_file():
            missing.append(a["asset_id"])
            continue
        actual = hash_artifact(p)
        if actual != a["sha256"]:
            drifted.append({"asset_id": a["asset_id"], "expected": a["sha256"], "actual": actual})
            continue
        verified_count += 1

    ok = (len(drifted) == 0 and len(missing) == 0)
    return {
        "pass": ok,
        "status": "CACHE_INTEGRITY_PASS" if ok else "CACHE_DRIFT_OR_MISSING",
        "verified_count": verified_count,
        "drifted": drifted,
        "missing": missing,
    }


def load_sfx_provider_config(
    *,
    cli_library_dirs: list[str | Path] | None = None,
    brief_library_dirs: list[str | Path] | None = None,
    config_path: str | Path | None = None,
    provider_mode: str = "auto",
    sfx_policy: str = "OPTIONAL_LICENSED",
    offline: bool = False,
    intended_purpose: str = "NONCOMMERCIAL_ONLY",
    tool_invoker: Any = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve real local & MCP SFX providers from CLI flags, brief, approved config, and JIANJI_SFX_DIR (no blind HOME scan)."""
    env_map = env if env is not None else dict(os.environ)
    if sfx_policy == "OFF" or provider_mode == "off":
        local_p = LocalProvider([], intended_purpose=intended_purpose)
        mcp_p = McpProvider("sfx_mcp", server_config={"configured": False}, offline=True, intended_purpose=intended_purpose)
        return {
            "local_provider": local_p,
            "remote_provider": None,
            "mcp_provider": mcp_p,
            "library_dirs": [],
            "provider_statuses": [local_p.status(), mcp_p.status()],
            "overall_status": "OFF_BY_BRIEF",
            "next_action": "SFX disabled by brief/CLI policy (sfx_policy=OFF or sfx_provider=off).",
            "config_loaded": None,
        }

    cfg_data: dict[str, Any] = {}
    cfg_loaded_path: str | None = None
    if config_path is not None:
        cp = Path(config_path).expanduser().resolve()
        if not cp.is_file():
            raise FileNotFoundError(f"Approved SFX config file not found: {cp}")
        cfg_data = json.loads(cp.read_text(encoding="utf-8"))
        if not isinstance(cfg_data, dict):
            raise ValueError("SFX config JSON must be an object")
        cfg_loaded_path = str(cp)

    # Ordered explicit library roots (CLI -> brief -> config -> JIANJI_SFX_DIR); deduplicated while keeping order
    raw_dirs: list[str] = []
    for src_list in (cli_library_dirs or [], brief_library_dirs or [], cfg_data.get("local_library_dirs") or []):
        for d in src_list:
            ds = str(d).strip()
            if ds and ds not in raw_dirs:
                raw_dirs.append(ds)
    jianji_dir = str(env_map.get("JIANJI_SFX_DIR") or "").strip()
    if jianji_dir and jianji_dir not in raw_dirs:
        raw_dirs.append(jianji_dir)

    local_p = LocalProvider(raw_dirs, intended_purpose=intended_purpose)

    # MCP config ONLY from explicit approved config JSON (never auto-installs or fabricates endpoints)
    mcp_cfg_in = dict(cfg_data.get("mcp") or {})
    api_key_env = str(mcp_cfg_in.get("api_key_env") or "").strip()
    api_key_present = bool(mcp_cfg_in.get("api_key_present", False))
    if api_key_env and env_map.get(api_key_env):
        api_key_present = True
    # Strip any accidental raw secret fields before passing down
    safe_mcp_cfg = {
        k: v for k, v in mcp_cfg_in.items()
        if not any(tok in k.lower() for tok in ("secret", "token", "password", "raw_key", "api_key_value"))
    }
    safe_mcp_cfg["api_key_present"] = api_key_present
    # Configured only when explicitly enabled in config AND (tool_invoker supplied or verified tools list)
    want_mcp = provider_mode in ("mcp", "auto") and not offline
    if not want_mcp:
        safe_mcp_cfg["configured"] = False
    mcp_p = McpProvider(
        str(safe_mcp_cfg.get("provider_id") or "sfx_mcp"),
        server_config=safe_mcp_cfg,
        tool_invoker=tool_invoker,
        offline=(offline or not want_mcp),
        intended_purpose=intended_purpose,
    )

    l_st = local_p.status()
    m_st = mcp_p.status()
    remote_p: SfxProvider | None = mcp_p if (want_mcp and m_st["status"] in ("READY", "SEARCH_ONLY")) else None

    if l_st["verified_asset_count"] > 0:
        overall = "LOCAL_READY"
        next_act = f"{l_st['verified_asset_count']} verified local licensed SFX asset(s) ready for cue acquisition."
    elif remote_p is not None and m_st["status"] == "READY":
        overall = "MCP_LIVE_READY"
        next_act = "Authorized MCP SFX provider ready for licensed cue acquisition."
    elif offline:
        overall = "OFFLINE_CACHE_ONLY"
        next_act = "Offline mode active with 0 verified local SFX assets; supply --sfx-library with licensed audio."
    else:
        overall = "BLOCKED_PROVIDER_CONFIG"
        next_act = (
            "Supply a licensed local SFX directory via --sfx-library or JIANJI_SFX_DIR (with SFX_LICENSE_MANIFEST.json), "
            "or pass an approved --sfx-config pointing to an authorized MCP SFX server."
        )

    return {
        "local_provider": local_p,
        "remote_provider": remote_p,
        "mcp_provider": mcp_p,
        "library_dirs": raw_dirs,
        "provider_statuses": [l_st, m_st],
        "overall_status": overall,
        "next_action": next_act,
        "config_loaded": cfg_loaded_path,
    }


def emit_sfx_asset_artifacts(
    out_dir: str | Path,
    *,
    assets: list[dict[str, Any]],
    provider_statuses: list[dict[str, Any]],
    missing_categories: list[str],
    acquisition_log: list[dict[str, Any]],
    query_plan: list[dict[str, Any]],
    cache_dir: str | Path,
    overall_status: str,
    next_action: str,
) -> dict[str, Path]:
    """Emit all mandatory R1.03 + R2 SFX data contract files atomically (incl. MISSING_AUDIO_ASSETS & CACHE_INTEGRITY)."""
    base = Path(out_dir)
    base.mkdir(parents=True, exist_ok=True)
    now = _now_utc()

    # Strip runtime-only local_abs_path from schema-validated index
    clean_assets = []
    for a in assets:
        ca = {k: v for k, v in a.items() if k not in ("local_abs_path", "alternate_sources")}
        clean_assets.append(ca)

    index_doc = {
        "schema_version": "kh02r1.sfx_asset_index.v1",
        "generated_at_utc": now,
        "assets": clean_assets,
    }
    errs_idx = validate_schema(index_doc, "sfx_asset")
    if errs_idx:
        raise ValueError(f"SFX_ASSET_INDEX failed schema validation: {errs_idx[:3]}")

    status_doc = {
        "schema_version": "kh02r1.sfx_provider_status.v1",
        "checked_at_utc": now,
        "overall_status": overall_status,
        "providers": provider_statuses,
        "missing_categories": list(missing_categories),
        "next_action": next_action,
    }
    errs_st = validate_schema(status_doc, "sfx_provider_status")
    if errs_st:
        raise ValueError(f"SFX_PROVIDER_STATUS failed schema validation: {errs_st[:3]}")

    def _atomic_write_text(path: Path, text: str) -> Path:
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        return path

    paths: dict[str, Path] = {}
    paths["asset_index"] = _atomic_write_text(base / "SFX_ASSET_INDEX.json", json.dumps(index_doc, ensure_ascii=False, indent=2) + "\n")
    paths["provider_status"] = _atomic_write_text(base / "SFX_PROVIDER_STATUS.json", json.dumps(status_doc, ensure_ascii=False, indent=2) + "\n")
    paths["query_plan"] = _atomic_write_text(base / "SFX_QUERY_PLAN.json", json.dumps({"schema_version": "kh02r1.sfx_query_plan.v1", "queries": query_plan}, ensure_ascii=False, indent=2) + "\n")
    paths["cache_manifest"] = _atomic_write_text(
        base / "SFX_CACHE_MANIFEST.json",
        json.dumps({
            "schema_version": "kh02r1.sfx_cache_manifest.v1",
            "cache_dir_name": Path(cache_dir).name,
            "asset_count": len(clean_assets),
            "total_bytes": sum(int(a["byte_size"]) for a in clean_assets),
            "hashes": {a["asset_id"]: a["sha256"] for a in clean_assets},
        }, ensure_ascii=False, indent=2) + "\n",
    )

    # SFX_ACQUISITION_LOG.jsonl
    jsonl_body = "\n".join(json.dumps(row, ensure_ascii=False) for row in acquisition_log) + ("\n" if acquisition_log else "")
    paths["acquisition_log"] = _atomic_write_text(base / "SFX_ACQUISITION_LOG.jsonl", jsonl_body)

    # SFX_RIGHTS_LEDGER.csv
    csv_path = base / "SFX_RIGHTS_LEDGER.csv"
    tmp_csv = csv_path.with_suffix(".csv.part")
    cols = [
        "asset_id", "provider", "provider_asset_id", "source_url", "license_name",
        "license_version", "license_url", "license_evidence", "provider_terms_note",
        "author", "attribution_required", "attribution_text", "approved_purpose",
        "license_status", "relative_cache_path", "sha256", "byte_size", "codec",
        "sample_rate", "channels", "duration_sec", "onset_sec", "peak_db", "category", "test_only",
    ]
    with tmp_csv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for a in clean_assets:
            w.writerow({c: a.get(c, "") for c in cols})
    tmp_csv.replace(csv_path)
    paths["rights_ledger_csv"] = csv_path

    # R2 additions: MISSING_AUDIO_ASSETS.json & CACHE_INTEGRITY.json
    paths["missing_audio_assets"] = _atomic_write_text(
        base / "MISSING_AUDIO_ASSETS.json",
        json.dumps({
            "schema_version": "kh02r2.missing_audio_assets.v1",
            "checked_at_utc": now,
            "overall_status": overall_status,
            "missing_categories": list(missing_categories),
            "unresolved_queries": [q for q in query_plan if q.get("status") != "RESOLVED_LOCAL"],
            "next_action": next_action,
        }, ensure_ascii=False, indent=2) + "\n",
    )
    cache_check = audit_cache_integrity(cache_dir, {"assets": assets})
    paths["cache_integrity"] = _atomic_write_text(
        base / "CACHE_INTEGRITY.json",
        json.dumps({
            "schema_version": "kh02r2.cache_integrity.v1",
            "checked_at_utc": now,
            **cache_check,
        }, ensure_ascii=False, indent=2) + "\n",
    )
    return paths

