# KH-02 Cross-Niche Capability Matrix & Generalization (`kh02-cross-niche.md`)

KH-02 is designed as a generalizable autonomous editorial architecture rather than an Olivia-only template. Zero Olivia timestamps, filenames, or product strings are hardcoded inside `scripts/kh02/`.

## Capability Support Matrix (Honest Verification Posture)

| Mode / Scenario | Planner + Schema + Compiler | Synthetic Smoke Render (`render.mjs` + `ffprobe`) | Real Footage E2E Verified | Notes & Constraints |
|---|---|---|---|---|
| `talking_head` (`9:16`, `LOCK_VOICE`) | **SUPPORTED** | **VERIFIED** | **VERIFIED** (Olivia KH-02 noncommercial eval) | Core SeeCut v3.4 workflow; supports expressive pauses, evidence cards, PIP/full reframing, JianYing layered export. |
| `software_demo` / `tutorial` (`16:9` / `9:16`) | **SUPPORTED** | **VERIFIED** (unseen synthetic smoke render) | `NOT_VERIFIED_REAL_FOOTAGE` | Uses `technical_slate_cyan` theme, `hf.code_snippet`, crisp non-bounce UI panels, optional requested captions. |
| `education` / `explainer` (`16:9` / `1:1`) | **SUPPORTED** | **VERIFIED** (unseen synthetic smoke render) | `NOT_VERIFIED_REAL_FOOTAGE` | Uses `explainer_cobalt_chalk` theme, structured `step_rail_3` diagrams (`CONCEPT_ILLUSTRATION`), multilingual EN/VI/CJK typography. |
| `product_demo` / comparison | **SUPPORTED** | `TEST_ONLY` (unit/contract fixtures) | `NOT_VERIFIED_REAL_FOOTAGE` | Requires real verified screenshots/footage (`claim_evidence[]`); fabricated UI mockups blocked. |
| `documentary` / `narrative` (`minimal_documentary`) | **SUPPORTED (EXPERIMENTAL)** | `TEST_ONLY` (unit/contract fixtures) | `NOT_VERIFIED_REAL_FOOTAGE` | Enforces zero spring overshoot (`validate_motion_curves`) and explicit `ALLOW_INTENTIONAL_CUT` exception logging. |
| `faceless` / multi-take without A-roll | **EXPERIMENTAL** | `TEST_ONLY` (unit/contract fixtures) | `NOT_VERIFIED_REAL_FOOTAGE` | Upstream `render.mjs` runs person-free builds, but full multi-source B-roll editorial verification requires real multi-clip footage. |
| Long-form (>60s, chaptered streaming) | **SUPPORTED IN CONTRACT/TIME-MAP** | `TEST_ONLY` (bounded synthetic chapter & time-map drift tests, 10+ min @ `30000/1001` & `24fps`) | `PENDING_LONGFORM_RENDER` | No 30s hard cap in planner/EDL/compiler; actual full long-form render requires explicit disk/time/owner authorization. |

## Generalization Guardrails
- Never claim a niche is production-validated from synthetic fixtures alone: distinguish `TEST_ONLY` vs `SMOKE_VERIFIED` vs `REAL_FOOTAGE_VERIFIED`.
- Multi-aspect (`9:16`, `16:9`, `1:1`, `4:5`) and multilingual (`en`, `vi` diacritics, `zh`/CJK) are validated deterministically in `tests/kh02/`.
