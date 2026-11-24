---
name: seecut-ai
description: >-
  Run the original YeJe-cpu/SeeCut v3.4 Core motion-graphics talking-head editing
  pipeline (HyperFrames + HTML/GSAP + render.mjs + H1 geom-check + H2 agy Gemini
  review + order-swapped pairwise iteration up to 3 versions + layered JianYing
  package + independent QA) with strict PolyForm Noncommercial 1.0.0 license gating,
  non-bypass agy security checks, isolated workspaces, and honest human-approval gates.
  Use when the user asks to edit a talking-head or pre-voiced AI-avatar video using
  SeeCut, seecut-ai, HyperFrames/GSAP motion graphics, or AI self-review loops.
  Does NOT generate avatars/TTS or silently substitute other editors (Concat/Remotion).
---

# seecut-ai — Safe-Gated YeJe-cpu/SeeCut v3.4 Core Pipeline

> **Required Notice**: Copyright (c) 2026 YeJe-cpu (https://github.com/YeJe-cpu)  
> **Upstream License**: PolyForm Noncommercial License 1.0.0 (`https://polyformproject.org/licenses/noncommercial/1.0.0`). See [LICENSE](./LICENSE) and [NOTICE](./NOTICE).  
> **Commercial Licensing Contact**: `aiesecxuanli@gmail.com`.

This skill orchestrates the **original `YeJe-cpu/SeeCut` v3.4 Core** edit workflow in a strictly isolated workspace with fail-closed gates for licensing, security permissions, source integrity, evidence truthfulness, geometry/video QA, and human visual/audio approval.

## Non-Negotiable Guardrails

1. **Hard License Gate (Run First)**:
   - Audit upstream `LICENSE` and `NOTICE` before executing any edit/render.
   - `YeJe-cpu/SeeCut` is licensed under **PolyForm Noncommercial 1.0.0**, NOT MIT.
   - If the intended use includes monetized social media, ads, client services, resale, or any anticipated commercial application, **STOP** with `BLOCKED_COMMERCIAL_LICENSE`, cite `aiesecxuanli@gmail.com`, and do not run an unlicensed commercial production.
   - Only execute SeeCut after confirming strictly noncommercial personal/research/educational use (with no anticipated commercial application) OR logging explicit written commercial permission from the author.
2. **Hard Security / Permission Gate**:
   - Upstream `skill/seecut/scripts/preflight.sh`, `agy_read.sh`, and `agy_judge.py` pass `--dangerously-skip-permissions` to `agy`.
   - **Never** run `--dangerously-skip-permissions` silently or modify upstream scripts to fake a security PASS.
   - Always run [scripts/safe_preflight.sh](./scripts/safe_preflight.sh) first (`SKIP_AGY_PING=1` + safe non-bypass `agy` check). When authorized by the owner, use the audited no-bypass wrappers [scripts/agy_read_safe.sh](./scripts/agy_read_safe.sh) and [scripts/agy_judge_safe.py](./scripts/agy_judge_safe.py) (verified `exit 0` on Antigravity CLI `1.5.15`, preserving 100% of upstream prompts, schemas, 540p transcode, tmp isolation, fingerprint/duration checks, and order-swapped pairwise logic without `--dangerously-skip-permissions`). If any upstream script requiring `--dangerously-skip-permissions` must be executed directly, stop at `PENDING_OWNER_SECURITY_APPROVAL` with the exact command and security consequences.
3. **Strict Engine & Workspace Isolation**:
   - Create a fresh timestamped run directory per job; never overwrite prior runs or touch unrelated repositories, branches, CapCut/JianYing user profiles, or unrelated global skills.
   - **Only** use SeeCut's native stack: HyperFrames + HTML/GSAP + `scripts/render.mjs` + `ffmpeg` audio mux + `scripts/geom-check.mjs` + `agy` (`check`/`pair`) + `scripts/jianying_layers.mjs` + `scripts/jianying_plan.py`.
   - Never substitute Concat, Remotion, mimic-mcp, Chase Animations, video-use, or cloud renderers.
   - When editing an already-voiced talking-head / AI-avatar clip, keep avatar/TTS generation **DISABLED** (no HeyGen, Doubao TTS, voice cloning, or lip-sync synthesis).
4. **Input & Evidence Truthfulness**:
   - Require the real absolute local path to the primary talking-head video. Never substitute a competitor or reference video; if absent, stop with `BLOCKED_INPUT` and complete only safe Phase 00 preparation.
   - Any optional reference video is strictly `ANALYSIS_ONLY` (never imported into renders or packages).
   - Never fabricate product UIs, mock Mac/App windows, fake metrics, or unverified screenshots (`00-总纲.md` §4–5). Mask source identifiers (logos/handles/avatars) on real screenshots and log visible text to `build/fp-manifest.txt`.
5. **No Fake PASS / Mandatory Human Approval**:
   - Every PASS/FAIL/BLOCKED/PENDING status must point to real generated files and logs.
   - Keep `OWNER_VISUAL_APPROVAL=PENDING` and `OWNER_AUDIO_APPROVAL=PENDING` until the owner actually watches and hears the verified exported MP4 SHA-256.

---

## Authoritative Upstream & References

- **Pinned Upstream Clone**: `~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut` (or `$SEECUT_UPSTREAM_ROOT`; Git SHA `b05d7ed27404a064060e0d370402f562b0c0c02e`, SeeCut `v3.4`, unmodified).
- **Pinned Workspace Tools**: `~/Projects/SeeCut-Olivia-Standalone/tools/` (`hyperframes@0.9.86`, `playwright-core@1.65.1`, Apache-2.0, plus isolated `.venv` with `faster-whisper`).
- Read [references/upstream-index.md](./references/upstream-index.md) for the exact reference reading hierarchy (`00-总纲.md` highest priority) and logged upstream documentation conflicts.
- Read [references/gates-and-compliance.md](./references/gates-and-compliance.md) for all 19 mandatory reporting gates and fail-closed criteria.

---

## Execution Workflow (Phases 00–07)

### Phase 00 — Environment, License & Security Audit
1. Create a fresh unique run directory `<project_root>/runs/<YYYYMMDD_HHMMSS>_<RUN_TAG>/` with subdirectories `build/`, `qc/`, `exports/`, `jianying/layers/`, `logs/`, `evidence/`, `probe/`.
2. Verify upstream repo remote HEAD vs pinned SHA (`b05d7ed27404a064060e0d370402f562b0c0c02e`); write `UPSTREAM_SHA.txt` and `LICENSE_AUDIT.md`. Enforce the **Hard License Gate**.
3. Probe primary talking-head input via `ffprobe` (duration, resolution, native fps, audio stream, full decode QA) and compute streaming `SHA-256` (`SOURCE_SHA256.json`). If missing, stop at `BLOCKED_INPUT` after safe preparation.
4. Run [scripts/safe_preflight.sh](./scripts/safe_preflight.sh) and log `PRE_FLIGHT.log` + `ENV_REPORT.json`. Confirm:
   - `node >= 22`, `ffmpeg`/`ffprobe`, `python3`, workspace-local `faster-whisper` (inside `.venv`, no global/sudo), pinned `hyperframes` CLI + live `hyperframes catalog --json`, `playwright-core` + local headless Chromium cache, and authorized `agy` CLI (`agy models` + safe non-bypass ping).
   - Check free disk space and memory/swap pressure; default heavy render concurrency to `1`.

### Phase 01 — Source Understanding & ASR
1. Understand the real talking-head video via `agy` video understanding (honoring the Security Gate — no silent `--dangerously-skip-permissions`) and inspect timestamped frames. Save `素材台账.md` and `REFERENCE_STYLE_NOTES.md` (if an `ANALYSIS_ONLY` reference exists).
2. Run `faster-whisper` once locally on the source audio to generate word-level `seg_words.json`. Verify every key phrase/number against actual speech.
3. Classify A-roll framing per `02-设计令牌Design-Reference.md` §6.5 (full half-body / corner PIP / decorative), measure exact normalized `face_rect = [x0, y0, x1, y1]`, and classify every spoken sentence as evidence-backed (`有据`) or opinion/concept (`无据`).
4. Collect real user-approved evidence or capture real pages via `grab_evidence.mjs`; inspect every image for sensitive text, mask source identifiers, record `EVIDENCE_LEDGER.md`, `EVIDENCE_GAPS.md`, and `build/fp-manifest.txt`.

### Phase 02 — Creative Strategy & SHOTBOOK
1. Read upstream references in strict priority order: `00-总纲.md` → `08-避坑SOP.md` → `01-方法论.md` + `03-编排层决策表.md` → `02-设计令牌Design-Reference.md` → `04-SHOTBOOK施工规格.md` → `05-视觉质量地板.md` (+ `11-无据段画法参考.md` for opinion beats).
2. Keep the original spoken audio uninterrupted as the timeline backbone. Map 2–4 meaningful visual beats for ~15s clips (never force extra beats to mimic a longer reference).
3. Inspect the live pinned `hyperframes catalog --json` first, plus bundled `assets/hf-hw/` (`hw-callout-circle`, `hw-underline`, `hw-arrow`). Every shot must name a real registry component or state why none applies.
4. Anchor every payoff reveal to the exact spoken **keyword** frame from `seg_words.json` (never early / never sentence-start). Write `DESIGN_REFERENCE.md`, `SHOTBOOK.md`, `SHOTBOOK.json`, `BEAT_SYNC.md`, `HF_COMPONENT_SELECTION.md`.

### Phase 03 — HyperFrames / HTML / GSAP Build & Render
1. Author `build/project.json` matching `07-几何规则自检spec.md` (`source`, native `fps`, `width`, `height`, `duration`, `output_scale`, `face_rect`, `steady`, `spring`) and `build/master.html` exposing `window.__timelines.master`.
2. Let `scripts/render.mjs` extract person JPG frames automatically from `source` at native `fps` (`<img>` frame swap — never `<video>` seek). Start delayed elements at CSS `opacity: 0` with GSAP `.set` + `.to` (never `.from`).
3. Tag all elements with `data-check="box"|"bg"` (`bg` ONLY on verified screenshot/recording media, never paper/solid backgrounds), `data-anchor`, `data-word` + `data-t`, and `data-layer` + `data-layer-cat`.
4. Run `node scripts/render.mjs build probe` and visually inspect probe frames before full render. Use `RENDER_SCALE=0.5` for intermediate iterations (`v1`/`v2`) and full native scale (no `RENDER_SCALE` override) for the adopted final render; mux untouched original audio with `ffmpeg` and verify AV sync (`< 0.1s` drift).

### Phase 04 — Self-Review Loop (H1 + H2 + Pairwise, Max 3 Versions)
1. **H1 Geometry Gate**: Run `node scripts/geom-check.mjs build`; capture logs to `qc/vN-geom.log`. Fix every real collision, face occlusion, empty beat, early reveal, missing avatar, or spring failure (never strip `data-check` tags).
2. **H2 Video Hard-Defect Gate**: Run `agy` check (`check.json` schema + `硬伤清单.txt`, isolated tmp dir, 540p transcode, fingerprint >= 2 hits, duration delta <= 3s, respecting the Security Gate). Inspect extracted fail-frames at the exact flagged timestamp before dismissing any false positive.
3. **Pairwise Adoption (`v2`, `v3`)**: Compare candidate vs currently adopted winner with order-swapped runs (`X=A/Y=B` and `X=B/Y=A`). Adopt ONLY when both orders agree AND H1/H2 pass. Treat `invalid` as "comparison did not occur" (never as tie or pass). Stop at max `v3` or no win. Save `qc/`, `CRITIC_REPORT.md`, `REVIEW_DECISION.json`.

### Phase 05 — Optional Licensed SFX & Core Layered Package
1. Preserve the original voice track untouched. Run `sfx_ledger.py` + `sfx.py plan` (+ `sfx.py mix` when no JianYing draft engine is installed) ONLY if a verified licensed SFX library exists; otherwise record `SFX_NOT_AVAILABLE` (never fabricate placeholder SFX).
2. Run `node scripts/jianying_layers.mjs build <run>/jianying/layers` and `python3 scripts/jianying_plan.py <run>/jianying/layers/manifest.json --name "<title>" --out <run>/jianying/plan.json` (plus optional `--preview`). Verify transparent PNGs, person-state MP4s, original audio, events, and manifest.
3. Do NOT invent or force a JianYing draft unless a verified, licensed draft engine (`Easy JianYing`) is explicitly installed and authorized. Never touch existing CapCut/JianYing profiles.

### Phase 06 & 07 — Independent Export QA, Manifest & Human Sign-Off
1. Verify full `ffmpeg` decode, `ffprobe` streams/fps/duration, SHA-256 of exported MP4, source-vs-export before/after SHA-256 integrity (`SOURCE_INTEGRITY.json`), mouth-sync stills (start/mid/end + beat boundaries), and person-region crop freeze check.
2. Generate `FINAL_QA.json`, `QUALITY_DIFF.md`, `RIGHTS_LEDGER.csv`, `PROVENANCE.md`, `OWNER_REVIEW.md`, `DELIVERY_REPORT.md`, `README.md`, and `MANIFEST.json` (SHA-256 for all outputs and evidence).
3. Report all 19 status gates truthfully with exact file paths, keeping `OWNER_VISUAL=PENDING` and `OWNER_AUDIO=PENDING` until owner playback sign-off.
