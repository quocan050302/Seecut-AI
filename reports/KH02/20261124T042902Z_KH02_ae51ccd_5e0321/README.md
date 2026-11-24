# KH-02 Execution Summary — `20261124T042902Z_KH02_ae51ccd_5e0321` (Redacted)

> Small redacted evidence summary committed to repository. Heavy media outputs, raw logs, frame buffers, and local workstation paths remain strictly outside Git inside the isolated standalone workspace run folder `runs/20261124T042902Z_KH02_ae51ccd_5e0321/`.

## 1. Provenance & Licensing
- **Repository / Branch**: `https://github.com/quocan050302/Seecut-AI.git` — branch **`QA`** (owner-requested branch from `master`; `master` untouched)
- **Base Commit (`origin/master`)**: `ae51ccd4127c95a456e96c66d46a77fb65142b80`
- **Pinned Upstream SeeCut (Unmodified)**: `https://github.com/YeJe-cpu/SeeCut` @ `b05d7ed27404a064060e0d370402f562b0c0c02e` (`PolyForm Noncommercial License 1.0.0`, `0` dirty files)
- **Pinned Reference `video-use` (Read-Only MIT Audit)**: `https://github.com/browser-use/video-use` @ `43cfc566833548093455c9d4a46bd69913b7e552` (`MIT License`, selective helper adaptation only; `helpers/render.py` never executed)
- **License Posture**: `NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED` | **`COMMERCIAL_RELEASE = BLOCKED`** (requires written permission from `aiesecxuanli@gmail.com`)

## 2. Separated Gate Statuses
- `CODE_QA_PASS`: **`PASS`** (`36/36` unit/negative/regression/browser/smoke tests passed cleanly across 2 consecutive runs, `0` fail, `0` skip)
- `OLIVIA_RENDER_VERIFIED`: **`PASS`** (full native `1080×1920 @ 24fps`, `476` frames, `19.862s`, untouched stereo AAC PCM correlation `1.0`, EBU R128 `-22.2 LUFS` / `-6.3 dBTP`, source SHA-256 unchanged before/after)
- `AUTO_EDITORIAL_QA`: **`PASS`** (`v2` passed H1 `geom-check.mjs` `0 FAIL / 0 WARN`, passed H2 `agy_judge_safe.py check` `6/6` rubric `{C1,C3,C4,C5,C6,C7}`, and won order-swapped pairwise `["A","A"]`, confidence `5/5` in both orders vs SC-01 baseline)
- `OWNER_VISUAL`: **`PENDING`** (human playback sign-off required)
- `OWNER_AUDIO`: **`PENDING`** (human listening sign-off required)
- `COMMERCIAL_RELEASE`: **`BLOCKED`** (PolyForm Noncommercial 1.0.0)
- `CROSS_NICHE_VALIDATED`: **`PARTIAL_SMOKE_VERIFIED`** (6+ niche contracts pass; 3 unseen synthetic smoke renders verified via pinned `render.mjs` + `ffprobe`; real-footage non-talking-head niches remain `NOT_VERIFIED_REAL_FOOTAGE`)
- **Overall Classification**: **`PREVIEW_PENDING_APPROVAL`** (never claiming `READY_FOR_PRODUCTION` while commercial rights & owner approvals remain open)

## 3. Artifact SHA-256 Registry (Local Workspace Relative Paths)
- Raw Olivia Input (`READ_ONLY`, unchanged): `olivia.mp4` — `c83a3fee672a3725b3f7ffd2d784c70f1b405883f94b8df343273a0305a3543d`
- SC-01 Baseline (`ANALYSIS_ONLY`, unchanged): `SeeCut_Olivia_SC01_adopted.mp4` — `d4a7cc1b1586c5a36fc348aa2873810d985506e79b663d6d3f53b2efcece0e28`
- Masked Web Evidence (`READ_ONLY`, unchanged): `evidence/evid_01_gumroad_masked.png` — `c9194736075cde41d9543235476b8fa96a492fd1ac62c5a4a408cdf3519c0174`
- KH-02 Adopted Full Native Export (`v2`): `exports/SeeCut_Olivia_KH02_adopted.mp4` — `590ec7c5f747c3349718f805e06958335f0e4761730b8d181f38135824699df9` (`17,487,101` bytes, `476` frames, `19.862s`)
- Layered Package: `jianying/layers/manifest.json` (`21` PNGs, `5` person states, `25` cue events), `jianying/plan.json` (`7` tracks), `jianying/preview.mp4` (`LAYERED_PACKAGE_VERIFIED`, `NATIVE_EDITOR_PROJECT_NOT_CREATED`)
- Unseen Cross-Niche Synthetic Smoke Renders (`render.mjs` + `ffprobe` + EBU R128 PASS):
  - `talking_head (9:16, en)`: `bb14ffda443d157df0f0fc66a9aaff5510945a3f53c973502d08b72207d49704`
  - `software_demo (16:9, en)`: `8e3301a6f9ab9335173d27e281bc8639c7c9e923824830fb981cf50631626391`
  - `education (1:1, vi)`: `4de7ead332d190d78cfc1900d758296f552eaaa0cc20ed098cac8e122b77c9c7`

## 4. Verification Commands
```bash
# Re-run full offline preflight & 36-test suite:
zsh scripts/safe_preflight.sh --offline
python3 -m unittest discover -s tests/kh02 -v
```
