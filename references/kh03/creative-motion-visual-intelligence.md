# KH-03.R3 Creative Motion & Visual Intelligence (`CMI-01..CMI-10`)

> **Applies to:** ALL FIVE universal editing skills (`SK-01..SK-05`) across arbitrary niches, aspect ratios (`9:16`, `16:9`, `1:1`, `4:5`), frame rates (`24`, `30`, `24000/1001`, `30000/1001`), and both short-form and long-form timelines.
> **Upstream License (`PROBLEM-01`):** Upstream `YeJe-cpu/SeeCut` remains **PolyForm Noncommercial License 1.0.0**. Commercial marketing requires written commercial permission from `aiesecxuanli@gmail.com`. Independent clean-room previews (Path B) are strictly separated from native SeeCut QA gates.

---

## Architecture Overview (`CMI-01` .. `CMI-10`)

KH-03.R3 upgrades `seecut-ai` from box-level beat overlays to a semantic, evidence-grounded, streaming-verified creative motion intelligence layer without adding any sixth skill or niche-specific hardcoding:

| Module | Capability | Core Files | Primary Artifacts |
|---|---|---|---|
| **CMI-01** | Semantic Hook Director (frame-0..3s microbeats, no-spoiler truth check, calm/absent-transcript fallbacks, rendered frame-0 pixel check) | `scripts/kh03/motion_graph.py`, `scripts/kh03/director.py`, `scripts/kh03/visual_qa.py` | `HOOK_CANDIDATES.json`, `HOOK_MICROBEATS.json`, `HOOK_TRUTH_LEDGER.json`, `SELECTED_HOOK.json`, `HOOK_PAYOFF_MAP.json`, `HOOK_PIXEL_QA.json` |
| **CMI-02** | Declarative Motion Scene Graph & Internal Node Timelines (word-anchored sub-nodes, exact `Decimal` numeric proofs, non-numeric narrative/process primitives, internal ROI pixel motion audit) | `scripts/kh03/motion_graph.py`, `scripts/kh03/components.py`, `scripts/kh02/compile_plan.py`, `schemas/kh03/motion_graph.schema.json` | `MOTION_GRAPH.json`, `COMPONENT_NODE_BINDINGS.json`, `SEMANTIC_MOTION_EVENTS.json`, `COMPONENT_STATE_TRACE.jsonl`, `COMPONENT_PIXEL_QA.json` |
| **CMI-03** | Shared-Element Morphing & Single-Pass EDL Remapping (claim/evidence identity handoffs, face/mouth-safe routing, 1–4f blank-gap rejection, `DOUBLE_REMAP_FORBIDDEN`) | `scripts/kh03/motion_graph.py`, `scripts/kh03/director.py`, `scripts/kh03/layout.py` | `SHARED_ELEMENT_GRAPH.json`, `MORPH_PLAN.json`, `MOTION_TRANSFORM_TRACE.csv`, `MORPH_SEMANTIC_COVERAGE.json`, `MORPH_SEAM_REPORT.json` |
| **CMI-04** | Subject, Object, Action & Scene-Cut Intelligence (multi-subject, faceless zero-face tracking, streaming scene-cut detection, honest `TRACKING_STATIC_FALLBACK` disclosure) | `scripts/kh03/tracking.py`, `schemas/kh03/tracking_observation.schema.json` | `SUBJECT_TRACKS.jsonl`, `OBJECT_TRACKS.jsonl`, `ACTION_TIMELINE.json`, `TRACKING_CONFIDENCE_REPORT.json`, `TRACKING_PROVENANCE.json`, `PROTECTED_REGIONS.jsonl` |
| **CMI-05** | Universal Cross-Niche Evidence & Proof Showcase Ledger (`ORIGINAL_SOURCE`, `ACTUAL_SCREENSHOT`, `ILLUSTRATIVE_DIAGRAM`, `THIRD_PARTY_LICENSED`, `MISSING_EVIDENCE`; fails closed on missing required proof or invalid rights) | `scripts/kh03/evidence_and_type.py`, `schemas/kh03/evidence_binding.schema.json` | `EVIDENCE_NEEDS.json`, `EVIDENCE_LEDGER.json`, `RIGHTS_LEDGER.csv`, `EVIDENCE_CROP_MAP.json`, `EVIDENCE_GAPS.md`, `PRODUCT_OR_PROOF_SHOWCASE_PLAN.json` |
| **CMI-06** | Dynamic Typography & Multilingual Captions (`360px` readability floor, WCAG contrast, EN/VI/CJK/RTL wrapping, negation/decimal atom protection, slot hysteresis, `caption_policy=OFF` respected) | `scripts/kh03/evidence_and_type.py`, `scripts/kh03/layout.py` | `TYPE_SYSTEM.json`, `TYPOGRAPHY_LAYOUT.json`, `TEXT_BOUNDS.jsonl`, `MOBILE_READABILITY_REPORT.json` |
| **CMI-07** | Multi-Level Visual Density & Duration-Weighted Anti-Repetition (catches "all-but-one top-banner" loops, visual appearance clones, unjustified static holds, clutter; preserves justified reading holds & `SK-05`/`FULLSCREEN_LOCK`) | `scripts/kh03/motion_graph.py`, `scripts/kh03/layout.py`, `scripts/kh03/qa.py` | `VISUAL_DENSITY_REPORT.json`, `COMPONENT_REPETITION_REPORT.json`, `VISUAL_HOLD_AUDIT.json`, `NEGATIVE_SPACE_MAP.json`, `ART_DIRECTION.md` |
| **CMI-08** | Motion-Bound Multi-Layer Sound Anchors (binds SFX cues to sub-event `motion_event_id` + `anchor_id`, exact `sample_onset`, `<= 1` output-frame onset QA, silent-stem & true-peak rejection) | `scripts/kh03/sound_director.py` | `SOUND_DIRECTOR_PLAN.json`, `MOTION_SOUND_ANCHORS.json`, `SFX_RIGHTS_LEDGER.csv`, `ONSET_QA.json`, `AUDIO_COMFORT_REPORT.md` |
| **CMI-09** | Streaming Decoded Pixel QA & Native Pinned GSAP Verification (1-frame black/freeze/crop-drift/occlusion detection, internal ROI motion, `FINAL_RENDER_SHA256.txt`, native pinned HyperFrames GSAP vs shim separation) | `scripts/kh03/visual_qa.py`, `scripts/kh03/qa.py`, `scripts/kh03/router.py` | `FRAME_QA.jsonl`, `STREAMING_QA_REPORT.json`, `PROCESS_CLEANUP_REPORT.json`, `NATIVE_GALLERY_VERIFICATION.json`, `FINAL_RENDER_SHA256.txt`, `FINAL_QA.json`, `OWNER_REVIEW.md` |
| **CMI-10** | Bounded Async DAG, True `O(1)` Streaming Decoder & Resource Safety (`StreamingRGBFrameDecoder`, `BoundedAsyncExecutor`, `AtomicKeyedCache`, host-calibrated RAM/disk guards, deterministic `workers=1/2/4` canonical hashes) | `scripts/kh03/execution.py`, `schemas/kh03/execution_budget.schema.json` | `RESOURCE_BUDGET.json`, `TASK_DAG.json`, `TASK_RECEIPTS.jsonl`, `EXECUTION_TRACE.jsonl`, `DISK_HIGH_WATER.json` |

---

## Verified Additive CLI Options (`python -m scripts.kh03`)

All legacy `kh03` and `kh02` invocations remain backward-compatible. Verified additive flags on `execute` and `verify`:

```bash
# Execute with explicit resource, tracking, and reduced-motion controls:
python -m scripts.kh03 execute \
  --run-root <isolated_run_dir> \
  --mode plan|preview|full \
  --workers 2 \
  --max-disk 4.0 \
  --max-memory-mib 4096 \
  --queue-frames 8 \
  [--enable-tracking] \
  [--reduced-motion] \
  [--sfx-library <licensed_dir>] \
  [--footage-library <licensed_dir>]

# Strict verification requiring final rendered MP4 + SHA match:
python -m scripts.kh03 verify --run-root <isolated_run_dir> [--require-render]
```

## Fail-Closed Guarantees
- **Zero Auto-Selection:** `recommend` ALWAYS halts in `AWAITING_USER_SELECTION`; no async job continues past selection without a cryptographically bound `SELECTION_RECEIPT.json`.
- **Zero Single-Pass Clock Drift:** `remap_motion_graph_to_edl` and `remap_layout_and_transitions_to_edl` remap exactly once via rational `Fraction` EDL coordinates and raise `DOUBLE_REMAP_FORBIDDEN` on any second attempt.
- **Zero Unbounded RGB Buffering:** `verify_fullscreen_frames()` and `verify_adaptive_layout_frames()` stream paired frames via `StreamingRGBFrameDecoder` (`subprocess.Popen` rawvideo + bounded stderr drain + platform-safe process-tree cleanup) and write JSONL/CSV incrementally.
- **Honest Provenance:** `OWNER_VISUAL=PENDING`, `OWNER_AUDIO=PENDING`, and `COMMERCIAL_RELEASE=BLOCKED` remain strictly separated from automated engineering passes.

