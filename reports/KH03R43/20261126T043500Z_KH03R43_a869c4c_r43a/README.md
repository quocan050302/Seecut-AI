# KH-03.R4.3Intelligence — Semantic Editing, Cinematic Composition & Async Acceleration (Sanitized Summary)

- **Run ID**: `20261126T043500Z_KH03R43_a869c4c_r43a`
- **Branch / Audited Baseline**: `feat/kh03-universal-editing-intelligence` @ `a869c4c61a4d889fc1dce47baadaf8064dfe7899`
- **Protected Branches Untouched**: `origin/QA` (`7e8a93d3e70ad4f4deb16e50c1d502764d0f2bb9`), `origin/master` (`ae51ccd4127c95a456e96c66d46a77fb65142b80`)

## Universal Cross-Niche Architecture Implemented (R4.3-01 .. R4.3-08 + Async Acceleration)

1. **Canonical Semantic Timeline Compiler (`scripts/kh03/semantic_timeline.py`)**:
   - Introduced versioned `INDEPENDENT_RENDER_SPEC_V2` (`compile_semantic_timeline`, `validate_render_spec`, `build_source_registry`, `remap_events_to_output_clock`, `bind_execution_artifacts`) binding arbitrary N sources (`MUTE_DEFAULT` auxiliary audio), voice EDL, creative/layout/motion/sound/evidence plans, declarative camera events, seam coverage, and canonical SHA-256 hashes onto one authoritative output clock.
   - Wired directly into `scripts/kh03/router.py::run_execute` independent branch with `--production-profile quick|standard|high`, `--cache-dir`, and `--job-brief`, eliminating percentage-split fallbacks and internal skill-ID text leaks.
2. **Universal Semantic Motion Library & Spatial Composition (`scripts/kh03/motion_components.py`)**:
   - Retained 5 baseline primitives and added 6 genuinely animated generic primitives (`resource_depletion`, `resource_allocation`, `contrast_transformation`, `evidence_spotlight`, `causal_chain`, `decision_branch`) validated on non-finance units (`min`, `kcal`, `GB`, `pts`).
   - Added fail-closed payload validation (blocking unsupported kinds, internal `SK-0x`/debug strings, non-conserving allocation math), brief-driven palette tokens, measured glyph auto-fit, LRU layer caching, ROI-only NumPy alpha compositing, and swept-interval spatial collision QA.
3. **Shared Ingest Cache & Parallel Pipeline (`scripts/kh03/ingest_cache.py`, `scripts/kh03/independent_renderer.py`)**:
   - Content-addressed atomic locked LRU cache (`SharedIngestCache`) sharing single-pass source scan, 48 kHz PCM extraction, word-level ASR, and reusable low-res candidate proxies (never upscaled as final).
   - Overlapped silent visual encode + audio stem mix (`ThreadPoolExecutor`) with fast stream-copy mux (`-c:v copy -c:a aac -movflags +faststart`), plus `"luxury_lounge"` clean-room procedural BGM and `"sub_drop"` SFX.
4. **Fail-Closed Backend-Neutral Independent QA (`scripts/kh03/independent_qa.py`, `scripts/kh03/qa.py`, `scripts/kh03/router.py`)**:
   - Added `INDEPENDENT_PREVIEW` and `INDEPENDENT_FULL` gate profiles marking native-only gates (`G_H1_GEOM`, `G_H2_JUDGE`, `G_NATIVE_PROOF`) `NOT_APPLICABLE` via explicit profile rules while enforcing 100% decoded frame coverage, intentional reading-hold separation, targeted full-res component motion proof, spatial collision proof, continuity seam proof, post-AAC-mux `ebur128` LUFS/true-peak, post-render ASR diff, and raw-artifact recomputation in `run_verify()`.

## Real Video Delivery Proof (Stored Outside Git Repository)

- **Artifact**: `exports/Olivia_V7_Luxury_Ramen_KH03R43_9x16_FINAL_CANDIDATE.mp4`
- **SHA-256**: `53721db5465e3b80e2aaf9b12624ba921eae1dd32bdc006810dab8c2b35874ca`
- **Source SHA-256**: `339ddd429b9fd306fba7f2bf7276eeae97e84048097cacac28b908f19da772e3`
- **Format**: `1080x1920` (9:16), H.264 `yuv420p`, `24/1 CFR`, `913` frames, `38.0417s`, AAC `48 kHz` stereo, `+faststart`
- **Skill / Backend**: `SK-01` | `INDEPENDENT_FFMPEG_PIL` (`AUTHORIZED_INDEPENDENT_CLEAN_ROOM`); `NATIVE_SEECUT` remains `BLOCKED_COMMERCIAL_LICENSE`
- **Technical QA & Verify**: `technical_pass=true`, `verify_status=VERIFY_PASS` (`8/8` components verified, `0` collisions, `0` black frames, `0` unintended freezes, `-15.1 LUFS`, `-2.8 dBTP`, `av_diff=0.0s`, post-render ASR `100.0%` / `97/97` words, `0` missing numbers, `0` unintended duplicates)
- **Candidates (`540x960`)**: `cand_A` (`db8a6943...`), `cand_B` (`241b3227...`), `cand_C` (`7c068dc8...`) — all `3/3 PASS`; provisional binding (`NOT_EDITORIALLY_APPROVED`)
- **Approval State**: `CANDIDATE_RENDERED` | `EDITORIAL_PENDING` | `OWNER_VISUAL=PENDING` | `OWNER_AUDIO=PENDING` | `FINAL_APPROVED=false`

## Measured Performance (Separate From Developer Regression)

- **COLD Standard Production (`38.04s`, 3 previews + 1 final `1080x1920` + full QA + post ASR)**: `160.844s` (`2m 41s`, target `300-600s` **MET**, `2.10x` faster than audited R4.2 HOTFIX `338.03s`)
- **Final `1080x1920` Encode (1 encode total)**: `74.125s` (`2.23x` faster than HOTFIX `165.2s`)
- **WARM Shared Ingest**: `0.032s` (`325x` faster than cold `10.406s`)
- **WARM Quick Preview (`540x960` + QA)**: `24.515s` (target `60-180s` **MET**)
- **Peak Process-Tree RSS**: `173.9 MiB` (`-42.1%` vs HOTFIX `300.4 MiB`)
- **Synthetic 3-Min (`180s / 4320f`) Streaming Scan**: `4.734s`, RSS `94.76 -> 96.09 MiB`

## Regression & Cross-Niche Summary

- **Discovered Tests**: `235` (`88 KH02` + `147 KH03`) across `33` modules (`225` baseline + `10` new R4.3 tests)
- **Serial**: `235/235 PASS` (`0 fail, 0 error, 0 skip`) in `296.719s`
- **Bounded Parallel (3 Shards)**: `235/235 PASS` (`0 fail, 0 error, 0 skip`) in `132.359s` (`2.242x` speedup, `exact_id_parity=true`, `exact_status_parity=true`)
- **Cross-Niche Corpus Honesty**: Real media verified on `personal_finance / HYBRID` (V7) and `saas_product_demo` (V6 non-regression); absent real-media groups explicitly reported `BLOCKED_INPUT`.
