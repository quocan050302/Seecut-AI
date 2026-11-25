"""KH-03 Contracts: strict versioned schemas, fail-closed state machine, safe paths & anti-injection sanitization."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from scripts.kh02.contracts import (
    canonicalize,
    hash_artifact,
    is_strict_bool,
    is_strict_int,
    is_strict_number,
    parse_json_strict,
    safe_path,
    sanitize_identifier,
    validate_schema as _validate_kh02_schema,
)

KH03_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas" / "kh03"

STATE_NEW = "NEW"
STATE_ANALYZED = "ANALYZED"
STATE_AWAITING_USER_SELECTION = "AWAITING_USER_SELECTION"
STATE_USER_SELECTED = "USER_SELECTED"
STATE_PLANNING = "PLANNING"
STATE_ASSET_READY = "ASSET_READY"
STATE_ASSET_PARTIAL = "ASSET_PARTIAL"
STATE_COMPILED = "COMPILED"
STATE_RENDERED = "RENDERED"
STATE_QA = "QA"
STATE_CANDIDATE = "CANDIDATE"
STATE_ADOPTED = "ADOPTED"
STATE_BLOCKED = "BLOCKED"
STATE_FAILED = "FAILED"

VALID_STATES = frozenset({
    STATE_NEW, STATE_ANALYZED, STATE_AWAITING_USER_SELECTION, STATE_USER_SELECTED,
    STATE_PLANNING, STATE_ASSET_READY, STATE_ASSET_PARTIAL, STATE_COMPILED,
    STATE_RENDERED, STATE_QA, STATE_CANDIDATE, STATE_ADOPTED, STATE_BLOCKED, STATE_FAILED,
})

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATE_NEW: frozenset({STATE_ANALYZED, STATE_BLOCKED, STATE_FAILED}),
    STATE_ANALYZED: frozenset({STATE_AWAITING_USER_SELECTION, STATE_BLOCKED, STATE_FAILED}),
    STATE_AWAITING_USER_SELECTION: frozenset({STATE_USER_SELECTED, STATE_AWAITING_USER_SELECTION, STATE_BLOCKED, STATE_FAILED}),
    STATE_USER_SELECTED: frozenset({STATE_PLANNING, STATE_AWAITING_USER_SELECTION, STATE_BLOCKED, STATE_FAILED}),
    STATE_PLANNING: frozenset({STATE_ASSET_READY, STATE_ASSET_PARTIAL, STATE_COMPILED, STATE_BLOCKED, STATE_FAILED}),
    STATE_ASSET_READY: frozenset({STATE_COMPILED, STATE_BLOCKED, STATE_FAILED}),
    STATE_ASSET_PARTIAL: frozenset({STATE_COMPILED, STATE_BLOCKED, STATE_FAILED}),
    STATE_COMPILED: frozenset({STATE_RENDERED, STATE_QA, STATE_CANDIDATE, STATE_BLOCKED, STATE_FAILED}),
    STATE_RENDERED: frozenset({STATE_QA, STATE_BLOCKED, STATE_FAILED}),
    STATE_QA: frozenset({STATE_CANDIDATE, STATE_ADOPTED, STATE_PLANNING, STATE_BLOCKED, STATE_FAILED}),
    STATE_CANDIDATE: frozenset({STATE_ADOPTED, STATE_PLANNING, STATE_BLOCKED}),
    STATE_ADOPTED: frozenset({STATE_ADOPTED}),
    STATE_BLOCKED: frozenset({STATE_AWAITING_USER_SELECTION, STATE_PLANNING, STATE_BLOCKED}),
    STATE_FAILED: frozenset({STATE_NEW, STATE_FAILED}),
}

_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"(?i)\bignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?\b"),
    re.compile(r"(?i)\bdisregard\s+(?:all\s+)?(?:system|safety|license)\s+rules?\b"),
    re.compile(r"(?i)\bauto[\s_-]*select\s+SK-0[1-5]\b"),
    re.compile(r"(?i)\bbypass\s+(?:license|selection|gate|problem-01)\b"),
    re.compile(r"(?i)<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", re.DOTALL),
)


class StateTransitionError(ValueError):
    """Raised when an illegal KH-03 state machine transition is attempted."""


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json_atomic(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    for attempt in range(5):
        try:
            tmp.replace(path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.03 * (attempt + 1))
    return path


def load_kh03_schema(schema_name: str) -> dict[str, Any]:
    fname = schema_name if schema_name.endswith(".json") else f"{schema_name}.schema.json"
    p = KH03_SCHEMAS_DIR / fname
    if not p.is_file():
        raise FileNotFoundError(f"Unknown KH-03 schema: {schema_name} ({p})")
    return parse_json_strict(p)


def validate_kh03_schema(data: Any, schema_name: str) -> list[dict[str, str]]:
    """Strict stdlib validation against versioned schemas/kh03/*.schema.json."""
    schema = load_kh03_schema(schema_name)
    return _validate_kh02_schema(data, schema)


def assert_kh03_schema(data: Any, schema_name: str) -> None:
    errs = validate_kh03_schema(data, schema_name)
    if errs:
        raise ValueError(f"KH-03 schema '{schema_name}' validation failed: {errs[:4]}")


def sanitize_untrusted_text(value: Any, *, max_len: int = 4000, strip_injection: bool = True) -> str:
    """Treat external transcripts, metadata, and scripts strictly as inert data (never instructions)."""
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", text)
    if strip_injection:
        for pat in _PROMPT_INJECTION_PATTERNS:
            text = pat.sub("[FILTERED_UNTRUSTED_DIRECTIVE]", text)
    text = " ".join(text.split())
    return text[:max_len]


def init_state_machine(run_dir: Path, run_id: str) -> dict[str, Any]:
    sm = {
        "schema_version": "kh03.state_machine.v1",
        "run_id": run_id,
        "state": STATE_NEW,
        "history": [{"from": None, "to": STATE_NEW, "reason": "Run initialized.", "at_utc": now_utc()}],
    }
    write_json_atomic(run_dir / "STATE_MACHINE.json", sm)
    return sm


def load_state_machine(run_dir: Path) -> dict[str, Any]:
    p = run_dir / "STATE_MACHINE.json"
    if not p.is_file():
        raise FileNotFoundError(f"STATE_MACHINE.json missing in {run_dir}")
    return parse_json_strict(p)


def transition_state(run_dir: Path, target_state: str, *, reason: str, run_id: str | None = None) -> dict[str, Any]:
    """Enforce legal KH-03 workflow transitions and persist STATE_MACHINE.json atomically."""
    if target_state not in VALID_STATES:
        raise StateTransitionError(f"Unknown KH-03 state: {target_state!r}")
    p = run_dir / "STATE_MACHINE.json"
    if not p.is_file():
        sm = init_state_machine(run_dir, run_id or run_dir.name)
    else:
        sm = parse_json_strict(p)
    cur = sm["state"]
    if target_state not in ALLOWED_TRANSITIONS.get(cur, frozenset()):
        raise StateTransitionError(f"Illegal KH-03 transition: {cur} -> {target_state} ({reason})")
    sm["state"] = target_state
    sm["history"].append({"from": cur, "to": target_state, "reason": reason, "at_utc": now_utc()})
    write_json_atomic(p, sm)
    return sm


def persist_kh03_stage_receipt(
    run_dir: Path,
    stage_id: str,
    *,
    run_id: str,
    status: str,
    reason: str,
    input_hashes: dict[str, Any],
    output_hashes: dict[str, Any],
    evidence_paths: list[str] | None = None,
    test_only: bool = False,
) -> Path:
    rec = {
        "schema_version": "kh03.stage_receipt.v1",
        "stage_id": sanitize_identifier(stage_id),
        "run_id": run_id,
        "timestamp_utc": now_utc(),
        "status": status,
        "reason": reason,
        "input_hashes": {k: v for k, v in input_hashes.items() if v is not None},
        "output_hashes": {k: v for k, v in output_hashes.items() if v is not None},
        "evidence_paths": list(evidence_paths or []),
        "test_only": bool(test_only),
    }
    return write_json_atomic(run_dir / "STAGE_RECEIPTS" / f"{sanitize_identifier(stage_id)}.json", rec)


def ensure_run_tree(run_dir: Path) -> Path:
    r = Path(os.path.realpath(str(run_dir.expanduser())))
    for sub in (
        "logs", "evidence", "analysis", "selection", "research", "assets",
        "layouts", "build", "qc", "tests", "stems", "exports", "manifests", "cache",
        "staging", "STAGE_RECEIPTS",
    ):
        (r / sub).mkdir(parents=True, exist_ok=True)
    return r


__all__ = [
    "ALLOWED_TRANSITIONS",
    "KH03_SCHEMAS_DIR",
    "STATE_ADOPTED",
    "STATE_ANALYZED",
    "STATE_ASSET_PARTIAL",
    "STATE_ASSET_READY",
    "STATE_AWAITING_USER_SELECTION",
    "STATE_BLOCKED",
    "STATE_CANDIDATE",
    "STATE_COMPILED",
    "STATE_FAILED",
    "STATE_NEW",
    "STATE_PLANNING",
    "STATE_QA",
    "STATE_RENDERED",
    "STATE_USER_SELECTED",
    "StateTransitionError",
    "VALID_STATES",
    "assert_kh03_schema",
    "canonicalize",
    "ensure_run_tree",
    "hash_artifact",
    "init_state_machine",
    "is_strict_bool",
    "is_strict_int",
    "is_strict_number",
    "load_kh03_schema",
    "load_state_machine",
    "now_utc",
    "parse_json_strict",
    "persist_kh03_stage_receipt",
    "safe_path",
    "sanitize_identifier",
    "sanitize_untrusted_text",
    "transition_state",
    "validate_kh03_schema",
    "write_json_atomic",
]
