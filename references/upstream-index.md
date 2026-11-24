# Upstream Reference Index & Logged Conflicts (`YeJe-cpu/SeeCut` v3.4)

- **Upstream Repository**: `https://github.com/YeJe-cpu/SeeCut`
- **Pinned Commit SHA**: `b05d7ed27404a064060e0d370402f562b0c0c02e` (`2026-10-30 17:46:33 +0800`)
- **Default Local Unmodified Path**: `~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut` (override via `SEECUT_UPSTREAM_ROOT`)

## Mandatory Reading Priority (Per `00-总纲.md` & SC-01 Spec)

When internal reference documents disagree, the higher-precedence file wins (`00-总纲.md` is supreme) and the conflict MUST be logged in the delivery report without silent harmonization.

1. `skill/seecut/references/00-总纲.md` — 16 supreme rules (highest authority).
2. `skill/seecut/references/08-避坑SOP.md` — Frame-by-frame rendering laws (`<img>` swap, `.time(i/fps)`, `opacity:0` + `.set`/`.to`, watchdogs).
3. `skill/seecut/references/01-方法论.md` + `03-编排层决策表.md` — Two-layer model (continuous audio backbone + person/info layers) and 13 sentence-function rules.
4. `skill/seecut/references/02-设计令牌Design-Reference.md` — Geek Craft design tokens (invariant core + content-adaptive layer, §6.5 A-roll framing classes).
5. `skill/seecut/references/04-SHOTBOOK施工规格.md` — SHOTBOOK schema, 5 director questions, official HyperFrames catalog-first rule.
6. `skill/seecut/references/05-视觉质量地板.md` — Visual quality floor (no blurred-face backgrounds, no harsh pure-red banners, no empty beats).
7. `skill/seecut/references/06-质检闭环spec.md` — H1 geometry + H2 `agy check` + order-swapped `agy pair` consensus loop (max 3 versions).
8. `skill/seecut/references/07-几何规则自检spec.md` — `project.json` schema contract and H1 deterministic geometry rules.
9. `skill/seecut/references/09-音效规范.md` — Optional licensed SFX placement, volume targets (-16 to -11 dB below voice peak), density caps.
10. `skill/seecut/references/10-剪映分层工程.md` — Core layered package (`data-layer` / `data-layer-cat`, transparent PNGs, person-state videos, `plan.json`).
11. `skill/seecut/references/11-无据段画法参考.md` — 6 motion-graphic techniques for opinion/no-evidence segments.

## Upstream Scripts Security & Behavior Map

| Script | Role | Audited Security / Runtime Notes |
|---|---|---|
| `scripts/preflight.sh` | Environment check | Line 12 passes `--dangerously-skip-permissions` unless `SKIP_AGY_PING=1`; line 22 runs mutable `npx -y hyperframes@latest` unless `hyperframes` is on `PATH`; line 18 suggests `JIANJI_PROXY=http://127.0.0.1:1087`. |
| `scripts/agy_read.sh` | Video/audio understanding | Line 11 hardcodes `--dangerously-skip-permissions` and `--model gemini-3.8-flash-high`; copies target into `mktemp -d` and runs `rm -rf "$D"`. |
| `scripts/agy_judge.py` | H2 `check` & `pair` judge | Line 103 hardcodes `--dangerously-skip-permissions` and `--model gemini-3.8-flash-high`; isolates video in system tmp at 540p; checks fingerprint >= 2 hits and duration delta <= 3s. |
| `scripts/render.mjs` | Frame capture & person extraction | Auto-extracts `person/p%04d.jpg` from `project.json` `source` at native `fps` (`fs.rmSync(PD)` on `person_dir`); supports `probe` and `full` + `RENDER_SCALE`. |
| `scripts/geom-check.mjs` | H1 geometry QA | Single in-browser `page.evaluate`; enforces `steady`, `face_rect`, overlap, face occlusion, empty beats, word timing, spring overshoot > 1.02. |
| `scripts/grab_evidence.mjs` | Real web screenshot capture | Headless Chromium screenshot via `playwright-core`; optional `JIANJI_PROXY`. |
| `scripts/sfx_ledger.py` / `sfx.py` | Optional SFX indexing & mixing | Requires user's own licensed SFX directory; never synthesizes placeholder SFX. |
| `scripts/jianying_layers.mjs` / `jianying_plan.py` | Core layered editable package | Exports paper PNG, element PNGs, per-state person MP4s, original audio, `manifest.json`, `plan.json`, and optional ffmpeg preview. |
| `scripts/wait_for.sh` | Long-task log waiter | ASCII completion marker + bounded timeout + byte-offset tailing; replaces handwritten loops. |

## Logged Internal Upstream Documentation Conflicts

1. **HyperFrames Catalog Count**: `00-总纲.md` §11 states `394 项`, whereas `04-SHOTBOOK施工规格.md` §1 line 43 and `CHANGELOG.md` v3.3.1 state the live `hyperframes catalog` count is authoritative. Live pinned `hyperframes@0.9.86` catalog verified on 2026-11-24 contains **448 items** (`210` blocks + `238` components).
2. **Legacy Remotion / `student-kit` / `talkcraft` Mentions**: `01-方法论.md` §7, `02-设计令牌Design-Reference.md` §8.5, and `05-视觉质量地板.md` still contain Round 1/2 notes mentioning Remotion and `student-kit`/`talkcraft` mixing. However, `04-SHOTBOOK施工规格.md` line 46 (`不用 Remotion`), `05-视觉质量地板.md` line 48, `CHANGELOG.md` v3.3.1, and `SKILL.md` establish **HyperFrames HTML/GSAP** as the sole active v3.4 rendering path (`student-kit` is not bundled).
3. **Hardcoded 30fps Snippet in `08-避坑SOP.md`**: `08-避坑SOP.md` §5 line 36 shows `-framerate 30` as an example ffmpeg mux command, whereas `08-避坑SOP.md` §1 line 18 and `render.mjs` require using the **exact source video fps** from `project.json`. Always use native source fps.
4. **Hardcoded Proxy Assumption**: `SKILL.md` line 12 recommends `export JIANJI_PROXY=http://127.0.0.1:1087`, which is specific to proxy-behind-firewall setups. Never set `JIANJI_PROXY` unless a verified local proxy is actually listening on that port; on direct networks, leave it unset.
5. **`agy` `--dangerously-skip-permissions` Requirement**: `README.en.md` line 59 claims headless `agy` calls need `--dangerously-skip-permissions`. Empirical verification on Antigravity CLI `1.5.15` (macOS 14.4.1, 2026-11-24) proved BOTH text ping and `@video.mp4` video understanding succeed (`exit 0`) **without** `--dangerously-skip-permissions`.
