"""KH-02 Contracts: strict JSON parsing, schema validation, rational frame clock, path safety & reference checks."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas" / "kh02"


@dataclass(frozen=True)
class ValidationErrorItem:
    code: str
    location: str
    message: str
    evidence: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "location": self.location,
            "message": self.message,
            "evidence": self.evidence,
        }


def is_strict_bool(v: Any) -> bool:
    return type(v) is bool


def is_strict_int(v: Any) -> bool:
    return type(v) is int


def is_strict_number(v: Any) -> bool:
    return type(v) in (int, float) and math.isfinite(v)


def _reject_constant(token: str) -> Any:
    raise ValueError(f"Non-finite JSON constant rejected: {token}")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"Duplicate JSON key rejected: {k!r}")
        out[k] = v
    return out


def parse_json_strict(raw: str | bytes | Path) -> Any:
    """Parse JSON strictly: rejects duplicate keys, NaN, Infinity, -Infinity."""
    if isinstance(raw, Path):
        text = raw.read_text(encoding="utf-8")
    elif isinstance(raw, bytes):
        text = raw.decode("utf-8")
    else:
        text = raw
    return json.loads(
        text,
        object_pairs_hook=_no_duplicate_keys,
        parse_constant=_reject_constant,
    )


def canonicalize(obj: Any) -> str:
    """Deterministic canonical JSON serialization (rejects NaN/Inf)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def hash_artifact(obj_or_path: Any) -> str:
    """Compute SHA-256 of either a canonical JSON object or a file on disk (streaming)."""
    h = hashlib.sha256()
    if isinstance(obj_or_path, Path):
        with obj_or_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    h.update(canonicalize(obj_or_path).encode("utf-8"))
    return h.hexdigest()


def safe_path(
    candidate: str | Path,
    allowed_root: str | Path,
    *,
    allow_symlinks: bool = False,
    must_exist: bool = False,
) -> Path:
    """Validate that candidate resolves strictly inside allowed_root without traversal or forbidden symlinks."""
    raw_str = str(candidate)
    if "\x00" in raw_str:
        raise ValueError(f"Null byte in path: {raw_str!r}")
    root_raw = Path(allowed_root).expanduser()
    root_real = Path(os.path.realpath(str(root_raw)))
    cand_path = Path(raw_str)
    if cand_path.is_absolute():
        joined = cand_path
    else:
        joined = root_real / cand_path

    if not allow_symlinks:
        # Walk ONLY path components inside allowed_root (avoids false positives on OS symlinks like macOS /var -> /private/var)
        rel_parts: tuple[str, ...] | None = None
        for base in (root_real, root_raw.resolve(strict=False), root_raw):
            try:
                rel_parts = joined.relative_to(base).parts
                break
            except ValueError:
                continue
        if rel_parts is not None:
            cur = root_real
            for part in rel_parts:
                if part in ("", "."):
                    continue
                if part == "..":
                    cur = cur.parent
                else:
                    cur = cur / part
                if cur.is_symlink():
                    raise ValueError(f"Symlink rejected in safe path: {cur}")
        elif joined.is_symlink():
            raise ValueError(f"Symlink rejected in safe path: {joined}")

    resolved = Path(os.path.realpath(str(joined)))
    if resolved != root_real and not str(resolved).startswith(str(root_real) + os.sep):
        raise ValueError(f"Path escapes allowed root {root_real}: {candidate} -> {resolved}")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"Required path does not exist: {resolved}")
    return resolved


def sanitize_identifier(value: str, max_len: int = 64) -> str:
    """Sanitize layer/event names to safe filename/CSS tokens without slashes or '..'."""
    if not isinstance(value, str) or ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"Unsafe identifier rejected: {value!r}")
    cleaned = re.sub(r"[^A-Za-z0-9_.\u4e00-\u9fff\u00C0-\u024F\u1E00-\u1EFF-]+", "_", value).strip("._-")
    if not cleaned:
        raise ValueError(f"Identifier collapses to empty: {value!r}")
    return cleaned[:max_len]


def _check_type(val: Any, expected: str | list[str]) -> bool:
    types = expected if isinstance(expected, list) else [expected]
    for t in types:
        if t == "null" and val is None:
            return True
        if t == "boolean" and is_strict_bool(val):
            return True
        if t == "integer" and is_strict_int(val):
            return True
        if t == "number" and is_strict_number(val):
            return True
        if t == "string" and isinstance(val, str):
            return True
        if t == "array" and isinstance(val, list):
            return True
        if t == "object" and isinstance(val, dict):
            return True
    return False


def _validate_node(val: Any, schema: dict[str, Any], loc: str, errors: list[ValidationErrorItem]) -> None:
    if "const" in schema and val != schema["const"]:
        errors.append(ValidationErrorItem("SCHEMA_CONST", loc, f"Expected const {schema['const']!r}", repr(val)))
        return
    if "enum" in schema and val not in schema["enum"]:
        errors.append(ValidationErrorItem("SCHEMA_ENUM", loc, f"Value not in enum {schema['enum']}", repr(val)))
        return
    if "type" in schema and not _check_type(val, schema["type"]):
        errors.append(
            ValidationErrorItem("SCHEMA_TYPE", loc, f"Expected type {schema['type']}, got {type(val).__name__}", repr(val)[:80])
        )
        return

    if isinstance(val, str):
        if "minLength" in schema and len(val) < schema["minLength"]:
            errors.append(ValidationErrorItem("SCHEMA_MINLEN", loc, f"Length {len(val)} < {schema['minLength']}", repr(val)))
        if "pattern" in schema and not re.search(schema["pattern"], val):
            errors.append(ValidationErrorItem("SCHEMA_PATTERN", loc, f"Does not match pattern {schema['pattern']}", repr(val)))
    elif is_strict_number(val):
        if "minimum" in schema and val < schema["minimum"]:
            errors.append(ValidationErrorItem("SCHEMA_MIN", loc, f"Value {val} < {schema['minimum']}", str(val)))
        if "maximum" in schema and val > schema["maximum"]:
            errors.append(ValidationErrorItem("SCHEMA_MAX", loc, f"Value {val} > {schema['maximum']}", str(val)))
    elif isinstance(val, list):
        if "minItems" in schema and len(val) < schema["minItems"]:
            errors.append(ValidationErrorItem("SCHEMA_MINITEMS", loc, f"Array length {len(val)} < {schema['minItems']}", str(len(val))))
        if "maxItems" in schema and len(val) > schema["maxItems"]:
            errors.append(ValidationErrorItem("SCHEMA_MAXITEMS", loc, f"Array length {len(val)} > {schema['maxItems']}", str(len(val))))
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(val):
                _validate_node(item, item_schema, f"{loc}[{idx}]", errors)
    elif isinstance(val, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in val:
                errors.append(ValidationErrorItem("SCHEMA_REQUIRED", f"{loc}.{req}", f"Missing required key {req!r}"))
        if schema.get("additionalProperties") is False:
            for k in sorted(val.keys()):
                if k not in props:
                    errors.append(ValidationErrorItem("SCHEMA_EXTRA_KEY", f"{loc}.{k}", f"Unknown property {k!r}"))
        elif isinstance(schema.get("additionalProperties"), dict):
            add_s = schema["additionalProperties"]
            for k in sorted(val.keys()):
                if k not in props:
                    _validate_node(val[k], add_s, f"{loc}.{k}", errors)
        for k, sub_s in props.items():
            if k in val:
                _validate_node(val[k], sub_s, f"{loc}.{k}", errors)


def load_schema(schema_name: str) -> dict[str, Any]:
    path = SCHEMAS_DIR / (schema_name if schema_name.endswith(".json") else f"{schema_name}.schema.json")
    if not path.is_file():
        raise FileNotFoundError(f"Unknown KH-02 schema: {schema_name} ({path})")
    return parse_json_strict(path)


def validate_schema(data: Any, schema_name_or_dict: str | dict[str, Any]) -> list[dict[str, str]]:
    """Validate object against a named KH-02 JSON schema (stdlib-only, strict bool/number checks)."""
    schema = load_schema(schema_name_or_dict) if isinstance(schema_name_or_dict, str) else schema_name_or_dict
    errors: list[ValidationErrorItem] = []
    _validate_node(data, schema, "$", errors)
    errors.sort(key=lambda e: (e.location, e.code, e.message))
    return [e.to_dict() for e in errors]


def seconds_to_frame(sec: float | Fraction, fps_num: int, fps_den: int = 1, mode: str = "nearest") -> int:
    """Convert seconds to integer frame index once using exact rational arithmetic."""
    if isinstance(sec, float) and not math.isfinite(sec):
        raise ValueError(f"Non-finite seconds: {sec}")
    if fps_num <= 0 or fps_den <= 0:
        raise ValueError(f"Invalid rational fps: {fps_num}/{fps_den}")
    q = Fraction(str(sec)) * Fraction(fps_num, fps_den)
    if mode == "floor":
        return math.floor(q)
    if mode == "ceil":
        return math.ceil(q)
    # Half-up deterministic rounding
    return int(math.floor(q + Fraction(1, 2)))


def frame_to_seconds(frame: int, fps_num: int, fps_den: int = 1) -> Fraction:
    if not is_strict_int(frame) or frame < 0:
        raise ValueError(f"Invalid frame index: {frame!r}")
    if fps_num <= 0 or fps_den <= 0:
        raise ValueError(f"Invalid rational fps: {fps_num}/{fps_den}")
    return Fraction(frame * fps_den, fps_num)


def validate_frame_clock(
    fps_num: int,
    fps_den: int,
    total_frames: int,
    intervals: list[tuple[str, int, int]],
    *,
    require_contiguous_cover: bool = False,
) -> list[dict[str, str]]:
    """Validate rational fps and half-open [start, end) integer frame intervals."""
    errors: list[ValidationErrorItem] = []
    if not is_strict_int(fps_num) or fps_num <= 0:
        errors.append(ValidationErrorItem("CLOCK_FPS_NUM", "$.output_spec.fps_num", "fps_num must be positive int", str(fps_num)))
    if not is_strict_int(fps_den) or fps_den <= 0:
        errors.append(ValidationErrorItem("CLOCK_FPS_DEN", "$.output_spec.fps_den", "fps_den must be positive int", str(fps_den)))
    if not is_strict_int(total_frames) or total_frames <= 0:
        errors.append(ValidationErrorItem("CLOCK_TOTAL", "$.output_spec.total_frames", "total_frames must be positive int", str(total_frames)))
        return [e.to_dict() for e in errors]

    valid_spans: list[tuple[int, int, str]] = []
    for label, start, end in intervals:
        if not is_strict_int(start) or not is_strict_int(end):
            errors.append(ValidationErrorItem("CLOCK_NONINT", label, "Frame bounds must be strict integers", f"[{start}, {end})"))
            continue
        if start < 0 or end <= start:
            errors.append(ValidationErrorItem("CLOCK_RANGE", label, "Half-open interval [start, end) requires 0 <= start < end", f"[{start}, {end})"))
            continue
        if end > total_frames:
            errors.append(ValidationErrorItem("CLOCK_OOB", label, f"Interval end {end} exceeds total_frames {total_frames}", f"[{start}, {end})"))
            continue
        valid_spans.append((start, end, label))

    if require_contiguous_cover and valid_spans:
        valid_spans.sort(key=lambda x: (x[0], x[1]))
        if valid_spans[0][0] != 0:
            errors.append(ValidationErrorItem("CLOCK_START_GAP", valid_spans[0][2], f"Primary timeline starts at frame {valid_spans[0][0]} instead of 0"))
        for idx in range(len(valid_spans) - 1):
            s1, e1, l1 = valid_spans[idx]
            s2, e2, l2 = valid_spans[idx + 1]
            if e1 < s2:
                errors.append(ValidationErrorItem("CLOCK_GAP", f"{l1}->{l2}", f"Unplanned timeline gap [{e1}, {s2})"))
            elif e1 > s2:
                errors.append(ValidationErrorItem("CLOCK_OVERLAP", f"{l1}->{l2}", f"Primary beat overlap [{s2}, {e1})"))
        if valid_spans[-1][1] != total_frames:
            errors.append(
                ValidationErrorItem("CLOCK_END_GAP", valid_spans[-1][2], f"Primary timeline ends at {valid_spans[-1][1]} != total_frames {total_frames}")
            )

    errors.sort(key=lambda e: (e.location, e.code, e.message))
    return [e.to_dict() for e in errors]


PRESENTER_MODES = frozenset({"LEGACY_DYNAMIC", "FIXED_PIP", "ADAPTIVE_PIP", "DYNAMIC_HYBRID", "FACELESS"})
PRESENTER_VISIBILITIES = frozenset({"ALWAYS_VISIBLE", "EDITORIAL_ALLOWED"})
PRESENTER_ANCHORS = frozenset({"TOP_LEFT", "TOP_RIGHT", "BOTTOM_LEFT", "BOTTOM_RIGHT", "CUSTOM"})
PRESENTER_SHAPES = frozenset({"ROUNDED_RECT", "CIRCLE"})
PRESENTER_ASPECTS = frozenset({"SOURCE_AWARE", "PORTRAIT_3_4", "SQUARE_1_1", "PORTRAIT_9_16", "LANDSCAPE_16_9"})


def validate_presenter_policy(
    policy: dict[str, Any] | None,
    *,
    brief_mode: str = "talking_head",
    has_presenter_source: bool = True,
) -> list[dict[str, str]]:
    """Fail-closed semantic & conflict validator for explicit presenter_policy contracts."""
    if policy is None:
        return []
    errors: list[ValidationErrorItem] = []
    if not isinstance(policy, dict):
        return [ValidationErrorItem("PRESENTER_POLICY_TYPE", "$.presenter_policy", "presenter_policy must be an object").to_dict()]

    mode = policy.get("mode", "LEGACY_DYNAMIC")
    vis = policy.get("visibility", "ALWAYS_VISIBLE" if mode == "FIXED_PIP" else "EDITORIAL_ALLOWED")
    anchor = policy.get("anchor", "BOTTOM_LEFT")
    shape = policy.get("shape", "ROUNDED_RECT")
    aspect = policy.get("aspect_ratio", "SOURCE_AWARE")

    if mode not in PRESENTER_MODES:
        errors.append(ValidationErrorItem("PRESENTER_UNKNOWN_MODE", "$.presenter_policy.mode", f"Unknown presenter mode {mode!r}"))
    if vis not in PRESENTER_VISIBILITIES:
        errors.append(ValidationErrorItem("PRESENTER_UNKNOWN_VISIBILITY", "$.presenter_policy.visibility", f"Unknown visibility {vis!r}"))
    if anchor not in PRESENTER_ANCHORS:
        errors.append(ValidationErrorItem("PRESENTER_UNKNOWN_ANCHOR", "$.presenter_policy.anchor", f"Unknown anchor {anchor!r}"))
    if shape not in PRESENTER_SHAPES:
        errors.append(ValidationErrorItem("PRESENTER_UNKNOWN_SHAPE", "$.presenter_policy.shape", f"Unknown shape {shape!r}"))
    if aspect not in PRESENTER_ASPECTS:
        errors.append(ValidationErrorItem("PRESENTER_UNKNOWN_ASPECT", "$.presenter_policy.aspect_ratio", f"Unknown aspect_ratio {aspect!r}"))

    wr = policy.get("width_ratio", 0.30)
    min_wr = policy.get("min_width_ratio", 0.24)
    max_wr = policy.get("max_width_ratio", 0.36)
    if not (is_strict_number(wr) and is_strict_number(min_wr) and is_strict_number(max_wr)):
        errors.append(ValidationErrorItem("PRESENTER_WIDTH_TYPE", "$.presenter_policy.width_ratio", "width_ratio bounds must be finite numbers"))
    elif min_wr > max_wr or wr < min_wr - 1e-6 or wr > max_wr + 1e-6:
        errors.append(ValidationErrorItem("PRESENTER_WIDTH_RANGE", "$.presenter_policy.width_ratio", f"width_ratio={wr} outside [{min_wr}, {max_wr}]"))

    if anchor == "CUSTOM":
        cb = policy.get("custom_box")
        if not (isinstance(cb, list) and len(cb) == 4 and all(is_strict_number(x) for x in cb)):
            errors.append(ValidationErrorItem("PRESENTER_CUSTOM_BOX_MISSING", "$.presenter_policy.custom_box", "anchor='CUSTOM' requires valid [x,y,w,h] custom_box"))
        else:
            cx, cy, cw, ch = [float(x) for x in cb]
            if cx < 0 or cy < 0 or cw < 0.14 or ch < 0.12 or cx + cw > 1.0 + 1e-6 or cy + ch > 1.0 + 1e-6:
                errors.append(ValidationErrorItem("PRESENTER_CUSTOM_BOX_OOB", "$.presenter_policy.custom_box", f"custom_box {cb} out of bounds or too small"))

    # Policy conflict checks
    if mode == "FACELESS":
        if vis == "ALWAYS_VISIBLE":
            errors.append(ValidationErrorItem("PRESENTER_CONFLICT", "$.presenter_policy.visibility", "FACELESS mode cannot require ALWAYS_VISIBLE"))
    if mode == "FIXED_PIP":
        if policy.get("allow_fullscreen") is True:
            errors.append(ValidationErrorItem("PRESENTER_CONFLICT_FULLSCREEN", "$.presenter_policy.allow_fullscreen", "FIXED_PIP forbids allow_fullscreen=true"))
        if policy.get("allow_anchor_switch") is True:
            errors.append(ValidationErrorItem("PRESENTER_CONFLICT_ANCHOR_SWITCH", "$.presenter_policy.allow_anchor_switch", "FIXED_PIP forbids allow_anchor_switch=true"))
        if vis == "ALWAYS_VISIBLE" and policy.get("allow_hide") is True:
            errors.append(ValidationErrorItem("PRESENTER_CONFLICT_HIDE", "$.presenter_policy.allow_hide", "ALWAYS_VISIBLE forbids allow_hide=true"))
        if brief_mode == "faceless" or not has_presenter_source:
            errors.append(ValidationErrorItem("BLOCKED_PRESENTER_SOURCE", "$.presenter_policy.mode", "FIXED_PIP requires a continuous usable presenter source (no silent faceless fallback)"))
    elif vis == "ALWAYS_VISIBLE":
        if policy.get("allow_hide") is True:
            errors.append(ValidationErrorItem("PRESENTER_CONFLICT_HIDE", "$.presenter_policy.allow_hide", "ALWAYS_VISIBLE forbids allow_hide=true"))
        if brief_mode == "faceless" or not has_presenter_source:
            errors.append(ValidationErrorItem("BLOCKED_PRESENTER_SOURCE", "$.presenter_policy.visibility", "ALWAYS_VISIBLE requires a usable presenter source"))

    errors.sort(key=lambda e: (e.location, e.code, e.message))
    return [e.to_dict() for e in errors]


def validate_references(
    shotbook: dict[str, Any],
    inventory: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Validate duplicate IDs, dangling beat/asset/capability references, rights enforcement, bboxes, presenter policy, and frame clock."""
    errors: list[ValidationErrorItem] = []
    for se in validate_schema(shotbook, "shotbook_v2"):
        errors.append(ValidationErrorItem(se["code"], se["location"], se["message"], se["evidence"]))
    if not isinstance(shotbook, dict) or not isinstance(shotbook.get("output_spec"), dict) or not isinstance(shotbook.get("beats"), list):
        return [e.to_dict() for e in errors]

    if shotbook.get("presenter_policy") is not None:
        has_pres = True
        if inventory and isinstance(inventory.get("assets"), list):
            has_pres = any(a.get("usable_in_render") and a.get("kind") == "a_roll" for a in inventory["assets"])
        for pe in validate_presenter_policy(
            shotbook.get("presenter_policy"),
            brief_mode=str(shotbook.get("mode", "talking_head")),
            has_presenter_source=has_pres,
        ):
            errors.append(ValidationErrorItem(pe["code"], pe["location"], pe["message"], pe["evidence"]))

    spec = shotbook["output_spec"]
    total = spec["total_frames"]

    # Asset rights map
    usable_assets: set[str] = set()
    denied_assets: set[str] = set()
    if inventory and isinstance(inventory.get("assets"), list):
        for a in inventory["assets"]:
            aid = a["asset_id"]
            if a.get("usable_in_render") and a.get("kind") != "analysis_only" and a.get("rights") not in ("ANALYSIS_ONLY", "UNLICENSED"):
                usable_assets.add(aid)
            else:
                denied_assets.add(aid)
    else:
        usable_assets = set(shotbook.get("source_sha_set", {}).keys())

    # Registry map
    known_caps: dict[str, dict[str, Any]] = {}
    if registry and isinstance(registry.get("capabilities"), list):
        for cap in registry["capabilities"]:
            known_caps[cap["capability_id"]] = cap

    # Duplicate IDs + beat intervals
    beat_ids: set[str] = set()
    beat_intervals: list[tuple[str, int, int]] = []
    for idx, b in enumerate(shotbook["beats"]):
        bid = b["beat_id"]
        loc = f"$.beats[{idx}]"
        if bid in beat_ids:
            errors.append(ValidationErrorItem("DUP_BEAT_ID", loc, f"Duplicate beat_id {bid!r}"))
        beat_ids.add(bid)
        beat_intervals.append((f"{loc}({bid})", b["start_frame"], b["end_frame"]))
        for ref in b.get("content_refs", []):
            if ref in denied_assets:
                errors.append(ValidationErrorItem("RIGHTS_DENIED_ASSET", f"{loc}.content_refs", f"ANALYSIS_ONLY or unlicensed asset referenced: {ref!r}"))
            elif ref not in usable_assets:
                errors.append(ValidationErrorItem("DANGLING_ASSET", f"{loc}.content_refs", f"Unknown asset_ref: {ref!r}"))

    for err in validate_frame_clock(spec["fps_num"], spec["fps_den"], total, beat_intervals, require_contiguous_cover=True):
        errors.append(ValidationErrorItem(err["code"], err["location"], err["message"], err["evidence"]))

    # Visual events
    ev_ids: set[str] = set()
    ev_intervals: list[tuple[str, int, int]] = []
    for idx, ev in enumerate(shotbook["visual_events"]):
        eid = ev["event_id"]
        loc = f"$.visual_events[{idx}]"
        if eid in ev_ids:
            errors.append(ValidationErrorItem("DUP_EVENT_ID", loc, f"Duplicate event_id {eid!r}"))
        ev_ids.add(eid)
        if ev["beat_id"] not in beat_ids:
            errors.append(ValidationErrorItem("DANGLING_BEAT", f"{loc}.beat_id", f"Unknown beat_id {ev['beat_id']!r}"))
        ev_intervals.append((f"{loc}({eid})", ev["start_frame"], ev["end_frame"]))
        # Safe layer_id
        try:
            sanitize_identifier(ev["layer_id"])
        except ValueError as ex:
            errors.append(ValidationErrorItem("UNSAFE_LAYER_ID", f"{loc}.layer_id", str(ex)))
        aref = ev.get("asset_ref")
        if aref is not None:
            if aref in denied_assets:
                errors.append(ValidationErrorItem("RIGHTS_DENIED_ASSET", f"{loc}.asset_ref", f"ANALYSIS_ONLY/unlicensed asset {aref!r} forbidden in visual event"))
            elif aref not in usable_assets:
                errors.append(ValidationErrorItem("DANGLING_ASSET", f"{loc}.asset_ref", f"Unknown asset_ref {aref!r}"))
        if known_caps:
            cap = known_caps.get(ev["capability_id"])
            if not cap:
                errors.append(ValidationErrorItem("UNKNOWN_CAPABILITY", f"{loc}.capability_id", f"Unknown capability_id {ev['capability_id']!r}"))
            elif cap.get("availability") == "UNAVAILABLE":
                errors.append(ValidationErrorItem("UNAVAILABLE_CAPABILITY", f"{loc}.capability_id", f"Capability {ev['capability_id']!r} is UNAVAILABLE"))
        bx, by, bw, bh = ev["normalized_bbox"]
        if bw <= 0 or bh <= 0 or bx + bw > 1.25 or by + bh > 1.25 or bx < -0.25 or by < -0.25:
            errors.append(ValidationErrorItem("INVALID_BBOX", f"{loc}.normalized_bbox", f"Invalid normalized bbox {ev['normalized_bbox']}"))

    for err in validate_frame_clock(spec["fps_num"], spec["fps_den"], total, ev_intervals, require_contiguous_cover=False):
        errors.append(ValidationErrorItem(err["code"], err["location"], err["message"], err["evidence"]))

    # Camera events
    cam_ids: set[str] = set()
    cam_intervals: list[tuple[str, int, int]] = []
    for idx, c in enumerate(shotbook["camera_events"]):
        cid = c["camera_id"]
        loc = f"$.camera_events[{idx}]"
        if cid in cam_ids:
            errors.append(ValidationErrorItem("DUP_CAMERA_ID", loc, f"Duplicate camera_id {cid!r}"))
        cam_ids.add(cid)
        if c["beat_id"] not in beat_ids:
            errors.append(ValidationErrorItem("DANGLING_BEAT", f"{loc}.beat_id", f"Unknown beat_id {c['beat_id']!r}"))
        cam_intervals.append((f"{loc}({cid})", c["start_frame"], c["end_frame"]))
    for err in validate_frame_clock(spec["fps_num"], spec["fps_den"], total, cam_intervals, require_contiguous_cover=False):
        errors.append(ValidationErrorItem(err["code"], err["location"], err["message"], err["evidence"]))

    # Transitions
    tr_ids: set[str] = set()
    for idx, tr in enumerate(shotbook["transitions"]):
        tid = tr["transition_id"]
        loc = f"$.transitions[{idx}]"
        if tid in tr_ids:
            errors.append(ValidationErrorItem("DUP_TRANSITION_ID", loc, f"Duplicate transition_id {tid!r}"))
        tr_ids.add(tid)
        if tr["from_beat"] not in beat_ids:
            errors.append(ValidationErrorItem("DANGLING_BEAT", f"{loc}.from_beat", f"Unknown from_beat {tr['from_beat']!r}"))
        if tr["to_beat"] not in beat_ids:
            errors.append(ValidationErrorItem("DANGLING_BEAT", f"{loc}.to_beat", f"Unknown to_beat {tr['to_beat']!r}"))
        ov0, ov1 = tr["overlap_range"]
        if ov0 > ov1 or ov1 > total:
            errors.append(ValidationErrorItem("INVALID_OVERLAP", f"{loc}.overlap_range", f"Invalid overlap_range {tr['overlap_range']}"))

    # Text events
    txt_ids: set[str] = set()
    txt_intervals: list[tuple[str, int, int]] = []
    for idx, tx in enumerate(shotbook["text_events"]):
        xid = tx["text_id"]
        loc = f"$.text_events[{idx}]"
        if xid in txt_ids:
            errors.append(ValidationErrorItem("DUP_TEXT_ID", loc, f"Duplicate text_id {xid!r}"))
        txt_ids.add(xid)
        if tx["beat_id"] not in beat_ids:
            errors.append(ValidationErrorItem("DANGLING_BEAT", f"{loc}.beat_id", f"Unknown beat_id {tx['beat_id']!r}"))
        txt_intervals.append((f"{loc}({xid})", tx["start_frame"], tx["end_frame"]))
    for err in validate_frame_clock(spec["fps_num"], spec["fps_den"], total, txt_intervals, require_contiguous_cover=False):
        errors.append(ValidationErrorItem(err["code"], err["location"], err["message"], err["evidence"]))

    # Audio cues
    cue_ids: set[str] = set()
    aud_intervals: list[tuple[str, int, int]] = []
    for idx, cu in enumerate(shotbook["audio_cues"]):
        aid_c = cu["cue_id"]
        loc = f"$.audio_cues[{idx}]"
        if aid_c in cue_ids:
            errors.append(ValidationErrorItem("DUP_AUDIO_ID", loc, f"Duplicate cue_id {aid_c!r}"))
        cue_ids.add(aid_c)
        aref = cu.get("asset_ref")
        if aref is not None:
            if aref in denied_assets:
                errors.append(ValidationErrorItem("RIGHTS_DENIED_ASSET", f"{loc}.asset_ref", f"ANALYSIS_ONLY/unlicensed asset {aref!r} forbidden in audio cue"))
            elif aref not in usable_assets:
                errors.append(ValidationErrorItem("DANGLING_ASSET", f"{loc}.asset_ref", f"Unknown asset_ref {aref!r}"))
        if cu.get("license_ref") in ("UNLICENSED", "ANALYSIS_ONLY"):
            errors.append(ValidationErrorItem("UNLICENSED_AUDIO", f"{loc}.license_ref", f"Forbidden audio license_ref {cu.get('license_ref')!r}"))
        aud_intervals.append((f"{loc}({aid_c})", cu["frame_start"], cu["frame_end"]))
    for err in validate_frame_clock(spec["fps_num"], spec["fps_den"], total, aud_intervals, require_contiguous_cover=False):
        errors.append(ValidationErrorItem(err["code"], err["location"], err["message"], err["evidence"]))

    # Claim evidence
    for idx, clm in enumerate(shotbook.get("claim_evidence", [])):
        if clm.get("verification_status") == "UNVERIFIED_BLOCKED" and clm.get("allowed_display") is True:
            errors.append(ValidationErrorItem("UNVERIFIED_CLAIM_DISPLAY", f"$.claim_evidence[{idx}]", "Unverified claim cannot have allowed_display=true"))

    errors.sort(key=lambda e: (e.location, e.code, e.message))
    return [e.to_dict() for e in errors]
