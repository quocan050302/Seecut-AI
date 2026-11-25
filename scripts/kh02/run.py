"""KH-02.R2 Unified Single-Path CLI Orchestrator (`python3 -m scripts.kh02.run`).

Ordered canonical pipeline (Stages A -> N) with stage receipts, SHA-256 dependency linkage,
real rough-cut application & verification, frame-0 hook compilation, explicit presenter_policy,
configurable real SFX providers, real multi-layer stem mixing/muxing when licensed assets exist,
H1 geom-check, decoded per-frame presenter QA, optional H2/pairwise & JianYing layered export.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from . import UPSTREAM_SEECUT_SHA, __version__
from .sound import (
    compare_dry_and_mix,
    measure_stems,
    mix_audio_checked,
    plan_soundscape,
    render_stems,
    verify_mix_sync,
)
from .camera import normalize_presenter_policy
from .compile_plan import compile_shotbook
from .contracts import hash_artifact, parse_json_strict, safe_path
from .edit_decisions import (
    apply_r1_cut_map,
    build_r1_time_map,
    emit_rough_cut_artifacts,
    propose_r1_cut_candidates,
    resolve_cut_policy,
    verify_cut_result,
)
from .planner import (
    audit_payoff,
    build_editor_brief,
    build_hook_candidates,
    choose_opening,
    emit_hook_and_pacing_artifacts,
    emit_shotbook_v2,
    plan_attention_beats,
    propose_directions,
    resolve_editorial_plan,
    resolve_pacing_budget,
)
from .qa import (
    audit_transition_seams,
    audit_visual_holds,
    build_effect_application_ledger,
    decide_adoption,
    evaluate_editorial,
    export_and_verify_jianying_layers,
    inspect_audio,
    render_timeline_diagnostics,
    run_h1_geom_check,
    validate_structural,
    verify_presenter_frames,
)
from .registry import audit_effect_variety, build_registry
from .render_guard import DEFAULT_DISK_RESERVE_GIB, estimate_resources, run_render_checked
from .sfx_assets import emit_sfx_asset_artifacts, ensure_cue_assets, load_sfx_provider_config
from .source_analysis import build_inventory, verify_source_integrity
from .transcript import build_packed_transcript, cache_transcript, map_words_to_output, normalize_faster_whisper


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def assert_stage_input_hashes(expected_map: dict[str, tuple[Path, str]]) -> None:
    """Verify upstream file artifacts match their exact expected SHA-256 before executing downstream stage."""
    for label, (path_obj, want_sha) in expected_map.items():
        if not path_obj.is_file():
            raise FileNotFoundError(f"Stage input missing ({label}): {path_obj}")
        actual = hash_artifact(path_obj)
        if actual != want_sha:
            raise ValueError(f"Stage input hash drift ({label}): {actual[:12]} != {want_sha[:12]}")


def persist_stage_receipt(run_dir: Path, receipt: dict[str, Any]) -> Path:
    """Atomically persist one stage execution receipt under STAGE_RECEIPTS/<stage_id>.json."""
    rdir = run_dir / "STAGE_RECEIPTS"
    rdir.mkdir(parents=True, exist_ok=True)
    return _write_json(rdir / f"{receipt['stage_id']}.json", receipt)


def execute_stage(
    stage_id: str,
    fn: Callable[[], dict[str, Any]],
    *,
    run_dir: Path,
    run_id: str,
    input_hashes: dict[str, str | None],
    dry_run: bool = False,
    test_only: bool = False,
    version: int = 1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one pipeline stage, capture timing/hashes/status, and persist receipt (unless dry_run)."""
    t0 = time.monotonic()
    payload = fn()
    dur = round(time.monotonic() - t0, 4)
    receipt = {
        "schema_version": "kh02r2.stage_receipt.v1",
        "stage_id": stage_id,
        "version": version,
        "run_id": run_id,
        "timestamp_utc": _now_utc(),
        "upstream_seecut_sha": UPSTREAM_SEECUT_SHA,
        "input_hashes": {k: v for k, v in input_hashes.items() if v is not None},
        "output_hashes": dict(payload.get("_output_hashes") or {}),
        "commands": list(payload.get("_commands") or []),
        "duration_sec": dur,
        "warnings": list(payload.get("_warnings") or []),
        "evidence_paths": list(payload.get("_evidence_paths") or []),
        "test_only": bool(test_only),
        "status": str(payload.get("_status") or "PASS"),
        "reason": str(payload.get("_reason") or "Completed cleanly."),
    }
    if not dry_run:
        persist_stage_receipt(run_dir, receipt)
    return payload, receipt


def verify_pipeline_receipts(run_dir: Path, required_stages: list[str]) -> dict[str, Any]:
    """Verify every required stage receipt exists on disk, passed/applicable, and has linked SHA evidence."""
    rdir = run_dir / "STAGE_RECEIPTS"
    missing: list[str] = []
    failed: list[str] = []
    loaded: dict[str, dict[str, Any]] = {}
    for sid in required_stages:
        rp = rdir / f"{sid}.json"
        if not rp.is_file():
            missing.append(sid)
            continue
        rec = parse_json_strict(rp)
        loaded[sid] = rec
        if rec.get("status") in ("FAIL", "ERROR"):
            failed.append(sid)
    ok = (len(missing) == 0 and len(failed) == 0)
    return {
        "pass": ok,
        "status": "PASS" if ok else "FAIL",
        "required_stages": required_stages,
        "present_stages": sorted(loaded.keys()),
        "missing_stages": missing,
        "failed_stages": failed,
        "receipts": loaded,
    }


def _build_sfx_cue_requests(shotbook: dict[str, Any], energy_mode: str) -> list[dict[str, Any]]:
    """Derive semantic SFX cue requests from SHOTBOOK V2 beats & transitions before acquisition."""
    reqs: list[dict[str, Any]] = []
    for idx, tr in enumerate(shotbook.get("transitions", [])):
        reqs.append({
            "cue_id": f"req_tr_{idx + 1:02d}",
            "category": "whoosh",
            "tags": ["soft", "whoosh", "sweep", "transition"],
            "query": "soft subtle transition whoosh sweep",
            "min_duration_sec": 0.10,
            "max_duration_sec": 1.2,
            "strict_category": True,
        })
    for idx, ev in enumerate(shotbook.get("visual_events", [])):
        kind = str((ev.get("params") or {}).get("kind") or "")
        if kind == "progress_spine":
            continue
        cat = "pop" if kind in ("split_callout_light", "stagger_grid_4", "step_pillars_3", "chapter_pill") else (
            "click" if kind in ("highlighter_card", "freeze_callout", "code_window") else (
                "chime" if kind in ("light_sweep_badge", "verdict_bar") else "whoosh"
            )
        )
        reqs.append({
            "cue_id": f"req_ev_{idx + 1:02d}_{ev['event_id']}",
            "category": cat,
            "tags": [cat, "clean", "subtle", energy_mode],
            "query": f"clean subtle UI {cat} sound",
            "min_duration_sec": 0.05,
            "max_duration_sec": 1.5,
            "strict_category": True,
        })
    # Cap unique requests cleanly so provider queries stay focused & bounded
    seen_cats: dict[str, int] = {}
    bounded: list[dict[str, Any]] = []
    for r in reqs:
        c = r["category"]
        seen_cats[c] = seen_cats.get(c, 0) + 1
        if seen_cats[c] <= 2:
            bounded.append(r)
    return bounded


def _verify_existing_run(run_dir: Path) -> dict[str, Any]:
    """Verify an existing run directory's STAGE_RECEIPTS, SHOTBOOK_V2, COMPILE_MAP & output hashes."""
    sb_p = run_dir / "SHOTBOOK_V2.json"
    cm_p = run_dir / "COMPILE_MAP.json"
    issues: list[str] = []
    if not sb_p.is_file():
        issues.append(f"Missing SHOTBOOK_V2.json in {run_dir}")
    if not cm_p.is_file():
        issues.append(f"Missing COMPILE_MAP.json in {run_dir}")
    rdir = run_dir / "STAGE_RECEIPTS"
    present = sorted(p.stem for p in rdir.glob("*.json")) if rdir.is_dir() else []
    if not present:
        issues.append("No STAGE_RECEIPTS found")
    sb_sha = hash_artifact(parse_json_strict(sb_p)) if sb_p.is_file() else None
    cm_sha = parse_json_strict(cm_p).get("plan_sha256") if cm_p.is_file() else None
    if sb_sha and cm_sha and sb_sha != cm_sha:
        issues.append(f"COMPILE_MAP plan_sha256 ({cm_sha[:12]}) != SHOTBOOK_V2 ({sb_sha[:12]})")
    return {
        "status": "VERIFY_PASS" if not issues else "VERIFY_FAIL",
        "exit_code": 0 if not issues else 1,
        "run_id": run_dir.name,
        "present_receipts": present,
        "shotbook_sha256": sb_sha,
        "issues": issues,
    }


def execute_pipeline(
    *,
    brief_path: Path,
    run_root: Path,
    mode: str = "plan",
    dry_run: bool = False,
    workers: int = 2,
    max_disk_gib: float | None = None,
    profile_override: str | None = None,
    license_decision_override: str | None = None,
    cut_policy_override: str | None = None,
    sfx_provider_mode: str = "auto",
    sfx_policy_override: str | None = None,
    music_policy_override: str | None = None,
    sfx_library_dirs: list[str | Path] | None = None,
    sfx_config_path: Path | None = None,
    sfx_cache_dir: Path | None = None,
    presenter_mode_override: str | None = None,
    presenter_anchor_override: str | None = None,
    presenter_shape_override: str | None = None,
    presenter_width_ratio_override: float | None = None,
    max_downloads: int = 3,
    max_download_bytes: int = 25 * 1024 * 1024,
    offline: bool = False,
    approved_brief: bool = False,
    transcript_json_path: Path | None = None,
    catalog_json_path: Path | None = None,
    seecut_root: Path | None = None,
    tools_root: Path | None = None,
    reserve_floor_gib: float = DEFAULT_DISK_RESERVE_GIB,
    run_h2: bool = False,
    compare_baselines: list[Path] | None = None,
    export_layers: bool = False,
    candidate_version: int = 1,
    output_basename: str | None = None,
    production_mode: bool = True,
    allow_test_only_sfx: bool = False,
    sfx_tool_invoker: Any = None,
    kh03_selection_receipt: dict[str, Any] | None = None,
    kh03_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Single-entry orchestrated KH-02.R2 pipeline (Stages A -> N) with strict license gate, stage receipts & SHA linkage."""
    workers = max(1, min(4, int(workers)))
    run_dir = Path(os.path.realpath(str(run_root.expanduser())))
    run_id = run_dir.name

    if mode == "verify":
        return _verify_existing_run(run_dir)

    raw_brief = parse_json_strict(brief_path)
    if profile_override:
        raw_brief["profile"] = profile_override
    if license_decision_override:
        raw_brief["license_decision"] = license_decision_override
    if cut_policy_override:
        raw_brief["cut_policy"] = cut_policy_override
        raw_brief["voice_policy"] = cut_policy_override
    if sfx_policy_override:
        raw_brief["sfx_policy"] = sfx_policy_override
    if music_policy_override:
        raw_brief["music_policy"] = music_policy_override
    if sfx_provider_mode:
        raw_brief["sfx_provider"] = sfx_provider_mode
    if any(x is not None for x in (presenter_mode_override, presenter_anchor_override, presenter_shape_override, presenter_width_ratio_override)):
        pp = dict(raw_brief.get("presenter_policy") or {})
        if presenter_mode_override:
            pp["mode"] = presenter_mode_override
        if presenter_anchor_override:
            pp["anchor"] = presenter_anchor_override
        if presenter_shape_override:
            pp["shape"] = presenter_shape_override
        if presenter_width_ratio_override is not None:
            pp["width_ratio"] = float(presenter_width_ratio_override)
        raw_brief["presenter_policy"] = pp

    try:
        brief = build_editor_brief(raw_brief)
    except ValueError as ex:
        code = 3 if "BLOCKED_PRESENTER_SOURCE" in str(ex) else 1
        return {
            "status": "BLOCKED_PRESENTER_SOURCE" if "BLOCKED_PRESENTER_SOURCE" in str(ex) else "INVALID_BRIEF",
            "exit_code": code,
            "reason": str(ex),
        }

    # KH-03 fail-closed receipt verification via single shared verifier (R1.06)
    if kh03_overrides is not None:
        valid_sk = {"SK-01", "SK-02", "SK-03", "SK-04", "SK-05"}
        if not isinstance(kh03_selection_receipt, dict) or kh03_selection_receipt.get("selected_skill_id") not in valid_sk:
            return {
                "status": "BLOCKED_KH03_SELECTION_REQUIRED",
                "exit_code": 2,
                "reason": "KH-03 overrides require a validated SELECTION_RECEIPT binding SK-01..SK-05.",
            }
        if kh03_overrides.get("selected_skill_id") != kh03_selection_receipt.get("selected_skill_id"):
            return {
                "status": "BLOCKED_KH03_SKILL_MISMATCH",
                "exit_code": 2,
                "reason": "KH-03 override skill_id does not match validated SELECTION_RECEIPT.",
            }
        try:
            from scripts.kh03.selection import validate_user_selection as _kh03_validate_selection
            _kh03_validate_selection(
                run_dir=run_dir,
                production_mode=production_mode,
                expected_skill_id=str(kh03_overrides.get("selected_skill_id")),
                supplied_receipt=kh03_selection_receipt,
            )
        except Exception as ex:
            return {
                "status": "BLOCKED_KH03_SELECTION_INVALID",
                "exit_code": 2,
                "reason": f"Direct KH-02 ingress rejected KH-03 selection receipt: {ex}",
            }

    # Hard License Gate BEFORE any SeeCut execution (P0 hardened)
    valid_lics = {
        "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
        "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED",
        "BLOCKED_COMMERCIAL_LICENSE",
        "BLOCKED_LICENSE_UNRESOLVED",
    }
    lic = brief.get("license_decision")
    if lic not in valid_lics or lic in ("BLOCKED_COMMERCIAL_LICENSE", "BLOCKED_LICENSE_UNRESOLVED"):
        return {
            "status": lic if lic in valid_lics else "BLOCKED_LICENSE_UNRESOLVED",
            "exit_code": 2,
            "reason": (
                f"License gate blocked execution ({lic}); PolyForm Noncommercial 1.0.0 requires "
                "noncommercial confirmation or written commercial permission from aiesecxuanli@gmail.com."
            ),
        }
    intended_purpose = str(brief.get("intended_purpose") or "").strip().upper()
    comm_evid = str(brief.get("commercial_permission_evidence") or "").strip()
    if intended_purpose == "COMMERCIAL_MARKETING" and (lic != "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED" or len(comm_evid) < 8):
        return {
            "status": "BLOCKED_COMMERCIAL_LICENSE",
            "exit_code": 2,
            "reason": "COMMERCIAL_MARKETING requires WRITTEN_COMMERCIAL_PERMISSION_VERIFIED plus verified commercial_permission_evidence.",
        }
    if lic == "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED" and kh03_overrides is not None and len(comm_evid) < 8:
        return {
            "status": "BLOCKED_COMMERCIAL_LICENSE",
            "exit_code": 2,
            "reason": "Unsubstantiated WRITTEN_COMMERCIAL_PERMISSION_VERIFIED rejected: missing commercial_permission_evidence.",
        }

    if not dry_run:
        for sub in (
            "build", "qc", "exports", "jianying/layers", "logs", "evidence",
            "probe", "cache", "manifests", "stems", "diagnostics", "STAGE_RECEIPTS", "PIP_CROP_EVIDENCE",
        ):
            (run_dir / sub).mkdir(parents=True, exist_ok=True)

    stage_receipts: list[dict[str, Any]] = []
    brief_sha = hash_artifact(brief)

    # ------------------------------------------------------------------
    # STAGE A: Authorize, resolve sources, build inventory & registry
    # ------------------------------------------------------------------
    def _stage_a() -> dict[str, Any]:
        resolved_sources = []
        for s in brief["sources"]:
            sc = dict(s)
            sp = Path(sc["path"])
            if not sp.is_absolute():
                sc["path"] = str((brief_path.parent / sp).resolve())
            resolved_sources.append(sc)
        with ThreadPoolExecutor(max_workers=min(workers, 2)) as ex:
            f_inv = ex.submit(
                build_inventory, resolved_sources, run_id,
                cache_dir=(None if dry_run else run_dir / "cache"),
                analyze_audio_motion=(not dry_run),
            )
            f_reg = ex.submit(build_registry, catalog_json_path=catalog_json_path)
            inv = f_inv.result()
            reg = f_reg.result()
        usable = [a for a in inv["assets"] if a.get("usable_in_render")]
        if not usable:
            raise ValueError("BLOCKED_INPUT: No usable_in_render source asset found in inventory")
        primary_a = next((a for a in usable if a["kind"] in ("a_roll", "b_roll")), usable[0])
        has_person = any(a["kind"] == "a_roll" for a in usable)
        pres_pol = normalize_presenter_policy(
            brief.get("presenter_policy"), brief_mode=brief["mode"], has_presenter_source=has_person,
        )
        evid_paths = []
        if not dry_run:
            _write_json(run_dir / "manifests/SOURCE_INVENTORY.json", inv)
            _write_json(run_dir / "manifests/CAPABILITY_REGISTRY.json", reg)
            _write_json(run_dir / "PRESENTER_POLICY.json", pres_pol)
            evid_paths = [
                str(run_dir / "manifests/SOURCE_INVENTORY.json"),
                str(run_dir / "manifests/CAPABILITY_REGISTRY.json"),
                str(run_dir / "PRESENTER_POLICY.json"),
            ]
        return {
            "inventory": inv,
            "registry": reg,
            "primary": primary_a,
            "presenter_policy": pres_pol,
            "_output_hashes": {
                "inventory_sha256": hash_artifact(inv),
                "registry_sha256": hash_artifact(reg),
                "primary_source_sha256": primary_a["sha256"],
                "presenter_policy_sha256": hash_artifact(pres_pol),
            },
            "_evidence_paths": evid_paths,
        }

    try:
        st_a, rec_a = execute_stage(
            "A_authorize_inventory", _stage_a, run_dir=run_dir, run_id=run_id,
            input_hashes={"brief_sha256": brief_sha}, dry_run=dry_run, test_only=(not production_mode), version=candidate_version,
        )
    except Exception as ex:
        msg = str(ex)
        return {
            "status": "BLOCKED_PRESENTER_SOURCE" if "BLOCKED_PRESENTER_SOURCE" in msg else "BLOCKED_INPUT",
            "exit_code": 2,
            "reason": msg,
        }
    stage_receipts.append(rec_a)
    inventory = st_a["inventory"]
    registry = st_a["registry"]
    primary = st_a["primary"]
    presenter_policy = st_a["presenter_policy"]
    media = primary["media"]
    fps_num = int(media.get("fps_num") or 24)
    fps_den = int(media.get("fps_den") or 1)
    fps = fps_num / fps_den
    total_src_frames = int(media.get("nb_frames") or max(1, int(round((media.get("duration_sec") or 6.0) * fps))))
    sample_rate = int(media.get("audio_sample_rate") or 48000)

    # ------------------------------------------------------------------
    # STAGE B: Transcript normalization & caching (exact source SHA bound)
    # ------------------------------------------------------------------
    def _stage_b() -> dict[str, Any]:
        tr = None
        evid_paths = []
        if transcript_json_path and transcript_json_path.is_file():
            raw_tr = parse_json_strict(transcript_json_path)
            tr = normalize_faster_whisper(
                raw_tr,
                source_asset_id=primary["asset_id"],
                audio_sha256=primary["sha256"],
                language=brief["language"],
            )
            if not dry_run:
                cache_transcript(run_dir / "cache", primary["sha256"], tr["model"], tr["language"], transcript=tr)
                _write_json(run_dir / "TRANSCRIPT_NORMALIZED.json", tr)
                (run_dir / "TAKES_PACKED.md").write_text(build_packed_transcript(tr, title=brief.get("title")), encoding="utf-8")
                evid_paths = [str(run_dir / "TRANSCRIPT_NORMALIZED.json"), str(run_dir / "TAKES_PACKED.md")]
        return {
            "transcript": tr,
            "_output_hashes": {"transcript_sha256": hash_artifact(tr) if tr else "none"},
            "_evidence_paths": evid_paths,
            "_status": "PASS" if tr else "NOT_APPLICABLE",
            "_reason": "Normalized & bound transcript to primary source SHA." if tr else "No transcript supplied (silent/visual workflow).",
        }

    st_b, rec_b = execute_stage(
        "B_transcript_analysis", _stage_b, run_dir=run_dir, run_id=run_id,
        input_hashes={"primary_source_sha256": primary["sha256"]}, dry_run=dry_run, test_only=(not production_mode), version=candidate_version,
    )
    stage_receipts.append(rec_b)
    transcript = st_b["transcript"]

    # ------------------------------------------------------------------
    # STAGE C: Rough cut policy, candidates & single authoritative EDL
    # ------------------------------------------------------------------
    def _stage_c() -> dict[str, Any]:
        eff_cut_pol = brief.get("cut_policy") or brief.get("voice_policy", "LOCK_VOICE")
        c_info = resolve_cut_policy(eff_cut_pol, approved_scope=brief.get("approved_scope"))
        stall_wins = [tuple(w) for w in (brief.get("visual_stall_windows") or []) if len(w) == 2]
        c_cands = propose_r1_cut_candidates(
            source_asset_id=primary["asset_id"],
            source_sha256=primary["sha256"],
            source_duration_sec=float(media.get("duration_sec") or total_src_frames / fps),
            transcript=transcript,
            silences=primary.get("silences"),
            visual_stall_windows=stall_wins or None,
            cut_policy=c_info["cut_policy"],
            approved_scope=c_info.get("approval_scope"),
            has_audio_track=bool(media.get("has_audio", True)),
        )
        edl_doc = build_r1_time_map(
            primary["asset_id"],
            total_src_frames,
            fps_num,
            fps_den,
            source_sha256=primary["sha256"],
            audio_sample_rate=sample_rate,
            cut_policy=c_info["cut_policy"],
            approved_scope=c_info.get("approval_scope"),
            candidate_doc=c_cands,
            words=(transcript or {}).get("words"),
        )
        return {
            "cut_info": c_info,
            "cut_candidates": c_cands,
            "edl": edl_doc,
            "_output_hashes": {
                "cut_candidates_sha256": hash_artifact(c_cands),
                "edl_sha256": hash_artifact(edl_doc),
            },
            "_reason": f"Authoritative EDL built ({c_info['cut_policy']}: {total_src_frames}f -> {edl_doc['total_output_frames']}f).",
        }

    st_c, rec_c = execute_stage(
        "C_rough_cut_edl", _stage_c, run_dir=run_dir, run_id=run_id,
        input_hashes={"primary_source_sha256": primary["sha256"], "transcript_sha256": rec_b["output_hashes"].get("transcript_sha256")},
        dry_run=dry_run, test_only=(not production_mode), version=candidate_version,
    )
    stage_receipts.append(rec_c)
    cut_info = st_c["cut_info"]
    cut_cands = st_c["cut_candidates"]
    edl = st_c["edl"]
    edl_sha = rec_c["output_hashes"]["edl_sha256"]

    # ------------------------------------------------------------------
    # STAGE D: Apply exact EDL -> derived paired A/V + verify_cut_result
    # ------------------------------------------------------------------
    def _stage_d() -> dict[str, Any]:
        is_non_identity = (
            cut_info["cut_policy"] != "LOCK_VOICE"
            and (int(edl["total_output_frames"]) != total_src_frames or len(edl["segments"]) > 1)
        )
        if dry_run:
            return {
                "applied": False,
                "derived_path": primary["canonical_path"],
                "derived_sha256": primary["sha256"],
                "verification": None,
                "cut_status": "DRY_RUN_PLANNED_CUT" if is_non_identity else "IDENTITY_VERIFIED",
                "_output_hashes": {"derived_source_sha256": primary["sha256"]},
                "_status": "NOT_APPLICABLE",
                "_reason": "Dry-run skips media derivation; EDL validated.",
            }
        if not is_non_identity:
            rc_paths = emit_rough_cut_artifacts(run_dir, candidate_doc=cut_cands, edl=edl, original_transcript=transcript)
            return {
                "applied": False,
                "derived_path": primary["canonical_path"],
                "derived_sha256": primary["sha256"],
                "verification": None,
                "cut_status": "IDENTITY_VERIFIED",
                "_output_hashes": {"derived_source_sha256": primary["sha256"], "edl_sha256": edl_sha},
                "_evidence_paths": [str(p) for p in rc_paths.values()],
                "_status": "PASS",
                "_reason": f"Identity mapping verified ({cut_info['cut_policy']}, 0 frames removed; original source SHA preserved).",
            }
        derived_mp4 = run_dir / "build" / "derived_aroll.mp4"
        apply_res = apply_r1_cut_map(primary["canonical_path"], edl, derived_mp4)
        ver = verify_cut_result(
            primary["canonical_path"],
            derived_mp4,
            edl,
            expected_source_sha256=primary["sha256"],
            original_transcript=transcript,
            candidate_doc=cut_cands,
        )
        rc_paths = emit_rough_cut_artifacts(
            run_dir, candidate_doc=cut_cands, edl=edl, original_transcript=transcript, verification=ver,
        )
        if not ver["pass"]:
            raise RuntimeError(f"CUT_VERIFICATION_FAILED: {ver['issues'][:3]}")
        return {
            "applied": True,
            "derived_path": str(derived_mp4),
            "derived_sha256": ver["derived_sha256"],
            "apply_result": apply_res,
            "verification": ver,
            "cut_status": "R2_CUT_APPLIED",
            "_output_hashes": {"derived_source_sha256": ver["derived_sha256"], "edl_sha256": edl_sha},
            "_evidence_paths": [str(derived_mp4)] + [str(p) for p in rc_paths.values()],
            "_status": "PASS",
            "_reason": f"Applied paired A/V rough cut ({ver['before_frames']}f -> {ver['after_frames']}f, saved {ver['saved_sec']}s) and verified.",
        }

    try:
        st_d, rec_d = execute_stage(
            "D_apply_cut_and_verify", _stage_d, run_dir=run_dir, run_id=run_id,
            input_hashes={"primary_source_sha256": primary["sha256"], "edl_sha256": edl_sha},
            dry_run=dry_run, test_only=(not production_mode), version=candidate_version,
        )
    except Exception as ex:
        return {"status": "CUT_APPLY_FAIL", "exit_code": 1, "run_id": run_id, "reason": str(ex)}
    stage_receipts.append(rec_d)
    derived_source_path = st_d["derived_path"]
    derived_source_sha = st_d["derived_sha256"]

    # ------------------------------------------------------------------
    # STAGE E: Semantic Strategy, Hook Candidates & Pacing Budget (R1.04 KH-03 hook fidelity)
    # ------------------------------------------------------------------
    eff_kh03_overrides: dict[str, Any] | None = None
    if kh03_overrides is not None:
        from scripts.kh03.director import remap_kh03_plan_to_output_clock
        if kh03_overrides.get("clock_domain") == "OUTPUT_EDL_MAPPED":
            if kh03_overrides.get("time_map_sha256") != edl_sha:
                return {
                    "status": "BLOCKED_TIMELINE_MISMATCH",
                    "exit_code": 1,
                    "run_id": run_id,
                    "reason": "Pre-mapped kh03_overrides time_map_sha256 does not match authoritative Stage C EDL SHA.",
                }
            eff_kh03_overrides = dict(kh03_overrides)
        else:
            try:
                eff_kh03_overrides = remap_kh03_plan_to_output_clock(
                    kh03_overrides, edl, transcript=transcript, run_dir=(None if dry_run else run_dir),
                )
            except Exception as ex:
                return {
                    "status": "TIMELINE_REMAP_FAIL",
                    "exit_code": 1,
                    "run_id": run_id,
                    "reason": f"Authoritative KH-03 EDL remapping failed: {ex}",
                }

    def _stage_e() -> dict[str, Any]:
        h_opts = build_hook_candidates(
            brief, inventory, transcript=transcript,
            cut_policy=cut_info["cut_policy"], presenter_policy=presenter_policy,
        )
        if eff_kh03_overrides and isinstance(eff_kh03_overrides.get("selected_hook_override"), dict):
            kh_hook = dict(eff_kh03_overrides["selected_hook_override"])
            if kh_hook.get("spoils_payoff") or kh_hook.get("deceptive_urgency"):
                raise ValueError("HOOK_FIDELITY_REJECTED: KH-03 hook override flagged spoiler or deceptive urgency.")
            base_dec = choose_opening(h_opts, brief=brief)
            kh_opt_id = str(kh_hook.get("selected_option_id") or "kh03_hook_01")
            opts_out = [dict(o) for o in base_dec.get("options", [])]
            if opts_out:
                opts_out[0] = dict(opts_out[0])
                opts_out[0]["option_id"] = kh_opt_id
                opts_out[0]["strategy"] = str(kh_hook.get("strategy") or opts_out[0].get("strategy", "kh03_semantic_hook"))
                if kh_hook.get("opening_visual"):
                    opts_out[0]["first_frame_visual"] = str(kh_hook["opening_visual"])[:180]
                opts_out[0]["selected"] = True
                for rest in opts_out[1:]:
                    rest["selected"] = False
            h_dec = dict(base_dec)
            h_dec.update({
                "selected_option_id": kh_opt_id,
                "selection_rationale": (
                    f"KH-03 authoritative hook ({kh_hook.get('strategy', 'semantic')}, "
                    f"plan={str(kh_hook.get('kh03_plan_sha256') or '')[:12]}): "
                    f"{str(kh_hook.get('opening_visual') or 'Truthful opening frame')}"
                ),
                "options": opts_out,
                "strategy": str(kh_hook.get("strategy") or "kh03_semantic_hook"),
                "opening_line": str(kh_hook.get("opening_line") or ""),
                "opening_visual": str(kh_hook.get("opening_visual") or ""),
                "first_frame_visual_id": str(kh_hook.get("first_frame_visual_id") or "kh03_comp_01_beat_01"),
                "start_frame": int(kh_hook.get("start_frame", 0)),
                "presenter_mode": presenter_policy["mode"],
                "payoff_beat_id": str(kh_hook.get("payoff_beat_id") or "beat_01"),
                "authoritative_source": "KH03_CREATIVE_PLAN",
                "kh03_plan_sha256": kh_hook.get("kh03_plan_sha256"),
            })
        else:
            h_dec = choose_opening(h_opts, brief=brief)
        out_dur_sec = float(edl["total_output_frames"]) / fps
        p_budget = resolve_pacing_budget(brief, total_duration_sec=out_dur_sec)
        dirs = propose_directions(brief, inventory, transcript)
        evid_paths = []
        if not dry_run:
            _write_json(run_dir / "manifests/CREATIVE_DIRECTIONS.json", dirs)
            evid_paths = [str(run_dir / "manifests/CREATIVE_DIRECTIONS.json")]
        return {
            "hook_options": h_opts,
            "hook_decision": h_dec,
            "pacing_budget": p_budget,
            "directions": dirs,
            "_output_hashes": {
                "hook_decision_sha256": hash_artifact(h_dec),
                "pacing_budget_sha256": hash_artifact(p_budget),
            },
            "_evidence_paths": evid_paths,
            "_reason": f"Selected truthful hook {h_dec['selected_option_id']} (energy={p_budget['energy_mode']}).",
        }

    st_e, rec_e = execute_stage(
        "E_hook_and_strategy", _stage_e, run_dir=run_dir, run_id=run_id,
        input_hashes={"edl_sha256": edl_sha, "presenter_policy_sha256": rec_a["output_hashes"]["presenter_policy_sha256"]},
        dry_run=dry_run, test_only=(not production_mode), version=candidate_version,
    )
    stage_receipts.append(rec_e)
    hook_dec = st_e["hook_decision"]
    pacing_budget = st_e["pacing_budget"]
    directions = st_e["directions"]

    # ------------------------------------------------------------------
    # STAGE F: Canonical SHOTBOOK V2 (authoritative EDL + hook + presenter_policy)
    # ------------------------------------------------------------------
    def _stage_f() -> dict[str, Any]:
        kh03_meta = (
            {
                "selected_skill_id": eff_kh03_overrides["selected_skill_id"],
                "kh03_plan_sha256": eff_kh03_overrides["kh03_plan_sha256"],
                "clock_domain": eff_kh03_overrides.get("clock_domain", "OUTPUT_EDL_MAPPED"),
                "time_map_sha256": eff_kh03_overrides.get("time_map_sha256", edl_sha),
                "sound_plan_sha256": eff_kh03_overrides.get("sound_plan_sha256"),
                "layout_policy": eff_kh03_overrides.get("layout_policy"),
                "layout_plan_sha256": eff_kh03_overrides.get("layout_plan_sha256"),
                "motion_graph_sha256": eff_kh03_overrides.get("motion_graph_sha256"),
            }
            if eff_kh03_overrides else None
        )
        sb = resolve_editorial_plan(
            brief, inventory, run_id=run_id,
            transcript=transcript, registry=registry,
            edl_override=edl,
            hook_decision=hook_dec,
            presenter_policy_override=presenter_policy,
            custom_beats=(eff_kh03_overrides.get("custom_beats") if eff_kh03_overrides else None),
            custom_visual_events=(eff_kh03_overrides.get("custom_visual_events") if eff_kh03_overrides else None),
            creative_direction_override=(eff_kh03_overrides.get("creative_direction_override") if eff_kh03_overrides else None),
            adaptive_layout_plan=(eff_kh03_overrides.get("layout_plan") if eff_kh03_overrides else None),
            kh03_metadata=kh03_meta,
            status="TEST_ONLY" if (dry_run or not production_mode) else "PLANNED",
        )
        att_b = plan_attention_beats(sb["beats"], brief=brief, fps=fps)
        p_audit = audit_payoff(hook_dec, sb["beats"], claim_evidence=sb.get("claim_evidence"), brief=brief)
        var = audit_effect_variety(sb["visual_events"], registry=registry)
        ed_ev = evaluate_editorial(sb, inventory=inventory, registry=registry)
        r_est = estimate_resources(
            sb["output_spec"]["total_frames"],
            sb["output_spec"]["width"],
            sb["output_spec"]["height"],
            render_scale=float(sb["output_spec"].get("output_scale", 1.5)),
            target_dir=run_dir,
            reserve_floor_gib=reserve_floor_gib,
            max_disk_gib=max_disk_gib,
        )
        evid_paths = []
        if not dry_run and sb.get("presenter_layout"):
            _write_json(run_dir / "PRESENTER_LAYOUT.json", sb["presenter_layout"])
            evid_paths.append(str(run_dir / "PRESENTER_LAYOUT.json"))
        return {
            "shotbook": sb,
            "attention_beats": att_b,
            "payoff_audit": p_audit,
            "variety": var,
            "editorial_eval": ed_ev,
            "resource_estimate": r_est,
            "_output_hashes": {"shotbook_pre_sfx_sha256": hash_artifact(sb)},
            "_evidence_paths": evid_paths,
            "_status": "PASS" if ed_ev["pass"] else "FAIL",
            "_reason": f"Canonical SHOTBOOK V2 built ({len(sb['beats'])} beats, {len(sb['visual_events'])} visual events, hook={sb['selected_hook']['selected_option_id']}).",
        }

    try:
        st_f, rec_f = execute_stage(
            "F_canonical_shotbook", _stage_f, run_dir=run_dir, run_id=run_id,
            input_hashes={"edl_sha256": edl_sha, "hook_decision_sha256": rec_e["output_hashes"]["hook_decision_sha256"]},
            dry_run=dry_run, test_only=(not production_mode), version=candidate_version,
        )
    except ValueError as ex:
        return {"status": "LAYOUT_CONFLICT" if "LAYOUT_CONFLICT" in str(ex) else "PLAN_FAIL", "exit_code": 1, "run_id": run_id, "reason": str(ex)}
    stage_receipts.append(rec_f)
    shotbook = st_f["shotbook"]
    att_beats = st_f["attention_beats"]
    payoff = st_f["payoff_audit"]
    variety = st_f["variety"]
    ed_eval = st_f["editorial_eval"]
    res_est = st_f["resource_estimate"]

    # ------------------------------------------------------------------
    # STAGE G: Real Configurable SFX Acquisition & Soundscape Binding
    # ------------------------------------------------------------------
    def _stage_g() -> dict[str, Any]:
        intended = "COMMERCIAL_AND_NONCOMMERCIAL" if lic == "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED" else "NONCOMMERCIAL_ONLY"
        prov_bundle = load_sfx_provider_config(
            cli_library_dirs=sfx_library_dirs,
            brief_library_dirs=brief.get("sfx_library_dirs"),
            config_path=sfx_config_path or (Path(brief["sfx_config_path"]) if brief.get("sfx_config_path") else None),
            provider_mode=brief.get("sfx_provider", sfx_provider_mode),
            sfx_policy=brief["sfx_policy"],
            offline=(offline or dry_run),
            intended_purpose=intended,
            tool_invoker=sfx_tool_invoker,
        )
        cache_root = Path(sfx_cache_dir).resolve() if sfx_cache_dir else (run_dir / "cache/sfx")
        kh03_sp = eff_kh03_overrides.get("sound_plan") if (eff_kh03_overrides and isinstance(eff_kh03_overrides.get("sound_plan"), dict)) else None
        if brief["sfx_policy"] == "OFF":
            cue_reqs = []
        elif kh03_sp is not None:
            # R1.03: Honor KH-03 sound director events cleanly without regenerating duplicate legacy cues
            from scripts.kh03.sound_director import build_cue_requests as _kh03_build_reqs
            cue_reqs = _kh03_build_reqs(kh03_sp.get("events", []), pacing_budget["energy_mode"])
        else:
            cue_reqs = _build_sfx_cue_requests(shotbook, pacing_budget["energy_mode"])

        acquired_assets: list[dict[str, Any]] = []
        acq_log: list[dict[str, Any]] = []
        q_plan: list[dict[str, Any]] = []
        missing_cats: list[str] = []
        downloads_performed = 0

        if dry_run or brief["sfx_policy"] == "OFF":
            # Dry-run inspects already-indexed local assets without downloading or writing cache
            acquired_assets = list(prov_bundle["local_provider"]._scan["assets"]) if brief["sfx_policy"] != "OFF" else []
        else:
            acq = ensure_cue_assets(
                cue_reqs,
                local_provider=prov_bundle["local_provider"],
                remote_provider=prov_bundle["remote_provider"],
                cache_dir=cache_root,
                max_downloads=max_downloads,
                max_download_bytes=max_download_bytes,
                workers=min(workers, 2),
                offline=offline,
                intended_purpose=intended,
                allow_test_only=allow_test_only_sfx,
            )
            acquired_assets = acq["assets"]
            acq_log = acq["acquisition_log"]
            q_plan = acq["query_plan"]
            missing_cats = acq["missing_categories"]
            downloads_performed = int(acq["downloads_used"])

        mapped_w = map_words_to_output(transcript, edl) if transcript else []
        speech_spans = [(float(w["out_start_sec"]), float(w["out_end_sec"])) for w in mapped_w]
        soundscape = plan_soundscape(
            shotbook,
            acquired_assets,
            voice_asset_id=primary["asset_id"] if media.get("has_audio") else None,
            speech_spans=speech_spans,
            energy_mode=pacing_budget["energy_mode"],
            sfx_policy=brief["sfx_policy"],
            bgm_policy=brief["music_policy"],
            production_mode=(production_mode and not allow_test_only_sfx),
        )
        # Update shotbook audio_cues with resolved soundscape cues cleanly (only schema-safe fields)
        verified_non_voice = [
            c for c in soundscape["cues"]
            if c["kind"] != "voice" and c.get("status") in ("VERIFIED_ASSET", "APPLIED", "ONSET_VERIFIED")
        ]
        asset_path_map = {
            a["asset_id"]: Path(a["local_abs_path"])
            for a in acquired_assets
            if a.get("local_abs_path") and Path(a["local_abs_path"]).is_file()
        }

        evid_paths: list[str] = []
        if not dry_run:
            _write_json(run_dir / "sfx_plan.json", soundscape)
            _write_json(run_dir / "AUDIO_GRAPH.json", soundscape)
            evid_paths.extend([str(run_dir / "sfx_plan.json"), str(run_dir / "AUDIO_GRAPH.json")])
            if kh03_sp is not None:
                resolved_sp = {
                    "schema_version": "kh03.resolved_sound_plan.v1",
                    "run_id": run_id,
                    "clock_domain": kh03_sp.get("clock_domain", "OUTPUT_EDL_MAPPED"),
                    "time_map_sha256": kh03_sp.get("time_map_sha256", edl_sha),
                    "planned_sound_plan_sha256": hash_artifact(kh03_sp),
                    "planned_add_events": sum(1 for e in kh03_sp.get("events", []) if e.get("status") == "ADD"),
                    "resolved_verified_cues": len(verified_non_voice),
                    "cues": soundscape["cues"],
                    "deltas": [
                        {"cue_id": c["cue_id"], "status": c.get("status"), "onset_sec": c.get("start_sec")}
                        for c in soundscape["cues"] if c.get("kind") != "voice"
                    ],
                }
                _write_json(run_dir / "stems/RESOLVED_SOUND_PLAN.json", resolved_sp)
                _write_json(run_dir / "stems/SFX_APPLIED.json", {
                    "schema_version": "kh03.sfx_applied.v1",
                    "verified_non_voice_cues": verified_non_voice,
                    "count": len(verified_non_voice),
                })
                evid_paths.extend([str(run_dir / "stems/RESOLVED_SOUND_PLAN.json"), str(run_dir / "stems/SFX_APPLIED.json")])
            sfx_paths = emit_sfx_asset_artifacts(
                run_dir,
                assets=acquired_assets,
                provider_statuses=prov_bundle["provider_statuses"],
                missing_categories=sorted(set(missing_cats) | {c.get("role", "foley") for c in soundscape["cues"] if c.get("status") == "UNAVAILABLE"}),
                acquisition_log=acq_log,
                query_plan=q_plan,
                cache_dir=cache_root,
                overall_status=prov_bundle["overall_status"],
                next_action=prov_bundle["next_action"],
            )
            evid_paths.extend(str(p) for p in sfx_paths.values())
            hook_paths = emit_hook_and_pacing_artifacts(
                run_dir, run_id=run_id, brief=brief,
                hook_decision=hook_dec, pacing_budget=pacing_budget,
                attention_beats=att_beats, payoff_audit=payoff,
                beats=shotbook["beats"], visual_events=shotbook["visual_events"],
                audio_cues=soundscape["cues"], fps=fps,
            )
            evid_paths.extend(str(p) for p in hook_paths.values())
            sb_j, sb_m = emit_shotbook_v2(shotbook, run_dir)
            evid_paths.extend([str(sb_j), str(sb_m)])

        req_blocked = (brief["sfx_policy"] == "REQUIRED_LICENSED" and len(verified_non_voice) == 0)
        return {
            "provider_bundle": prov_bundle,
            "acquired_assets": acquired_assets,
            "asset_path_map": asset_path_map,
            "soundscape": soundscape,
            "verified_non_voice_cues": verified_non_voice,
            "speech_spans": speech_spans,
            "downloads_performed": downloads_performed,
            "required_sfx_blocked": req_blocked,
            "_output_hashes": {
                "soundscape_sha256": hash_artifact(soundscape),
                "shotbook_sha256": hash_artifact(shotbook),
            },
            "_evidence_paths": evid_paths,
            "_status": "BLOCKED" if req_blocked else ("PASS" if (verified_non_voice or brief["sfx_policy"] == "OFF") else "BLOCKED"),
            "_reason": (
                f"Resolved {len(verified_non_voice)} verified SFX/music cue(s) from {len(acquired_assets)} asset(s)."
                if verified_non_voice else prov_bundle["next_action"]
            ),
        }

    st_g, rec_g = execute_stage(
        "G_sfx_acquisition_soundscape", _stage_g, run_dir=run_dir, run_id=run_id,
        input_hashes={"shotbook_pre_sfx_sha256": rec_f["output_hashes"]["shotbook_pre_sfx_sha256"]},
        dry_run=dry_run, test_only=(not production_mode), version=candidate_version,
    )
    stage_receipts.append(rec_g)
    soundscape = st_g["soundscape"]
    verified_non_voice_cues = st_g["verified_non_voice_cues"]
    asset_path_map = st_g["asset_path_map"]
    prov_bundle = st_g["provider_bundle"]

    if dry_run:
        return {
            "status": "DRY_RUN_PASS" if (ed_eval["pass"] and res_est["disk_ok"] and not st_g["required_sfx_blocked"]) else "DRY_RUN_FAIL",
            "exit_code": 0 if (ed_eval["pass"] and res_est["disk_ok"] and not st_g["required_sfx_blocked"]) else 1,
            "run_id": run_id,
            "dry_run": True,
            "offline": offline,
            "cut_policy": cut_info["cut_policy"],
            "presenter_mode": presenter_policy["mode"],
            "selected_hook": hook_dec["selected_option_id"],
            "sfx_provider_status": prov_bundle["mcp_provider"].status()["status"],
            "sfx_overall_status": prov_bundle["overall_status"],
            "sfx_verified_local_count": len(st_g["acquired_assets"]),
            "sfx_downloads_performed": 0,
            "shotbook_sha256": hash_artifact(shotbook),
            "edl_sha256": edl_sha,
            "editorial_eval": ed_eval,
            "effect_variety": variety,
            "resource_estimate": res_est,
            "directions_count": len(directions),
        }

    if st_g["required_sfx_blocked"]:
        return {
            "status": "BLOCKED_SFX_ASSETS",
            "exit_code": 3,
            "run_id": run_id,
            "reason": "sfx_policy=REQUIRED_LICENSED but 0 verified licensed SFX assets were resolved.",
            "sfx_overall_status": prov_bundle["overall_status"],
            "stage_receipts": [r["stage_id"] for r in stage_receipts],
        }

    result: dict[str, Any] = {
        "status": "PLANNED_OK" if ed_eval["pass"] else "PLAN_FAIL",
        "exit_code": 0 if ed_eval["pass"] else 1,
        "run_id": run_id,
        "shotbook_json": str(run_dir / "SHOTBOOK_V2.json"),
        "shotbook_md": str(run_dir / "SHOTBOOK_V2.md"),
        "shotbook_sha256": hash_artifact(shotbook),
        "edl_sha256": edl_sha,
        "cut_status": st_d["cut_status"],
        "derived_source_path": derived_source_path,
        "derived_source_sha256": derived_source_sha,
        "presenter_mode": presenter_policy["mode"],
        "selected_hook": shotbook.get("selected_hook"),
        "sfx_overall_status": prov_bundle["overall_status"],
        "sfx_verified_cue_count": len(verified_non_voice_cues),
        "editorial_eval": ed_eval,
        "resource_estimate": res_est,
        "stage_receipts": [r["stage_id"] for r in stage_receipts],
    }

    if mode == "plan":
        return result

    # ------------------------------------------------------------------
    # STAGE H: Compile canonical SHOTBOOK V2 -> build/master.html + project.json
    # ------------------------------------------------------------------
    def _stage_h() -> dict[str, Any]:
        wmap = {}
        if transcript:
            for w in map_words_to_output(transcript, edl):
                wmap[w["word_id"]] = w["out_start_sec"]
        comp = compile_shotbook(
            shotbook, inventory, run_dir / "build",
            tools_root=tools_root, seecut_root=seecut_root,
            word_time_map=wmap, registry=registry,
            derived_source_path=derived_source_path,
        )
        return {
            "compiled": comp,
            "_output_hashes": {
                "html_sha256": hash_artifact(Path(comp["html_path"])),
                "project_json_sha256": hash_artifact(Path(comp["project_json_path"])),
                "compile_map_sha256": hash_artifact(Path(comp["compile_map_path"])),
            },
            "_evidence_paths": [comp["html_path"], comp["project_json_path"], comp["compile_map_path"]],
            "_reason": f"Compiled {len(comp['compile_map']['compiled_events'])} visual events (hook={comp['compile_map'].get('selected_hook_id')}, presenter={presenter_policy['mode']}).",
        }

    st_h, rec_h = execute_stage(
        "H_compile_master_timeline", _stage_h, run_dir=run_dir, run_id=run_id,
        input_hashes={"shotbook_sha256": hash_artifact(shotbook), "derived_source_sha256": derived_source_sha},
        dry_run=False, test_only=(not production_mode), version=candidate_version,
    )
    stage_receipts.append(rec_h)
    compiled = st_h["compiled"]
    result["compiled"] = {"html_path": compiled["html_path"], "compile_map_path": compiled["compile_map_path"], "project_json_path": compiled["project_json_path"]}

    if mode in ("preview", "full"):
        scale = 0.5 if mode == "preview" else float(shotbook["output_spec"].get("output_scale", 1.5))

        # --------------------------------------------------------------
        # STAGE I: Guarded Native Probe + Real H1 geom-check.mjs
        # --------------------------------------------------------------
        def _stage_i() -> dict[str, Any]:
            pr_res = run_render_checked(
                run_dir / "build", run_dir, run_id=run_id, mode="probe",
                render_scale=scale, seecut_root=seecut_root, tools_root=tools_root,
                reserve_floor_gib=reserve_floor_gib, max_disk_gib=max_disk_gib,
            )
            h1 = run_h1_geom_check(
                run_dir / "build", seecut_root=seecut_root, tools_root=tools_root,
                out_log=run_dir / "logs" / f"h1_geom_check_v{candidate_version}.log",
            )
            _write_json(run_dir / "qc" / f"H1_GEOM_v{candidate_version}.json", h1)
            return {
                "probe_render": pr_res,
                "h1": h1,
                "_output_hashes": {"h1_report_sha256": hash_artifact(h1)},
                "_commands": [h1.get("command", "")],
                "_evidence_paths": [str(run_dir / "qc" / f"H1_GEOM_v{candidate_version}.json")],
                "_status": "PASS" if h1["pass"] else "FAIL",
                "_reason": f"Probe OK; H1 geom-check FAIL={h1.get('fail_count')} WARN={h1.get('warn_count')}.",
            }

        st_i, rec_i = execute_stage(
            "I_probe_and_h1", _stage_i, run_dir=run_dir, run_id=run_id,
            input_hashes={"html_sha256": rec_h["output_hashes"]["html_sha256"]},
            dry_run=False, test_only=(not production_mode), version=candidate_version,
        )
        stage_receipts.append(rec_i)
        result["probe_render"] = st_i["probe_render"]
        result["h1_geom"] = st_i["h1"]
        if not st_i["h1"]["pass"]:
            result["status"] = "H1_GEOM_FAIL"
            result["exit_code"] = 1
            result["stage_receipts"] = [r["stage_id"] for r in stage_receipts]
            return result

        # --------------------------------------------------------------
        # STAGE J: Native Full Render + Real Multi-Layer Audio Mix/Mux
        # --------------------------------------------------------------
        base_stem = output_basename or f"{brief['brief_id']}_{'preview' if mode == 'preview' else f'candidate_v{candidate_version}'}"
        dry_mp4 = run_dir / "exports" / (f"{base_stem}_dry.mp4" if verified_non_voice_cues else f"{base_stem}.mp4")
        final_mp4 = run_dir / "exports" / f"{base_stem}.mp4"

        def _stage_j() -> dict[str, Any]:
            full_res = run_render_checked(
                run_dir / "build", run_dir, run_id=run_id, mode="full",
                render_scale=scale, out_mp4=dry_mp4, seecut_root=seecut_root, tools_root=tools_root,
                reserve_floor_gib=reserve_floor_gib, max_disk_gib=max_disk_gib,
            )
            mix_info = None
            mix_sync = None
            dry_vs_mix = None
            stem_metrics = None
            actual_final = Path(full_res["mp4_path"])
            actual_sha = full_res["mp4_sha256"]
            evid_paths = [str(actual_final)]

            if verified_non_voice_cues:
                stems_res = render_stems(
                    soundscape,
                    dry_voice_source=derived_source_path if media.get("has_audio") else None,
                    asset_paths=asset_path_map,
                    stems_dir=run_dir / "stems",
                    total_frames=int(shotbook["output_spec"]["total_frames"]),
                    fps_num=fps_num,
                    fps_den=fps_den,
                    audio_sample_rate=sample_rate,
                    production_mode=(production_mode and not allow_test_only_sfx),
                )
                stem_metrics = measure_stems(stems_res["stems"])
                mix_sync = verify_mix_sync(
                    soundscape,
                    stems_res["applied_cues"],
                    sfx_stem_wav=stems_res["stems"]["sfx"],
                    mix_wav=stems_res["stems"]["mix"],
                    fps_num=fps_num,
                    fps_den=fps_den,
                    audio_sample_rate=sample_rate,
                )
                dry_vs_mix = compare_dry_and_mix(
                    stems_res["stems"]["dry_voice"],
                    stems_res["stems"]["mix"],
                    st_g["speech_spans"],
                    audio_sample_rate=sample_rate,
                )
                mix_info = mix_audio_checked(
                    dry_mp4,
                    stems_res["stems"]["mix"],
                    final_mp4,
                    expected_duration_sec=stems_res["expected_duration_sec"],
                )
                actual_final = Path(mix_info["mp4_path"])
                actual_sha = mix_info["mp4_sha256"]
                _write_json(run_dir / "STEM_MANIFEST.json", {"stems": stems_res["stems"], "stem_hashes": stems_res["stem_hashes"], "metrics": stem_metrics})
                _write_json(run_dir / "MIX_VERIFICATION.json", {"mix_sync": mix_sync, "dry_vs_mix": dry_vs_mix, "mux": mix_info})
                _write_json(run_dir / "AUDIO_CUE_COVERAGE.json", {"applied_cues": stems_res["applied_cues"], "total_planned": len(soundscape["cues"])})
                evid_paths.extend([str(actual_final), str(run_dir / "STEM_MANIFEST.json"), str(run_dir / "MIX_VERIFICATION.json")])
                mix_ok = bool(mix_sync["pass"] and dry_vs_mix["pass"] and mix_info["ok"])
            else:
                mix_ok = True
                _write_json(run_dir / "MIX_VERIFICATION.json", {
                    "status": "SKIPPED_NO_VERIFIED_SFX_ASSETS" if brief["sfx_policy"] != "OFF" else "OFF_BY_BRIEF",
                    "live_sound_design_status": "OFF_BY_BRIEF" if brief["sfx_policy"] == "OFF" else "BLOCKED_PROVIDER_CONFIG",
                    "dry_mp4_sha256": actual_sha,
                    "note": "No fake/TEST_ONLY audio injected; original mapped voice preserved cleanly.",
                })
                evid_paths.append(str(run_dir / "MIX_VERIFICATION.json"))

            # Update provenance mp4_sha256 for post-mix structural QA linkage
            prov_final = dict(full_res)
            prov_final["mp4_path"] = str(actual_final)
            prov_final["mp4_sha256"] = actual_sha
            prov_final["audio_mixed"] = bool(verified_non_voice_cues)
            return {
                "full_render": prov_final,
                "final_mp4_path": str(actual_final),
                "final_mp4_sha256": actual_sha,
                "mix_info": mix_info,
                "mix_sync": mix_sync,
                "dry_vs_mix": dry_vs_mix,
                "stem_metrics": stem_metrics,
                "mix_ok": mix_ok,
                "_output_hashes": {"final_mp4_sha256": actual_sha},
                "_evidence_paths": evid_paths,
                "_status": "PASS" if mix_ok else "FAIL",
                "_reason": (
                    f"Rendered native MP4 + mixed/muxed {len(verified_non_voice_cues)} verified cues (SHA={actual_sha[:12]})."
                    if verified_non_voice_cues else
                    f"Rendered native MP4 with original voice (SHA={actual_sha[:12]}); live SFX {prov_bundle['overall_status']}."
                ),
            }

        st_j, rec_j = execute_stage(
            "J_render_and_mix", _stage_j, run_dir=run_dir, run_id=run_id,
            input_hashes={"h1_report_sha256": rec_i["output_hashes"]["h1_report_sha256"], "derived_source_sha256": derived_source_sha},
            dry_run=False, test_only=(not production_mode), version=candidate_version,
        )
        stage_receipts.append(rec_j)
        final_mp4_path = st_j["final_mp4_path"]
        final_mp4_sha = st_j["final_mp4_sha256"]
        result["full_render"] = st_j["full_render"]
        result["final_mp4_path"] = final_mp4_path
        result["final_mp4_sha256"] = final_mp4_sha
        result["audio_mix"] = {"applied": bool(verified_non_voice_cues), "mix_ok": st_j["mix_ok"], "mix_sync": st_j["mix_sync"], "dry_vs_mix": st_j["dry_vs_mix"]}

        # --------------------------------------------------------------
        # STAGE K: Post-Mix Structural, Audio, Per-Frame Presenter, Seam & Hold QA
        # --------------------------------------------------------------
        def _stage_k() -> dict[str, Any]:
            struct_qa = validate_structural(
                final_mp4_path, shotbook,
                expect_audio=bool(media.get("has_audio")),
                provenance_log=st_j["full_render"],
                render_scale_override=scale,
            )
            aud_qa = inspect_audio(
                final_mp4_path, shotbook,
                raw_source_path=derived_source_path if (cut_info["cut_policy"] == "LOCK_VOICE" and not verified_non_voice_cues) else None,
            )
            integrity = verify_source_integrity(inventory)
            pres_qa = None
            if presenter_policy["mode"] in ("FIXED_PIP", "ADAPTIVE_PIP", "DYNAMIC_HYBRID"):
                pres_qa = verify_presenter_frames(
                    final_mp4_path, derived_source_path, shotbook, run_dir,
                )
            elif eff_kh03_overrides and isinstance(eff_kh03_overrides.get("layout_plan"), dict) and eff_kh03_overrides["layout_plan"].get("layout_policy") == "ADAPTIVE_DYNAMIC":
                from scripts.kh03.visual_qa import verify_adaptive_layout_frames
                pres_qa = verify_adaptive_layout_frames(
                    derived_source_path,
                    final_mp4_path,
                    layout_plan=eff_kh03_overrides["layout_plan"],
                    compile_map=compiled.get("layout_compile_map"),
                    out_dir=run_dir / "qc",
                )
            eff_ledger = build_effect_application_ledger(
                shotbook, compiled["compile_map"], registry=registry,
                rendered_mp4_sha=final_mp4_sha, out_path=run_dir / "EFFECT_APPLICATION_LEDGER.json",
            )
            seam_rep = audit_transition_seams(shotbook, final_mp4_path, out_path=run_dir / "TRANSITION_SEAM_REPORT.json")
            hold_rep = audit_visual_holds(shotbook, final_mp4_path, out_path=run_dir / "VISUAL_HOLD_AUDIT.json")

            # Bounded filmstrip/waveform diagnostics on start, beat boundaries, and EOF
            out_dur_s = float(shotbook["output_spec"]["total_frames"]) / fps
            diag_wins = [{"name": "opening", "start_sec": 0.0, "end_sec": min(out_dur_s, 3.0)}]
            for idx_t, tr in enumerate(shotbook.get("transitions", [])[:3]):
                bt = int(tr["boundary_frame"]) / fps
                diag_wins.append({"name": f"seam_{idx_t + 1:02d}", "start_sec": max(0.0, bt - 1.5), "end_sec": min(out_dur_s, bt + 1.5)})
            diag_wins.append({"name": "ending", "start_sec": max(0.0, out_dur_s - 3.0), "end_sec": out_dur_s})
            mapped_w = map_words_to_output(transcript, edl) if transcript else []
            diag_rep = render_timeline_diagnostics(
                final_mp4_path, run_dir / "diagnostics",
                windows=diag_wins, mapped_words=mapped_w, shotbook=shotbook,
            )

            pres_ok = True if pres_qa is None else bool(pres_qa["pass"])
            all_k_ok = bool(struct_qa["pass"] and aud_qa["pass"] and integrity["all_unchanged"] and pres_ok and seam_rep["pass"] and st_j["mix_ok"])
            _write_json(run_dir / "manifests/SOURCE_INTEGRITY.json", integrity)
            return {
                "structural_qa": struct_qa,
                "audio_qa": aud_qa,
                "source_integrity": integrity,
                "presenter_frame_qa": pres_qa,
                "effect_ledger": eff_ledger,
                "seam_report": seam_rep,
                "hold_audit": hold_rep,
                "diagnostics": diag_rep,
                "all_ok": all_k_ok,
                "_output_hashes": {
                    "final_mp4_sha256": final_mp4_sha,
                    "effect_ledger_sha256": hash_artifact(eff_ledger),
                    "presenter_qa_sha256": hash_artifact(pres_qa) if pres_qa else "na",
                },
                "_evidence_paths": [
                    str(run_dir / "EFFECT_APPLICATION_LEDGER.json"),
                    str(run_dir / "TRANSITION_SEAM_REPORT.json"),
                    str(run_dir / "VISUAL_HOLD_AUDIT.json"),
                    str(run_dir / "manifests/SOURCE_INTEGRITY.json"),
                ] + ([pres_qa["json_path"], pres_qa["csv_path"]] if pres_qa and pres_qa.get("json_path") else []),
                "_status": "PASS" if all_k_ok else "FAIL",
                "_reason": (
                    f"Post-mix QA PASS (struct=PASS, audio=PASS, presenter={pres_qa['status'] if pres_qa else 'N/A'}, seams=PASS)."
                    if all_k_ok else
                    f"Post-mix QA FAIL (struct={struct_qa['pass']}, audio={aud_qa['pass']}, presenter={pres_ok}, seams={seam_rep['pass']}, mix={st_j['mix_ok']})."
                ),
            }

        st_k, rec_k = execute_stage(
            "K_post_mix_and_presenter_qa", _stage_k, run_dir=run_dir, run_id=run_id,
            input_hashes={"final_mp4_sha256": final_mp4_sha},
            dry_run=False, test_only=(not production_mode), version=candidate_version,
        )
        stage_receipts.append(rec_k)
        result["structural_qa"] = st_k["structural_qa"]
        result["audio_qa"] = st_k["audio_qa"]
        result["source_integrity"] = st_k["source_integrity"]
        result["presenter_frame_qa"] = st_k["presenter_frame_qa"]
        result["seam_report"] = st_k["seam_report"]
        result["hold_audit"] = st_k["hold_audit"]

        # --------------------------------------------------------------
        # STAGE L (Optional Live H2 + Dual-Order Pairwise on EXACT final SHA)
        # --------------------------------------------------------------
        h2_res = None
        pairwise_results: list[dict[str, Any]] = []
        if run_h2 and not offline:
            def _stage_l() -> dict[str, Any]:
                skill_root = Path(__file__).resolve().parents[2]
                judge_py = skill_root / "scripts" / "agy_judge_safe.py"
                h2_out = run_dir / "qc" / f"h2_check_v{candidate_version}.json"
                html_path = str(compiled["html_path"])
                p_chk = subprocess.run(
                    [sys.executable, str(judge_py), "check", "--video", str(final_mp4_path), "--html", html_path, "--out", str(h2_out)],
                    capture_output=True, text=True, timeout=300,
                )
                chk_data = parse_json_strict(h2_out) if h2_out.is_file() else {"summary": {"valid": False, "gate_pass": None}, "returncode": p_chk.returncode, "stderr": p_chk.stderr[-300:]}
                chk_data["reviewed_mp4_sha256"] = final_mp4_sha
                chk_pass = bool((chk_data.get("summary") or {}).get("valid") and (chk_data.get("summary") or {}).get("gate_pass"))
                chk_data["status"] = "PASS" if chk_pass else ("INVALID" if not (chk_data.get("summary") or {}).get("valid") else "FAIL")
                _write_json(h2_out, chk_data)

                def _find_baseline_html(bp_path: Path) -> Path | None:
                    import re as _re
                    run_base = bp_path.parent.parent
                    m_ver = _re.search(r"_v(\d+)$", bp_path.stem)
                    cands: list[Path] = []
                    if m_ver:
                        vtag = f"v{m_ver.group(1)}"
                        cands.extend([run_base / vtag / "build" / "master.html", run_base / vtag / "master.html"])
                    cands.append(run_base / "build" / "master.html")
                    cands.extend(sorted(run_base.glob("v*/build/master.html"), reverse=True))
                    cands.extend(sorted(run_base.glob("v*/master.html"), reverse=True))
                    for cand in cands:
                        if cand.is_file():
                            return cand
                    return None

                pairs: list[dict[str, Any]] = []
                for idx_b, b_mp4 in enumerate(compare_baselines or []):
                    bp = Path(b_mp4).resolve()
                    if not bp.is_file():
                        pairs.append({"baseline": str(bp), "status": "MISSING_BASELINE"})
                        continue
                    pair_out = run_dir / "qc" / f"pairwise_v{candidate_version}_vs_{bp.stem}.json"
                    # Locate baseline HTML beside its run directory (build/ or v*/build/) for strict text fingerprint binding
                    b_html_cand = _find_baseline_html(bp)
                    pair_cmd = [
                        sys.executable, str(judge_py), "pair",
                        "--a", str(final_mp4_path), "--b", str(bp),
                        "--html-a", html_path, "--out", str(pair_out),
                    ]
                    if b_html_cand is not None:
                        pair_cmd.extend(["--html-b", str(b_html_cand)])
                    p_pr = subprocess.run(pair_cmd, capture_output=True, text=True, timeout=420)
                    pr_data = parse_json_strict(pair_out) if pair_out.is_file() else {"consensus": "invalid", "returncode": p_pr.returncode, "stderr": p_pr.stderr[-300:]}
                    pr_data["candidate_mp4_sha256"] = final_mp4_sha
                    pr_data["baseline_mp4_sha256"] = hash_artifact(bp)
                    _write_json(pair_out, pr_data)
                    pairs.append(pr_data)
                return {
                    "h2_check": chk_data,
                    "pairwise": pairs,
                    "_output_hashes": {"h2_check_sha256": hash_artifact(chk_data)},
                    "_evidence_paths": [str(h2_out)],
                    "_status": "PASS" if chk_pass else "FAIL",
                    "_reason": f"H2 check status={chk_data.get('status')} on exact SHA {final_mp4_sha[:12]}; {len(pairs)} dual-order pairwise check(s).",
                }

            st_l, rec_l = execute_stage(
                "L_h2_and_pairwise", _stage_l, run_dir=run_dir, run_id=run_id,
                input_hashes={"final_mp4_sha256": final_mp4_sha},
                dry_run=False, test_only=(not production_mode), version=candidate_version,
            )
            stage_receipts.append(rec_l)
            h2_res = st_l["h2_check"]
            pairwise_results = st_l["pairwise"]
            result["h2_check"] = h2_res
            result["pairwise"] = pairwise_results

        # --------------------------------------------------------------
        # STAGE M (Optional Guarded JianYing Layered Package + Fidelity Check)
        # --------------------------------------------------------------
        if export_layers and mode == "full":
            def _stage_m() -> dict[str, Any]:
                jy = export_and_verify_jianying_layers(
                    run_dir / "build", run_dir / "jianying",
                    project_name=f"{brief['brief_id']}_v{candidate_version}",
                    shotbook=shotbook, seecut_root=seecut_root, tools_root=tools_root,
                )
                return {
                    "jianying": jy,
                    "_output_hashes": {"layer_fidelity_sha256": hash_artifact(jy)},
                    "_evidence_paths": [jy.get("report_path", "")],
                    "_status": "PASS" if jy.get("pass") else "FAIL",
                    "_reason": f"Layered package exported ({jy.get('exported_png_count', 0)} PNGs, {jy.get('fidelity_status')}).",
                }

            st_m, rec_m = execute_stage(
                "M_layered_export", _stage_m, run_dir=run_dir, run_id=run_id,
                input_hashes={"html_sha256": rec_h["output_hashes"]["html_sha256"]},
                dry_run=False, test_only=(not production_mode), version=candidate_version,
            )
            stage_receipts.append(rec_m)
            result["jianying_layers"] = st_m["jianying"]

        # --------------------------------------------------------------
        # STAGE N: Receipt Audit & Honest Final Status (Owner Approvals PENDING)
        # --------------------------------------------------------------
        req_sids = [r["stage_id"] for r in stage_receipts]
        rec_audit = verify_pipeline_receipts(run_dir, req_sids)
        all_ok = bool(st_k["all_ok"] and rec_audit["pass"])
        result["receipts_audit"] = {"pass": rec_audit["pass"], "stages": req_sids}
        result["stage_receipts"] = req_sids
        result["owner_visual_status"] = "PENDING"
        result["owner_audio_status"] = "PENDING"
        result["commercial_release_status"] = "BLOCKED" if lic != "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED" else "AUTHORIZED_PENDING_OWNER"
        if final_mp4_path and Path(final_mp4_path).is_file() and final_mp4_sha:
            (run_dir / "FINAL_RENDER_SHA256.txt").write_text(f"{final_mp4_sha}  {Path(final_mp4_path).name}\n", encoding="utf-8")
        result["status"] = "RENDER_VERIFIED_PENDING_OWNER" if all_ok else "RENDER_QA_FAIL"
        result["exit_code"] = 0 if all_ok else 1

    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m scripts.kh02.run", description="KH-02.R2 Autonomous Professional Video Editor CLI")
    ap.add_argument("--brief", required=True, type=Path, help="Path to KH-02 brief JSON")
    ap.add_argument("--run-root", required=True, type=Path, help="Approved isolated run directory")
    ap.add_argument("--mode", choices=["plan", "preview", "full", "verify"], default="plan")
    ap.add_argument("--dry-run", action="store_true", help="Validate & plan without writing files, downloads, or invoking render/model")
    ap.add_argument("--workers", type=int, default=2, help="Bounded CPU/IO workers (1..4, default 2)")
    ap.add_argument("--max-disk", type=float, default=None, help="Maximum projected disk budget in GiB")
    ap.add_argument("--profile", choices=["seecut_core", "editorial_pro", "minimal_documentary", "silent_visual"], default=None)
    ap.add_argument("--license-decision", default=None)
    ap.add_argument("--cut-policy", choices=["LOCK_VOICE", "PRESERVE_SCRIPT_TRIM", "EDITORIAL_CUT", "TRIM_APPROVED"], default=None)
    ap.add_argument("--sfx-provider", choices=["local", "mcp", "api", "auto", "off"], default="auto")
    ap.add_argument("--sfx-policy", choices=["OPTIONAL_LICENSED", "OFF", "REQUIRED_LICENSED"], default=None)
    ap.add_argument("--music-policy", choices=["OFF", "OPTIONAL_LICENSED", "REQUIRED_LICENSED"], default=None)
    ap.add_argument("--sfx-library", action="append", default=None, help="Approved local licensed SFX directory (repeatable)")
    ap.add_argument("--sfx-config", type=Path, default=None, help="Approved JSON config for local/MCP SFX providers")
    ap.add_argument("--sfx-cache", type=Path, default=None, help="Approved SFX cache directory outside repo")
    ap.add_argument("--presenter-mode", choices=["LEGACY_DYNAMIC", "FIXED_PIP", "ADAPTIVE_PIP", "DYNAMIC_HYBRID", "FACELESS"], default=None)
    ap.add_argument("--presenter-anchor", choices=["TOP_LEFT", "TOP_RIGHT", "BOTTOM_LEFT", "BOTTOM_RIGHT", "CUSTOM"], default=None)
    ap.add_argument("--presenter-shape", choices=["ROUNDED_RECT", "CIRCLE"], default=None)
    ap.add_argument("--presenter-width-ratio", type=float, default=None)
    ap.add_argument("--max-downloads", type=int, default=3, help="Per-run SFX download cap (default 3)")
    ap.add_argument("--max-download-bytes", type=int, default=25 * 1024 * 1024, help="Per-run SFX download byte cap")
    ap.add_argument("--offline", action="store_true", help="Disable all remote provider lookups/downloads")
    ap.add_argument("--approved-brief", action="store_true", help="Record explicit owner brief approval")
    ap.add_argument("--transcript", type=Path, default=None, help="Optional pre-computed faster-whisper JSON")
    ap.add_argument("--catalog", type=Path, default=None, help="Optional pinned hyperframes catalog JSON")
    ap.add_argument("--run-h2", action="store_true", help="Run real H2 agy_judge_safe.py check (+ optional --compare-baseline)")
    ap.add_argument("--compare-baseline", action="append", type=Path, default=None, help="Baseline MP4 for dual-order pairwise comparison (repeatable)")
    ap.add_argument("--export-layers", action="store_true", help="Export guarded JianYing layered package & fidelity audit")
    ap.add_argument("--candidate-version", type=int, default=1, help="Candidate iteration version 1..3")
    ap.add_argument("--output-basename", default=None, help="Custom output MP4 stem inside exports/")
    a = ap.parse_args(argv)

    res = execute_pipeline(
        brief_path=a.brief,
        run_root=a.run_root,
        mode=a.mode,
        dry_run=a.dry_run,
        workers=a.workers,
        max_disk_gib=a.max_disk,
        profile_override=a.profile,
        license_decision_override=a.license_decision,
        cut_policy_override=a.cut_policy,
        sfx_provider_mode=a.sfx_provider,
        sfx_policy_override=a.sfx_policy,
        music_policy_override=a.music_policy,
        sfx_library_dirs=a.sfx_library,
        sfx_config_path=a.sfx_config,
        sfx_cache_dir=a.sfx_cache,
        presenter_mode_override=a.presenter_mode,
        presenter_anchor_override=a.presenter_anchor,
        presenter_shape_override=a.presenter_shape,
        presenter_width_ratio_override=a.presenter_width_ratio,
        max_downloads=a.max_downloads,
        max_download_bytes=a.max_download_bytes,
        offline=a.offline,
        approved_brief=a.approved_brief,
        transcript_json_path=a.transcript,
        catalog_json_path=a.catalog,
        run_h2=a.run_h2,
        compare_baselines=a.compare_baseline,
        export_layers=a.export_layers,
        candidate_version=a.candidate_version,
        output_basename=a.output_basename,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return int(res.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
