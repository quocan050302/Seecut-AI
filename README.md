# Seecut-AI

Antigravity skill (`seecut-ai`) wrapping the original **YeJe-cpu/SeeCut v3.4 Core** motion-graphics talking-head editing workflow with fail-closed license gating, non-bypass `agy` CLI wrappers, workspace isolation, and honest export QA / human approval gates.

> **Required Notice**: Copyright (c) 2026 YeJe-cpu (https://github.com/YeJe-cpu)  
> **License**: [PolyForm Noncommercial License 1.0.0](./LICENSE) (`https://polyformproject.org/licenses/noncommercial/1.0.0`). See [NOTICE](./NOTICE) for third-party notices.  
> **Commercial Use**: NOT permitted under PolyForm Noncommercial 1.0.0 without written permission from the SeeCut author (`aiesecxuanli@gmail.com`).

## What Is Included

```text
.
├── SKILL.md                          # Antigravity skill instructions (name: seecut-ai)
├── LICENSE                           # Verbatim PolyForm Noncommercial License 1.0.0 + Required Notice
├── NOTICE                            # Verbatim third-party license notices
├── references/
│   ├── upstream-index.md             # Reading hierarchy, script security audit & doc conflict log
│   └── gates-and-compliance.md       # 19 mandatory fail-closed pipeline gates
└── scripts/
    ├── safe_preflight.sh             # Non-bypass preflight (SKIP_AGY_PING=1 + safe agy ping + audit)
    ├── agy_read_safe.sh              # Non-bypass agy video understanding wrapper (no --dangerously-skip-permissions)
    └── agy_judge_safe.py             # Non-bypass agy H2 check + order-swapped pairwise judge wrapper
```

## Setup (Antigravity Skill)

1. Clone this skill and symlink it into Antigravity's global skills folder:
   ```bash
   ln -s "$(pwd)" ~/.gemini/config/skills/seecut-ai
   ```
2. Clone the pinned unmodified upstream SeeCut repository separately into your standalone workspace (not bundled in this repo):
   ```bash
   mkdir -p ~/Projects/SeeCut-Olivia-Standalone/third_party
   git clone https://github.com/YeJe-cpu/SeeCut.git ~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut
   git -C ~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut checkout b05d7ed27404a064060e0d370402f562b0c0c02e
   ```
3. Install pinned workspace-local tools (`hyperframes@0.9.86`, `playwright-core@1.65.1`, and `faster-whisper` in an isolated Python `.venv`) and run:
   ```bash
   zsh scripts/safe_preflight.sh
   ```

## Guardrails

- **Noncommercial License Gate**: Strictly respects PolyForm Noncommercial 1.0.0; commercial release remains `BLOCKED` until written author authorization is logged.
- **No Silent Permission Bypass**: Never runs `--dangerously-skip-permissions` silently; verified non-bypass wrappers work on Antigravity CLI `1.5.15`.
- **No Fake PASS**: Requires real H1 geometry checks (`geom-check.mjs`), real H2 video review (`agy_judge_safe.py check`), order-swapped pairwise adoption (`agy_judge_safe.py pair`, max 3 versions), real full-resolution HyperFrames render, layered JianYing package, independent Export QA, and explicit human visual/audio approval (`OWNER_VISUAL_APPROVAL=PENDING`, `OWNER_AUDIO_APPROVAL=PENDING`).
