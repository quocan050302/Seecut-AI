# Upstream Reference Index & Logged Conflicts (`YeJe-cpu/SeeCut` v3.4 + `video-use` Selective Reference)

- **Authoritative Core Engine Upstream**: `https://github.com/YeJe-cpu/SeeCut`
  - **Pinned Commit SHA**: `b05d7ed27404a064060e0d370402f562b0c0c02e` (`2026-10-30 17:46:33 +0800`, unmodified read-only checkout)
  - **License**: PolyForm Noncommercial License 1.0.0 (`Required Notice: Copyright (c) 2026 YeJe-cpu`)
  - **Default Local Path**: `~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut` (override via `SEECUT_UPSTREAM_ROOT`)
- **Selective Helper Reference Upstream**: `https://github.com/browser-use/video-use`
  - **Pinned Commit SHA**: `43cfc566833548093455c9d4a46bd69913b7e552` (`2026-10-08`, read-only audit checkout; NEVER executed as final renderer)
  - **License**: MIT License (`Copyright (c) 2026 Browser Use`). See [kh02-video-use-attribution.md](./kh02-video-use-attribution.md).

## Mandatory Reading Priority (Per `00-总纲.md`)

When internal reference documents disagree, the higher-precedence file wins (`00-总纲.md` is supreme) and every conflict MUST be logged without silent harmonization.

1. `skill/seecut/references/00-总纲.md` — 16 supreme rules (highest authority).
2. `skill/seecut/references/08-避坑SOP.md` — Frame-by-frame rendering laws (`<img>` swap, `.time(i/fps)`, `opacity:0` + `.set`/`.to`, watchdogs).
3. `skill/seecut/references/01-方法论.md` + `03-编排层决策表.md` — Two-layer model and 13 sentence-function rules.
4. `skill/seecut/references/02-设计令牌Design-Reference.md` — Geek Craft design tokens and §6.5 A-roll framing classes.
5. `skill/seecut/references/04-SHOTBOOK施工规格.md` — SHOTBOOK spec + official HyperFrames catalog-first rule.
6. `skill/seecut/references/05-视觉质量地板.md` — Visual quality floor.
7. `skill/seecut/references/06-质检闭环spec.md` — H1 + H2 + order-swapped pairwise loop (max 3 versions).
8. `skill/seecut/references/07-几何规则自检spec.md` — `project.json` contract and H1 geometry checks.
9. `skill/seecut/references/09-音效规范.md` — Optional licensed SFX placement, volume targets, density caps.
10. `skill/seecut/references/10-剪映分层工程.md` — Core layered editable package.
11. `skill/seecut/references/11-无据段画法参考.md` — Motion-graphic techniques for opinion/no-evidence segments.
12. KH-02 Additive References (`references/kh02-*.md`) — Editorial doctrine, motion/camera/transitions/typography, sound QA, cross-niche matrix, `video-use` MIT attribution.

## Upstream Scripts Security & KH-02 Adapter Map

| Upstream Script | Role | Audited Risk & KH-02 Guarded Solution |
|---|---|---|
| `SeeCut/scripts/preflight.sh` | Env check | Passes `--dangerously-skip-permissions`, uses `npx -y hyperframes@latest`, exits 0 on `MISS`. **Replaced by** hardened `scripts/safe_preflight.sh` (pinned tools, no `@latest`, exact normalized `OK`, JSON output, strict exit `0/1/2`). |
| `SeeCut/scripts/agy_read.sh` | Video read | Passes `--dangerously-skip-permissions`, lacks trap/allowlist checks. **Replaced by** hardened `scripts/agy_read_safe.sh` (symlink/allowlist rejection, ownership-marked trap cleanup, transmission policy gate). |
| `SeeCut/scripts/agy_judge.py` | H2 check & pair | Passes `--dangerously-skip-permissions`. **Replaced by** hardened `scripts/agy_judge_safe.py` (rc-before-parse, strict schema/bool checks, full rubric `{C1,C3,C4,C5,C6,C7}`, distinct SHA-bound fingerprint hits, calibrated duration tol, strict `X`/`Y` winner validation, safe `grab_fail_frames`). |
| `SeeCut/scripts/render.mjs` | Sole frame renderer | Derives `person_dir` from JSON and runs `fs.rmSync(PD)` with weak `{src,fps}` stamp. **Guarded by** `scripts/kh02/render_guard.py` (canonical path containment, `.kh02_run_owned` marker, composite cache identity, disk floor, atomic mux + full decode QA). |
| `SeeCut/scripts/geom-check.mjs` | H1 geometry QA | Reused unmodified on KH-02 compiled builds (`build/master.html` + `build/project.json`). |
| `SeeCut/scripts/jianying_layers.mjs` + `jianying_plan.py` | Phase 05 layered package | Reused unmodified; `compile_plan.py` guarantees sanitized `data-layer` names & `data-layer-cat` tags. |
| `SeeCut/scripts/sfx.py` + `sfx_ledger.py` | Optional SFX | Guarded via `scripts/kh02/sound.py` (rights/bounds/finite gain/real EBU R128 checks, honest `SFX_NOT_AVAILABLE`). |
| `video-use/helpers/pack_transcripts.py` | Phrase packing (MIT) | Selectively adapted into `scripts/kh02/transcript.py` for local `faster-whisper`. |
| `video-use/helpers/timeline_view.py` | Filmstrip + waveform (MIT) | Selectively adapted into `scripts/kh02/qa.py::render_timeline_diagnostics`. |
| `video-use/helpers/render.py` | Video renderer (MIT) | **REJECTED AS RENDERER**; only `chunk_words`/`build_master_srt` (opt-in captions) and `measure_loudness` referenced. |

## Logged Internal Upstream Documentation Conflicts

1. **HyperFrames Catalog Count**: `00-总纲.md` §11 states `394 项`, whereas live pinned `hyperframes@0.9.86` contains **448 items** (`210` blocks + `238` components).
2. **Legacy Remotion / `student-kit` Mentions**: Superseded by `04-SHOTBOOK施工规格.md` & `CHANGELOG.md` v3.3.1 — HyperFrames HTML/GSAP is the sole active v3.4 rendering path.
3. **Hardcoded 30fps Snippet in `08-避坑SOP.md`**: Always use exact native rational fps from `project.json` (`fps_num/fps_den`).
4. **Hardcoded `JIANJI_PROXY=http://127.0.0.1:1087`**: Only set when a verified local proxy listens on that port.
5. **`agy --dangerously-skip-permissions` Assumption**: Empirically verified unnecessary on Antigravity CLI `1.5.15` for both text ping and `@video.mp4` reading/judging.
6. **Upstream `00-总纲.md` No-Hard-Cut / Manual BGM & Captions vs KH-02 Profiles**: Preserved as default in `seecut_core` and `editorial_pro` (`LOCK_VOICE`, captions `OFF`, BGM `OFF`, eased transitions). Any profile override (e.g., `ALLOW_INTENTIONAL_CUT` in `minimal_documentary` or `ON_REQUESTED` captions) MUST be explicitly logged in `approved_exceptions[]` with rationale and approver.
