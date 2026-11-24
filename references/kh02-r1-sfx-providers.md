# KH-02.R1 — Licensed SFX Discovery, MCP Acquisition & Multi-Layer Sound Design

## 1. Rights & Provider Separation (`scripts/kh02/sfx_assets.py`)
- **MCP is a retrieval protocol, NOT a grant of audio rights.**
- **Provider software license / API contractual terms are tracked separately from individual audio asset rights**:
  - `SFXMint` (`sfxmint/sfxmint-mcp`, `https://sfxmint.com/api/docs`): Free search without key (rate-limited); API/MCP downloads require account API key (`SFXMINT_API_KEY`) with daily download caps (`429` honored without bypass).
  - `Freesound` (`sandraschi/sfx-mcp`, `https://freesound.org/docs/api/terms_of_use.html`): Free API tier is **non-commercial only** even when individual files are `CC0`; `CC-BY` requires author/attribution metadata; `CC-BY-NC` blocked for commercial use.
  - `SoundFetch` (`Losera/soundfetch`): Multi-provider adapter; every individual asset's license metadata must be verified before acceptance.
- **Fail-Closed Verification**: `CC0` preferred; `CC-BY` accepted only with attribution metadata; `NC`/`ND`/`UNKNOWN`/filename-only claims rejected.
- **`TEST_ONLY` Isolation**: Synthetic PCM tones and mock fixtures are tagged `test_only=true` / `TEST_ONLY_FIXTURE` and blocked at runtime (`assert_not_test_only_in_production()`) from entering production renders or upstream ledgers.

## 2. Acquisition Order & Security Hardening
1. Approved local library (`LocalProvider`, bounded entries/bytes/depth/walltime, zero symlinks, no HOME scan).
2. Verified persistent local cache (`audit_cache_integrity()` fails closed on any SHA-256 drift).
3. One configured & owner-authorized MCP / HTTPS provider (`McpProvider` via real discovered `tools/list`, HTTPS host allowlist, SSRF/private-IP blocking, redirect limit, byte cap, atomic `.part` download, `ffprobe` decode verification, content-hash naming, deduplicated concurrent downloads with shared quota).
4. Truthful `BLOCKED_PROVIDER_CONFIG` / `BLOCKED_SFX_ASSETS` fallback (zero fake placeholders or unapproved global MCP installs).

## 3. Multi-Layer Sound Design & Adaptive Density (`scripts/kh02/audio_mix.py`, `sound.py`)
- Adaptive density profiles (`calm`, `explanatory`, `proof`, `kinetic`, `suspense`, `emotional`) provide illustrative guidance only — **never mandatory minimum cue counts**.
- Every meaningful visual event receives explicit coverage status: `ADD`, `KEEP_NATURAL`, `SILENCE`, `MERGE_WITH_OTHER`, or `UNAVAILABLE`.
- Conflicts with upstream `sfx.py` talking-head guidelines (`<=2/3s`, `<=0.7/s`) are explicitly logged; richer caps require `ALLOW_R1_ADAPTIVE_SFX_DENSITY` in `approved_exceptions`.
- Real 48kHz stereo PCM stems (`dry_voice.wav`, `sfx.wav`, `ambience.wav`, `music.wav`, `mix.wav`) are rendered via chunked FFmpeg argv graphs (zero shell injection, `normalize=0` exact gains, smooth sidechain ducking `20–80ms` attack / `150–400ms` release, soft true-peak limiter).
- Post-mix QA verifies real EBU R128 + dBTP, measured cue onset alignment (`max(1 frame, 50ms)`), and dry-vs-mix speech window preservation (`sfx_plan.json`, `SFX_APPLIED.json`, `SFX_ONSET_QA.json`).

