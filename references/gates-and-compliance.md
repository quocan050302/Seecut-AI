# Gates & Compliance Contract (`seecut-ai` + KH-02)

Every `seecut-ai` execution MUST evaluate and report all **19 original gates** AND all **10 additive KH-02 subgates** with one of `PASS`, `FAIL`, `PENDING`, `BLOCKED`, `TEST_ONLY`, or `NOT_APPLICABLE`, accompanied by an exact local evidence path.

## Original 19 Mandatory Gates (Preserved Intact)

| # | Gate | Fail-Closed Condition | Required Evidence |
|---|---|---|---|
| 1 | `ENV` | Host OS/CPU/RAM/disk/tools unverified or critically low disk/RAM | `ENV_REPORT.json` |
| 2 | `INPUT` | Primary talking-head video missing, unreadable, no audio, or substituted with ref/competitor clip | `INPUTS_AND_RIGHTS.md` / `OLIVIA_INPUT_MAP.json`, `SOURCE_SHA256.json` |
| 3 | `LICENSE` | Upstream `LICENSE`/`NOTICE` unaudited, or intended usage ambiguous/unlicensed commercial | `LICENSE_AUDIT.md`, `UPSTREAM_SHA.txt`, `RIGHTS_LEDGER.csv` |
| 4 | `PREFLIGHT` | Required dependency missing (`ffmpeg`, `node>=22`, `faster_whisper`, `agy`, pinned `hyperframes`, `playwright-core`) or unsafe bypass / `npx @latest` executed | `PRE_FLIGHT.log` |
| 5 | `AGY` | `agy` unauthenticated, region-blocked, or `--dangerously-skip-permissions` gate unresolved | `logs/agy_*.txt`, `PRE_FLIGHT.log` |
| 6 | `ASR` | `faster-whisper` word-level transcript not run or unverified against audio | `seg_words.json` / `TRANSCRIPT_NORMALIZED.json`, `TAKES_PACKED.md` |
| 7 | `EVIDENCE` | Fabricated UI/evidence, unmasked sensitive text, or missing `EVIDENCE_GAPS.md` | `EVIDENCE_LEDGER.md`, `EVIDENCE_GAPS.md`, `build/fp-manifest.txt` |
| 8 | `SHOTBOOK` | Beats not grounded in real word timestamps or missing registry component citations | `SHOTBOOK_V2.json`, `SHOTBOOK_V2.md`, `BEAT_SYNC.md`, `HF_COMPONENT_SELECTION.md` |
| 9 | `HYPERFRAMES` | Mutable `@latest` used without pin, catalog unverified, or substitute editor used | `logs/hyperframes_catalog.json`, `build/master.html`, `build/project.json` |
| 10 | `H1` | `geom-check.mjs` not executed or has unresolved FAILs | `qc/vN-geom.log` |
| 11 | `H2` | `agy check` not executed, `invalid`, incomplete rubric `{C1,C3,C4,C5,C6,C7}`, duplicate/missing fingerprint hits, or FAILs dismissed without inspecting exact fail-frames | `qc/vN-check.json`, `qc/vN-check_failframes/` |
| 12 | `PAIRWISE` | Multi-version adoption without dual-order consensus (`A` in both orders), invalid winner mapped silently, or `invalid` treated as tie/pass | `qc/vN-vs-prev.json`, `REVIEW_DECISION.json` |
| 13 | `SFX` | Fabricated/unlicensed SFX used; missing licensed library must be marked `SFX_NOT_AVAILABLE` (not FAIL of core video) | `sfx_plan.json` or `DELIVERY_REPORT.md` |
| 14 | `LAYERED_PACKAGE` | `jianying_layers.mjs` + `jianying_plan.py` not generated/verified for adopted version | `jianying/layers/manifest.json`, `jianying/plan.json` |
| 15 | `FINAL_MP4` | Missing real HyperFrames-rendered full-resolution MP4 at native source fps with untouched original audio | `exports/<project>_adopted.mp4` (or `candidate`), `RUN_MANIFEST.json` |
| 16 | `EXPORT_QA` | Missing full ffmpeg decode check, ffprobe audit, before/after source SHA-256 check, mouth-sync stills, boundary filmstrips/waveforms, or person-crop freeze check | `FINAL_QA.json`, `SOURCE_INTEGRITY.json`, `QUALITY_DIFF.md` |
| 17 | `OWNER_VISUAL` | Always `PENDING` until owner watches the verified exported MP4 SHA-256 and approves | `OWNER_REVIEW.md` |
| 18 | `OWNER_AUDIO` | Always `PENDING` until owner listens to the verified exported MP4 SHA-256 and approves | `OWNER_REVIEW.md` |
| 19 | `COMMERCIAL_RELEASE` | Always `BLOCKED` under PolyForm Noncommercial 1.0.0 unless written commercial license from `aiesecxuanli@gmail.com` is logged AND all media/likeness rights cleared | `LICENSE_AUDIT.md`, `RIGHTS_LEDGER.csv` |

## Additive KH-02 Subgates (10 Orthogonal Verification Gates)

| Subgate | Scope & Fail-Closed Criteria | Primary Module / Evidence |
|---|---|---|
| `KH02_CONTRACT` | Strict JSON (no duplicate keys, `NaN`/`Inf`, bool-as-int), schema compliance, rational frame clock, zero dangling/denied refs | `scripts/kh02/contracts.py`, `SHOTBOOK_V2.json`, `COMPILE_MAP.json` |
| `KH02_EDITORIAL` | Multi-direction creative evaluation (2–3 options), non-template beat graph, factual claim evidence ledger, no hardcoded niche logic | `scripts/kh02/planner.py`, `CREATIVE_DIRECTIONS.md` |
| `KH02_MOTION` | Adaptive easing by profile, GSAP `.set/.to` absolute seek determinism, no spring bounce on serious/documentary profiles | `scripts/kh02/motion.py`, `T18`, `T20` |
| `KH02_CAMERA` | 2D virtual zoom capped by source resolution, zero black underscan/pan edges, face-safe crop, smooth non-cut continuity | `scripts/kh02/camera.py`, `T19` |
| `KH02_TRANSITION` | Motivated transitions (`match_anchor`/`reframe`/`motion_bridge`), zero beat frame gaps, explicit exception for hard cuts | `scripts/kh02/transition.py`, `T21` |
| `KH02_TYPO` | Reading-rate hold floor, 360px mobile legibility, EN/VI/CJK fallback stacks, captions OFF default or top-layer when ON | `scripts/kh02/typography.py`, `T22` |
| `KH02_AUDIO` | Real EBU R128 LUFS + true peak measurement, `LOCK_VOICE` PCM correlation >= 0.995, safe fade/rights cue validation | `scripts/kh02/sound.py`, `T24`, `T25` |
| `KH02_REGRESSION` | Executable `T01`–`T36` suite run twice cleanly + `workers=1` vs `workers=2` deterministic comparison + P0 wrapper hardening | `tests/kh02/`, `TEST_RUN_1.log`, `TEST_RUN_2.log`, `TEST_MATRIX.json` |
| `KH02_OLIVIA` | Fresh Olivia edit from raw `olivia.mp4` (never flattened SC-01 MP4), full native render, H1/H2/pairwise vs SC-01, layered package & Export QA | `exports/SeeCut_Olivia_KH02_*.mp4`, `QUALITY_DIFF.md`, `FINAL_QA.json` |
| `KH02_GENERALIZATION` | Cross-niche fixtures (6+ modes, 16:9/9:16/1:1, EN/VI/CJK, long-form) + honest distinction between `TEST_ONLY` vs smoke renders vs real footage | `references/kh02-cross-niche.md`, `T33`, `T34` |

## Additive KH-02.R1 Subgates (13 Orthogonal Verification Gates)

| Subgate | Scope & Fail-Closed Criteria | Primary Module / Evidence |
|---|---|---|
| `R1_ROUGH_CUT_CONTRACT` | All 13 pause/take candidate classifications, protected meaning/negation/ASR-confidence guard, policy non-broadening (`LOCK_VOICE` / `PRESERVE_SCRIPT_TRIM` / `EDITORIAL_CUT`), rational frame+sample time map | `scripts/kh02/rough_cut.py`, `CUT_CANDIDATES.json`, `CUT_DECISION_REPORT.md`, `EDL.json`, `TIME_MAP.json`, `OUTPUT_TRANSCRIPT.json` |
| `R1_CUT_APPLIED` | Paired derived A/V FFmpeg trim executed before composition (or truthful `NOT_APPLICABLE`), exact frame/sample counts, zero mid-word split, zero seam clicks/drift, unchanged source SHA-256 | `CUT_VERIFICATION.json`, before/after waveforms & mouth-sync stills |
| `R1_HOOK_PACING` | 2–3 distinct truthful opening options evaluated, fake claim/spoiler/speech-reorder blocked, energy-aware pacing budget (`editorial_intent_only=true`, zero fake CTR/retention), payoff audit | `HOOK_OPTIONS.json`, `HOOK_DECISION.md`, `PACING_PLAN.json`, `ATTENTION_BEATS.md`, `EDITORIAL_COVERAGE.json` |
| `R1_SFX_LOCAL_INVENTORY` | Bounded read-only local SFX scan (zero symlinks/HOME scan), real `ffprobe` + onset measurement, truthful `BLOCKED_SFX_ASSETS` when `0` licensed files exist | `scripts/kh02/sfx_assets.py`, `SFX_ASSET_INDEX.json`, `SFX_CACHE_MANIFEST.json` |
| `R1_MCP_LIVE` | Live authorized MCP tool discovery & real downloaded/verified asset; truthful `BLOCKED_PROVIDER_CONFIG` when unconfigured (never faked by search-only or mock) | `SFX_PROVIDER_STATUS.json`, `SFX_ACQUISITION_LOG.jsonl`, `SFX_QUERY_PLAN.json` |
| `R1_SFX_ASSET_RIGHTS` | Per-asset rights (`CC0`/`CC-BY` attribution) separated from provider API terms; rejects `NC`/`ND`/`UNKNOWN`/filename-only; blocks `TEST_ONLY` fixtures in production | `SFX_RIGHTS_LEDGER.csv`, `verify_license()`, `assert_not_test_only_in_production()` |
| `R1_SOUND_PLAN` | Multi-layer soundscape planned alongside visuals, adaptive density with zero artificial minimum counts, upstream `00-总纲` conflict logging, event coverage (`ADD`/`KEEP_NATURAL`/`SILENCE`/`MERGE_WITH_OTHER`/`UNAVAILABLE`) | `scripts/kh02/audio_mix.py`, `sfx_plan.json` |
| `R1_SOUND_MIX` | Real chunked PCM stems (`dry_voice`, `sfx`, `ambience`, `music`, `mix`), smooth sidechain ducking, real EBU R128 + true peak, measured onset alignment, dry-vs-mix speech preservation (honestly `BLOCKED_SFX_ASSETS` on Olivia when no licensed files exist) | `stems/*.wav`, `SFX_APPLIED.json`, `SFX_ONSET_QA.json` |
| `R1_EFFECT_CAPABILITY` | Real compiled R1 visual families (`clip_mask_wipe`, `spotlight_focus`, `kinetic_phrase_mask`, `light_sweep_badge`, `freeze_callout`, `stagger_grid_4`) verified in headless Chromium Effect Gallery (direct/reverse/repeat/cold seek + visible region diff) | `scripts/kh02/effects_gallery.py`, `GALLERY_VERIFICATION.json`, `CAPABILITY_REGISTRY_VERIFIED.json` |
| `R1_SEAM_QA` | Camera pixel coverage/rotation bounds, anchor handoff continuity, zero orphan layers, boundary filmstrips/waveforms ±1.5s, zero flash/audio off-seam | `camera.py`, `transition.py`, `qc/timeline_*.png` |
| `R1_OLIVIA_REMAKE` | Fresh Olivia edit strictly from raw `olivia.mp4` (never flattened SC-01/prior KH-02), real `PRESERVE_SCRIPT_TRIM`, new hook & verified effects, H1/H2/dual-order pairwise vs SC-01 & prior KH-02, native full-res export & honest candidate/adopted status | `exports/SeeCut_Olivia_KH02_R1_*.mp4`, `QUALITY_DIFF.md`, `FINAL_QA.json` |
| `R1_HOLDOUT` | Unseen multi-niche holdouts (calm interview, fast product ad, software tutorial, education, documentary, 2-min long-form stress) with distinct pacing/mix/effect choices & unchanged source hashes | `tests/kh02/test_effects.py::test_r1_27_multi_niche_holdout_and_long_timeline` |
| `R1_REGRESSION` | Full 64-group executable test matrix (`T01`–`T36` + `R1-01`..`R1-28`) executed twice cleanly with deterministic outputs and zero race/duplicate writes | `tests/TEST_RUN_1.log`, `tests/TEST_RUN_2.log`, `TEST_MATRIX.json` |

## Additive KH-02.R2 Subgates (Live Pipeline & Persistent Presenter Verification)

| Subgate | Scope & Fail-Closed Criteria | Primary Module / Evidence |
|---|---|---|
| `R2_PIPELINE_INTEGRATED` | Normal `scripts/kh02/run.py::execute_pipeline()` executes Stages A..N with verified `STAGE_RECEIPTS/` SHA-256 continuity (no manual bypass) | `scripts/kh02/run.py`, `STAGE_RECEIPTS/*.json`, `R2-01` |
| `R2_CUT_APPLIED` / `IDENTITY_VERIFIED` | Authoritative EDL applied via `apply_r1_cut_map()` + `verify_cut_result()` inside normal CLI (or `IDENTITY_VERIFIED` under `LOCK_VOICE`) with derived SHA bound into `project.json` | `EDL.json`, `TIME_MAP.json`, `CUT_VERIFICATION.json`, `R2-02`..`R2-04` |
| `R2_HOOK_APPLIED` | Selected truthful opening compiled visibly at frame `0` with `selected_hook_id` in `SHOTBOOK_V2`, `COMPILE_MAP.json`, and `HOOK_APPLIED_EVIDENCE.json` | `HOOK_CANDIDATES.json`, `SELECTED_HOOK.json`, `HOOK_APPLIED_EVIDENCE.json`, `R2-05` |
| `R2_PRESENTER_POLICY` | Schema-validated optional `presenter_policy` (`LEGACY_DYNAMIC` v1 default, `FIXED_PIP`/`ADAPTIVE_PIP`/`DYNAMIC_HYBRID`/`FACELESS`), policy conflicts & missing source fail closed (`BLOCKED_PRESENTER_SOURCE`), swept-bbox collision solver | `PRESENTER_POLICY.json`, `PRESENTER_LAYOUT.json`, `R2-07`, `R2-09`, `R2-10`, `R2-11` |
| `R2_PRESENTER_FRAME_QA` | Every decoded frame `0..N-1` of rendered MP4 matched against derived source crop inside PIP box (frame 0, mid, seams, EOF); catches occlusion, flash, or frozen presenter | `PRESENTER_FRAME_QA.csv/json`, `PIP_CROP_EVIDENCE/`, `R2-08`, `R2-12` |
| `R2_EFFECTS_APPLIED` | Real applied capability ledger (`EFFECT_APPLICATION_LEDGER.json`), transition seam flash audit (`TRANSITION_SEAM_REPORT.json`), and visual hold audit (`VISUAL_HOLD_AUDIT.json`) | `EFFECT_APPLICATION_LEDGER.json`, `TRANSITION_SEAM_REPORT.json`, `VISUAL_HOLD_AUDIT.json`, `R2-13`, `R2-14` |
| `R2_SFX_LOCAL` / `R2_SFX_PROVIDER_LIVE` | Configurable local/MCP providers via `--sfx-library`/`--sfx-config`/`JIANJI_SFX_DIR` (zero hardcoded empties); honest `BLOCKED_PROVIDER_CONFIG` when absent | `SFX_PROVIDER_STATUS.json`, `MISSING_AUDIO_ASSETS.json`, `CACHE_INTEGRITY.json`, `R2-15`..`R2-18` |
| `R2_SOUND_MIX_VERIFIED` | Normal CLI runs `render_stems` -> `measure_stems` -> `verify_mix_sync` -> streaming `compare_dry_and_mix` -> `mix_audio_checked` whenever verified licensed cues exist | `AUDIO_GRAPH.json`, `STEM_MANIFEST.json`, `MIX_VERIFICATION.json`, `R2-19` |
| `R2_FINAL_POST_MIX_QA` | Structural, audio, presenter, seam, H1, and optional H2/dual-order pairwise bound strictly to exact final mixed MP4 SHA-256 | `FINAL_QA.json`, `H1_GEOM_vN.json`, `R2-20` |
| `R2_LAYERED_FIDELITY` | Guarded upstream `jianying_layers.mjs` + `jianying_plan.py` package verified with honest `LAYER_FIDELITY_PARTIAL` disclosure | `jianying/LAYER_FIDELITY_REPORT.json`, `R2-20` |
| `R2_OLIVIA_RENDER` | Fresh Olivia `FIXED_PIP` bottom-left native `1080x1920@24fps` edit via normal CLI from raw `olivia.mp4`, compared honestly vs SC-01, prior KH-02 adopted & R1 v3 | `exports/SeeCut_Olivia_KH02_R2_FIXED_PIP_*.mp4`, `QUALITY_DIFF.md`, `R2-22` |

## Required Separated Status Flags
Reports MUST never collapse these 7 independent statuses into one PASS:
- `CODE_QA_PASS`
- `OLIVIA_RENDER_VERIFIED`
- `AUTO_EDITORIAL_QA`
- `OWNER_VISUAL` (`PENDING` until genuine owner watch signoff)
- `OWNER_AUDIO` (`PENDING` until genuine owner listen signoff)
- `COMMERCIAL_RELEASE` (`BLOCKED` under PolyForm Noncommercial 1.0.0)
- `CROSS_NICHE_VALIDATED` (`TEST_ONLY` / `PARTIAL_SMOKE_VERIFIED` / `VERIFIED`)
