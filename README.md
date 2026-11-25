# Seecut-AI — KH-03.R3 Universal Editing & Creative Motion Intelligence (atop KH-02.R2 / SeeCut v3.4 Core)

Antigravity skill (`seecut-ai`) combining the **KH-03.R3 Creative Motion & Visual Intelligence** cross-niche decision layer (**5 universal editing skills `SK-01..SK-05`**, mandatory integrity-bound user-selection gate, **CMI-01..CMI-10** semantic hook director, declarative internal-node motion graphs, face-safe shared-element morphing, subject/object/scene-cut tracking, universal cross-niche evidence ledgers, 360px/WCAG/multilingual typography, duration-weighted visual density/anti-repetition, motion-anchored licensed sound direction, true `O(1)`-RAM streaming decoded frame QA, and bounded async DAG execution) directly on top of the **KH-02.R2** single-path live pipeline (`scripts/kh02/`) and the unmodified **YeJe-cpu/SeeCut v3.4 Core** rendering engine.

> **Required Notice**: Copyright (c) 2026 YeJe-cpu (https://github.com/YeJe-cpu)
> **License**: [PolyForm Noncommercial License 1.0.0](./LICENSE) (`https://polyformproject.org/licenses/noncommercial/1.0.0`). See [NOTICE](./NOTICE) and [references/kh02-video-use-attribution.md](./references/kh02-video-use-attribution.md).
> **Commercial Use (`PROBLEM-01`)**: NOT permitted under PolyForm Noncommercial 1.0.0 without written permission from the SeeCut author (`aiesecxuanli@gmail.com`). Commercial release remains fail-closed (`BLOCKED`). Independent clean-room previews (Path B) live outside the tracked repo and are never conflated with native SeeCut gates.

---

## Owner Quick-Start Example (Mandatory Two-Step Selection)

```bash
# 0. Check host environment, disk floor (>= 5 GiB), tools & license posture:
python -m scripts.kh03 preflight

# 1. Supply raw video + script -> AI analyzes & issues 5-skill menu -> STOPS:
python -m scripts.kh03 recommend \
  --video /path/to/raw_source.mp4 \
  --script /path/to/script.txt \
  --run-root runs/my_project_01 \
  --layout-policy ADAPTIVE_DYNAMIC \
  --cut-policy PRESERVE_SCRIPT_TRIM \
  --caption-policy ON_REQUESTED

# 2. User explicitly chooses ONE of SK-01, SK-02, SK-03, SK-04, or SK-05:
python -m scripts.kh03 select --run-root runs/my_project_01 --skill SK-04

# 3. Execution resumes via bounded async DAG -> CMI-01..10 -> KH-02 pipeline -> streaming QA:
python -m scripts.kh03 execute \
  --run-root runs/my_project_01 \
  --mode plan \
  --workers 2 \
  --max-memory-mib 4096 \
  --queue-frames 8
# (Use --mode preview or --mode full when pinned upstream SeeCut + HyperFrames tools and lawful license are present.)

# 4. Verify selection binding, SHA continuity & status:
python -m scripts.kh03 status --run-root runs/my_project_01
python -m scripts.kh03 verify --run-root runs/my_project_01 [--require-render]
```

---

## Repository Layout

```text
.
├── SKILL.md                             # Updated KH-03.R3 interactive router + mandatory selection contract
├── LICENSE                              # Verbatim PolyForm Noncommercial License 1.0.0 + Required Notice
├── NOTICE                               # Verbatim SeeCut + selective MIT video-use third-party notices
├── schemas/
│   ├── kh02/                            # 13 authoritative KH-02/R1/R2 JSON schemas (preserved intact)
│   └── kh03/                            # 15 strict versioned KH-03/R2/R3 JSON schemas
├── references/
│   ├── kh03/                            # Shared doctrine, ADL-01..07, CMI-01..10 & SK-01..SK-05 contracts
│   └── kh02-*.md / gates-and-compliance.md
├── research/kh03/                       # Versioned METADATA_ONLY research snapshot (no scraped footage)
├── templates/
│   ├── kh02/                            # Legacy KH-02 examples (preserved intact)
│   └── kh03/                            # Cross-niche input fixtures (NOT fixed shot templates)
├── scripts/
│   ├── kh03/                            # KH-03.R3 Universal Editing & Creative Motion Intelligence modules
│   │   ├── __init__.py / __main__.py    # Package version (kh03.3.0), VALID_SKILL_IDS (SK-01..SK-05), CLI entry
│   │   ├── contracts.py                 # Strict KH-03 schemas, legal state machine, anti-injection sanitizer
│   │   ├── execution.py                 # CMI-10: StreamingRGBFrameDecoder, BoundedAsyncExecutor, AtomicKeyedCache, RAM/disk budgets
│   │   ├── intelligence.py              # ffprobe, ASR reconciliation, semantic graph, numeric/action extraction
│   │   ├── tracking.py                  # CMI-04: multi-subject/object/faceless streaming tracking & scene-cut intelligence
│   │   ├── evidence_and_type.py         # CMI-05 universal evidence/rights ledger + CMI-06 360px/WCAG/VI/CJK/RTL typography
│   │   ├── motion_graph.py              # CMI-01 hooks, CMI-02 motion graph, CMI-03 shared-element morphs, CMI-07 visual density
│   │   ├── recommend.py                 # 5-skill registry, cross-niche scoring rubric, menu issuer (no auto-select)
│   │   ├── selection.py                 # Fail-closed SELECTION_RECEIPT.json bound to run/video/script/rec SHAs
│   │   ├── research.py                  # Archived METADATA_ONLY reader + offline fail-closed adapter
│   │   ├── footage.py                   # Per-beat evidence planner, strict rights validator, SSRF-safe downloader
│   │   ├── explanation.py               # Typed explanation graphs, exact Decimal math checks, viewer Q&A
│   │   ├── layout.py                    # ADL-01..07 adaptive dynamic layouts, smooth morphing & anti-repetition
│   │   ├── components.py                # 8 compiled KH-03 component families, internal nodes, XSS/eval sandbox, collision solver
│   │   ├── sound_director.py            # CMI-08: motion-anchored multi-layer sound director & <=1f onset QA
│   │   ├── director.py                  # 3 internal directions/skill, single-pass EDL remapper & KH-02 override compiler
│   │   ├── visual_qa.py                 # CMI-09/10: true streaming decoded QA, internal ROI motion, native pinned GSAP gallery
│   │   ├── qa.py                        # 100-pt editorial rubric, hard-defect overrides & separated gates
│   │   └── router.py                    # Two-step CLI orchestrator routing directly into scripts/kh02/run.py
│   └── kh02/                            # Preserved KH-02.R2 single-path live pipeline (backward-compatible hooks)
├── tests/
│   ├── kh02/                            # Existing 88-test KH-02/R1/R2 regression suite
│   └── kh03/                            # KH-03/R1/R2 (61 tests) + R3-01..R3-30 suite (30 tests, 60x holdout matrix, 30m streaming)
└── reports/                             # Redacted compact verification summaries (KH02/, KH02R1/, KH02R2/, KH03/, KH03R2/, KH03R3/)
```

---

## Running Tests & Verification

```bash
# Run full KH-03 + R1 + R2 + R3 suite (91 tests incl. 60x holdouts, 30s..30m streaming, workers=1/2/4, native GSAP gallery):
python -m unittest discover -s tests/kh03 -t . -v

# Run legacy KH-02 regression suite (88 tests, backward compatibility):
python -m unittest discover -s tests/kh02 -t . -v

# Legacy explicit KH-02 CLI remains fully supported:
python -m scripts.kh02.run --brief templates/kh02/BRIEF.example.json --run-root /tmp/kh02_dry --mode plan --dry-run
```

---

## Environment, Providers, Rights & Honest Fallbacks
- **Local Prerequisites**: Python 3.12+, `ffmpeg`/`ffprobe`, Node.js 22+ (plus Chromium for native component gallery verification; pinned upstream `YeJe-cpu/SeeCut` + `hyperframes@0.9.86` + `playwright-core@1.65.1` required for native full SeeCut MP4 rendering).
- **Optional Licensed Footage & SFX Libraries**: Pass `--footage-library <dir>` (with per-asset rights `manifest.json`) and `--sfx-library <dir>`. Never store API keys or credentials inside the repo. When external providers are absent or offline, KH-03 reports `FOOTAGE_PARTIAL` / `A_ROLL_AND_GRAPHICS_ONLY` / `BLOCKED_SFX_ASSETS` honestly without injecting `TEST_ONLY` tones or fabricated UI.
- **No Fake PASS**: `OWNER_VISUAL`, `OWNER_AUDIO`, and `COMMERCIAL_RELEASE` stay explicitly `PENDING` / `BLOCKED` until real human review and written commercial authorization.
