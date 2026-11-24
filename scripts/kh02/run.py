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
from .planner import build_editor_brief, emit_shotbook_v2, propose_directions, resolve_editorial_plan
from .qa import build_review_report, evaluate_editorial, inspect_audio, validate_structural
from .registry import build_registry
from .render_guard import DEFAULT_DISK_RESERVE_GIB, estimate_resources, run_render_checked
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
    transcript_json_path: Path | None = None,
    catalog_json_path: Path | None = None,
    seecut_root: Path | None = None,
    tools_root: Path | None = None,
    reserve_floor_gib: float = DEFAULT_DISK_RESERVE_GIB,
) -> dict[str, Any]:
    """Single-entry orchestrated pipeline with strict license gate, bounded workers, and hash-validated stages."""
    workers = max(1, min(4, int(workers)))
    raw_brief = parse_json_strict(brief_path)
    if profile_override:
        raw_brief["profile"] = profile_override
    if license_decision_override:
        raw_brief["license_decision"] = license_decision_override
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
        for sub in ("build", "qc", "exports", "jianying/layers", "logs", "evidence", "probe", "cache", "manifests"):
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
    if transcript_json_path and transcript_json_path.is_file():
        primary = next((a for a in inventory["assets"] if a["usable_in_render"]), inventory["assets"][0])
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

    # Resolve canonical SHOTBOOK V2
    directions = propose_directions(brief, inventory, transcript)
    shotbook = resolve_editorial_plan(
        brief, inventory, run_id=run_id,
        transcript=transcript, registry=registry,
        status="TEST_ONLY" if dry_run else "PLANNED",
    )
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

    if dry_run:
        return {
            "status": "DRY_RUN_PASS" if (ed_eval["pass"] and res_est["disk_ok"]) else "DRY_RUN_FAIL",
            "exit_code": 0 if (ed_eval["pass"] and res_est["disk_ok"]) else 1,
            "run_id": run_id,
            "dry_run": True,
            "shotbook_sha256": hash_artifact(shotbook),
            "editorial_eval": ed_eval,
            "resource_estimate": res_est,
            "directions_count": len(directions),
        }

    # Persist Phase 01/02 artifacts
    _write_json(run_dir / "manifests/SOURCE_INVENTORY.json", inventory)
    _write_json(run_dir / "manifests/CAPABILITY_REGISTRY.json", registry)
    _write_json(run_dir / "manifests/CREATIVE_DIRECTIONS.json", directions)
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
    ap = argparse.ArgumentParser(prog="python3 -m scripts.kh02.run", description="KH-02 Autonomous Professional Video Editor CLI")
    ap.add_argument("--brief", required=True, type=Path, help="Path to KH-02 brief JSON")
    ap.add_argument("--run-root", required=True, type=Path, help="Approved isolated run directory")
    ap.add_argument("--mode", choices=["plan", "preview", "full", "verify"], default="plan")
    ap.add_argument("--dry-run", action="store_true", help="Validate & plan without writing files or invoking render/model")
    ap.add_argument("--workers", type=int, default=2, help="Bounded CPU/IO workers (1..4, default 2)")
    ap.add_argument("--max-disk", type=float, default=None, help="Maximum projected disk budget in GiB")
    ap.add_argument("--profile", choices=["seecut_core", "editorial_pro", "minimal_documentary", "silent_visual"], default=None)
    ap.add_argument("--license-decision", default=None)
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
        transcript_json_path=a.transcript,
        catalog_json_path=a.catalog,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return int(res.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
