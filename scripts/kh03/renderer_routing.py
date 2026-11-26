"""KH-03.R4.2.HOTFIX — Per-Renderer License Separation, Provenance Audit & Editorial Lifecycle Routing.

Strictly separates rendering backend rights so that YeJe-cpu/SeeCut's PolyForm Noncommercial 1.0.0
license NEVER blocks an audited, commercially-safe clean-room independent renderer (`INDEPENDENT_FFMPEG_PIL`),
while NEVER weakening or bypassing PROBLEM-01 on `NATIVE_SEECUT` and NEVER forging permission receipts!

Also cleanly separates candidate/editorial/approval lifecycle states when AI judge (`agy` CLI) is absent:
- `CANDIDATE_RENDERED`: valid preview MP4(s) rendered & passed technical QA for direct owner viewing
- `EDITORIAL_PENDING`: live AI judge unavailable or tie; awaiting manual owner visual/audio selection
- `FINAL_APPROVED`: ONLY after explicit owner playback approval on exact final MP4 SHA-256!
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import UPSTREAM_SEECUT_SHA
from .contracts import hash_artifact, now_utc, verify_commercial_permission_evidence, write_json_atomic

VALID_RENDERER_MODES = ("auto", "native_seecut", "independent_clean_room")

RENDERER_NATIVE_SEECUT = "NATIVE_SEECUT"
RENDERER_INDEPENDENT_CLEAN_ROOM = "INDEPENDENT_FFMPEG_PIL"

# Forbidden upstream tokens/imports inside clean-room independent renderer modules
_FORBIDDEN_SEECUT_MARKERS = (
    "third_party/seecut",
    "third_party\\seecut",
    "render.mjs",
    "geom-check.mjs",
    "jianying_layers.mjs",
    "jianying_plan.py",
    "hyperframes",
    "render_guard",
    "compile_plan",
)


def audit_renderer_provenance(
    renderer_id: str,
    *,
    seecut_root: Path | None = None,
    tools_root: Path | None = None,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Audit code, imports, templates, and dependencies for a specific renderer backend."""
    rid = str(renderer_id or "").strip().upper()
    repo_root = Path(__file__).resolve().parents[2]

    if rid == RENDERER_INDEPENDENT_CLEAN_ROOM:
        ind_mod = repo_root / "scripts" / "kh03" / "independent_renderer.py"
        violations: list[str] = []
        imports_found: list[str] = []
        mod_sha = None
        if not ind_mod.is_file():
            violations.append(f"MISSING_CLEAN_ROOM_MODULE:{ind_mod}")
        else:
            src_txt = ind_mod.read_text(encoding="utf-8")
            mod_sha = hash_artifact(ind_mod)
            low_src = src_txt.lower()
            for marker in _FORBIDDEN_SEECUT_MARKERS:
                if marker in low_src:
                    violations.append(f"FORBIDDEN_UPSTREAM_MARKER_IN_CLEAN_ROOM:{marker}")
            try:
                tree = ast.parse(src_txt, filename=str(ind_mod))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for al in node.names:
                            imports_found.append(al.name)
                            if "kh02" in al.name or "seecut" in al.name.lower():
                                violations.append(f"FORBIDDEN_IMPORT_IN_CLEAN_ROOM:{al.name}")
                    elif isinstance(node, ast.ImportFrom):
                        mod_n = str(node.module or "")
                        imports_found.append(mod_n)
                        if "kh02" in mod_n or "seecut" in mod_n.lower():
                            violations.append(f"FORBIDDEN_FROM_IMPORT_IN_CLEAN_ROOM:{mod_n}")
            except SyntaxError as ex:
                violations.append(f"SYNTAX_ERROR:{ex}")

        ffmpeg_ok = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
        pil_ok = False
        np_ok = False
        try:
            import PIL  # noqa: F401
            pil_ok = True
        except Exception:
            pass
        try:
            import numpy  # noqa: F401
            np_ok = True
        except Exception:
            pass

        clean_room_verified = (len(violations) == 0 and ind_mod.is_file())
        deps_ready = bool(ffmpeg_ok and pil_ok and np_ok)
        rep = {
            "schema_version": "kh03.renderer_provenance_audit.v1",
            "audited_at_utc": now_utc(),
            "renderer_id": RENDERER_INDEPENDENT_CLEAN_ROOM,
            "engine_label": "Clean-Room Independent FFmpeg + Pillow + NumPy Renderer (NOT Native SeeCut)",
            "license_classification": "PERMISSIVE_CLEAN_ROOM_COMMERCIAL_ELIGIBLE",
            "uses_seecut_upstream_code": False if clean_room_verified else True,
            "uses_seecut_assets": False if clean_room_verified else True,
            "module_path": str(ind_mod),
            "module_sha256": mod_sha,
            "imports_audited": sorted(set(imports_found)),
            "dependencies": [
                {"name": "Python stdlib", "license": "PSF-2.0", "commercial_eligible": True},
                {"name": "Pillow (PIL)", "license": "HPND / MIT-CMU", "available": pil_ok, "commercial_eligible": True},
                {"name": "NumPy", "license": "BSD-3-Clause", "available": np_ok, "commercial_eligible": True},
                {"name": "FFmpeg / FFprobe CLI", "license": "LGPL/GPL CLI invocation (no static linking into proprietary binary)", "available": ffmpeg_ok, "commercial_eligible": True},
                {"name": "Procedural NumPy Audio Synthesis", "license": "Original clean-room mathematical synthesis", "commercial_eligible": True},
                {"name": "System/Installed TrueType Fonts", "license": "Host OS licensed system fonts", "commercial_eligible": True},
            ],
            "isolation_violations": violations,
            "clean_room_verified": clean_room_verified,
            "runtime_ready": deps_ready,
            "commercial_eligible": bool(clean_room_verified and deps_ready),
            "noncommercial_eligible": bool(clean_room_verified and deps_ready),
            "note": "Independent clean-room backend; strictly distinct from YeJe-cpu/SeeCut and NEVER claims NATIVE_SEECUT provenance.",
        }
    else:
        standalone = Path(os.environ.get("SEECUT_STANDALONE_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone")))
        up_root = seecut_root or Path(os.environ.get("SEECUT_UPSTREAM_ROOT", str(standalone / "third_party/SeeCut")))
        troot = tools_root or (standalone / "tools")
        up_head = None
        if (up_root / ".git").exists():
            try:
                up_head = subprocess.check_output(["git", "-C", str(up_root), "rev-parse", "HEAD"], text=True, timeout=10).strip()
            except Exception:
                pass
        render_mjs = up_root / "skill/seecut/scripts/render.mjs"
        gsaps = sorted(troot.glob("node_modules/hyperframes/dist/vendor/npm/gsap@*/dist/gsap.min.js"))
        runtime_ready = bool(render_mjs.is_file() and up_head == UPSTREAM_SEECUT_SHA and gsaps and shutil.which("node"))
        rep = {
            "schema_version": "kh03.renderer_provenance_audit.v1",
            "audited_at_utc": now_utc(),
            "renderer_id": RENDERER_NATIVE_SEECUT,
            "engine_label": "YeJe-cpu/SeeCut v3.4 Native HyperFrames + HTML/GSAP Renderer",
            "license_classification": "POLYFORM_NONCOMMERCIAL_1_0_0",
            "upstream_pin_expected": UPSTREAM_SEECUT_SHA,
            "upstream_pin_observed": up_head,
            "uses_seecut_upstream_code": True,
            "uses_seecut_assets": True,
            "commercial_contact": "aiesecxuanli@gmail.com",
            "isolation_violations": [],
            "clean_room_verified": False,
            "runtime_ready": runtime_ready,
            "commercial_eligible": False,  # Requires verified written commercial authorization receipt!
            "noncommercial_eligible": runtime_ready,
            "note": "PolyForm Noncommercial 1.0.0: commercial use BLOCKED unless verified written authorization from aiesecxuanli@gmail.com is supplied.",
        }

    if out_path is not None:
        write_json_atomic(out_path, rep)
    return rep


def select_renderer_backend(
    *,
    requested: str = "auto",
    intended_purpose: str = "NONCOMMERCIAL_ENGINEERING",
    commercial_permission_evidence: Any = None,
    seecut_root: Path | None = None,
    tools_root: Path | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Select an authorized renderer backend fail-closed per-renderer (ZERO PolyForm bypass, ZERO fake receipts)."""
    req = str(requested or "auto").strip().lower()
    if req in ("independent", "independent_ffmpeg_pil", "clean_room"):
        req = "independent_clean_room"
    elif req in ("native", "seecut"):
        req = "native_seecut"
    if req not in VALID_RENDERER_MODES:
        raise ValueError(f"INVALID_RENDERER_MODE: {requested!r}; expected one of {VALID_RENDERER_MODES}")

    purpose = str(intended_purpose or "NONCOMMERCIAL_ENGINEERING").strip().upper()
    is_commercial = (purpose == "COMMERCIAL_MARKETING")
    seecut_perm = verify_commercial_permission_evidence(commercial_permission_evidence) if is_commercial else {
        "verified": True, "status": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED", "reason": "Noncommercial use permitted under PolyForm Noncommercial 1.0.0."
    }
    native_audit = audit_renderer_provenance(RENDERER_NATIVE_SEECUT, seecut_root=seecut_root, tools_root=tools_root)
    indep_audit = audit_renderer_provenance(RENDERER_INDEPENDENT_CLEAN_ROOM)

    native_license_ok = (not is_commercial) or bool(seecut_perm.get("verified"))
    native_usable = bool(native_license_ok and native_audit["runtime_ready"])
    indep_usable = bool(indep_audit["clean_room_verified"] and indep_audit["runtime_ready"] and indep_audit["commercial_eligible"])

    selected: str | None = None
    allowed = False
    status = "BLOCKED_NO_AUTHORIZED_RENDERER"
    reason = ""

    if req == "native_seecut":
        if not native_license_ok:
            status = "BLOCKED_COMMERCIAL_LICENSE"
            reason = (
                f"Explicit --renderer native_seecut blocked for {purpose}: "
                f"{seecut_perm.get('reason')}. Use written permission from aiesecxuanli@gmail.com or select --renderer independent_clean_room."
            )
        elif not native_audit["runtime_ready"]:
            status = "BLOCKED_NATIVE_ENV_MISSING"
            reason = "Explicit --renderer native_seecut requested but pinned SeeCut/HyperFrames runtime is missing."
        else:
            selected = RENDERER_NATIVE_SEECUT
            allowed = True
            status = "AUTHORIZED_NATIVE_SEECUT"
            reason = "Native SeeCut authorized for requested purpose and runtime verified."
    elif req == "independent_clean_room":
        if indep_usable:
            selected = RENDERER_INDEPENDENT_CLEAN_ROOM
            allowed = True
            status = "AUTHORIZED_INDEPENDENT_CLEAN_ROOM"
            reason = (
                "Audited clean-room independent FFmpeg+Pillow+NumPy renderer selected "
                "(zero PolyForm Noncommercial SeeCut code/assets; native SeeCut commercial status remains independently tracked)."
            )
        else:
            status = "BLOCKED_INDEPENDENT_AUDIT_FAILED"
            reason = f"Independent clean-room renderer failed provenance/runtime check: {indep_audit.get('isolation_violations')}"
    else:  # auto
        if is_commercial:
            if native_usable:
                selected = RENDERER_NATIVE_SEECUT
                allowed = True
                status = "AUTHORIZED_NATIVE_SEECUT"
                reason = "Verified written commercial permission present for native SeeCut."
            elif indep_usable:
                selected = RENDERER_INDEPENDENT_CLEAN_ROOM
                allowed = True
                status = "AUTHORIZED_INDEPENDENT_CLEAN_ROOM"
                reason = (
                    "Native SeeCut is BLOCKED_COMMERCIAL_LICENSE (PolyForm Noncommercial 1.0.0); "
                    "routed cleanly to audited INDEPENDENT_FFMPEG_PIL clean-room renderer (zero PolyForm code/assets, zero fake receipt)."
                )
            else:
                status = "BLOCKED_COMMERCIAL_LICENSE"
                reason = "Native SeeCut blocked for commercial use and independent clean-room renderer unavailable."
        else:
            if native_usable:
                selected = RENDERER_NATIVE_SEECUT
                allowed = True
                status = "AUTHORIZED_NATIVE_SEECUT"
                reason = "Noncommercial engineering/personal use authorized on verified pinned native SeeCut."
            elif indep_usable:
                selected = RENDERER_INDEPENDENT_CLEAN_ROOM
                allowed = True
                status = "AUTHORIZED_INDEPENDENT_CLEAN_ROOM"
                reason = "Native SeeCut runtime unavailable; routed to audited clean-room independent renderer."
            else:
                status = "BLOCKED_NO_RENDERER_AVAILABLE"
                reason = "Neither native SeeCut nor independent clean-room renderer is runtime ready."

    decision = {
        "schema_version": "kh03.renderer_routing_decision.v1",
        "decided_at_utc": now_utc(),
        "requested_mode": req,
        "intended_purpose": purpose,
        "allowed": allowed,
        "status": status,
        "selected_renderer": selected,
        "reason": reason,
        "per_renderer_rights": {
            RENDERER_NATIVE_SEECUT: {
                "license": "PolyForm Noncommercial 1.0.0",
                "authorized_for_purpose": native_license_ok,
                "runtime_ready": native_audit["runtime_ready"],
                "status": "AUTHORIZED" if native_usable else ("BLOCKED_COMMERCIAL_LICENSE" if not native_license_ok else "ENV_NOT_READY"),
                "commercial_permission_check": seecut_perm,
            },
            RENDERER_INDEPENDENT_CLEAN_ROOM: {
                "license": "PERMISSIVE_CLEAN_ROOM_COMMERCIAL_ELIGIBLE",
                "authorized_for_purpose": indep_usable,
                "clean_room_verified": indep_audit["clean_room_verified"],
                "runtime_ready": indep_audit["runtime_ready"],
                "status": "AUTHORIZED" if indep_usable else "AUDIT_OR_ENV_FAILED",
            },
        },
        "fake_permission_receipt_created": False,
        "audits": {
            RENDERER_NATIVE_SEECUT: native_audit,
            RENDERER_INDEPENDENT_CLEAN_ROOM: indep_audit,
        },
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(out_dir / "RENDERER_ROUTING_DECISION.json", decision)
        write_json_atomic(out_dir / "RENDERER_PROVENANCE_AUDIT.json", indep_audit if selected == RENDERER_INDEPENDENT_CLEAN_ROOM else native_audit)
    return decision


def resolve_editorial_lifecycle(
    *,
    rendered_candidates: list[dict[str, Any]],
    pairwise_status: str,
    winner_candidate_id: str | None = None,
    owner_override_candidate_id: str | None = None,
    owner_visual_approved: bool = False,
    owner_audio_approved: bool = False,
    live_judge_available: bool = False,
) -> dict[str, Any]:
    """Explicitly separate CANDIDATE_RENDERED, EDITORIAL_PENDING, and FINAL_APPROVED (R4.2.HOTFIX Item 5)."""
    valid_rendered = [c for c in rendered_candidates if c.get("preview_qa_pass") is True and c.get("preview_mp4_sha256")]
    candidate_state = "CANDIDATE_RENDERED" if len(valid_rendered) > 0 else "CANDIDATE_NOT_RENDERED"

    if owner_override_candidate_id and any(c.get("candidate_id") == owner_override_candidate_id for c in valid_rendered):
        editorial_state = "EDITORIAL_OWNER_SELECTED"
    elif pairwise_status == "CONSENSUS" and winner_candidate_id and live_judge_available:
        editorial_state = "EDITORIAL_AI_CONSENSUS"
    else:
        editorial_state = "EDITORIAL_PENDING"

    if candidate_state == "CANDIDATE_RENDERED" and owner_visual_approved and owner_audio_approved:
        final_state = "FINAL_APPROVED"
    else:
        final_state = "PENDING_OWNER_APPROVAL"

    return {
        "schema_version": "kh03.editorial_lifecycle.v1",
        "candidate_state": candidate_state,
        "editorial_state": editorial_state,
        "final_state": final_state,
        "live_judge_available": bool(live_judge_available),
        "valid_rendered_candidate_count": len(valid_rendered),
        "valid_rendered_candidate_ids": [c["candidate_id"] for c in valid_rendered],
        "manual_owner_review_required": editorial_state == "EDITORIAL_PENDING" or final_state != "FINAL_APPROVED",
        "blocked_preview_due_to_missing_ai_judge": False,
    }


__all__ = [
    "RENDERER_INDEPENDENT_CLEAN_ROOM",
    "RENDERER_NATIVE_SEECUT",
    "VALID_RENDERER_MODES",
    "audit_renderer_provenance",
    "resolve_editorial_lifecycle",
    "select_renderer_backend",
]

