# KH-02.R1 — Effect Capability Registry & Deterministic Effect Gallery

## 1. Proven Working Capabilities First (`scripts/kh02/registry.py`, `compile_plan.py`, `effects_gallery.py`)
Catalog listing NEVER implies verified usability. Registry availability separates:
- `IMPLEMENTED_VERIFIED`: Compiled in `compile_plan.py` AND passed headless Chromium Effect Gallery verification (`direct == reverse == repeat == cold browser restart`, zero first-frame flash, visible pixel region delta >= 2.0, zero console/page errors) with recorded preview PNG + SHA-256.
- `LOCAL_VERIFIED`: Implemented and unit-tested locally before browser gallery run.
- `CATALOG_LISTED`: Observed in pinned `hyperframes@0.9.86` catalog JSON, not yet gallery-verified.
- `UNAVAILABLE` / `UNSUPPORTED`: Explicitly rejected or routed via fallback chain (e.g., `experimental.true_3d_camera`).

## 2. Shipped Representative R1 Families (All HyperFrames/HTML/CSS/GSAP)
- **Camera**: 2D virtual micro-reframe/punch-in/crop/pan strictly bounded inside source pixel coverage (`camera.py::build_camera_path`, rotation-aware, face-safe, zero black overscan edges).
- **Transition & Mask Reveals**:
  - `r1.clip_mask_wipe`: Directional `clip-path: inset(...)` reveal/transition.
  - Anchor-preserving match-anchor handoff (`transition.py::solve_anchor_handoff`, `plan_transition_seam`, `validate_continuity`).
- **Typography**:
  - `r1.kinetic_phrase_mask`: Overflow-hidden staggered slide-up phrase mask synced to spoken keywords (`typography.py::measure_layout_mobile` checks 360px legibility across Latin, Vietnamese diacritics, and CJK).
- **VFX & Editorial Punctuation**:
  - `r1.spotlight_focus`: Selective background dim + crisp focal highlight window preserving natural UI/product color fidelity.
  - `r1.light_sweep_badge`: Restrained deterministic diagonal light sweep on hero verdict/CTA badges.
  - `r1.freeze_callout`: Explicitly declared freeze-frame punctuation card with readable hold duration.
- **Layer Choreography & Hand-Drawn Craft**:
  - `hf.stagger_cascade` (`stagger_grid_4`, `step_pillars_3`, `step_rail_3`), `highlighter_card`, `hf_hw.underline`, `hf_hw.arrow`, `hf_hw.callout_circle`.

