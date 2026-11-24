---
name: seecut-ai
description: >-
  Run the KH-02 autonomous professional video editor built strictly on YeJe-cpu/SeeCut
  v3.4 Core (unified SHOTBOOK V2, editorial/camera/motion/transition/typography/audio
  direction, HyperFrames + HTML/CSS/GSAP + guarded render.mjs + H1 geom-check + hardened
  H2 agy review + order-swapped pairwise iteration up to 3 versions + layered JianYing
  package + boundary filmstrip/waveform & EBU R128 QA) with strict PolyForm Noncommercial
  1.0.0 license gating, non-bypass agy security checks, isolated workspaces, and honest
  human-approval gates. Use when the user asks to edit a talking-head, product/software
  demo, tutorial, explainer, or pre-voiced AI-avatar video using SeeCut, seecut-ai,
  SHOTBOOK V2, or HyperFrames/GSAP motion graphics. Does NOT generate avatars/TTS or
  silently substitute other video rendering engines (Concat/Remotion/video-use render.py).
---

# seecut-ai — KH-02 Autonomous Professional Editor on YeJe-cpu/SeeCut v3.4 Core

> **Required Notice**: Copyright (c) 2026 YeJe-cpu (https://github.com/YeJe-cpu)
> **Upstream License**: PolyForm Noncommercial License 1.0.0 (`https://polyformproject.org/licenses/noncommercial/1.0.0`). See [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
> **Commercial Licensing Contact**: `aiesecxuanli@gmail.com`.
> **Selective MIT Helper Attribution**: Analysis/diagnostic helpers adapted from `browser-use/video-use` (`@43cfc566`, MIT) in `transcript.py`, `qa.py`, `typography.py`, and `sound.py` — see [references/kh02-video-use-attribution.md](./references/kh02-video-use-attribution.md). Does NOT alter SeeCut's PolyForm Noncommercial license or rendering engine.

This skill orchestrates the **KH-02 Autonomous Professional Editor** directly atop the unmodified **YeJe-cpu/SeeCut v3.4 Core** rendering engine in a strictly isolated workspace with fail-closed gates for licensing, security permissions, path/cache safety, source integrity, evidence truthfulness, deterministic + browser + model QA, and human visual/audio sign-off.

## Non-Negotiable Guardrails

1. **Hard License Gate (Run First)**:
   - Audit upstream `LICENSE` and `NOTICE` before executing any edit/render.
   - `YeJe-cpu/SeeCut` is licensed under **PolyForm Noncommercial 1.0.0**, NOT MIT.
   - If the intended use includes monetized social media, ads, client services, resale, or any anticipated commercial application without written permission from `aiesecxuanli@gmail.com`, **STOP** with `BLOCKED_COMMERCIAL_LICENSE` and do not run SeeCut or render deliverables.
2. **Hard Security & P0 Safety Gate**:
   - **Never** run `--dangerously-skip-permissions` or `npx hyperframes@latest`.
   - Use only hardened local wrappers:
     - [scripts/safe_preflight.sh](./scripts/safe_preflight.sh) (`--offline` or `--live`, pinned executables, exact normalized `OK` check, JSON output, strict exit `0/1/2`).
     - [scripts/agy_read_safe.sh](./scripts/agy_read_safe.sh) (symlink/allowlist rejection, ownership-marked `trap` cleanup, transmission gate).
     - [scripts/agy_judge_safe.py](./scripts/agy_judge_safe.py) (exit-code-before-parse, strict schema/bool checks, complete H2 rubric `{C1,C3,C4,C5,C6,C7}`, distinct SHA-bound fingerprint hits, calibrated duration tolerance, strict `X`/`Y` winner mapping, sanitized `grab_fail_frames`).
   - Always invoke upstream `render.mjs` through [scripts/kh02/render_guard.py](./scripts/kh02/render_guard.py) (canonical run-owned `person_dir`, `.kh02_run_owned` marker, composite cache key, `>= 5 GiB` disk floor, atomic staging+rename, full decode QA).
3. **Strict Engine & Workspace Isolation**:
   - Sole rendering engine: **YeJe-cpu/SeeCut v3.4 Core** (`HyperFrames + HTML/CSS/GSAP + Playwright/Chromium + render.mjs + FFmpeg mux`).
   - Selective `video-use` helpers are permitted ONLY for read-only transcript phrase packing, boundary filmstrip/waveform QA diagnostics, opt-in SRT chunking, and loudness measurement — **NEVER** execute `video-use/helpers/render.py`, ElevenLabs API, Concat, Remotion, Manim, or CapCut as a rendering engine.
   - Keep avatar/TTS generation **DISABLED** for already-voiced clips (`LOCK_VOICE` default). Never touch unrelated repositories, global skills, or CapCut/JianYing user profiles.
4. **Input & Evidence Truthfulness**:
   - Require verified raw source inputs; never substitute `ANALYSIS_ONLY` reference reels or prior flattened adopted MP4s as source footage (`validate_references()` blocks any `ANALYSIS_ONLY` asset).
   - Never fabricate UI mockups, fake screenshots, or unverified claims (`claim_evidence[]` enforced). Mask source identifiers on real captures and record all visible text in `build/fp-manifest.txt`.
5. **No Fake PASS / Mandatory Human Sign-Off**:
   - Report all **19 original gates + 10 KH-02 subgates** truthfully and keep `CODE_QA_PASS`, `OLIVIA_RENDER_VERIFIED`, `AUTO_EDITORIAL_QA`, `OWNER_VISUAL` (`PENDING`), `OWNER_AUDIO` (`PENDING`), `COMMERCIAL_RELEASE` (`BLOCKED`), and `CROSS_NICHE_VALIDATED` strictly separated.

---

## Single-Entry CLI (`scripts/kh02/run.py`)

```bash
# Dry-run contract, editorial planning, and resource budget check (no writes/renders/model calls):
python3 -m scripts.kh02.run --brief <brief.json> --run-root <isolated_run_dir> --mode plan --dry-run

# Plan + emit authoritative SHOTBOOK_V2.json & SHOTBOOK_V2.md:
python3 -m scripts.kh02.run --brief <brief.json> --run-root <isolated_run_dir> --mode plan --workers 2

# Preview (0.5x) or Full native render + structural/audio/integrity QA:
python3 -m scripts.kh02.run --brief <brief.json> --run-root <isolated_run_dir> --mode full --workers 2 --max-disk 4.0
```

Options: `--dry-run`, `--workers 1..4` (default `2`, heavy browser/ffmpeg concurrency locked to `1`), `--max-disk <GiB>`, `--profile seecut_core|editorial_pro|minimal_documentary|silent_visual`, `--license-decision <status>`, `--transcript <faster_whisper.json>`, `--catalog <hyperframes_catalog.json>`.

---

## Authoritative References & Schemas

- **Upstream Index & Conflict Log**: [references/upstream-index.md](./references/upstream-index.md)
- **Gates & Compliance (19 + 10 KH-02)**: [references/gates-and-compliance.md](./references/gates-and-compliance.md)
- **KH-02 Doctrine**: [references/kh02-editorial-doctrine.md](./references/kh02-editorial-doctrine.md)
- **Motion, Camera, Transitions, Typography**: [references/kh02-motion-and-camera.md](./references/kh02-motion-and-camera.md)
- **Sound & Audio QA**: [references/kh02-audio.md](./references/kh02-audio.md)
- **Independent QA & Adoption**: [references/kh02-qa.md](./references/kh02-qa.md)
- **Cross-Niche Matrix**: [references/kh02-cross-niche.md](./references/kh02-cross-niche.md)
- **MIT Attribution (`video-use`)**: [references/kh02-video-use-attribution.md](./references/kh02-video-use-attribution.md)
- **JSON Schemas**: `schemas/kh02/{brief,source_inventory,transcript,edl,shotbook_v2,timeline,capability_registry,sfx_cues,qa_report}.schema.json`
- **Examples**: `templates/kh02/{BRIEF,SHOTBOOK_V2,SHOTBOOK_V2.negative,EDIT_PLAN,QA_REPORT}.example.json`

---

## Execution Workflow (KH-02 Phases 00–07)

### Phase 00 — Preflight, License & Run Isolation
1. Create a unique timestamped run folder `runs/<UTC_TS>_KH02_<shortsha>_<nonce>/` with `README.md`, `build/`, `qc/`, `exports/`, `jianying/layers/`, `logs/`, `evidence/`, `probe/`, `tests/`, `cache/`, `manifests/`.
2. Enforce the **Hard License Gate** (`LICENSE_AUDIT.md`, `REUSE_DECISIONS.md`) and run `zsh scripts/safe_preflight.sh --live --json-out <run>/logs/preflight.json` (or `--offline` for code-only checks).

### Phase 01 — Read-Only Source Analysis & ASR
1. Run `source_analysis.py::build_inventory()` (multi-root unique IDs, chunked SHA-256, `ffprobe` streams/fps/VFR/rotation/SAR, silence & scene-cut detection, rights validation).
2. Run local `faster-whisper` via `transcript.py::run_local_faster_whisper()` (16kHz PCM pipe immune to PyAV 19 `metadata_errors`) and normalize via `normalize_faster_whisper()` + `build_packed_transcript()` (`TAKES_PACKED.md` + `TRANSCRIPT_NORMALIZED.json`).
3. Measure raw audio loudness/peaks via `sound.py::analyze_audio()` and capture/mask real web evidence when required (`EVIDENCE_LEDGER.md`, `EVIDENCE_GAPS.md`).

### Phase 02 — Master Editorial Planner (`SHOTBOOK_V2`)
1. Evaluate 2–3 distinct creative directions (`planner.py::propose_directions()`), record alternatives & rejection reasons (`CREATIVE_DIRECTIONS.md`).
2. Build audio-first cut candidates & rational piecewise time map (`edit_decisions.py`; default `LOCK_VOICE` identity map, `TRIM_APPROVED` only on explicit brief approval).
3. Resolve unified `SHOTBOOK_V2.json` (`planner.py::resolve_editorial_plan()`), integrating `registry.py`, `motion.py`, `camera.py`, `transition.py`, `typography.py`, and `sound.py` (with `SHOTBOOK_V2.md` generated strictly from JSON). Validate via `qa.py::evaluate_editorial()`.

### Phase 03 — Canonical Compile & Guarded Render
1. Compile `SHOTBOOK_V2.json` via `compile_plan.py::compile_shotbook()` → `build/master.html`, `build/project.json`, `build/fp-manifest.txt`, `COMPILE_MAP.json`.
2. Render probe frames first via `render_guard.py::run_render_checked(..., mode="probe")` and visually inspect before full render. Use `RENDER_SCALE=0.5` for intermediate iterations (`v1`/`v2`), and full native scale for the final adopted render with untouched original audio (`-c:a copy`, no premature `-shortest` truncation).

### Phase 04 — Self-Review Loop (H1 + H2 + Order-Swapped Pairwise, Max 3 Versions)
1. **H1 Geometry Gate**: Run unmodified `node third_party/SeeCut/skill/seecut/scripts/geom-check.mjs <build>` (`FAIL 0` required).
2. **H2 Hard-Defect Gate**: Run hardened `python3 scripts/agy_judge_safe.py check --video <mp4> --html <build>/master.html --out qc/vN-check.json`. Inspect extracted fail-frames before acting on any flag.
3. **Order-Swapped Pairwise**: Run `python3 scripts/agy_judge_safe.py pair --a <cand> --b <base> --html-a <cand_html> --html-b <base_html> --out qc/vN-vs-base.json`. Adopt ONLY via `qa.py::decide_adoption()` (H1 + H2 + objective QA PASS + dual-order consensus). Cap at max 3 versions (`v1`–`v3`).

### Phase 05 — Optional Licensed SFX & Layered JianYing Package
1. Apply verified licensed SFX ONLY when present (`sound.py` + guarded `sfx.py`); otherwise record `SFX_NOT_AVAILABLE`.
2. Export layered package via unmodified `jianying_layers.mjs` + `jianying_plan.py` (`jianying/layers/manifest.json`, `jianying/plan.json`, optional `--preview`). Distinguish `LAYERED_PACKAGE_VERIFIED` from `NATIVE_EDITOR_PROJECT_NOT_CREATED`.

### Phase 06 & 07 — Independent Export QA, Diagnostics & Human Sign-Off
1. Run `qa.py::validate_structural()`, `qa.py::inspect_audio()`, `source_analysis.py::verify_source_integrity()`, person-crop freeze & mouth-sync checks, and `qa.py::render_timeline_diagnostics()` (boundary filmstrips + PCM waveform + output-clock word labels + `frame_diagnostics.csv`).
2. Emit `FINAL_QA.json`, `QUALITY_DIFF.md`, `DELIVERY_REPORT.md`, `OWNER_REVIEW.md`, `RUN_MANIFEST.json` / `SHA256SUMS`, keeping `OWNER_VISUAL=PENDING`, `OWNER_AUDIO=PENDING`, and `COMMERCIAL_RELEASE=BLOCKED`.
