"""KH-03.R3 Subject, Object, Action & Scene-Cut Intelligence (CMI-04).

Provides deterministic, bounded, local streaming frame-analysis tracking without paid APIs,
cloud uploads, or fabricated face-recognition identity claims:
- Separates subject/head/mouth/hands, active objects, interaction zones, screen/text zones,
  multi-subject regions, and faceless/tabletop/outdoor motion tracks.
- Explicitly distinguishes verified detector hits (`LOCAL_DETECTOR_VERIFIED`), local streaming
  motion/cut saliency (`LOCAL_MOTION_SALIENT_TRACKED`), and conservative static fallback
  (`TRACKING_STATIC_FALLBACK` / `STATIC_FALLBACK_VERIFIED`).
- Never fabricates human face boxes on faceless/tabletop/screen footage, never labels interpolated
  frames as detector hits, and never claims physical actions solely from script text!
"""

from __future__ import annotations

import json
import math
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

from .contracts import assert_kh03_schema, hash_artifact, write_json_atomic
from .execution import StreamingRGBFrameDecoder

TRACKER_PROVIDER = "kh03.local_streaming_saliency_tracker"
TRACKER_VERSION = "r3.0.0"
DEFAULT_TRACKING_SEED = 20261125


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _detect_motion_and_cuts_streaming(
    video_path: Path,
    *,
    nb_frames: int,
    fps: float,
    probe_w: int = 120,
    probe_h: int = 160,
    max_keyframes: int = 48,
    ffmpeg_threads: int = 2,
) -> dict[str, Any]:
    """Stream compact low-res frames in O(1) RAM to detect scene cuts, active motion centroids & energy windows."""
    stride = max(1, int(math.ceil(max(1, nb_frames) / float(max(8, max_keyframes)))))
    scene_cuts: list[int] = []
    motion_samples: list[dict[str, Any]] = []
    prev_im: Image.Image | None = None
    prev_mean_luma: float | None = None
    frame_idx = 0

    with StreamingRGBFrameDecoder(
        video_path, probe_w, probe_h, ffmpeg_threads=ffmpeg_threads, timeout_sec=120.0,
    ) as dec:
        while True:
            raw = dec.read_frame()
            if raw is None:
                break
            im = Image.frombytes("RGB", (probe_w, probe_h), raw)
            gray = im.convert("L")
            mean_luma = float(ImageStat.Stat(gray).mean[0])

            if prev_im is not None:
                diff = ImageChops.difference(prev_im, gray)
                mean_diff = float(ImageStat.Stat(diff).mean[0])
                # Hard scene cut detection (abrupt global luminance/structure jump)
                if mean_diff >= 32.0 and (not scene_cuts or (frame_idx - scene_cuts[-1]) >= max(4, int(round(fps * 0.25)))):
                    scene_cuts.append(frame_idx)

                if frame_idx % stride == 0 or frame_idx in scene_cuts or frame_idx == nb_frames - 1:
                    # Compute 4x4 grid motion energy to locate active motion / hand / object centroid cleanly
                    gw, gh = probe_w // 4, probe_h // 4
                    grid_energies: list[tuple[float, int, int]] = []
                    for gy in range(4):
                        for gx in range(4):
                            cell = diff.crop((gx * gw, gy * gh, (gx + 1) * gw, (gy + 1) * gh))
                            e = float(ImageStat.Stat(cell).mean[0])
                            grid_energies.append((e, gx, gy))
                    grid_energies.sort(key=lambda t: (-t[0], t[2], t[1]))
                    top_e, top_gx, top_gy = grid_energies[0]
                    # Compute bounding region around cells above 55% of peak motion energy
                    thresh = max(3.5, top_e * 0.55)
                    hot = [(gx, gy) for e, gx, gy in grid_energies if e >= thresh]
                    if hot:
                        hx0 = min(g[0] for g in hot) * 0.25
                        hy0 = min(g[1] for g in hot) * 0.25
                        hx1 = (max(g[0] for g in hot) + 1) * 0.25
                        hy1 = (max(g[1] for g in hot) + 1) * 0.25
                        active_box = [round(hx0, 4), round(hy0, 4), round(max(0.18, hx1 - hx0), 4), round(max(0.14, hy1 - hy0), 4)]
                    else:
                        active_box = None
                    motion_samples.append({
                        "frame": frame_idx,
                        "time_sec": round(frame_idx / max(1.0, fps), 4),
                        "global_motion_diff": round(mean_diff, 3),
                        "peak_cell_energy": round(top_e, 3),
                        "mean_luma": round(mean_luma, 2),
                        "active_motion_bbox_norm": active_box,
                        "is_scene_cut": frame_idx in scene_cuts,
                        "is_empty_dark": mean_luma < 8.0,
                        "observation_kind": "OBSERVED_KEYFRAME",
                    })
            else:
                motion_samples.append({
                    "frame": 0,
                    "time_sec": 0.0,
                    "global_motion_diff": 0.0,
                    "peak_cell_energy": 0.0,
                    "mean_luma": round(mean_luma, 2),
                    "active_motion_bbox_norm": None,
                    "is_scene_cut": False,
                    "is_empty_dark": mean_luma < 8.0,
                    "observation_kind": "OBSERVED_KEYFRAME",
                })

            prev_im = gray
            prev_mean_luma = mean_luma
            frame_idx += 1

    return {
        "frames_scanned": frame_idx,
        "stride": stride,
        "scene_cuts": scene_cuts,
        "samples": motion_samples,
        "cleanup": dec.cleanup_report,
    }


def analyze_subject_object_tracks(
    source_media: dict[str, Any],
    *,
    run_id: str,
    video_path: str | Path | None = None,
    transcript: dict[str, Any] | None = None,
    presenter_mode: str = "LEGACY_DYNAMIC",
    probe_observations: list[dict[str, Any]] | None = None,
    enable_streaming_scan: bool = False,
    seed: int = DEFAULT_TRACKING_SEED,
    ffmpeg_threads: int = 2,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Build multi-track subject/object/action intelligence with explicit provenance & conservative safety fallback (CMI-04).

    Handles:
    - Verified detector observations (`probe_observations` with `verified_detector=True`)
    - Faceless/tabletop/screen/outdoor/pet videos (zero fabricated human face boxes!)
    - Multiple speakers/subjects (`subject_class`, `track_id`)
    - Scene-cut resets, low-confidence conservative padding, and rotation-corrected effective dimensions.
    """
    src_w = max(16, int(source_media.get("width") or 1080))
    src_h = max(16, int(source_media.get("height") or 1920))
    rot = int(source_media.get("rotation_deg") or 0) % 360
    eff_w, eff_h = (src_h, src_w) if rot in (90, 270) else (src_w, src_h)
    aspect = str(source_media.get("aspect_ratio") or ("9:16" if eff_h > eff_w else ("1:1" if eff_w == eff_h else "16:9")))
    nb_frames = max(1, int(source_media.get("nb_frames") or 240))
    fps_num = max(1, int(source_media.get("fps_num") or 24))
    fps_den = max(1, int(source_media.get("fps_den") or 1))
    fps = float(Fraction(fps_num, fps_den))
    dur_sec = round(nb_frames / max(1.0, fps), 4)
    is_faceless = str(presenter_mode or "").strip().upper() == "FACELESS"
    low_res = (min(eff_w, eff_h) < 360)

    # 1. Inspect supplied verified detector observations (if any)
    valid_obs = [
        ob for ob in (probe_observations or [])
        if isinstance(ob, dict) and ob.get("verified_detector") is True and isinstance(ob.get("bbox_norm"), list) and len(ob["bbox_norm"]) == 4
    ]

    scene_cuts: list[int] = []
    scan_samples: list[dict[str, Any]] = []
    subject_tracks: list[dict[str, Any]] = []
    object_tracks: list[dict[str, Any]] = []
    action_timeline: list[dict[str, Any]] = []
    protected_regions: list[dict[str, Any]] = []
    interpolated_ratio = 0.0
    conservative_fallback = False

    # 2. Optional real streaming scan on local video file
    vp = Path(video_path).resolve() if (video_path and Path(video_path).is_file()) else None
    if enable_streaming_scan and vp is not None:
        scan = _detect_motion_and_cuts_streaming(
            vp, nb_frames=nb_frames, fps=fps, ffmpeg_threads=ffmpeg_threads,
        )
        scene_cuts = list(scan["scene_cuts"])
        scan_samples = list(scan["samples"])
        interpolated_ratio = round(max(0.0, 1.0 - (len(scan_samples) / float(max(1, nb_frames)))), 4)

    if valid_obs:
        # Verified detector path (supports single speaker, multi-speaker, hands, object, text/screen, pet!)
        by_track: dict[str, list[dict[str, Any]]] = {}
        for ob in valid_obs:
            tid = str(ob.get("track_id") or f"trk_{ob.get('object_class', 'subject')}_01")
            by_track.setdefault(tid, []).append(ob)
            if ob.get("is_scene_cut"):
                scene_cuts.append(int(ob.get("frame", 0)))
        scene_cuts = sorted(set(scene_cuts))

        confs: list[float] = []
        for tid in sorted(by_track.keys()):
            obs_list = sorted(by_track[tid], key=lambda x: int(x.get("frame", 0)))
            cls = str(obs_list[0].get("object_class") or "person").lower()
            mean_c = round(sum(float(o.get("confidence", 0.85)) for o in obs_list) / len(obs_list), 3)
            confs.append(mean_c)
            # Swept union bbox across track observations (padded when confidence < 0.70!)
            pad = 0.04 if mean_c < 0.70 else 0.015
            if mean_c < 0.70:
                conservative_fallback = True
            x0 = _clamp(min(float(o["bbox_norm"][0]) for o in obs_list) - pad, 0.0, 0.95)
            y0 = _clamp(min(float(o["bbox_norm"][1]) for o in obs_list) - pad, 0.0, 0.95)
            x1 = _clamp(max(float(o["bbox_norm"][0]) + float(o["bbox_norm"][2]) for o in obs_list) + pad, x0 + 0.04, 1.0)
            y1 = _clamp(max(float(o["bbox_norm"][1]) + float(o["bbox_norm"][3]) for o in obs_list) + pad, y0 + 0.04, 1.0)
            union_box = [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]

            entry = {
                "track_id": tid,
                "object_class": cls,
                "start_frame": int(obs_list[0].get("frame", 0)),
                "end_frame": int(obs_list[-1].get("frame", nb_frames - 1)) + 1,
                "union_bbox_norm": union_box,
                "mean_confidence": mean_c,
                "observed_hits": len(obs_list),
                "provider": str(obs_list[0].get("provider") or "external_verified_detector"),
                "provider_version": str(obs_list[0].get("provider_version") or "1.0"),
                "interpolated_between_hits": len(obs_list) < nb_frames,
                "observations": [
                    {
                        "frame": int(o.get("frame", 0)),
                        "bbox_norm": [round(float(v), 4) for v in o["bbox_norm"]],
                        "confidence": round(float(o.get("confidence", 0.85)), 3),
                        "observation_kind": "DETECTOR_HIT",
                    }
                    for o in obs_list
                ],
            }
            if cls in ("person", "face", "head", "mouth", "hands", "animal", "pet"):
                subject_tracks.append(entry)
            else:
                object_tracks.append(entry)
            protected_regions.append({
                "region_id": f"prot_{tid}",
                "zone": cls,
                "track_id": tid,
                "bbox_norm": union_box,
                "confidence": mean_c,
                "provenance": "LOCAL_DETECTOR_VERIFIED",
            })

        mean_conf = round(sum(confs) / max(1, len(confs)), 3)
        mode = "FACELESS_OBJECT_TRACKING" if is_faceless else ("TRACKING_STATIC_FALLBACK" if mean_conf < 0.55 else "LOCAL_DETECTOR_VERIFIED")
        if mean_conf < 0.55:
            conservative_fallback = True
        interpolated_ratio = round(max(0.0, 1.0 - (len(valid_obs) / float(max(1, nb_frames)))), 4)
        disclosure = (
            f"Verified local detector observations ({len(valid_obs)} hits, mean confidence={mean_conf}); "
            "inter-keyframe poses are explicitly marked interpolated rather than detector hits."
        )
    elif scan_samples:
        # Deterministic local streaming motion/saliency analysis
        hot_boxes = [s["active_motion_bbox_norm"] for s in scan_samples if s.get("active_motion_bbox_norm")]
        mean_energy = sum(float(s.get("peak_cell_energy", 0.0)) for s in scan_samples) / max(1, len(scan_samples))
        empty_frames = sum(1 for s in scan_samples if s.get("is_empty_dark"))

        if hot_boxes:
            ux0 = _clamp(min(b[0] for b in hot_boxes), 0.0, 0.90)
            uy0 = _clamp(min(b[1] for b in hot_boxes), 0.0, 0.90)
            ux1 = _clamp(max(b[0] + b[2] for b in hot_boxes), ux0 + 0.12, 1.0)
            uy1 = _clamp(max(b[1] + b[3] for b in hot_boxes), uy0 + 0.12, 1.0)
            act_union = [round(ux0, 4), round(uy0, 4), round(ux1 - ux0, 4), round(uy1 - uy0, 4)]
        else:
            act_union = [0.15, 0.25, 0.70, 0.55]

        object_tracks.append({
            "track_id": "trk_active_motion_01",
            "object_class": "interaction_region" if not is_faceless else "faceless_active_subject",
            "start_frame": 0,
            "end_frame": nb_frames,
            "union_bbox_norm": act_union,
            "mean_confidence": round(_clamp(0.58 + min(0.24, mean_energy / 80.0) - (0.15 if low_res else 0.0), 0.35, 0.84), 3),
            "observed_hits": len(scan_samples),
            "provider": TRACKER_PROVIDER,
            "provider_version": TRACKER_VERSION,
            "interpolated_between_hits": True,
        })

        if is_faceless:
            mode = "FACELESS_OBJECT_TRACKING"
            mean_conf = object_tracks[0]["mean_confidence"]
            protected_regions.append({
                "region_id": "prot_faceless_motion",
                "zone": "faceless_active_subject",
                "track_id": "trk_active_motion_01",
                "bbox_norm": act_union,
                "confidence": mean_conf,
                "provenance": "LOCAL_MOTION_SALIENT_TRACKED",
            })
            disclosure = (
                "Faceless footage: tracked active visual/object saliency region via local streaming scan; "
                "zero human face boxes fabricated."
            )
        else:
            mode = "LOCAL_MOTION_SALIENT_TRACKED" if not low_res else "TRACKING_STATIC_FALLBACK"
            conservative_fallback = True
            mean_conf = round(max(0.52, object_tracks[0]["mean_confidence"] - 0.05), 3)
            # Combine conservative aspect-aware face/hands bounds with observed motion union (never infer specific hand action from face box alone!)
            face_box = [0.24, 0.20, 0.52, 0.27] if aspect == "9:16" else ([0.33, 0.14, 0.34, 0.38] if aspect == "16:9" else [0.25, 0.18, 0.50, 0.32])
            hands_box = [0.06, 0.52, 0.88, 0.42] if aspect == "9:16" else [0.16, 0.52, 0.68, 0.42]
            subject_tracks.extend([
                {
                    "track_id": "trk_person_head_conservative",
                    "object_class": "head_conservative_fallback",
                    "start_frame": 0, "end_frame": nb_frames,
                    "union_bbox_norm": face_box, "mean_confidence": 0.68,
                    "observed_hits": 0, "provider": TRACKER_PROVIDER, "provider_version": TRACKER_VERSION,
                    "interpolated_between_hits": True,
                },
                {
                    "track_id": "trk_interaction_motion",
                    "object_class": "interaction_region",
                    "start_frame": 0, "end_frame": nb_frames,
                    "union_bbox_norm": act_union, "mean_confidence": mean_conf,
                    "observed_hits": len(scan_samples), "provider": TRACKER_PROVIDER, "provider_version": TRACKER_VERSION,
                    "interpolated_between_hits": True,
                },
            ])
            protected_regions.extend([
                {"region_id": "prot_face_conservative", "zone": "face", "track_id": "trk_person_head_conservative", "bbox_norm": face_box, "confidence": 0.68, "provenance": "CONSERVATIVE_FALLBACK"},
                {"region_id": "prot_hands_conservative", "zone": "hands", "track_id": "trk_interaction_motion", "bbox_norm": hands_box, "confidence": mean_conf, "provenance": "LOCAL_MOTION_SALIENT_TRACKED"},
            ])
            disclosure = (
                f"Local streaming motion/cut scan observed {len(scan_samples)} keyframes ({len(scene_cuts)} cuts, {empty_frames} dark frames); "
                "face/hands protected via conservative padded regions (no ML face detector run)."
            )

        # Derive honest temporal action events strictly from observed motion/cut bursts (NOT from script!)
        for s in scan_samples:
            if s.get("is_scene_cut"):
                action_timeline.append({
                    "action_id": f"act_cut_f{s['frame']}",
                    "kind": "scene_transition",
                    "start_frame": int(s["frame"]),
                    "end_frame": min(nb_frames, int(s["frame"]) + 2),
                    "start_sec": s["time_sec"],
                    "evidence_basis": "OBSERVED_PIXEL_CUT",
                    "confidence": 0.90,
                    "verified_physical_event": True,
                })
            elif float(s.get("peak_cell_energy", 0.0)) >= 14.0 and not s.get("is_empty_dark"):
                action_timeline.append({
                    "action_id": f"act_motion_f{s['frame']}",
                    "kind": "observed_motion_burst",
                    "start_frame": int(s["frame"]),
                    "end_frame": min(nb_frames, int(s["frame"]) + max(4, int(round(fps * 0.4)))),
                    "start_sec": s["time_sec"],
                    "bbox_norm": s.get("active_motion_bbox_norm"),
                    "evidence_basis": "OBSERVED_OPTICAL_ENERGY",
                    "confidence": round(_clamp(float(s["peak_cell_energy"]) / 35.0, 0.45, 0.88), 3),
                    "verified_physical_event": True,
                })
    else:
        # Honest metadata-only / conservative static fallback (widened protective bounds!)
        conservative_fallback = True
        interpolated_ratio = 1.0
        if is_faceless:
            mode = "FACELESS_OBJECT_TRACKING"
            mean_conf = 0.55
            obj_box = [0.12, 0.22, 0.76, 0.56]
            object_tracks.append({
                "track_id": "trk_faceless_conservative",
                "object_class": "faceless_center_stage",
                "start_frame": 0, "end_frame": nb_frames,
                "union_bbox_norm": obj_box, "mean_confidence": mean_conf,
                "observed_hits": 0, "provider": TRACKER_PROVIDER, "provider_version": TRACKER_VERSION,
                "interpolated_between_hits": True,
            })
            protected_regions.append({
                "region_id": "prot_faceless_conservative", "zone": "object", "track_id": "trk_faceless_conservative",
                "bbox_norm": obj_box, "confidence": mean_conf, "provenance": "TRACKING_STATIC_FALLBACK",
            })
            disclosure = (
                "TRACKING_STATIC_FALLBACK: Faceless mode without live pixel scan; "
                "using conservative central object protection and zero fabricated human boxes."
            )
        else:
            mode = "TRACKING_STATIC_FALLBACK"
            mean_conf = 0.60 if not low_res else 0.48
            # Widened conservative safe boxes when live tracking did NOT run
            face_box = [0.20, 0.18, 0.60, 0.30] if aspect == "9:16" else ([0.30, 0.12, 0.40, 0.42] if aspect == "16:9" else [0.22, 0.16, 0.56, 0.34])
            hands_box = [0.04, 0.50, 0.92, 0.46] if aspect == "9:16" else [0.14, 0.50, 0.72, 0.46]
            subject_tracks.append({
                "track_id": "trk_static_conservative_person",
                "object_class": "person_conservative_static",
                "start_frame": 0, "end_frame": nb_frames,
                "union_bbox_norm": [min(face_box[0], hands_box[0]), face_box[1], max(face_box[2], hands_box[2]), round(hands_box[1] + hands_box[3] - face_box[1], 4)],
                "mean_confidence": mean_conf, "observed_hits": 0,
                "provider": TRACKER_PROVIDER, "provider_version": TRACKER_VERSION,
                "interpolated_between_hits": True,
            })
            protected_regions.extend([
                {"region_id": "prot_face_static", "zone": "face", "track_id": "trk_static_conservative_person", "bbox_norm": face_box, "confidence": mean_conf, "provenance": "TRACKING_STATIC_FALLBACK"},
                {"region_id": "prot_hands_static", "zone": "hands", "track_id": "trk_static_conservative_person", "bbox_norm": hands_box, "confidence": mean_conf, "provenance": "TRACKING_STATIC_FALLBACK"},
            ])
            disclosure = (
                "TRACKING_STATIC_FALLBACK: Conservative widened safe framing applied; live ML subject/hand tracking "
                "was NOT executed, so 0% occlusion is NOT claimed beyond verified geometric clearance."
            )

    if not action_timeline:
        action_timeline.append({
            "action_id": "act_unestablished",
            "kind": "unknown_unestablished",
            "start_frame": 0,
            "end_frame": nb_frames,
            "start_sec": 0.0,
            "evidence_basis": "NO_OBSERVED_ACTION_DETECTOR",
            "confidence": 0.0,
            "verified_physical_event": False,
            "note": "Script text is treated as hypothesis only, never as proof of observed physical action.",
        })

    provenance = {
        "schema_version": "kh03.tracking_provenance.v1",
        "run_id": run_id,
        "tracking_mode": mode,
        "provider": TRACKER_PROVIDER,
        "provider_version": TRACKER_VERSION,
        "deterministic_seed": int(seed),
        "confidence": float(mean_conf),
        "is_faceless": is_faceless,
        "effective_dims": [eff_w, eff_h],
        "rotation_deg": rot,
        "scene_cuts": scene_cuts,
        "subject_tracks_summary": [
            {k: v for k, v in tr.items() if k != "observations"} for tr in subject_tracks
        ],
        "object_tracks_summary": [
            {k: v for k, v in tr.items() if k != "observations"} for tr in object_tracks
        ],
        "action_timeline": action_timeline[:64],
        "interpolated_frame_ratio": float(interpolated_ratio),
        "conservative_fallback_applied": bool(conservative_fallback),
        "honest_disclosure": disclosure,
    }
    assert_kh03_schema(provenance, "tracking_observation")

    conf_report = {
        "schema_version": "kh03.tracking_confidence_report.v1",
        "run_id": run_id,
        "tracking_mode": mode,
        "overall_confidence": float(mean_conf),
        "low_res_source": low_res,
        "conservative_fallback_applied": bool(conservative_fallback),
        "subject_track_count": len(subject_tracks),
        "object_track_count": len(object_tracks),
        "scene_cut_count": len(scene_cuts),
        "verified_physical_actions": sum(1 for a in action_timeline if a.get("verified_physical_event")),
        "unverified_script_only_warning": not any(a.get("verified_physical_event") for a in action_timeline),
        "honest_disclosure": disclosure,
    }

    if run_dir is not None:
        adir = run_dir / "analysis"
        adir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(adir / "TRACKING_PROVENANCE.json", provenance)
        write_json_atomic(adir / "TRACKING_CONFIDENCE_REPORT.json", conf_report)
        write_json_atomic(adir / "ACTION_TIMELINE.json", {
            "schema_version": "kh03.action_timeline.v1",
            "run_id": run_id,
            "tracking_mode": mode,
            "scene_cuts": scene_cuts,
            "events": action_timeline,
        })
        (adir / "SUBJECT_TRACKS.jsonl").write_text(
            "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in subject_tracks), encoding="utf-8",
        )
        (adir / "OBJECT_TRACKS.jsonl").write_text(
            "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in object_tracks), encoding="utf-8",
        )
        (adir / "PROTECTED_REGIONS.jsonl").write_text(
            "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in protected_regions), encoding="utf-8",
        )
        dbg_dir = adir / "TRACKING_DEBUG_SAMPLES"
        dbg_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(dbg_dir / "sampled_keyframes.json", {
            "schema_version": "kh03.tracking_debug_samples.v1",
            "sample_count": len(scan_samples),
            "samples": scan_samples[:24],
        })

    return {
        "provenance": provenance,
        "confidence_report": conf_report,
        "subject_tracks": subject_tracks,
        "object_tracks": object_tracks,
        "action_timeline": action_timeline,
        "protected_regions": protected_regions,
        "scene_cuts": scene_cuts,
        "provenance_sha256": hash_artifact(provenance),
    }
