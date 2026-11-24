# KH-02 Editorial Doctrine (`kh02-editorial-doctrine.md`)

KH-02 upgrades `seecut-ai` from a single-template motion-graphics wrapper into a **single-master, content-driven editorial system** built strictly atop **YeJe-cpu/SeeCut v3.4 Core** (`HyperFrames + HTML/CSS/GSAP + Playwright + FFmpeg`).

## 1. One Master Editor, One Authoritative Timeline
- **Authoritative Truth**: `SHOTBOOK_V2.json` (`schemas/kh02/shotbook_v2.schema.json`) is the single source of truth for beats, visual events, 2D virtual camera keyframes, transitions, typography, audio cues, claim evidence, and approved exceptions.
- **Derived Views Only**: `SHOTBOOK_V2.md`, `build/master.html`, `build/project.json`, and `COMPILE_MAP.json` are generated deterministically from `SHOTBOOK_V2.json`. No competing second planner or silent HTML-only edits are permitted.
- **Logical Editorial Roles**: Source Analyst, Script/Semantic Director, Pacing Director, Creative/Visual Director, Camera Director, Motion/Transition Director, Typography/Layout Director, Sound Director, Continuity Inspector, and Independent Senior Critic all emit structured proposals resolved deterministically by `scripts/kh02/planner.py`.

## 2. Precedence & Upstream Rule Conflicts
- `third_party/SeeCut/skill/seecut/references/00-总纲.md` remains supreme for `seecut_core` and `editorial_pro` profiles.
- Any deliberate profile variation (for example, `ALLOW_INTENTIONAL_CUT` in `minimal_documentary`, `ON_REQUESTED` captions when explicitly requested in the brief, or licensed BGM when explicitly permitted) MUST be logged in `approved_exceptions[]` with `rule_id`, `reason`, and `approved_by`. Never silently override `00-总纲.md`.

## 3. Content-First Editorial Principles
1. **Creative Direction Variety**: Always evaluate 2–3 distinct creative directions grounded in actual footage and evidence (`propose_directions()`), record alternatives and rejection rationale, and never reuse one fixed cream+pill+bounce template across unrelated niches.
2. **One Focal Point Per Beat**: Every beat must answer a concrete viewer question (`viewer_goal`) with a clear `planned_information_change`. Decorative motion without semantic payoff is rejected.
3. **Evidence Truthfulness**: Every claim referencing a product, web page, or statistic requires an entry in `claim_evidence[]`. Real screenshots must have source identifiers masked; explanatory diagrams must be labeled `CONCEPT_ILLUSTRATION` and never disguised as real captures.
4. **Audio-First Pacing (`LOCK_VOICE` Default)**: Preserve the original spoken voice untouched by default (`LOCK_VOICE`). Only trim silence/retakes under explicit `TRIM_APPROVED` brief authorization using exact rational piecewise time maps (`scripts/kh02/edit_decisions.py`). Preserve intentional visual showcase pauses instead of cutting blindly.
