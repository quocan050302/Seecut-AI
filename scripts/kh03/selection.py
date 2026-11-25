"""KH-03 Mandatory User Selection Gate & Fresh Hash-Bound SELECTION_RECEIPT.json (KH-03.R1 Hardened).

Fail-closed guarantees:
- Only SK-01..SK-05 are accepted (rejects AUTO, HYBRID, blank, unknown).
- AI cannot self-approve (`AI_AUTO` rejected).
- Receipt binds strictly to run_id, fresh video_sha256, raw_script_sha256 (if script file supplied),
  normalized script_sha256, asr_sha256, recommendation_sha256, registry_version, and constraints_sha256.
- Any modification of input video, script file on disk, recommendation, constraints, or registry invalidates prior selection!
- TEST_ONLY receipts are strictly rejected in production runs (`production_mode=True`).
- Trust boundary honesty: local JSON hash binding enforces tamper/drift integrity, NOT a cryptographic
  signature proving human identity outside a trusted UI/CLI origin.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import REGISTRY_VERSION, VALID_SKILL_IDS
from .contracts import (
    STATE_USER_SELECTED,
    assert_kh03_schema,
    hash_artifact,
    now_utc,
    parse_json_strict,
    transition_state,
    write_json_atomic,
)


class SelectionGateError(ValueError):
    """Raised whenever the mandatory KH-03 user selection gate fails."""


_ALLOWED_APPROVAL_SOURCES = frozenset({
    "EXPLICIT_OWNER_CLI",
    "EXPLICIT_HOST_MESSAGE",
    "TEST_ONLY_FIXTURE",
})

TRUST_MODEL_NOTE = "INTEGRITY_HASH_BINDING_ONLY_NOT_CRYPTOGRAPHIC_HUMAN_SIGNATURE"


def _hash_file_bytes(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def record_user_selection(
    *,
    run_dir: Path,
    selected_skill_id: str,
    approval_source: str = "EXPLICIT_OWNER_CLI",
    approval_evidence: str = "Explicit user CLI command: scripts.kh03 select",
    test_only: bool = False,
) -> dict[str, Any]:
    """Bind an explicit user skill choice to the current run's recommendation, fresh input bytes & constraints."""
    sid = str(selected_skill_id or "").strip().upper()
    if sid not in VALID_SKILL_IDS:
        raise SelectionGateError(
            f"Invalid skill_id {selected_skill_id!r}. Must be one of {VALID_SKILL_IDS} (AUTO/HYBRID/blank forbidden)."
        )
    if approval_source not in _ALLOWED_APPROVAL_SOURCES:
        raise SelectionGateError(
            f"Unauthorized approval_source {approval_source!r}; AI self-selection is strictly forbidden."
        )
    if approval_source == "TEST_ONLY_FIXTURE" and not test_only:
        raise SelectionGateError("TEST_ONLY_FIXTURE approval_source must set test_only=True.")
    if test_only and approval_source != "TEST_ONLY_FIXTURE":
        approval_source = "TEST_ONLY_FIXTURE"

    rec_path = run_dir / "selection" / "SKILL_RECOMMENDATION.json"
    inp_path = run_dir / "analysis" / "INPUT_ANALYSIS.json"
    if not rec_path.is_file() or not inp_path.is_file():
        raise SelectionGateError(
            f"Cannot select skill before recommendation is issued (missing {rec_path} or {inp_path})."
        )

    rec = parse_json_strict(rec_path)
    inp = parse_json_strict(inp_path)
    if rec["run_id"] != inp["run_id"]:
        raise SelectionGateError("Run ID mismatch between INPUT_ANALYSIS.json and SKILL_RECOMMENDATION.json.")
    if rec["video_sha256"] != inp["video_sha256"] or rec["script_sha256"] != inp["script_sha256"]:
        raise SelectionGateError("Input hashes changed since recommendation; re-run `recommend` before selecting.")

    # Verify fresh bytes on disk at selection time too (prevent stale selection over mutated source)
    vp = Path(inp["video_path"])
    if vp.is_file() and _hash_file_bytes(vp) != inp["video_sha256"]:
        raise SelectionGateError("Source video bytes on disk changed since recommendation; re-run `recommend`.")
    sp_raw = inp.get("script_source_path")
    if sp_raw and Path(sp_raw).is_file() and inp.get("raw_script_sha256"):
        if _hash_file_bytes(Path(sp_raw)) != inp["raw_script_sha256"]:
            raise SelectionGateError("Supplied script file on disk changed since recommendation; re-run `recommend`.")

    constraints_sha = inp.get("constraints_sha256") or hash_artifact(inp.get("constraints") or {})

    receipt = {
        "schema_version": "kh03.selection.v1",
        "run_id": rec["run_id"],
        "video_sha256": inp["video_sha256"],
        "script_sha256": inp["script_sha256"],
        "raw_script_sha256": inp.get("raw_script_sha256"),
        "script_source_path": inp.get("script_source_path"),
        "asr_sha256": inp.get("asr_sha256"),
        "constraints_sha256": constraints_sha,
        "recommendation_sha256": hash_artifact(rec),
        "registry_version": rec["registry_version"],
        "recommended_skill_id": rec["recommended_skill_id"],
        "selected_skill_id": sid,
        "overrode_recommendation": (sid != rec["recommended_skill_id"]),
        "approval_source": approval_source,
        "approval_evidence": str(approval_evidence).strip() or "Explicit user selection",
        "test_only": bool(test_only),
        "selected_at_utc": now_utc(),
        "binding": True,
        "trust_model": TRUST_MODEL_NOTE,
    }
    assert_kh03_schema(receipt, "selection")
    out_p = write_json_atomic(run_dir / "selection" / "SELECTION_RECEIPT.json", receipt)
    transition_state(
        run_dir,
        STATE_USER_SELECTED,
        reason=f"Explicit user selection recorded: {sid} (source={approval_source}, override={receipt['overrode_recommendation']}).",
        run_id=rec["run_id"],
    )
    return {
        "status": STATE_USER_SELECTED,
        "receipt": receipt,
        "receipt_path": str(out_p),
        "receipt_sha256": hash_artifact(receipt),
    }


def validate_user_selection(
    *,
    run_dir: Path,
    production_mode: bool = True,
    expected_video_sha256: str | None = None,
    expected_script_sha256: str | None = None,
    expected_raw_script_sha256: str | None = None,
    expected_constraints: dict[str, Any] | None = None,
    expected_skill_id: str | None = None,
    supplied_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Single shared fail-closed verifier for SELECTION_RECEIPT.json across KH-03 and direct KH-02 ingress.

    Always re-hashes fresh video bytes, fresh raw script file bytes (when script_source_path was supplied),
    recommendation JSON, registry version, and constraints digest. Rejects forged in-memory dicts that
    do not match the persisted receipt on disk inside run_dir.
    """
    sel_path = run_dir / "selection" / "SELECTION_RECEIPT.json"
    rec_path = run_dir / "selection" / "SKILL_RECOMMENDATION.json"
    inp_path = run_dir / "analysis" / "INPUT_ANALYSIS.json"

    if not sel_path.is_file():
        raise SelectionGateError(
            "SELECTION_REQUIRED: No SELECTION_RECEIPT.json found. User must explicitly choose SK-01..SK-05 before execution."
        )
    if not rec_path.is_file() or not inp_path.is_file():
        raise SelectionGateError("Incomplete run state: missing SKILL_RECOMMENDATION.json or INPUT_ANALYSIS.json.")

    receipt = parse_json_strict(sel_path)
    rec = parse_json_strict(rec_path)
    inp = parse_json_strict(inp_path)
    assert_kh03_schema(receipt, "selection")

    # If a caller supplied an in-memory receipt dict (e.g. direct KH-02 override call), it MUST match persisted receipt byte-canonically!
    if supplied_receipt is not None:
        if not isinstance(supplied_receipt, dict) or hash_artifact(supplied_receipt) != hash_artifact(receipt):
            raise SelectionGateError(
                "FORGED_OR_MISMATCHED_RECEIPT: Supplied kh03_selection_receipt does not match persisted SELECTION_RECEIPT.json."
            )

    if receipt["selected_skill_id"] not in VALID_SKILL_IDS:
        raise SelectionGateError(f"Invalid selected_skill_id: {receipt['selected_skill_id']!r}")
    if expected_skill_id is not None and receipt["selected_skill_id"] != expected_skill_id:
        raise SelectionGateError(
            f"Skill mismatch: override skill {expected_skill_id!r} != bound selection {receipt['selected_skill_id']!r}."
        )
    if receipt["approval_source"] not in _ALLOWED_APPROVAL_SOURCES:
        raise SelectionGateError(f"Unauthorized approval_source: {receipt['approval_source']!r}")
    if production_mode and (receipt.get("test_only") or receipt["approval_source"] == "TEST_ONLY_FIXTURE"):
        raise SelectionGateError(
            "TEST_ONLY selection receipt is strictly forbidden in production execution; require explicit owner choice."
        )
    if receipt["run_id"] != inp["run_id"] or receipt["run_id"] != rec["run_id"]:
        raise SelectionGateError(
            f"Stale/replayed SELECTION_RECEIPT.json: receipt run_id={receipt['run_id']!r} != current run_id={inp['run_id']!r}."
        )
    if receipt["registry_version"] != REGISTRY_VERSION:
        raise SelectionGateError(
            f"Registry version mismatch: receipt={receipt['registry_version']!r} != current={REGISTRY_VERSION!r}."
        )
    if receipt["recommendation_sha256"] != hash_artifact(rec):
        raise SelectionGateError("Recommendation hash mismatch: SKILL_RECOMMENDATION.json changed after selection.")
    if receipt["video_sha256"] != inp["video_sha256"]:
        raise SelectionGateError("Input video SHA changed after selection; re-run recommend and obtain new selection.")
    if receipt["script_sha256"] != inp["script_sha256"]:
        raise SelectionGateError("Input script hash changed after selection; re-run recommend and obtain new selection.")

    # Fresh disk re-hash of primary video
    vp = Path(inp["video_path"])
    fresh_vid_sha = _hash_file_bytes(vp) if vp.is_file() else "MISSING_VIDEO_ON_DISK"
    if fresh_vid_sha != receipt["video_sha256"]:
        raise SelectionGateError(
            f"FRESH_VIDEO_HASH_MISMATCH: Source video on disk changed/missing ({fresh_vid_sha[:12]} != {receipt['video_sha256'][:12]})."
        )
    if expected_video_sha256 and receipt["video_sha256"] != expected_video_sha256:
        raise SelectionGateError("Expected video SHA differs from bound selection receipt; selection invalidated.")

    # Fresh disk re-hash of raw script file whenever script_source_path was bound
    sp_raw = receipt.get("script_source_path") or inp.get("script_source_path")
    bound_raw_sha = receipt.get("raw_script_sha256") or inp.get("raw_script_sha256")
    if sp_raw and bound_raw_sha:
        sp_path = Path(sp_raw)
        fresh_raw_sha = _hash_file_bytes(sp_path) if sp_path.is_file() else "MISSING_SCRIPT_ON_DISK"
        if fresh_raw_sha != bound_raw_sha:
            raise SelectionGateError(
                f"FRESH_SCRIPT_FILE_MISMATCH: Script file on disk changed/missing after recommendation/selection ({sp_path.name})."
            )
    if expected_raw_script_sha256 and bound_raw_sha and bound_raw_sha != expected_raw_script_sha256:
        raise SelectionGateError("Supplied raw script hash differs from bound selection receipt.")
    if expected_script_sha256 and receipt["script_sha256"] != expected_script_sha256:
        raise SelectionGateError("Current normalized script SHA differs from bound selection receipt; selection invalidated.")

    # Constraints drift check (layout_lock / cut_policy / caption_policy / music_policy / license_decision)
    bound_c_sha = receipt.get("constraints_sha256")
    if bound_c_sha:
        saved_c_sha = inp.get("constraints_sha256") or hash_artifact(inp.get("constraints") or {})
        if bound_c_sha != saved_c_sha:
            raise SelectionGateError("CONSTRAINTS_MUTATED: INPUT_ANALYSIS constraints changed after selection.")
        if expected_constraints is not None and hash_artifact(expected_constraints) != bound_c_sha:
            raise SelectionGateError("CONSTRAINTS_OVERRIDE_MISMATCH: Execution constraints diverge from bound selection.")

    return {
        "valid": True,
        "selected_skill_id": receipt["selected_skill_id"],
        "receipt": receipt,
        "receipt_sha256": hash_artifact(receipt),
        "fresh_video_sha256": fresh_vid_sha,
        "trust_model": receipt.get("trust_model") or TRUST_MODEL_NOTE,
    }
