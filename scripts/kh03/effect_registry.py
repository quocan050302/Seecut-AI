"""DX01-01 — Typed, Versioned Offline Universal Effect Registry & Capability Discovery.

Catalogs 26 genuinely callable, frame-driven DX-01 motion & footage effects across ALL 8 visual
motion families plus all 11 preserved R4.3 baseline/semantic components, with:
- Strict fail-closed validation (finite numbers, bounds, aspect constraints, duration range,
  source references, z-order, forbidden arbitrary expressions / executable strings / internal labels).
- Explicit lifecycle statuses: AVAILABLE, VERIFIED_RENDER, DISABLED, BLOCKED.
- Cached search, filter, and deterministic ranking by semantic intent, media affordances,
  aspect ratio, layout constraints, caption policy, and compute budget.
- Honest license/provenance tracking per effect (ZERO YeJe-cpu/SeeCut PolyForm code/assets).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REGISTRY_SCHEMA_VERSION = "kh03.dx01.effect_registry.v1"
EFFECT_REGISTRY_VERSION = "dx01.1.0.0"

MOTION_FAMILIES: tuple[str, ...] = (
    "typography",
    "geometry_path",
    "footage_motion",
    "transitions",
    "depth_layers",
    "numerical_data",
    "evidence",
    "atmosphere_accent",
)

VALID_EFFECT_STATUSES = frozenset({"AVAILABLE", "VERIFIED_RENDER", "DISABLED", "BLOCKED"})
VALID_COST_TIERS = frozenset({"low", "medium", "high"})

_FORBIDDEN_EXPR_RE = re.compile(
    r"(?:<\s*script|javascript:|eval\s*\(|new\s+Function|__import__|os\.system|subprocess\.|http://|https://)",
    re.IGNORECASE,
)
_INTERNAL_LABEL_RE = re.compile(
    r"\b(?:SK-0[1-5]|TODO|FIXME|DEBUG_[A-Z0-9_]+|PLACEHOLDER|LOREM IPSUM|DUMMY_TEXT)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EffectDescriptor:
    effect_id: str
    version: str
    family: str
    backend: str  # "remotion" | "footage_engine" | "hybrid" | "independent_pil"
    semantic_roles: tuple[str, ...]
    supported_media: tuple[str, ...]
    aspect_constraints: tuple[str, ...] = ("9:16", "16:9", "1:1", "4:5")
    min_duration_sec: float = 0.25
    max_duration_sec: float = 30.0
    cost_tier: str = "medium"  # low | medium | high
    required_capabilities: tuple[str, ...] = ()
    safe_area_mode: str = "negative_space"  # negative_space | full_canvas | footage_transform | boundary
    temporal_handles_frames: int = 0
    max_simultaneous_layers: int = 2
    deterministic_seed_default: int = 20261126
    license_provenance: str = "DX01_CLEAN_ROOM_ORIGINAL"
    fallback_id: str | None = None
    non_card_form: bool = True
    requires_caption_policy: bool = False
    is_legacy_component: bool = False
    param_bounds: dict[str, tuple[float, float]] = field(default_factory=dict)
    required_params: tuple[str, ...] = ()
    description: str = ""

    def to_dict(self, *, status: str = "AVAILABLE", verify_receipt: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "effect_id": self.effect_id,
            "version": self.version,
            "family": self.family,
            "backend": self.backend,
            "semantic_roles": list(self.semantic_roles),
            "supported_media": list(self.supported_media),
            "aspect_constraints": list(self.aspect_constraints),
            "duration_range_sec": [self.min_duration_sec, self.max_duration_sec],
            "cost_tier": self.cost_tier,
            "required_capabilities": list(self.required_capabilities),
            "safe_area_mode": self.safe_area_mode,
            "temporal_handles_frames": self.temporal_handles_frames,
            "max_simultaneous_layers": self.max_simultaneous_layers,
            "deterministic_seed_default": self.deterministic_seed_default,
            "license_provenance": self.license_provenance,
            "fallback_id": self.fallback_id,
            "non_card_form": self.non_card_form,
            "requires_caption_policy": self.requires_caption_policy,
            "is_legacy_component": self.is_legacy_component,
            "param_bounds": {k: [v[0], v[1]] for k, v in self.param_bounds.items()},
            "required_params": list(self.required_params),
            "description": self.description,
            "status": status,
            "verified_render_receipt": verify_receipt,
        }


# ---------------------------------------------------------------------------
# 26 Curated DX-01 Effects Across All 8 Motion Families
# ---------------------------------------------------------------------------

DX01_EFFECT_DESCRIPTORS: tuple[EffectDescriptor, ...] = (
    # --- Family 1: typography (4 effects) ---
    EffectDescriptor(
        effect_id="dx01.typo.kinetic_headline",
        version="1.0.0",
        family="typography",
        backend="remotion",
        semantic_roles=("hook", "claim", "emphasis", "payoff", "emotion"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.45,
        max_duration_sec=10.0,
        cost_tier="medium",
        required_capabilities=("remotion_bridge",),
        license_provenance="MIT_ADAPTED:av/remotion-bits@de35fda8",
        fallback_id="legacy.editorial_hook_banner",
        param_bounds={"font_size_scale": (0.5, 2.0), "x_norm": (0.0, 0.9), "y_norm": (0.0, 0.9), "w_norm": (0.15, 1.0)},
        required_params=("title",),
        description="Free-standing mixed bold sans + italic serif headline with word-staggered spring motion (no card box).",
    ),
    EffectDescriptor(
        effect_id="dx01.typo.masked_reveal",
        version="1.0.0",
        family="typography",
        backend="remotion",
        semantic_roles=("claim", "reveal", "orientation", "emphasis", "payoff"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.45,
        max_duration_sec=10.0,
        cost_tier="medium",
        required_capabilities=("remotion_bridge",),
        license_provenance="MIT_ADAPTED:Remocn/remocn@ac1e4081",
        fallback_id="legacy.context_chip",
        param_bounds={"font_size_scale": (0.5, 2.0), "x_norm": (0.0, 0.9), "y_norm": (0.0, 0.9), "w_norm": (0.15, 1.0)},
        required_params=("title",),
        description="Clip-path masked baseline typography reveal with sleek accent rule and zero opaque card box.",
    ),
    EffectDescriptor(
        effect_id="dx01.typo.karaoke_cue",
        version="1.0.0",
        family="typography",
        backend="remotion",
        semantic_roles=("emphasis", "claim", "reveal", "emotion", "payoff"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "mixed"),
        min_duration_sec=0.25,
        max_duration_sec=12.0,
        cost_tier="medium",
        required_capabilities=("remotion_bridge",),
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL",
        fallback_id=None,
        requires_caption_policy=True,
        param_bounds={"font_size_scale": (0.5, 1.8), "x_norm": (0.0, 0.9), "y_norm": (0.05, 0.92)},
        required_params=("word_cues",),
        description="Word-synced 2-5 word creator caption cue mixing bold sans with italic serif emphasis (only when captions enabled).",
    ),
    EffectDescriptor(
        effect_id="dx01.typo.floating_statement",
        version="1.0.0",
        family="typography",
        backend="remotion",
        semantic_roles=("orientation", "emotion", "pause", "claim", "payoff"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.45,
        max_duration_sec=12.0,
        cost_tier="medium",
        required_capabilities=("remotion_bridge",),
        license_provenance="MIT_ADAPTED:Remocn/remocn@ac1e4081",
        fallback_id="legacy.context_chip",
        param_bounds={"font_size_scale": (0.5, 1.8), "y_norm": (0.04, 0.92)},
        required_params=("title",),
        description="Restrained unboxed floating statement in negative space for calm emotional or CTA beats.",
    ),
    # --- Family 2: geometry_path (4 effects) ---
    EffectDescriptor(
        effect_id="dx01.geom.svg_path_draw",
        version="1.0.0",
        family="geometry_path",
        backend="remotion",
        semantic_roles=("emphasis", "process", "cause", "orientation"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.35,
        max_duration_sec=10.0,
        cost_tier="low",
        required_capabilities=("remotion_bridge", "remotion_paths"),
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL+MIT:@remotion/paths",
        fallback_id=None,
        param_bounds={"stroke_width": (2.0, 24.0)},
        description="Progressive vector SVG stroke draw via @remotion/paths evolvePath (curved underline, bracket, polyline).",
    ),
    EffectDescriptor(
        effect_id="dx01.geom.callout_arrow",
        version="1.0.0",
        family="geometry_path",
        backend="remotion",
        semantic_roles=("evidence", "reveal", "cause", "emphasis"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.45,
        max_duration_sec=10.0,
        cost_tier="medium",
        required_capabilities=("remotion_bridge", "remotion_paths"),
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL+MIT:@remotion/paths",
        fallback_id="legacy.evidence_spotlight",
        param_bounds={"stroke_width": (2.0, 20.0)},
        required_params=("from_point", "to_point"),
        description="Curved animated SVG pointer arrow with tangent-aligned arrowhead and free-space label.",
    ),
    EffectDescriptor(
        effect_id="dx01.geom.focus_ring",
        version="1.0.0",
        family="geometry_path",
        backend="remotion",
        semantic_roles=("evidence", "emphasis", "reveal"),
        supported_media=("screencast", "product", "documentary", "talking_head", "faceless", "silent", "mixed"),
        min_duration_sec=0.45,
        max_duration_sec=10.0,
        cost_tier="low",
        required_capabilities=("remotion_bridge", "remotion_paths"),
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL+MIT:@remotion/paths",
        fallback_id="legacy.evidence_spotlight",
        param_bounds={"stroke_width": (2.0, 20.0), "vignette_opacity": (0.0, 0.85)},
        required_params=("focus_box_norm",),
        description="Animated vector highlight outline sweeping cleanly around an authentic UI/subject region.",
    ),
    EffectDescriptor(
        effect_id="dx01.geom.shape_morph",
        version="1.0.0",
        family="geometry_path",
        backend="remotion",
        semantic_roles=("contrast", "process", "reveal", "transition"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.50,
        max_duration_sec=8.0,
        cost_tier="low",
        required_capabilities=("remotion_bridge", "remotion_paths"),
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL+MIT:@remotion/paths",
        fallback_id=None,
        param_bounds={"w_norm": (0.08, 0.65)},
        description="Valid closed-topology SVG morph via @remotion/paths interpolatePath.",
    ),
    # --- Family 3: footage_motion (4 effects) ---
    EffectDescriptor(
        effect_id="dx01.footage.push_zoom",
        version="1.0.0",
        family="footage_motion",
        backend="footage_engine",
        semantic_roles=("emphasis", "emotion", "hook", "reveal", "payoff"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.35,
        max_duration_sec=30.0,
        cost_tier="low",
        safe_area_mode="footage_transform",
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL",
        param_bounds={"scale_start": (1.0, 1.8), "scale_end": (1.0, 1.8), "cx_norm": (0.1, 0.9), "cy_norm": (0.1, 0.9)},
        description="Subject-safe smooth cubic push-in or pull-out crop zoom protecting face and hands.",
    ),
    EffectDescriptor(
        effect_id="dx01.footage.pullback_canvas",
        version="1.0.0",
        family="footage_motion",
        backend="footage_engine",
        semantic_roles=("orientation", "evidence", "contrast", "process", "payoff"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.50,
        max_duration_sec=20.0,
        cost_tier="low",
        safe_area_mode="footage_transform",
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL",
        param_bounds={"scale_start": (0.35, 1.15), "scale_end": (0.35, 1.15), "rotation_deg": (-12.0, 12.0)},
        description="True <1.0 canvas pullback framing footage onto art-directed canvas without distortion.",
    ),
    EffectDescriptor(
        effect_id="dx01.footage.perspective_tilt",
        version="1.0.0",
        family="footage_motion",
        backend="footage_engine",
        semantic_roles=("evidence", "reveal", "product", "emphasis"),
        supported_media=("screencast", "product", "documentary", "faceless", "talking_head", "silent", "mixed"),
        min_duration_sec=0.45,
        max_duration_sec=15.0,
        cost_tier="medium",
        safe_area_mode="footage_transform",
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL",
        param_bounds={"tilt_x_deg": (-16.0, 16.0), "tilt_y_deg": (-16.0, 16.0), "scale": (0.55, 1.15)},
        description="Smooth 2.5D perspective tilt with subtle floating shadow for authentic screen/product shots.",
    ),
    EffectDescriptor(
        effect_id="dx01.footage.exposure_grade",
        version="1.0.0",
        family="footage_motion",
        backend="footage_engine",
        semantic_roles=("emotion", "contrast", "emphasis", "pause", "orientation"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.35,
        max_duration_sec=30.0,
        cost_tier="low",
        safe_area_mode="footage_transform",
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL",
        param_bounds={"exposure_ev": (-0.6, 0.6), "contrast": (0.75, 1.35), "saturation": (0.5, 1.35), "vignette": (0.0, 0.65)},
        description="Restrained exposure, contrast, warmth, and soft edge vignette directly on footage.",
    ),
    # --- Family 4: transitions (4 effects) ---
    EffectDescriptor(
        effect_id="dx01.trans.dual_crossfade",
        version="1.0.0",
        family="transitions",
        backend="footage_engine",
        semantic_roles=("transition", "emotion", "orientation", "contrast"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.12,
        max_duration_sec=1.5,
        cost_tier="low",
        safe_area_mode="boundary",
        temporal_handles_frames=6,
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL",
        param_bounds={"duration_frames": (2.0, 36.0)},
        description="True dual-moving stream crossfade evaluating both outgoing and incoming sources at live PTS.",
    ),
    EffectDescriptor(
        effect_id="dx01.trans.mask_wipe",
        version="1.0.0",
        family="transitions",
        backend="footage_engine",
        semantic_roles=("transition", "reveal", "evidence", "contrast"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.16,
        max_duration_sec=1.2,
        cost_tier="low",
        safe_area_mode="boundary",
        temporal_handles_frames=8,
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL",
        param_bounds={"duration_frames": (3.0, 30.0), "feather_px": (0.0, 120.0)},
        description="Directional or feathered shape mask wipe across two simultaneously moving sources.",
    ),
    EffectDescriptor(
        effect_id="dx01.trans.whip_blur",
        version="1.0.0",
        family="transitions",
        backend="footage_engine",
        semantic_roles=("transition", "emphasis", "contrast", "hook"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.12,
        max_duration_sec=0.65,
        cost_tier="medium",
        safe_area_mode="boundary",
        temporal_handles_frames=6,
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL",
        param_bounds={"duration_frames": (3.0, 16.0), "blur_px": (4.0, 48.0)},
        description="Bounded directional whip-pan blur transition across moving streams (clamped intensity, no strobe).",
    ),
    EffectDescriptor(
        effect_id="dx01.trans.match_reveal",
        version="1.0.0",
        family="transitions",
        backend="footage_engine",
        semantic_roles=("transition", "evidence", "reveal", "process"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.16,
        max_duration_sec=1.0,
        cost_tier="low",
        safe_area_mode="boundary",
        temporal_handles_frames=8,
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL",
        param_bounds={"duration_frames": (4.0, 24.0)},
        description="Motion-matched slide/scale handoff between primary and evidence footage with handle clamping.",
    ),
    # --- Family 5: depth_layers (3 effects) ---
    EffectDescriptor(
        effect_id="dx01.depth.adaptive_split",
        version="1.0.0",
        family="depth_layers",
        backend="footage_engine",
        semantic_roles=("evidence", "contrast", "process", "payoff"),
        supported_media=("talking_head", "screencast", "product", "documentary", "mixed"),
        min_duration_sec=0.65,
        max_duration_sec=20.0,
        cost_tier="medium",
        safe_area_mode="full_canvas",
        max_simultaneous_layers=3,
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL",
        param_bounds={"split_ratio": (0.25, 0.75)},
        description="True N-source multi-layer adaptive split displaying live presenter + live evidence simultaneously.",
    ),
    EffectDescriptor(
        effect_id="dx01.depth.floating_evidence",
        version="1.0.0",
        family="depth_layers",
        backend="footage_engine",
        semantic_roles=("evidence", "reveal", "claim", "payoff"),
        supported_media=("talking_head", "screencast", "product", "documentary", "mixed"),
        min_duration_sec=0.60,
        max_duration_sec=15.0,
        cost_tier="medium",
        safe_area_mode="full_canvas",
        max_simultaneous_layers=3,
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL",
        param_bounds={"scale": (0.3, 0.95), "dim_bg": (0.0, 0.7)},
        description="Unboxed floating evidence footage layer with soft drop shadow over subtly dimmed live primary layer.",
    ),
    EffectDescriptor(
        effect_id="dx01.depth.parallax_stack",
        version="1.0.0",
        family="depth_layers",
        backend="footage_engine",
        semantic_roles=("orientation", "emotion", "reveal", "evidence"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.70,
        max_duration_sec=15.0,
        cost_tier="medium",
        safe_area_mode="full_canvas",
        max_simultaneous_layers=3,
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL",
        param_bounds={"parallax_strength": (0.01, 0.12)},
        description="Multi-layer parallax depth motion across genuinely distinct background and foreground media layers.",
    ),
    # --- Family 6: numerical_data (3 effects) ---
    EffectDescriptor(
        effect_id="dx01.data.kinetic_counter",
        version="1.0.0",
        family="numerical_data",
        backend="remotion",
        semantic_roles=("payoff", "contrast", "reveal", "claim"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.65,
        max_duration_sec=12.0,
        cost_tier="medium",
        required_capabilities=("remotion_bridge",),
        license_provenance="MIT_ADAPTED:Remocn/remocn@ac1e4081",
        fallback_id="legacy.numerical_payoff_hero",
        param_bounds={"count_sec": (0.2, 4.0), "font_size_scale": (0.5, 1.8)},
        required_params=("from_value", "to_value"),
        description="Beat-synced floating numeric delta/payoff counter with exact tabular math and optional qualifier.",
    ),
    EffectDescriptor(
        effect_id="dx01.data.sequential_tally",
        version="1.0.0",
        family="numerical_data",
        backend="remotion",
        semantic_roles=("process", "evidence", "claim", "reveal"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.75,
        max_duration_sec=15.0,
        cost_tier="medium",
        required_capabilities=("remotion_bridge",),
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL",
        fallback_id="legacy.resource_depletion",
        param_bounds={"font_size_scale": (0.5, 1.8)},
        required_params=("items",),
        description="Non-spoiler sequential pill/value tally revealing each unit strictly at its spoken reveal_sec.",
    ),
    EffectDescriptor(
        effect_id="dx01.data.flow_bar",
        version="1.0.0",
        family="numerical_data",
        backend="remotion",
        semantic_roles=("contrast", "process", "payoff", "evidence"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.75,
        max_duration_sec=15.0,
        cost_tier="medium",
        required_capabilities=("remotion_bridge",),
        license_provenance="MIT_ADAPTED:Remocn/remocn@ac1e4081",
        fallback_id="legacy.resource_allocation",
        param_bounds={"w_norm": (0.35, 0.94)},
        required_params=("bars",),
        description="Unit-generic animated bar comparison chart with spring growth and tabular numbers.",
    ),
    # --- Family 7: evidence (2 effects) ---
    EffectDescriptor(
        effect_id="dx01.evidence.ui_spotlight",
        version="1.0.0",
        family="evidence",
        backend="hybrid",
        semantic_roles=("evidence", "reveal", "emphasis", "process"),
        supported_media=("screencast", "product", "documentary", "mixed", "talking_head"),
        min_duration_sec=0.50,
        max_duration_sec=15.0,
        cost_tier="medium",
        required_capabilities=("remotion_bridge", "remotion_paths"),
        license_provenance="DX01_CLEAN_ROOM_ORIGINAL+MIT:@remotion/paths",
        fallback_id="legacy.evidence_spotlight",
        param_bounds={"vignette_opacity": (0.1, 0.75), "stroke_width": (2.0, 18.0)},
        required_params=("focus_box_norm",),
        description="Authentic source UI spotlight darkening peripheral canvas while tracing a crisp vector ring around target proof.",
    ),
    EffectDescriptor(
        effect_id="dx01.evidence.before_after_wipe",
        version="1.0.0",
        family="evidence",
        backend="hybrid",
        semantic_roles=("contrast", "evidence", "payoff", "reveal"),
        supported_media=("screencast", "product", "documentary", "faceless", "mixed"),
        min_duration_sec=0.80,
        max_duration_sec=12.0,
        cost_tier="medium",
        required_capabilities=("remotion_bridge",),
        license_provenance="MIT_ADAPTED:Remocn/remocn@ac1e4081",
        fallback_id="legacy.contrast_transformation",
        param_bounds={"divider_pause_at": (0.2, 0.8)},
        description="Sweeping split before/after comparison divider pairing two authentic media timestamps/states.",
    ),
    # --- Family 8: atmosphere_accent (2 effects) ---
    EffectDescriptor(
        effect_id="dx01.atmo.light_sweep",
        version="1.0.0",
        family="atmosphere_accent",
        backend="remotion",
        semantic_roles=("transition", "reveal", "payoff", "emotion"),
        supported_media=("talking_head", "faceless", "screencast", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.35,
        max_duration_sec=4.0,
        cost_tier="low",
        required_capabilities=("remotion_bridge",),
        license_provenance="MIT_ADAPTED:Remocn/remocn@ac1e4081",
        fallback_id=None,
        param_bounds={"angle_deg": (-65.0, 65.0), "intensity": (0.05, 0.75)},
        description="Restrained diagonal light-beam sweep accent across beat handoff or payoff.",
    ),
    EffectDescriptor(
        effect_id="dx01.atmo.seeded_particles_grain",
        version="1.0.0",
        family="atmosphere_accent",
        backend="remotion",
        semantic_roles=("emotion", "orientation", "pause", "payoff"),
        supported_media=("talking_head", "faceless", "product", "documentary", "silent", "mixed"),
        min_duration_sec=0.60,
        max_duration_sec=15.0,
        cost_tier="low",
        required_capabilities=("remotion_bridge",),
        license_provenance="MIT_ADAPTED:av/remotion-bits@de35fda8",
        fallback_id=None,
        param_bounds={"particle_count": (6.0, 36.0), "intensity": (0.05, 0.7), "grain_opacity": (0.0, 0.2)},
        description="Deterministic seeded ambient particles and subtle organic film grain (optional, budgeted).",
    ),
)

# Preserved R4.3 legacy clean-room components (cards/diagrams when explicitly appropriate or fallback)
LEGACY_R43_DESCRIPTORS: tuple[EffectDescriptor, ...] = (
    EffectDescriptor("legacy.editorial_hook_banner", "1.0.0", "typography", "independent_pil", ("hook", "claim"), ("talking_head", "faceless", "mixed"), cost_tier="low", non_card_form=False, is_legacy_component=True, description="R4.3 clean-room top hook banner."),
    EffectDescriptor("legacy.context_chip", "1.0.0", "typography", "independent_pil", ("orientation", "claim"), ("talking_head", "faceless", "mixed"), cost_tier="low", non_card_form=False, is_legacy_component=True, description="R4.3 clean-room negative-space context chip."),
    EffectDescriptor("legacy.keyword_accent_ticker", "1.0.0", "typography", "independent_pil", ("emphasis", "claim"), ("talking_head", "faceless", "mixed"), cost_tier="low", non_card_form=False, is_legacy_component=True, description="R4.3 clean-room keyword accent pill."),
    EffectDescriptor("legacy.numerical_payoff_hero", "1.0.0", "numerical_data", "independent_pil", ("payoff", "contrast"), ("talking_head", "faceless", "mixed"), cost_tier="low", non_card_form=False, is_legacy_component=True, description="R4.3 clean-room numerical payoff card."),
    EffectDescriptor("legacy.cta_resolve_pill", "1.0.0", "typography", "independent_pil", ("payoff", "orientation"), ("talking_head", "faceless", "mixed"), cost_tier="low", non_card_form=False, is_legacy_component=True, description="R4.3 clean-room CTA resolve pill."),
    EffectDescriptor("legacy.resource_depletion", "1.0.0", "numerical_data", "independent_pil", ("process", "contrast"), ("talking_head", "faceless", "mixed"), cost_tier="low", non_card_form=False, is_legacy_component=True, description="R4.3 exact-Decimal resource depletion bar."),
    EffectDescriptor("legacy.resource_allocation", "1.0.0", "numerical_data", "independent_pil", ("process", "payoff"), ("talking_head", "faceless", "mixed"), cost_tier="low", non_card_form=False, is_legacy_component=True, description="R4.3 exact-conservation resource allocation split."),
    EffectDescriptor("legacy.contrast_transformation", "1.0.0", "evidence", "independent_pil", ("contrast", "payoff"), ("talking_head", "faceless", "mixed"), cost_tier="low", non_card_form=False, is_legacy_component=True, description="R4.3 two-scenario contrast card."),
    EffectDescriptor("legacy.evidence_spotlight", "1.0.0", "evidence", "independent_pil", ("evidence", "reveal"), ("talking_head", "screencast", "product", "mixed"), cost_tier="low", non_card_form=False, is_legacy_component=True, description="R4.3 evidence spotlight component."),
    EffectDescriptor("legacy.causal_chain", "1.0.0", "geometry_path", "independent_pil", ("cause", "process"), ("talking_head", "faceless", "mixed"), cost_tier="low", non_card_form=False, is_legacy_component=True, description="R4.3 ordered causal chain diagram."),
    EffectDescriptor("legacy.decision_branch", "1.0.0", "numerical_data", "independent_pil", ("contrast", "process"), ("talking_head", "faceless", "mixed"), cost_tier="low", non_card_form=False, is_legacy_component=True, description="R4.3 sequential decision branch comparison."),
)


class EffectRegistry:
    """Cached, fail-closed offline catalog of DX-01 + legacy R4.3 motion & footage effects."""

    def __init__(self, *, remotion_available: bool | None = None, verified_receipts: dict[str, dict[str, Any]] | None = None) -> None:
        self._by_id: dict[str, EffectDescriptor] = {}
        self._by_family: dict[str, list[EffectDescriptor]] = {f: [] for f in MOTION_FAMILIES}
        for d in (*DX01_EFFECT_DESCRIPTORS, *LEGACY_R43_DESCRIPTORS):
            self._by_id[d.effect_id] = d
            self._by_family.setdefault(d.family, []).append(d)
        self._verified_receipts: dict[str, dict[str, Any]] = dict(verified_receipts or {})
        self._disabled_ids: set[str] = set()
        self._remotion_available = remotion_available

    def set_remotion_available(self, available: bool) -> None:
        self._remotion_available = bool(available)

    def mark_verified_render(self, effect_id: str, receipt: dict[str, Any]) -> None:
        if effect_id not in self._by_id:
            raise KeyError(f"UNKNOWN_EFFECT_ID: {effect_id}")
        self._verified_receipts[effect_id] = dict(receipt)

    def disable_effect(self, effect_id: str) -> None:
        self._disabled_ids.add(effect_id)

    def get(self, effect_id: str) -> EffectDescriptor | None:
        return self._by_id.get(str(effect_id or "").strip())

    def all_descriptors(self, *, include_legacy: bool = True) -> list[EffectDescriptor]:
        if include_legacy:
            return list(self._by_id.values())
        return [d for d in self._by_id.values() if not d.is_legacy_component]

    def dx01_effect_ids(self) -> list[str]:
        return [d.effect_id for d in DX01_EFFECT_DESCRIPTORS]

    def families(self) -> list[str]:
        return list(MOTION_FAMILIES)

    def status_of(self, effect_id: str) -> str:
        d = self._by_id.get(effect_id)
        if d is None:
            return "BLOCKED"
        if effect_id in self._disabled_ids:
            return "DISABLED"
        if "remotion_bridge" in d.required_capabilities and self._remotion_available is False:
            return "BLOCKED"
        if effect_id in self._verified_receipts and self._verified_receipts[effect_id].get("verified"):
            return "VERIFIED_RENDER"
        return "AVAILABLE"

    def resolve_with_fallback(self, effect_id: str) -> tuple[str | None, str, str | None]:
        """Resolve an effect ID honoring capability status; returns `(resolved_id, status, fallback_reason)`."""
        st = self.status_of(effect_id)
        if st in ("AVAILABLE", "VERIFIED_RENDER"):
            return effect_id, st, None
        d = self._by_id.get(effect_id)
        if d is None:
            return None, "BLOCKED", f"UNKNOWN_EFFECT_ID:{effect_id}"
        if d.fallback_id and self.status_of(d.fallback_id) in ("AVAILABLE", "VERIFIED_RENDER"):
            return d.fallback_id, "FALLBACK_SELECTED", f"{effect_id} status={st} -> deterministic fallback {d.fallback_id}"
        return None, st, f"{effect_id} status={st} with no usable fallback"

    def search(
        self,
        *,
        semantic_role: str | None = None,
        family: str | None = None,
        media_type: str | None = None,
        aspect_ratio: str | None = None,
        duration_sec: float | None = None,
        max_cost_tier: str = "high",
        caption_policy: str = "OFF",
        prefer_non_card: bool = True,
        exclude_ids: set[str] | None = None,
        include_legacy: bool = False,
    ) -> list[dict[str, Any]]:
        """Deterministic filter & rank of usable effects for a beat objective & constraints."""
        cost_rank = {"low": 1, "medium": 2, "high": 3}
        max_c = cost_rank.get(max_cost_tier, 3)
        cap_on = str(caption_policy or "OFF").upper() in ("ON", "ON_REQUESTED", "KEYWORDS_ONLY")
        excl = set(exclude_ids or ())

        ranked: list[tuple[float, str, dict[str, Any]]] = []
        for d in self.all_descriptors(include_legacy=include_legacy):
            if d.effect_id in excl:
                continue
            st = self.status_of(d.effect_id)
            if st in ("DISABLED", "BLOCKED"):
                continue
            if family and d.family != family:
                continue
            if aspect_ratio and aspect_ratio not in d.aspect_constraints:
                continue
            if media_type and media_type not in d.supported_media and "mixed" not in d.supported_media:
                continue
            if duration_sec is not None and not (d.min_duration_sec <= duration_sec <= d.max_duration_sec):
                continue
            if cost_rank.get(d.cost_tier, 2) > max_c:
                continue
            if d.requires_caption_policy and not cap_on:
                continue

            score = 50.0
            if semantic_role and semantic_role in d.semantic_roles:
                score += 30.0 - d.semantic_roles.index(semantic_role) * 2.5
            elif semantic_role:
                score -= 10.0
            if prefer_non_card and d.non_card_form:
                score += 14.0
            if st == "VERIFIED_RENDER":
                score += 6.0
            if d.backend == "remotion":
                score += 4.0
            elif d.backend == "footage_engine":
                score += 3.0
            score -= (cost_rank.get(d.cost_tier, 2) - 1) * 1.5
            ranked.append((round(score, 3), d.effect_id, d.to_dict(status=st, verify_receipt=self._verified_receipts.get(d.effect_id))))

        ranked.sort(key=lambda x: (-x[0], x[1]))
        return [{**item, "rank_score": sc} for sc, _eid, item in ranked]

    def validate_invocation(
        self,
        inv: dict[str, Any],
        *,
        known_source_ids: set[str] | None = None,
        caption_policy: str = "OFF",
    ) -> dict[str, Any]:
        """Fail-closed validation of a single DX-01 effect invocation (finite numbers, bounds, security, sources)."""
        issues: list[str] = []
        if not isinstance(inv, dict):
            return {"pass": False, "issues": ["INVOCATION_NOT_DICT"]}
        eid = str(inv.get("effect_id") or "").strip()
        d = self._by_id.get(eid)
        if d is None:
            return {"pass": False, "effect_id": eid, "issues": [f"UNKNOWN_EFFECT_ID:{eid!r}"]}

        # Caption policy gate
        cap_pol = str(caption_policy or "OFF").upper()
        if d.requires_caption_policy and cap_pol == "OFF":
            issues.append(f"CAPTION_POLICY_OFF_FORBIDS:{eid}")

        # Validate finite numbers recursively & reject NaN/inf or executable strings
        def _walk(obj: Any, path_k: str) -> None:
            if isinstance(obj, bool):
                return
            if isinstance(obj, (int, float)):
                if not math.isfinite(float(obj)):
                    issues.append(f"NON_FINITE_NUMBER:{path_k}={obj!r}")
            elif isinstance(obj, str):
                if _FORBIDDEN_EXPR_RE.search(obj):
                    issues.append(f"FORBIDDEN_EXPRESSION_OR_URL:{path_k}")
                if path_k.split(".")[-1] not in ("effect_id", "event_id", "source_id", "claim_id", "evidence_id", "fallback_id", "kind"):
                    if _INTERNAL_LABEL_RE.search(obj):
                        issues.append(f"INTERNAL_LABEL_LEAK:{path_k}={obj!r}")
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    _walk(v, f"{path_k}.{k}")
            elif isinstance(obj, list):
                for idx_i, item in enumerate(obj):
                    _walk(item, f"{path_k}[{idx_i}]")

        _walk(inv, "inv")

        # Duration & timing validation
        if "start_sec" in inv and "end_sec" in inv:
            try:
                t0, t1 = float(inv["start_sec"]), float(inv["end_sec"])
                if not (math.isfinite(t0) and math.isfinite(t1)) or t0 < 0.0 or t1 <= t0:
                    issues.append(f"INVALID_TIME_INTERVAL:[{t0},{t1})")
                else:
                    dur = t1 - t0
                    if dur < d.min_duration_sec - 0.05 or dur > d.max_duration_sec + 0.05:
                        issues.append(f"DURATION_OUT_OF_RANGE:{dur:.3f}s not in [{d.min_duration_sec},{d.max_duration_sec}]")
            except (TypeError, ValueError):
                issues.append("NON_NUMERIC_TIME_INTERVAL")

        # Z-order check
        if "z_order" in inv and inv["z_order"] is not None:
            try:
                zv = int(inv["z_order"])
                if not (0 <= zv <= 200):
                    issues.append(f"Z_ORDER_OUT_OF_RANGE:{zv}")
            except (TypeError, ValueError):
                issues.append("INVALID_Z_ORDER")

        # Parameter bounds check
        for pk, (lo, hi) in d.param_bounds.items():
            if pk in inv and inv[pk] is not None:
                try:
                    val = float(inv[pk])
                    if not (math.isfinite(val) and lo <= val <= hi):
                        issues.append(f"PARAM_OUT_OF_BOUNDS:{pk}={val} not in [{lo},{hi}]")
                except (TypeError, ValueError):
                    issues.append(f"PARAM_NOT_NUMERIC:{pk}")

        # Required parameters check
        for req_p in d.required_params:
            if req_p not in inv or inv[req_p] is None or inv[req_p] == "" or inv[req_p] == []:
                issues.append(f"MISSING_REQUIRED_PARAM:{req_p}")

        # Focus box geometry check
        if "focus_box_norm" in inv and inv["focus_box_norm"] is not None:
            fb = inv["focus_box_norm"]
            if not (isinstance(fb, (list, tuple)) and len(fb) == 4):
                issues.append("INVALID_FOCUS_BOX_SHAPE")
            else:
                fx, fy, fw, fh = [float(x) for x in fb]
                if fx < 0.0 or fy < 0.0 or fw <= 0.02 or fh <= 0.02 or fx + fw > 1.001 or fy + fh > 1.001:
                    issues.append(f"FOCUS_BOX_OUT_OF_RANGE:{fb}")

        # Source reference validation
        if known_source_ids is not None:
            for sk in ("source_id", "from_source_id", "to_source_id", "evidence_source_id"):
                sv = inv.get(sk)
                if sv is not None and str(sv) not in known_source_ids:
                    issues.append(f"UNKNOWN_SOURCE_REF:{sk}={sv!r}")

        return {
            "pass": len(issues) == 0,
            "effect_id": eid,
            "family": d.family,
            "backend": d.backend,
            "status": self.status_of(eid),
            "issues": issues,
        }

    def snapshot(self) -> dict[str, Any]:
        dx_entries = [d.to_dict(status=self.status_of(d.effect_id), verify_receipt=self._verified_receipts.get(d.effect_id)) for d in DX01_EFFECT_DESCRIPTORS]
        leg_entries = [d.to_dict(status=self.status_of(d.effect_id), verify_receipt=self._verified_receipts.get(d.effect_id)) for d in LEGACY_R43_DESCRIPTORS]
        by_fam = {f: [e["effect_id"] for e in dx_entries if e["family"] == f] for f in MOTION_FAMILIES}
        doc = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "registry_version": EFFECT_REGISTRY_VERSION,
            "remotion_available": self._remotion_available,
            "family_count": len(MOTION_FAMILIES),
            "families": list(MOTION_FAMILIES),
            "dx01_effect_count": len(dx_entries),
            "legacy_component_count": len(leg_entries),
            "verified_render_count": sum(1 for e in dx_entries if e["status"] == "VERIFIED_RENDER"),
            "available_count": sum(1 for e in dx_entries if e["status"] in ("AVAILABLE", "VERIFIED_RENDER")),
            "blocked_count": sum(1 for e in dx_entries if e["status"] == "BLOCKED"),
            "by_family": by_fam,
            "effects": dx_entries,
            "legacy_components": leg_entries,
        }
        doc["registry_sha256"] = hashlib.sha256(
            json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return doc


_CACHED_DEFAULT_REGISTRY: EffectRegistry | None = None


def get_default_effect_registry(*, remotion_available: bool | None = None, refresh: bool = False) -> EffectRegistry:
    """Return cached default EffectRegistry (auto-probing local Remotion capability once unless explicitly passed)."""
    global _CACHED_DEFAULT_REGISTRY
    if _CACHED_DEFAULT_REGISTRY is None or refresh:
        if remotion_available is None:
            try:
                from .remotion_adapter import probe_remotion_runtime
                pr = probe_remotion_runtime()
                rem_ok = bool(pr.get("ready"))
            except Exception:
                rem_ok = False
        else:
            rem_ok = bool(remotion_available)
        _CACHED_DEFAULT_REGISTRY = EffectRegistry(remotion_available=rem_ok)
    elif remotion_available is not None:
        _CACHED_DEFAULT_REGISTRY.set_remotion_available(remotion_available)
    return _CACHED_DEFAULT_REGISTRY


__all__ = [
    "DX01_EFFECT_DESCRIPTORS",
    "EFFECT_REGISTRY_VERSION",
    "EffectDescriptor",
    "EffectRegistry",
    "LEGACY_R43_DESCRIPTORS",
    "MOTION_FAMILIES",
    "REGISTRY_SCHEMA_VERSION",
    "get_default_effect_registry",
]
