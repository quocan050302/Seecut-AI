# KH-02.R2 Verification & Delivery Summary (`20261124T094047Z_KH02R2_f610fa5_e9bdc8`)

> **Redaction Notice**: This repository report contains zero local absolute paths, zero secrets/credentials, and zero media/audio/frame binaries. Full local run artifacts remain inside the isolated external run folder.

## 1. Git & Engine Pinning
- **Branch**: `QA` (base SHA `f610fa51d0038f0e9629273cb419ed065ae21901`; `master` untouched at `ae51ccd4127c95a456e96c66d46a77fb65142b80`)
- **Pinned Engine**: Unmodified `YeJe-cpu/SeeCut @ b05d7ed27404a064060e0d370402f562b0c0c02e` (**PolyForm Noncommercial 1.0.0**; `COMMERCIAL_RELEASE=BLOCKED`)
- **Selective Helper Reference**: Unmodified `browser-use/video-use @ 43cfc566833548093455c9d4a46bd69913b7e552` (MIT)
- **Security Invariants**: Zero `--dangerously-skip-permissions`, zero `npx @latest`, zero synthetic/fake SFX in production renders.

## 2. Live Integrated Pipeline & Presenter Policy Proof
- **Single-Path CLI**: `python3 -m scripts.kh02.run --brief <brief.json> --run-root <run_dir> --mode full` executes Stages `A..N` with SHA-linked `STAGE_RECEIPTS/`.
- **Presenter Policy Contract**: Explicit optional `presenter_policy` (`LEGACY_DYNAMIC` default for v1/R1 backward compatibility; `FIXED_PIP`, `ADAPTIVE_PIP`, `DYNAMIC_HYBRID`, `FACELESS`) with platform-aware safe zones, face-safe PIP crop math, swept-bbox collision solver, and streaming decoded per-frame PIP QA (`0..N-1`).
- **Configurable SFX & Streaming Audio**: Real CLI/brief/config/env provider loading (`--sfx-library`, `--sfx-config`, `JIANJI_SFX_DIR`) + windowed streaming PCM analysis. Real multi-layer stem mixing/muxing verified end-to-end on isolated `TEST_ONLY` fixtures (`R1-18`, `R2-19`); live host SFX reported honestly as `BLOCKED_PROVIDER_CONFIG`.

## 3. Regression & Cross-Niche Verification
- **88-Group Suite Executed Twice**:
  - Pass 1: **`88/88 PASS`** (`49.619s`)
  - Pass 2: **`88/88 PASS`** (`48.402s`)
  - Coverage: `36` KH-02 Core (`T01..T36`) + `28` KH-02.R1 (`R1-01..R1-28`) + `24` KH-02.R2 (`R2-01..R2-24`), `0` fail, `0` skip, `0` flaky diff.
- **Unseen Cross-Niche Native Smoke**: `16:9` software tutorial (`FIXED_PIP`, `BOTTOM_RIGHT`, `SYNTHETIC_SMOKE`) completed end-to-end via normal CLI (`preview` mode, H1 PASS, decoded PIP QA PASS).

## 4. Olivia `FIXED_PIP` Bottom-Left Remake (`candidate_v3`, Max 3 Enforced)
| Metric | Raw Source | SC-01 Adopted | KH-02 Adopted | KH-02.R1 `v3` | **KH-02.R2 `candidate_v3`** |
|---|---|---|---|---|---|
| Presenter Policy | Raw | `LEGACY_DYNAMIC` | `LEGACY_DYNAMIC` | `LEGACY_DYNAMIC` | **`FIXED_PIP` Bottom-Left (`0..N-1`)** |
| Frames / Duration | `476f` (`19.861s`) | `476f` (`19.862s`) | `476f` (`19.862s`) | `404f` (`16.834s`) | **`203f` (`8.459s`, `-11.375s`)** |
| Spoken Words | `26/26` | `26/26` | `26/26` | `26/26` | **`26/26` exact order (`0.3ms` drift)** |
| Silence (`-55dB >=0.2s`) | `9.122s` | `9.122s` | `9.122s` | `6.266s` | **`0.316s` total / `0.316s` max (`-96.5%`)** |
| Loudness / True Peak | `-22.2 LUFS / -6.3 dBTP` | `-22.2 LUFS / -6.3 dBTP` | `-22.2 LUFS / -6.3 dBTP` | `-22.2 LUFS / -6.3 dBTP` | **`-21.8 LUFS / -6.3 dBTP`** |
| Compiled Capabilities | N/A | Upstream | KH-02 v1 | 7 | **`10` verified (`11` events)** |
| H1 `geom-check.mjs` | N/A | PASS | PASS | PASS | **`PASS` (`FAIL=0, WARN=0`)** |
| Decoded PIP QA (`0..N-1`) | N/A | N/A | N/A | N/A | **`PASS` (`203/203 = 100.0%`, `0` occ, `0` frz)** |
| Live H2 Check | N/A | PASS | PASS | PASS | **`FAIL` (`5/6 PASS`: `C1,C4,C5,C6,C7`; `C3` 2f seam overlap at `00:03.5`)** |
| Dual-Order Pairwise | N/A | Baseline | Baseline | Baseline | **All 3 `valid=(True,True)`, `consensus=B` -> `NOT_ADOPTED`** |
| JianYing Layered Export | N/A | Exported | Exported | Exported | **`PASS` (`LAYER_FIDELITY_PARTIAL`, `19` PNGs, `12` segs, PIP box matched)** |

## 5. Honest Remaining Gates
- `ADOPTION_STATUS`: `NOT_ADOPTED` (`candidate_v3` retained honestly; upstream dynamic-hide pairwise rubric + `C3` 2-frame transition seam at `00:03.5`)
- `LIVE_SOUND_DESIGN`: `BLOCKED_PROVIDER_CONFIG` (0 licensed local/MCP SFX on host; 0 fake tones injected)
- `OWNER_VISUAL`: `PENDING`
- `OWNER_AUDIO`: `PENDING`
- `COMMERCIAL_RELEASE`: `BLOCKED`
