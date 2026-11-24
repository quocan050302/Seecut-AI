# Seecut-AI (KH-02.R1 Autonomous Professional Editor)

Antigravity skill (`seecut-ai`) combining the **KH-02.R1 Autonomous Professional Video Editor** (Intelligent Rough Cut, Hook & Pacing Director, Licensed SFX Discovery & MCP Adapter, Multi-Layer Sound Design & Chunked PCM Stem Mixing, Verified Advanced Effects + Deterministic Chromium Effect Gallery, unified `SHOTBOOK_V2`, P0 hardened wrappers, selective MIT `video-use` diagnostic helpers, and independent QA) with the unmodified **YeJe-cpu/SeeCut v3.4 Core** rendering engine (`HyperFrames + HTML/CSS/GSAP + Playwright/Chromium + FFmpeg`).

> **Required Notice**: Copyright (c) 2026 YeJe-cpu (https://github.com/YeJe-cpu)
> **License**: [PolyForm Noncommercial License 1.0.0](./LICENSE) (`https://polyformproject.org/licenses/noncommercial/1.0.0`). See [NOTICE](./NOTICE) and [references/kh02-video-use-attribution.md](./references/kh02-video-use-attribution.md) for third-party notices.
> **Commercial Use**: NOT permitted under PolyForm Noncommercial 1.0.0 without written permission from the SeeCut author (`aiesecxuanli@gmail.com`).

## Repository Layout

```text
.
├── SKILL.md                             # Antigravity skill instructions (name: seecut-ai)
├── LICENSE                              # Verbatim PolyForm Noncommercial License 1.0.0 + Required Notice
├── NOTICE                               # Verbatim SeeCut + selective MIT video-use third-party notices
├── schemas/kh02/                        # 13 authoritative KH-02/R1 JSON schemas (brief, inventory, transcript, edl, cut_candidate, hook_plan, shotbook_v2, timeline, registry, sfx_asset, sfx_provider_status, sfx_cues, qa_report)
├── templates/kh02/                      # Positive & negative example JSON contracts
├── references/                          # Upstream index, 19+10+13 gates, R1 rough-cut/hook/SFX/effects docs, doctrine & MIT attribution
├── scripts/
│   ├── safe_preflight.sh                # Hardened P0 preflight (pinned tools, no @latest, exact OK, JSON output, exit 0/1/2)
│   ├── agy_read_safe.sh                 # Hardened non-bypass agy video reader (allowlist, ownership trap cleanup)
│   ├── agy_judge_safe.py                # Hardened non-bypass H2 check & order-swapped pair judge (strict schema/rubric/fingerprint/failframes)
│   └── kh02/                            # Modular stdlib-first Python editor & QA engine
│       ├── contracts.py                 # Strict JSON, schema validator, rational frame clock, safe_path, reference validator
│       ├── source_analysis.py           # Read-only ffprobe, chunked SHA-256 inventory, silence/motion detection
│       ├── transcript.py                # faster-whisper normalizer (PyAV-19 safe), phrase packer (MIT adapted), cache & remap
│       ├── rough_cut.py                 # R1.01 Intelligent Rough Cut (13 candidate types, LOCK_VOICE/PRESERVE_SCRIPT_TRIM/EDITORIAL_CUT, paired A/V trim & seam verify)
│       ├── edit_decisions.py            # Audio-first EDL, rational time-map & re-exports of R1.01 rough_cut contracts
│       ├── sfx_assets.py                # R1.03 SfxProvider (LocalProvider/McpProvider), license/API-terms separation, SSRF-safe download, onset & cache audit
│       ├── audio_mix.py                 # R1.04 Multi-layer soundscape, adaptive density, chunked PCM stems, sidechain ducking & onset/dry-vs-mix QA
│       ├── sound.py                     # Real EBU R128 LUFS/true peak analysis, cue validator & re-exports of R1.04 audio_mix contracts
│       ├── registry.py                  # Pinned HyperFrames + R1 capability registry, gallery verification & effect variety audit
│       ├── motion.py                    # Adaptive easing grammar, R1 effect emitter & deterministic pose sampler
│       ├── camera.py                    # 2D virtual framing, rotation-aware crop caps, face-safe continuity validator
│       ├── transition.py                # Semantic transitions, anchor handoff solver, seam & continuity validator
│       ├── typography.py                # EN/VI/CJK legibility, 360px phone layout check, caption-OFF default, opt-in SRT (MIT adapted)
│       ├── planner.py                   # Multi-direction planner, R1.02 Hook/Pacing Director, SHOTBOOK_V2 emitter & v1->v2 migration
│       ├── compile_plan.py              # Canonical SHOTBOOK_V2 -> build/master.html (incl. R1 clip-mask, spotlight, kinetic mask, light sweep, freeze callout)
│       ├── effects_gallery.py           # R1.05 Real headless Chromium Effect Gallery verifier (direct/reverse/repeat/cold restart determinism)
│       ├── render_guard.py              # P0 path/marker/cache-key guard, disk floor, pinned render.mjs launcher & atomic mux
│       ├── qa.py                        # Structural decode, audio correlation, boundary filmstrip+waveform (MIT adapted), adoption gate
│       └── run.py                       # Unified CLI (`python3 -m scripts.kh02.run`) with R1 cut/SFX/offline/quota flags
├── tests/kh02/                          # Full 64-group executable regression suite (`T01`–`T36` + `R1-01`..`R1-28`) via stdlib unittest
└── reports/                             # Small redacted run summaries & hashes (`KH02/`, `KH02R1/`) — zero media/secrets/user paths
```

## Quick Verification & Usage

```bash
# 1. Run hardened offline preflight:
zsh scripts/safe_preflight.sh --offline

# 2. Run full executable regression suite (stdlib unittest, zero network/paid calls):
python3 -m unittest discover -s tests/kh02 -v

# 3. Dry-run KH-02 planner & contract check:
python3 -m scripts.kh02.run --brief templates/kh02/BRIEF.example.json --run-root /tmp/kh02_dry --mode plan --dry-run
```

## Guardrails
- **Noncommercial License Gate**: Strictly enforces PolyForm Noncommercial 1.0.0 (`COMMERCIAL_RELEASE=BLOCKED` until written permission from `aiesecxuanli@gmail.com`).
- **Sole Rendering Engine**: Preserves YeJe-cpu/SeeCut v3.4 Core (`HyperFrames + HTML/CSS/GSAP + Playwright + render.mjs`); `video-use/helpers/render.py` is never used as a video renderer.
- **No Silent Permission Bypass & No Fake PASS**: Zero `--dangerously-skip-permissions`, zero `npx @latest`, explicit separation of `CODE_QA_PASS`, `OLIVIA_RENDER_VERIFIED`, `AUTO_EDITORIAL_QA`, `OWNER_VISUAL=PENDING`, `OWNER_AUDIO=PENDING`, `COMMERCIAL_RELEASE=BLOCKED`, and `CROSS_NICHE_VALIDATED`.
