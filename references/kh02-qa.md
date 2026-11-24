# KH-02 Independent Quality Control & Adoption Contract (`kh02-qa.md`)

KH-02 enforces **objective deterministic gates BEFORE subjective model ranking**, keeping upstream H1 (`geom-check.mjs`), H2 (`agy_judge_safe.py check`), and order-swapped pairwise consensus (`agy_judge_safe.py pair`, max 3 versions) intact while hardening every P0 boundary.

## 1. Orthogonal QA Layers (`scripts/kh02/qa.py`)
1. **Contract & Semantic QA (`evaluate_editorial`)**: Schema strictness (no duplicate keys, no `NaN`/`Inf`, no bool-as-int), reference integrity, zero `ANALYSIS_ONLY` asset leakage, frame-clock contiguity.
2. **Visual / Motion / Camera / Transition / Typography QA**: `validate_motion_curves`, `validate_camera_continuity`, `validate_seam`, `validate_readability`, plus upstream browser-evaluated H1 `geom-check.mjs`.
3. **Structural Decode & Provenance QA (`validate_structural`)**: Full `ffmpeg -f null -` decode across all video/audio packets, exact `nb_frames` & resolution check, monotonic PTS check, black-flash check, and cryptographic provenance verification (`engine == "SeeCut-v3.4-HyperFrames-HTML-GSAP"` + `renderer_sha256` + `mp4_sha256`).
4. **Audio QA (`inspect_audio`)**: Real EBU R128 integrated LUFS / LRA / true peak, clipping detection, cue bounds/rights validation, and `LOCK_VOICE` PCM correlation.
5. **Boundary Filmstrip + Waveform Diagnostics (`render_timeline_diagnostics`)**: Adapted from `browser-use/video-use` (MIT), generating bounded PNG strips with exact timestamps, real PCM waveform, silence highlights, output-clock word labels, and a `frame_diagnostics.csv` separating planned events from observed pixel luminance/motion deltas.

## 2. Adoption Rule (`decide_adoption`)
A candidate is marked `ADOPTED` **ONLY** when:
- H1 geometry gate PASS (`FAIL 0`), AND
- H2 hard-defect gate PASS (complete rubric `{"C1","C3","C4","C5","C6","C7"}`, distinct verified fingerprint hits `>= 2`, calibrated duration delta), AND
- Objective post-render structural + audio QA PASS, AND
- Order-swapped pairwise comparison (`X=A/Y=B` and `X=B/Y=A`) agrees on the candidate in BOTH orders (`consensus == candidate_label`).

`tie` -> `CANDIDATE_NOT_ADOPTED_TIE`; `invalid` -> `BLOCKED_INVALID_JUDGE`; missing model judge -> `PENDING_REVIEW`.

## 3. Separated Honest Status Flags
Every final QA report keeps these 7 dimensions strictly separate:
- `CODE_QA_PASS`
- `OLIVIA_RENDER_VERIFIED`
- `AUTO_EDITORIAL_QA`
- `OWNER_VISUAL` (always `PENDING` until owner watches the exported MP4 SHA)
- `OWNER_AUDIO` (always `PENDING` until owner listens to the exported MP4 SHA)
- `COMMERCIAL_RELEASE` (always `BLOCKED` under PolyForm Noncommercial 1.0.0 unless written permission from `aiesecxuanli@gmail.com` is logged)
- `CROSS_NICHE_VALIDATED` (`TEST_ONLY` / `PARTIAL_SMOKE_VERIFIED` / `VERIFIED` strictly matching real evidence)
