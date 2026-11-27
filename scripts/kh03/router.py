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
    verify_commercial_permission_evidence,
    write_json_atomic,
)
from .candidates import (
    adopt_candidate,
    bind_winner_to_execution,
    build_live_pairwise_judge,
    build_native_candidate_preview_runner,
    generate_creative_candidates,
    rank_candidates,
    validate_candidate_diversity,
)
from .director import build_creative_plan, compile_to_kh02_overrides
from .execution import BoundedAsyncExecutor, DAGTask, TaskContext, compute_resource_budget
from .explanation import plan_explanation
from .footage import acquire_and_bind_footage
from .intelligence import analyze_inputs, build_editorial_context, build_semantic_graph
from .layout import VALID_LAYOUT_POLICIES, resolve_layout_policy
from .qa import aggregate_gate_results, evaluate_kh03_editorial, orchestrate_final_render_qa, resolve_gate_profile
from .recommend import get_skill_contract, issue_recommendation, recommend_skills
from .renderer_routing import (
    RENDERER_HYBRID_REMOTION,
    RENDERER_INDEPENDENT_CLEAN_ROOM,
    RENDERER_NATIVE_SEECUT,
    VALID_RENDERER_MODES,
    resolve_editorial_lifecycle,
    select_renderer_backend,
)
from .research import emit_research_summary
from .selection import SelectionGateError, record_user_selection, validate_user_selection
from .sound_director import plan_soundscape_for_skill
from .visual_qa import audit_fullscreen_and_collisions


def run_preflight(
    *,
    run_dir: Path | None = None,
    mode: str = "plan",
    intended_purpose: str = "NONCOMMERCIAL_ENGINEERING",
    commercial_permission_evidence: str | None = None,
    compete_candidates: bool = False,
    online_opt_in: bool = False,
    export_layers: bool = False,
    renderer: str = "auto",
    seecut_root: Path | None = None,
    tools_root: Path | None = None,
) -> dict[str, Any]:
    """Mode-aware render readiness preflight (R4.2-06): separates PLAN_READY, NATIVE_PREVIEW_READY,
    NATIVE_FULL_RENDER_READY, and RELEASE_READY; lists ALL blockers and next actions in parallel!
    """
    import subprocess
    from scripts.kh02.render_guard import resolve_local_chromium
    from .tracking import resolve_detector_capabilities

    req_mode = str(mode or "plan").strip().lower()
    if req_mode not in ("plan", "preview", "full", "release"):
        req_mode = "plan"

    chk_disk = Path(run_dir or Path.cwd()).expanduser()
    while not chk_disk.exists() and chk_disk != chk_disk.parent:
        chk_disk = chk_disk.parent
    disk = shutil.disk_usage(str(chk_disk))
    free_gib = round(disk.free / (1024 ** 3), 2)
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    node_bin = shutil.which("node")
    agy_bin = (
        os.environ.get("AGY_BIN")
        or shutil.which("agy")
        or (str(Path.home() / ".gemini/bin/agy") if (Path.home() / ".gemini/bin/agy").is_file() else None)
    )
    chrome_bin = resolve_local_chromium()

    standalone = Path(os.environ.get("SEECUT_STANDALONE_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone")))
    up_root = seecut_root or Path(os.environ.get("SEECUT_UPSTREAM_ROOT", str(standalone / "third_party/SeeCut")))
    troot = tools_root or (standalone / "tools")

    render_mjs = up_root / "skill/seecut/scripts/render.mjs"
    geom_mjs = up_root / "skill/seecut/scripts/geom-check.mjs"
    layers_mjs = up_root / "skill/seecut/scripts/jianying_layers.mjs"
    plan_py = up_root / "skill/seecut/scripts/jianying_plan.py"
    has_upstream_render = render_mjs.is_file()
    has_h1 = geom_mjs.is_file()
    has_layers = layers_mjs.is_file() and plan_py.is_file()

    up_head = None
    up_pin_match = False
    if (up_root / ".git").exists():
        try:
            up_head = subprocess.check_output(["git", "-C", str(up_root), "rev-parse", "HEAD"], text=True, timeout=10).strip()
            up_pin_match = (up_head == UPSTREAM_SEECUT_SHA)
        except Exception:
            pass

    hf_pkg = troot / "node_modules/hyperframes/package.json"
    pw_pkg = troot / "node_modules/playwright-core/package.json"
    hf_ver = json.loads(hf_pkg.read_text(encoding="utf-8")).get("version") if hf_pkg.is_file() else None
    pw_ver = json.loads(pw_pkg.read_text(encoding="utf-8")).get("version") if pw_pkg.is_file() else None
    gsaps = sorted(troot.glob("node_modules/hyperframes/dist/vendor/npm/gsap@*/dist/gsap.min.js"))
    has_hf = bool(gsaps)
    gsap_sha = hash_artifact(gsaps[-1]) if gsaps else None

    is_comm = str(intended_purpose or "").strip().upper() == "COMMERCIAL_MARKETING"
    perm_check = verify_commercial_permission_evidence(commercial_permission_evidence) if is_comm else {"verified": True, "status": "NONCOMMERCIAL_PERMITTED"}
    license_ok = (not is_comm) or bool(perm_check.get("verified"))
    det_caps = resolve_detector_capabilities()

    blockers: list[dict[str, str]] = []
    next_actions: list[str] = []

    if not ffmpeg_bin or not ffprobe_bin:
        blockers.append({"code": "MISSING_FFMPEG", "scope": "PLAN", "detail": "ffmpeg/ffprobe not found on PATH"})
        next_actions.append("Install ffmpeg and ffprobe and add them to PATH.")
    if free_gib < DEFAULT_DISK_RESERVE_GIB:
        blockers.append({"code": "LOW_DISK_RESERVE", "scope": "PLAN", "detail": f"free_disk_gib={free_gib} < {DEFAULT_DISK_RESERVE_GIB}"})
        next_actions.append(f"Free at least {DEFAULT_DISK_RESERVE_GIB} GiB disk space.")
    if not node_bin:
        blockers.append({"code": "MISSING_NODE", "scope": "NATIVE_PREVIEW", "detail": "Node.js >= 22 not found on PATH"})
        next_actions.append("Install workspace-local Node.js >= 22.")
    if not has_upstream_render or not up_pin_match:
        blockers.append({"code": "MISSING_OR_DRIFTED_UPSTREAM_SEECUT", "scope": "NATIVE_PREVIEW", "detail": f"Expected {UPSTREAM_SEECUT_SHA}, observed={up_head}, render.mjs={has_upstream_render}"})
        next_actions.append(f"Checkout unmodified YeJe-cpu/SeeCut @ {UPSTREAM_SEECUT_SHA} into {up_root}.")
    if not has_hf or not pw_ver or chrome_bin is None:
        blockers.append({"code": "MISSING_NATIVE_RENDER_DEPS", "scope": "NATIVE_PREVIEW", "detail": f"hyperframes={hf_ver}, playwright-core={pw_ver}, gsap={has_hf}, chromium={bool(chrome_bin)}"})
        next_actions.append("Install pinned hyperframes@0.9.86 + playwright-core@1.65.1 + local Chromium in tools root.")
    if not has_h1:
        blockers.append({"code": "MISSING_H1_GEOM_CHECK", "scope": "NATIVE_PREVIEW", "detail": f"Missing {geom_mjs}"})
    if export_layers and not has_layers:
        blockers.append({"code": "MISSING_JIANYING_EXPORTERS", "scope": "NATIVE_FULL", "detail": "Missing jianying_layers.mjs or jianying_plan.py"})
    if not license_ok:
        blockers.append({"code": "BLOCKED_COMMERCIAL_LICENSE", "scope": "COMMERCIAL", "detail": str(perm_check.get("reason") or "Commercial permission required from aiesecxuanli@gmail.com")})
        next_actions.append("Obtain verified written commercial authorization from YeJe-cpu (aiesecxuanli@gmail.com) and supply structured JSON receipt.")
    if (compete_candidates or req_mode in ("full", "release")) and online_opt_in and not agy_bin:
        blockers.append({"code": "MISSING_AGY_LIVE_JUDGE", "scope": "EDITORIAL_H2_PAIRWISE", "detail": "agy CLI binary not found for live H2/pairwise judging"})
        next_actions.append("Install and authenticate non-bypass agy CLI, or run owner visual review for pairwise selection.")

    plan_ready = bool(ffmpeg_bin and ffprobe_bin and free_gib >= DEFAULT_DISK_RESERVE_GIB)
    native_preview_ready = bool(plan_ready and node_bin and has_upstream_render and up_pin_match and has_hf and pw_ver and chrome_bin and has_h1 and license_ok)
    native_full_ready = bool(native_preview_ready and (has_layers or not export_layers))
    release_ready = False  # Always False at preflight: requires post-render QA + explicit OWNER_VISUAL & OWNER_AUDIO approval on final SHA!

    routing = select_renderer_backend(
        requested=renderer,
        intended_purpose=intended_purpose,
        commercial_permission_evidence=commercial_permission_evidence,
        seecut_root=up_root,
        tools_root=troot,
    )
    indep_ready = bool(plan_ready and routing["per_renderer_rights"][RENDERER_INDEPENDENT_CLEAN_ROOM]["authorized_for_purpose"])

    from .effect_registry import get_default_effect_registry
    from .remotion_adapter import probe_remotion_runtime

    rem_probe = probe_remotion_runtime()
    eff_reg_snap = get_default_effect_registry().snapshot()
    hybrid_remotion_ready = bool(indep_ready and rem_probe.get("ready"))

    mode_ready_map = {
        "plan": plan_ready,
        "preview": (
            native_preview_ready if renderer == "native_seecut"
            else (hybrid_remotion_ready if renderer == "hybrid_remotion"
                  else (indep_ready if renderer == "independent_clean_room" else (native_preview_ready or (indep_ready and is_comm and False))))
        ),
        "full": (
            native_full_ready if renderer == "native_seecut"
            else (hybrid_remotion_ready if renderer == "hybrid_remotion"
                  else (indep_ready if renderer == "independent_clean_room" else native_full_ready))
        ),
        "release": release_ready,
    }
    requested_ready = mode_ready_map[req_mode]

    rep = {
        "schema_version": "kh03.preflight.v2",
        "kh03_version": __version__,
        "registry_version": REGISTRY_VERSION,
        "base_qa_sha": BASE_QA_SHA,
        "upstream_seecut_sha": UPSTREAM_SEECUT_SHA,
        "upstream_observed_sha": up_head,
        "upstream_pin_match": up_pin_match,
        "upstream_license": "PolyForm Noncommercial License 1.0.0 (commercial contact: aiesecxuanli@gmail.com)",
        "intended_purpose": intended_purpose,
        "commercial_license_ok": license_ok,
        "commercial_release_default": "BLOCKED",
        "requested_mode": req_mode,
        "requested_renderer": renderer,
        "selected_renderer": routing.get("selected_renderer"),
        "renderer_routing": {
            "status": routing["status"],
            "selected_renderer": routing["selected_renderer"],
            "per_renderer_rights": routing["per_renderer_rights"],
            "fake_permission_receipt_created": False,
        },
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count() or 1,
        "free_disk_gib": free_gib,
        "disk_floor_ok": free_gib >= DEFAULT_DISK_RESERVE_GIB,
        "readiness": {
            "PLAN_READY": plan_ready,
            "NATIVE_PREVIEW_READY": native_preview_ready,
            "NATIVE_FULL_RENDER_READY": native_full_ready,
            "INDEPENDENT_CLEAN_ROOM_READY": indep_ready,
            "HYBRID_REMOTION_READY": hybrid_remotion_ready,
            "RELEASE_READY": release_ready,
        },
        "tools": {
            "ffmpeg": bool(ffmpeg_bin),
            "ffprobe": bool(ffprobe_bin),
            "node": bool(node_bin),
            "chromium": bool(chrome_bin),
            "remotion_ready": bool(rem_probe.get("ready")),
            "remotion_status": rem_probe.get("status"),
            "remotion_version": rem_probe.get("remotion_version"),
            "remotion_browser_found": bool(rem_probe.get("browser_executable")),
            "dx01_effect_count": eff_reg_snap.get("dx01_effect_count"),
            "dx01_families": eff_reg_snap.get("families"),
            "upstream_seecut_render_mjs": has_upstream_render,
            "upstream_h1_geom_check_mjs": has_h1,
            "upstream_jianying_layers_mjs": has_layers,
            "pinned_hyperframes_version": hf_ver,
            "pinned_playwright_version": pw_ver,
            "pinned_hyperframes_gsap": has_hf,
            "pinned_gsap_sha256": gsap_sha,
            "agy_cli_present": bool(agy_bin),
            "candidate_preview_adapter_wired": True,
            "live_pairwise_adapter_wired": True,
            "detector_primary": det_caps.get("primary_detector"),
            "detector_semantic_classification": det_caps.get("semantic_object_classification"),
        },
        "render_capability": (
            "FULL_SEECUT_CORE_READY" if native_preview_ready
            else ("HYBRID_REMOTION_AND_INDEPENDENT_READY" if hybrid_remotion_ready else "PLAN_AND_OFFLINE_QA_ONLY_UPSTREAM_OR_LICENSE_BLOCKED")
        ),
        "blockers": blockers,
        "next_actions": next_actions,
        "pass": requested_ready,
    }
    if run_dir is not None:
        write_json_atomic(run_dir / "manifests" / "ENV_REPORT.json", rep)
    return rep


def run_capabilities(*, workspace_override: Path | None = None) -> dict[str, Any]:
    """DX01-01 CLI capability discovery: reflects ACTUAL installed Remotion runtime & EffectRegistry state."""
    from .effect_registry import get_default_effect_registry
    from .remotion_adapter import probe_remotion_runtime

    rem = probe_remotion_runtime(remotion_root=workspace_override)
    reg = get_default_effect_registry()
    reg.set_remotion_available(bool(rem.get("ready")))
    snap = reg.snapshot()
    return {
        "schema_version": "kh03.capabilities.v1",
        "kh03_version": __version__,
        "registry_version": snap["registry_version"],
        "remotion_runtime": {
            "ready": bool(rem.get("ready")),
            "status": rem.get("status"),
            "remotion_version": rem.get("remotion_version"),
            "versions_aligned": bool(rem.get("versions_aligned")),
            "browser_available": bool(rem.get("browser_exists")),
            "offline_capable": bool(rem.get("offline_capable")),
            "node_version": rem.get("node_version"),
        },
        "effect_registry": snap,
    }


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
    secondary_sources: list[dict[str, Any]] | None = None,
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
    comm_evid = commercial_permission_evidence if isinstance(commercial_permission_evidence, dict) else (str(commercial_permission_evidence or "").strip() or None)
    perm_check = verify_commercial_permission_evidence(comm_evid)
    if eff_purpose == "COMMERCIAL_MARKETING" and (eff_lic != "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED" or not perm_check["verified"]):
        eff_lic = "BLOCKED_COMMERCIAL_LICENSE"
    elif eff_lic == "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED" and not perm_check["verified"]:
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
        secondary_sources=secondary_sources,
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
    max_memory_mib: int | None = None,
    queue_frames: int = 2,
    enable_tracking: bool | None = None,
    reduced_motion: bool = False,
    compete_candidates: bool = False,
    max_candidates: int = 3,
    pairwise_fn: Any = None,
    owner_override_candidate_id: str | None = None,
    owner_selection_receipt: dict[str, Any] | None = None,
    candidate_preview_runner: Any = None,
    offline: bool = True,
    online_opt_in: bool = False,
    run_h2: bool = False,
    compare_baselines: list[Path] | None = None,
    export_layers: bool = False,
    production_mode: bool = True,
    allow_test_only_sfx: bool = False,
    renderer: str = "native_seecut",
    production_profile: str = "standard",
    cache_dir: Path | None = None,
    independent_spec_overrides: dict[str, Any] | None = None,
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
    saved_evid = saved_c.get("commercial_permission_evidence")

    eff_purpose = str(intended_purpose or saved_purpose).strip().upper()
    eff_evid = commercial_permission_evidence if commercial_permission_evidence is not None else saved_evid
    eff_lic = str(license_decision or saved_lic).strip().upper()
    perm_check = verify_commercial_permission_evidence(eff_evid)

    # 2. Per-Renderer License Routing (R4.2.HOTFIX): separates NATIVE_SEECUT (PolyForm Noncommercial 1.0.0)
    # from audited clean-room INDEPENDENT_FFMPEG_PIL without weakening PROBLEM-01 on Native SeeCut!
    routing_decision = select_renderer_backend(
        requested=renderer,
        intended_purpose=eff_purpose,
        commercial_permission_evidence=eff_evid,
        seecut_root=seecut_root,
        tools_root=tools_root,
        out_dir=(None if dry_run else rdir / "manifests"),
    )
    use_independent_clean_room = (
        routing_decision.get("selected_renderer") in (RENDERER_INDEPENDENT_CLEAN_ROOM, RENDERER_HYBRID_REMOTION)
        and routing_decision.get("allowed") is True
    )
    use_hybrid_remotion = (routing_decision.get("selected_renderer") == RENDERER_HYBRID_REMOTION)

    if not use_independent_clean_room:
        # P0 Fail-Closed PROBLEM-01 License Gate for NATIVE_SEECUT (rejects invalid status, commercial override bypass, or unsubstantiated permission!)
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
        if eff_purpose == "COMMERCIAL_MARKETING" and (eff_lic != "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED" or not perm_check["verified"]):
            transition_state(rdir, STATE_BLOCKED, reason=f"COMMERCIAL_MARKETING lacks verified receipt: {perm_check['reason']}", run_id=run_id)
            return {
                "exit_code": 2,
                "status": "BLOCKED_COMMERCIAL_LICENSE",
                "run_id": run_id,
                "selected_skill_id": skill_id,
                "renderer_routing": routing_decision,
                "reason": f"COMMERCIAL_MARKETING on NATIVE_SEECUT requires WRITTEN_COMMERCIAL_PERMISSION_VERIFIED with verified structured receipt: {perm_check['reason']}",
            }
        if eff_lic == "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED" and not perm_check["verified"]:
            transition_state(rdir, STATE_BLOCKED, reason=f"Unsubstantiated WRITTEN_COMMERCIAL_PERMISSION_VERIFIED: {perm_check['reason']}", run_id=run_id)
            return {
                "exit_code": 2,
                "status": "BLOCKED_COMMERCIAL_LICENSE",
                "run_id": run_id,
                "selected_skill_id": skill_id,
                "reason": f"Unsubstantiated WRITTEN_COMMERCIAL_PERMISSION_VERIFIED rejected: {perm_check['reason']}",
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
                    "Written commercial permission from aiesecxuanli@gmail.com is required before commercial native execution, "
                    "or select audited --renderer independent_clean_room."
                ),
            }

    # Check disk floor & CMI-10 bounded resource budget BEFORE materializing assets/renders
    pf = run_preflight(run_dir=rdir)
    if not pf["disk_floor_ok"]:
        transition_state(rdir, STATE_BLOCKED, reason=f"Insufficient disk space: {pf['free_disk_gib']} GiB", run_id=run_id)
        return {"exit_code": 2, "status": "BLOCKED_LOW_DISK", "run_id": run_id, "free_disk_gib": pf["free_disk_gib"]}

    budget = compute_resource_budget(
        rdir,
        workers=workers,
        max_memory_mib=max_memory_mib,
        max_disk_gib=max_disk_gib,
        queue_frames=queue_frames if queue_frames is not None else 8,
        run_dir=(None if dry_run else rdir),
    )
    if not budget["budget_ok"]:
        b_stat = str(budget.get("status") or "BLOCKED_RESOURCE_BUDGET")
        transition_state(rdir, STATE_BLOCKED, reason=f"{b_stat}: {budget.get('reason')}", run_id=run_id)
        return {"exit_code": 2, "status": b_stat, "run_id": run_id, "execution_budget": budget}

    eff_workers = int(budget["workers"])
    transition_state(rdir, STATE_PLANNING, reason=f"Starting KH-03.R3 planning for selected skill {skill_id} (workers={eff_workers}).", run_id=run_id)
    asset_purpose = (
        "COMMERCIAL_AND_NONCOMMERCIAL" if eff_lic == "WRITTEN_COMMERCIAL_PERMISSION_VERIFIED"
        else ("TEST_ONLY" if not production_mode else "NONCOMMERCIAL_ONLY")
    )

    # 3. CMI-10 + R4.1-03 Ctx-Safe Bounded Async DAG for independent Stage 3 analyses
    # ALL write-capable workers receive (deps, ctx) and publish ONLY via CoordinatorPublisher under active TaskLease!
    def _w_research(_deps: dict[str, Any], ctx: TaskContext) -> Path:
        ctx.check_cancelled()
        return emit_research_summary(rdir, skill_id, ctx=(None if dry_run else ctx))

    def _w_footage(_deps: dict[str, Any], ctx: TaskContext) -> dict[str, Any]:
        ctx.check_cancelled()
        return acquire_and_bind_footage(
            run_id=run_id,
            run_dir=rdir,
            semantic_graph=sem,
            skill_id=skill_id,
            constraints=saved_c,
            local_libraries=footage_library_dirs,
            supplied_assets=supplied_assets,
            intended_purpose=asset_purpose,
            workers=eff_workers,
            offline=eff_offline,
            ctx=(None if dry_run else ctx),
        )

    def _w_explanation(_deps: dict[str, Any], ctx: TaskContext) -> dict[str, Any]:
        ctx.check_cancelled()
        return plan_explanation(sem, inp, skill_id=skill_id, run_dir=(None if dry_run else rdir), ctx=(None if dry_run else ctx))

    def _w_registry(_deps: dict[str, Any], ctx: TaskContext) -> dict[str, Any]:
        ctx.check_cancelled()
        return build_registry()

    dag = BoundedAsyncExecutor(
        run_id=run_id,
        budget=budget,
        run_dir=(None if dry_run else rdir),
    )
    dag_res = dag.run([
        DAGTask(task_id="01_research_summary", pool="network", fn=_w_research),
        DAGTask(task_id="02_footage_acquire", pool="network", fn=_w_footage),
        DAGTask(task_id="03_explanation_graph", pool="cpu", fn=_w_explanation),
        DAGTask(task_id="04_capability_registry", pool="cpu", fn=_w_registry),
    ])
    if not dag_res["pass"]:
        fail_r = [r for r in dag_res["receipts"] if r["status"] != "PASS"]
        transition_state(rdir, STATE_BLOCKED, reason=f"Bounded DAG planning failed: {fail_r}", run_id=run_id)
        return {"exit_code": 1, "status": "PLANNING_DAG_FAIL", "run_id": run_id, "errors": fail_r}

    footage_manifest = dag_res["results"]["02_footage_acquire"]
    explanation_graph = dag_res["results"]["03_explanation_graph"]
    reg = dag_res["results"]["04_capability_registry"]
    transition_state(
        rdir,
        STATE_ASSET_READY if footage_manifest["status"] == "ASSET_READY" else STATE_ASSET_PARTIAL,
        reason=f"Footage status={footage_manifest['status']} ({len(footage_manifest['assets'])} verified asset(s)).",
        run_id=run_id,
    )

    eff_exec_c = dict(saved_c)
    eff_enable_tracking = bool(production_mode) if enable_tracking is None else bool(enable_tracking)
    if eff_enable_tracking:
        eff_exec_c["enable_streaming_tracking"] = True
    if reduced_motion:
        eff_exec_c["reduced_motion"] = True
    if presenter_mode:
        eff_exec_c["presenter_mode"] = presenter_mode
    eff_cut = cut_policy or saved_c.get("cut_policy") or "LOCK_VOICE"
    eff_caption = caption_policy or saved_c.get("caption_policy") or "OFF"
    eff_music = music_policy or saved_c.get("music_policy") or "OFF"
    eff_exec_c["caption_policy"] = eff_caption

    # PED-00 / Section 03: Universal cross-niche editorial context
    ed_ctx = build_editorial_context(
        inp,
        sem,
        skill_id=skill_id,
        available_assets=footage_manifest.get("assets"),
        constraints=eff_exec_c,
        run_dir=(None if dry_run else rdir),
    )
    if ed_ctx["support_status"] == "UNSUPPORTED":
        transition_state(rdir, STATE_BLOCKED, reason=f"UNSUPPORTED_INPUT: {ed_ctx['unsupported_reasons']}", run_id=run_id)
        return {"exit_code": 2, "status": "UNSUPPORTED_INPUT", "run_id": run_id, "reasons": ed_ctx["unsupported_reasons"]}

    # 4. Adaptive Creative Plan + Layout Choreography + CMI-01..07 + PED-01..07 Motion/Evidence/Typography/Density
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
        constraints=eff_exec_c,
    )
    lp_file = rdir / "layouts" / "LAYOUT_PLAN.json"
    layout_plan = parse_json_strict(lp_file) if lp_file.is_file() else None
    mg_file = rdir / "manifests" / "MOTION_GRAPH.json"
    motion_graph = parse_json_strict(mg_file) if mg_file.is_file() else None
    ev_file = rdir / "assets" / "EVIDENCE_LEDGER.json"
    evidence_ledger = parse_json_strict(ev_file) if ev_file.is_file() else None
    vd_file = rdir / "qc" / "VISUAL_DENSITY_REPORT.json"
    visual_density = parse_json_strict(vd_file) if vd_file.is_file() else None
    trk_file = rdir / "analysis" / "TRACKING_PROVENANCE.json"
    tracking_prov = parse_json_strict(trk_file) if trk_file.is_file() else None

    # PED-09 / R4.1-01 Real Multi-Candidate Creative Competition + End-to-End Winner Binding
    candidate_decision = None
    winner_binding_doc = None
    winning_candidate_id: str | None = None
    winner_binding_sha: str | None = None
    competition_status = "SINGLE_PLAN_NO_COMPETITION"

    source_hashes: dict[str, str] = {"primary_aroll": inp["video_sha256"]}
    if inp.get("secondary_sources_sha256"):
        source_hashes["secondary_sources"] = str(inp["secondary_sources_sha256"])

    if compete_candidates and not dry_run and layout_plan is not None and not use_independent_clean_room:
        sat_p = rdir / "layouts" / "SUBJECT_ACTION_TRACKS.json"
        sat_doc = parse_json_strict(sat_p) if sat_p.is_file() else {}
        tr_p_cand = rdir / "analysis" / "TRANSCRIPT_NORMALIZED.json"
        tr_doc_cand = parse_json_strict(tr_p_cand) if tr_p_cand.is_file() else None
        cand_list = generate_creative_candidates(
            run_id=run_id,
            selected_skill_id=skill_id,
            beats=list(creative_plan.get("beats") or []),
            canvas=dict(layout_plan.get("canvas") or {"width": 720, "height": 1280, "aspect_ratio": "9:16"}),
            source_media=inp["media"],
            action_tracks=sat_doc,
            layout_policy=str(layout_plan.get("layout_policy") or "ADAPTIVE_DYNAMIC"),
            constraints=eff_exec_c,
            assets=footage_manifest.get("assets"),
            max_candidates=max_candidates,
            base_creative_plan=creative_plan,
            semantic_graph=sem,
            explanation_graph=explanation_graph,
            transcript=tr_doc_cand,
            selection_receipt_sha256=sel_sha,
            source_hashes=source_hashes,
            run_dir=rdir,
        )
        div_check = validate_candidate_diversity(cand_list)
        if not div_check["pass"]:
            valid_id_set = set(div_check.get("valid_candidate_ids") or [])
            cand_list = [c for c in cand_list if c["candidate_id"] in valid_id_set]
            if not cand_list:
                transition_state(rdir, STATE_BLOCKED, reason="CANDIDATE_DIVERSITY_FAIL: no valid structurally distinct candidates.", run_id=run_id)
                return {
                    "exit_code": 2,
                    "status": "CANDIDATE_DIVERSITY_FAIL",
                    "run_id": run_id,
                    "selected_skill_id": skill_id,
                    "diversity_check": div_check,
                }

        # Preview/Full competition: wire default native preview runner & live pairwise judge when not injected (R4.2-03/04)
        eff_preview_runner = candidate_preview_runner
        if mode in ("preview", "full") and eff_preview_runner is None:
            _fs_lk = bool(saved_c.get("fullscreen_lock"))
            _lp_pol = str(saved_c.get("layout_policy") or ("FULLSCREEN_LOCK" if _fs_lk else "LEGACY_DYNAMIC"))
            _pres_pol_prev = (
                {"mode": "LEGACY_DYNAMIC", "visibility": "ALWAYS_VISIBLE", "allow_hide": False, "allow_fullscreen": True, "fullscreen_lock": True, "layout_policy": "FULLSCREEN_LOCK"}
                if _fs_lk else (
                    {"mode": "LEGACY_DYNAMIC", "visibility": "ALWAYS_VISIBLE", "allow_hide": False, "allow_fullscreen": True, "fullscreen_lock": False, "layout_policy": "ADAPTIVE_DYNAMIC"}
                    if _lp_pol == "ADAPTIVE_DYNAMIC" else {"mode": presenter_mode or "LEGACY_DYNAMIC"}
                )
            )
            _srcs_prev = [{
                "asset_id": "primary_aroll",
                "path": inp["video_path"],
                "kind": "a_roll",
                "rights": inp["video_rights"] if inp["video_rights"] in (
                    "OWNER_SUPPLIED_NONCOMMERCIAL", "LICENSED_COMMERCIAL", "CAPTURED_REAL_EVIDENCE_MASKED",
                ) else "OWNER_SUPPLIED_NONCOMMERCIAL",
                "label": "Primary source A-roll",
            }]
            for fa in footage_manifest["assets"]:
                _srcs_prev.append({
                    "asset_id": fa["asset_id"],
                    "path": fa["local_path"],
                    "kind": "still_asset" if fa["media_type"] in ("image", "screen_capture", "diagram_asset") else "b_roll",
                    "rights": "LICENSED_COMMERCIAL" if asset_purpose == "COMMERCIAL_AND_NONCOMMERCIAL" else "OWNER_SUPPLIED_NONCOMMERCIAL",
                    "label": fa["source_label"],
                })
            eff_preview_runner = build_native_candidate_preview_runner(
                run_id=run_id, parent_run_dir=rdir, inp=inp, skill_id=skill_id,
                selection_receipt=sel_val["receipt"], sources_list=_srcs_prev,
                pres_pol=_pres_pol_prev, eff_cut=eff_cut, eff_caption=eff_caption,
                eff_music=eff_music, sfx_policy=sfx_policy, eff_purpose=eff_purpose,
                eff_lic=eff_lic, eff_evid=eff_evid,
                sfx_library_dirs=sfx_library_dirs, sfx_config_path=sfx_config_path,
                offline=eff_offline, production_mode=production_mode,
                allow_test_only_sfx=allow_test_only_sfx,
                seecut_root=seecut_root, tools_root=tools_root, render_scale=0.5,
            )

        preview_receipts: list[dict[str, Any]] = []
        if mode in ("preview", "full") and eff_preview_runner is not None:
            for c_item in cand_list:
                if not c_item.get("plan_gate_pass"):
                    continue
                prev_res = eff_preview_runner(c_item, Path(c_item["workspace_dir"]))
                c_item["preview_mp4_path"] = prev_res.get("preview_mp4_path")
                c_item["preview_mp4_sha256"] = prev_res.get("preview_mp4_sha256")
                c_item["html_path"] = prev_res.get("html_path")
                c_item["preview_qa_pass"] = bool(prev_res.get("preview_qa_pass", False))
                preview_receipts.append(dict(prev_res, candidate_id=c_item["candidate_id"]))
            write_json_atomic(rdir / "manifests" / "CANDIDATE_PREVIEW_RECEIPTS.json", {
                "schema_version": "kh03.candidate_preview_receipts.v1",
                "run_id": run_id,
                "mode": mode,
                "receipts": preview_receipts,
            })

        eff_pairwise_fn = pairwise_fn
        if eff_pairwise_fn is None and mode in ("preview", "full") and online_opt_in:
            eff_pairwise_fn = build_live_pairwise_judge(
                online_opt_in=online_opt_in,
                seecut_root=seecut_root,
                out_dir=rdir / "qc" / "pairwise",
            )

        ranking = rank_candidates(
            cand_list,
            pairwise_fn=eff_pairwise_fn,
            mode=mode,
            require_preview_proof=(mode in ("preview", "full")),
        )
        ranking["diversity_check"] = div_check
        if ranking.get("pairwise_records"):
            write_json_atomic(rdir / "qc" / "PAIRWISE_AB_BA.json", {
                "schema_version": "kh03.pairwise_ab_ba.v1",
                "run_id": run_id,
                "mode": mode,
                "pairwise_status": ranking.get("pairwise_status"),
                "winner_candidate_id": (ranking.get("winner") or {}).get("candidate_id"),
                "records": ranking.get("pairwise_records") or [],
            })
        candidate_decision = adopt_candidate(
            ranking,
            owner_override_candidate_id=owner_override_candidate_id,
            owner_selection_receipt=owner_selection_receipt,
            mode=mode,
            require_preview_proof=(mode in ("preview", "full")),
            expected_selection_sha=sel_sha,
            run_dir=rdir,
        )
        cands_by_id = {c["candidate_id"]: c for c in cand_list}
        bind_res = bind_winner_to_execution(
            candidate_decision,
            cands_by_id,
            run_dir=rdir,
            selection_receipt_sha256=sel_sha,
            source_hashes=source_hashes,
            mode=mode,
        )
        winner_binding_doc = bind_res["binding"]
        if not bind_res["bound"]:
            blk_st = str(candidate_decision.get("status") or "CANDIDATE_BLOCKED")
            lifecycle = resolve_editorial_lifecycle(
                rendered_candidates=preview_receipts if preview_receipts else cand_list,
                pairwise_status=str(ranking.get("pairwise_status") or "PENDING_REVIEW_MISSING_JUDGE"),
                winner_candidate_id=(ranking.get("winner") or {}).get("candidate_id"),
                owner_override_candidate_id=owner_override_candidate_id,
                live_judge_available=bool(online_opt_in and eff_pairwise_fn is not None and ranking.get("pairwise_status") != "PENDING_REVIEW_MISSING_JUDGE"),
            )
            write_json_atomic(rdir / "manifests" / "MANUAL_EDITORIAL_REVIEW.json", {
                "schema_version": "kh03.manual_editorial_review.v1",
                "run_id": run_id,
                "mode": mode,
                "selected_skill_id": skill_id,
                "lifecycle": lifecycle,
                "preview_receipts": preview_receipts,
                "next_action": "Review rendered candidate preview MP4s directly and pass --owner-override-candidate <cand_A|cand_B|cand_C> with --owner-selection-receipt to proceed to full render.",
            })
            transition_state(rdir, STATE_BLOCKED, reason=f"Candidate competition halted without valid winner ({blk_st}).", run_id=run_id)
            return {
                "exit_code": 2,
                "status": blk_st,
                "run_id": run_id,
                "selected_skill_id": skill_id,
                "candidate_decision": candidate_decision,
                "winner_binding": winner_binding_doc,
                "lifecycle": lifecycle,
                "preview_receipts": preview_receipts,
                "reason": candidate_decision.get("reason") or "No valid consensus/adopted candidate; refusing silent fallback to original plan.",
            }
        # REPLACE authoritative creative_plan, layout_plan, motion_graph with adopted winner bundle BEFORE sound & KH-02 compile!
        creative_plan = bind_res["creative_plan"]
        layout_plan = bind_res["layout_plan"]
        if bind_res["motion_graph"] is not None:
            motion_graph = bind_res["motion_graph"]
        winning_candidate_id = str(winner_binding_doc["winning_candidate_id"])
        winner_binding_sha = str(winner_binding_doc["winner_binding_sha256"])
        competition_status = str(winner_binding_doc["status"])

    fps = inp["media"]["fps_num"] / float(max(1, inp["media"]["fps_den"]))

    # 5. Multi-Layer Sound Director (anchored to WINNER layout transitions AND WINNER motion-graph sub-events!)
    snd_res = plan_soundscape_for_skill(
        run_id=run_id,
        run_dir=rdir,
        creative_plan=creative_plan,
        layout_plan=layout_plan,
        motion_graph=motion_graph,
        fps=fps,
        fps_num=int(inp["media"]["fps_num"]),
        fps_den=int(inp["media"]["fps_den"]),
        sample_rate=int(inp["media"].get("audio_sample_rate") or 48000),
        voice_policy=eff_cut,
        sfx_policy=sfx_policy,
        bgm_policy=eff_music,
        sfx_library_dirs=sfx_library_dirs,
        sfx_config_path=sfx_config_path,
        intended_purpose=asset_purpose,
        offline=eff_offline,
        workers=eff_workers,
        production_mode=production_mode,
        allow_test_only_sfx=allow_test_only_sfx,
    )

    # 6. Build KH-02 Brief & Invoke Normal KH-02 Pipeline (passing WINNER creative_plan + layout_plan + motion_graph + sound_plan!)
    sk = get_skill_contract(skill_id)
    kh03_ov = compile_to_kh02_overrides(
        creative_plan,
        fps=fps,
        sound_plan=snd_res["sound_plan"],
        layout_plan=layout_plan,
        motion_graph=motion_graph,
        caption_policy=eff_caption,
        winning_candidate_id=winning_candidate_id,
        winner_binding_sha256=winner_binding_sha,
        competition_status=competition_status,
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

    # R4.3 Clean-Room Independent Semantic Compiler, Bounded Candidate Competition, Render & Full Independent QA
    if use_independent_clean_room and not dry_run and mode in ("preview", "full"):
        import time as _time
        from concurrent.futures import ThreadPoolExecutor
        from .independent_qa import run_independent_render_qa
        from .independent_renderer import render_independent_video
        from .ingest_cache import SharedIngestCache, build_lowres_proxy_cached, get_process_tree_rss_mib
        from .semantic_timeline import build_source_registry, compile_semantic_timeline

        t0_ind = _time.monotonic()
        eff_profile = str(production_profile or "standard").strip().lower()
        if eff_profile not in ("quick", "standard", "high"):
            eff_profile = "standard"

        ind_cache = SharedIngestCache(cache_dir or (rdir / "cache" / "ingest"))
        ov_cfg = dict(independent_spec_overrides or {})
        sec_srcs = list(inp.get("secondary_sources") or [])
        sec_entries = [
            {
                "source_id": str(s.get("source_id") or f"secondary_{idx_s + 1:02d}"),
                "path": str(s["path"]),
                "role": str(s.get("role") or "secondary_evidence"),
                "audio_policy": str(s.get("audio_policy") or "MUTE_DEFAULT"),
                "rights": str(s.get("rights") or "OWNER_SUPPLIED_NONCOMMERCIAL"),
            }
            for idx_s, s in enumerate(sec_srcs)
            if isinstance(s, dict) and s.get("path")
        ]

        # Build arbitrary-N source registry (single probe/SHA per input!)
        src_reg = build_source_registry(
            {"source_id": "primary_aroll", "path": inp["video_path"], "role": "primary_aroll", "audio_policy": "MASTER_VOICE"},
            sec_entries,
            cache=ind_cache,
        )
        write_json_atomic(rdir / "manifests" / "SOURCE_REGISTRY.json", src_reg)

        # Load authoritative VOICE_EDL.json if computed (NO silent fallback to uncut A-roll when EDL exists!)
        ve_path = rdir / "analysis" / "VOICE_EDL.json"
        voice_edl_doc = ov_cfg.get("voice_edl") or (parse_json_strict(ve_path) if ve_path.is_file() else None)
        sat_p_ind = rdir / "layouts" / "SUBJECT_ACTION_TRACKS.json"
        sat_ind = parse_json_strict(sat_p_ind) if sat_p_ind.is_file() else None

        is_portrait = inp["media"]["aspect_ratio"] == "9:16"
        is_square = inp["media"]["aspect_ratio"] == "1:1"
        if is_square:
            full_w, full_h = (1080, 1080)
            prev_w, prev_h = (540, 540)
        elif is_portrait:
            full_w, full_h = (1080, 1920)
            prev_w, prev_h = (540, 960)
        else:
            full_w, full_h = (1920, 1080)
            prev_w, prev_h = (960, 540)
        out_w = prev_w if (mode == "preview" or eff_profile == "quick") else full_w
        out_h = prev_h if (mode == "preview" or eff_profile == "quick") else full_h
        fps_n = int(inp["media"]["fps_num"])
        fps_d = int(inp["media"]["fps_den"])
        ff_thr = max(1, min(4, int(budget.get("ffmpeg_threads") or 2)))
        rem_conc = max(1, min(2, int(budget.get("browser_render_concurrency") or 1)))
        eff_bgm = (eff_music != "OFF") and bool(ov_cfg.get("bgm_enabled", True))
        eff_sfx = (sfx_policy != "OFF") and bool(ov_cfg.get("sfx_enabled", True))
        eff_enable_remotion = bool(use_hybrid_remotion or ov_cfg.get("enable_remotion", False))
        eff_prefer_dx01 = bool(use_hybrid_remotion or ov_cfg.get("prefer_dx01_non_card", False))
        eff_silent_policy = bool(ov_cfg.get("silent_policy", not inp["media"].get("has_audio", True)))
        rem_cache_dir = (cache_dir.parent if cache_dir is not None else (rdir / "cache"))

        # DX01-08: Distinct candidate competition (structural dedup + visual fingerprint + real preview QA + normalized plan binding)
        cand_preview_receipts: list[dict[str, Any]] = []
        skipped_duplicate_cands: list[dict[str, Any]] = []
        provisional_binding: dict[str, Any] | None = None
        selected_cand_id = str(owner_override_candidate_id or "cand_A")
        cand_variants: list[dict[str, Any]] = list(ov_cfg.get("candidate_variants") or [])
        cand_specs_by_id: dict[str, dict[str, Any]] = {}

        def _strip_ids_and_palette(spec_obj: dict[str, Any]) -> dict[str, Any]:
            """Structural signature excluding IDs and palette/colors so color-only clones are detected."""
            def _clean_ev(ev: dict[str, Any]) -> dict[str, Any]:
                return {
                    k: v for k, v in ev.items()
                    if k not in ("event_id", "track_id", "layer_id", "color", "accent", "bg_color", "palette")
                }
            return {
                "visual_tracks": [_clean_ev(t) for t in (spec_obj.get("visual_tracks") or [])],
                "layers": [_clean_ev(l) for l in (spec_obj.get("layers") or [])],
                "footage_effects": [_clean_ev(f) for f in (spec_obj.get("footage_effects") or [])],
                "transitions": [_clean_ev(tr) for tr in (spec_obj.get("transitions") or [])],
                "camera_events": [_clean_ev(c) for c in (spec_obj.get("camera_events") or [])],
                "overlays": [_clean_ev(o) for o in (spec_obj.get("overlays") or [])],
                "effect_invocations": [_clean_ev(fx) for fx in (spec_obj.get("effect_invocations") or [])],
            }

        def _sample_mp4_visual_fingerprint(mp4_path: Path, n_frames: int) -> str:
            import hashlib as _hl
            import subprocess as _sp
            fps_sample = max(0.05, 3.0 / max(0.5, n_frames / 24.0))
            cmd = [
                "ffmpeg", "-v", "error", "-nostdin", "-i", str(mp4_path),
                "-vf", f"fps={fps_sample:.4f},scale=64:64:flags=area",
                "-frames:v", "3", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
            ]
            p = _sp.run(cmd, capture_output=True, timeout=30)
            return _hl.sha256(p.stdout or b"").hexdigest()

        if compete_candidates and eff_profile in ("standard", "high"):
            # Build reusable low-res proxy ONLY for candidate previews (NEVER upscaled as final!)
            proxy_reg = build_source_registry(
                {
                    "source_id": "primary_aroll",
                    "path": inp["video_path"],
                    "proxy_path": build_lowres_proxy_cached(
                        Path(inp["video_path"]), width=prev_w, height=prev_h,
                        fps_num=fps_n, fps_den=fps_d, cache=ind_cache, ffmpeg_threads=ff_thr,
                    )["proxy_path"],
                    "role": "primary_aroll",
                    "audio_policy": "MASTER_VOICE",
                },
                [
                    {
                        **se,
                        "proxy_path": build_lowres_proxy_cached(
                            Path(se["path"]), width=prev_w, height=prev_h,
                            fps_num=fps_n, fps_den=fps_d, cache=ind_cache, ffmpeg_threads=ff_thr,
                        )["proxy_path"],
                    }
                    for se in sec_entries
                ],
                cache=ind_cache,
            )
            if not cand_variants:
                # Synthesize genuinely distinct A/B/C structural strategies (framing, pullback, transitions, effect grammar)
                prim_dur = float(src_reg["by_id"]["primary_aroll"]["video_duration_sec"])
                tot_f_est = max(12, int(round(prim_dur * (fps_n / float(max(1, fps_d))))))
                mid_f = max(4, tot_f_est // 2)
                has_sec = len(sec_entries) > 0
                sec_id = sec_entries[0]["source_id"] if has_sec else "primary_aroll"
                sec_dur = float(src_reg["by_id"][sec_id]["video_duration_sec"]) if has_sec else prim_dur

                # Candidate A: Clean subject-led framing with subtle micro-zoom and minimal lower/top motion
                cand_a_tracks = ov_cfg.get("visual_tracks") or [{
                    "track_id": "trk_01", "source_id": "primary_aroll", "mode": "source",
                    "out_start_frame": 0, "out_end_frame": tot_f_est,
                    "src_in_sec": 0.0, "src_out_sec": prim_dur,
                    "scale_start": 1.0, "scale_end": 1.04, "cx_norm": 0.5, "cy_norm": 0.48,
                    "transition_frames": 0, "transition_kind": "cut",
                }]
                # Candidate B: Evidence / Pullback-led framing (<1.0 pullback or multi-source dual-moving mask wipe!)
                if has_sec and tot_f_est >= 24:
                    cand_b_tracks = [
                        {
                            "track_id": "trk_01", "source_id": "primary_aroll", "mode": "source",
                            "out_start_frame": 0, "out_end_frame": mid_f,
                            "src_in_sec": 0.0, "src_out_sec": min(prim_dur, mid_f / (fps_n / float(max(1, fps_d)))),
                            "scale_start": 1.0, "scale_end": 1.02, "cx_norm": 0.5, "cy_norm": 0.5,
                            "transition_frames": 0, "transition_kind": "cut",
                        },
                        {
                            "track_id": "trk_02", "source_id": sec_id, "mode": "source",
                            "out_start_frame": mid_f, "out_end_frame": tot_f_est,
                            "src_in_sec": min(0.2, max(0.0, sec_dur - 0.5)),
                            "src_out_sec": min(sec_dur - 0.05, 0.2 + (tot_f_est - mid_f) / (fps_n / float(max(1, fps_d)))),
                            "scale_start": 0.92, "scale_end": 0.96, "cx_norm": 0.5, "cy_norm": 0.5,
                            "border_radius_px": 24,
                            "transition_frames": min(6, max(2, mid_f // 4)),
                            "transition_kind": "dx01.trans.mask_wipe",
                            "transition_direction": "left",
                        },
                    ]
                else:
                    cand_b_tracks = [{
                        "track_id": "trk_01", "source_id": "primary_aroll", "mode": "source",
                        "out_start_frame": 0, "out_end_frame": tot_f_est,
                        "src_in_sec": 0.0, "src_out_sec": prim_dur,
                        "scale_start": 0.90, "scale_end": 0.96, "cx_norm": 0.5, "cy_norm": 0.5,
                        "tilt_y_deg": 4.0, "border_radius_px": 28,
                        "transition_frames": 0, "transition_kind": "cut",
                    }]
                # Candidate C: Contrast & camera rhythm variant (framed emphasis + exposure grade)
                cand_c_tracks = [{
                    "track_id": "trk_01", "source_id": "primary_aroll", "mode": "source",
                    "out_start_frame": 0, "out_end_frame": tot_f_est,
                    "src_in_sec": 0.0, "src_out_sec": prim_dur,
                    "scale_start": 1.06, "scale_end": 1.01, "cx_norm": 0.5, "cy_norm": 0.44,
                    "transition_frames": 0, "transition_kind": "cut",
                }]
                cand_c_ffx = [{
                    "event_id": "ffx_c_grade", "effect_id": "dx01.footage.exposure_grade",
                    "start_sec": 0.0, "end_sec": prim_dur,
                    "params": {"exposure_gain": 1.04, "contrast": 1.06, "saturation": 1.05, "vignette": 0.14},
                }]

                cand_variants = [
                    {
                        "candidate_id": "cand_A", "label": "A", "strategy": "SUBJECT_CLEAN_PACED",
                        "visual_tracks": cand_a_tracks,
                        "overlays": ov_cfg.get("semantic_overlays"),
                        "effect_invocations": ov_cfg.get("effect_invocations"),
                        "camera_events": ov_cfg.get("camera_events"),
                        "footage_effects": ov_cfg.get("footage_effects"),
                    },
                    {
                        "candidate_id": "cand_B", "label": "B", "strategy": "EVIDENCE_PULLBACK_WIPE",
                        "visual_tracks": cand_b_tracks,
                        "overlays": ov_cfg.get("semantic_overlays"),
                        "effect_invocations": ov_cfg.get("effect_invocations"),
                        "camera_events": ov_cfg.get("camera_events"),
                        "footage_effects": ov_cfg.get("footage_effects"),
                    },
                    {
                        "candidate_id": "cand_C", "label": "C", "strategy": "CONTRAST_DYNAMIC_GRADE",
                        "visual_tracks": cand_c_tracks,
                        "overlays": ov_cfg.get("semantic_overlays"),
                        "effect_invocations": ov_cfg.get("effect_invocations"),
                        "camera_events": ov_cfg.get("camera_events"),
                        "footage_effects": cand_c_ffx,
                    },
                ][: max(1, min(3, int(max_candidates)))]

            # Compile & structurally deduplicate BEFORE wasting render cycles on cosmetic clones!
            unique_cand_jobs: list[tuple[dict[str, Any], dict[str, Any], str]] = []
            seen_struct_sigs: dict[str, str] = {}
            for cv in cand_variants:
                cid = str(cv["candidate_id"])
                cspec = compile_semantic_timeline(
                    job_id=f"{run_id}_{cid}",
                    source_registry=proxy_reg,
                    voice_edl=voice_edl_doc,
                    creative_plan=creative_plan,
                    layout_plan=layout_plan,
                    motion_graph=motion_graph,
                    sound_director_plan=snd_res["sound_plan"],
                    evidence_ledger=evidence_ledger,
                    subject_action_tracks=sat_ind,
                    selection_receipt=sel_val["receipt"],
                    candidate_binding={"provisional_candidate_id": cid, "strategy": cv.get("strategy")},
                    visual_tracks=cv.get("visual_tracks", ov_cfg.get("visual_tracks")),
                    layers=cv.get("layers", ov_cfg.get("layers")),
                    footage_effects=cv.get("footage_effects", ov_cfg.get("footage_effects")),
                    transitions=cv.get("transitions", ov_cfg.get("transitions")),
                    effect_invocations=cv.get("effect_invocations", ov_cfg.get("effect_invocations")),
                    motion_director_plan=cv.get("motion_director_plan", ov_cfg.get("motion_director_plan")),
                    camera_events=cv.get("camera_events", ov_cfg.get("camera_events")),
                    semantic_overlays=cv.get("overlays", ov_cfg.get("semantic_overlays")),
                    sfx_cues=cv.get("sfx_cues", ov_cfg.get("sfx_cues")),
                    continuity_seams=cv.get("continuity_seams", ov_cfg.get("continuity_seams")),
                    occupancy_windows=cv.get("occupancy_windows", ov_cfg.get("occupancy_windows")),
                    art_tokens=cv.get("art_tokens", ov_cfg.get("art_tokens")),
                    caption_policy=eff_caption,
                    prefer_dx01_non_card=eff_prefer_dx01,
                    width=prev_w,
                    height=prev_h,
                    fps_num=fps_n,
                    fps_den=fps_d,
                    bgm_enabled=eff_bgm,
                    bgm_style=str(cv.get("bgm_style") or ov_cfg.get("bgm_style") or "warm_calm"),
                    bgm_gain_db=float(ov_cfg.get("bgm_gain_db", -24.5)),
                    sfx_enabled=eff_sfx,
                    sfx_gain_db=float(ov_cfg.get("sfx_gain_db", -16.5)),
                    x264_preset="veryfast",
                    crf=22,
                    use_proxy_sources=True,
                    production_profile=eff_profile,
                    pass_through_allowed=bool(ov_cfg.get("pass_through_allowed", False)),
                )
                if eff_silent_policy:
                    cspec["silent_policy"] = True
                sig = hash_artifact(_strip_ids_and_palette(cspec))
                if sig in seen_struct_sigs:
                    skipped_duplicate_cands.append({
                        "candidate_id": cid,
                        "duplicate_of": seen_struct_sigs[sig],
                        "structural_signature_sha256": sig,
                        "reason": "SKIPPED_STRUCTURAL_DUPLICATE: identical tracks/layers/effects/overlays ignoring IDs and colors.",
                    })
                    continue
                seen_struct_sigs[sig] = cid
                cand_specs_by_id[cid] = cspec
                unique_cand_jobs.append((cv, cspec, sig))

            def _render_one_cand(item: tuple[dict[str, Any], dict[str, Any], str]) -> dict[str, Any]:
                cv, cspec, sig = item
                cid = str(cv["candidate_id"])
                lbl = str(cv.get("label") or cid[-1])
                cdir = rdir / "candidates" / lbl
                cdir.mkdir(parents=True, exist_ok=True)
                write_json_atomic(cdir / "spec.json", cspec)
                cmp4 = cdir / f"preview_{cid}_{prev_w}x{prev_h}.mp4"
                cres = render_independent_video(
                    cspec, out_mp4=cmp4, stems_dir=cdir / "stems",
                    cache=ind_cache, ffmpeg_threads=max(1, ff_thr // 2),
                    enable_remotion=eff_enable_remotion,
                    remotion_cache_root=rem_cache_dir,
                    remotion_concurrency=1,
                )
                # Real candidate technical QA (coverage + isolated motion + spatial + continuity + audio, NOT decode-only!)
                cqa = run_independent_render_qa(
                    run_id=f"{run_id}_{cid}",
                    run_dir=cdir,
                    final_mp4=cmp4,
                    render_spec=cspec,
                    render_receipt=cres,
                    reference_script=ov_cfg.get("reference_script") or inp.get("normalized_script"),
                    mode="INDEPENDENT_PREVIEW",
                    license_status=str(routing_decision.get("status") or "AUTHORIZED_INDEPENDENT_CLEAN_ROOM"),
                    cache=ind_cache,
                    run_post_asr=False,
                    ffmpeg_threads=max(1, ff_thr // 2),
                )
                vfp = _sample_mp4_visual_fingerprint(cmp4, int(cres["nb_frames"]))
                rec_c = {
                    "candidate_id": cid,
                    "label": lbl,
                    "strategy": str(cv.get("strategy") or cid),
                    "engine_kind": cres["engine_kind"],
                    "remotion_used": bool(cres.get("remotion_used")),
                    "spec_sha256": cspec["spec_sha256"],
                    "normalized_creative_plan_sha256": cspec["normalized_creative_plan_sha256"],
                    "structural_signature_sha256": sig,
                    "visual_fingerprint_sha256": vfp,
                    "preview_mp4_path": cres["final_mp4_path"],
                    "preview_mp4_sha256": cres["final_mp4_sha256"],
                    "duration_sec": cres["duration_sec"],
                    "nb_frames": cres["nb_frames"],
                    "decode_qa_pass": bool(cres["decode_qa_pass"]),
                    "preview_qa_pass": bool(cqa["technical_pass"]),
                    "preview_qa_failing_gates": list(cqa["gate_matrix"].get("failing_gates") or []),
                    "elapsed_sec": cres["elapsed_sec"],
                }
                write_json_atomic(cdir / "PREVIEW_RECEIPT.json", rec_c)
                return rec_c

            cand_pool_w = max(1, min(2, int(eff_workers)))
            if cand_pool_w > 1 and len(unique_cand_jobs) > 1:
                with ThreadPoolExecutor(max_workers=cand_pool_w) as cpool:
                    cand_preview_receipts = list(cpool.map(_render_one_cand, unique_cand_jobs))
            else:
                cand_preview_receipts = [_render_one_cand(j) for j in unique_cand_jobs]
            cand_preview_receipts.sort(key=lambda r: r["candidate_id"])

            vfp_set = {r["visual_fingerprint_sha256"] for r in cand_preview_receipts}
            passing_cands = [r for r in cand_preview_receipts if r["preview_qa_pass"]]
            if not passing_cands:
                transition_state(rdir, STATE_BLOCKED, reason="ALL_INDEPENDENT_CANDIDATES_FAILED_QA", run_id=run_id)
                return {"exit_code": 1, "status": "CANDIDATE_TECHNICAL_QA_FAIL", "run_id": run_id, "preview_receipts": cand_preview_receipts}
            if owner_override_candidate_id and any(r["candidate_id"] == owner_override_candidate_id for r in passing_cands):
                selected_cand_id = str(owner_override_candidate_id)
                bind_mode = "OWNER_OVERRIDE_SELECTED"
            else:
                selected_cand_id = str(passing_cands[0]["candidate_id"])
                bind_mode = "PROVISIONAL_TECHNICAL_DEFAULT_NOT_EDITORIALLY_APPROVED"
            sel_preview = next(r for r in passing_cands if r["candidate_id"] == selected_cand_id)
            provisional_binding = {
                "schema_version": "kh03.provisional_binding.v2",
                "run_id": run_id,
                "provisional_candidate_id": selected_cand_id,
                "winning_candidate_id": selected_cand_id,
                "binding_mode": bind_mode,
                "editorially_approved": False,
                "preview_spec_sha256": sel_preview["spec_sha256"],
                "normalized_creative_plan_sha256": sel_preview["normalized_creative_plan_sha256"],
                "preview_mp4_sha256": sel_preview["preview_mp4_sha256"],
                "selection_receipt_sha256": sel_sha,
                "distinct_structural_count": len(cand_preview_receipts),
                "distinct_visual_fingerprint_count": len(vfp_set),
                "skipped_duplicates": skipped_duplicate_cands,
                "candidates_evaluated": cand_preview_receipts,
            }
            provisional_binding["winner_binding_sha256"] = hash_artifact(provisional_binding)
            write_json_atomic(rdir / "manifests" / "PROVISIONAL_BINDING.json", provisional_binding)
            write_json_atomic(rdir / "manifests" / "CANDIDATE_DIVERSITY_AND_BINDING.json", provisional_binding)

        chosen_cv = next((c for c in cand_variants if c.get("candidate_id") == selected_cand_id), {}) if cand_variants else {}
        # Compile canonical FULL-RES `INDEPENDENT_RENDER_SPEC_V2` from ORIGINAL source (NEVER proxy!)
        # Strictly bound to winning candidate's exact normalized creative plan!
        ind_spec = compile_semantic_timeline(
            job_id=f"{run_id}_{selected_cand_id}",
            source_registry=src_reg,
            voice_edl=voice_edl_doc,
            creative_plan=creative_plan,
            layout_plan=layout_plan,
            motion_graph=motion_graph,
            sound_director_plan=snd_res["sound_plan"],
            evidence_ledger=evidence_ledger,
            subject_action_tracks=sat_ind,
            selection_receipt=sel_val["receipt"],
            candidate_binding=provisional_binding or {"provisional_candidate_id": selected_cand_id},
            visual_tracks=chosen_cv.get("visual_tracks", ov_cfg.get("visual_tracks")),
            layers=chosen_cv.get("layers", ov_cfg.get("layers")),
            footage_effects=chosen_cv.get("footage_effects", ov_cfg.get("footage_effects")),
            transitions=chosen_cv.get("transitions", ov_cfg.get("transitions")),
            effect_invocations=chosen_cv.get("effect_invocations", ov_cfg.get("effect_invocations")),
            motion_director_plan=chosen_cv.get("motion_director_plan", ov_cfg.get("motion_director_plan")),
            camera_events=chosen_cv.get("camera_events", ov_cfg.get("camera_events")),
            semantic_overlays=chosen_cv.get("overlays", ov_cfg.get("semantic_overlays")),
            sfx_cues=chosen_cv.get("sfx_cues", ov_cfg.get("sfx_cues")),
            continuity_seams=chosen_cv.get("continuity_seams", ov_cfg.get("continuity_seams")),
            occupancy_windows=chosen_cv.get("occupancy_windows", ov_cfg.get("occupancy_windows")),
            art_tokens=chosen_cv.get("art_tokens", ov_cfg.get("art_tokens")),
            caption_policy=eff_caption,
            prefer_dx01_non_card=eff_prefer_dx01,
            width=out_w,
            height=out_h,
            fps_num=fps_n,
            fps_den=fps_d,
            bgm_enabled=eff_bgm,
            bgm_style=str(chosen_cv.get("bgm_style") or ov_cfg.get("bgm_style") or "warm_calm"),
            bgm_gain_db=float(ov_cfg.get("bgm_gain_db", -24.5)),
            sfx_enabled=eff_sfx,
            sfx_gain_db=float(ov_cfg.get("sfx_gain_db", -16.5)),
            x264_preset="veryfast" if eff_profile == "quick" else ("slow" if eff_profile == "high" else "medium"),
            crf=21 if eff_profile == "quick" else (16 if eff_profile == "high" else 18),
            use_proxy_sources=False,
            production_profile=eff_profile,
            pass_through_allowed=bool(ov_cfg.get("pass_through_allowed", False)),
        )
        if eff_silent_policy:
            ind_spec["silent_policy"] = True
            ind_spec["spec_sha256"] = hash_artifact({k: v for k, v in ind_spec.items() if k != "spec_sha256"})

        # DX01-08 Fail-closed normalized plan hash parity check between winning preview & full-res render!
        if provisional_binding is not None:
            want_norm_sha = provisional_binding.get("normalized_creative_plan_sha256")
            got_norm_sha = ind_spec.get("normalized_creative_plan_sha256")
            if want_norm_sha and got_norm_sha != want_norm_sha:
                transition_state(rdir, STATE_BLOCKED, reason=f"NORMALIZED_PLAN_DRIFT: preview={want_norm_sha[:12]} != final={str(got_norm_sha)[:12]}", run_id=run_id)
                return {
                    "exit_code": 1,
                    "status": "CANDIDATE_PLAN_BINDING_DRIFT",
                    "run_id": run_id,
                    "preview_normalized_sha256": want_norm_sha,
                    "final_normalized_sha256": got_norm_sha,
                }

        write_json_atomic(rdir / "manifests" / "INDEPENDENT_RENDER_SPEC_V2.json", ind_spec)
        write_json_atomic(rdir / "manifests" / "INDEPENDENT_RENDER_SPEC.json", ind_spec)

        out_name = str(ov_cfg.get("output_filename") or f"{run_id}_{skill_id.lower()}_independent_{mode}.mp4")
        out_mp4_ind = rdir / "exports" / out_name
        ind_res = render_independent_video(
            ind_spec, out_mp4=out_mp4_ind, stems_dir=rdir / "stems",
            cache=ind_cache, ffmpeg_threads=ff_thr,
            enable_remotion=eff_enable_remotion,
            remotion_cache_root=rem_cache_dir,
            remotion_concurrency=rem_conc,
        )
        actual_engine_kind = str(ind_res.get("engine_kind") or RENDERER_INDEPENDENT_CLEAN_ROOM)
        write_json_atomic(rdir / "manifests" / "RENDER_PROOF.json", ind_res)
        write_json_atomic(rdir / "manifests" / "CACHE_MANIFEST.json", ind_cache.manifest())

        # Mandatory fail-closed independent/hybrid production QA (NOT decode-only!)
        qa_mode = "INDEPENDENT_PREVIEW" if (mode == "preview" or eff_profile == "quick") else "INDEPENDENT_FULL"
        ind_qa = run_independent_render_qa(
            run_id=run_id,
            run_dir=rdir,
            final_mp4=out_mp4_ind,
            render_spec=ind_spec,
            render_receipt=ind_res,
            reference_script=ov_cfg.get("reference_script") or inp.get("normalized_script"),
            mode=qa_mode,
            license_status=str(routing_decision.get("status") or "AUTHORIZED_INDEPENDENT_CLEAN_ROOM"),
            cache=ind_cache,
            run_post_asr=bool(ov_cfg.get("run_post_asr", False)),
            asr_runner=ov_cfg.get("asr_runner"),
            max_true_peak_dbtp=float(ov_cfg.get("max_true_peak_dbtp", -1.0)),
            min_lufs=float(ov_cfg.get("min_lufs", -22.0)),
            max_lufs=float(ov_cfg.get("max_lufs", -10.0)),
            ffmpeg_threads=ff_thr,
        )
        write_json_atomic(rdir / "manifests" / "CACHE_MANIFEST.json", ind_cache.manifest())

        rec4_doc = {
            "schema_version": "kh03.stage_receipt.v2",
            "stage": "KH03_04_execute_and_qa",
            "run_id": run_id,
            "engine_kind": actual_engine_kind,
            "remotion_used": bool(ind_res.get("remotion_used")),
            "remotion_verified": bool(ind_qa.get("remotion_verified")),
            "production_profile": eff_profile,
            "selected_skill_id": skill_id,
            "selection_receipt_sha256": sel_sha,
            "creative_plan_sha256": creative_plan["plan_sha256"],
            "spec_sha256": ind_spec["spec_sha256"],
            "normalized_creative_plan_sha256": ind_spec.get("normalized_creative_plan_sha256"),
            "output_hashes": {
                "final_mp4_sha256": ind_res["final_mp4_sha256"],
                "spec_sha256": ind_spec["spec_sha256"],
            },
            "evidence_paths": [
                ind_res["final_mp4_path"],
                str(rdir / "manifests" / "INDEPENDENT_RENDER_SPEC_V2.json"),
                str(rdir / "qc" / "FINAL_GATE_MATRIX.json"),
                str(rdir / "qc" / "ISOLATED_MOTION_PROOF.json"),
            ],
            "technical_pass": bool(ind_qa["technical_pass"]),
            "rss_tree": get_process_tree_rss_mib(),
            "elapsed_sec": round(_time.monotonic() - t0_ind, 3),
        }
        write_json_atomic(rdir / "STAGE_RECEIPTS" / "KH03_04_execute_and_qa.json", rec4_doc)

        lifecycle = resolve_editorial_lifecycle(
            rendered_candidates=cand_preview_receipts or [{
                "candidate_id": selected_cand_id,
                "preview_mp4_path": ind_res["final_mp4_path"],
                "preview_mp4_sha256": ind_res["final_mp4_sha256"],
                "preview_qa_pass": bool(ind_qa["technical_pass"]),
            }],
            pairwise_status="PENDING_REVIEW_MISSING_JUDGE",
            owner_override_candidate_id=owner_override_candidate_id,
            live_judge_available=False,
        )
        write_json_atomic(rdir / "qc" / "MANUAL_EDITORIAL_REVIEW.json", {
            "schema_version": "kh03.manual_editorial_review.v2",
            "run_id": run_id,
            "engine_kind": actual_engine_kind,
            "remotion_verified": bool(ind_qa.get("remotion_verified")),
            "production_profile": eff_profile,
            "native_seecut_status": routing_decision["per_renderer_rights"][RENDERER_NATIVE_SEECUT]["status"],
            "lifecycle": lifecycle,
            "provisional_binding": provisional_binding,
            "candidates": cand_preview_receipts,
            "final_mp4_path": ind_res["final_mp4_path"],
            "final_mp4_sha256": ind_res["final_mp4_sha256"],
            "technical_pass": bool(ind_qa["technical_pass"]),
        })
        write_json_atomic(rdir / "manifests" / "MANUAL_EDITORIAL_REVIEW.json", parse_json_strict(rdir / "qc" / "MANUAL_EDITORIAL_REVIEW.json"))
        (rdir / "FINAL_RENDER_SHA256.txt").write_text(f"{ind_res['final_mp4_sha256']}  {out_mp4_ind.name}\n", encoding="utf-8")

        transition_state(rdir, STATE_COMPILED, reason="Canonical INDEPENDENT_RENDER_SPEC_V2 (+DX-01) compiled.", run_id=run_id)
        transition_state(rdir, STATE_RENDERED, reason=f"Rendered via {actual_engine_kind} ({ind_res['final_mp4_sha256'][:12]}).", run_id=run_id)
        transition_state(rdir, STATE_QA, reason=f"Independent QA technical_pass={ind_qa['technical_pass']}.", run_id=run_id)
        if not ind_qa["technical_pass"]:
            transition_state(rdir, STATE_BLOCKED, reason=f"Independent technical QA FAILED: {ind_qa['gate_matrix'].get('failing_gates')}", run_id=run_id)
            return {
                "exit_code": 1,
                "status": "CANDIDATE_QA_FAILED",
                "mode": mode,
                "run_id": run_id,
                "selected_skill_id": skill_id,
                "engine_kind": actual_engine_kind,
                "remotion_verified": bool(ind_qa.get("remotion_verified")),
                "final_mp4_path": ind_res["final_mp4_path"],
                "final_mp4_sha256": ind_res["final_mp4_sha256"],
                "independent_qa": ind_qa,
                "owner_visual": "PENDING",
                "owner_audio": "PENDING",
                "final_approved": False,
            }

        transition_state(rdir, STATE_CANDIDATE, reason=f"Candidate rendered via {actual_engine_kind} & passed technical QA; OWNER_VISUAL/AUDIO PENDING.", run_id=run_id)
        return {
            "exit_code": 0,
            "status": "HYBRID_REMOTION_CANDIDATE_RENDERED" if actual_engine_kind == RENDERER_HYBRID_REMOTION else "INDEPENDENT_CLEAN_ROOM_CANDIDATE_RENDERED",
            "mode": mode,
            "production_profile": eff_profile,
            "run_id": run_id,
            "selected_skill_id": skill_id,
            "selection_receipt_sha256": sel_sha,
            "creative_plan_sha256": creative_plan["plan_sha256"],
            "spec_sha256": ind_spec["spec_sha256"],
            "normalized_creative_plan_sha256": ind_spec.get("normalized_creative_plan_sha256"),
            "engine_kind": actual_engine_kind,
            "remotion_verified": bool(ind_qa.get("remotion_verified")),
            "renderer_routing": routing_decision,
            "provisional_binding": provisional_binding,
            "candidates": cand_preview_receipts,
            "skipped_duplicate_candidates": skipped_duplicate_cands,
            "lifecycle": lifecycle,
            "final_mp4_path": ind_res["final_mp4_path"],
            "final_mp4_sha256": ind_res["final_mp4_sha256"],
            "independent_render": ind_res,
            "independent_qa": ind_qa,
            "owner_visual": "PENDING",
            "owner_audio": "PENDING",
            "final_approved": False,
        }

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

    sb_path = rdir / "SHOTBOOK_V2.json"
    shotbook = parse_json_strict(sb_path) if sb_path.is_file() else None
    final_mp4_raw = kh02_res.get("final_mp4_path")
    final_mp4_exists = bool(final_mp4_raw and Path(final_mp4_raw).is_file())
    final_mp4_sha = hash_artifact(Path(final_mp4_raw)) if final_mp4_exists else None

    if kh02_res.get("exit_code", 1) != 0 and not final_mp4_exists:
        transition_state(rdir, STATE_BLOCKED, reason=f"KH-02 pipeline returned {kh02_res.get('status')}", run_id=run_id)
        return {
            "exit_code": int(kh02_res.get("exit_code", 1)),
            "status": str(kh02_res.get("status", "KH02_PIPELINE_FAIL")),
            "run_id": run_id,
            "selected_skill_id": skill_id,
            "kh02_result": kh02_res,
        }

    transition_state(rdir, STATE_COMPILED, reason="KH-03 plan compiled cleanly through KH-02 pipeline.", run_id=run_id)
    if mode in ("preview", "full") and final_mp4_exists:
        transition_state(rdir, STATE_RENDERED, reason=f"Rendered {final_mp4_raw}", run_id=run_id)

    # 7. KH-03 Visual & Editorial QA (strict shotbook requirement; separates PLAN vs RENDER!)
    vis_qa = audit_fullscreen_and_collisions(
        creative_plan, inp, shotbook=shotbook, require_shotbook=(not dry_run), run_dir=(None if dry_run else rdir),
    )
    transition_state(rdir, STATE_QA, reason="Executing KH-03 editorial & comprehension QA.", run_id=run_id)
    # Reload post-EDL remapped layout_plan & motion_graph from disk if written during Stage E/F
    eff_layout_plan = parse_json_strict(lp_file) if lp_file.is_file() else layout_plan
    eff_motion_graph = parse_json_strict(mg_file) if mg_file.is_file() else motion_graph
    sq_file = rdir / "qc" / "STREAMING_QA_REPORT.json"
    streaming_qa_doc = parse_json_strict(sq_file) if sq_file.is_file() else None
    qa_rep = evaluate_kh03_editorial(
        run_id=run_id,
        creative_plan=creative_plan,
        explanation_graph=explanation_graph,
        footage_manifest=footage_manifest,
        sound_plan=snd_res["sound_plan"],
        visual_qa=vis_qa,
        shotbook=shotbook,
        layout_plan=eff_layout_plan,
        motion_graph=eff_motion_graph,
        evidence_ledger=evidence_ledger,
        visual_density=visual_density,
        tracking_report=tracking_prov,
        streaming_qa=streaming_qa_doc,
        license_decision=eff_lic,
        evidence_paths=[
            str(rdir / "manifests/CREATIVE_PLAN.json"),
            str(rdir / "manifests/MOTION_GRAPH.json"),
            str(rdir / "analysis/EXPLANATION_GRAPH.json"),
            str(rdir / "assets/FOOTAGE_MANIFEST.json"),
            str(rdir / "assets/EVIDENCE_LEDGER.json"),
            str(rdir / "stems/SOUND_DIRECTOR_PLAN.json"),
        ] + ([str(sb_path)] if sb_path.is_file() else []),
        run_dir=(None if dry_run else rdir),
    )

    final_render_qa = None
    if not dry_run and final_mp4_exists and final_mp4_sha:
        # PED-08 P0: Invoke ALL final pixel/audio/action/spatial/layout/export QA gates on EXACT final muxed MP4 SHA-256!
        apw_p = rdir / "analysis" / "ACTION_PROTECTION_WINDOWS.json"
        apw_list = (parse_json_strict(apw_p).get("windows") or []) if apw_p.is_file() else []
        trks_p = rdir / "analysis" / "TRACKS.json"
        trks_list = (parse_json_strict(trks_p).get("entities") or []) if trks_p.is_file() else []
        src_for_qa = kh02_res.get("derived_source_path") or inp.get("video_path")
        _default_up = Path(os.environ.get("SEECUT_UPSTREAM_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut")))
        eff_seecut_root_qa = seecut_root or (_default_up if _default_up.is_dir() else None)
        final_render_qa = orchestrate_final_render_qa(
            run_id=run_id,
            run_dir=rdir,
            final_mp4=final_mp4_raw,
            source_mp4=src_for_qa,
            creative_plan=creative_plan,
            shotbook=shotbook,
            layout_plan=eff_layout_plan,
            motion_graph=eff_motion_graph,
            evidence_ledger=evidence_ledger,
            action_protection_windows=apw_list,
            tracked_entities=trks_list,
            kh02_result=kh02_res,
            fps=fps,
            license_decision=eff_lic,
            engine_kind="NATIVE_SEECUT",
            seecut_root=eff_seecut_root_qa,
        )
        (rdir / "FINAL_RENDER_SHA256.txt").write_text(f"{final_mp4_sha}  {Path(final_mp4_raw).name}\n", encoding="utf-8")
        write_json_atomic(rdir / "qc" / "FINAL_QA.json", {
            "schema_version": "kh03.final_qa.v1",
            "run_id": run_id,
            "mode": mode,
            "final_mp4_path": str(final_mp4_raw),
            "final_mp4_sha256": final_mp4_sha,
            "creative_plan_sha256": creative_plan["plan_sha256"],
            "motion_graph_sha256": (eff_motion_graph or {}).get("graph_sha256"),
            "gates": qa_rep["gates"],
            "final_gate_matrix": final_render_qa["gate_matrix"],
            "technical_pass": final_render_qa["technical_pass"],
            "editorial_candidate_pass": qa_rep["editorial_candidate_pass"] and final_render_qa["technical_pass"],
            "owner_visual": "PENDING",
            "owner_audio": "PENDING",
            "commercial_release": qa_rep["gates"]["COMMERCIAL_RELEASE"],
        })
        (rdir / "OWNER_REVIEW.md").write_text(
            "\n".join([
                f"# Owner Review Checklist — `{run_id}` (`{skill_id}`)",
                "",
                f"- **Final MP4 SHA-256**: `{final_mp4_sha}`",
                f"- **Technical Pixel/Audio QA**: `{final_render_qa['gate_matrix']['overall_technical_status']}`",
                "- **OWNER_VISUAL**: `PENDING` (requires human playback inspection)",
                "- **OWNER_AUDIO**: `PENDING` (requires human listening inspection)",
                f"- **COMMERCIAL_RELEASE**: `{qa_rep['gates']['COMMERCIAL_RELEASE']}`",
                "",
            ]),
            encoding="utf-8",
        )

    # R1.07 + R4 PED-08: Mode-truthful status — rendered mode requires BOTH editorial pass AND final pixel QA pass!
    if mode == "plan":
        if qa_rep["editorial_candidate_pass"]:
            final_status = "KH03_PLAN_PASS"
            exit_c = 0
        else:
            final_status = "KH03_QA_DEFECTS"
            exit_c = 1
    elif mode in ("preview", "full"):
        render_qa_ok = bool(final_render_qa is not None and final_render_qa.get("technical_pass"))
        kh02_ok = int(kh02_res.get("exit_code", 1)) == 0
        if not final_mp4_exists:
            final_status = "KH03_RENDER_INCOMPLETE"
            exit_c = 1
        elif kh02_ok and qa_rep["editorial_candidate_pass"] and render_qa_ok:
            final_status = "KH03_RENDER_CANDIDATE_VERIFIED" if mode == "full" else "KH03_PREVIEW_VERIFIED"
            exit_c = 0
            transition_state(rdir, STATE_CANDIDATE, reason=f"Rendered & verified ({final_status}); owner approvals PENDING.", run_id=run_id)
        else:
            final_status = str(kh02_res.get("status")) if not kh02_ok else "KH03_QA_DEFECTS"
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
            "final_pixel_qa_sha256": hash_artifact(final_render_qa["final_pixel_qa"]) if final_render_qa else None,
        },
        evidence_paths=[str(rdir / "qc/KH03_QA_REPORT.json")]
        + ([str(final_mp4_raw), str(rdir / "qc/FINAL_PIXEL_QA.json"), str(rdir / "qc/FINAL_GATE_MATRIX.json")] if final_mp4_exists and final_render_qa else []),
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
        "final_render_qa": final_render_qa,
        "candidate_decision": candidate_decision,
        "winner_binding": winner_binding_doc,
        "winning_candidate_id": winning_candidate_id,
        "competition_status": competition_status,
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


def run_verify(
    *,
    run_root: Path,
    production_mode: bool = True,
    require_render: bool = False,
    verify_mode: str | None = None,
) -> dict[str, Any]:
    """Fail-closed verifier requiring full KH-03 + KH-02 SHA linkage, winner binding & raw gate recomputation (R4.1-02)."""
    rdir = Path(os.path.realpath(str(run_root.expanduser())))
    issues: list[str] = []
    try:
        sel = validate_user_selection(run_dir=rdir, production_mode=production_mode)
    except Exception as ex:
        issues.append(str(ex))
        sel = None

    ind_spec_p = rdir / "manifests" / "INDEPENDENT_RENDER_SPEC_V2.json"
    is_independent_run = ind_spec_p.is_file() and not (rdir / "SHOTBOOK_V2.json").is_file()

    if is_independent_run:
        required_files = [
            rdir / "analysis" / "INPUT_ANALYSIS.json",
            rdir / "selection" / "SKILL_RECOMMENDATION.json",
            rdir / "selection" / "SELECTION_RECEIPT.json",
            rdir / "manifests" / "CREATIVE_PLAN.json",
            ind_spec_p,
        ]
    else:
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

    if is_independent_run and ind_spec_p.is_file():
        from .semantic_timeline import validate_render_spec
        ind_doc = parse_json_strict(ind_spec_p)
        ind_body = {k: v for k, v in ind_doc.items() if k != "spec_sha256"}
        if ind_doc.get("spec_sha256") and hash_artifact(ind_body) != ind_doc["spec_sha256"]:
            issues.append("TAMPERED_INDEPENDENT_RENDER_SPEC_V2: spec_sha256 does not match body.")
        iv = validate_render_spec(ind_doc)
        if not iv["pass"]:
            issues.append(f"INVALID_INDEPENDENT_RENDER_SPEC_V2: {iv['issues']}")
        pb_p = rdir / "manifests" / "PROVISIONAL_BINDING.json"
        if pb_p.is_file():
            pb_doc = parse_json_strict(pb_p)
            want_norm = pb_doc.get("normalized_creative_plan_sha256")
            got_norm = ind_doc.get("normalized_creative_plan_sha256")
            if want_norm and got_norm and want_norm != got_norm:
                issues.append(f"NORMALIZED_PLAN_DRIFT: PROVISIONAL_BINDING ({want_norm[:12]}) != INDEPENDENT_RENDER_SPEC_V2 ({got_norm[:12]}).")

    cp = rdir / "manifests" / "CREATIVE_PLAN.json"
    sb = rdir / "SHOTBOOK_V2.json"
    wb_p = rdir / "manifests" / "WINNER_BINDING.json"
    if cp.is_file() and sb.is_file():
        plan = parse_json_strict(cp)
        shot = parse_json_strict(sb)
        # Re-verify canonical CREATIVE_PLAN SHA (blocks manual tampering!)
        plan_body = {k: v for k, v in plan.items() if k != "plan_sha256"}
        if plan.get("plan_sha256") and hash_artifact(plan_body) != plan["plan_sha256"]:
            issues.append("TAMPERED_CREATIVE_PLAN: plan_sha256 does not match CREATIVE_PLAN.json body.")
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
        mg_f = rdir / "manifests" / "MOTION_GRAPH.json"
        if plan.get("motion_graph_ref") and not mg_f.is_file():
            issues.append("MISSING_REQUIRED_ARTIFACT: MOTION_GRAPH.json")
        if mg_f.is_file() and prov.get("kh03_motion_graph_sha256"):
            mg_doc = parse_json_strict(mg_f)
            if mg_doc.get("graph_sha256") != prov.get("kh03_motion_graph_sha256"):
                issues.append("MOTION_GRAPH SHA mismatch between MOTION_GRAPH.json and SHOTBOOK_V2 provenance.")
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
        # R4.1-01 Winner Binding SHA chain validation when candidate competition ran
        if wb_p.is_file():
            wb = parse_json_strict(wb_p)
            if not wb.get("bound"):
                issues.append(f"CANDIDATE_WINNER_NOT_BOUND: {wb.get('status')}")
            else:
                if sel and wb.get("selection_receipt_sha256") != sel["receipt_sha256"]:
                    issues.append("WINNER_BINDING selection_receipt_sha256 mismatch.")
                if wb.get("creative_plan_sha256") != plan.get("plan_sha256"):
                    issues.append("WINNER_BINDING creative_plan_sha256 != CREATIVE_PLAN.json plan_sha256.")
                if prov.get("kh03_winning_candidate_id") != wb.get("winning_candidate_id"):
                    issues.append(f"SHOTBOOK_V2 kh03_winning_candidate_id mismatch ({prov.get('kh03_winning_candidate_id')!r} != {wb.get('winning_candidate_id')!r}).")
                if prov.get("kh03_winner_binding_sha256") != wb.get("winner_binding_sha256"):
                    issues.append("SHOTBOOK_V2 kh03_winner_binding_sha256 mismatch.")
                wb_body = {k: v for k, v in wb.items() if k not in ("winner_binding_sha256", "bound_at_utc")}
                if wb.get("winner_binding_sha256") and hash_artifact(wb_body) != wb["winner_binding_sha256"]:
                    issues.append("TAMPERED_WINNER_BINDING: winner_binding_sha256 does not match body.")

    recomputed_matrix = None
    dimensions = {
        "plan_status": "PLAN_VALID" if not issues else "VERIFY_FAIL",
        "technical_status": "NOT_RUN",
        "editorial_status": "NOT_RUN",
        "native_status": "NOT_RUN",
        "owner_visual": "PENDING",
        "owner_audio": "PENDING",
        "release_status": "RELEASE_BLOCKED",
    }

    rec4 = rdir / "STAGE_RECEIPTS" / "KH03_04_execute_and_qa.json"
    if require_render:
        eff_vmode = str(verify_mode or "ENGINEERING").upper()
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
            sha_txt = rdir / "FINAL_RENDER_SHA256.txt"
            if sha_txt.is_file():
                recorded_sha = sha_txt.read_text(encoding="utf-8").strip().split()[0]
                if recorded_sha != mp4_sha or (mp4_paths and mp4_paths[0].is_file() and hash_artifact(mp4_paths[0]) != recorded_sha):
                    issues.append("TAMPERED_FINAL_MP4: FINAL_RENDER_SHA256.txt does not match final MP4 on disk.")
            fpq = rdir / "qc" / "FINAL_PIXEL_QA.json"
            fgm = rdir / "qc" / "FINAL_GATE_MATRIX.json"
            if not fpq.is_file() or not fgm.is_file():
                issues.append("MISSING_FINAL_PIXEL_QA: require_render=True requires FINAL_PIXEL_QA.json and FINAL_GATE_MATRIX.json.")
            else:
                fpq_doc = parse_json_strict(fpq)
                fgm_doc = parse_json_strict(fgm)
                if fpq_doc.get("final_mp4_sha256") != mp4_sha or fgm_doc.get("final_mp4_sha256") != mp4_sha:
                    issues.append("STALE_OR_TAMPERED_FINAL_QA: FINAL_PIXEL_QA/FINAL_GATE_MATRIX SHA does not match final MP4.")

                # R4.1-02: Recompute aggregate_gate_results from raw gate artifacts on disk — NEVER trust stored technical_pass!
                raw_gates = dict(fgm_doc.get("gates") or {})
                prof = resolve_gate_profile(fgm_doc.get("mode") or eff_vmode)
                # Cross-check key raw evidence files on disk against stored gate status & MP4 SHA!
                is_ind_mat = (
                    str(fgm_doc.get("engine_kind") or "").upper() in (RENDERER_INDEPENDENT_CLEAN_ROOM, RENDERER_HYBRID_REMOTION)
                    or str(prof["mode"]).startswith("INDEPENDENT_")
                )
                if is_ind_mat:
                    evidence_file_checks = [
                        ("G_FRAME_AUDIT", rdir / "qc" / "FRAME_AUDIT.json"),
                        ("G_FRAME_COVERAGE", rdir / "qc" / "FRAME_COVERAGE.json"),
                        ("G_COMPONENT_COVERAGE", rdir / "qc" / "COMPONENT_COVERAGE.json"),
                        ("G_SPATIAL_ALIGNMENT", rdir / "qc" / "SPATIAL_QA.json"),
                        ("G_CONTINUITY", rdir / "qc" / "CONTINUITY_QA.json"),
                        ("G_AUDIO_SYNC", rdir / "qc" / "AUDIO_QA.json"),
                    ]
                    iso_p = rdir / "qc" / "ISOLATED_MOTION_PROOF.json"
                    if iso_p.is_file():
                        iso_d = parse_json_strict(iso_p)
                        if iso_d.get("pass") is False and raw_gates.get("G_COMPONENT_COVERAGE", {}).get("status") == "PASS":
                            issues.append("FORGED_GATE_PASS(G_COMPONENT_COVERAGE): ISOLATED_MOTION_PROOF.json reports pass=False.")
                            raw_gates["G_COMPONENT_COVERAGE"] = {**raw_gates["G_COMPONENT_COVERAGE"], "status": "FAIL", "reason": "ISOLATED_MOTION_PROOF_FAILED"}
                else:
                    evidence_file_checks = [
                        ("G_HOOK_PIXELS", rdir / "qc" / "HOOK_PIXEL_QA.json"),
                        ("G_COMPONENT_ROI_MOTION", rdir / "qc" / "COMPONENT_PIXEL_QA.json"),
                        ("G_COMPONENT_COVERAGE", rdir / "qc" / "COMPONENT_COVERAGE.json"),
                        ("G_FRAME_AUDIT", rdir / "qc" / "FRAME_AUDIT.json"),
                        ("G_FRAME_COVERAGE", rdir / "qc" / "FRAME_COVERAGE.json"),
                        ("G_AUDIO_SYNC", rdir / "qc" / "MOTION_SOUND_SYNC.json"),
                        ("G_EXPORT_QA", rdir / "qc" / "EXPORT_QA.json"),
                        ("G_ACTION_PROTECTION", rdir / "qc" / "ACTION_PROTECTION.json"),
                    ]
                for gid_k, ef_p in evidence_file_checks:
                    if gid_k in raw_gates and raw_gates[gid_k].get("status") == "PASS":
                        if ef_p.is_file():
                            ed = parse_json_strict(ef_p)
                            if ed.get("final_mp4_sha256") and ed.get("final_mp4_sha256") != mp4_sha:
                                issues.append(f"STALE_GATE_EVIDENCE({gid_k}): {ef_p.name} MP4 SHA mismatch.")
                                raw_gates[gid_k] = {**raw_gates[gid_k], "status": "FAIL", "reason": "MP4_SHA_MISMATCH"}
                            if ed.get("pass") is False:
                                issues.append(f"FORGED_GATE_PASS({gid_k}): raw {ef_p.name} reports pass=False while matrix claims PASS.")
                                raw_gates[gid_k] = {**raw_gates[gid_k], "status": "FAIL", "reason": "RAW_EVIDENCE_FAILED"}
                        elif gid_k in prof["mandatory_gates"]:
                            issues.append(f"MISSING_RAW_GATE_EVIDENCE({gid_k}): {ef_p.name} missing on disk.")
                            raw_gates[gid_k] = {**raw_gates[gid_k], "status": "BLOCKED", "reason": "MISSING_RAW_EVIDENCE"}

                recomputed_matrix = aggregate_gate_results(
                    raw_gates,
                    mandatory_gates=prof["mandatory_gates"],
                    mode=prof["mode"],
                )
                if recomputed_matrix["technical_pass"] != bool(fgm_doc.get("technical_pass")):
                    issues.append(
                        f"TAMPERED_GATE_MATRIX: stored technical_pass={fgm_doc.get('technical_pass')} != recomputed={recomputed_matrix['technical_pass']} "
                        f"(missing={recomputed_matrix.get('missing_gates')}, failing={recomputed_matrix.get('failing_gates')}, blocked={recomputed_matrix.get('blocked_gates')})"
                    )
                if not recomputed_matrix["technical_pass"]:
                    issues.append(
                        f"FINAL_QA_GATES_FAILED: failing={recomputed_matrix['failing_gates']}, "
                        f"blocked={recomputed_matrix['blocked_gates']}, missing={recomputed_matrix['missing_gates']}, pending={recomputed_matrix['pending_gates']}"
                    )
                dimensions["technical_status"] = recomputed_matrix["overall_technical_status"]
                dimensions["editorial_status"] = recomputed_matrix.get("editorial_status", "PENDING_REVIEW")
                dimensions["native_status"] = recomputed_matrix.get("native_status", "NOT_RUN")
                dimensions["release_status"] = recomputed_matrix.get("release_status", "RELEASE_BLOCKED")

                # Verify OWNER_APPROVAL.json binds exact MP4 SHA
                oa_p = rdir / "qc" / "OWNER_APPROVAL.json"
                if oa_p.is_file():
                    oa = parse_json_strict(oa_p)
                    if oa.get("final_mp4_sha256") != mp4_sha:
                        issues.append("STALE_OWNER_APPROVAL: OWNER_APPROVAL.json final_mp4_sha256 mismatch.")
                    dimensions["owner_visual"] = str(oa.get("owner_visual_approval") or "PENDING")
                    dimensions["owner_audio"] = str(oa.get("owner_audio_approval") or "PENDING")

    return {
        "exit_code": 0 if not issues else 1,
        "status": "VERIFY_PASS" if not issues else "VERIFY_FAIL",
        "dimensions": dimensions,
        "recomputed_gate_matrix": recomputed_matrix,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.kh03", description="KH-03 Universal Editing Intelligence CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_pre = sub.add_parser("preflight", help="Run mode-aware environment, native pin, license & readiness preflight")
    p_pre.add_argument("--run-root", type=Path, default=None)
    p_pre.add_argument("--mode", choices=["plan", "preview", "full", "release"], default="plan")
    p_pre.add_argument("--purpose", choices=sorted(_VALID_PURPOSES), default="NONCOMMERCIAL_ENGINEERING")
    p_pre.add_argument("--commercial-permission-evidence", default=None)
    p_pre.add_argument("--compete-candidates", action="store_true", default=False)
    p_pre.add_argument("--online-opt-in", action="store_true", default=False)
    p_pre.add_argument("--export-layers", action="store_true", default=False)
    p_pre.add_argument("--renderer", choices=list(VALID_RENDERER_MODES), default="auto")
    p_pre.add_argument("--seecut-root", type=Path, default=None)
    p_pre.add_argument("--tools-root", type=Path, default=None)

    p_rec = sub.add_parser("recommend", help="Analyze video + optional secondary sources + script and issue 5-skill menu (STOPS for user selection)")
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
    p_rec.add_argument("--secondary-video", action="append", default=None, help="Repeatable path to secondary evidence video (MUTE_DEFAULT audio)")
    p_rec.add_argument("--secondary-role", default="secondary_screen_evidence")
    p_rec.add_argument("--secondary-audio-policy", choices=["MUTE_DEFAULT", "AUTHORIZED_STEM_APPROVED"], default="MUTE_DEFAULT")
    p_rec.add_argument("--sources-manifest", type=Path, default=None, help="JSON manifest describing secondary sources (path/role/audio_policy/rights)")
    p_rec.add_argument("--voice-master", default="primary_aroll")
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
    p_exe.add_argument("--max-memory-mib", type=int, default=None)
    p_exe.add_argument("--queue-frames", type=int, default=2)
    p_exe.add_argument("--enable-tracking", action="store_true", default=None)
    p_exe.add_argument("--disable-tracking", action="store_true", default=False)
    p_exe.add_argument("--reduced-motion", action="store_true", default=False)
    p_exe.add_argument("--compete-candidates", action="store_true", default=False)
    p_exe.add_argument("--max-candidates", type=int, default=3)
    p_exe.add_argument("--owner-override-candidate", default=None)
    p_exe.add_argument("--owner-selection-receipt", type=Path, default=None)
    p_exe.add_argument("--online-opt-in", action="store_true", default=False)
    p_exe.add_argument("--run-h2", action="store_true", default=False)
    p_exe.add_argument("--compare-baseline", action="append", type=Path, default=None)
    p_exe.add_argument("--export-layers", action="store_true", default=False)
    p_exe.add_argument("--renderer", choices=list(VALID_RENDERER_MODES), default="native_seecut")
    p_exe.add_argument("--production-profile", choices=["quick", "standard", "high"], default="standard")
    p_exe.add_argument("--cache-dir", type=Path, default=None)
    p_exe.add_argument("--job-brief", type=Path, default=None, help="Optional JSON job brief with semantic overlays/camera/art tokens")
    p_exe.add_argument("--seecut-root", type=Path, default=None)
    p_exe.add_argument("--tools-root", type=Path, default=None)

    p_st = sub.add_parser("status", help="Inspect KH-03 state machine and binding receipt")
    p_st.add_argument("--run-root", required=True, type=Path)

    p_cap = sub.add_parser("capabilities", help="Report live installed Remotion backend and DX-01 effect registry capabilities")
    p_cap.add_argument("--remotion-workspace", type=Path, default=None)

    p_vf = sub.add_parser("verify", help="Verify selection receipt and KH-03 -> KH-02 SHA propagation")
    p_vf.add_argument("--run-root", required=True, type=Path)
    p_vf.add_argument("--require-render", action="store_true", default=False)

    args = ap.parse_args(argv)
    if args.cmd == "capabilities":
        out = run_capabilities(workspace_override=args.remotion_workspace)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "preflight":
        out = run_preflight(
            run_dir=args.run_root,
            mode=args.mode,
            intended_purpose=args.purpose,
            commercial_permission_evidence=args.commercial_permission_evidence,
            compete_candidates=args.compete_candidates,
            online_opt_in=args.online_opt_in,
            export_layers=args.export_layers,
            renderer=args.renderer,
            seecut_root=args.seecut_root,
            tools_root=args.tools_root,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out["pass"] else 1
    if args.cmd == "recommend":
        sec_list: list[dict[str, Any]] = []
        if args.sources_manifest is not None:
            m_doc = parse_json_strict(args.sources_manifest)
            entries = m_doc.get("secondary_sources") if isinstance(m_doc, dict) else (m_doc if isinstance(m_doc, list) else [])
            for idx_m, ent in enumerate(entries or []):
                if isinstance(ent, dict) and ent.get("path"):
                    sec_list.append({
                        "source_id": str(ent.get("source_id") or ent.get("asset_id") or f"secondary_{idx_m + 1:02d}"),
                        "path": str(ent["path"]),
                        "role": str(ent.get("role") or args.secondary_role),
                        "audio_policy": str(ent.get("audio_policy") or args.secondary_audio_policy),
                        "rights": str(ent.get("rights") or "OWNER_SUPPLIED_NONCOMMERCIAL"),
                    })
        for idx_s, sp in enumerate(args.secondary_video or []):
            sec_list.append({
                "source_id": f"secondary_{len(sec_list) + idx_s + 1:02d}",
                "path": str(sp),
                "role": args.secondary_role,
                "audio_policy": args.secondary_audio_policy,
            })
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
            secondary_sources=sec_list or None,
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
        owner_sel_rec = parse_json_strict(args.owner_selection_receipt) if args.owner_selection_receipt else None
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
            max_memory_mib=args.max_memory_mib,
            queue_frames=args.queue_frames,
            enable_tracking=(False if args.disable_tracking else args.enable_tracking),
            reduced_motion=args.reduced_motion,
            compete_candidates=args.compete_candidates,
            max_candidates=args.max_candidates,
            owner_override_candidate_id=args.owner_override_candidate,
            owner_selection_receipt=owner_sel_rec,
            offline=(not args.online_opt_in),
            online_opt_in=args.online_opt_in,
            run_h2=args.run_h2,
            compare_baselines=args.compare_baseline,
            export_layers=args.export_layers,
            renderer=args.renderer,
            production_profile=args.production_profile,
            cache_dir=args.cache_dir,
            independent_spec_overrides=(parse_json_strict(args.job_brief) if args.job_brief else None),
            seecut_root=args.seecut_root,
            tools_root=args.tools_root,
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
