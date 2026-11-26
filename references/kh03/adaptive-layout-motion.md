# KH-03.R2 Adaptive Dynamic Layout & Motion Choreography (`ADL-01..ADL-07`)

## 1. Policy Model (`ADAPTIVE_DYNAMIC` vs `FULLSCREEN_LOCK` vs Legacy)
- `ADAPTIVE_DYNAMIC` is an **additive layout policy**, NOT a sixth skill (`SK-06`) and NOT a new renderer.
- Existing layout contracts remain 100% preserved:
  - `FULLSCREEN_9_16` / `FULLSCREEN_LOCK`: strict `1.0x` full-canvas source frame from frame 0 through EOF (zero PIP, zoom, pan, crop, or opacity fade).
  - `LEGACY_DYNAMIC`, `FIXED_PIP`, `ADAPTIVE_PIP`, `DYNAMIC_HYBRID`, `FACELESS`: unchanged defaults.
- **Fail-Closed Conflict Rule**: Passing both `--layout-lock FULLSCREEN_9_16` (or `fullscreen_lock=True`) AND `--layout-policy ADAPTIVE_DYNAMIC` raises `LAYOUT_POLICY_CONFLICT`.
- **Selection Binding**: `layout_policy` is hashed inside `constraints_sha256` on `INPUT_ANALYSIS.json` and `SELECTION_RECEIPT.json`. Switching from `FULLSCREEN_9_16` to `ADAPTIVE_DYNAMIC` automatically invalidates stale receipts and requires a fresh recommendation + explicit user selection.

## 2. Shared Topology Vocabulary (`scripts/kh03/layout.py`)
1. `FULL_CANVAS`: 100% canvas live presenter opening (Frame-0 human-first hook).
2. `PRESENTER_LEFT_SPLIT`: Live aspect-preserved presenter on the left, dedicated visual explainer/ledger column on the right.
3. `PRESENTER_RIGHT_SPLIT`: Spatial mirror rebalance (live presenter right, payoff/result hero stage left).
4. `LOWER_COMPANION_SPLIT`: Wide upper proof/diagram stage paired with continuous lower presenter frame.
5. `UPPER_COMPANION_SPLIT`: Upper presenter frame paired with lower structured panel.
6. `FLOATING_INSET_PRESENTER`: Evidence/product hero canvas with rounded live inset presenter preserving face, mouth, and hands.
7. `FULL_SCREEN_RETURN`: Fluid enlargement back to 100% full-screen for direct human CTA.

All topologies use uniform `object-fit: cover` crop math (`aspect_distortion == 0.0`, zero stretched faces), minimum mobile `360px` face visibility floor (`>= 22px`), and conservative static tracking fallback (`STATIC_FALLBACK_VERIFIED`) unless verified detector boxes are supplied.

## 3. Smooth Choreography, EDL Clock & Adaptive Captions
- Transitions run `0.35s–0.85s` with cubic ease-in-out on the single master GSAP seek timeline (`window.__timelines.master`), inline Frame-0 `#personWrap` geometry before JS seek, and zero duplicate `<video>` clocks.
- `remap_kh03_plan_to_output_clock` maps beats, components, `LAYOUT_PLAN.json`, morph transitions, adaptive captions, and `SOUND_DIRECTOR_PLAN.json` ONCE onto the exact rational EDL output clock (`DOUBLE_REMAP_FORBIDDEN`).
- Adaptive captions (`CAPTION_LAYOUT_PLAN.json`) chunk speech at sentence boundaries and choose collision-free negative-space slots respecting Reels/TikTok/Shorts right-rail and bottom controls without covering mouth or active hands.

## 4. Per-Frame Decoded QA & Anti-Repetition
- `verify_adaptive_layout_frames()` streams EVERY decoded output frame comparing expected transformed source content inside the live moving presenter box (catching freezes, black flashes, missing presenter, distortion, or frame-order drift).
- `compute_anti_repetition_report()` evaluates spatial topology variety separately from text similarity, flagging unintentional top-banner loops while accepting deliberate `SK-05` natural holds and explicit `FULLSCREEN_LOCK` runs.

## 5. CLI Usage
```bash
# Recommend with adaptive layout policy (STOPS in AWAITING_USER_SELECTION):
python -m scripts.kh03 recommend --video <raw.mp4> --script <script.txt> --run-root <run_dir> --layout-policy ADAPTIVE_DYNAMIC --cut-policy PRESERVE_SCRIPT_TRIM --caption-policy ON_REQUESTED

# Explicit user skill selection:
python -m scripts.kh03 select --run-root <run_dir> --skill SK-04

# Execute through normal KH-03 -> KH-02.R2 pipeline:
python -m scripts.kh03 execute --run-root <run_dir> --mode plan --workers 2
```
