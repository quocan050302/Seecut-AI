# Gates & Compliance Contract (`seecut-ai`)

Every `seecut-ai` execution MUST evaluate and report all **19 gates** with one of `PASS`, `FAIL`, `PENDING`, or `BLOCKED`, accompanied by an exact local evidence path.

| # | Gate | Fail-Closed Condition | Required Evidence |
|---|---|---|---|
| 1 | `ENV` | Host OS/CPU/RAM/disk/tools unverified or critically low disk/RAM | `ENV_REPORT.json` |
| 2 | `INPUT` | Primary talking-head video missing, unreadable, no audio, or substituted with ref/competitor clip | `INPUTS_AND_RIGHTS.md`, `SOURCE_SHA256.json` |
| 3 | `LICENSE` | Upstream `LICENSE`/`NOTICE` unaudited, or intended usage ambiguous/unlicensed commercial | `LICENSE_AUDIT.md`, `UPSTREAM_SHA.txt`, `RIGHTS_LEDGER.csv` |
| 4 | `PREFLIGHT` | Required dependency missing (`ffmpeg`, `node>=22`, `faster_whisper`, `agy`, pinned `hyperframes`, `playwright-core`) or unsafe bypass executed | `PRE_FLIGHT.log` |
| 5 | `AGY` | `agy` unauthenticated, region-blocked, or `--dangerously-skip-permissions` gate unresolved | `logs/agy_*.txt`, `PRE_FLIGHT.log` |
| 6 | `ASR` | `faster-whisper` word-level transcript not run or unverified against audio | `seg_words.json`, `素材台账.md` |
| 7 | `EVIDENCE` | Fabricated UI/evidence, unmasked sensitive text, or missing `EVIDENCE_GAPS.md` | `EVIDENCE_LEDGER.md`, `EVIDENCE_GAPS.md`, `build/fp-manifest.txt` |
| 8 | `SHOTBOOK` | Beats not grounded in real word timestamps or missing registry component citations | `SHOTBOOK.md`, `SHOTBOOK.json`, `BEAT_SYNC.md`, `HF_COMPONENT_SELECTION.md` |
| 9 | `HYPERFRAMES` | Mutable `@latest` used without pin, catalog unverified, or substitute editor used | `logs/hyperframes_catalog.json`, `build/master.html`, `build/project.json` |
| 10 | `H1` | `geom-check.mjs` not executed or has unresolved FAILs | `qc/vN-geom.log` |
| 11 | `H2` | `agy check` not executed, `invalid`, or FAILs dismissed without inspecting exact timestamp fail-frames | `qc/vN-check.json`, `qc/vN-check_failframes/` |
| 12 | `PAIRWISE` | Multi-version adoption without dual-order consensus (`A` in both orders), or `invalid` treated as tie/pass | `qc/vN-vs-prev.json`, `REVIEW_DECISION.json` |
| 13 | `SFX` | Fabricated/unlicensed SFX used; missing licensed library must be marked `SFX_NOT_AVAILABLE` (not FAIL of core video) | `sfx_plan.json` or `DELIVERY_REPORT.md` |
| 14 | `LAYERED_PACKAGE` | `jianying_layers.mjs` + `jianying_plan.py` not generated/verified for adopted version | `jianying/layers/manifest.json`, `jianying/plan.json` |
| 15 | `FINAL_MP4` | Missing real HyperFrames-rendered full-resolution MP4 at native source fps with untouched original audio | `exports/<project>_SeeCut_adopted.mp4`, `MANIFEST.json` |
| 16 | `EXPORT_QA` | Missing full ffmpeg decode check, ffprobe audit, before/after source SHA-256 check, mouth-sync stills, or person-crop freeze check | `FINAL_QA.json`, `SOURCE_INTEGRITY.json`, `QUALITY_DIFF.md` |
| 17 | `OWNER_VISUAL` | Always `PENDING` until owner watches the verified exported MP4 SHA-256 and approves | `OWNER_REVIEW.md` |
| 18 | `OWNER_AUDIO` | Always `PENDING` until owner listens to the verified exported MP4 SHA-256 and approves | `OWNER_REVIEW.md` |
| 19 | `COMMERCIAL_RELEASE` | Always `BLOCKED` under PolyForm Noncommercial 1.0.0 unless written commercial license from `aiesecxuanli@gmail.com` is logged AND all media/likeness rights cleared | `LICENSE_AUDIT.md`, `RIGHTS_LEDGER.csv` |
