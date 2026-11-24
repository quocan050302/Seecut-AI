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

## Required Separated Status Flags
Reports MUST never collapse these 7 independent statuses into one PASS:
- `CODE_QA_PASS`
- `OLIVIA_RENDER_VERIFIED`
- `AUTO_EDITORIAL_QA`
- `OWNER_VISUAL` (`PENDING` until genuine owner watch signoff)
- `OWNER_AUDIO` (`PENDING` until genuine owner listen signoff)
- `COMMERCIAL_RELEASE` (`BLOCKED` under PolyForm Noncommercial 1.0.0)
- `CROSS_NICHE_VALIDATED` (`TEST_ONLY` / `PARTIAL_SMOKE_VERIFIED` / `VERIFIED`)
