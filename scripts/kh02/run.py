"""KH-02 Unified CLI Orchestrator (`python3 -m scripts.kh02.run`)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

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
from .qa import build_review_report, evaluate_editorial, inspect_audio, validate_structural
from .registry import audit_effect_variety, build_registry
from .render_guard import DEFAULT_DISK_RESERVE_GIB, estimate_resources, run_render_checked
from .sfx_assets import LocalProvider, McpProvider, emit_sfx_asset_artifacts
from .sound import plan_soundscape
from .source_analysis import build_inventory, verify_source_integrity
from .transcript import build_packed_transcript, cache_transcript, normalize_faster_whisper


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    sfx_provider_mode: str = "local",
    sfx_policy_override: str | None = None,
    music_policy_override: str | None = None,
    max_downloads: int = 3,
    max_download_bytes: int = 25 * 1024 * 1024,
    offline: bool = False,
    approved_brief: bool = False,
    transcript_json_path: Path | None = None,
    catalog_json_path: Path | None = None,
    seecut_root: Path | None = None,
    tools_root: Path | None = None,
    reserve_floor_gib: float = DEFAULT_DISK_RESERVE_GIB,
) -> dict[str, Any]:
    """Single-entry orchestrated KH-02.R1 pipeline with strict license gate, bounded workers & dry-run safety."""
    workers = max(1, min(4, int(workers)))
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
    brief = build_editor_brief(raw_brief)

    # Hard License Gate BEFORE executing SeeCut render
    lic = brief["license_decision"]
    if lic in ("BLOCKED_COMMERCIAL_LICENSE", "BLOCKED_LICENSE_UNRESOLVED"):
        return {
            "status": lic,
            "exit_code": 2,
            "reason": f"License gate blocked execution ({lic}); PolyForm Noncommercial 1.0.0 requires noncommercial confirmation or written commercial permission from aiesecxuanli@gmail.com.",
        }

    run_dir = Path(os.path.realpath(str(run_root.expanduser())))
    run_id = run_dir.name
    if not dry_run:
        for sub in ("build", "qc", "exports", "jianying/layers", "logs", "evidence", "probe", "cache", "manifests", "stems"):
            (run_dir / sub).mkdir(parents=True, exist_ok=True)

    # Resolve relative source paths relative to brief_path.parent
    resolved_sources = []
    for s in brief["sources"]:
        sc = dict(s)
        sp = Path(sc["path"])
        if not sp.is_absolute():
            sc["path"] = str((brief_path.parent / sp).resolve())
        resolved_sources.append(sc)

    # Bounded read-only parallel stage: source inventory + capability registry
    with ThreadPoolExecutor(max_workers=min(workers, 2)) as ex:
        f_inv = ex.submit(
            build_inventory, resolved_sources, run_id,
            cache_dir=(None if dry_run else run_dir / "cache"),
            analyze_audio_motion=(not dry_run),
        )
        f_reg = ex.submit(build_registry, catalog_json_path=catalog_json_path)
        inventory = f_inv.result()
        registry = f_reg.result()

    # Optional transcript normalization
    transcript = None
    primary = next((a for a in inventory["assets"] if a["usable_in_render"]), inventory["assets"][0])
    if transcript_json_path and transcript_json_path.is_file():
        raw_tr = parse_json_strict(transcript_json_path)
        transcript = normalize_faster_whisper(
            raw_tr,
            source_asset_id=primary["asset_id"],
            audio_sha256=primary["sha256"],
            language=brief["language"],
        )
        if not dry_run:
            cache_transcript(run_dir / "cache", primary["sha256"], transcript["model"], transcript["language"], transcript=transcript)
            _write_json(run_dir / "TRANSCRIPT_NORMALIZED.json", transcript)
            (run_dir / "TAKES_PACKED.md").write_text(build_packed_transcript(transcript, title=brief.get("title")), encoding="utf-8")

    # R1.01 Rough Cut Policy & Candidates (dry-run never mutates files or invokes remote providers)
    eff_cut_pol = brief.get("cut_policy") or brief.get("voice_policy", "LOCK_VOICE")
    cut_info = resolve_cut_policy(eff_cut_pol, approved_scope=brief.get("approved_scope"))
    media = primary["media"]
    fps_num = int(media.get("fps_num") or 24)
    fps_den = int(media.get("fps_den") or 1)
    fps = fps_num / fps_den
    total_src_frames = int(media.get("nb_frames") or max(1, int(round((media.get("duration_sec") or 6.0) * fps))))

    cut_cands = propose_r1_cut_candidates(
        source_asset_id=primary["asset_id"],
        source_sha256=primary["sha256"],
        source_duration_sec=float(media.get("duration_sec") or total_src_frames / fps),
        transcript=transcript,
        silences=primary.get("silences"),
        cut_policy=cut_info["cut_policy"],
        approved_scope=cut_info.get("approval_scope"),
        has_audio_track=bool(media.get("has_audio", True)),
    )

    # R1.02 Hook & Pacing Director
    hook_opts = build_hook_candidates(brief, inventory, transcript=transcript, cut_policy=cut_info["cut_policy"])
    hook_dec = choose_opening(hook_opts, brief=brief)
    pacing_budget = resolve_pacing_budget(brief, total_duration_sec=float(media.get("duration_sec") or total_src_frames / fps))

    # Resolve canonical SHOTBOOK V2
    directions = propose_directions(brief, inventory, transcript)
    shotbook = resolve_editorial_plan(
        brief, inventory, run_id=run_id,
        transcript=transcript, registry=registry,
        status="TEST_ONLY" if dry_run else "PLANNED",
    )
    att_beats = plan_attention_beats(shotbook["beats"], brief=brief, fps=fps)
    payoff = audit_payoff(hook_dec, shotbook["beats"], claim_evidence=shotbook.get("claim_evidence"), brief=brief)
    variety = audit_effect_variety(shotbook["visual_events"], registry=registry)
    ed_eval = evaluate_editorial(shotbook, inventory=inventory, registry=registry)
    res_est = estimate_resources(
        shotbook["output_spec"]["total_frames"],
        shotbook["output_spec"]["width"],
        shotbook["output_spec"]["height"],
        render_scale=float(shotbook["output_spec"].get("output_scale", 1.5)),
        target_dir=run_dir,
        reserve_floor_gib=reserve_floor_gib,
        max_disk_gib=max_disk_gib,
    )

    # R1.03 Local/MCP SFX provider status (zero remote downloads on dry-run or offline)
    sfx_local = LocalProvider([], intended_purpose="NONCOMMERCIAL_ONLY")
    sfx_mcp = McpProvider("sfx_mcp", server_config={"configured": False}, offline=(offline or dry_run))
    sfx_soundscape = plan_soundscape(
        shotbook, [],
        voice_asset_id=primary["asset_id"] if media.get("has_audio") else None,
        energy_mode=pacing_budget["energy_mode"],
        sfx_policy=brief["sfx_policy"],
        bgm_policy=brief["music_policy"],
        production_mode=True,
    )

    if dry_run:
        return {
            "status": "DRY_RUN_PASS" if (ed_eval["pass"] and res_est["disk_ok"]) else "DRY_RUN_FAIL",
            "exit_code": 0 if (ed_eval["pass"] and res_est["disk_ok"]) else 1,
            "run_id": run_id,
            "dry_run": True,
            "offline": offline,
            "cut_policy": cut_info["cut_policy"],
            "selected_hook": hook_dec["selected_option_id"],
            "sfx_provider_status": sfx_mcp.status()["status"],
            "sfx_downloads_performed": 0,
            "shotbook_sha256": hash_artifact(shotbook),
            "editorial_eval": ed_eval,
            "effect_variety": variety,
            "resource_estimate": res_est,
            "directions_count": len(directions),
        }

    # Persist Phase 01/02 artifacts
    _write_json(run_dir / "manifests/SOURCE_INVENTORY.json", inventory)
    _write_json(run_dir / "manifests/CAPABILITY_REGISTRY.json", registry)
    _write_json(run_dir / "manifests/CREATIVE_DIRECTIONS.json", directions)
    _write_json(run_dir / "sfx_plan.json", sfx_soundscape)
    emit_rough_cut_artifacts(
        run_dir,
        candidate_doc=cut_cands,
        edl=shotbook["edit_decision_map"],
        original_transcript=transcript,
    )
    emit_hook_and_pacing_artifacts(
        run_dir,
        run_id=run_id,
        brief=brief,
        hook_decision=hook_dec,
        pacing_budget=pacing_budget,
        attention_beats=att_beats,
        payoff_audit=payoff,
        beats=shotbook["beats"],
        visual_events=shotbook["visual_events"],
        audio_cues=sfx_soundscape["cues"],
        fps=fps,
    )
    emit_sfx_asset_artifacts(
        run_dir,
        assets=[],
        provider_statuses=[sfx_local.status(), sfx_mcp.status()],
        missing_categories=sorted({c.get("role", "foley") for c in sfx_soundscape["cues"] if c.get("status") == "UNAVAILABLE"}),
        acquisition_log=[],
        query_plan=[],
        cache_dir=run_dir / "cache",
        overall_status="OFF_BY_BRIEF" if brief["sfx_policy"] == "OFF" else ("OFFLINE_CACHE_ONLY" if offline else "BLOCKED_PROVIDER_CONFIG"),
        next_action="Supply verified licensed local SFX directory or configure an authorized MCP SFX server with API credentials.",
    )
    sb_json, sb_md = emit_shotbook_v2(shotbook, run_dir)

    result: dict[str, Any] = {
        "status": "PLANNED_OK" if ed_eval["pass"] else "PLAN_FAIL",
        "exit_code": 0 if ed_eval["pass"] else 1,
        "run_id": run_id,
        "shotbook_json": str(sb_json),
        "shotbook_md": str(sb_md),
        "shotbook_sha256": hash_artifact(shotbook),
        "editorial_eval": ed_eval,
        "resource_estimate": res_est,
    }

    if mode == "plan":
        return result

    # Compile canonical SHOTBOOK V2 -> build/master.html + project.json
    wmap = {}
    if transcript:
        from .transcript import map_words_to_output
        for w in map_words_to_output(transcript, shotbook["edit_decision_map"]):
            wmap[w["word_id"]] = w["out_start_sec"]

    compiled = compile_shotbook(
        shotbook, inventory, run_dir / "build",
        tools_root=tools_root, seecut_root=seecut_root,
        word_time_map=wmap, registry=registry,
    )
    result["compiled"] = {"html_path": compiled["html_path"], "compile_map_path": compiled["compile_map_path"]}

    if mode in ("preview", "full"):
        scale = 0.5 if mode == "preview" else float(shotbook["output_spec"].get("output_scale", 1.5))
        out_mp4 = run_dir / "exports" / (f"{brief['brief_id']}_preview.mp4" if mode == "preview" else f"{brief['brief_id']}_full.mp4")
        # Probe first
        probe_res = run_render_checked(
            run_dir / "build", run_dir, run_id=run_id, mode="probe",
            render_scale=scale, seecut_root=seecut_root, tools_root=tools_root,
            reserve_floor_gib=reserve_floor_gib, max_disk_gib=max_disk_gib,
        )
        full_res = run_render_checked(
            run_dir / "build", run_dir, run_id=run_id, mode="full",
            render_scale=scale, out_mp4=out_mp4, seecut_root=seecut_root, tools_root=tools_root,
            reserve_floor_gib=reserve_floor_gib, max_disk_gib=max_disk_gib,
        )
        result["probe_render"] = probe_res
        result["full_render"] = full_res

        # Post-render structural & audio QA
        if mode == "full" and full_res.get("mp4_path"):
            primary = next((a for a in inventory["assets"] if a["usable_in_render"]), inventory["assets"][0])
            struct_qa = validate_structural(
                full_res["mp4_path"], shotbook,
                expect_audio=bool(primary["media"].get("has_audio")),
                provenance_log=full_res,
            )
            aud_qa = inspect_audio(full_res["mp4_path"], shotbook, raw_source_path=primary["canonical_path"])
            integrity = verify_source_integrity(inventory)
            result["structural_qa"] = struct_qa
            result["audio_qa"] = aud_qa
            result["source_integrity"] = integrity
            all_ok = struct_qa["pass"] and aud_qa["pass"] and integrity["all_unchanged"]
            result["status"] = "RENDER_VERIFIED_PENDING_OWNER" if all_ok else "RENDER_QA_FAIL"
            result["exit_code"] = 0 if all_ok else 1

    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m scripts.kh02.run", description="KH-02.R1 Autonomous Professional Video Editor CLI")
    ap.add_argument("--brief", required=True, type=Path, help="Path to KH-02 brief JSON")
    ap.add_argument("--run-root", required=True, type=Path, help="Approved isolated run directory")
    ap.add_argument("--mode", choices=["plan", "preview", "full", "verify"], default="plan")
    ap.add_argument("--dry-run", action="store_true", help="Validate & plan without writing files, downloads, or invoking render/model")
    ap.add_argument("--workers", type=int, default=2, help="Bounded CPU/IO workers (1..4, default 2)")
    ap.add_argument("--max-disk", type=float, default=None, help="Maximum projected disk budget in GiB")
    ap.add_argument("--profile", choices=["seecut_core", "editorial_pro", "minimal_documentary", "silent_visual"], default=None)
    ap.add_argument("--license-decision", default=None)
    ap.add_argument("--cut-policy", choices=["LOCK_VOICE", "PRESERVE_SCRIPT_TRIM", "EDITORIAL_CUT", "TRIM_APPROVED"], default=None)
    ap.add_argument("--sfx-provider", choices=["local", "mcp", "api", "auto", "off"], default="local")
    ap.add_argument("--sfx-policy", choices=["OPTIONAL_LICENSED", "OFF", "REQUIRED_LICENSED"], default=None)
    ap.add_argument("--music-policy", choices=["OFF", "OPTIONAL_LICENSED", "REQUIRED_LICENSED"], default=None)
    ap.add_argument("--max-downloads", type=int, default=3, help="Per-run SFX download cap (default 3)")
    ap.add_argument("--max-download-bytes", type=int, default=25 * 1024 * 1024, help="Per-run SFX download byte cap")
    ap.add_argument("--offline", action="store_true", help="Disable all remote provider lookups/downloads")
    ap.add_argument("--approved-brief", action="store_true", help="Record explicit owner brief approval")
    ap.add_argument("--transcript", type=Path, default=None, help="Optional pre-computed faster-whisper JSON")
    ap.add_argument("--catalog", type=Path, default=None, help="Optional pinned hyperframes catalog JSON")
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
        max_downloads=a.max_downloads,
        max_download_bytes=a.max_download_bytes,
        offline=a.offline,
        approved_brief=a.approved_brief,
        transcript_json_path=a.transcript,
        catalog_json_path=a.catalog,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return int(res.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
