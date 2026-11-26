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
import os
import platform
import shutil
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

from .contracts import assert_kh03_schema, hash_artifact, write_json_atomic
from .execution import StreamingRGBFrameDecoder, run_subprocess_isolated

TRACKER_PROVIDER = "kh03.local_streaming_saliency_tracker"
TRACKER_VERSION = "r4.0.0"
DEFAULT_TRACKING_SEED = 20261125

_VISION_BATCH_SWIFT = r"""
import Foundation
import Vision

let args = Array(CommandLine.arguments.dropFirst())
var results: [[String: Any]] = []
for raw in args {
    let parts = raw.split(separator: "|", maxSplits: 1).map(String.init)
    guard parts.count == 2, let fidx = Int(parts[0]) else { continue }
    let url = URL(fileURLWithPath: parts[1])
    let handler = VNImageRequestHandler(url: url, options: [:])
    let faceReq = VNDetectFaceRectanglesRequest()
    let handReq = VNDetectHumanHandPoseRequest()
    handReq.maximumHandCount = 4
    let textReq = VNRecognizeTextRequest()
    textReq.recognitionLevel = .fast
    textReq.usesLanguageCorrection = false
    do {
        try handler.perform([faceReq, handReq, textReq])
    } catch {
        continue
    }
    var ents: [[String: Any]] = []
    for f in (faceReq.results ?? []) {
        let b = f.boundingBox
        let x = max(0.0, min(0.98, Double(b.origin.x)))
        let y = max(0.0, min(0.98, 1.0 - Double(b.origin.y) - Double(b.size.height)))
        let w = max(0.02, min(1.0 - x, Double(b.size.width)))
        let h = max(0.02, min(1.0 - y, Double(b.size.height)))
        ents.append(["role": "face", "bbox_norm": [x, y, w, h], "confidence": Double(f.confidence), "source": "macos_vision_face"])
    }
    for hObs in (handReq.results ?? []) {
        if let pts = try? hObs.recognizedPoints(.all) {
            let valid = pts.values.filter { $0.confidence > 0.25 }
            if valid.count >= 4 {
                let xs = valid.map { Double($0.location.x) }
                let ys = valid.map { 1.0 - Double($0.location.y) }
                let x0 = max(0.0, (xs.min() ?? 0.0) - 0.03)
                let y0 = max(0.0, (ys.min() ?? 0.0) - 0.03)
                let x1 = min(1.0, (xs.max() ?? 1.0) + 0.03)
                let y1 = min(1.0, (ys.max() ?? 1.0) + 0.03)
                let meanC = valid.map { Double($0.confidence) }.reduce(0.0, +) / Double(valid.count)
                ents.append(["role": "hand", "bbox_norm": [x0, y0, max(0.03, x1 - x0), max(0.03, y1 - y0)], "confidence": meanC, "source": "macos_vision_hand_pose"])
            }
        }
    }
    let textObs = (textReq.results ?? []).filter { $0.confidence > 0.45 }
    if textObs.count >= 2 {
        var tx0 = 1.0, ty0 = 1.0, tx1 = 0.0, ty1 = 0.0
        var csum = 0.0
        for t in textObs.prefix(12) {
            let b = t.boundingBox
            let x = Double(b.origin.x), y = 1.0 - Double(b.origin.y) - Double(b.size.height)
            tx0 = min(tx0, x); ty0 = min(ty0, y)
            tx1 = max(tx1, x + Double(b.size.width)); ty1 = max(ty1, y + Double(b.size.height))
            csum += Double(t.confidence)
        }
        if tx1 > tx0 && ty1 > ty0 {
            ents.append(["role": "screen_text", "bbox_norm": [max(0.0, tx0), max(0.0, ty0), min(1.0 - max(0.0, tx0), tx1 - tx0), min(1.0 - max(0.0, ty0), ty1 - ty0)], "confidence": csum / Double(min(12, textObs.count)), "source": "macos_vision_text"])
        }
    }
    results.append(["frame": fidx, "entities": ents])
}
if let data = try? JSONSerialization.data(withJSONObject: results, options: []), let txt = String(data: data, encoding: .utf8) {
    print(txt)
}
"""


def resolve_detector_capabilities(*, allow_macos_vision: bool = True, out_dir: Path | None = None) -> dict[str, Any]:
    """Audit available real local visual detectors with honest OS provenance, license & non-semantic motion limits (R4.1-04)."""
    sys_low = platform.system().lower()
    is_macos = sys_low == "darwin"
    swift_bin = shutil.which("swift") if (is_macos and allow_macos_vision) else None
    has_cv2 = False
    cv2_ver = None
    has_mp = False
    mp_ver = None
    try:
        mod_cv2 = __import__("cv2")
        has_cv2 = True
        cv2_ver = getattr(mod_cv2, "__version__", "unknown")
    except Exception:
        pass
    try:
        mod_mp = __import__("mediapipe")
        has_mp = True
        mp_ver = getattr(mod_mp, "__version__", "unknown")
    except Exception:
        pass
    primary = (
        "MACOS_VISION_DETECTOR" if swift_bin
        else ("OPENCV_DETECTOR" if has_cv2 else "NUMPY_STREAMING_MOTION_DETECTOR")
    )
    if swift_bin:
        cap_class = "SEMANTIC_FACE_HAND_TEXT_KEYFRAME_DETECTOR"
        sem_obj_cls = True
        roles = ["face", "hand", "screen_text", "motion_region"]
        limits = [
            "Apple Vision detects face rectangles, hand pose keypoints, and screen text on sampled keyframes; does NOT classify arbitrary physical objects.",
            "Inter-keyframe intervals are temporally associated/interpolated, never labeled detector hits.",
        ]
    elif has_cv2:
        cap_class = "OPENCV_LOCAL_NON_SEMANTIC_OR_CASCADE"
        sem_obj_cls = False
        roles = ["motion_region"]
        limits = ["OpenCV installed without pinned semantic object weights; optical motion regions only."]
    else:
        cap_class = "MOTION_SALIENT_REGION_NON_SEMANTIC"
        sem_obj_cls = False
        roles = ["motion_region"]
        limits = [
            f"Apple Vision unavailable on OS={platform.system()}; no OpenCV/MediaPipe semantic weights installed.",
            "NumPy/Pillow streaming motion detects luminance/pixel change clusters ONLY — NEVER claims semantic object identity (hands, envelopes, tools, devices).",
        ]

    caps = {
        "schema_version": "kh03.detector_capabilities.v2",
        "os": platform.system(),
        "platform": platform.platform(),
        "primary_detector": primary,
        "capability_class": cap_class,
        "semantic_object_classification": sem_obj_cls,
        "macos_vision_available": bool(swift_bin),
        "macos_vision_status": "AVAILABLE" if swift_bin else f"UNAVAILABLE_ON_{platform.system().upper()}",
        "swift_executable": swift_bin,
        "opencv_available": has_cv2,
        "opencv_version": cv2_ver,
        "mediapipe_available": has_mp,
        "mediapipe_version": mp_ver,
        "streaming_motion_available": True,
        "offline_supported": True,
        "supported_roles": roles,
        "limitations": limits,
        "license_note": (
            "Apple macOS system Vision framework + local Pillow/NumPy streaming motion (zero unlicensed external weights)."
            if swift_bin else "Local Pillow/NumPy streaming motion & edge saliency (zero external downloads; non-semantic motion clusters only)."
        ),
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(out_dir / "DETECTOR_CAPABILITIES.json", caps)
    return caps


def _run_macos_vision_batch(
    video_path: Path,
    sample_frames: list[int],
    *,
    fps: float,
    probe_w: int = 360,
    probe_h: int = 640,
    timeout_sec: float = 45.0,
) -> list[dict[str, Any]]:
    """Run real Apple Vision face/hand/text detection on bounded sampled frames in an isolated staging dir."""
    caps = resolve_detector_capabilities()
    if not caps["macos_vision_available"] or not sample_frames:
        return []
    wanted = sorted({max(0, int(f)) for f in sample_frames})[:16]
    tmp_dir = Path(tempfile.mkdtemp(prefix="kh03_vision_"))
    try:
        swift_file = tmp_dir / "detect.swift"
        swift_file.write_text(_VISION_BATCH_SWIFT.strip() + "\n", encoding="utf-8")
        frame_args: list[str] = []
        max_f = max(wanted)
        idx = 0
        with StreamingRGBFrameDecoder(video_path, probe_w, probe_h, ffmpeg_threads=2, timeout_sec=60.0) as dec:
            while idx <= max_f:
                raw = dec.read_frame()
                if raw is None:
                    break
                if idx in wanted:
                    im = Image.frombytes("RGB", (probe_w, probe_h), raw)
                    jpg = tmp_dir / f"f_{idx:05d}.jpg"
                    im.save(jpg, format="JPEG", quality=88)
                    frame_args.append(f"{idx}|{jpg}")
                idx += 1
        if not frame_args:
            return []
        res = run_subprocess_isolated(
            [str(caps["swift_executable"]), str(swift_file), *frame_args],
            timeout_sec=timeout_sec,
        )
        if res["returncode"] != 0 or not res["stdout"].strip():
            return []
        parsed = json.loads(res["stdout"].strip().splitlines()[-1])
        out: list[dict[str, Any]] = []
        for item in parsed:
            fidx = int(item.get("frame", 0))
            for idx_e, ent in enumerate(item.get("entities") or []):
                bb = ent.get("bbox_norm")
                if not isinstance(bb, list) or len(bb) != 4:
                    continue
                role = str(ent.get("role") or "object")
                out.append({
                    "frame": fidx,
                    "time_sec": round(fidx / max(1.0, fps), 4),
                    "track_id": f"vis_{role}_{idx_e + 1:02d}",
                    "object_class": "hands" if role == "hand" else role,
                    "role": role,
                    "bbox_norm": [round(float(v), 4) for v in bb],
                    "confidence": round(float(ent.get("confidence", 0.75)), 3),
                    "verified_detector": True,
                    "provider": str(ent.get("source") or "macos_vision"),
                    "provider_version": "macos_vision_1.0",
                })
        return out
    except Exception:
        return []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def detect_semantic_entities(
    video_path: str | Path | None,
    source_media: dict[str, Any],
    *,
    presenter_mode: str = "LEGACY_DYNAMIC",
    probe_observations: list[dict[str, Any]] | None = None,
    use_macos_vision: bool = True,
    max_keyframes: int = 12,
    ffmpeg_threads: int = 2,
) -> dict[str, Any]:
    """Detect real semantic entities (face, hand, object, screen_text, motion_region) with honest provenance (PED-02)."""
    nb_frames = max(1, int(source_media.get("nb_frames") or 240))
    fps = float(Fraction(max(1, int(source_media.get("fps_num") or 24)), max(1, int(source_media.get("fps_den") or 1))))
    dur_sec = nb_frames / max(1.0, fps)
    is_faceless = str(presenter_mode or "").strip().upper() == "FACELESS"
    caps = resolve_detector_capabilities(allow_macos_vision=use_macos_vision)

    # 1. Explicit verified observations take precedence (real detector fixtures / pre-computed passes)
    valid_ext = [
        ob for ob in (probe_observations or [])
        if isinstance(ob, dict) and ob.get("verified_detector") is True and isinstance(ob.get("bbox_norm"), list) and len(ob["bbox_norm"]) == 4
    ]
    raw_hits: list[dict[str, Any]] = list(valid_ext)
    detector_used = "INJECTED_VERIFIED_DETECTOR" if valid_ext else "NONE"
    scene_cuts: list[int] = [int(c) for c in (source_media.get("scene_cuts") or [])]
    scan_samples: list[dict[str, Any]] = []

    vp = Path(video_path).resolve() if (video_path and Path(video_path).is_file()) else None
    if not raw_hits and vp is not None:
        # Always run fast O(1) streaming motion + scene-cut scan
        scan = _detect_motion_and_cuts_streaming(
            vp, nb_frames=nb_frames, fps=fps, max_keyframes=max(12, max_keyframes * 2), ffmpeg_threads=ffmpeg_threads,
        )
        scene_cuts = list(scan["scene_cuts"])
        scan_samples = list(scan["samples"])

        # Run real macOS Apple Vision batch detector on sampled frames when available
        if caps["macos_vision_available"] and use_macos_vision:
            n_samp = min(max_keyframes, max(4, int(round(dur_sec / 2.2))))
            samp_frames = sorted({int(round(i * max(0, nb_frames - 1) / max(1, n_samp - 1))) for i in range(n_samp)})
            # Reset around scene cuts by sampling right after cuts too
            for c_f in scene_cuts[:4]:
                if c_f < nb_frames:
                    samp_frames.append(c_f)
            vis_hits = _run_macos_vision_batch(vp, sorted(set(samp_frames))[:14], fps=fps)
            if is_faceless:
                vis_hits = [h for h in vis_hits if h.get("role") != "face"]
            if vis_hits:
                raw_hits.extend(vis_hits)
                detector_used = "MACOS_VISION_DETECTOR"

        if not raw_hits and scan_samples:
            detector_used = "NUMPY_STREAMING_MOTION_DETECTOR"
            for s in scan_samples:
                bb = s.get("active_motion_bbox_norm")
                if bb and float(s.get("peak_cell_energy", 0.0)) >= 4.0:
                    raw_hits.append({
                        "frame": int(s["frame"]),
                        "time_sec": float(s["time_sec"]),
                        "track_id": "mot_region_01",
                        "object_class": "motion_salient_region_non_semantic",
                        "role": "motion_region",
                        "semantic_classification_verified": False,
                        "bbox_norm": list(bb),
                        "confidence": round(_clamp(0.55 + float(s["peak_cell_energy"]) / 85.0, 0.40, 0.85), 3),
                        "verified_detector": False,
                        "provider": TRACKER_PROVIDER,
                        "provider_version": TRACKER_VERSION,
                    })

    # Per-instance temporal association with scene-cut resets (R4.1-04: never merges distinct instances by role!)
    grouped = _associate_temporal_instances(raw_hits, scene_cuts=scene_cuts)

    entities: list[dict[str, Any]] = []
    for eid in sorted(grouped.keys()):
        hits = sorted(grouped[eid], key=lambda x: int(x.get("frame", 0)))
        role = str(hits[0].get("role") or hits[0].get("object_class") or "motion_region").lower()
        x0 = _clamp(min(float(x["bbox_norm"][0]) for x in hits), 0.0, 0.98)
        y0 = _clamp(min(float(x["bbox_norm"][1]) for x in hits), 0.0, 0.98)
        x1 = _clamp(max(float(x["bbox_norm"][0]) + float(x["bbox_norm"][2]) for x in hits), x0 + 0.02, 1.0)
        y1 = _clamp(max(float(x["bbox_norm"][1]) + float(x["bbox_norm"][3]) for x in hits), y0 + 0.02, 1.0)
        mean_c = round(sum(float(x.get("confidence", 0.65)) for x in hits) / len(hits), 3)
        source_pre_cropped = (x0 <= 0.015 or y0 <= 0.015 or x1 >= 0.985 or y1 >= 0.985)
        imp = 1.0 if role in ("face", "hand", "hands", "mouth") else (0.9 if role in ("screen_text", "screen", "object") else 0.75)
        f_first = int(hits[0].get("frame", 0))
        f_last = int(hits[-1].get("frame", nb_frames - 1))
        span_len = max(1, (f_last - f_first) + 1)
        obs_cnt = len({int(x.get("frame", 0)) for x in hits})
        interp_cnt = max(0, span_len - obs_cnt)
        unk_cnt = max(0, nb_frames - span_len)
        entities.append({
            "id": eid,
            "role": "hand" if role == "hands" else role,
            "semantic_class": str(hits[0].get("object_class") or role),
            "scene_id": int(hits[0].get("scene_id", 0)),
            "time_span": [round(f_first / max(1.0, fps), 4), round((f_last + 1) / max(1.0, fps), 4)],
            "frame_span": [f_first, f_last + 1],
            "bbox_norm": [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)],
            "coordinate_space": "SOURCE_NORMALIZED_TOP_LEFT",
            "source": str(hits[0].get("provider") or detector_used),
            "capability_class": caps["capability_class"] if not any(x.get("verified_detector") for x in hits) else "VERIFIED_DETECTOR_OBSERVATIONS",
            "verified_detector": bool(any(x.get("verified_detector") for x in hits)),
            "confidence": mean_c,
            "occlusion": 0.0,
            "semantic_importance": imp,
            "uncertainty": round(max(0.01, (1.0 - mean_c) * 0.08), 4),
            "estimated": obs_cnt < nb_frames,
            "source_pre_cropped": source_pre_cropped,
            "temporal_coverage": {
                "observed_frame_count": obs_cnt,
                "interpolated_frame_count": interp_cnt,
                "unknown_frame_count": unk_cnt,
                "observed_coverage_ratio": round(obs_cnt / float(max(1, nb_frames)), 4),
            },
            "observations": [
                {"frame": int(x.get("frame", 0)), "bbox_norm": [round(float(v), 4) for v in x["bbox_norm"]], "confidence": round(float(x.get("confidence", 0.65)), 3)}
                for x in hits
            ],
        })

    return {
        "schema_version": "kh03.tracked_entities.v2",
        "detector_capabilities": caps,
        "detector_used": detector_used,
        "capability_class": caps["capability_class"],
        "sampling_mode": "ADAPTIVE_KEYFRAME_SAMPLED" if len(scan_samples) < nb_frames else "FULL_FRAME",
        "scene_cuts": scene_cuts,
        "scan_samples": scan_samples,
        "raw_hits": raw_hits,
        "entities": entities,
    }


def build_action_protection_windows(
    entities: list[dict[str, Any]],
    source_media: dict[str, Any],
    *,
    scene_cuts: list[int] | None = None,
    min_confidence: float = 0.45,
) -> list[dict[str, Any]]:
    """Convert tracked entities into time-indexed critical action protection windows (PED-02)."""
    nb_frames = max(1, int(source_media.get("nb_frames") or 240))
    fps = float(Fraction(max(1, int(source_media.get("fps_num") or 24)), max(1, int(source_media.get("fps_den") or 1))))
    windows: list[dict[str, Any]] = []
    for idx, ent in enumerate(entities):
        conf = float(ent.get("confidence", 0.0))
        if conf < min_confidence:
            continue
        role = str(ent.get("role") or "object")
        pad = float(ent.get("uncertainty", 0.015)) + (0.03 if conf < 0.70 else 0.012)
        bx, by, bw, bh = [float(v) for v in ent["bbox_norm"]]
        px0 = _clamp(bx - pad, 0.0, 0.96)
        py0 = _clamp(by - pad, 0.0, 0.96)
        if not ent.get("verified_detector") and role == "face" and by >= 0.18:
            # Conservative unverified face placeholders must not bleed up into the top 16.5% safe header zone;
            # verified detector face boxes are NEVER clamped.
            py0 = max(0.165, py0)
        px1 = _clamp(bx + bw + pad, px0 + 0.03, 1.0)
        py1 = _clamp(by + bh + pad, py0 + 0.03, 1.0)
        f0, f1 = ent.get("frame_span") or [0, nb_frames]
        windows.append({
            "window_id": f"apw_{idx + 1:02d}_{role}",
            "entity_id": ent["id"],
            "role": role,
            "start_frame": max(0, int(f0)),
            "end_frame": min(nb_frames, max(int(f0) + 1, int(f1))),
            "start_sec": round(max(0, int(f0)) / max(1.0, fps), 4),
            "end_sec": round(min(nb_frames, max(int(f0) + 1, int(f1))) / max(1.0, fps), 4),
            "protected_bbox_norm": [round(px0, 4), round(py0, 4), round(px1 - px0, 4), round(py1 - py0, 4)],
            "confidence": round(conf, 3),
            "verified_detector": bool(ent.get("verified_detector")),
            "source_pre_cropped": bool(ent.get("source_pre_cropped")),
            "source_limitation": "SOURCE_BORDER_PRE_CROPPED" if ent.get("source_pre_cropped") else None,
            "scene_cuts_inside": [c for c in (scene_cuts or []) if int(f0) < c < int(f1)],
        })
    return windows


def _rect_intersection_area(a: list[float], b: list[float]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return (ix1 - ix0) * (iy1 - iy0)


def _box_iou_norm(a: list[float], b: list[float]) -> float:
    inter = _rect_intersection_area(a, b)
    if inter <= 0.0:
        return 0.0
    union = max(1e-9, a[2] * a[3] + b[2] * b[3] - inter)
    return inter / union


def _associate_temporal_instances(
    raw_hits: list[dict[str, Any]],
    *,
    scene_cuts: list[int],
    max_gap_frames: int = 48,
) -> dict[str, list[dict[str, Any]]]:
    """Per-instance temporal association respecting scene-cut resets (never merges distinct hands/faces into 1 giant box!)."""
    cuts_sorted = sorted(set(int(c) for c in scene_cuts if int(c) > 0))

    def _scene_id(fidx: int) -> int:
        sid = 0
        for c in cuts_sorted:
            if fidx >= c:
                sid += 1
        return sid

    # Explicit pre-labeled track_ids stay distinct per scene segment
    hits_sorted = sorted(raw_hits, key=lambda h: (int(h.get("frame", 0)), str(h.get("role") or ""), float((h.get("bbox_norm") or [0])[0])))
    tracks: dict[str, list[dict[str, Any]]] = {}
    track_meta: dict[str, dict[str, Any]] = {}
    role_counters: dict[str, int] = {}

    for h in hits_sorted:
        fidx = int(h.get("frame", 0))
        sid = _scene_id(fidx)
        role = str(h.get("role") or h.get("object_class") or "motion_region").lower()
        if role == "hands":
            role = "hand"
        bb = [float(v) for v in h["bbox_norm"]]
        cx, cy = bb[0] + bb[2] * 0.5, bb[1] + bb[3] * 0.5
        explicit_tid = h.get("track_id")

        # If caller gave an explicit distinct verified track_id (not auto-generated vis_role_idx), honor per scene!
        if explicit_tid and not str(explicit_tid).startswith("vis_"):
            key = f"{explicit_tid}_s{sid}" if sid > 0 else str(explicit_tid)
            tracks.setdefault(key, []).append({**h, "scene_id": sid})
            track_meta[key] = {"role": role, "scene_id": sid, "last_frame": fidx, "last_bbox": bb, "cx": cx, "cy": cy}
            continue

        # Match to best active track of same role in SAME scene_id (strict scene-cut reset!)
        best_key: str | None = None
        best_score = -1.0
        for tid_k, meta in track_meta.items():
            if meta["role"] != role or meta["scene_id"] != sid:
                continue
            dt = fidx - int(meta["last_frame"])
            if dt <= 0 or dt > max_gap_frames:
                continue
            iou = _box_iou_norm(bb, meta["last_bbox"])
            dist = math.hypot(cx - meta["cx"], cy - meta["cy"])
            if iou >= 0.15 or dist <= 0.18:
                sc = iou * 2.0 + max(0.0, 0.25 - dist)
                if sc > best_score:
                    best_score = sc
                    best_key = tid_k

        if best_key is None:
            role_counters[role] = role_counters.get(role, 0) + 1
            best_key = f"trk_{role}_{role_counters[role]:02d}"
        tracks.setdefault(best_key, []).append({**h, "scene_id": sid})
        track_meta[best_key] = {"role": role, "scene_id": sid, "last_frame": fidx, "last_bbox": bb, "cx": cx, "cy": cy}

    return tracks
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return (ix1 - ix0) * (iy1 - iy0)


def _map_src_span_via_edl(sf: int, ef: int, edl: dict[str, Any] | None) -> tuple[int, int] | None:
    """Map source frame span [sf, ef) onto output EDL frame span; returns None if completely trimmed out."""
    if not edl or not edl.get("segments"):
        return sf, ef
    lo, hi = None, None
    for seg in edl["segments"]:
        s0, s1 = int(seg["src_start_frame"]), int(seg["src_end_frame"])
        o0 = int(seg["out_start_frame"])
        ov0, ov1 = max(sf, s0), min(ef, s1)
        if ov1 > ov0:
            ms, me = o0 + (ov0 - s0), o0 + (ov1 - s0)
            lo = ms if lo is None else min(lo, ms)
            hi = me if hi is None else max(hi, me)
    if lo is None or hi is None or hi <= lo:
        return None
    return lo, hi


def _map_out_frame_to_src(out_f: int, edl: dict[str, Any] | None) -> int:
    if not edl or not edl.get("segments"):
        return out_f
    for seg in edl["segments"]:
        o0, o1 = int(seg["out_start_frame"]), int(seg["out_end_frame"])
        s0 = int(seg["src_start_frame"])
        if o0 <= out_f < o1:
            return s0 + (out_f - o0)
    return out_f


def evaluate_action_visibility(
    final_mp4: str | Path | None,
    protection_windows: list[dict[str, Any]],
    *,
    layout_plan: dict[str, Any] | None = None,
    overlay_boxes: list[dict[str, Any]] | None = None,
    source_mp4: str | Path | None = None,
    edl: dict[str, Any] | None = None,
    require_edl_mapping: bool = False,
    min_retained_area: float = 0.95,
    max_overlay_coverage: float = 0.05,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Final-pixel & exact crop-transform action visibility gate (R4.1-04 / PED-02).

    - Synchronizes source protection windows through EDL source->output PTS mapping (`BLOCKED_CLOCK_MAPPING` if mismatched/absent).
    - Evaluates actual transformed crop/split/PIP geometry AND pixel compositing across ALL layouts.
    - Distinguishes `VERIFIED_ACTION_PROTECTION` (>=0.95 retained & <=0.05 overlay on verified tracks) from
      `SAFE_FALLBACK_GEOMETRY_ONLY` (unverified conservative placeholders — NEVER silently certified as detector-verified!).
    """
    if not protection_windows:
        rep = {
            "schema_version": "kh03.action_protection.v2",
            "status": "NOT_APPLICABLE_NO_CRITICAL_REGIONS",
            "certification": "NOT_APPLICABLE_NO_CRITICAL_REGIONS",
            "windows_checked": 0,
            "violations": [],
            "pass": True,
        }
        if out_dir is not None:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            write_json_atomic(Path(out_dir) / "ACTION_PROTECTION.json", rep)
        return rep

    # Verify EDL source->output clock mapping before pixel comparison!
    if require_edl_mapping and not (isinstance(edl, dict) and edl.get("segments")):
        rep_blk = {
            "schema_version": "kh03.action_protection.v2",
            "status": "BLOCKED_CLOCK_MAPPING",
            "certification": "BLOCKED_CLOCK_MAPPING",
            "pixel_level_verified": False,
            "windows_checked": 0,
            "violations": [{"window_id": "CLOCK", "role": "edl", "reason": "Missing EDL source->output PTS mapping."}],
            "pass": False,
        }
        if out_dir is not None:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            write_json_atomic(Path(out_dir) / "ACTION_PROTECTION.json", rep_blk)
        return rep_blk

    beats = (layout_plan or {}).get("beats") or []
    transitions = (layout_plan or {}).get("transitions") or []
    overlays = list(overlay_boxes or [])
    checked: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []

    def _settled_frame_in_span(bs_f: int, be_f: int) -> int:
        # Avoid sampling inside an active CSS layout morph transition where presenter_box_norm is mid-interpolation
        settled_lo = bs_f
        for tr in transitions:
            ts, te = int(tr.get("start_frame", -1)), int(tr.get("end_frame", -1))
            if ts <= settled_lo < te:
                settled_lo = max(settled_lo, te + 1)
        if settled_lo >= be_f:
            return max(bs_f, be_f - 1)
        return min(be_f - 1, (settled_lo + be_f) // 2)

    # Map every window onto output clock via EDL
    mapped_windows: list[tuple[dict[str, Any], int, int]] = []
    for w in protection_windows:
        sf_src, ef_src = int(w["start_frame"]), int(w["end_frame"])
        sp_out = _map_src_span_via_edl(sf_src, ef_src, edl)
        if sp_out is not None:
            mapped_windows.append((w, sp_out[0], sp_out[1]))

    fin_frames: dict[int, Image.Image] = {}
    src_frames: dict[int, Image.Image] = {}
    out_sample_set: set[int] = set()
    src_sample_set: set[int] = set()
    for _w, sf_o, ef_o in mapped_windows:
        for f_o in (sf_o, (sf_o + ef_o) // 2, max(sf_o, ef_o - 1)):
            out_sample_set.add(f_o)
            src_sample_set.add(_map_out_frame_to_src(f_o, edl))
        for b in beats:
            bs_b, be_b = max(sf_o, int(b["start_frame"])), min(ef_o, int(b["end_frame"]))
            if be_b > bs_b:
                sf_set = _settled_frame_in_span(bs_b, be_b)
                out_sample_set.add(sf_set)
                src_sample_set.add(_map_out_frame_to_src(sf_set, edl))

    pw, ph = 270, 480
    clock_mismatch = False
    if final_mp4 and Path(final_mp4).is_file() and source_mp4 and Path(source_mp4).is_file() and out_sample_set:
        # Check whether source vs final lengths diverge without an EDL mapping!
        if not (isinstance(edl, dict) and edl.get("segments")):
            from .visual_qa import _probe_video_meta
            try:
                sm = _probe_video_meta(Path(source_mp4))
                fm = _probe_video_meta(Path(final_mp4))
                if sm.get("nb_frames") and fm.get("nb_frames") and abs(int(sm["nb_frames"]) - int(fm["nb_frames"])) > 2:
                    clock_mismatch = True
            except Exception:
                pass

        if clock_mismatch:
            rep_cm = {
                "schema_version": "kh03.action_protection.v2",
                "final_mp4_sha256": hash_artifact(Path(final_mp4)),
                "status": "BLOCKED_CLOCK_MAPPING",
                "certification": "BLOCKED_CLOCK_MAPPING",
                "pixel_level_verified": False,
                "windows_checked": 0,
                "violations": [{"window_id": "CLOCK", "role": "edl", "reason": "Source and final frame counts diverge with no EDL mapping supplied."}],
                "pass": False,
            }
            if out_dir is not None:
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                write_json_atomic(Path(out_dir) / "ACTION_PROTECTION.json", rep_cm)
            return rep_cm

        for path_obj, target_dict, wanted_set in (
            (Path(final_mp4), fin_frames, out_sample_set),
            (Path(source_mp4), src_frames, src_sample_set),
        ):
            max_f = max(wanted_set)
            idx = 0
            with StreamingRGBFrameDecoder(path_obj, pw, ph, ffmpeg_threads=2, timeout_sec=90.0) as dec:
                while idx <= max_f:
                    raw = dec.read_frame()
                    if raw is None:
                        break
                    if idx in wanted_set:
                        target_dict[idx] = Image.frombytes("RGB", (pw, ph), raw)
                    idx += 1

    pixel_samples_evaluated = 0
    any_unverified_placeholder = False

    for w, sf, ef in mapped_windows:
        mid_f = (sf + ef) // 2
        src_box = [float(v) for v in w["protected_bbox_norm"]]
        area_src = max(1e-6, src_box[2] * src_box[3])
        is_verified = bool(w.get("verified_detector") is True)
        is_conservative_placeholder = bool(not is_verified)
        if is_conservative_placeholder:
            any_unverified_placeholder = True

        overlapping_beats = [b for b in beats if int(b["end_frame"]) > sf and int(b["start_frame"]) < ef]
        if not overlapping_beats:
            overlapping_beats = [{"start_frame": sf, "end_frame": ef, "layout_mode": "FULL_CANVAS", "presenter_box_norm": [0.0, 0.0, 1.0, 1.0]}]

        worst_retained = 1.0
        worst_ov_cov = 0.0
        worst_canvas_box = list(src_box)
        pixel_diff_ratio = None

        for b in overlapping_beats:
            bs = max(sf, int(b["start_frame"]))
            be = min(ef, int(b["end_frame"]))
            if be <= bs:
                continue
            p_box = [float(v) for v in b.get("presenter_box_norm", [0.0, 0.0, 1.0, 1.0])]
            crop = None
            if isinstance(b.get("source_crop"), dict) and b["source_crop"].get("crop_rect_norm"):
                crop = [float(v) for v in b["source_crop"]["crop_rect_norm"]]
                inter_crop = _rect_intersection_area(src_box, crop)
                b_retained = round(inter_crop / area_src, 4)
                vx0 = max(src_box[0], crop[0])
                vy0 = max(src_box[1], crop[1])
                vx1 = min(src_box[0] + src_box[2], crop[0] + crop[2])
                vy1 = min(src_box[1] + src_box[3], crop[1] + crop[3])
                if vx1 > vx0 and vy1 > vy0:
                    cx0 = p_box[0] + ((vx0 - crop[0]) / max(1e-6, crop[2])) * p_box[2]
                    cy0 = p_box[1] + ((vy0 - crop[1]) / max(1e-6, crop[3])) * p_box[3]
                    cw_b = ((vx1 - vx0) / max(1e-6, crop[2])) * p_box[2]
                    ch_b = ((vy1 - vy0) / max(1e-6, crop[3])) * p_box[3]
                    c_box = [round(cx0, 4), round(cy0, 4), round(cw_b, 4), round(ch_b, 4)]
                else:
                    c_box = [0.0, 0.0, 0.0, 0.0]
            else:
                b_retained = 1.0
                c_box = [
                    round(p_box[0] + src_box[0] * p_box[2], 4),
                    round(p_box[1] + src_box[1] * p_box[3], 4),
                    round(src_box[2] * p_box[2], 4),
                    round(src_box[3] * p_box[3], 4),
                ]

            if b_retained < worst_retained:
                worst_retained = b_retained
                worst_canvas_box = c_box

            area_c = max(1e-6, c_box[2] * c_box[3])
            for ov in overlays:
                if ov.get("intentional_highlight"):
                    continue
                osf, oef = int(ov.get("start_frame", 0)), int(ov.get("end_frame", 10**9))
                if oef <= bs or osf >= be:
                    continue
                obb = ov.get("bbox_norm") or ov.get("normalized_bbox")
                if isinstance(obb, list) and len(obb) == 4:
                    cov = round(_rect_intersection_area(c_box, [float(v) for v in obb]) / area_c, 4)
                    if cov > worst_ov_cov:
                        worst_ov_cov = cov
                        worst_canvas_box = c_box

            # Pixel comparison supporting BOTH FULL_CANVAS AND split/PIP/inset transforms via registered crop+resize!
            # Sample a settled post-morph frame inside [bs, be) so smooth layout transitions are never misread as occlusion.
            eval_f = _settled_frame_in_span(bs, be)
            src_mid = _map_out_frame_to_src(eval_f, edl)
            if bs <= eval_f < be and eval_f in fin_frames and src_mid in src_frames and c_box[2] > 0.02 and c_box[3] > 0.02:
                fin_im = fin_frames[eval_f]
                src_im = src_frames[src_mid]
                if crop is not None:
                    scx0 = max(0, int(round(crop[0] * pw)))
                    scy0 = max(0, int(round(crop[1] * ph)))
                    scx1 = min(pw, max(scx0 + 4, int(round((crop[0] + crop[2]) * pw))))
                    scy1 = min(ph, max(scy0 + 4, int(round((crop[1] + crop[3]) * ph))))
                    pb_w = max(4, int(round(p_box[2] * pw)))
                    pb_h = max(4, int(round(p_box[3] * ph)))
                    reg_src = src_im.crop((scx0, scy0, scx1, scy1)).resize((pb_w, pb_h), Image.BILINEAR)
                    pb_x0 = max(0, int(round(p_box[0] * pw)))
                    pb_y0 = max(0, int(round(p_box[1] * ph)))
                    reg_fin = fin_im.crop((pb_x0, pb_y0, min(pw, pb_x0 + pb_w), min(ph, pb_y0 + pb_h))).resize((pb_w, pb_h), Image.BILINEAR)
                    rx0 = max(0, int(round((c_box[0] - p_box[0]) / max(1e-6, p_box[2]) * pb_w)))
                    ry0 = max(0, int(round((c_box[1] - p_box[1]) / max(1e-6, p_box[3]) * pb_h)))
                    rx1 = min(pb_w, max(rx0 + 2, int(round((c_box[0] + c_box[2] - p_box[0]) / max(1e-6, p_box[2]) * pb_w))))
                    ry1 = min(pb_h, max(ry0 + 2, int(round((c_box[1] + c_box[3] - p_box[1]) / max(1e-6, p_box[3]) * pb_h))))
                    # Inset 3px inside scaled split/PIP cards to ignore CSS card border-radius / 1px edge resampling
                    if (rx1 - rx0) > 12 and (ry1 - ry0) > 12:
                        rx0 += 3; rx1 -= 3; ry0 += 3; ry1 -= 3
                    diff_im = ImageChops.difference(reg_fin.crop((rx0, ry0, rx1, ry1)), reg_src.crop((rx0, ry0, rx1, ry1))).convert("L")
                else:
                    bx0 = max(0, int(c_box[0] * pw))
                    by0 = max(0, int(c_box[1] * ph))
                    bx1 = min(pw, max(bx0 + 2, int((c_box[0] + c_box[2]) * pw)))
                    by1 = min(ph, max(by0 + 2, int((c_box[1] + c_box[3]) * ph)))
                    diff_im = ImageChops.difference(fin_im.crop((bx0, by0, bx1, by1)), src_im.crop((bx0, by0, bx1, by1))).convert("L")
                hist = diff_im.histogram()
                total_px = max(1, sum(hist))
                thresh = 38 if crop is not None else 28
                changed_px = sum(hist[thresh:])
                pixel_diff_ratio = round(changed_px / float(total_px), 4)
                pixel_samples_evaluated += 1
                worst_ov_cov = max(worst_ov_cov, pixel_diff_ratio)

        retained = worst_retained
        ov_cov = worst_ov_cov
        canvas_box = worst_canvas_box

        # R4.1-04: STRICT >=0.95 retained & <=0.05 overlay for verified tracks! NO silent 0.50/0.85 PASS downgrade!
        if is_verified:
            win_ok = (retained >= min_retained_area) and (ov_cov <= max_overlay_coverage)
            win_cert = "VERIFIED_AVAILABLE_PORTION_SOURCE_PRE_CROPPED" if w.get("source_pre_cropped") else "VERIFIED_ACTION_PROTECTION"
        else:
            # Unverified conservative placeholders check overlay clearance strictly and NEVER claim VERIFIED_ACTION_PROTECTION!
            win_ok = (ov_cov <= max_overlay_coverage) and (retained >= min_retained_area or str(w.get("entity_id") or "").endswith(("_conservative", "_static")))
            win_cert = "SAFE_FALLBACK_GEOMETRY_ONLY"

        rec = {
            "window_id": w["window_id"],
            "role": w["role"],
            "frame_span": [sf, ef],
            "verified_detector": is_verified,
            "certification": win_cert,
            "retained_visible_area_ratio": retained,
            "overlay_coverage_ratio": ov_cov,
            "pixel_occlusion_ratio": pixel_diff_ratio,
            "source_pre_cropped": bool(w.get("source_pre_cropped")),
            "canvas_bbox_norm": canvas_box,
            "pass": win_ok,
        }
        checked.append(rec)
        if not win_ok:
            violations.append({
                "window_id": w["window_id"],
                "role": w["role"],
                "reason": (
                    f"ACTION_CROPPED(retained={retained:.3f}<{min_retained_area})" if retained < min_retained_area
                    else f"ACTION_OCCLUDED_BY_OVERLAY(coverage={ov_cov:.3f}>{max_overlay_coverage})"
                ),
            })

    if violations:
        overall_st = "FAIL"
        cert = "ACTION_PROTECTION_FAILED"
    elif any_unverified_placeholder:
        overall_st = "SAFE_FALLBACK_GEOMETRY_ONLY"
        cert = "SAFE_FALLBACK_GEOMETRY_ONLY_PENDING_DETECTOR"
    else:
        overall_st = "PASS"
        cert = "VERIFIED_ACTION_PROTECTION"

    rep = {
        "schema_version": "kh03.action_protection.v2",
        "final_mp4_sha256": hash_artifact(Path(final_mp4)) if (final_mp4 and Path(final_mp4).is_file()) else None,
        "pixel_level_verified": pixel_samples_evaluated > 0,
        "pixel_samples_evaluated": pixel_samples_evaluated,
        "edl_mapped": bool(isinstance(edl, dict) and edl.get("segments")),
        "min_retained_area_policy": min_retained_area,
        "max_overlay_coverage_policy": max_overlay_coverage,
        "certification": cert,
        "windows_checked": len(checked),
        "windows": checked,
        "violations": violations,
        "status": overall_st,
        "pass": len(violations) == 0,
    }
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        write_json_atomic(Path(out_dir) / "ACTION_PROTECTION.json", rep)
    return rep


# ======================================================================
# PED-04 — SPATIAL INTERACTION (TRACK-LINKED ANCHORS & PIXEL ALIGNMENT)
# ======================================================================


def bind_component_to_track(
    component: dict[str, Any],
    entities: list[dict[str, Any]],
    *,
    preferred_roles: tuple[str, ...] = ("hand", "object", "screen_text", "motion_region", "face"),
    offset_norm: tuple[float, float] = (0.04, -0.10),
    safe_margin: float = 0.04,
) -> dict[str, Any]:
    """Bind a component spatially to a relevant tracked entity (or fall back safely to screen coords when none exists)."""
    sf = int(component.get("start_frame", 0))
    ef = int(component.get("end_frame", sf + 1))
    best_ent = None
    best_rank = 999
    for ent in entities:
        if not ent.get("observations"):
            continue
        esf, eef = ent.get("frame_span") or [0, 10**9]
        if eef <= sf or esf >= ef:
            continue
        role = str(ent.get("role") or "object")
        rk = preferred_roles.index(role) if role in preferred_roles else len(preferred_roles)
        if rk < best_rank or (rk == best_rank and best_ent and float(ent.get("confidence", 0)) > float(best_ent.get("confidence", 0))):
            best_rank = rk
            best_ent = ent

    if best_ent is None:
        return {
            "component_id": component["component_id"],
            "anchor_mode": "SCREEN_SPACE_FALLBACK",
            "track_entity_id": None,
            "occlusion_mode": "SAFE_PLACEMENT_NO_MASK",
            "offset_norm": list(offset_norm),
            "safe_margin": safe_margin,
            "reason": "No overlapping tracked entity; using safe screen-space placement.",
        }
    return {
        "component_id": component["component_id"],
        "anchor_mode": "TRACK_LINKED_SPATIAL",
        "track_entity_id": best_ent["id"],
        "track_role": best_ent["role"],
        "entity_confidence": best_ent["confidence"],
        "occlusion_mode": "SAFE_PLACEMENT_NO_DEPTH_MASK",
        "offset_norm": list(offset_norm),
        "safe_margin": safe_margin,
        "scene_cut_reset": True,
        "lost_track_policy": "SHORT_HOLD_THEN_SAFE_REPOSITION",
    }


def resolve_spatial_anchor(
    binding: dict[str, Any],
    frame_idx: int,
    entity: dict[str, Any] | None,
    *,
    component_size_norm: tuple[float, float] = (0.36, 0.12),
    scene_cuts: list[int] | None = None,
    max_hold_frames: int = 8,
    protected_boxes: list[list[float]] | None = None,
) -> dict[str, Any]:
    """Resolve exact safe spatial anchor box at `frame_idx` with scene-cut reset & lost-track fallback (PED-04)."""
    cw, ch = float(component_size_norm[0]), float(component_size_norm[1])
    margin = float(binding.get("safe_margin", 0.04))
    ox, oy = [float(v) for v in binding.get("offset_norm", [0.04, -0.10])]

    if entity is None or binding.get("anchor_mode") != "TRACK_LINKED_SPATIAL":
        return {"frame": frame_idx, "bbox_norm": [margin, margin, cw, ch], "status": "SCREEN_FALLBACK", "stale_track": False}

    obs = sorted(entity.get("observations") or [], key=lambda x: int(x["frame"]))
    # Find latest observation AFTER the most recent scene cut before frame_idx (strict scene-cut reset!)
    last_cut = max((c for c in (scene_cuts or []) if c <= frame_idx), default=-1)
    valid_obs = [o for o in obs if int(o["frame"]) >= last_cut and int(o["frame"]) <= frame_idx]
    if not valid_obs:
        # Lost track or post-cut with no new observation -> safe fallback position
        return {"frame": frame_idx, "bbox_norm": [margin, margin, cw, ch], "status": "LOST_TRACK_SAFE_REPOSITION", "stale_track": True}

    latest = valid_obs[-1]
    age = frame_idx - int(latest["frame"])
    # Support sparse detector keyframes cleanly via linear interpolation between bracketing post-cut observations,
    # while still failing closed on genuine lost-track / stale-track past the final detection!
    next_cut = min((c for c in (scene_cuts or []) if c > frame_idx), default=10**9)
    future_obs = [o for o in obs if frame_idx < int(o["frame"]) < next_cut]
    if age > max_hold_frames and future_obs:
        nxt = future_obs[0]
        span = int(nxt["frame"]) - int(latest["frame"])
        if 0 < span <= max(36, max_hold_frames * 4):
            alpha = (frame_idx - int(latest["frame"])) / float(span)
            b0 = [float(v) for v in latest["bbox_norm"]]
            b1 = [float(v) for v in nxt["bbox_norm"]]
            ex, ey, ew, eh = [b0[i] + alpha * (b1[i] - b0[i]) for i in range(4)]
            age = 0
        else:
            return {"frame": frame_idx, "bbox_norm": [margin, margin, cw, ch], "status": "STALE_TRACK_SAFE_REPOSITION", "stale_track": True}
    elif age > max_hold_frames:
        return {"frame": frame_idx, "bbox_norm": [margin, margin, cw, ch], "status": "STALE_TRACK_SAFE_REPOSITION", "stale_track": True}
    else:
        ex, ey, ew, eh = [float(v) for v in latest["bbox_norm"]]
    # Place anchored callout cleanly adjacent (never colliding with the tracked entity itself!)
    ax = _clamp(ex + ew * 0.5 - cw * 0.5 + ox, margin, max(margin, 1.0 - margin - cw))
    ay = _clamp(ey + oy - ch if oy < 0 else ey + eh + oy, margin, max(margin, 1.0 - margin - ch))
    box = [round(ax, 4), round(ay, 4), round(cw, 4), round(ch, 4)]

    # Avoid protected boxes (face/hands) by flipping vertically if colliding
    for pb in (protected_boxes or []):
        if _rect_intersection_area(box, pb) > 0.005:
            alt_y = _clamp(pb[1] - ch - 0.02 if pb[1] > 0.45 else pb[1] + pb[3] + 0.02, margin, max(margin, 1.0 - margin - ch))
            box = [round(ax, 4), round(alt_y, 4), round(cw, 4), round(ch, 4)]
    return {"frame": frame_idx, "bbox_norm": box, "status": "TRACK_LOCKED" if age == 0 else "SHORT_HOLD", "stale_track": False, "track_age_frames": age}


def validate_spatial_alignment(
    resolved_anchors: list[dict[str, Any]],
    *,
    protected_boxes: list[list[float]] | None = None,
    max_frame_jitter_norm: float = 0.08,
    max_stale_ratio: float = 0.35,
) -> dict[str, Any]:
    """Validate resolved spatial anchors for bounds, jitter/drift, stale tracks, and protected-box collisions (PED-04)."""
    issues: list[str] = []
    if not resolved_anchors:
        return {"schema_version": "kh03.spatial_alignment_qa.v1", "anchors_checked": 0, "issues": [], "pass": True}

    stale_cnt = 0
    max_jitter = 0.0
    prev_box: list[float] | None = None
    prev_frame: int | None = None
    for a in resolved_anchors:
        bb = a["bbox_norm"]
        if bb[0] < -0.001 or bb[1] < -0.001 or (bb[0] + bb[2]) > 1.001 or (bb[1] + bb[3]) > 1.001:
            issues.append(f"SPATIAL_CLIPPING_F{a['frame']}: {bb}")
        if a.get("stale_track"):
            stale_cnt += 1
        for pb in (protected_boxes or []):
            if _rect_intersection_area(bb, pb) > 0.01:
                issues.append(f"SPATIAL_COLLISION_F{a['frame']}: anchor overlaps protected region")
        if prev_box is not None and prev_frame is not None and (int(a["frame"]) - prev_frame) == 1:
            jit = math.hypot(bb[0] - prev_box[0], bb[1] - prev_box[1])
            max_jitter = max(max_jitter, jit)
            if jit > max_frame_jitter_norm and a.get("status") == "TRACK_LOCKED":
                issues.append(f"SPATIAL_JITTER_F{a['frame']}: delta={jit:.4f}>{max_frame_jitter_norm}")
        prev_box = bb
        prev_frame = int(a["frame"])

    stale_ratio = round(stale_cnt / float(len(resolved_anchors)), 4)
    if stale_ratio > max_stale_ratio:
        issues.append(f"STALE_TRACK_RATIO_EXCEEDED({stale_ratio}>{max_stale_ratio})")

    return {
        "schema_version": "kh03.spatial_alignment_qa.v1",
        "anchors_checked": len(resolved_anchors),
        "max_frame_jitter_norm": round(max_jitter, 4),
        "stale_track_ratio": stale_ratio,
        "issues": issues,
        "pass": len(issues) == 0,
    }


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

    # 2. Optional real streaming scan + real macOS Vision / NumPy entity detection on local video file (PED-02)
    vp = Path(video_path).resolve() if (video_path and Path(video_path).is_file()) else None
    detected_bundle: dict[str, Any] | None = None
    if enable_streaming_scan and vp is not None:
        detected_bundle = detect_semantic_entities(
            vp,
            source_media,
            presenter_mode=presenter_mode,
            probe_observations=valid_obs or None,
            use_macos_vision=True,
            ffmpeg_threads=ffmpeg_threads,
        )
        scene_cuts = list(detected_bundle["scene_cuts"])
        scan_samples = list(detected_bundle["scan_samples"])
        interpolated_ratio = round(max(0.0, 1.0 - (len(scan_samples) / float(max(1, nb_frames)))), 4)
        # Promote real macOS Vision verified hits into valid_obs so real face/hand/text detections drive protection!
        if not valid_obs and detected_bundle["detector_used"] == "MACOS_VISION_DETECTOR":
            valid_obs = [h for h in detected_bundle["raw_hits"] if h.get("verified_detector")]

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
            face_box = [0.22, 0.21, 0.56, 0.27] if aspect == "9:16" else ([0.30, 0.12, 0.40, 0.42] if aspect == "16:9" else [0.22, 0.16, 0.56, 0.34])
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

    # PED-02: Materialize canonical tracked_entities & action_protection_windows for downstream layout & final pixel QA
    if detected_bundle is not None and detected_bundle.get("entities"):
        tracked_entities = list(detected_bundle["entities"])
        detector_used = str(detected_bundle.get("detector_used") or mode)
    else:
        detector_used = mode
        tracked_entities = []
        for pr in protected_regions:
            tracked_entities.append({
                "id": pr["region_id"],
                "role": "hand" if pr["zone"] == "hands" else str(pr["zone"]),
                "time_span": [0.0, dur_sec],
                "frame_span": [0, nb_frames],
                "bbox_norm": list(pr["bbox_norm"]),
                "coordinate_space": "SOURCE_NORMALIZED_TOP_LEFT",
                "source": str(pr.get("provenance") or mode),
                "verified_detector": str(pr.get("provenance") or "") == "LOCAL_DETECTOR_VERIFIED",
                "confidence": float(pr.get("confidence", mean_conf)),
                "occlusion": 0.0,
                "semantic_importance": 1.0 if pr["zone"] in ("face", "hands", "mouth") else 0.8,
                "uncertainty": round(max(0.015, (1.0 - float(pr.get("confidence", mean_conf))) * 0.08), 4),
                "estimated": str(pr.get("provenance") or "") != "LOCAL_DETECTOR_VERIFIED",
                "source_pre_cropped": False,
                "observations": [],
            })
    action_protection_windows = build_action_protection_windows(
        tracked_entities, source_media, scene_cuts=scene_cuts, min_confidence=0.40,
    )

    if run_dir is not None:
        adir = run_dir / "analysis"
        adir.mkdir(parents=True, exist_ok=True)
        resolve_detector_capabilities(out_dir=adir)
        write_json_atomic(adir / "TRACKING_PROVENANCE.json", provenance)
        write_json_atomic(adir / "TRACKING_CONFIDENCE_REPORT.json", conf_report)
        write_json_atomic(adir / "TRACKS.json", {
            "schema_version": "kh03.tracks.v1",
            "run_id": run_id,
            "detector_used": detector_used,
            "tracking_mode": mode,
            "scene_cuts": scene_cuts,
            "entities": tracked_entities,
        })
        write_json_atomic(adir / "ACTION_PROTECTION_WINDOWS.json", {
            "schema_version": "kh03.action_protection_windows.v1",
            "run_id": run_id,
            "detector_used": detector_used,
            "windows": action_protection_windows,
        })
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
        "detector_used": detector_used,
        "tracked_entities": tracked_entities,
        "action_protection_windows": action_protection_windows,
        "subject_tracks": subject_tracks,
        "object_tracks": object_tracks,
        "action_timeline": action_timeline,
        "protected_regions": protected_regions,
        "scene_cuts": scene_cuts,
        "provenance_sha256": hash_artifact(provenance),
    }
