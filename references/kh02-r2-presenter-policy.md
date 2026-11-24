# KH-02.R2 Explicit Presenter Policy & Persistent KOL PIP Contract

## 1. Purpose & Optional Default (`LEGACY_DYNAMIC`)
Persistent bottom-corner KOL Picture-in-Picture (`FIXED_PIP`) is an **explicit optional editorial layout policy**, never a forced global default.
- Legacy `kh02.brief.v1` briefs without `presenter_policy` deterministically migrate to **`LEGACY_DYNAMIC`** (or **`FACELESS`** when `mode == "faceless"`), keeping all prior talking-head and faceless workflows intact.
- Unknown presenter modes, invalid anchors, conflicting flags, or missing presenter A-roll under `FIXED_PIP` / `ALWAYS_VISIBLE` fail closed (`BLOCKED_PRESENTER_SOURCE`, exit code `2` or `3` — never silently falling back to faceless!).

## 2. Supported Modes & Anchors
- **Modes**: `LEGACY_DYNAMIC`, `FIXED_PIP`, `ADAPTIVE_PIP`, `DYNAMIC_HYBRID`, `FACELESS`
- **Visibility**: `ALWAYS_VISIBLE` | `EDITORIAL_ALLOWED`
- **Anchors**: `TOP_LEFT`, `TOP_RIGHT`, `BOTTOM_LEFT`, `BOTTOM_RIGHT`, `CUSTOM` (requires explicit `[x, y, w, h]` `custom_box`)
- **Shapes**: `ROUNDED_RECT` (recommended default for head+shoulders preservation), `CIRCLE` (strictly validated against face+mouth corner inset)

## 3. Geometry, Safe Zones & Face-Safe Crop Math (`scripts/kh02/camera.py`)
- `resolve_presenter_region()` computes integer pixel & normalized coordinates respecting platform UI safe zones (`reels`, `tiktok`, `shorts` reserve ~19–21% bottom clearance and ~11% right interaction rail; `youtube` 16:9 and `square` 1:1 adapt cleanly without stretching).
- `fit_face_safe_crop()` calculates orientation-corrected `object-fit: cover` crop windows, CSS `object-position`, and resolution-capped zoom keeping face + chin/mouth + headroom strictly inside the clipped container. Without a live ML face tracker, it reports `FALLBACK_STATIC_VERIFIED` (never claiming automated face tracking).
- `compute_presenter_reserved_region()` pads the presenter box by `guard_margin_ratio` (`0.024` default) so `typography.resolve_layout_avoiding_presenter()` solves swept entry/overshoot/exit collisions without ever hiding the speaker. Unsolvable layouts fail closed with `LAYOUT_CONFLICT`.

## 4. Two-Subtree DOM Layering (`scripts/kh02/compile_plan.py`)
- `#mainVisualStage` (`isolation: isolate; z-index: 10`) isolates all story graphics, evidence wipes, and transition masks so global overlays can never paint over `#personWrap`.
- In `FIXED_PIP` / `ADAPTIVE_PIP`, `#personWrap` renders at `z-index: 200` with initial inline CSS geometry (`left/top/width/height/border-radius/opacity:1`) already set **before** JS timeline seek — eliminating frame-0 full-screen flash and EOF fade-to-zero.
- `project.json::face_rect` is updated to `presenter_crop["face_in_container_norm"]` so upstream H1 `geom-check.mjs` inspects actual transformed face occlusion inside the PIP container.

## 5. Decoded Per-Frame Presenter QA (`scripts/kh02/qa.py::verify_presenter_frames`)
- Metadata alone cannot prove visibility. Stage K decodes **every frame `0..N-1`** of the rendered MP4 and streams the PIP region against the derived source's expected crop window:
  - Emits `PRESENTER_FRAME_QA.csv`, `PRESENTER_FRAME_QA.json`, and `PIP_CROP_EVIDENCE/` (start/mid/seam/end side-by-side crops + `mobile_360px_preview.png`).
  - Fails closed on frame-0 flash, EOF fade, solid/overlay occlusion, frozen presenter frames vs moving source, or frame count drift.
