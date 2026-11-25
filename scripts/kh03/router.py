"""KH-03 Single-Entry Router & Normal Pipeline Integrator (`python -m scripts.kh03`).

Enforces the strict two-step workflow:
  Step 1 (`recommend`): analyze_inputs -> build_semantic_graph -> recommend_skills -> issue_recommendation -> STOP (AWAITING_USER_SELECTION)
  Step 2 (`select`): explicit user choice -> SELECTION_RECEIPT.json (bound to run/video/script/recommendation hashes)
  Step 3 (`execute`): validate_user_selection + PROBLEM-01 license gate -> research -> footage -> explanation -> director -> sound_director -> KH-02 execute_pipeline() -> KH-03 QA.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from scripts.kh02.registry import build_registry
from scripts.kh02.render_guard import DEFAULT_DISK_RESERVE_GIB
from scripts.kh02.run import execute_pipeline as execute_kh02_pipeline

from . import BASE_QA_SHA, REGISTRY_VERSION, UPSTREAM_SEECUT_SHA, VALID_SKILL_IDS, __version__
from .contracts import (
    STATE_ANALYZED,
    STATE_ASSET_PARTIAL,
    STATE_ASSET_READY,
    STATE_BLOCKED,
    STATE_CANDIDATE,
    STATE_COMPILED,
    STATE_PLANNING,
    STATE_QA,
    STATE_RENDERED,
    ensure_run_tree,
    hash_artifact,
    init_state_machine,
    load_state_machine,
    parse_json_strict,
    persist_kh03_stage_receipt,
    transition_state,
    write_json_atomic,
)
from .director import build_creative_plan, compile_to_kh02_overrides
from .explanation import plan_explanation
from .footage import acquire_and_bind_footage
from .intelligence import analyze_inputs, build_semantic_graph
from .layout import VALID_LAYOUT_POLICIES, resolve_layout_policy
from .qa import evaluate_kh03_editorial
from .recommend import get_skill_contract, issue_recommendation, recommend_skills
from .research import emit_research_summary
from .selection import SelectionGateError, record_user_selection, validate_user_selection
from .sound_director import plan_soundscape_for_skill
from .visual_qa import audit_fullscreen_and_collisions


def run_preflight(*, run_dir: Path | None = None) -> dict[str, Any]:
    """Read-only offline environment, disk floor & license status preflight."""
    disk = shutil.disk_usage(str(run_dir or Path.cwd()))
    free_gib = round(disk.free / (1024 ** 3), 2)
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    node_bin = shutil.which("node")
    standalone = Path(os.environ.get("SEECUT_STANDALONE_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone")))
    seecut_root = Path(os.environ.get("SEECUT_UPSTREAM_ROOT", str(standalone / "third_party/SeeCut")))
    tools_root = standalone / "tools"
    has_upstream = (seecut_root / "skill/seecut/scripts/render.mjs").is_file()
    has_hf = any(tools_root.glob("node_modules/hyperframes/dist/vendor/npm/gsap@*/dist/gsap.min.js"))

    rep = {
        "schema_version": "kh03.preflight.v1",
        "kh03_version": __version__,
        "registry_version": REGISTRY_VERSION,
        "base_qa_sha": BASE_QA_SHA,
        "upstream_seecut_sha": UPSTREAM_SEECUT_SHA,
        "upstream_license": "PolyForm Noncommercial License 1.0.0 (commercial contact: aiesecxuanli@gmail.com)",
        "commercial_release_default": "BLOCKED",
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count() or 1,
        "free_disk_gib": free_gib,
        "disk_floor_ok": free_gib >= DEFAULT_DISK_RESERVE_GIB,
        "tools": {
            "ffmpeg": bool(ffmpeg_bin),
            "ffprobe": bool(ffprobe_bin),
            "node": bool(node_bin),
            "upstream_seecut_render_mjs": has_upstream,
            "pinned_hyperframes_gsap": has_hf,
        },
        "render_capability": "FULL_SEECUT_CORE_READY" if (has_upstream and has_hf and ffmpeg_bin and node_bin) else "PLAN_AND_OFFLINE_QA_ONLY_UPSTREAM_MISSING",
        "pass": bool(ffmpeg_bin and ffprobe_bin and free_gib >= DEFAULT_DISK_RESERVE_GIB),
    }
    if run_dir is not None:
        write_json_atomic(run_dir / "manifests" / "ENV_REPORT.json", rep)
    return rep


_VALID_LICENSE_DECISIONS = frozenset({
    "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
    "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED",
    "BLOCKED_COMMERCIAL_LICENSE",
    "BLOCKED_LICENSE_UNRESOLVED",
})
_VALID_PURPOSES = frozenset({
    "NONCOMMERCIAL_ENGINEERING",
    "NONCOMMERCIAL_PERSONAL",
    "COMMERCIAL_MARKETING",
})


def run_recommend(
    *,
    video_path: Path,
    run_root: Path,
    script_text: str | None = None,
    script_path: Path | None = None,
    transcript_path: Path | None = None,
    video_rights: str = "OWNER_SUPPLIED_NONCOMMERCIAL",
    license_decision: str = "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
    intended_purpose: str = "NONCOMMERCIAL_ENGINEERING",
    commercial_permission_evidence: str | None = None,
    language: str | None = None,
    layout_lock: str | None = None,
    layout_policy: str | None = None,
    cut_policy: str = "LOCK_VOICE",
    caption_policy: str = "OFF",
    music_policy: str = "OFF",
    run_asr: bool = False,
    test_only: bool = False,
) -> dict[str, Any]:
    """Step 1: Read-only source/script analysis + 5-skill recommendation -> STOP at AWAITING_USER_SELECTION."""
    # Validate layout policy / lock conflict BEFORE writing run state (ADL-01 fail-closed!)
    pol_check = resolve_layout_policy(
        constraints={
            "layout_lock": layout_lock,
            "layout_policy": layout_policy,
            "fullscreen_lock": (layout_lock == "FULLSCREEN_9_16"),
        }
    )

    rdir = ensure_run_tree(run_root)
    run_id = rdir.name
    init_state_machine(rdir, run_id)

    eff_purpose = str(intended_purpose or "NONCOMMERCIAL_ENGINEERING").strip().upper()
    if eff_purpose not in _VALID_PURPOSES:
        eff_purpose = "COMMERCIAL_MARKETING"
    eff_lic = str(license_decision or "BLOCKED_LICENSE_UNRESOLVED").strip().upper()
    if eff_lic not in _VALID_LICENSE_DECISIONS:
        eff_lic = "BLOCKED_LICENSE_UNRESOLVED"
    comm_evid = str(commercial_permission_evidence or "").strip() or None
    if eff_purpose == "COMMERCIAL_MARKETING" and (eff_lic != "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED" or not comm_evid or len(comm_evid) < 8):
        eff_lic = "BLOCKED_COMMERCIAL_LICENSE"

    tr_json = parse_json_strict(transcript_path) if (transcript_path and Path(transcript_path).is_file()) else None
    constraints = {
        "layout_lock": layout_lock,
        "layout_policy": pol_check["layout_policy"],
        "fullscreen_lock": bool(pol_check["fullscreen_lock"]),
        "cut_policy": cut_policy,
        "caption_policy": caption_policy,
        "music_policy": music_policy,
        "intended_purpose": eff_purpose,
        "license_decision": eff_lic,
        "commercial_permission_evidence": comm_evid,
    }
    res_in = analyze_inputs(
        video_path=video_path,
        run_id=run_id,
        script_text=script_text,
        script_path=script_path,
        transcript_json=tr_json,
        video_rights=video_rights,
        language=language,
        constraints=constraints,
        run_asr=run_asr,
        run_dir=rdir,
    )
    inp = res_in["input_analysis"]
    sem = build_semantic_graph(inp, transcript=res_in["transcript"], run_dir=rdir)
    if res_in["transcript"] is not None:
        write_json_atomic(rdir / "analysis" / "TRANSCRIPT_NORMALIZED.json", res_in["transcript"])

    transition_state(rdir, STATE_ANALYZED, reason="Completed input intelligence & semantic graph.", run_id=run_id)
    persist_kh03_stage_receipt(
        rdir, "KH03_01_analyze_inputs", run_id=run_id, status="PASS",
        reason=f"Analyzed {inp['media']['aspect_ratio']} video ({inp['media']['nb_frames']}f) & {len(sem['phrases'])} semantic phrases.",
        input_hashes={"video_sha256": inp["video_sha256"], "script_sha256": inp["script_sha256"]},
        output_hashes={"input_analysis_sha256": hash_artifact(inp), "semantic_graph_sha256": hash_artifact(sem)},
        evidence_paths=[str(rdir / "analysis/INPUT_ANALYSIS.json"), str(rdir / "analysis/SEMANTIC_GRAPH.json")],
        test_only=test_only,
    )

    rec = recommend_skills(inp, sem, constraints=constraints, license_decision=eff_lic)
    issued = issue_recommendation(rec, rdir)
    persist_kh03_stage_receipt(
        rdir, "KH03_02_recommend_skills", run_id=run_id, status="AWAITING_USER_SELECTION",
        reason=f"Recommended {rec['recommended_skill_id']}; halted awaiting explicit user choice.",
        input_hashes={"input_analysis_sha256": hash_artifact(inp), "semantic_graph_sha256": hash_artifact(sem)},
        output_hashes={"recommendation_sha256": issued["recommendation_sha256"]},
        evidence_paths=[issued["recommendation_json"], issued["recommendation_md"]],
        test_only=test_only,
    )

    return {
        "exit_code": 0,
        "run_id": run_id,
        "run_root": str(rdir),
        **issued,
        "recommendation": rec,
    }


def run_select(
    *,
    run_root: Path,
    skill_id: str,
    approval_source: str = "EXPLICIT_OWNER_CLI",
    approval_evidence: str = "Explicit user selection via scripts.kh03 select",
    test_only: bool = False,
) -> dict[str, Any]:
    """Step 2: Record explicit, cryptographically bound user skill choice (SK-01..SK-05)."""
    rdir = Path(os.path.realpath(str(run_root.expanduser())))
    try:
        res = record_user_selection(
            run_dir=rdir,
            selected_skill_id=skill_id,
            approval_source=approval_source,
            approval_evidence=approval_evidence,
            test_only=test_only,
        )
    except SelectionGateError as ex:
        return {"exit_code": 2, "status": "SELECTION_REJECTED", "reason": str(ex)}

    persist_kh03_stage_receipt(
        rdir, "KH03_03_user_selection", run_id=rdir.name, status="PASS",
        reason=f"Bound explicit user selection {res['receipt']['selected_skill_id']} (override={res['receipt']['overrode_recommendation']}).",
        input_hashes={"recommendation_sha256": res["receipt"]["recommendation_sha256"]},
        output_hashes={"selection_receipt_sha256": res["receipt_sha256"]},
        evidence_paths=[res["receipt_path"]],
        test_only=test_only,
    )
    return {
        "exit_code": 0,
        "run_id": rdir.name,
        **res,
        "next_command": f'python -m scripts.kh03 execute --run-root "{rdir}" --mode plan',
    }


def run_execute(
    *,
    run_root: Path,
    mode: str = "plan",
    dry_run: bool = False,
    license_decision: str | None = None,
    intended_purpose: str | None = None,
    commercial_permission_evidence: str | None = None,
    cut_policy: str | None = None,
    caption_policy: str | None = None,
    presenter_mode: str | None = None,
    layout_policy: str | None = None,
    sfx_policy: str = "OPTIONAL_LICENSED",
    music_policy: str | None = None,
    sfx_library_dirs: list[str | Path] | None = None,
    sfx_config_path: Path | None = None,
    footage_library_dirs: list[Path] | None = None,
    supplied_assets: list[dict[str, Any]] | None = None,
    workers: int = 2,
    max_disk_gib: float | None = None,
    offline: bool = True,
    online_opt_in: bool = False,
    run_h2: bool = False,
    compare_baselines: list[Path] | None = None,
    export_layers: bool = False,
    production_mode: bool = True,
    allow_test_only_sfx: bool = False,
    seecut_root: Path | None = None,
    tools_root: Path | None = None,
) -> dict[str, Any]:
    """Step 3: Validate mandatory user selection + P0 license gate -> KH-03 intelligence -> KH-02 pipeline."""
    rdir = Path(os.path.realpath(str(run_root.expanduser())))
    run_id = rdir.name
    eff_offline = False if online_opt_in else bool(offline)

    # 1. Fail-closed mandatory user selection gate (fresh disk re-hash of video + raw script + constraints!)
    try:
        inp = parse_json_strict(rdir / "analysis" / "INPUT_ANALYSIS.json")
        sem = parse_json_strict(rdir / "analysis" / "SEMANTIC_GRAPH.json")
        vp = Path(inp["video_path"])
        cur_vid_sha = hash_artifact(vp) if vp.is_file() else "MISSING_VIDEO"
        sel_val = validate_user_selection(
            run_dir=rdir,
            production_mode=production_mode,
            expected_video_sha256=cur_vid_sha,
            expected_script_sha256=inp["script_sha256"],
        )
    except (FileNotFoundError, SelectionGateError, ValueError) as ex:
        return {"exit_code": 2, "status": "BLOCKED_SELECTION_GATE", "run_id": run_id, "reason": str(ex)}

    skill_id = sel_val["selected_skill_id"]
    sel_sha = sel_val["receipt_sha256"]
    saved_c = dict(inp.get("constraints") or {})
    if layout_policy is not None:
        req_lp = str(layout_policy).strip().upper()
        bound_lp = str(saved_c.get("layout_policy") or ("FULLSCREEN_LOCK" if saved_c.get("fullscreen_lock") else "LEGACY_DYNAMIC")).upper()
        if req_lp != bound_lp:
            return {
                "exit_code": 2,
                "status": "BLOCKED_SELECTION_GATE",
                "run_id": run_id,
                "reason": f"LAYOUT_POLICY_DRIFT: requested layout_policy={req_lp!r} differs from selection-bound {bound_lp!r}; re-run recommend and select.",
            }
    saved_lic = str(saved_c.get("license_decision") or "BLOCKED_LICENSE_UNRESOLVED")
    saved_purpose = str(saved_c.get("intended_purpose") or "NONCOMMERCIAL_ENGINEERING").upper()
    saved_evid = str(saved_c.get("commercial_permission_evidence") or "").strip()

    eff_purpose = str(intended_purpose or saved_purpose).strip().upper()
    eff_evid = str(commercial_permission_evidence if commercial_permission_evidence is not None else saved_evid).strip()
    eff_lic = str(license_decision or saved_lic).strip().upper()

    # 2. P0 Fail-Closed PROBLEM-01 License Gate (rejects invalid status, commercial override bypass, or unsubstantiated permission!)
    if eff_lic not in _VALID_LICENSE_DECISIONS or eff_purpose not in _VALID_PURPOSES:
        transition_state(rdir, STATE_BLOCKED, reason=f"Invalid license/purpose: {eff_lic!r}/{eff_purpose!r}", run_id=run_id)
        return {
            "exit_code": 2,
            "status": "BLOCKED_LICENSE_UNRESOLVED",
            "run_id": run_id,
            "selected_skill_id": skill_id,
            "reason": f"Fail-closed: invalid license_decision={eff_lic!r} or intended_purpose={eff_purpose!r}.",
        }
    # Block attempting to downgrade a previously blocked commercial recommendation into NONCOMMERCIAL!
    if saved_lic in ("BLOCKED_COMMERCIAL_LICENSE", "BLOCKED_LICENSE_UNRESOLVED") and eff_lic == "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED":
        transition_state(rdir, STATE_BLOCKED, reason="Blocked override from BLOCKED_COMMERCIAL_LICENSE to NONCOMMERCIAL.", run_id=run_id)
        return {
            "exit_code": 2,
            "status": "BLOCKED_COMMERCIAL_LICENSE",
            "run_id": run_id,
            "selected_skill_id": skill_id,
            "reason": "Cannot override a BLOCKED_COMMERCIAL_LICENSE recommendation with NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED.",
        }
    if eff_purpose == "COMMERCIAL_MARKETING" and (eff_lic != "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED" or len(eff_evid) < 8):
        transition_state(rdir, STATE_BLOCKED, reason="COMMERCIAL_MARKETING lacks verified written permission evidence.", run_id=run_id)
        return {
            "exit_code": 2,
            "status": "BLOCKED_COMMERCIAL_LICENSE",
            "run_id": run_id,
            "selected_skill_id": skill_id,
            "reason": "COMMERCIAL_MARKETING requires WRITTEN_COMMERCIAL_PERMISSION_VERIFIED with substantiated commercial_permission_evidence.",
        }
    if eff_lic == "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED" and len(eff_evid) < 8:
        transition_state(rdir, STATE_BLOCKED, reason="Unsubstantiated WRITTEN_COMMERCIAL_PERMISSION_VERIFIED.", run_id=run_id)
        return {
            "exit_code": 2,
            "status": "BLOCKED_COMMERCIAL_LICENSE",
            "run_id": run_id,
            "selected_skill_id": skill_id,
            "reason": "Unsubstantiated WRITTEN_COMMERCIAL_PERMISSION_VERIFIED rejected: provide commercial_permission_evidence.",
        }
    if eff_lic in ("BLOCKED_COMMERCIAL_LICENSE", "BLOCKED_LICENSE_UNRESOLVED"):
        transition_state(rdir, STATE_BLOCKED, reason=f"PROBLEM-01 license gate: {eff_lic}", run_id=run_id)
        return {
            "exit_code": 2,
            "status": eff_lic,
            "run_id": run_id,
            "selected_skill_id": skill_id,
            "reason": (
                f"PROBLEM-01 fail-closed block ({eff_lic}): YeJe-cpu/SeeCut is PolyForm Noncommercial 1.0.0. "
                "Written commercial permission from aiesecxuanli@gmail.com is required before commercial execution."
            ),
        }

    # Check disk floor before materializing assets/renders
    pf = run_preflight(run_dir=rdir)
    if not pf["disk_floor_ok"]:
        transition_state(rdir, STATE_BLOCKED, reason=f"Insufficient disk space: {pf['free_disk_gib']} GiB", run_id=run_id)
        return {"exit_code": 2, "status": "BLOCKED_LOW_DISK", "run_id": run_id, "free_disk_gib": pf["free_disk_gib"]}

    transition_state(rdir, STATE_PLANNING, reason=f"Starting KH-03 planning for selected skill {skill_id}.", run_id=run_id)
    asset_purpose = (
        "COMMERCIAL_AND_NONCOMMERCIAL" if eff_lic == "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED"
        else ("TEST_ONLY" if not production_mode else "NONCOMMERCIAL_ONLY")
    )

    # 3. Research context, Footage Acquisition & Deep Explanation Engine
    emit_research_summary(rdir, skill_id)
    footage_manifest = acquire_and_bind_footage(
        run_id=run_id,
        run_dir=rdir,
        semantic_graph=sem,
        skill_id=skill_id,
        constraints=saved_c,
        local_libraries=footage_library_dirs,
        supplied_assets=supplied_assets,
        intended_purpose=asset_purpose,
        workers=workers,
        offline=eff_offline,
    )
    transition_state(
        rdir,
        STATE_ASSET_READY if footage_manifest["status"] == "ASSET_READY" else STATE_ASSET_PARTIAL,
        reason=f"Footage status={footage_manifest['status']} ({len(footage_manifest['assets'])} verified asset(s)).",
        run_id=run_id,
    )

    explanation_graph = plan_explanation(sem, inp, skill_id=skill_id, run_dir=rdir)
    reg = build_registry()

    # 4. Adaptive Creative Plan + Layout Choreography + Component Collision Resolution
    creative_plan = build_creative_plan(
        run_id=run_id,
        run_dir=rdir,
        selected_skill_id=skill_id,
        selection_receipt_sha256=sel_sha,
        input_analysis=inp,
        semantic_graph=sem,
        explanation_graph=explanation_graph,
        footage_manifest=footage_manifest,
        registry=reg,
        constraints=saved_c,
    )
    lp_file = rdir / "layouts" / "LAYOUT_PLAN.json"
    layout_plan = parse_json_strict(lp_file) if lp_file.is_file() else None

    fps = inp["media"]["fps_num"] / float(max(1, inp["media"]["fps_den"]))
    eff_cut = cut_policy or saved_c.get("cut_policy") or "LOCK_VOICE"
    eff_caption = caption_policy or saved_c.get("caption_policy") or "OFF"
    eff_music = music_policy or saved_c.get("music_policy") or "OFF"

    # 5. Multi-Layer Sound Director (anchored to spatial layout transitions & visual reveals!)
    snd_res = plan_soundscape_for_skill(
        run_id=run_id,
        run_dir=rdir,
        creative_plan=creative_plan,
        layout_plan=layout_plan,
        fps=fps,
        voice_policy=eff_cut,
        sfx_policy=sfx_policy,
        bgm_policy=eff_music,
        sfx_library_dirs=sfx_library_dirs,
        sfx_config_path=sfx_config_path,
        intended_purpose=asset_purpose,
        offline=eff_offline,
        workers=workers,
        production_mode=production_mode,
        allow_test_only_sfx=allow_test_only_sfx,
    )

    # 6. Build KH-02 Brief & Invoke Normal KH-02 Pipeline (passing authoritative layout_plan + hook + sound_plan!)
    sk = get_skill_contract(skill_id)
    kh03_ov = compile_to_kh02_overrides(
        creative_plan, fps=fps, sound_plan=snd_res["sound_plan"], layout_plan=layout_plan,
    )

    sources_list = [{
        "asset_id": "primary_aroll",
        "path": inp["video_path"],
        "kind": "a_roll",
        "rights": inp["video_rights"] if inp["video_rights"] in (
            "OWNER_SUPPLIED_NONCOMMERCIAL", "LICENSED_COMMERCIAL", "CAPTURED_REAL_EVIDENCE_MASKED",
        ) else "OWNER_SUPPLIED_NONCOMMERCIAL",
        "label": "Primary source A-roll",
    }]
    for fa in footage_manifest["assets"]:
        sources_list.append({
            "asset_id": fa["asset_id"],
            "path": fa["local_path"],
            "kind": "still_asset" if fa["media_type"] in ("image", "screen_capture", "diagram_asset") else "b_roll",
            "rights": "LICENSED_COMMERCIAL" if asset_purpose == "COMMERCIAL_AND_NONCOMMERCIAL" else "OWNER_SUPPLIED_NONCOMMERCIAL",
            "label": fa["source_label"],
        })

    # R1.02 / R2 ADL-01: Explicit FULLSCREEN_9_16 lock OR ADAPTIVE_DYNAMIC propagated cleanly to camera.py & SHOTBOOK_V2
    fs_lock = bool(saved_c.get("fullscreen_lock"))
    eff_layout_pol = str(saved_c.get("layout_policy") or ("FULLSCREEN_LOCK" if fs_lock else "LEGACY_DYNAMIC"))
    pres_pol: dict[str, Any]
    if fs_lock:
        pres_pol = {
            "mode": "LEGACY_DYNAMIC",
            "visibility": "ALWAYS_VISIBLE",
            "allow_hide": False,
            "allow_fullscreen": True,
            "fullscreen_lock": True,
            "layout_policy": "FULLSCREEN_LOCK",
        }
    elif eff_layout_pol == "ADAPTIVE_DYNAMIC":
        pres_pol = {
            "mode": "LEGACY_DYNAMIC",
            "visibility": "ALWAYS_VISIBLE",
            "allow_hide": False,
            "allow_fullscreen": True,
            "fullscreen_lock": False,
            "layout_policy": "ADAPTIVE_DYNAMIC",
        }
    elif presenter_mode:
        pres_pol = {"mode": presenter_mode}
    else:
        pres_pol = {"mode": "LEGACY_DYNAMIC"}

    kh02_brief = {
        "schema_version": "kh02.brief.v2",
        "brief_id": f"kh03_{skill_id.lower()}_{run_id[:24]}",
        "title": f"KH-03 {skill_id}: {sk['name']}",
        "mode": "talking_head",
        "profile": kh03_ov["profile"],
        "objective": f"Execute user-selected {skill_id} ({sk['name']}) grounded in semantic & explanation graph.",
        "audience": inp["audience_profile"],
        "platform": "reels" if inp["media"]["aspect_ratio"] == "9:16" else "youtube",
        "aspect_ratio": inp["media"]["aspect_ratio"],
        "language": inp["language"],
        "tone": sk["preferred_energy"],
        "key_message": (inp.get("normalized_script") or "Visual showcase")[:140],
        "voice_policy": eff_cut,
        "cut_policy": eff_cut,
        "energy_mode": kh03_ov["energy_mode"],
        "caption_policy": eff_caption,
        "music_policy": eff_music,
        "sfx_policy": sfx_policy,
        "intended_purpose": eff_purpose,
        "commercial_permission_evidence": eff_evid or None,
        "license_decision": eff_lic,
        "creative_theme": creative_plan["art_direction"]["theme"],
        "presenter_policy": pres_pol,
        "sources": sources_list,
    }
    brief_path = write_json_atomic(rdir / "manifests" / "KH02_DERIVED_BRIEF.json", kh02_brief)

    tr_file = rdir / "analysis" / "TRANSCRIPT_NORMALIZED.json"
    kh02_res = execute_kh02_pipeline(
        brief_path=brief_path,
        run_root=rdir,
        mode=mode,
        dry_run=dry_run,
        workers=workers,
        max_disk_gib=max_disk_gib,
        sfx_library_dirs=sfx_library_dirs,
        sfx_config_path=sfx_config_path,
        offline=eff_offline,
        transcript_json_path=tr_file if tr_file.is_file() else None,
        seecut_root=seecut_root,
        tools_root=tools_root,
        run_h2=run_h2,
        compare_baselines=compare_baselines,
        export_layers=export_layers,
        production_mode=production_mode,
        allow_test_only_sfx=allow_test_only_sfx,
        kh03_selection_receipt=sel_val["receipt"],
        kh03_overrides=kh03_ov,
    )

    if kh02_res.get("exit_code", 1) != 0:
        transition_state(rdir, STATE_BLOCKED, reason=f"KH-02 pipeline returned {kh02_res.get('status')}", run_id=run_id)
        return {
            "exit_code": int(kh02_res.get("exit_code", 1)),
            "status": str(kh02_res.get("status", "KH02_PIPELINE_FAIL")),
            "run_id": run_id,
            "selected_skill_id": skill_id,
            "kh02_result": kh02_res,
        }

    sb_path = rdir / "SHOTBOOK_V2.json"
    shotbook = parse_json_strict(sb_path) if sb_path.is_file() else None
    final_mp4_raw = kh02_res.get("final_mp4_path")
    final_mp4_exists = bool(final_mp4_raw and Path(final_mp4_raw).is_file())
    final_mp4_sha = hash_artifact(Path(final_mp4_raw)) if final_mp4_exists else None

    transition_state(rdir, STATE_COMPILED, reason="KH-03 plan compiled cleanly through KH-02 pipeline.", run_id=run_id)
    if mode in ("preview", "full") and final_mp4_exists:
        transition_state(rdir, STATE_RENDERED, reason=f"Rendered {final_mp4_raw}", run_id=run_id)

    # 7. KH-03 Visual & Editorial QA (strict shotbook requirement; separates PLAN vs RENDER!)
    vis_qa = audit_fullscreen_and_collisions(
        creative_plan, inp, shotbook=shotbook, require_shotbook=(not dry_run), run_dir=(None if dry_run else rdir),
    )
    transition_state(rdir, STATE_QA, reason="Executing KH-03 editorial & comprehension QA.", run_id=run_id)
    # Reload post-EDL remapped layout_plan from disk if written during Stage E/F
    eff_layout_plan = parse_json_strict(lp_file) if lp_file.is_file() else layout_plan
    qa_rep = evaluate_kh03_editorial(
        run_id=run_id,
        creative_plan=creative_plan,
        explanation_graph=explanation_graph,
        footage_manifest=footage_manifest,
        sound_plan=snd_res["sound_plan"],
        visual_qa=vis_qa,
        shotbook=shotbook,
        layout_plan=eff_layout_plan,
        license_decision=eff_lic,
        evidence_paths=[
            str(rdir / "manifests/CREATIVE_PLAN.json"),
            str(rdir / "analysis/EXPLANATION_GRAPH.json"),
            str(rdir / "assets/FOOTAGE_MANIFEST.json"),
            str(rdir / "stems/SOUND_DIRECTOR_PLAN.json"),
        ] + ([str(sb_path)] if sb_path.is_file() else []),
        run_dir=(None if dry_run else rdir),
    )

    # R1.07: Mode-truthful status — plan mode NEVER claims RENDER_CANDIDATE_VERIFIED or OWNER_PENDING final video!
    if mode == "plan":
        if qa_rep["editorial_candidate_pass"]:
            final_status = "KH03_PLAN_PASS"
            exit_c = 0
        else:
            final_status = "KH03_QA_DEFECTS"
            exit_c = 1
    elif mode in ("preview", "full"):
        if not final_mp4_exists:
            final_status = "KH03_RENDER_INCOMPLETE"
            exit_c = 1
        elif qa_rep["editorial_candidate_pass"]:
            final_status = "KH03_RENDER_CANDIDATE_VERIFIED" if mode == "full" else "KH03_PREVIEW_VERIFIED"
            exit_c = 0
            transition_state(rdir, STATE_CANDIDATE, reason=f"Rendered & verified ({final_status}); owner approvals PENDING.", run_id=run_id)
        else:
            final_status = "KH03_QA_DEFECTS"
            exit_c = 1
    else:
        final_status = "KH03_PLAN_PASS" if qa_rep["editorial_candidate_pass"] else "KH03_QA_DEFECTS"
        exit_c = 0 if qa_rep["editorial_candidate_pass"] else 1

    persist_kh03_stage_receipt(
        rdir, "KH03_04_execute_and_qa", run_id=run_id,
        status="PASS" if exit_c == 0 else "FAIL",
        reason=f"mode={mode}, status={final_status}, skill={skill_id}, score={qa_rep['total_score']}/100, final_mp4={bool(final_mp4_exists)}.",
        input_hashes={"selection_receipt_sha256": sel_sha, "kh03_plan_sha256": creative_plan["plan_sha256"]},
        output_hashes={
            "qa_report_sha256": hash_artifact(qa_rep),
            "shotbook_sha256": kh02_res.get("shotbook_sha256"),
            "final_mp4_sha256": final_mp4_sha,
        },
        evidence_paths=[str(rdir / "qc/KH03_QA_REPORT.json")] + ([str(final_mp4_raw)] if final_mp4_exists else []),
        test_only=(not production_mode),
    )

    return {
        "exit_code": exit_c,
        "status": final_status,
        "mode": mode,
        "run_id": run_id,
        "selected_skill_id": skill_id,
        "selection_receipt_sha256": sel_sha,
        "creative_plan_sha256": creative_plan["plan_sha256"],
        "shotbook_sha256": kh02_res.get("shotbook_sha256"),
        "final_mp4_path": final_mp4_raw if final_mp4_exists else None,
        "final_mp4_sha256": final_mp4_sha,
        "qa_report": qa_rep,
        "kh02_status": kh02_res.get("status"),
    }


def run_status(*, run_root: Path) -> dict[str, Any]:
    rdir = Path(os.path.realpath(str(run_root.expanduser())))
    sm = load_state_machine(rdir)
    sel_p = rdir / "selection" / "SELECTION_RECEIPT.json"
    rec_p = rdir / "selection" / "SKILL_RECOMMENDATION.json"
    return {
        "exit_code": 0,
        "run_id": sm["run_id"],
        "state": sm["state"],
        "recommended_skill_id": parse_json_strict(rec_p)["recommended_skill_id"] if rec_p.is_file() else None,
        "selected_skill_id": parse_json_strict(sel_p)["selected_skill_id"] if sel_p.is_file() else None,
        "history_len": len(sm["history"]),
    }


def run_verify(*, run_root: Path, production_mode: bool = True, require_render: bool = False) -> dict[str, Any]:
    """Fail-closed verifier requiring full KH-03 + KH-02 artifact presence and SHA linkage (R1.07)."""
    rdir = Path(os.path.realpath(str(run_root.expanduser())))
    issues: list[str] = []
    try:
        sel = validate_user_selection(run_dir=rdir, production_mode=production_mode)
    except Exception as ex:
        issues.append(str(ex))
        sel = None

    required_files = [
        rdir / "analysis" / "INPUT_ANALYSIS.json",
        rdir / "selection" / "SKILL_RECOMMENDATION.json",
        rdir / "selection" / "SELECTION_RECEIPT.json",
        rdir / "manifests" / "CREATIVE_PLAN.json",
        rdir / "SHOTBOOK_V2.json",
        rdir / "qc" / "KH03_QA_REPORT.json",
        rdir / "STAGE_RECEIPTS" / "F_canonical_shotbook.json",
    ]
    for rf in required_files:
        if not rf.is_file():
            issues.append(f"MISSING_REQUIRED_ARTIFACT: {rf.name}")

    cp = rdir / "manifests" / "CREATIVE_PLAN.json"
    sb = rdir / "SHOTBOOK_V2.json"
    if cp.is_file() and sb.is_file():
        plan = parse_json_strict(cp)
        shot = parse_json_strict(sb)
        if sel and plan["selected_skill_id"] != sel["selected_skill_id"]:
            issues.append("Skill mismatch between SELECTION_RECEIPT and CREATIVE_PLAN.")
        prov = shot.get("provenance") or {}
        prov_skill = prov.get("kh03_selected_skill_id")
        if sel and prov_skill != sel["selected_skill_id"]:
            issues.append(f"Skill propagation missing in SHOTBOOK_V2 provenance ({prov_skill!r} != {sel['selected_skill_id']!r}).")
        if prov.get("kh03_plan_sha256") != plan.get("plan_sha256"):
            issues.append("CREATIVE_PLAN SHA mismatch in SHOTBOOK_V2 provenance.")
        if prov.get("kh03_clock_domain") != "OUTPUT_EDL_MAPPED":
            issues.append(f"SHOTBOOK_V2 missing OUTPUT_EDL_MAPPED clock provenance ({prov.get('kh03_clock_domain')!r}).")
        lp_pol = (plan.get("layout_constraints") or {}).get("layout_policy")
        if lp_pol == "ADAPTIVE_DYNAMIC":
            lp_f = rdir / "layouts" / "LAYOUT_PLAN.json"
            ar_f = rdir / "qc" / "ANTI_REPETITION_REPORT.json"
            if not lp_f.is_file():
                issues.append("MISSING_REQUIRED_ARTIFACT: LAYOUT_PLAN.json")
            if not ar_f.is_file():
                issues.append("MISSING_REQUIRED_ARTIFACT: ANTI_REPETITION_REPORT.json")
            if prov.get("kh03_layout_policy") != "ADAPTIVE_DYNAMIC":
                issues.append(f"SHOTBOOK_V2 missing kh03_layout_policy=ADAPTIVE_DYNAMIC ({prov.get('kh03_layout_policy')!r}).")

    rec4 = rdir / "STAGE_RECEIPTS" / "KH03_04_execute_and_qa.json"
    if require_render:
        if not rec4.is_file():
            issues.append("MISSING_RENDER_RECEIPT: KH03_04_execute_and_qa.json absent.")
        else:
            r4 = parse_json_strict(rec4)
            mp4_sha = (r4.get("output_hashes") or {}).get("final_mp4_sha256")
            mp4_paths = [Path(p) for p in (r4.get("evidence_paths") or []) if str(p).lower().endswith(".mp4")]
            if not mp4_sha or not mp4_paths or not mp4_paths[0].is_file():
                issues.append("MISSING_FINAL_MP4: require_render=True but final rendered MP4 missing.")
            elif hash_artifact(mp4_paths[0]) != mp4_sha:
                issues.append("TAMPERED_FINAL_MP4: final MP4 SHA on disk does not match stage receipt.")

    return {
        "exit_code": 0 if not issues else 1,
        "status": "VERIFY_PASS" if not issues else "VERIFY_FAIL",
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.kh03", description="KH-03 Universal Editing Intelligence CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_pre = sub.add_parser("preflight", help="Run read-only offline environment & license preflight")
    p_pre.add_argument("--run-root", type=Path, default=None)

    p_rec = sub.add_parser("recommend", help="Analyze video + script and issue 5-skill menu (STOPS for user selection)")
    p_rec.add_argument("--video", required=True, type=Path)
    p_rec.add_argument("--run-root", required=True, type=Path)
    p_rec.add_argument("--script", type=Path, default=None)
    p_rec.add_argument("--script-text", default=None)
    p_rec.add_argument("--transcript", type=Path, default=None)
    p_rec.add_argument("--language", default=None)
    p_rec.add_argument("--layout-lock", default=None)
    p_rec.add_argument("--layout-policy", choices=list(VALID_LAYOUT_POLICIES), default=None)
    p_rec.add_argument("--cut-policy", default="LOCK_VOICE")
    p_rec.add_argument("--caption-policy", choices=["OFF", "ON_REQUESTED", "KEYWORDS_ONLY"], default="OFF")
    p_rec.add_argument("--music-policy", choices=["OFF", "OPTIONAL_LICENSED", "REQUIRED_LICENSED"], default="OFF")
    p_rec.add_argument("--purpose", choices=sorted(_VALID_PURPOSES), default="NONCOMMERCIAL_ENGINEERING")
    p_rec.add_argument("--license-decision", default="NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED")
    p_rec.add_argument("--commercial-permission-evidence", default=None)
    p_rec.add_argument("--run-asr", action="store_true")

    p_sel = sub.add_parser("select", help="Record explicit user selection of ONE universal skill (SK-01..SK-05)")
    p_sel.add_argument("--run-root", required=True, type=Path)
    p_sel.add_argument("--skill", required=True, choices=list(VALID_SKILL_IDS))
    p_sel.add_argument("--approval-evidence", default="Explicit owner CLI command: scripts.kh03 select")

    p_exe = sub.add_parser("execute", help="Execute full KH-03 -> KH-02 pipeline AFTER validated user selection")
    p_exe.add_argument("--run-root", required=True, type=Path)
    p_exe.add_argument("--mode", choices=["plan", "preview", "full", "verify"], default="plan")
    p_exe.add_argument("--dry-run", action="store_true")
    p_exe.add_argument("--license-decision", default=None)
    p_exe.add_argument("--purpose", choices=sorted(_VALID_PURPOSES), default=None)
    p_exe.add_argument("--commercial-permission-evidence", default=None)
    p_exe.add_argument("--cut-policy", default=None)
    p_exe.add_argument("--caption-policy", choices=["OFF", "ON_REQUESTED", "KEYWORDS_ONLY"], default=None)
    p_exe.add_argument("--presenter-mode", default=None)
    p_exe.add_argument("--layout-policy", choices=list(VALID_LAYOUT_POLICIES), default=None)
    p_exe.add_argument("--sfx-policy", default="OPTIONAL_LICENSED")
    p_exe.add_argument("--music-policy", choices=["OFF", "OPTIONAL_LICENSED", "REQUIRED_LICENSED"], default=None)
    p_exe.add_argument("--sfx-library", action="append", default=None)
    p_exe.add_argument("--sfx-config", type=Path, default=None)
    p_exe.add_argument("--footage-library", action="append", type=Path, default=None)
    p_exe.add_argument("--workers", type=int, default=2)
    p_exe.add_argument("--max-disk", type=float, default=None)
    p_exe.add_argument("--online-opt-in", action="store_true", default=False)
    p_exe.add_argument("--run-h2", action="store_true", default=False)
    p_exe.add_argument("--compare-baseline", action="append", type=Path, default=None)
    p_exe.add_argument("--export-layers", action="store_true", default=False)

    p_st = sub.add_parser("status", help="Inspect KH-03 state machine and binding receipt")
    p_st.add_argument("--run-root", required=True, type=Path)

    p_vf = sub.add_parser("verify", help="Verify selection receipt and KH-03 -> KH-02 SHA propagation")
    p_vf.add_argument("--run-root", required=True, type=Path)
    p_vf.add_argument("--require-render", action="store_true", default=False)

    args = ap.parse_args(argv)
    if args.cmd == "preflight":
        out = run_preflight(run_dir=args.run_root)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out["pass"] else 1
    if args.cmd == "recommend":
        out = run_recommend(
            video_path=args.video,
            run_root=args.run_root,
            script_text=args.script_text,
            script_path=args.script,
            transcript_path=args.transcript,
            language=args.language,
            layout_lock=args.layout_lock,
            layout_policy=args.layout_policy,
            cut_policy=args.cut_policy,
            caption_policy=args.caption_policy,
            music_policy=args.music_policy,
            intended_purpose=args.purpose,
            license_decision=args.license_decision,
            commercial_permission_evidence=args.commercial_permission_evidence,
            run_asr=args.run_asr,
        )
        print(out["menu_text"])
        return int(out["exit_code"])
    if args.cmd == "select":
        out = run_select(
            run_root=args.run_root,
            skill_id=args.skill,
            approval_source="EXPLICIT_OWNER_CLI",
            approval_evidence=args.approval_evidence,
            test_only=False,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return int(out["exit_code"])
    if args.cmd == "execute":
        out = run_execute(
            run_root=args.run_root,
            mode=args.mode,
            dry_run=args.dry_run,
            license_decision=args.license_decision,
            intended_purpose=args.purpose,
            commercial_permission_evidence=args.commercial_permission_evidence,
            cut_policy=args.cut_policy,
            caption_policy=args.caption_policy,
            presenter_mode=args.presenter_mode,
            layout_policy=args.layout_policy,
            sfx_policy=args.sfx_policy,
            music_policy=args.music_policy,
            sfx_library_dirs=args.sfx_library,
            sfx_config_path=args.sfx_config,
            footage_library_dirs=args.footage_library,
            workers=args.workers,
            max_disk_gib=args.max_disk,
            offline=(not args.online_opt_in),
            online_opt_in=args.online_opt_in,
            run_h2=args.run_h2,
            compare_baselines=args.compare_baseline,
            export_layers=args.export_layers,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return int(out["exit_code"])
    if args.cmd == "status":
        out = run_status(run_root=args.run_root)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return int(out["exit_code"])
    out = run_verify(run_root=args.run_root, require_render=args.require_render)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return int(out["exit_code"])
