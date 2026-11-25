"""KH-03 Intelligent Footage Acquisition Engine (Shared Across All Five Universal Skills).

Plans visual evidence needs per beat, enforces strict per-asset rights & provider terms
separation, defends against SSRF/redirects/symlinks/decompression bombs/prompt injection,
runs bounded concurrent acquisition & content-hash caching, and fails honestly to
FOOTAGE_PARTIAL / A_ROLL_AND_GRAPHICS_ONLY when lawful external footage is absent.
"""

from __future__ import annotations

import csv
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image

from scripts.kh02.source_analysis import probe_source

from .contracts import (
    assert_kh03_schema,
    hash_artifact,
    now_utc,
    parse_json_strict,
    sanitize_identifier,
    sanitize_untrusted_text,
    write_json_atomic,
)

IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".webm"})
MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb ceiling (~40 MP)
DEFAULT_ALLOWED_FOOTAGE_HOSTS = frozenset({
    "upload.wikimedia.org",
    "commons.wikimedia.org",
    "images.pexels.com",
    "videos.pexels.com",
    "public-files.gumroad.com",
})


def is_safe_remote_url(
    url: str,
    *,
    allowed_hosts: frozenset[str] = DEFAULT_ALLOWED_FOOTAGE_HOSTS,
) -> tuple[bool, str]:
    """Enforce HTTPS-only, strict host allowlist, and SSRF protection against loopback/private/metadata IPs."""
    raw = str(url or "").strip()
    if not raw or any(ch in raw for ch in ("\r", "\n", "\x00")):
        return False, "Malformed or control-character URL rejected"
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme.lower() != "https":
        return False, f"Non-HTTPS scheme rejected: {parsed.scheme!r}"
    host = (parsed.hostname or "").lower().strip(".")
    if not host:
        return False, "Missing hostname"
    if host in ("localhost", "ip6-localhost", "metadata.google.internal", "169.254.169.254"):
        return False, f"Blocked loopback/metadata host: {host!r}"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return False, f"Blocked private/loopback IP literal: {host!r}"
    except ValueError:
        pass
    if allowed_hosts and host not in allowed_hosts:
        return False, f"Host {host!r} not in approved HTTPS allowlist"
    try:
        infos = socket.getaddrinfo(host, None)
        for _, _, _, _, sockaddr in infos:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                return False, f"Host {host!r} resolved to private/loopback IP {ip}"
    except OSError:
        return False, f"Host {host!r} unresolvable (cannot verify non-private IP)"
    return True, "OK"


def validate_asset_rights(
    metadata: dict[str, Any],
    *,
    intended_purpose: str = "NONCOMMERCIAL_ONLY",
) -> dict[str, Any]:
    """Validate per-asset rights & provider API terms separately (fail-closed on unknown/NC/ND/watermarked/competitor)."""
    raw_lic = sanitize_untrusted_text(metadata.get("license_name"), max_len=120)
    lic_up = raw_lic.upper()
    evidence = sanitize_untrusted_text(metadata.get("license_evidence") or metadata.get("license_url"), max_len=300)
    provider_terms = sanitize_untrusted_text(metadata.get("provider_terms"), max_len=200).upper()
    author = sanitize_untrusted_text(metadata.get("author"), max_len=120)
    attribution = sanitize_untrusted_text(metadata.get("attribution_text"), max_len=240)
    source_class = sanitize_untrusted_text(metadata.get("source_class"), max_len=80).upper()

    if metadata.get("has_watermark") is True:
        return {"approved": False, "license_status": "REJECTED_WATERMARKED", "attribution_required": False, "reason": "Watermarked stock preview rejected."}
    if source_class in ("COMPETITOR_RAW", "COMPETITOR_CLIP", "SCRAPED_SOCIAL"):
        return {"approved": False, "license_status": "REJECTED_COMPETITOR_CONTENT", "attribution_required": False, "reason": "Competitor footage strictly forbidden."}
    if metadata.get("generated_media") is True and not evidence:
        return {"approved": False, "license_status": "REJECTED_UNVERIFIED_GENERATED_RIGHTS", "attribution_required": False, "reason": "Generated media requires explicit rights/provenance evidence."}

    if not raw_lic or lic_up in ("UNKNOWN", "UNSPECIFIED", "NONE", "COPYRIGHTED", "ALL RIGHTS RESERVED", "ANALYSIS_ONLY"):
        return {"approved": False, "license_status": "REJECTED_UNKNOWN_LICENSE", "attribution_required": False, "reason": f"Missing or unknown license ({raw_lic!r})."}
    if not evidence:
        return {"approved": False, "license_status": "REJECTED_MISSING_EVIDENCE", "attribution_required": False, "reason": "Missing license URL/manifest evidence."}

    tokens = set(re.split(r"[^A-Z0-9]+", lic_up))
    if "ND" in tokens or "NODERIV" in lic_up or "NO DERIV" in lic_up:
        return {"approved": False, "license_status": "REJECTED_ND_INCOMPATIBLE", "attribution_required": True, "reason": f"NoDerivatives license ({raw_lic!r}) cannot be composited."}

    is_nc = ("NC" in tokens) or ("NONCOMMERCIAL" in lic_up)
    if is_nc and intended_purpose == "COMMERCIAL_AND_NONCOMMERCIAL":
        return {"approved": False, "license_status": "REJECTED_NC_INCOMPATIBLE", "attribution_required": True, "reason": f"Noncommercial license ({raw_lic!r}) blocked for commercial use."}

    if "NONCOMMERCIAL_API_ONLY" in provider_terms and intended_purpose == "COMMERCIAL_AND_NONCOMMERCIAL":
        return {"approved": False, "license_status": "REJECTED_PROVIDER_TERMS", "attribution_required": False, "reason": "Provider API terms forbid commercial use even if asset is CC0."}

    if any(k in lic_up for k in ("CC0", "CC-0", "PUBLIC DOMAIN", "UNLICENSE", "MIT")):
        return {
            "approved": True,
            "license_status": "APPROVED_CC0_OR_PD",
            "attribution_required": False,
            "attribution_text": attribution,
            "reason": f"Verified CC0/Public-Domain/permissive asset ({evidence[:80]}).",
        }
    if lic_up in ("OWNER_SUPPLIED_NONCOMMERCIAL", "OWNER_OWNED_LICENSED", "CAPTURED_REAL_EVIDENCE_MASKED", "LICENSED_COMMERCIAL", "ORIGINAL_GENERATED_VERIFIED"):
        if lic_up == "OWNER_SUPPLIED_NONCOMMERCIAL" and intended_purpose == "COMMERCIAL_AND_NONCOMMERCIAL":
            return {"approved": False, "license_status": "REJECTED_NC_INCOMPATIBLE", "attribution_required": False, "reason": "Owner asset marked noncommercial only."}
        return {
            "approved": True,
            "license_status": f"APPROVED_{lic_up}",
            "attribution_required": False,
            "attribution_text": attribution,
            "reason": f"Verified owner/authorized asset ({evidence[:80]}).",
        }
    if "CC-BY" in lic_up or "CC BY" in lic_up or "ATTRIBUTION" in lic_up:
        if not author and not attribution:
            return {"approved": False, "license_status": "REJECTED_MISSING_ATTRIBUTION", "attribution_required": True, "reason": "CC-BY asset requires explicit author or attribution text."}
        return {
            "approved": True,
            "license_status": "APPROVED_CC_BY",
            "attribution_required": True,
            "attribution_text": attribution or f"{metadata.get('title', 'Asset')} by {author} ({raw_lic})",
            "reason": f"Verified CC-BY asset with attribution ({author or attribution[:50]}).",
        }

    return {"approved": False, "license_status": "REJECTED_UNSUPPORTED_LICENSE", "attribution_required": False, "reason": f"Unrecognized license {raw_lic!r}."}


def plan_visual_evidence_needs(
    semantic_graph: dict[str, Any],
    skill_id: str,
    *,
    constraints: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Plan relevant supporting visual evidence per beat for ANY of the five skills (no irrelevant filler)."""
    c = dict(constraints or {})
    fullscreen_lock = bool(c.get("fullscreen_lock") or c.get("layout_lock") == "FULLSCREEN_9_16")
    needs: list[dict[str, Any]] = []

    for idx, ph in enumerate(semantic_graph.get("phrases", [])):
        role = ph.get("semantic_role", "context")
        txt = sanitize_untrusted_text(ph.get("text", ""), max_len=240)
        bid = f"beat_{idx + 1:02d}"
        p_nums = ph.get("numeric_claims") or []

        # Determine exact evidence kind & whether supporting footage helps comprehension
        if role == "evidence":
            ev_kind = "real_evidence"
            prio = "required"
            media_types = ["screen_capture", "image"]
            q_intent = f"verified product or real capture for: {txt[:80]}"
            v_question = "What real, unfabricated proof verifies this product or workflow claim?"
        elif p_nums:
            ev_kind = "illustration"
            prio = "optional"
            media_types = ["diagram_asset", "image"]
            q_intent = f"clean quantitative visual aid for: {txt[:80]}"
            v_question = "How do these exact numbers relate proportionally?"
        elif role in ("mechanism", "step", "comparison"):
            ev_kind = "illustration" if skill_id in ("SK-04", "SK-02") else "b_roll"
            prio = "optional"
            media_types = ["video", "image", "diagram_asset"]
            q_intent = f"clear visual demonstration of: {txt[:80]}"
            v_question = "What concrete mechanism, step, or contrast makes this immediately understandable?"
        elif role in ("context", "hook", "question") and skill_id in ("SK-01", "SK-03", "SK-02"):
            ev_kind = "b_roll"
            prio = "optional"
            media_types = ["video", "image"]
            q_intent = f"contextual supporting B-roll for: {txt[:80]}"
            v_question = "What grounded visual context reinforces this opening or narrative beat?"
        else:
            # Avoid forcing irrelevant B-roll on simple conversational transitions or pure CTA beats
            ev_kind = "real_evidence" if role == "cta" else "illustration"
            prio = "optional"
            media_types = ["image", "diagram_asset"]
            q_intent = f"supporting closing/payoff graphic for: {txt[:80]}"
            v_question = "What clean takeaway or proof closes this beat without distracting from speech?"

        needs.append({
            "need_id": f"need_{idx + 1:02d}_{bid}",
            "beat_id": bid,
            "phrase_id": ph["phrase_id"],
            "skill_id": skill_id,
            "evidence_kind": ev_kind,
            "visual_question": v_question,
            "search_intent": q_intent,
            "priority": prio,
            "accepted_media_types": media_types,
            "start_frame": int(ph["start_frame"]),
            "end_frame": int(ph["end_frame"]),
            "layout_constraint": "non_obscuring_top_or_edge_overlay" if fullscreen_lock else "adaptive_focal_panel",
            "expected_viewer_takeaway": f"Audience understands '{txt[:90]}' via truthful {ev_kind}.",
        })
    return needs


def _decode_and_validate_local_file(path: Path, *, max_bytes: int = 100 * 1024 * 1024) -> dict[str, Any]:
    """Decode-verify local image/video bytes; reject symlinks, HTML/scripts, oversize files & decompression bombs."""
    if path.is_symlink():
        return {"valid": False, "error": f"Symlink asset rejected: {path}"}
    if not path.is_file():
        return {"valid": False, "error": f"Missing file: {path}"}
    sz = path.stat().st_size
    if sz <= 0 or sz > max_bytes:
        return {"valid": False, "error": f"Invalid file size {sz} bytes (cap {max_bytes})"}
    head = path.read_bytes()[:128].lstrip()
    if head.startswith((b"<!DOCTYPE", b"<html", b"<HTML", b"<script", b"<?xml", b"#!/")):
        return {"valid": False, "error": "Malicious/non-media HTML or script payload rejected"}

    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        try:
            Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im2:
                w, h = im2.size
            if w < 16 or h < 16 or (w * h) > MAX_IMAGE_PIXELS:
                return {"valid": False, "error": f"Image dimensions {w}x{h} outside safe bounds"}
            return {"valid": True, "media_type": "image", "width": int(w), "height": int(h), "sha256": hash_artifact(path), "byte_size": sz}
        except Exception as ex:
            return {"valid": False, "error": f"Image decode failed: {ex}"}
    if ext in VIDEO_EXTS:
        try:
            pr = probe_source(path)
            m = pr["media"]
            w, h = int(m.get("width") or 0), int(m.get("height") or 0)
            if w < 16 or h < 16:
                return {"valid": False, "error": f"Invalid video geometry {w}x{h}"}
            return {"valid": True, "media_type": "video", "width": w, "height": h, "sha256": pr["sha256"], "byte_size": sz, "duration_sec": m.get("duration_sec")}
        except Exception as ex:
            return {"valid": False, "error": f"Video probe failed: {ex}"}
    return {"valid": False, "error": f"Unsupported media extension: {ext!r}"}


def query_footage_candidates(
    needs: list[dict[str, Any]],
    *,
    local_libraries: list[Path] | None = None,
    supplied_assets: list[dict[str, Any]] | None = None,
    intended_purpose: str = "NONCOMMERCIAL_ONLY",
    workers: int = 2,
    offline: bool = True,
) -> dict[str, Any]:
    """Query owner-supplied assets & bounded local libraries concurrently with strict rights + decode checks."""
    raw_items: list[dict[str, Any]] = []
    for sa in (supplied_assets or []):
        raw_items.append(dict(sa))

    # Bounded read-only scan of approved local asset libraries (requires sidecar .meta.json / manifest.json for rights!)
    for lib_dir in (local_libraries or []):
        ld = Path(lib_dir)
        if not ld.is_dir() or ld.is_symlink():
            continue
        manifest_map: dict[str, dict[str, Any]] = {}
        mf = ld / "manifest.json"
        if mf.is_file() and not mf.is_symlink():
            try:
                mdata = parse_json_strict(mf)
                for entry in mdata.get("assets", []):
                    if isinstance(entry, dict) and entry.get("filename"):
                        manifest_map[str(entry["filename"])] = entry
            except Exception:
                pass
        scanned = 0
        for p in sorted(ld.iterdir()):
            if scanned >= 200:
                break
            if p.suffix.lower() not in (IMAGE_EXTS | VIDEO_EXTS):
                continue
            scanned += 1
            meta = dict(manifest_map.get(p.name, {}))
            meta.setdefault("local_path", str(p))
            meta.setdefault("asset_id", sanitize_identifier(p.stem))
            meta.setdefault("title", sanitize_untrusted_text(p.stem, max_len=80))
            meta.setdefault("source_label", f"local:{p.name}")
            raw_items.append(meta)

    def _inspect_one(item: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        # Validate remote URL safety if present (never fetches unless online & allowlisted)
        if item.get("remote_url"):
            ok_u, msg_u = is_safe_remote_url(str(item["remote_url"]))
            if not ok_u:
                return None, {"candidate": sanitize_untrusted_text(item.get("asset_id") or item.get("remote_url"), max_len=120), "reason": f"SSRF_OR_URL_REJECTED: {msg_u}"}

        rights = validate_asset_rights(item, intended_purpose=intended_purpose)
        if not rights["approved"]:
            return None, {
                "candidate": sanitize_untrusted_text(item.get("asset_id") or item.get("local_path") or "unknown", max_len=120),
                "license_status": rights["license_status"],
                "reason": rights["reason"],
            }

        lp = Path(str(item.get("local_path") or ""))
        if not lp or not str(lp):
            return None, {"candidate": str(item.get("asset_id", "unknown")), "reason": "Missing verified local bytes (remote links without verified bytes rejected)."}
        dec = _decode_and_validate_local_file(lp)
        if not dec["valid"]:
            return None, {"candidate": str(lp.name), "reason": dec["error"]}

        cand = {
            "asset_id": sanitize_identifier(str(item.get("asset_id") or lp.stem)),
            "local_path": str(lp.resolve()),
            "sha256": dec["sha256"],
            "media_type": str(item.get("media_type") or dec["media_type"]),
            "evidence_kind": str(item.get("evidence_kind") or ("real_evidence" if dec["media_type"] == "image" else "b_roll")),
            "license_status": rights["license_status"],
            "license_name": sanitize_untrusted_text(item.get("license_name"), max_len=80),
            "provider_terms": sanitize_untrusted_text(item.get("provider_terms") or "LOCAL_OR_OWNER_SUPPLIED", max_len=120),
            "source_label": sanitize_untrusted_text(item.get("source_label") or lp.name, max_len=120),
            "attribution_required": bool(rights["attribution_required"]),
            "attribution_text": str(rights.get("attribution_text") or ""),
            "usable_in_render": True,
            "width": dec["width"],
            "height": dec["height"],
            "tags": [sanitize_untrusted_text(t, max_len=40).lower() for t in (item.get("tags") or [])],
            "title": sanitize_untrusted_text(item.get("title") or lp.stem, max_len=100),
            "rights_identity_hash": hash_artifact({"sha256": dec["sha256"], "license": rights["license_status"], "purpose": intended_purpose}),
        }
        return cand, None

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    max_w = max(1, min(4, int(workers)))
    with ThreadPoolExecutor(max_workers=max_w) as ex:
        results = list(ex.map(_inspect_one, raw_items))
    for ok_c, rej_c in results:
        if ok_c is not None:
            accepted.append(ok_c)
        if rej_c is not None:
            rejected.append(rej_c)

    # Deduplicate by (sha256, license_status) with deterministic sort
    accepted.sort(key=lambda a: (a["sha256"], a["asset_id"]))
    deduped: list[dict[str, Any]] = []
    seen_sha: set[str] = set()
    for a in accepted:
        if a["sha256"] in seen_sha:
            continue
        seen_sha.add(a["sha256"])
        deduped.append(a)

    return {
        "offline": offline,
        "candidates": deduped,
        "rejected": sorted(rejected, key=lambda r: (r.get("candidate", ""), r.get("reason", ""))),
    }


def rank_footage(need: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic relevance + rights + quality ranking for one beat's evidence need."""
    q_toks = set(re.findall(r"[a-z0-9]{3,}", need.get("search_intent", "").lower()))
    ranked: list[tuple[float, str, str, dict[str, Any]]] = []
    for c in candidates:
        c_toks = set(re.findall(r"[a-z0-9]{3,}", f"{c.get('title', '')} {' '.join(c.get('tags', []))} {c.get('asset_id', '')}".lower()))
        overlap = len(q_toks & c_toks)
        type_match = 20.0 if c["media_type"] in need.get("accepted_media_types", []) else 0.0
        kind_match = 20.0 if c["evidence_kind"] == need.get("evidence_kind") else 8.0
        res_score = 15.0 if (c["width"] >= 480 and c["height"] >= 320) else 6.0
        cc0_bonus = 5.0 if c["license_status"] in ("APPROVED_CC0_OR_PD", "APPROVED_OWNER_OWNED_LICENSED", "APPROVED_OWNER_SUPPLIED_NONCOMMERCIAL") else 2.0
        score = round(min(100.0, 20.0 + overlap * 12.0 + type_match + kind_match + res_score + cc0_bonus), 2)
        ranked.append((score, c["sha256"], c["asset_id"], {**c, "fit_score": score}))
    ranked.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [item[3] for item in ranked]


def safe_download_footage(
    url: str,
    dest_path: Path,
    *,
    allowed_hosts: frozenset[str] = DEFAULT_ALLOWED_FOOTAGE_HOSTS,
    max_bytes: int = 25 * 1024 * 1024,
    timeout_sec: float = 15.0,
    max_redirects: int = 3,
) -> dict[str, Any]:
    """Hardened HTTPS downloader blocking SSRF, unsafe redirects, symlinks, oversize & non-media payloads."""
    ok, msg = is_safe_remote_url(url, allowed_hosts=allowed_hosts)
    if not ok:
        return {"ok": False, "error": f"BLOCKED_URL: {msg}"}

    class _SafeRedirect(urllib.request.HTTPRedirectHandler):
        def __init__(self) -> None:
            self._hops = 0

        def redirect_request(self, req, fp, code, msg_r, headers, newurl):
            self._hops += 1
            if self._hops > max_redirects:
                raise urllib.error.HTTPError(newurl, code, "Too many redirects", headers, fp)
            ok_r, reason_r = is_safe_remote_url(newurl, allowed_hosts=allowed_hosts)
            if not ok_r:
                raise urllib.error.URLError(f"Unsafe redirect blocked: {reason_r}")
            return super().redirect_request(req, fp, code, msg_r, headers, newurl)

    opener = urllib.request.build_opener(_SafeRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": "Seecut-AI-KH03/1.0"})
    tmp = dest_path.with_suffix(dest_path.suffix + ".part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with opener.open(req, timeout=timeout_sec) as resp, tmp.open("wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"Download exceeded max_bytes={max_bytes}")
                fh.write(chunk)
        dec = _decode_and_validate_local_file(tmp, max_bytes=max_bytes)
        if not dec["valid"]:
            tmp.unlink(missing_ok=True)
            return {"ok": False, "error": f"INVALID_DOWNLOADED_MEDIA: {dec['error']}"}
        tmp.replace(dest_path)
        return {"ok": True, "path": str(dest_path), **dec}
    except Exception as ex:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": str(ex)}


def acquire_and_bind_footage(
    *,
    run_id: str,
    run_dir: Path,
    semantic_graph: dict[str, Any],
    skill_id: str,
    constraints: dict[str, Any] | None = None,
    local_libraries: list[Path] | None = None,
    supplied_assets: list[dict[str, Any]] | None = None,
    intended_purpose: str = "NONCOMMERCIAL_ONLY",
    workers: int = 2,
    offline: bool = True,
) -> dict[str, Any]:
    """End-to-end KH-03 footage planning, rights validation, caching, beat binding & artifact emission."""
    needs = plan_visual_evidence_needs(semantic_graph, skill_id, constraints=constraints)
    q_res = query_footage_candidates(
        needs,
        local_libraries=local_libraries,
        supplied_assets=supplied_assets,
        intended_purpose=intended_purpose,
        workers=workers,
        offline=offline,
    )
    candidates = q_res["candidates"]
    rejected = q_res["rejected"]

    bindings: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    used_asset_ids: set[str] = set()

    for need in needs:
        ranked = rank_footage(need, candidates)
        # Prefer unused candidate when close in score to prevent repetitive static reuse
        chosen = None
        if ranked:
            unused = [c for c in ranked if c["asset_id"] not in used_asset_ids]
            chosen = unused[0] if (unused and unused[0]["fit_score"] >= ranked[0]["fit_score"] - 10.0) else ranked[0]

        if chosen is not None and chosen["fit_score"] >= 40.0:
            used_asset_ids.add(chosen["asset_id"])
            bindings.append({
                "beat_id": need["beat_id"],
                "asset_id": chosen["asset_id"],
                "in_frame": need["start_frame"],
                "out_frame": need["end_frame"],
                "layout_mode": need["layout_constraint"],
                "evidence_label": "REAL_EVIDENCE" if chosen["evidence_kind"] == "real_evidence" else "ILLUSTRATIVE_SUPPORT",
                "why_selected": f"Fit {chosen['fit_score']}/100 ({chosen['license_status']}) for {need['evidence_kind']}.",
                "rejected_alternatives": [c["asset_id"] for c in ranked[1:3]],
            })
        else:
            bindings.append({
                "beat_id": need["beat_id"],
                "asset_id": None,
                "in_frame": need["start_frame"],
                "out_frame": need["end_frame"],
                "layout_mode": need["layout_constraint"],
                "evidence_label": "ORIGINAL_SEMANTIC_GRAPHIC_FALLBACK",
                "why_selected": "No external rights-cleared clip matched; using original semantic diagram/motion over A-roll.",
                "rejected_alternatives": [],
            })
            gaps.append({
                "beat_id": need["beat_id"],
                "priority": need["priority"],
                "evidence_kind": need["evidence_kind"],
                "search_intent": need["search_intent"],
                "fallback": "Original semantic diagram/typography without fabricated UI or unverified stock.",
            })

    req_gaps = [g for g in gaps if g["priority"] == "required"]
    if not candidates:
        status = "FOOTAGE_BLOCKED" if req_gaps else "A_ROLL_AND_GRAPHICS_ONLY"
    elif gaps:
        status = "FOOTAGE_PARTIAL"
    else:
        status = "ASSET_READY"

    clean_assets = [
        {k: v for k, v in c.items() if k in (
            "asset_id", "local_path", "sha256", "media_type", "evidence_kind",
            "license_status", "license_name", "provider_terms", "source_label",
            "attribution_required", "attribution_text", "usable_in_render", "width", "height",
        )}
        for c in candidates
    ]

    manifest = {
        "schema_version": "kh03.footage_manifest.v1",
        "run_id": run_id,
        "selected_skill_id": skill_id,
        "intended_purpose": intended_purpose,
        "status": status,
        "assets": clean_assets,
        "beat_bindings": bindings,
        "gaps": gaps,
        "rejected_candidates": rejected,
    }
    assert_kh03_schema(manifest, "footage_manifest")

    # Emit all Section 07 footage & rights artifacts
    assets_dir = run_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(assets_dir / "FOOTAGE_QUERY_PLAN.json", {"run_id": run_id, "skill_id": skill_id, "needs": needs})
    write_json_atomic(assets_dir / "FOOTAGE_CANDIDATES.json", {"run_id": run_id, "candidates": candidates, "rejected": rejected})
    write_json_atomic(assets_dir / "FOOTAGE_MANIFEST.json", manifest)
    write_json_atomic(assets_dir / "ASSET_CACHE_MANIFEST.json", {
        "schema_version": "kh03.asset_cache.v1",
        "run_id": run_id,
        "entries": [{"asset_id": c["asset_id"], "sha256": c["sha256"], "rights_identity_hash": c["rights_identity_hash"]} for c in candidates],
    })

    # Append acquisition JSONL log
    log_p = assets_dir / "FOOTAGE_ACQUISITION_LOG.jsonl"
    log_lines = [
        json.dumps({"ts": now_utc(), "event": "asset_verified", "asset_id": c["asset_id"], "sha256": c["sha256"], "license": c["license_status"]})
        for c in candidates
    ] + [
        json.dumps({"ts": now_utc(), "event": "candidate_rejected", **r})
        for r in rejected
    ]
    log_p.write_text("\n".join(log_lines) + ("\n" if log_lines else ""), encoding="utf-8")

    # RIGHTS_LEDGER.csv
    csv_p = run_dir / "evidence" / "RIGHTS_LEDGER.csv"
    csv_p.parent.mkdir(parents=True, exist_ok=True)
    with csv_p.open("w", newline="", encoding="utf-8") as cf:
        w = csv.DictWriter(cf, fieldnames=[
            "asset_id", "source_label", "media_type", "evidence_kind", "license_name",
            "license_status", "provider_terms", "attribution_required", "attribution_text", "sha256",
        ])
        w.writeheader()
        for c in clean_assets:
            w.writerow({k: c.get(k, "") for k in w.fieldnames})

    # FOOTAGE_GAPS.md & EVIDENCE_LEDGER.md
    gap_lines = [f"# Footage Gaps — `{run_id}` (`{skill_id}`)", "", f"- **Status:** `{status}`", ""]
    if gaps:
        for g in gaps:
            gap_lines.append(f"- `{g['beat_id']}` (`{g['priority']}`, `{g['evidence_kind']}`): {g['search_intent']} → *{g['fallback']}*")
    else:
        gap_lines.append("- None (all visual evidence needs matched with verified assets).")
    (assets_dir / "FOOTAGE_GAPS.md").write_text("\n".join(gap_lines) + "\n", encoding="utf-8")

    ev_lines = [f"# Visual Evidence Ledger — `{run_id}` (`{skill_id}`)", "", "| Beat | Asset ID | Label | Rationale |", "|---|---|---|---|"]
    for b in bindings:
        ev_lines.append(f"| `{b['beat_id']}` | `{b['asset_id'] or 'NONE'}` | `{b['evidence_label']}` | {b['why_selected']} |")
    (run_dir / "evidence" / "EVIDENCE_LEDGER.md").write_text("\n".join(ev_lines) + "\n", encoding="utf-8")

    return manifest
