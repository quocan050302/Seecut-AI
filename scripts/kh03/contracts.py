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
        "staging", "candidates", "STAGE_RECEIPTS",
    ):
        (r / sub).mkdir(parents=True, exist_ok=True)
    return r


_PLACEHOLDER_PERM_RE = re.compile(
    r"(?i)^(?:ok|yes|true|approved|approved_ok|test|dummy|placeholder|fake|bypass|none|n/a|12345678\d*)$"
)


def verify_commercial_permission_evidence(
    evidence: Any,
    *,
    expected_upstream_sha: str | None = None,
) -> dict[str, Any]:
    """Fail-closed verifier for written commercial authorization receipts (R4.1-02 / PROBLEM-01).

    Rejects arbitrary free-form strings (even len >= 8). Requires a structured JSON receipt
    (dict or path to non-symlink JSON file) containing verified licensor, scope, upstream pin,
    issued_at_utc, non-expired expiry, non-placeholder written_authorization_ref,
    verified_by_owner=True, and matching receipt_sha256 (when present).
    """
    from . import UPSTREAM_SEECUT_SHA

    want_pin = (expected_upstream_sha or UPSTREAM_SEECUT_SHA)[:7].lower()
    if evidence is None:
        return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": "Missing commercial_permission_evidence.", "receipt_sha256": None}

    doc: dict[str, Any] | None = None
    source_path: str | None = None
    if isinstance(evidence, dict):
        doc = dict(evidence)
    elif isinstance(evidence, (str, Path)):
        raw_s = str(evidence).strip()
        if not raw_s or _PLACEHOLDER_PERM_RE.match(raw_s):
            return {
                "verified": False,
                "status": "BLOCKED_COMMERCIAL_LICENSE",
                "reason": f"Unstructured or placeholder commercial_permission_evidence rejected ({raw_s[:32]!r}).",
                "receipt_sha256": None,
            }
        # Reject plain non-JSON free-form strings (even if len >= 8!)
        if raw_s.startswith("{"):
            try:
                parsed = json.loads(raw_s)
                if isinstance(parsed, dict):
                    doc = parsed
            except Exception as ex:
                return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": f"Malformed JSON permission receipt: {ex}", "receipt_sha256": None}
        else:
            p = Path(raw_s)
            if p.is_symlink():
                return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": "Symlink commercial permission receipt rejected.", "receipt_sha256": None}
            if not p.is_file():
                return {
                    "verified": False,
                    "status": "BLOCKED_COMMERCIAL_LICENSE",
                    "reason": "Free-form string commercial_permission_evidence rejected: supply a verified structured JSON permission receipt file.",
                    "receipt_sha256": None,
                }
            try:
                loaded = parse_json_strict(p)
                if isinstance(loaded, dict):
                    doc = loaded
                    source_path = str(p.resolve())
            except Exception as ex:
                return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": f"Invalid permission receipt file: {ex}", "receipt_sha256": None}
    else:
        return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": "Unsupported commercial_permission_evidence type.", "receipt_sha256": None}

    if not isinstance(doc, dict):
        return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": "Commercial permission receipt must be a JSON object.", "receipt_sha256": None}

    licensor = str(doc.get("licensor") or "").strip()
    scope = str(doc.get("scope") or "").strip()
    pin = str(doc.get("upstream_pin") or "").strip().lower()
    issued = str(doc.get("issued_at_utc") or "").strip()
    expires = str(doc.get("expires_at_utc") or "").strip()
    auth_ref = str(doc.get("written_authorization_ref") or "").strip()
    owner_ok = is_strict_bool(doc.get("verified_by_owner")) and bool(doc.get("verified_by_owner"))

    if not any(tok in licensor.lower() for tok in ("yeje-cpu", "aiesecxuanli@gmail.com")):
        return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": f"Unrecognized licensor {licensor!r}; must authorize YeJe-cpu (aiesecxuanli@gmail.com).", "receipt_sha256": None}
    if len(scope) < 8 or _PLACEHOLDER_PERM_RE.match(scope):
        return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": "Commercial permission receipt missing concrete scope.", "receipt_sha256": None}
    if not pin.startswith(want_pin):
        return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": f"Commercial permission upstream_pin {pin!r} does not match pinned {want_pin!r}.", "receipt_sha256": None}
    if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", issued):
        return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": f"Invalid issued_at_utc {issued!r}.", "receipt_sha256": None}
    if expires:
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", expires):
            return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": f"Invalid expires_at_utc {expires!r}.", "receipt_sha256": None}
        if expires < now_utc():
            return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": f"Commercial permission receipt expired at {expires}.", "receipt_sha256": None}
    if len(auth_ref) < 8 or _PLACEHOLDER_PERM_RE.match(auth_ref) or "test_only" in auth_ref.lower():
        return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": "Missing or placeholder written_authorization_ref.", "receipt_sha256": None}
    if not owner_ok:
        return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": "Commercial permission receipt lacks verified_by_owner=true.", "receipt_sha256": None}
    if doc.get("test_only") is True:
        return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": "TEST_ONLY permission receipt cannot authorize commercial execution.", "receipt_sha256": None}

    body_for_hash = {k: v for k, v in doc.items() if k != "receipt_sha256"}
    computed_sha = hash_artifact(body_for_hash)
    if doc.get("receipt_sha256") and str(doc["receipt_sha256"]) != computed_sha:
        return {"verified": False, "status": "BLOCKED_COMMERCIAL_LICENSE", "reason": "Tampered commercial permission receipt_sha256 mismatch.", "receipt_sha256": None}

    return {
        "verified": True,
        "status": "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED",
        "reason": f"Verified structured commercial authorization receipt ({auth_ref[:40]}).",
        "receipt_sha256": computed_sha,
        "source_path": source_path,
        "licensor": licensor,
        "scope": scope,
        "upstream_pin": pin,
    }


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
    "verify_commercial_permission_evidence",
    "write_json_atomic",
]
