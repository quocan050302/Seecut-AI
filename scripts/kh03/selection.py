"""KH-03 Mandatory User Selection Gate & Cryptographically Bound SELECTION_RECEIPT.json.

Fail-closed guarantees:
- Only SK-01..SK-05 are accepted (rejects AUTO, HYBRID, blank, unknown).
- AI cannot self-approve (`AI_AUTO` rejected).
- Receipt binds strictly to run_id, video_sha256, script_sha256, recommendation_sha256, registry_version.
- Any modification of input video, script, or recommendation invalidates prior selection!
- TEST_ONLY receipts are strictly rejected in production runs (`production_mode=True`).
"""

from __future__ import annotations

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


def record_user_selection(
    *,
    run_dir: Path,
    selected_skill_id: str,
    approval_source: str = "EXPLICIT_OWNER_CLI",
    approval_evidence: str = "Explicit user CLI command: scripts.kh03 select",
    test_only: bool = False,
) -> dict[str, Any]:
    """Bind an explicit user skill choice to the current run's recommendation & input hashes."""
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

    receipt = {
        "schema_version": "kh03.selection.v1",
        "run_id": rec["run_id"],
        "video_sha256": inp["video_sha256"],
        "script_sha256": inp["script_sha256"],
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
) -> dict[str, Any]:
    """Verify durable SELECTION_RECEIPT.json matches current run, inputs, recommendation & mode (fail-closed)."""
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

    if receipt["selected_skill_id"] not in VALID_SKILL_IDS:
        raise SelectionGateError(f"Invalid selected_skill_id: {receipt['selected_skill_id']!r}")
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
    if expected_video_sha256 and receipt["video_sha256"] != expected_video_sha256:
        raise SelectionGateError("Current video SHA on disk differs from bound selection receipt; selection invalidated.")
    if expected_script_sha256 and receipt["script_sha256"] != expected_script_sha256:
        raise SelectionGateError("Current script SHA differs from bound selection receipt; selection invalidated.")

    return {
        "valid": True,
        "selected_skill_id": receipt["selected_skill_id"],
        "receipt": receipt,
        "receipt_sha256": hash_artifact(receipt),
    }
