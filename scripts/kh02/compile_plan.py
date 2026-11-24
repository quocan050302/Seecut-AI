"""KH-02 Canonical Compiler: compiles validated SHOTBOOK_V2.json -> build/master.html, build/project.json, fp-manifest.txt & COMPILE_MAP.json.

Preserves SeeCut v3.4 Core renderer invariants:
- Local pinned GSAP (hyperframes/dist/vendor/npm/gsap@3.15.0/dist/gsap.min.js) & optional local hf-hw SVG helpers
- window.__timelines.master absolute timeline seek (no CSS autoplay, no Date.now/Math.random)
- Delayed elements start at CSS opacity:0 and animate with gsap .set() + .to() (never .from())
- Person <img> swap via #personWrap / #person (never <video> scrub)
- Full H1 tagging (data-check="box"|"bg", data-anchor, data-word, data-t) & JianYing tagging (data-layer, data-layer-cat)
- Strict HTML/attribute escaping (zero untrusted JS injection)
"""

from __future__ import annotations

import html
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .contracts import hash_artifact, sanitize_identifier, validate_references, validate_schema
from .typography import FONT_FALLBACK_STACKS

THEMES: dict[str, dict[str, str]] = {
    "editorial_obsidian_ivory": {
        "canvas_bg": "#151210",
        "paper_bg": "#F4EFE6",
        "paper_dot": "rgba(30,25,22,0.08)",
        "card_bg": "#1C1816",
        "card_fg": "#F7F3EB",
        "card_muted": "#D6CBBE",
        "card_border": "rgba(247,243,235,0.22)",
        "accent": "#D45233",
        "accent_soft": "#E9B949",
        "light_card_bg": "#FBF8F2",
        "light_card_fg": "#1C1816",
        "shadow": "0 18px 42px rgba(0,0,0,0.34)",
    },
    "technical_slate_cyan": {
        "canvas_bg": "#0F172A",
        "paper_bg": "#0F172A",
        "paper_dot": "rgba(148,163,184,0.12)",
        "card_bg": "#1E293B",
        "card_fg": "#F8FAFC",
        "card_muted": "#CBD5E1",
        "card_border": "rgba(56,189,248,0.35)",
        "accent": "#0EA5E9",
        "accent_soft": "#34D399",
        "light_card_bg": "#F8FAFC",
        "light_card_fg": "#0F172A",
        "shadow": "0 16px 36px rgba(2,6,23,0.45)",
    },
    "documentary_warm_archive": {
        "canvas_bg": "#1C1917",
        "paper_bg": "#EDE7DC",
        "paper_dot": "rgba(41,37,36,0.06)",
        "card_bg": "#292524",
        "card_fg": "#F5F0E6",
        "card_muted": "#D6D3D1",
        "card_border": "rgba(214,211,209,0.28)",
        "accent": "#B45309",
        "accent_soft": "#D97706",
        "light_card_bg": "#FAF7F2",
        "light_card_fg": "#1C1917",
        "shadow": "0 14px 30px rgba(0,0,0,0.28)",
    },
    "explainer_cobalt_chalk": {
        "canvas_bg": "#101E36",
        "paper_bg": "#F0F4FA",
        "paper_dot": "rgba(16,30,54,0.09)",
        "card_bg": "#172A4A",
        "card_fg": "#F8FAFC",
        "card_muted": "#CBD5E1",
        "card_border": "rgba(96,165,250,0.35)",
        "accent": "#2563EB",
        "accent_soft": "#F59E0B",
        "light_card_bg": "#FFFFFF",
        "light_card_fg": "#101E36",
        "shadow": "0 16px 34px rgba(15,23,42,0.30)",
    },
    "editorial_burgundy_parchment": {
        "canvas_bg": "#231B18",
        "paper_bg": "#FAF5EC",
        "paper_dot": "rgba(110,31,36,0.08)",
        "card_bg": "#6E1F24",
        "card_fg": "#FFFDF9",
        "card_muted": "#F4DFC8",
        "card_border": "#4A1216",
        "accent": "#6E1F24",
        "accent_soft": "#D99B26",
        "pine": "#1E4D3B",
        "light_card_bg": "#FFFDF8",
        "light_card_fg": "#231B18",
        "shadow": "0 12px 28px rgba(35,27,24,0.16)",
    },
    "seecut_geek_craft": {
        "canvas_bg": "#F6F1E7",
        "paper_bg": "#F6F1E7",
        "paper_dot": "rgba(26,26,26,0.10)",
        "card_bg": "#FFFDF9",
        "card_fg": "#1C1B19",
        "card_muted": "#57534E",
        "card_border": "#1C1B19",
        "accent": "#C94A32",
        "accent_soft": "#D99B26",
        "light_card_bg": "#FFFDF9",
        "light_card_fg": "#1C1B19",
        "shadow": "0 10px 24px rgba(28,27,25,0.14)",
    },
}


def _esc(val: Any) -> str:
    return html.escape(str(val or ""), quote=True)


def _locate_local_gsap(tools_root: Path | None) -> Path | None:
    candidates = []
    if tools_root:
        candidates.append(tools_root / "node_modules/hyperframes/dist/vendor/npm/gsap@3.15.0/dist/gsap.min.js")
        candidates.extend(sorted(tools_root.glob("node_modules/hyperframes/dist/vendor/npm/gsap@*/dist/gsap.min.js")))
    default_tools = Path(os.environ.get("SEECUT_STANDALONE_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone"))) / "tools"
    candidates.extend(sorted(default_tools.glob("node_modules/hyperframes/dist/vendor/npm/gsap@*/dist/gsap.min.js")))
    for c in candidates:
        if c.is_file():
            return c
    return None


def _locate_hf_hw(seecut_root: Path | None) -> Path | None:
    base = seecut_root or Path(
        os.environ.get("SEECUT_UPSTREAM_ROOT", os.path.expanduser("~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut"))
    )
    cand = base / "skill/seecut/assets/hf-hw"
    return cand if (cand / "hf-hw.js").is_file() else None


def compile_master_timeline(shotbook: dict[str, Any], *, word_time_map: dict[str, float] | None = None) -> dict[str, Any]:
    """Build deterministic timeline event schedule, steady sample times, and spring targets from SHOTBOOK V2."""
    spec = shotbook["output_spec"]
    fps = spec["fps_num"] / spec["fps_den"]
    total_frames = int(spec["total_frames"])
    duration_sec = round(total_frames / fps, 4)

    compiled_events: list[dict[str, Any]] = []
    spring_targets: list[list[Any]] = []
    steady_set: set[float] = set()

    for b in shotbook["beats"]:
        s_t = b["start_frame"] / fps
        e_t = b["end_frame"] / fps
        # Pick steady midpoint (slightly after start settle) inside every beat
        mid = round(min(duration_sec - 0.12, max(s_t + 0.45, (s_t + e_t) / 2.0)), 3)
        if 0.1 < mid < duration_sec - 0.05:
            steady_set.add(mid)

    for ev in shotbook["visual_events"]:
        dom_id = sanitize_identifier(ev["event_id"])
        s_sec = round(ev["start_frame"] / fps, 4)
        e_sec = round(ev["end_frame"] / fps, 4)
        compiled_events.append({
            "event_id": ev["event_id"],
            "selector": f"#{dom_id}",
            "layer_id": sanitize_identifier(ev["layer_id"]),
            "start_frame": int(ev["start_frame"]),
            "end_frame": int(ev["end_frame"]),
            "start_sec": s_sec,
            "end_sec": e_sec,
            "capability_id": ev["capability_id"],
            "expected_transform": ev.get("transform") or {},
        })
        if str(ev.get("easing", "")).startswith("back.out") and ev["capability_id"] in ("hf.spring_pop", "hf.badge_pop", "hf.stagger_cascade"):
            spring_targets.append([f"#{dom_id}", round(s_sec, 3)])

    steady_times = sorted(steady_set)
    if not steady_times:
        steady_times = [round(duration_sec * 0.5, 3)]

    return {
        "schema_version": "kh02.timeline.v1",
        "plan_sha256": hash_artifact(shotbook),
        "fps_num": int(spec["fps_num"]),
        "fps_den": int(spec["fps_den"]),
        "total_frames": total_frames,
        "duration_sec": duration_sec,
        "html_file": "master.html",
        "project_json_file": "project.json",
        "steady_times_sec": steady_times,
        "spring_targets": spring_targets,
        "compiled_events": compiled_events,
    }


def validate_compiled_events(compile_map: dict[str, Any], shotbook: dict[str, Any]) -> list[dict[str, str]]:
    """Verify compiled timeline matches canonical SHOTBOOK V2 plan SHA and covers all visual events."""
    errors = validate_schema(compile_map, "timeline")
    if compile_map.get("plan_sha256") != hash_artifact(shotbook):
        errors.append({"code": "COMPILE_SHA_MISMATCH", "location": "$.plan_sha256", "message": "Compile map plan SHA does not match SHOTBOOK_V2"})
    planned_ids = {ev["event_id"] for ev in shotbook.get("visual_events", [])}
    compiled_ids = {ev["event_id"] for ev in compile_map.get("compiled_events", [])}
    missing = sorted(planned_ids - compiled_ids)
    extra = sorted(compiled_ids - planned_ids)
    if missing:
        errors.append({"code": "COMPILE_MISSING_EVENTS", "location": "$.compiled_events", "message": f"Missing planned events: {missing}"})
    if extra:
        errors.append({"code": "COMPILE_UNPLANNED_EVENTS", "location": "$.compiled_events", "message": f"Unplanned events added by compiler: {extra}"})
    return errors


def export_project_config(
    shotbook: dict[str, Any],
    compile_map: dict[str, Any],
    *,
    primary_source_path: str | None,
    face_rect: list[float] | str | None,
    build_dir: Path,
) -> dict[str, Any]:
    """Emit SeeCut-compatible build/project.json with exact native rational fps and H1 check config."""
    spec = shotbook["output_spec"]
    fps = spec["fps_num"] / spec["fps_den"]
    proj: dict[str, Any] = {
        "html": "master.html",
        "timeline": "master",
        "fps": int(spec["fps_num"]) if spec["fps_den"] == 1 else round(fps, 6),
        "fps_num": int(spec["fps_num"]),
        "fps_den": int(spec["fps_den"]),
        "width": int(spec["width"]),
        "height": int(spec["height"]),
        "output_scale": float(spec.get("output_scale", 1.5)),
        "duration": round(int(spec["total_frames"]) / fps, 4),
        "total_frames": int(spec["total_frames"]),
        "person_dir": "person",
        "person_img_id": "person",
        "person_wrap_id": "personWrap",
        "steady": compile_map["steady_times_sec"],
        "spring": compile_map["spring_targets"],
        "kh02_plan_sha256": compile_map["plan_sha256"],
        "kh02_run_id": shotbook["run_id"],
    }
    if primary_source_path:
        proj["source"] = os.path.realpath(primary_source_path)
    if isinstance(face_rect, list) and len(face_rect) == 4:
        proj["face_rect"] = face_rect
    elif shotbook["mode"] != "faceless" and primary_source_path:
        # Conservative upper-center fallback only if person exists without measured face_rect
        proj["face_rect"] = [0.25, 0.16, 0.75, 0.56]

    (build_dir / "project.json").write_text(json.dumps(proj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return proj


def _render_event_html(
    ev: dict[str, Any],
    *,
    width: int,
    height: int,
    asset_rel_map: dict[str, str],
    word_time_map: dict[str, float],
    fp_texts: list[str],
) -> str:
    dom_id = sanitize_identifier(ev["event_id"])
    layer_name = sanitize_identifier(ev["layer_id"])
    layer_cat = ev.get("layer_cat", "card")
    bx, by, bw, bh = [float(v) for v in ev["normalized_bbox"]]
    left_px = round(bx * width, 1)
    top_px = round(by * height, 1)
    w_px = round(bw * width, 1)
    h_px = round(bh * height, 1)
    z = int(ev.get("z_order", 30))

    params = ev.get("params") or {}
    kind = str(params.get("kind") or "editorial_headline")
    aref = ev.get("asset_ref")
    has_media = bool(aref and aref in asset_rel_map)

    # H1 data-check contract: "bg" ONLY when element contains a real <img>/<video>/<canvas>
    data_check = "bg" if (layer_cat == "evbg" and has_media) else ("none" if kind == "progress_spine" else "box")
    check_attr = f' data-check="{data_check}"' if data_check in ("box", "bg") else ""

    anchor_wid = ev.get("anchor_word_id")
    word_attr = ""
    if anchor_wid and anchor_wid in word_time_map and params.get("anchor_word_label"):
        word_attr = f' data-word="{_esc(params["anchor_word_label"])}" data-t="{word_time_map[anchor_wid]:.2f}"'

    anchor_attr = f' data-anchor="{_esc(params["anchor_target_id"])}"' if params.get("anchor_target_id") else ""

    base_style = f"left:{left_px}px;top:{top_px}px;width:{w_px}px;height:{h_px}px;z-index:{z};"

    if kind == "progress_spine":
        return (
            f'  <div id="{dom_id}" class="kh-el kh-spine" style="{base_style}" '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
            f'<div class="kh-spine-track"><div id="{dom_id}_fill" class="kh-spine-fill"></div></div></div>'
        )

    if kind == "evidence_spotlight" and has_media:
        img_src = _esc(asset_rel_map[aref])
        kicker = _esc(params.get("kicker", "VERIFIED CAPTURE"))
        headline = _esc(params.get("headline", ""))
        sub = _esc(params.get("subtitle", ""))
        fit_cls = "kh-evid-img-contain" if params.get("fit", "contain") == "contain" else "kh-evid-img"
        for t in (params.get("kicker"), params.get("headline"), params.get("subtitle")):
            if t:
                fp_texts.append(str(t))
        return (
            f'  <div id="{dom_id}" class="kh-el kh-evid-frame" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="evbg">'
            f'<div class="kh-evid-bar"><span class="kh-dot"></span><span class="kh-kicker">{kicker}</span>'
            f'<span class="kh-evid-sub">{sub}</span></div>'
            f'<div class="kh-evid-media"><img src="{img_src}" alt="{headline}" class="{fit_cls}" /></div>'
            f'{f"<div class=kh-evid-foot>{headline}</div>" if headline else ""}'
            f'</div>'
        )

    if kind == "chapter_pill":
        kicker = _esc(params.get("kicker", "01"))
        headline = _esc(params.get("headline", ""))
        fp_texts.extend([str(params.get("kicker", "")), str(params.get("headline", ""))])
        return (
            f'  <div id="{dom_id}" class="kh-el kh-chapter-pill" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
            f'<span class="kh-ch-num">{kicker}</span><span class="kh-ch-text">{headline}</span></div>'
        )

    if kind == "stagger_grid_4":
        items = params.get("items") or []
        title = _esc(params.get("headline", ""))
        kicker = _esc(params.get("kicker", ""))
        fp_texts.extend([str(params.get("headline", "")), str(params.get("kicker", ""))])
        cells = []
        for idx_i, it in enumerate(items[:4]):
            num = _esc(it.get("num", f"0{idx_i + 1}"))
            it_title = _esc(it.get("title", ""))
            it_sub = _esc(it.get("desc", ""))
            tone = _esc(it.get("tone", "burgundy" if idx_i % 2 == 0 else "pine"))
            fp_texts.extend([str(it.get("title", "")), str(it.get("desc", ""))])
            cells.append(
                f'<div id="{dom_id}_cell_{idx_i + 1}" class="kh-grid-cell kh-tone-{tone}">'
                f'<div class="kh-cell-num">{num}</div>'
                f'<div class="kh-cell-title">{it_title}</div>'
                f'<div class="kh-cell-sub">{it_sub}</div></div>'
            )
        return (
            f'  <div id="{dom_id}" class="kh-el kh-grid-wrap" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
            f'{f"<div class=kh-grid-head><span class=kh-kicker-dark>{kicker}</span><span class=kh-grid-title>{title}</span></div>" if title else ""}'
            f'<div class="kh-grid-2x2">{"".join(cells)}</div></div>'
        )

    if kind == "step_pillars_3":
        steps = params.get("steps") or []
        title = _esc(params.get("headline", "3-STEP FLOW"))
        kicker = _esc(params.get("kicker", "HOW IT WORKS"))
        fp_texts.extend([str(params.get("headline", "")), str(params.get("kicker", ""))])
        cols = []
        tones = ("burgundy", "pine", "gold")
        for idx_s, st in enumerate(steps[:3]):
            num = _esc(st.get("num", f"0{idx_s + 1}"))
            st_t = _esc(st.get("title", ""))
            st_d = _esc(st.get("desc", ""))
            fp_texts.extend([str(st.get("title", "")), str(st.get("desc", ""))])
            cols.append(
                f'<div id="{dom_id}_col_{idx_s + 1}" class="kh-pillar kh-tone-{tones[idx_s % 3]}">'
                f'<div class="kh-pillar-num">{num}</div>'
                f'<div class="kh-pillar-title">{st_t}</div>'
                f'<div class="kh-pillar-desc">{st_d}</div></div>'
            )
        return (
            f'  <div id="{dom_id}" class="kh-el kh-pillars-wrap" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
            f'<div class="kh-grid-head"><span class="kh-kicker-dark">{kicker}</span><span class="kh-grid-title">{title}</span></div>'
            f'<div class="kh-pillars-3">{"".join(cols)}</div></div>'
        )

    if kind == "highlighter_card":
        kicker = _esc(params.get("kicker", "DETAIL"))
        prefix = _esc(params.get("prefix", ""))
        hl_word = _esc(params.get("highlight_text", ""))
        suffix = _esc(params.get("suffix", ""))
        sub = _esc(params.get("subtitle", ""))
        for t in (params.get("kicker"), params.get("prefix"), params.get("highlight_text"), params.get("suffix"), params.get("subtitle")):
            if t:
                fp_texts.append(str(t))
        return (
            f'  <div id="{dom_id}" class="kh-el kh-hl-card" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
            f'<div class="kh-kicker-dark">{kicker}</div>'
            f'<div class="kh-hl-line">{prefix} <span class="kh-hl-mark"><span id="{dom_id}_brush" class="kh-hl-brush"></span>'
            f'<span class="kh-hl-txt">{hl_word}</span></span> {suffix}</div>'
            f'{f"<div class=kh-sub-dark>{sub}</div>" if sub else ""}</div>'
        )

    if kind == "hand_arrow":
        color = _esc(params.get("stroke_color", "#6E1F24"))
        label = _esc(params.get("label", ""))
        if label:
            fp_texts.append(str(params.get("label")))
        return (
            f'  <div id="{dom_id}" class="kh-el kh-hand-wrap" style="{base_style}"{anchor_attr} '
            f'data-layer="{layer_name}" data-layer-cat="hand">'
            f'<svg viewBox="0 0 180 110" width="{int(w_px)}" height="{int(h_px)}" overflow="visible">'
            f'<path id="{dom_id}_path" d="M 140 88 Q 112 34 42 14" fill="none" stroke="{color}" stroke-width="5.5" stroke-linecap="round" />'
            f'<path id="{dom_id}_head" d="M 62 6 L 38 14 L 54 32" fill="none" stroke="{color}" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round" />'
            f'</svg>{f"<div class=kh-hand-note>{label}</div>" if label else ""}</div>'
        )

    if kind == "hand_circle":
        color = _esc(params.get("stroke_color", "#D99B26"))
        return (
            f'  <div id="{dom_id}" class="kh-el kh-hand-wrap" style="{base_style}"{anchor_attr} '
            f'data-layer="{layer_name}" data-layer-cat="hand">'
            f'<svg viewBox="0 0 {int(w_px)} {int(h_px)}" width="{int(w_px)}" height="{int(h_px)}" overflow="visible">'
            f'<path id="{dom_id}_path" d="M {int(w_px*0.12)} {int(h_px*0.18)} C {int(w_px*0.42)} {-4}, {int(w_px*0.95)} {int(h_px*0.04)}, {int(w_px*0.96)} {int(h_px*0.50)} '
            f'C {int(w_px*0.97)} {int(h_px*0.95)}, {int(w_px*0.45)} {int(h_px*1.03)}, {int(w_px*0.08)} {int(h_px*0.86)} '
            f'C {-4} {int(h_px*0.62)}, {int(w_px*0.02)} {int(h_px*0.24)}, {int(w_px*0.18)} {int(h_px*0.14)}" '
            f'fill="none" stroke="{color}" stroke-width="5" stroke-linecap="round" /></svg></div>'
        )

    if kind == "step_rail_3":
        steps = params.get("steps") or []
        title = _esc(params.get("headline", "HOW IT WORKS"))
        kicker = _esc(params.get("kicker", "3-STEP BREAKDOWN"))
        fp_texts.extend([str(params.get("headline", "")), str(params.get("kicker", ""))])
        items_html = []
        for s_idx, st in enumerate(steps[:3]):
            st_num = _esc(st.get("num", f"0{s_idx + 1}"))
            st_title = _esc(st.get("title", ""))
            st_desc = _esc(st.get("desc", ""))
            fp_texts.extend([str(st.get("title", "")), str(st.get("desc", ""))])
            items_html.append(
                f'<div id="{dom_id}_step_{s_idx + 1}" class="kh-step-item">'
                f'<div class="kh-step-num">{st_num}</div>'
                f'<div class="kh-step-body"><div class="kh-step-title">{st_title}</div>'
                f'<div class="kh-step-desc">{st_desc}</div></div></div>'
            )
        return (
            f'  <div id="{dom_id}" class="kh-el kh-step-rail" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
            f'<div class="kh-rail-head"><span class="kh-kicker-accent">{kicker}</span>'
            f'<div class="kh-rail-title">{title}</div></div>'
            f'<div class="kh-step-list">{"".join(items_html)}</div></div>'
        )

    if kind == "code_window":
        kicker = _esc(params.get("kicker", "WORKFLOW"))
        headline = _esc(params.get("headline", ""))
        code_lines = params.get("code_lines") or [str(params.get("subtitle", "# verified command"))]
        fp_texts.extend([str(params.get("kicker", "")), str(params.get("headline", ""))] + [str(x) for x in code_lines])
        code_html = "\n".join(_esc(line) for line in code_lines[:8])
        return (
            f'  <div id="{dom_id}" class="kh-el kh-code-card" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
            f'<div class="kh-code-head">{kicker} · {headline}</div>'
            f'<pre class="kh-code-pre">{code_html}</pre></div>'
        )

    if kind == "hand_underline":
        # Anchor wrapper matches target bbox so geom-check [圈歪] distance == 0
        target_id = _esc(params.get("anchor_target_id", ""))
        color = _esc(params.get("stroke_color", "#D45233"))
        return (
            f'  <div id="{dom_id}" class="kh-el kh-hand-wrap" style="{base_style}"{anchor_attr} '
            f'data-layer="{layer_name}" data-layer-cat="hand">'
            f'<svg viewBox="0 0 {int(w_px)} {int(h_px)}" width="{int(w_px)}" height="{int(h_px)}" overflow="visible">'
            f'<g transform="translate(0,{int(h_px - 10)})">'
            f'<path id="{dom_id}_path" d="M 8 4 Q {int(w_px * 0.5)} -4 {int(w_px - 8)} 4" '
            f'fill="none" stroke="{color}" stroke-width="6" stroke-linecap="round" /></g></svg></div>'
        )

    if kind == "verdict_bar":
        kicker = _esc(params.get("kicker", "RESULT"))
        headline = _esc(params.get("headline", ""))
        sub = _esc(params.get("subtitle", ""))
        fp_texts.extend([str(params.get("kicker", "")), str(params.get("headline", "")), str(params.get("subtitle", ""))])
        return (
            f'  <div id="{dom_id}" class="kh-el kh-verdict" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
            f'<div class="kh-verdict-accent"></div>'
            f'<div class="kh-verdict-copy"><div class="kh-kicker-accent">{kicker}</div>'
            f'<div class="kh-verdict-title">{headline}</div>'
            f'{f"<div class=kh-verdict-sub>{sub}</div>" if sub else ""}</div></div>'
        )

    if kind == "clip_mask_wipe":
        kicker = _esc(params.get("kicker", "REVEAL"))
        headline = _esc(params.get("headline", ""))
        sub = _esc(params.get("subtitle", ""))
        fit_cls = "kh-evid-img-contain" if params.get("fit", "contain") == "contain" else "kh-evid-img"
        for t in (params.get("kicker"), params.get("headline"), params.get("subtitle")):
            if t:
                fp_texts.append(str(t))
        if has_media:
            img_src = _esc(asset_rel_map[aref])
            clean_frame = bool(params.get("clean_frame", False))
            bar_html = (
                f'<div class="kh-evid-bar"><span class="kh-kicker">{kicker}</span><span class="kh-evid-sub">{sub}</span></div>'
                if clean_frame else
                f'<div class="kh-evid-bar"><span class="kh-dot"></span><span class="kh-kicker">{kicker}</span><span class="kh-evid-sub">{sub}</span></div>'
            )
            body = (
                f'{bar_html}'
                f'<div class="kh-evid-media"><img src="{img_src}" alt="{headline}" class="{fit_cls}" /></div>'
                f'{f"<div class=kh-evid-foot>{headline}</div>" if headline else ""}'
            )
            cls = "kh-el kh-evid-frame kh-clip-wipe"
        else:
            body = (
                f'<div class="kh-kicker-dark">{kicker}</div>'
                f'<div class="kh-title-dark">{headline}</div>'
                f'{f"<div class=kh-sub-dark>{sub}</div>" if sub else ""}'
            )
            cls = "kh-el kh-card-light kh-clip-wipe"
        return (
            f'  <div id="{dom_id}" class="{cls}" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">{body}</div>'
        )

    if kind == "spotlight_focus":
        kicker = _esc(params.get("kicker", "SPOTLIGHT"))
        headline = _esc(params.get("headline", ""))
        sub = _esc(params.get("subtitle", ""))
        badge = _esc(params.get("badge", "FOCUS"))
        for t in (params.get("kicker"), params.get("headline"), params.get("subtitle"), params.get("badge")):
            if t:
                fp_texts.append(str(t))
        media_block = ""
        if has_media:
            img_src = _esc(asset_rel_map[aref])
            media_block = f'<div class="kh-spot-media"><img src="{img_src}" alt="{headline}" class="kh-evid-img-contain" /></div>'
        return (
            f'  <div id="{dom_id}" class="kh-el kh-spot-wrap" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
            f'<div id="{dom_id}_scrim" class="kh-spot-scrim"></div>'
            f'<div class="kh-spot-card">'
            f'<div class="kh-card-top"><span class="kh-kicker-accent">{kicker}</span><span class="kh-pill">{badge}</span></div>'
            f'{media_block}'
            f'<div class="kh-spot-title">{headline}</div>'
            f'{f"<div class=kh-sub-light>{sub}</div>" if sub else ""}</div></div>'
        )

    if kind == "kinetic_phrase_mask":
        kicker = _esc(params.get("kicker", ""))
        lines_raw = params.get("lines") or ([params.get("headline", "")] if params.get("headline") else [])
        sub = _esc(params.get("subtitle", ""))
        if kicker:
            fp_texts.append(str(params.get("kicker")))
        if sub:
            fp_texts.append(str(params.get("subtitle")))
        line_divs = []
        for idx_l, ln in enumerate(lines_raw[:3]):
            fp_texts.append(str(ln))
            line_divs.append(
                f'<div class="kh-km-mask"><span id="{dom_id}_line_{idx_l + 1}" class="kh-km-line">{_esc(ln)}</span></div>'
            )
        return (
            f'  <div id="{dom_id}" class="kh-el kh-km-card" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
            f'{f"<div class=kh-kicker-accent>{kicker}</div>" if kicker else ""}'
            f'<div class="kh-km-lines">{"".join(line_divs)}</div>'
            f'{f"<div class=kh-sub-light>{sub}</div>" if sub else ""}</div>'
        )

    if kind == "light_sweep_badge":
        kicker = _esc(params.get("kicker", "RESULT"))
        headline = _esc(params.get("headline", ""))
        sub = _esc(params.get("subtitle", ""))
        for t in (params.get("kicker"), params.get("headline"), params.get("subtitle")):
            if t:
                fp_texts.append(str(t))
        return (
            f'  <div id="{dom_id}" class="kh-el kh-sweep-badge" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
            f'<div class="kh-verdict-accent"></div>'
            f'<div id="{dom_id}_sheen" class="kh-sweep-sheen"></div>'
            f'<div class="kh-verdict-copy"><div class="kh-kicker-accent">{kicker}</div>'
            f'<div class="kh-verdict-title">{headline}</div>'
            f'{f"<div class=kh-verdict-sub>{sub}</div>" if sub else ""}</div></div>'
        )

    if kind == "freeze_callout":
        kicker = _esc(params.get("kicker", "KEY DETAIL"))
        headline = _esc(params.get("headline", ""))
        sub = _esc(params.get("subtitle", ""))
        badge = _esc(params.get("badge", "DETAIL HOLD"))
        for t in (params.get("kicker"), params.get("headline"), params.get("subtitle"), params.get("badge")):
            if t:
                fp_texts.append(str(t))
        return (
            f'  <div id="{dom_id}" class="kh-el kh-freeze-card" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
            f'<div class="kh-card-top"><span class="kh-kicker-dark">{kicker}</span><span class="kh-freeze-tag">{badge}</span></div>'
            f'<div class="kh-title-dark">{headline}</div>'
            f'{f"<div class=kh-sub-dark>{sub}</div>" if sub else ""}</div>'
        )

    if kind == "split_callout_light":
        kicker = _esc(params.get("kicker", "DETAIL"))
        headline = _esc(params.get("headline", ""))
        sub = _esc(params.get("subtitle", ""))
        tags = params.get("tags") or []
        fp_texts.extend([str(params.get("kicker", "")), str(params.get("headline", "")), str(params.get("subtitle", ""))] + [str(t) for t in tags])
        tags_html = "".join(f'<span class="kh-tag">{_esc(t)}</span>' for t in tags[:4])
        return (
            f'  <div id="{dom_id}" class="kh-el kh-card-light" style="{base_style}"{check_attr}{word_attr} '
            f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
            f'<div class="kh-kicker-dark">{kicker}</div>'
            f'<div class="kh-title-dark">{headline}</div>'
            f'{f"<div class=kh-sub-dark>{sub}</div>" if sub else ""}'
            f'{f"<div class=kh-tags>{tags_html}</div>" if tags_html else ""}</div>'
        )

    # Default high-contrast editorial headline / callout panel
    kicker = _esc(params.get("kicker", ""))
    headline = _esc(params.get("headline", ""))
    sub = _esc(params.get("subtitle", ""))
    badge = _esc(params.get("badge", ""))
    for t in (params.get("kicker"), params.get("headline"), params.get("subtitle"), params.get("badge")):
        if t:
            fp_texts.append(str(t))
    return (
        f'  <div id="{dom_id}" class="kh-el kh-card-dark" style="{base_style}"{check_attr}{word_attr} '
        f'data-layer="{layer_name}" data-layer-cat="{_esc(layer_cat)}">'
        f'<div class="kh-card-top">{f"<span class=kh-kicker-accent>{kicker}</span>" if kicker else ""}'
        f'{f"<span class=kh-pill>{badge}</span>" if badge else ""}</div>'
        f'<div id="{dom_id}_headline" class="kh-title-light">{headline}</div>'
        f'{f"<div class=kh-sub-light>{sub}</div>" if sub else ""}</div>'
    )


def compile_shotbook(
    shotbook: dict[str, Any],
    inventory: dict[str, Any],
    build_dir: str | Path,
    *,
    tools_root: Path | None = None,
    seecut_root: Path | None = None,
    word_time_map: dict[str, float] | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile canonical SHOTBOOK V2 into build/master.html, build/project.json, build/fp-manifest.txt, COMPILE_MAP.json."""
    ref_errs = validate_references(shotbook, inventory=inventory, registry=registry)
    if ref_errs:
        raise ValueError(f"Cannot compile invalid SHOTBOOK V2: {ref_errs[:5]}")

    bdir = Path(build_dir).resolve()
    bdir.mkdir(parents=True, exist_ok=True)
    assets_dir = bdir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Copy pinned local GSAP into build/assets/gsap.min.js (zero CDN/network dependency)
    gsap_src = _locate_local_gsap(tools_root)
    if gsap_src and gsap_src.is_file():
        shutil.copy2(gsap_src, assets_dir / "gsap.min.js")
        gsap_rel = "assets/gsap.min.js"
    else:
        raise FileNotFoundError("Pinned local gsap.min.js not found inside tools/node_modules/hyperframes")

    # Copy verified usable media/still evidence into build/assets (read-only copy; never copies ANALYSIS_ONLY!)
    asset_rel_map: dict[str, str] = {}
    primary_source_path: str | None = None
    primary_face_rect: list[float] | str | None = None
    for a in inventory.get("assets", []):
        if not a.get("usable_in_render"):
            continue
        if a["kind"] in ("a_roll", "b_roll") and primary_source_path is None:
            primary_source_path = a["canonical_path"]
            primary_face_rect = a.get("face_rect")
        if a["kind"] in ("real_ui", "still_asset"):
            safe_fn = sanitize_identifier(f"{a['asset_id']}_{Path(a['canonical_path']).name}")
            ext = Path(a["canonical_path"]).suffix.lower() or ".png"
            if not safe_fn.endswith(ext):
                safe_fn += ext
            dst = assets_dir / safe_fn
            shutil.copy2(a["canonical_path"], dst)
            asset_rel_map[a["asset_id"]] = f"assets/{safe_fn}"

    spec = shotbook["output_spec"]
    width = int(spec["width"])
    height = int(spec["height"])
    fps = spec["fps_num"] / spec["fps_den"]
    duration_sec = round(int(spec["total_frames"]) / fps, 4)
    theme_name = shotbook["story_arc"].get("theme", "editorial_obsidian_ivory")
    th = THEMES.get(theme_name, THEMES["editorial_obsidian_ivory"])
    wmap = word_time_map or {}

    compile_map = compile_master_timeline(shotbook, word_time_map=wmap)
    comp_errs = validate_compiled_events(compile_map, shotbook)
    if comp_errs:
        raise ValueError(f"Compiled timeline validation failed: {comp_errs}")

    # Build HTML elements & GSAP tween specs
    fp_texts: list[str] = []
    elements_html: list[str] = []
    js_tweens: list[dict[str, Any]] = []

    for ev in shotbook["visual_events"]:
        dom_id = sanitize_identifier(ev["event_id"])
        elements_html.append(
            _render_event_html(ev, width=width, height=height, asset_rel_map=asset_rel_map, word_time_map=wmap, fp_texts=fp_texts)
        )
        s_sec = round(ev["start_frame"] / fps, 4)
        e_sec = round(ev["end_frame"] / fps, 4)
        en_sec = round(max(1, int(ev.get("entry_frames", 6))) / fps, 4)
        ex_sec = round(max(1, int(ev.get("exit_frames", 4))) / fps, 4)
        tr = ev.get("transform") or {}
        params = ev.get("params") or {}
        js_tweens.append({
            "id": dom_id,
            "kind": str(params.get("kind") or "editorial_headline"),
            "start": s_sec,
            "end": e_sec,
            "entry": en_sec,
            "exit": ex_sec,
            "opacity": float(ev.get("opacity", 1.0)),
            "scale_from": float(tr.get("scale_from", 0.94)),
            "scale_to": float(tr.get("scale_to", 1.0)),
            "y_from": float(tr.get("y_offset_px", 16)),
            "easing": str(ev.get("easing", "power2.out")),
            "step_delays": params.get("step_delays_sec", [0.15, 1.55, 2.95]),
        })

    # Optional captions (only emitted if caption_policy == ON_REQUESTED and role == caption)
    for tx in shotbook.get("text_events", []):
        if tx.get("role") == "caption" and tx.get("caption_policy") == "ON_REQUESTED":
            cid = sanitize_identifier(tx["text_id"])
            bx, by, bw, bh = [float(v) for v in tx["position"]]
            fp_texts.append(tx["text"])
            elements_html.append(
                f'  <div id="{cid}" class="kh-el kh-caption" style="left:{bx*width:.1f}px;top:{by*height:.1f}px;'
                f'width:{bw*width:.1f}px;height:{bh*height:.1f}px;z-index:999;" data-check="box" '
                f'data-layer="{cid}" data-layer-cat="title">{_esc(tx["text"])}</div>'
            )
            js_tweens.append({
                "id": cid, "kind": "caption",
                "start": round(tx["start_frame"] / fps, 4),
                "end": round(tx["end_frame"] / fps, 4),
                "entry": 0.08, "exit": 0.06, "opacity": 1.0,
                "scale_from": 0.98, "scale_to": 1.0, "y_from": 4, "easing": "power2.out",
            })

    # Copy local hf-hw assets if available (Caveat handwritten font + helpers)
    hf_hw_dir = _locate_hf_hw(seecut_root)
    caveat_css = ""
    if hf_hw_dir and (hf_hw_dir / "fonts/Caveat-700-latin.woff2").is_file():
        shutil.copy2(hf_hw_dir / "fonts/Caveat-700-latin.woff2", assets_dir / "Caveat-700-latin.woff2")
        caveat_css = "@font-face { font-family: 'Caveat'; src: url('assets/Caveat-700-latin.woff2') format('woff2'); font-weight: 700; font-style: normal; }"

    # Camera timeline specs for #personWrap & #person
    cam_specs: list[dict[str, Any]] = []
    for c in shotbook.get("camera_events", []):
        tx, ty, tw, th_r = [float(v) for v in c["target_region"]]
        hide_person = (c.get("framing_to") == "hidden_for_evidence")
        cam_specs.append({
            "start": round(c["start_frame"] / fps, 4),
            "end": round(c["end_frame"] / fps, 4),
            "left": round(tx * width, 1),
            "top": round(ty * height, 1),
            "width": round(tw * width, 1),
            "height": round(th_r * height, 1),
            "radius": 22 if tw < 0.96 else 0,
            "border": 4 if tw < 0.96 else 0,
            "opacity": 0.0 if hide_person else 1.0,
            "scale_from": float(c["scale_from"]),
            "scale_to": float(c["scale_to"]),
            "pos_from": [round(float(c.get("pos_from", [0, 0])[0]) * width, 2), round(float(c.get("pos_from", [0, 0])[1]) * height, 2)],
            "pos_to": [round(float(c.get("pos_to", [0, 0])[0]) * width, 2), round(float(c.get("pos_to", [0, 0])[1]) * height, 2)],
            "anchor": [f"{float(c['anchor'][0])*100:.1f}%", f"{float(c['anchor'][1])*100:.1f}%"],
            "easing": str(c.get("easing", "power2.inOut")),
        })

    person_html = ""
    if shotbook["mode"] != "faceless" and primary_source_path:
        person_html = (
            f'  <div id="personWrap" class="kh-person-wrap" style="left:0px;top:0px;width:{width}px;height:{height}px;z-index:10;">\n'
            f'    <img id="person" class="kh-person-img" alt="" />\n'
            f'  </div>'
        )

    sans_stack = FONT_FALLBACK_STACKS["system-sans-bold"]
    mono_stack = FONT_FALLBACK_STACKS["mono-code"]

    html_doc = f"""<!DOCTYPE html>
<html lang="{_esc(shotbook.get('language', 'en'))}">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width={width}, height={height}, initial-scale=1.0" />
<title>KH-02 {_esc(shotbook['run_id'])}</title>
<style>
  {caveat_css}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: {width}px; height: {height}px; overflow: hidden;
    background: {th['paper_bg']};
    background-image: radial-gradient({th['paper_dot']} 1.5px, transparent 1.5px);
    background-size: 22px 22px;
    font-family: {sans_stack};
    -webkit-font-smoothing: antialiased;
  }}
  #stage {{ position: relative; width: {width}px; height: {height}px; overflow: hidden; }}
  .kh-person-wrap {{
    position: absolute; overflow: hidden; background: {th['canvas_bg']};
    box-shadow: 0 14px 32px rgba(0,0,0,0.28);
  }}
  .kh-person-img {{
    width: 100%; height: 100%; object-fit: cover; display: block;
    transform-origin: 50% 38%;
  }}
  .kh-el {{ position: absolute; opacity: 0; will-change: transform, opacity; }}
  .kh-spine-track {{
    width: 100%; height: 100%; background: rgba(255,255,255,0.22);
    border-radius: 99px; overflow: hidden; border: 1px solid rgba(28,24,22,0.18);
  }}
  .kh-spine-fill {{
    width: 0%; height: 100%; background: linear-gradient(90deg, {th['accent']}, {th['accent_soft']});
    border-radius: 99px;
  }}
  .kh-card-dark {{
    background: {th['card_bg']}; color: {th['card_fg']};
    border: 2px solid {th['card_border']}; border-left: 8px solid {th['accent']};
    border-radius: 18px; padding: 18px 22px; box-shadow: {th['shadow']};
    display: flex; flex-direction: column; justify-content: center; gap: 6px;
  }}
  .kh-card-top {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; }}
  .kh-kicker-accent {{
    font-size: 16px; font-weight: 800; letter-spacing: 0.10em;
    text-transform: uppercase; color: {th['accent_soft']};
  }}
  .kh-pill {{
    font-size: 14px; font-weight: 800; padding: 4px 12px; border-radius: 99px;
    background: {th['accent']}; color: #FFFFFF; letter-spacing: 0.04em;
  }}
  .kh-title-light {{ font-size: 36px; font-weight: 900; line-height: 1.14; color: {th['card_fg']}; }}
  .kh-sub-light {{ font-size: 20px; font-weight: 500; line-height: 1.32; color: {th['card_muted']}; }}
  .kh-card-light {{
    background: {th['light_card_bg']}; color: {th['light_card_fg']};
    border: 2.5px solid {th['card_bg']}; border-top: 7px solid {th['accent']};
    border-radius: 18px; padding: 16px 20px; box-shadow: {th['shadow']};
    display: flex; flex-direction: column; justify-content: center; gap: 8px;
  }}
  .kh-kicker-dark {{ font-size: 15px; font-weight: 900; letter-spacing: 0.10em; text-transform: uppercase; color: {th['accent']}; }}
  .kh-title-dark {{ font-size: 32px; font-weight: 900; line-height: 1.16; color: {th['light_card_fg']}; }}
  .kh-sub-dark {{ font-size: 19px; font-weight: 600; line-height: 1.32; color: #4A433D; }}
  .kh-tags {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 2px; }}
  .kh-tag {{
    font-size: 15px; font-weight: 800; padding: 5px 12px; border-radius: 8px;
    background: rgba(212,82,51,0.12); color: {th['light_card_fg']}; border: 1.5px solid {th['accent']};
  }}
  .kh-evid-frame {{
    background: {th['card_bg']}; border: 2.5px solid {th['card_border']};
    border-radius: 20px; overflow: hidden; box-shadow: {th['shadow']};
    display: flex; flex-direction: column;
  }}
  .kh-evid-bar {{
    height: 44px; padding: 0 16px; background: #14110F;
    display: flex; align-items: center; gap: 10px; border-bottom: 1px solid rgba(255,255,255,0.12);
  }}
  .kh-dot {{ width: 10px; height: 10px; border-radius: 50%; background: {th['accent']}; flex-shrink: 0; }}
  .kh-kicker {{ font-size: 15px; font-weight: 800; letter-spacing: 0.08em; color: {th['accent_soft']}; text-transform: uppercase; }}
  .kh-evid-sub {{ font-size: 14px; color: {th['card_muted']}; margin-left: auto; font-weight: 600; }}
  .kh-evid-media {{ flex: 1; overflow: hidden; background: #FFF; position: relative; }}
  .kh-evid-img {{ width: 100%; height: 100%; object-fit: cover; object-position: center top; display: block; }}
  .kh-evid-img-contain {{ width: 100%; height: 100%; object-fit: contain; object-position: center center; display: block; background: #FFFFFF; padding: 6px; }}
  .kh-chapter-pill {{
    display: flex; align-items: center; gap: 12px; padding: 0 18px;
    background: {th['card_bg']}; color: {th['card_fg']}; border-radius: 99px;
    border: 2px solid {th['accent_soft']}; box-shadow: {th['shadow']};
  }}
  .kh-ch-num {{
    font-size: 16px; font-weight: 900; padding: 4px 10px; border-radius: 99px;
    background: {th['accent_soft']}; color: #231B18; letter-spacing: 0.06em;
  }}
  .kh-ch-text {{ font-size: 19px; font-weight: 800; letter-spacing: 0.06em; text-transform: uppercase; }}
  .kh-grid-wrap {{
    background: {th['light_card_bg']}; border: 2.5px solid {th['card_bg']};
    border-radius: 20px; padding: 14px 16px; box-shadow: {th['shadow']};
    display: flex; flex-direction: column; gap: 10px;
  }}
  .kh-grid-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 10px; border-bottom: 2px solid rgba(110,31,36,0.16); padding-bottom: 6px; }}
  .kh-grid-title {{ font-size: 22px; font-weight: 900; color: {th['light_card_fg']}; }}
  .kh-grid-2x2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; flex: 1; }}
  .kh-grid-cell {{
    border-radius: 14px; padding: 12px 14px; border: 2px solid rgba(35,27,24,0.14);
    display: flex; flex-direction: column; justify-content: center; gap: 3px; opacity: 0;
  }}
  .kh-tone-burgundy {{ background: #FBF1EF; border-left: 6px solid #6E1F24; }}
  .kh-tone-pine {{ background: #EFF6F2; border-left: 6px solid #1E4D3B; }}
  .kh-tone-gold {{ background: #FCF7E8; border-left: 6px solid #D99B26; }}
  .kh-cell-num {{ font-size: 14px; font-weight: 900; color: {th['accent']}; letter-spacing: 0.08em; }}
  .kh-cell-title {{ font-size: 21px; font-weight: 900; color: #231B18; line-height: 1.15; }}
  .kh-cell-sub {{ font-size: 15px; font-weight: 600; color: #544943; }}
  .kh-pillars-wrap {{
    background: {th['light_card_bg']}; border: 2.5px solid {th['card_bg']};
    border-radius: 20px; padding: 14px 16px; box-shadow: {th['shadow']};
    display: flex; flex-direction: column; gap: 10px;
  }}
  .kh-pillars-3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; flex: 1; }}
  .kh-pillar {{
    border-radius: 14px; padding: 12px 10px; border: 2px solid rgba(35,27,24,0.14);
    display: flex; flex-direction: column; gap: 6px; opacity: 0;
  }}
  .kh-pillar-num {{ font-size: 26px; font-weight: 900; color: {th['accent']}; }}
  .kh-pillar-title {{ font-size: 20px; font-weight: 900; color: #231B18; line-height: 1.15; }}
  .kh-pillar-desc {{ font-size: 15px; font-weight: 600; color: #4A403A; line-height: 1.25; }}
  .kh-hl-card {{
    background: {th['light_card_bg']}; color: {th['light_card_fg']};
    border: 2.5px solid {th['card_bg']}; border-left: 9px solid {th['accent']};
    border-radius: 18px; padding: 18px 22px; box-shadow: {th['shadow']};
    display: flex; flex-direction: column; justify-content: center; gap: 8px;
  }}
  .kh-hl-line {{ font-size: 31px; font-weight: 900; line-height: 1.2; color: #231B18; }}
  .kh-hl-mark {{ position: relative; display: inline-block; padding: 0 6px; }}
  .kh-hl-brush {{
    position: absolute; left: 0; bottom: 2px; height: 48%; width: 0%;
    background: rgba(233,185,73,0.68); border-radius: 4px; z-index: 0;
  }}
  .kh-hl-txt {{ position: relative; z-index: 1; }}
  .kh-hand-note {{
    font-family: 'Caveat', cursive, sans-serif; font-size: 26px; font-weight: 700;
    color: {th['accent']}; margin-top: -12px; text-align: right;
  }}
  .kh-evid-foot {{
    padding: 12px 18px; background: {th['card_bg']}; color: {th['card_fg']};
    font-size: 22px; font-weight: 800; border-top: 1px solid rgba(255,255,255,0.12);
  }}
  .kh-step-rail {{
    background: {th['card_bg']}; color: {th['card_fg']};
    border: 2px solid {th['card_border']}; border-radius: 20px;
    padding: 18px 20px; box-shadow: {th['shadow']};
    display: flex; flex-direction: column; gap: 12px;
  }}
  .kh-rail-head {{ border-bottom: 1.5px solid rgba(255,255,255,0.14); padding-bottom: 10px; }}
  .kh-rail-title {{ font-size: 28px; font-weight: 900; color: {th['card_fg']}; margin-top: 2px; }}
  .kh-step-list {{ display: flex; flex-direction: column; gap: 10px; flex: 1; justify-content: space-between; }}
  .kh-step-item {{
    display: flex; align-items: center; gap: 14px; padding: 11px 14px;
    border-radius: 12px; background: rgba(255,255,255,0.06);
    border-left: 5px solid {th['accent']}; opacity: 0;
  }}
  .kh-step-num {{
    font-size: 24px; font-weight: 900; color: {th['accent_soft']};
    min-width: 38px; text-align: center;
  }}
  .kh-step-title {{ font-size: 21px; font-weight: 800; color: {th['card_fg']}; }}
  .kh-step-desc {{ font-size: 16px; color: {th['card_muted']}; margin-top: 2px; }}
  .kh-code-card {{
    background: #0B1220; color: #E2E8F0; border: 2px solid rgba(56,189,248,0.45);
    border-radius: 16px; overflow: hidden; box-shadow: {th['shadow']};
    display: flex; flex-direction: column;
  }}
  .kh-code-head {{
    background: #1E293B; color: #38BDF8; font-size: 16px; font-weight: 800;
    padding: 10px 16px; letter-spacing: 0.05em;
  }}
  .kh-code-pre {{
    font-family: {mono_stack}; font-size: 18px; line-height: 1.45;
    padding: 16px; white-space: pre-wrap; color: #F8FAFC;
  }}
  .kh-verdict {{
    background: {th['card_bg']}; color: {th['card_fg']};
    border: 2px solid {th['accent_soft']}; border-radius: 18px;
    overflow: hidden; box-shadow: {th['shadow']}; display: flex; align-items: stretch;
  }}
  .kh-verdict-accent {{ width: 12px; background: linear-gradient(180deg, {th['accent']}, {th['accent_soft']}); flex-shrink: 0; }}
  .kh-verdict-copy {{ padding: 14px 20px; display: flex; flex-direction: column; justify-content: center; gap: 4px; }}
  .kh-verdict-title {{ font-size: 34px; font-weight: 900; line-height: 1.15; }}
  .kh-verdict-sub {{ font-size: 19px; color: {th['card_muted']}; font-weight: 600; }}
  .kh-caption {{
    display: flex; align-items: center; justify-content: center; text-align: center;
    font-size: 30px; font-weight: 900; color: #FFFFFF;
    text-shadow: 0 3px 10px rgba(0,0,0,0.95), 0 0 3px #000000;
    background: rgba(15,15,15,0.62); border-radius: 12px; padding: 8px 14px;
  }}
  .kh-hand-wrap {{ pointer-events: none; }}
  .kh-clip-wipe {{ clip-path: inset(0% 100% 0% 0%); }}
  .kh-spot-wrap {{ overflow: visible; }}
  .kh-spot-scrim {{
    position: absolute; inset: -16px; border-radius: 26px;
    background: radial-gradient(circle at 50% 50%, rgba(0,0,0,0.08), rgba(15,12,10,0.52));
    pointer-events: none; opacity: 0; z-index: 0;
  }}
  .kh-spot-card {{
    position: relative; z-index: 1; width: 100%; height: 100%;
    background: {th['card_bg']}; color: {th['card_fg']};
    border: 3px solid {th['accent_soft']}; border-radius: 20px; padding: 16px 20px;
    box-shadow: 0 22px 48px rgba(0,0,0,0.44); display: flex; flex-direction: column; justify-content: center; gap: 8px;
  }}
  .kh-spot-media {{ flex: 1; overflow: hidden; border-radius: 12px; background: #FFF; }}
  .kh-spot-title {{ font-size: 30px; font-weight: 900; line-height: 1.15; color: {th['card_fg']}; }}
  .kh-km-card {{
    background: {th['card_bg']}; color: {th['card_fg']};
    border: 2.5px solid {th['card_border']}; border-left: 9px solid {th['accent_soft']};
    border-radius: 18px; padding: 16px 22px; box-shadow: {th['shadow']};
    display: flex; flex-direction: column; justify-content: center; gap: 6px;
  }}
  .kh-km-lines {{ display: flex; flex-direction: column; gap: 2px; }}
  .kh-km-mask {{ overflow: hidden; display: block; }}
  .kh-km-line {{ display: inline-block; font-size: 36px; font-weight: 900; line-height: 1.14; color: {th['card_fg']}; }}
  .kh-sweep-badge {{
    position: relative; overflow: hidden;
    background: {th['card_bg']}; color: {th['card_fg']};
    border: 2.5px solid {th['accent_soft']}; border-radius: 18px;
    box-shadow: {th['shadow']}; display: flex; align-items: stretch;
  }}
  .kh-sweep-sheen {{
    position: absolute; top: -20%; left: -45%; width: 32%; height: 140%;
    background: linear-gradient(105deg, transparent 0%, rgba(255,255,255,0.34) 50%, transparent 100%);
    transform: skewX(-18deg); pointer-events: none; z-index: 2;
  }}
  .kh-freeze-card {{
    background: {th['light_card_bg']}; color: {th['light_card_fg']};
    border: 3px solid {th['accent']}; border-radius: 18px; padding: 16px 20px;
    box-shadow: {th['shadow']}; display: flex; flex-direction: column; justify-content: center; gap: 6px;
  }}
  .kh-freeze-tag {{
    font-size: 13px; font-weight: 900; padding: 4px 10px; border-radius: 6px;
    background: {th['accent']}; color: #FFFFFF; letter-spacing: 0.08em;
  }}
</style>
</head>
<body>
<div id="stage">
{person_html}
{chr(10).join(elements_html)}
</div>
<script src="{gsap_rel}"></script>
<script>
(function() {{
  const DURATION = {duration_sec};
  const TWEENS = {json.dumps(js_tweens, ensure_ascii=False)};
  const CAMS = {json.dumps(cam_specs, ensure_ascii=False)};
  const tl = gsap.timeline({{ paused: true }});
  tl.set('#stage', {{ opacity: 1 }}, 0);
  tl.to('#stage', {{ opacity: 1, duration: DURATION, ease: 'none' }}, 0);

  // Virtual 2D camera & person container choreography
  const pw = document.getElementById('personWrap');
  const pi = document.getElementById('person');
  if (pw && pi && CAMS.length) {{
    const c0 = CAMS[0];
    tl.set(pw, {{ left: c0.left, top: c0.top, width: c0.width, height: c0.height, opacity: c0.opacity, borderRadius: c0.radius, border: c0.border ? c0.border + 'px solid {th["card_bg"]}' : '0px solid transparent' }}, 0);
    tl.set(pi, {{ scale: c0.scale_from, x: c0.pos_from[0], y: c0.pos_from[1], transformOrigin: c0.anchor[0] + ' ' + c0.anchor[1] }}, 0);
    for (let i = 0; i < CAMS.length; i++) {{
      const c = CAMS[i];
      const dur = Math.max(0.15, c.end - c.start);
      const reframeDur = Math.min(0.24, dur * 0.35);
      if (i > 0) {{
        const prevOp = CAMS[i - 1].opacity;
        const opDur = (prevOp !== c.opacity) ? 0.08 : reframeDur;
        tl.to(pw, {{
          left: c.left, top: c.top, width: c.width, height: c.height,
          borderRadius: c.radius,
          border: c.border ? c.border + 'px solid {th["card_bg"]}' : '0px solid transparent',
          duration: reframeDur, ease: c.easing
        }}, c.start);
        tl.to(pw, {{ opacity: c.opacity, duration: opDur, ease: 'power1.inOut' }}, c.start);
      }}
      tl.to(pi, {{
        scale: c.scale_to, x: c.pos_to[0], y: c.pos_to[1],
        duration: dur, ease: c.easing
      }}, c.start);
    }}
  }}

  // Compiled SHOTBOOK V2 visual & typography events (strict CSS opacity:0 + gsap.set/to)
  for (const tw of TWEENS) {{
    const sel = '#' + tw.id;
    if (tw.kind === 'progress_spine') {{
      tl.set(sel, {{ opacity: tw.opacity }}, 0);
      tl.set('#' + tw.id + '_fill', {{ width: '0%' }}, 0);
      tl.to('#' + tw.id + '_fill', {{ width: '100%', duration: DURATION, ease: 'none' }}, 0);
      continue;
    }}
    const holdDur = Math.max(0.05, tw.end - tw.start - tw.exit);
    tl.set(sel, {{ opacity: 0, scale: tw.scale_from, y: tw.y_from }}, 0);
    if (tw.start > 0.001) {{
      tl.set(sel, {{ opacity: 0, scale: tw.scale_from, y: tw.y_from }}, tw.start);
    }}
    tl.to(sel, {{ opacity: tw.opacity, scale: tw.scale_to, y: 0, duration: Math.min(tw.entry, Math.max(0.12, tw.end - tw.start)), ease: tw.easing }}, tw.start);
    if (tw.kind === 'step_rail_3') {{
      const delays = tw.step_delays || [0.15, 1.5, 2.9];
      for (let s = 1; s <= 3; s++) {{
        const ssel = sel + '_step_' + s;
        tl.set(ssel, {{ opacity: 0, x: -24 }}, 0);
        const st = tw.start + (delays[s - 1] || (s - 1) * 0.45);
        if (st < tw.end - 0.15) {{
          tl.set(ssel, {{ opacity: 0, x: -24 }}, st);
          tl.to(ssel, {{ opacity: 1, x: 0, duration: 0.28, ease: 'back.out(1.9)' }}, st);
        }}
      }}
    }}
    if (tw.kind === 'stagger_grid_4') {{
      for (let s = 1; s <= 4; s++) {{
        const csel = sel + '_cell_' + s;
        const st = tw.start + 0.08 + (s - 1) * 0.11;
        tl.set(csel, {{ opacity: 0, scale: 0.65, y: 18 }}, 0);
        tl.set(csel, {{ opacity: 0, scale: 0.65, y: 18 }}, st);
        tl.to(csel, {{ opacity: 1, scale: 1.0, y: 0, duration: 0.30, ease: 'back.out(2.2)' }}, st);
      }}
    }}
    if (tw.kind === 'step_pillars_3') {{
      const delays = tw.step_delays || [0.10, 0.42, 0.74];
      for (let s = 1; s <= 3; s++) {{
        const psel = sel + '_col_' + s;
        const st = tw.start + (delays[s - 1] || (s - 1) * 0.25);
        tl.set(psel, {{ opacity: 0, scale: 0.62, y: 20 }}, 0);
        tl.set(psel, {{ opacity: 0, scale: 0.62, y: 20 }}, st);
        tl.to(psel, {{ opacity: 1, scale: 1.0, y: 0, duration: 0.32, ease: 'back.out(2.2)' }}, st);
      }}
    }}
    if (tw.kind === 'highlighter_card') {{
      const bsel = sel + '_brush';
      tl.set(bsel, {{ width: '0%' }}, 0);
      tl.to(bsel, {{ width: '100%', duration: 0.42, ease: 'power2.out' }}, tw.start + 0.16);
    }}
    if (tw.kind === 'clip_mask_wipe') {{
      tl.set(sel, {{ clipPath: 'inset(0% 100% 0% 0%)', opacity: 0 }}, 0);
      tl.set(sel, {{ clipPath: 'inset(0% 100% 0% 0%)', opacity: tw.opacity }}, tw.start);
      tl.to(sel, {{ clipPath: 'inset(0% 0% 0% 0%)', duration: Math.min(0.45, Math.max(0.18, tw.entry)), ease: 'power2.inOut' }}, tw.start);
    }}
    if (tw.kind === 'spotlight_focus') {{
      const scSel = sel + '_scrim';
      tl.set(scSel, {{ opacity: 0 }}, 0);
      tl.to(scSel, {{ opacity: 1, duration: 0.28, ease: 'power2.out' }}, tw.start + 0.04);
    }}
    if (tw.kind === 'kinetic_phrase_mask') {{
      for (let ln = 1; ln <= 3; ln++) {{
        const lEl = document.getElementById(tw.id + '_line_' + ln);
        if (!lEl) continue;
        const st = tw.start + (ln - 1) * 0.09;
        tl.set(lEl, {{ yPercent: 115, opacity: 0 }}, 0);
        tl.set(lEl, {{ yPercent: 115, opacity: 0 }}, st);
        tl.to(lEl, {{ yPercent: 0, opacity: 1, duration: 0.30, ease: 'power3.out' }}, st);
      }}
    }}
    if (tw.kind === 'light_sweep_badge') {{
      const shSel = sel + '_sheen';
      tl.set(shSel, {{ left: '-45%', opacity: 0 }}, 0);
      tl.set(shSel, {{ left: '-45%', opacity: 1 }}, tw.start + 0.22);
      tl.to(shSel, {{ left: '125%', duration: 0.55, ease: 'power2.inOut' }}, tw.start + 0.22);
      tl.set(shSel, {{ opacity: 0 }}, tw.start + 0.80);
    }}
    if (tw.kind === 'freeze_callout') {{
      tl.set(sel, {{ scale: 0.92, opacity: 0 }}, 0);
      tl.to(sel, {{ scale: 1.0, opacity: tw.opacity, duration: 0.18, ease: 'power2.out' }}, tw.start);
    }}
    if (tw.kind === 'hand_underline' || tw.kind === 'hand_arrow' || tw.kind === 'hand_circle') {{
      const pEl = document.getElementById(tw.id + '_path');
      if (pEl && pEl.getTotalLength) {{
        const L = pEl.getTotalLength();
        tl.set(pEl, {{ strokeDasharray: L, strokeDashoffset: L }}, 0);
        tl.to(pEl, {{ strokeDashoffset: 0, duration: 0.38, ease: 'power2.out' }}, tw.start + 0.04);
      }}
      const hEl = document.getElementById(tw.id + '_head');
      if (hEl && hEl.getTotalLength) {{
        const Lh = hEl.getTotalLength();
        tl.set(hEl, {{ strokeDasharray: Lh, strokeDashoffset: Lh }}, 0);
        tl.to(hEl, {{ strokeDashoffset: 0, duration: 0.16, ease: 'power2.out' }}, tw.start + 0.36);
      }}
    }}
    if (tw.end < DURATION - 0.06) {{
      const exStart = Math.max(tw.start + tw.entry, tw.end - tw.exit);
      tl.to(sel, {{ opacity: 0, y: -10, duration: tw.exit, ease: 'power2.in' }}, exStart);
    }}
  }}

  // Warm-up GSAP cache so seeking forward/backward to t=0 is 100% deterministic
  tl.progress(1).progress(0).time(0);
  window.__timelines = window.__timelines || {{}};
  window.__timelines.master = tl;
}})();
</script>
</body>
</html>
"""
    html_path = bdir / "master.html"
    html_path.write_text(html_doc, encoding="utf-8")

    # Write fp-manifest.txt with every visible on-screen text string
    unique_fp = []
    seen_fp = set()
    for t in fp_texts:
        tt = t.strip()
        if tt and tt not in seen_fp:
            seen_fp.add(tt)
            unique_fp.append(tt)
    (bdir / "fp-manifest.txt").write_text("\n".join(unique_fp) + ("\n" if unique_fp else ""), encoding="utf-8")

    proj = export_project_config(
        shotbook, compile_map,
        primary_source_path=primary_source_path,
        face_rect=primary_face_rect,
        build_dir=bdir,
    )
    compile_map_path = bdir.parent / "COMPILE_MAP.json"
    compile_map_path.write_text(json.dumps(compile_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "build_dir": str(bdir),
        "html_path": str(html_path),
        "project_json_path": str(bdir / "project.json"),
        "fp_manifest_path": str(bdir / "fp-manifest.txt"),
        "compile_map_path": str(compile_map_path),
        "compile_map": compile_map,
        "project_config": proj,
    }
