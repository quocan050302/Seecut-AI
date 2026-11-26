"""KH-03 Versioned Research Foundation Adapter (METADATA_ONLY — zero live vidIQ calls or competitor downloads)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import parse_json_strict, write_json_atomic

RESEARCH_SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "research" / "kh03" / "vidiq_snapshot_20261124.json"


def load_archived_research_snapshot(snapshot_path: Path | None = None) -> dict[str, Any]:
    """Load versioned METADATA_ONLY research cards; never claims live analytics or causal view guarantees."""
    p = snapshot_path or RESEARCH_SNAPSHOT_PATH
    if not p.is_file():
        return {
            "available": False,
            "evidence_class": "UNAVAILABLE",
            "live_vidiq_calls_executed": False,
            "competitor_media_downloaded": False,
            "records": [],
            "note": f"Archived research snapshot absent at {p}; editing operates independently without vidIQ.",
        }
    data = parse_json_strict(p)
    if data.get("competitor_media_downloaded") is True:
        raise ValueError("Research snapshot violates policy: competitor media downloads are strictly forbidden.")
    return {
        "available": True,
        "evidence_class": str(data.get("evidence_class") or "METADATA_ONLY"),
        "observation_date": data.get("observation_date"),
        "live_vidiq_calls_executed": False,
        "competitor_media_downloaded": False,
        "disclaimer": data.get("disclaimer", ""),
        "known_unknowns": list(data.get("known_unknowns") or []),
        "records": list(data.get("records") or []),
    }


def extract_grammar_notes_for_skill(skill_id: str, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Derive non-binding editorial craft notes from archived METADATA_ONLY descriptors."""
    snap = snapshot if snapshot is not None else load_archived_research_snapshot()
    recs = snap.get("records") or []
    patterns = [
        {"record_id": r["record_id"], "grammar_note": r["inferred_reusable_grammar"], "evidence_class": "METADATA_ONLY"}
        for r in recs[:4]
    ]
    return {
        "skill_id": skill_id,
        "evidence_class": "METADATA_ONLY",
        "live_provider_used": False,
        "causal_claim": False,
        "grammar_notes": patterns,
    }


def query_authorized_research_adapter(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Optional future authorized research adapter — fails closed when unconfigured; zero live vidIQ calls."""
    if not config or not config.get("authorized_provider"):
        return {
            "status": "NOT_CONFIGURED_OFFLINE_ONLY",
            "live_calls_made": 0,
            "note": "No live research provider configured; KH-03 operates fully offline on local analysis and archived metadata.",
        }
    return {
        "status": "BLOCKED_PROVIDER_UNVERIFIED",
        "live_calls_made": 0,
        "note": "External research provider requires explicit runtime verification and credentials outside repo.",
    }


def emit_research_summary(run_dir: Path, skill_id: str, *, ctx: Any | None = None) -> Path:
    if ctx is not None:
        ctx.check_cancelled()
    snap = load_archived_research_snapshot()
    notes = extract_grammar_notes_for_skill(skill_id, snap)
    payload = {
        "snapshot_available": snap["available"],
        "evidence_class": snap["evidence_class"],
        "live_vidiq_calls_executed": False,
        "competitor_media_downloaded": False,
        "notes": notes,
    }
    target = run_dir / "research" / "RESEARCH_CONTEXT.json"
    if ctx is not None:
        ctx.publish_json("RESEARCH_CONTEXT.json", payload, target)
        return target
    return write_json_atomic(target, payload)
