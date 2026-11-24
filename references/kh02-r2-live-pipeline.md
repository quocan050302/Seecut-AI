# KH-02.R2 Live Pipeline Orchestration & Stage Receipt Contract

## 1. Single Canonical Entrypoint (`python3 -m scripts.kh02.run`)
All production, preview, dry-run, planning, and verification runs execute through one integrated graph inside `scripts/kh02/run.py::execute_pipeline()`:

```
Stage A (License / Env / Source Inventory / Registry / Presenter Policy)
  -> Stage B (Transcript Normalization & SHA Binding)
  -> Stage C (Rough Cut Candidates & Single Authoritative EDL)
  -> Stage D (Paired A/V Cut Application via apply_r1_cut_map + verify_cut_result)
  -> Stage E (Truthful Opening Hook Candidates & Pacing Budget)
  -> Stage F (Canonical SHOTBOOK V2 + Auto-Layout Collision Solver)
  -> Stage G (Configurable Local/MCP SFX Discovery, Acquisition & Soundscape Binding)
  -> Stage H (Compile Master Timeline -> build/master.html, project.json, COMPILE_MAP.json)
  -> Stage I (Guarded Native Probe + Unmodified Upstream H1 geom-check.mjs)
  -> Stage J (Guarded Native Full Render + Real Multi-Layer Stem Mix & Checked Mux)
  -> Stage K (Post-Mix Structural, Audio, Decoded Per-Frame Presenter, Effect/Seam/Hold QA)
  -> Stage L (Optional Real H2 agy_judge_safe.py Check & Dual-Order Pairwise on Exact Final SHA)
  -> Stage M (Optional Guarded Upstream JianYing Layered Export + Fidelity Report)
  -> Stage N (Stage Receipt Audit & Honest Final Status — Owner Approvals PENDING)
```

## 2. Stage Receipts & Hash Continuity
Every non-dry-run stage writes an atomic JSON receipt under `STAGE_RECEIPTS/<stage_id>.json` recording:
- `stage_id`, `version`, `run_id`, `timestamp_utc`, `upstream_seecut_sha`
- `input_hashes` and `output_hashes` (SHA-256 linkage across EDL -> derived clip -> SHOTBOOK -> HTML -> final mixed MP4)
- `commands`, `duration_sec`, `warnings`, `evidence_paths`, `test_only`, `status`, `reason`

## 3. CLI Flags Implemented
```bash
python3 -m scripts.kh02.run \
  --brief <brief.json> \
  --run-root <isolated_run_dir> \
  --mode plan|preview|full|verify \
  [--dry-run] [--offline] [--workers 1..4] [--max-disk <GiB>] \
  [--profile seecut_core|editorial_pro|minimal_documentary|silent_visual] \
  [--cut-policy LOCK_VOICE|PRESERVE_SCRIPT_TRIM|EDITORIAL_CUT|TRIM_APPROVED] \
  [--presenter-mode LEGACY_DYNAMIC|FIXED_PIP|ADAPTIVE_PIP|DYNAMIC_HYBRID|FACELESS] \
  [--presenter-anchor TOP_LEFT|TOP_RIGHT|BOTTOM_LEFT|BOTTOM_RIGHT|CUSTOM] \
  [--presenter-shape ROUNDED_RECT|CIRCLE] [--presenter-width-ratio 0.30] \
  [--sfx-provider local|mcp|api|auto|off] [--sfx-policy OPTIONAL_LICENSED|OFF|REQUIRED_LICENSED] \
  [--sfx-library <licensed_dir>]... [--sfx-config <approved_cfg.json>] [--sfx-cache <cache_dir>] \
  [--music-policy OFF|OPTIONAL_LICENSED|REQUIRED_LICENSED] \
  [--transcript <words.json>] [--catalog <hyperframes_catalog.json>] \
  [--run-h2] [--compare-baseline <baseline.mp4>]... \
  [--export-layers] [--candidate-version 1..3] [--output-basename <stem>]
```

## 4. Exit Codes & Honest Blockers
- `0`: Requested mode succeeded (`DRY_RUN_PASS`, `PLANNED_OK`, `VERIFY_PASS`, or `RENDER_VERIFIED_PENDING_OWNER`).
- `1`: Technical validation/compile/H1/QA/layout failure.
- `2`: License gate (`BLOCKED_COMMERCIAL_LICENSE` / `BLOCKED_LICENSE_UNRESOLVED`) or missing/invalid input (`BLOCKED_INPUT` / `BLOCKED_PRESENTER_SOURCE`).
- `3`: Required licensed SFX/asset unavailable (`BLOCKED_SFX_ASSETS`).
- Secrets are never passed via CLI flags or logged into receipts/manifests; `TEST_ONLY` audio fixtures are strictly blocked from production renders.
