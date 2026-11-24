# KH-02.R1 — Intelligent Rough Cut & Hook / Pacing Director

## 1. Intelligent Rough Cut (`scripts/kh02/rough_cut.py`, re-exported via `edit_decisions.py`)

### Cut Policies (Fail-Closed — Never Silently Broadened)
- `LOCK_VOICE` (default): Identity output speech map, zero cuts, continuous original timing.
- `PRESERVE_SCRIPT_TRIM` (and legacy `TRIM_APPROVED` alias): Authorizes trimming **nonverbal** `LEAD_TAIL_PADDING`, `DEAD_AIR`, `WAITING_FOR_UI`, and `VISUAL_STALL` spans while preserving **100% of spoken words in exact chronological order** with zero phoneme retiming and `>=30–200ms` safe non-speech margins.
- `EDITORIAL_CUT`: Requires an explicit non-empty `approved_scope`; without it, safely downgrades to `LOCK_VOICE`. Permits low-risk `REPEATED_TAKE`, `FALSE_START`, `FILLER`, and `STUMBLE` removal while protecting negations, numbers, proper names, claim qualifiers, and low-confidence ASR words (`REVIEW`).

### 13 Candidate Classifications
`DEAD_AIR`, `LEAD_TAIL_PADDING`, `WAITING_FOR_UI`, `REPEATED_TAKE`, `FALSE_START`, `FILLER`, `STUMBLE`, `OFF_TOPIC`, `VISUAL_STALL`, `REACTION`, `DELIBERATE_PAUSE`, `CONTEXT_REQUIRED`, `TECHNICAL_DROPOUT`.

- Digital silence NEVER automatically equals redundant footage (silent tutorials / product showcases / reactions are preserved).
- Derived A/V clips are materialized with paired FFmpeg frame/sample trims BEFORE HyperFrames/GSAP composition; safe `<=30ms` fades sit strictly inside non-phoneme gaps with zero double fades on contiguous joins.
- `verify_cut_result()` enforces exact frame count, PCM sample continuity, click/dropout seam detection, exact word-order preservation, and unchanged source SHA-256.

### Required Artifacts
`CUT_CANDIDATES.json`, `CUT_DECISION_REPORT.md`, `EDL.json`, `TIME_MAP.json`, `OUTPUT_TRANSCRIPT.json`, `CUT_VERIFICATION.json`.

## 2. Hook, Retention & Editorial Energy Director (`scripts/kh02/planner.py`)

- Generates 2–3 distinct authentic opening candidates (`build_hook_candidates()`), validates every candidate through `evaluate_hook_truthfulness()` (refusing fabricated claims, unverified numbers/scarcity, suspense spoilers, and unauthorized speech rewriting/reordering), and selects the strongest match (`choose_opening()`).
- Energy modes: `kinetic`, `explanatory`, `suspense`, `calm`, `emotional`, `proof`, `reaction`, `payoff`.
- Calm openings with zero forced zoom or SFX are explicitly valid when content warrants them.
- Pacing budgets carry `editorial_intent_only: true` — zero invented CTR, viral, or retention percentages.
- Required Artifacts: `HOOK_OPTIONS.json`, `HOOK_DECISION.md`, `PACING_PLAN.json`, `ATTENTION_BEATS.md`, `EDITORIAL_COVERAGE.json`.

