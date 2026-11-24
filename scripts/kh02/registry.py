"""KH-02 Capability Registry: pinned HyperFrames catalog inspection, availability separation, parameter validation & fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .contracts import hash_artifact, is_strict_number

# Core verified capabilities grounded in pinned HyperFrames@0.9.86 catalog + SeeCut local hf-hw + CSS/GSAP
BUILTIN_CAPABILITIES: list[dict[str, Any]] = [
    {
        "capability_id": "hf.spring_pop",
        "catalog_name": "spring-pop",
        "purpose": "Tactile spring-overshoot card/badge entrance synced to spoken keyword",
        "engine_support": "hyperframes_gsap_dom",
        "availability": "LOCAL_VERIFIED",
        "asset_requirements": [],
        "license": "Apache-2.0 (HyperFrames) + GreenSock Standard License",
        "aspect_constraints": ["9:16", "16:9", "1:1", "4:5"],
        "params_schema": {"scale_from": [0.3, 0.95], "overshoot": [1.5, 3.0]},
        "fps_compatible": True,
        "min_duration_sec": 0.4,
        "max_duration_sec": 30.0,
        "forbidden_combinations": ["minimal_documentary_overshoot"],
        "alpha_supported": True,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestMotionAndCompiler::test_t18_gsap_deterministic_seek",
        "fallback_id": "local.clean_fade_slide",
    },
    {
        "capability_id": "hf.stagger_cascade",
        "catalog_name": "stagger-cascade",
        "purpose": "Sequential multi-item list or timeline step reveal with 80-120ms semantic stagger",
        "engine_support": "hyperframes_gsap_dom",
        "availability": "LOCAL_VERIFIED",
        "asset_requirements": [],
        "license": "Apache-2.0 (HyperFrames)",
        "aspect_constraints": ["9:16", "16:9", "1:1", "4:5"],
        "params_schema": {"stagger_sec": [0.05, 0.40], "max_items": [1, 6]},
        "fps_compatible": True,
        "min_duration_sec": 0.8,
        "max_duration_sec": 45.0,
        "forbidden_combinations": [],
        "alpha_supported": True,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestMotionAndCompiler::test_t20_motion_stagger_settle",
        "fallback_id": "local.clean_fade_slide",
    },
    {
        "capability_id": "hf.badge_pop",
        "catalog_name": "badge-pop",
        "purpose": "Compact semantic tag, pill, or verdict stamp anchored to spoken keyword",
        "engine_support": "hyperframes_gsap_dom",
        "availability": "LOCAL_VERIFIED",
        "asset_requirements": [],
        "license": "Apache-2.0 (HyperFrames)",
        "aspect_constraints": ["9:16", "16:9", "1:1", "4:5"],
        "params_schema": {"scale_from": [0.4, 0.9]},
        "fps_compatible": True,
        "min_duration_sec": 0.4,
        "max_duration_sec": 20.0,
        "forbidden_combinations": [],
        "alpha_supported": True,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestMotionAndCompiler::test_t18_gsap_deterministic_seek",
        "fallback_id": "local.clean_fade_slide",
    },
    {
        "capability_id": "hf.card_resize",
        "catalog_name": "card-resize",
        "purpose": "Evidence frame or PIP container smooth resize/reframe with continuous anchor",
        "engine_support": "hyperframes_gsap_dom",
        "availability": "LOCAL_VERIFIED",
        "asset_requirements": ["verified_media_or_footage"],
        "license": "Apache-2.0 (HyperFrames)",
        "aspect_constraints": ["9:16", "16:9", "1:1", "4:5"],
        "params_schema": {"scale_from": [0.5, 1.2], "scale_to": [0.5, 1.35]},
        "fps_compatible": True,
        "min_duration_sec": 0.5,
        "max_duration_sec": 60.0,
        "forbidden_combinations": [],
        "alpha_supported": True,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestCameraAndTransition::test_t19_camera_constraints",
        "fallback_id": "local.clean_fade_slide",
    },
    {
        "capability_id": "hf.comparison_split",
        "catalog_name": "comparison-split",
        "purpose": "Two-panel evidence comparison or asymmetric editorial split-screen",
        "engine_support": "hyperframes_gsap_dom",
        "availability": "LOCAL_VERIFIED",
        "asset_requirements": [],
        "license": "Apache-2.0 (HyperFrames)",
        "aspect_constraints": ["9:16", "16:9", "1:1", "4:5"],
        "params_schema": {"split_ratio": [0.25, 0.75]},
        "fps_compatible": True,
        "min_duration_sec": 1.0,
        "max_duration_sec": 60.0,
        "forbidden_combinations": [],
        "alpha_supported": True,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestRegistryAndPlanner::test_t17_registry_validation",
        "fallback_id": "local.editorial_panel",
    },
    {
        "capability_id": "hf.notes_reveal",
        "catalog_name": "notes-reveal",
        "purpose": "Progressive editorial callout card / structured key takeaways panel",
        "engine_support": "hyperframes_gsap_dom",
        "availability": "LOCAL_VERIFIED",
        "asset_requirements": [],
        "license": "Apache-2.0 (HyperFrames)",
        "aspect_constraints": ["9:16", "16:9", "1:1", "4:5"],
        "params_schema": {},
        "fps_compatible": True,
        "min_duration_sec": 0.8,
        "max_duration_sec": 45.0,
        "forbidden_combinations": [],
        "alpha_supported": True,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestRegistryAndPlanner::test_t17_registry_validation",
        "fallback_id": "local.editorial_panel",
    },
    {
        "capability_id": "hf.code_snippet",
        "catalog_name": "code-snippet-basic",
        "purpose": "Readable syntax/command callout window for software demos & tutorials",
        "engine_support": "hyperframes_gsap_dom",
        "availability": "LOCAL_VERIFIED",
        "asset_requirements": [],
        "license": "Apache-2.0 (HyperFrames)",
        "aspect_constraints": ["9:16", "16:9", "1:1"],
        "params_schema": {"max_lines": [1, 10]},
        "fps_compatible": True,
        "min_duration_sec": 1.0,
        "max_duration_sec": 60.0,
        "forbidden_combinations": [],
        "alpha_supported": True,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestRegistryAndPlanner::test_t17_registry_validation",
        "fallback_id": "local.editorial_panel",
    },
    {
        "capability_id": "hf_hw.callout_circle",
        "catalog_name": "hw-callout-circle",
        "purpose": "Hand-drawn SVG callout circle anchored onto target text/element",
        "engine_support": "hf_hw_svg",
        "availability": "LOCAL_VERIFIED",
        "asset_requirements": ["assets/hf-hw/hf-hw.js"],
        "license": "Apache-2.0 (HeyGen HyperFrames hf-hw)",
        "aspect_constraints": ["9:16", "16:9", "1:1", "4:5"],
        "params_schema": {},
        "fps_compatible": True,
        "min_duration_sec": 0.4,
        "max_duration_sec": 20.0,
        "forbidden_combinations": [],
        "alpha_supported": True,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestRegistryAndPlanner::test_t17_registry_validation",
        "fallback_id": "local.clean_fade_slide",
    },
    {
        "capability_id": "hf_hw.underline",
        "catalog_name": "hw-underline",
        "purpose": "Hand-drawn SVG underline with centered anchor wrapper (passes geom-check)",
        "engine_support": "hf_hw_svg",
        "availability": "LOCAL_VERIFIED",
        "asset_requirements": ["assets/hf-hw/hf-hw.js"],
        "license": "Apache-2.0 (HeyGen HyperFrames hf-hw)",
        "aspect_constraints": ["9:16", "16:9", "1:1", "4:5"],
        "params_schema": {},
        "fps_compatible": True,
        "min_duration_sec": 0.4,
        "max_duration_sec": 20.0,
        "forbidden_combinations": [],
        "alpha_supported": True,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestRegistryAndPlanner::test_t17_registry_validation",
        "fallback_id": "local.clean_fade_slide",
    },
    {
        "capability_id": "hf_hw.arrow",
        "catalog_name": "hw-arrow",
        "purpose": "Hand-drawn SVG directional pointer toward key visual proof",
        "engine_support": "hf_hw_svg",
        "availability": "LOCAL_VERIFIED",
        "asset_requirements": ["assets/hf-hw/hf-hw.js"],
        "license": "Apache-2.0 (HeyGen HyperFrames hf-hw)",
        "aspect_constraints": ["9:16", "16:9", "1:1", "4:5"],
        "params_schema": {},
        "fps_compatible": True,
        "min_duration_sec": 0.4,
        "max_duration_sec": 20.0,
        "forbidden_combinations": [],
        "alpha_supported": True,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestRegistryAndPlanner::test_t17_registry_validation",
        "fallback_id": "local.clean_fade_slide",
    },
    {
        "capability_id": "local.editorial_panel",
        "catalog_name": None,
        "purpose": "High-contrast local CSS/GSAP editorial card, step rail, or documentary lower-third",
        "engine_support": "local_css_gsap",
        "availability": "LOCAL_VERIFIED",
        "asset_requirements": [],
        "license": "PolyForm Noncommercial 1.0.0 (seecut-ai)",
        "aspect_constraints": ["9:16", "16:9", "1:1", "4:5"],
        "params_schema": {},
        "fps_compatible": True,
        "min_duration_sec": 0.3,
        "max_duration_sec": 120.0,
        "forbidden_combinations": [],
        "alpha_supported": True,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestMotionAndCompiler::test_t18_gsap_deterministic_seek",
        "fallback_id": None,
    },
    {
        "capability_id": "local.clean_fade_slide",
        "catalog_name": None,
        "purpose": "Restrained opacity + transform settle without bounce overshoot (serious/documentary/fallback)",
        "engine_support": "local_css_gsap",
        "availability": "LOCAL_VERIFIED",
        "asset_requirements": [],
        "license": "PolyForm Noncommercial 1.0.0 (seecut-ai)",
        "aspect_constraints": ["9:16", "16:9", "1:1", "4:5"],
        "params_schema": {},
        "fps_compatible": True,
        "min_duration_sec": 0.2,
        "max_duration_sec": 120.0,
        "forbidden_combinations": [],
        "alpha_supported": True,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestMotionAndCompiler::test_t18_gsap_deterministic_seek",
        "fallback_id": None,
    },
    {
        "capability_id": "local.progress_spine",
        "catalog_name": None,
        "purpose": "Persistent match-anchor editorial spine bridging visual transitions across beats",
        "engine_support": "local_css_gsap",
        "availability": "LOCAL_VERIFIED",
        "asset_requirements": [],
        "license": "PolyForm Noncommercial 1.0.0 (seecut-ai)",
        "aspect_constraints": ["9:16", "16:9", "1:1", "4:5"],
        "params_schema": {},
        "fps_compatible": True,
        "min_duration_sec": 0.5,
        "max_duration_sec": 600.0,
        "forbidden_combinations": [],
        "alpha_supported": True,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestCameraAndTransition::test_t21_transition_seam",
        "fallback_id": "local.clean_fade_slide",
    },
    {
        "capability_id": "experimental.true_3d_camera",
        "catalog_name": None,
        "purpose": "True 3D perspective camera on flat 2D footage (UNSUPPORTED — do not hallucinate 3D depth)",
        "engine_support": "unsupported",
        "availability": "UNAVAILABLE",
        "asset_requirements": ["3d_depth_scene"],
        "license": "N/A",
        "aspect_constraints": [],
        "params_schema": {},
        "fps_compatible": False,
        "min_duration_sec": 0.0,
        "max_duration_sec": 0.0,
        "forbidden_combinations": [],
        "alpha_supported": False,
        "diagnostic_test": "tests/kh02/test_kh02_suite.py::TestRegistryAndPlanner::test_t17_registry_validation",
        "fallback_id": "hf.card_resize",
    },
]


def _extract_catalog_names(catalog_data: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(catalog_data, list):
        for item in catalog_data:
            if isinstance(item, dict):
                nm = item.get("name") or item.get("id") or item.get("slug")
                if nm:
                    names.add(str(nm))
            elif isinstance(item, str):
                names.add(item)
    elif isinstance(catalog_data, dict):
        for v in catalog_data.values():
            if isinstance(v, list):
                names |= _extract_catalog_names(v)
    return names


def build_registry(
    *,
    catalog_json_path: str | Path | None = None,
    hyperframes_version: str = "0.9.86",
    verified_render_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Build deterministic capability registry from pinned live hyperframes catalog JSON + local verified modules.

    Strictly distinguishes CATALOG_LISTED vs AVAILABLE_VERIFIED (only after real render verification).
    """
    catalog_names: set[str] = set()
    catalog_sha: str | None = None
    total_items = 0

    if catalog_json_path and Path(catalog_json_path).is_file():
        p = Path(catalog_json_path)
        catalog_sha = hash_artifact(p)
        data = json.loads(p.read_text(encoding="utf-8"))
        catalog_names = _extract_catalog_names(data)
        total_items = len(catalog_names)

    verified = verified_render_ids or set()
    caps: list[dict[str, Any]] = []
    for base in BUILTIN_CAPABILITIES:
        c = dict(base)
        cname = c.get("catalog_name")
        if c["engine_support"] == "unsupported":
            c["availability"] = "UNAVAILABLE"
        elif c["capability_id"] in verified:
            c["availability"] = "AVAILABLE_VERIFIED"
        elif cname and catalog_names:
            c["availability"] = "CATALOG_LISTED" if cname in catalog_names else "LOCAL_VERIFIED"
        elif cname and not catalog_names:
            # Never claim AVAILABLE_VERIFIED without render proof
            c["availability"] = "LOCAL_VERIFIED"
        caps.append(c)

    # Also register remaining catalog items honestly as CATALOG_LISTED (not verified!)
    known_cnames = {c.get("catalog_name") for c in caps if c.get("catalog_name")}
    for nm in sorted(catalog_names - known_cnames):
        caps.append({
            "capability_id": f"hf.catalog.{nm}",
            "catalog_name": nm,
            "purpose": f"Pinned HyperFrames catalog entry '{nm}' (unverified until test-rendered)",
            "engine_support": "hyperframes_gsap_dom",
            "availability": "CATALOG_LISTED",
            "asset_requirements": [],
            "license": "Apache-2.0 (HyperFrames)",
            "aspect_constraints": ["9:16", "16:9", "1:1", "4:5"],
            "params_schema": {},
            "fps_compatible": True,
            "min_duration_sec": 0.3,
            "max_duration_sec": 60.0,
            "forbidden_combinations": [],
            "alpha_supported": True,
            "diagnostic_test": "unverified_catalog_entry",
            "fallback_id": "local.clean_fade_slide",
        })

    return {
        "schema_version": "kh02.capability_registry.v1",
        "hyperframes_version": hyperframes_version,
        "catalog_sha256": catalog_sha,
        "catalog_total_items": total_items,
        "capabilities": caps,
    }


def validate_capability(
    registry: dict[str, Any],
    capability_id: str,
    *,
    aspect_ratio: str = "9:16",
    duration_sec: float = 2.0,
    params: dict[str, Any] | None = None,
    profile: str = "editorial_pro",
) -> tuple[bool, list[str]]:
    """Validate capability availability, aspect ratio, duration bounds, profile rules, and parameter ranges."""
    cap = next((c for c in registry.get("capabilities", []) if c["capability_id"] == capability_id), None)
    if cap is None:
        return False, [f"Unknown capability_id: {capability_id!r}"]
    errors: list[str] = []
    if cap["availability"] == "UNAVAILABLE" or cap["engine_support"] == "unsupported":
        errors.append(f"Capability {capability_id!r} is UNAVAILABLE / unsupported")
    if cap["aspect_constraints"] and aspect_ratio not in cap["aspect_constraints"]:
        errors.append(f"Aspect {aspect_ratio!r} unsupported by {capability_id!r} ({cap['aspect_constraints']})")
    if not is_strict_number(duration_sec) or duration_sec < cap["min_duration_sec"] or duration_sec > cap["max_duration_sec"]:
        errors.append(
            f"Duration {duration_sec}s outside [{cap['min_duration_sec']}, {cap['max_duration_sec']}] for {capability_id!r}"
        )
    if profile == "minimal_documentary" and "minimal_documentary_overshoot" in cap.get("forbidden_combinations", []):
        errors.append(f"Capability {capability_id!r} forbidden under profile {profile!r} (no spring overshoot on serious material)")

    pschema = cap.get("params_schema") or {}
    for k, val in (params or {}).items():
        if k in pschema:
            lo, hi = pschema[k]
            if not is_strict_number(val) or val < lo or val > hi:
                errors.append(f"Param {k}={val!r} outside allowed range [{lo}, {hi}] for {capability_id!r}")
    return (len(errors) == 0), errors


def select_capability(
    registry: dict[str, Any],
    requested_id: str,
    *,
    aspect_ratio: str = "9:16",
    duration_sec: float = 2.0,
    params: dict[str, Any] | None = None,
    profile: str = "editorial_pro",
) -> dict[str, Any]:
    """Select requested capability if valid; otherwise follow explicit fallback chain or return BLOCKED."""
    ok, errs = validate_capability(registry, requested_id, aspect_ratio=aspect_ratio, duration_sec=duration_sec, params=params, profile=profile)
    if ok:
        cap = next(c for c in registry["capabilities"] if c["capability_id"] == requested_id)
        return {
            "selected_id": requested_id,
            "availability": cap["availability"],
            "fallback_used": False,
            "reason": "Requested capability validated.",
            "errors": [],
        }

    cap = next((c for c in registry.get("capabilities", []) if c["capability_id"] == requested_id), None)
    fb = cap.get("fallback_id") if cap else "local.clean_fade_slide"
    visited = {requested_id}
    while fb and fb not in visited:
        visited.add(fb)
        fb_ok, _ = validate_capability(registry, fb, aspect_ratio=aspect_ratio, duration_sec=duration_sec, params=None, profile=profile)
        if fb_ok:
            fb_cap = next(c for c in registry["capabilities"] if c["capability_id"] == fb)
            return {
                "selected_id": fb,
                "availability": fb_cap["availability"],
                "fallback_used": True,
                "requested_id": requested_id,
                "reason": f"Fallback from {requested_id} due to: {'; '.join(errs)}",
                "errors": errs,
            }
        fb_cap = next((c for c in registry.get("capabilities", []) if c["capability_id"] == fb), None)
        fb = fb_cap.get("fallback_id") if fb_cap else None

    return {
        "selected_id": None,
        "availability": "BLOCKED",
        "fallback_used": False,
        "requested_id": requested_id,
        "reason": f"No valid capability or fallback available: {'; '.join(errs)}",
        "errors": errs,
    }
