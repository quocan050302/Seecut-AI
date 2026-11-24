# KH-02 Motion, Virtual 2D Camera, Transitions & Typography (`kh02-motion-and-camera.md`)

## 1. Motion Grammar (`scripts/kh02/motion.py`)
- Every visual event follows an explicit `entry -> hold -> exit` frame structure evaluated deterministically on integer frame indices.
- **Adaptive Easing**:
  - `seecut_core` / `editorial_pro`: restrained spring overshoot (`back.out(2.1)`) for tactical UI cards/badges, `power2.inOut` for reframing/camera moves, `power2.out` for callouts.
  - `minimal_documentary` / `silent_visual`: zero bounce overshoot (`power2.out` / `power2.inOut`); spring overshoot is rejected by `validate_motion_curves()`.
- **SeeCut Core Render Invariants**: CSS starts delayed elements at `opacity: 0`; GSAP uses `.set()` + `.to()` (never `.from()`), absolute timeline seek (`window.__timelines.master.time(i / fps)`), zero CSS autoplay, zero `Date.now()` / `Math.random()`.

## 2. Virtual 2D Camera (`scripts/kh02/camera.py`)
- Flat 2D video footage NEVER claims true 3D camera depth (`experimental.true_3d_camera` is marked `UNAVAILABLE`).
- All reframing uses bounded 2D crop/scale/pan capped by `compute_max_safe_zoom(src_w, src_h, out_w, out_h, hard_cap=1.35)`.
- `validate_camera_continuity()` enforces:
  - `scale >= 1.0` on full-bleed footage (zero black underscan edges),
  - pan offsets strictly inside `(scale - 1) / (2 * scale)` overscan margin,
  - measured `face_rect` stays completely unclipped inside the viewport,
  - velocity cap and smooth boundary continuity across non-cut transitions.

## 3. Motivated Transitions (`scripts/kh02/transition.py`)
- Transitions derive from beat semantics (`match_anchor`, `reframe`, `motion_bridge`, `mask_wipe`, `crossfade`, or approved `intentional_cut`).
- `validate_seam()` verifies zero frame gaps/overlaps between adjacent beats, overlap durations `<= 1.2s`, and rejects unapproved hard cuts under `seecut_core` / `editorial_pro`.

## 4. Typography & Mobile Readability (`scripts/kh02/typography.py`)
- Supports Latin, Vietnamese diacritics (elevated `1.38` line-height headroom), and CJK glyphs with explicit local fallback stacks (zero unauthorized remote font downloads).
- Enforces mobile 360px legibility (`>= 10.5px` scaled floor), platform safe zones, bounding-box overflow checks, and reading-rate-based `minimum_read_frames`.
- **Captions OFF by Default**: Full subtitles are emitted ONLY when `caption_policy == "ON_REQUESTED"` in the brief, placed strictly at top `z_order` (`999`) above all visual overlays.
