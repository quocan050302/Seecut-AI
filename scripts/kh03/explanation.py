"""KH-03 Deep Understanding & Explanation Engine (Active Across All Five Universal Skills).

Constructs typed explanation graphs (concept/mechanism/cause/effect/comparison/example/conclusion),
verifies numeric & proportional arithmetic with exact Decimal math, chooses truthful visual
representations, generates 2-5 viewer comprehension questions with evidence anchors, and
audits explanation coverage (never captions-only on complex mechanisms).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .contracts import assert_kh03_schema, write_json_atomic


def validate_numeric_claims(
    numeric_claims: list[dict[str, Any]],
    *,
    full_text: str = "",
) -> dict[str, Any]:
    """Verify quantitative consistency, subtraction/allocation progressions & unit matching with exact Decimal math."""
    issues: list[str] = []
    steps: list[dict[str, Any]] = []

    if not numeric_claims:
        return {"checked_count": 0, "all_valid": True, "arithmetic_steps": [], "issues": []}

    # Check USD or same-unit allocation chain (e.g., starting balance 1200 minus 800, 200, 100 -> 100 remaining)
    by_unit: dict[str, list[Decimal]] = {}
    for c in numeric_claims:
        try:
            d = Decimal(str(c["value"]))
        except InvalidOperation:
            issues.append(f"Non-numeric value in claim {c.get('claim_id')}: {c.get('value')!r}")
            continue
        by_unit.setdefault(str(c.get("unit", "count")), []).append(d)

    for unit, vals in by_unit.items():
        if len(vals) >= 4:
            # Look for start >= sum(deductions) pattern (e.g., [1200, 1200, 800, 200, 100, 100] -> unique start=1200, parts=[800,200,100], rem=100)
            start = vals[0]
            # Deduplicate repeated mention of opening balance (like "$1,200 ... $1,200")
            tail = list(vals[1:])
            if tail and tail[0] == start:
                tail = tail[1:]
            if len(tail) >= 2 and start > Decimal("0"):
                deductions = tail[:-1]
                claimed_rem = tail[-1]
                total_ded = sum(deductions, Decimal("0"))
                computed_rem = start - total_ded
                if total_ded > Decimal("0") and total_ded <= start:
                    pcts = [round(float((d / start) * Decimal("100")), 3) for d in deductions]
                    rem_pct = round(float((computed_rem / start) * Decimal("100")), 3)
                    sum_pct = round(sum(pcts) + rem_pct, 3)
                    running = start
                    for idx_d, d in enumerate(deductions):
                        nxt = running - d
                        steps.append({
                            "step_index": idx_d + 1,
                            "unit": unit,
                            "before": str(running),
                            "delta": f"-{d}",
                            "after": str(nxt),
                            "bar_pct": pcts[idx_d],
                        })
                        running = nxt
                    steps.append({
                        "step_index": len(deductions) + 1,
                        "unit": unit,
                        "before": str(running),
                        "delta": "0",
                        "after": str(computed_rem),
                        "bar_pct": rem_pct,
                        "claimed_remainder": str(claimed_rem),
                        "bar_pct_sum": sum_pct,
                    })
                    if claimed_rem != computed_rem:
                        issues.append(
                            f"ARITHMETIC_MISMATCH ({unit}): {start} - sum({[str(x) for x in deductions]}) = {computed_rem}, "
                            f"but stated remainder is {claimed_rem}."
                        )
                    if abs(sum_pct - 100.0) > 0.05:
                        issues.append(f"PROPORTION_SUM_MISMATCH ({unit}): bar percentages sum to {sum_pct}% != 100.0%.")

    # Check explicit ratio / efficiency sanity (e.g., 3 units out / 1 unit in = 300%)
    if "count" in by_unit and "%" in by_unit and len(by_unit["count"]) >= 2:
        a, b = by_unit["count"][0], by_unit["count"][1]
        if b > Decimal("0") and a > b:
            expected_pct = (a / b) * Decimal("100")
            if expected_pct in by_unit["%"]:
                steps.append({
                    "step_index": len(steps) + 1,
                    "unit": "ratio_efficiency",
                    "formula": f"{a} / {b} * 100% = {expected_pct}%",
                    "verified": True,
                })

    return {
        "checked_count": len(numeric_claims),
        "all_valid": len(issues) == 0,
        "arithmetic_steps": steps,
        "issues": issues,
    }


def choose_visual_representation(
    node: dict[str, Any],
    *,
    skill_id: str,
    has_arithmetic_steps: bool = False,
) -> dict[str, Any]:
    """Select the clearest truthful visual mechanism for an explanation node (content-driven, not niche-hardcoded)."""
    nt = node["node_type"]
    if has_arithmetic_steps and nt in ("variable", "intermediate_step", "example"):
        return {
            "node_id": node["node_id"],
            "representation": "proportional_reservoir_and_rolling_counter",
            "capability_id": "kh03.proportional_reservoir",
            "illustrative_label_required": bool(node.get("illustrative", True)),
            "why": "True-proportion segmented bar + exact tabular counter demonstrates allocation math without distorting scale.",
        }
    if nt == "comparison":
        return {
            "node_id": node["node_id"],
            "representation": "labeled_comparison_matrix",
            "capability_id": "kh03.comparison_matrix",
            "illustrative_label_required": False,
            "why": "Side-by-side comparison clarifies contrasting properties directly.",
        }
    if nt in ("cause", "mechanism", "effect"):
        return {
            "node_id": node["node_id"],
            "representation": "progressive_causal_flow_graph",
            "capability_id": "kh03.causal_flow_graph",
            "illustrative_label_required": True,
            "why": "Explicit labeled cause -> mechanism -> effect chain makes hidden relationships visible.",
        }
    if nt in ("condition", "intermediate_step"):
        return {
            "node_id": node["node_id"],
            "representation": "annotated_step_timeline",
            "capability_id": "kh03.annotated_timeline",
            "illustrative_label_required": False,
            "why": "Ordered step progression builds understanding from prerequisite to outcome.",
        }
    if nt == "conclusion":
        return {
            "node_id": node["node_id"],
            "representation": "payoff_focus_summary",
            "capability_id": "kh03.cta_magnetic_underline" if skill_id in ("SK-01", "SK-02", "SK-05") else "r1.spotlight_focus",
            "illustrative_label_required": False,
            "why": "Resolves opening curiosity with clear takeaway hold.",
        }
    return {
        "node_id": node["node_id"],
        "representation": "contradiction_or_concept_lockup",
        "capability_id": "kh03.contradiction_lockup" if skill_id in ("SK-01", "SK-04") else "r1.kinetic_phrase_mask",
        "illustrative_label_required": bool(node.get("illustrative", False)),
        "why": "Frames core concept clearly so subsequent steps anchor to a known premise.",
    }


def plan_explanation(
    semantic_graph: dict[str, Any],
    input_analysis: dict[str, Any],
    *,
    skill_id: str,
    run_dir: Path | None = None,
    ctx: Any | None = None,
) -> dict[str, Any]:
    """Construct validated EXPLANATION_GRAPH.json, VIEWER_QUESTIONS.md & EXPLANATION_COVERAGE.json."""
    if ctx is not None:
        ctx.check_cancelled()
    phrases = semantic_graph.get("phrases") or []
    num_checks = validate_numeric_claims(
        input_analysis.get("numeric_claims") or [],
        full_text=input_analysis.get("normalized_script") or "",
    )
    has_arith = len(num_checks["arithmetic_steps"]) > 0

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    visual_reps: list[dict[str, Any]] = []

    role_to_node_type = {
        "hook": "concept",
        "question": "condition",
        "context": "concept",
        "demonstration": "example",
        "step": "intermediate_step",
        "mechanism": "mechanism",
        "comparison": "comparison",
        "evidence": "example",
        "payoff": "conclusion",
        "cta": "conclusion",
    }

    for idx, ph in enumerate(phrases):
        nid = f"exp_{idx + 1:02d}"
        role = ph.get("semantic_role", "context")
        nt = role_to_node_type.get(role, "concept")
        if ph.get("has_causal") and nt == "concept":
            nt = "cause"
        if ph.get("numeric_claims") and nt == "example":
            nt = "variable"
        complexity = "high" if (ph.get("has_causal") or len(ph.get("numeric_claims") or []) >= 2 or ph.get("has_comparison")) else (
            "medium" if (role in ("step", "mechanism", "evidence", "payoff") or ph.get("has_negation")) else "low"
        )
        illustrative = bool(any(c.get("illustrative") for c in (ph.get("numeric_claims") or [])) or nt in ("mechanism", "cause"))
        prereqs = [nodes[-1]["node_id"]] if idx > 0 else []

        node = {
            "node_id": nid,
            "node_type": nt,
            "label": ph["text"][:48].strip(),
            "summary": ph["text"].strip(),
            "word_ids": list(ph.get("word_ids") or []),
            "complexity": complexity,
            "prerequisite_ids": prereqs,
            "misconception_risk": (
                "Viewer may conflate headline balance with true spendable remainder."
                if (ph.get("has_negation") and ph.get("numeric_claims"))
                else ("Viewer may confuse correlation with causal mechanism." if ph.get("has_causal") else "Minimal misconception risk if clearly labeled.")
            ),
            "illustrative": illustrative,
            "source_evidence": ph.get("evidence_need"),
        }
        nodes.append(node)
        visual_reps.append(choose_visual_representation(node, skill_id=skill_id, has_arithmetic_steps=has_arith))

        if idx > 0:
            prev_n = nodes[idx - 1]
            rel = (
                "contrasts" if (ph.get("has_negation") or ph.get("has_comparison"))
                else ("causes" if ph.get("has_causal")
                      else ("quantifies" if ph.get("numeric_claims")
                            else ("illustrates" if role == "evidence" else "precedes")))
            )
            edges.append({
                "from_node": prev_n["node_id"],
                "to_node": nid,
                "relation": rel,
                "evidence_note": f"Grounded in spoken phrase {ph['phrase_id']} ({ph['start_frame']}..{ph['end_frame']}f).",
            })

    if not nodes:
        nodes.append({
            "node_id": "exp_01",
            "node_type": "concept",
            "label": "Visual showcase",
            "summary": "Primary visual subject",
            "word_ids": [],
            "complexity": "low",
            "prerequisite_ids": [],
            "misconception_risk": "None",
            "illustrative": False,
            "source_evidence": "source_aroll",
        })
        visual_reps.append(choose_visual_representation(nodes[0], skill_id=skill_id))

    # Derive 2-5 concrete viewer comprehension questions anchored to nodes & frames
    questions: list[dict[str, Any]] = []
    if nodes:
        questions.append({
            "question_id": "q_01_core_premise",
            "question": f"What core problem, question, or contrast is introduced at the start ({nodes[0]['label']})?",
            "expected_answer": nodes[0]["summary"],
            "evidence_node_ids": [nodes[0]["node_id"]],
            "target_frame_range": [int(phrases[0]["start_frame"]), int(phrases[0]["end_frame"])] if phrases else [0, 24],
        })
    if has_arith:
        last_step = num_checks["arithmetic_steps"][-1]
        questions.append({
            "question_id": "q_02_verified_math",
            "question": "After accounting for all stated deductions/ratios, what exact final value remains and is it proportional?",
            "expected_answer": f"Verified result = {last_step.get('after', last_step.get('formula', 'exact'))} (arithmetic_valid={num_checks['all_valid']}).",
            "evidence_node_ids": [n["node_id"] for n in nodes if n["node_type"] in ("variable", "conclusion")][:2] or [nodes[-1]["node_id"]],
            "target_frame_range": [int(phrases[min(1, len(phrases) - 1)]["start_frame"]), int(phrases[-1]["end_frame"])] if phrases else [0, 48],
        })
    else:
        mid_idx = len(nodes) // 2
        questions.append({
            "question_id": "q_02_mechanism",
            "question": f"What key mechanism, step, or comparison explains why this works ({nodes[mid_idx]['label']})?",
            "expected_answer": nodes[mid_idx]["summary"],
            "evidence_node_ids": [nodes[mid_idx]["node_id"]],
            "target_frame_range": [int(phrases[mid_idx]["start_frame"]), int(phrases[mid_idx]["end_frame"])] if phrases else [0, 48],
        })
    if len(nodes) >= 2:
        questions.append({
            "question_id": "q_03_takeaway",
            "question": "What grounded conclusion or actionable takeaway should the viewer remember at the end?",
            "expected_answer": nodes[-1]["summary"],
            "evidence_node_ids": [nodes[-1]["node_id"]],
            "target_frame_range": [int(phrases[-1]["start_frame"]), int(phrases[-1]["end_frame"])] if phrases else [0, 48],
        })

    # Verify explanation coverage: every medium/high complexity node has a concrete non-caption-only visual representation
    substantive = [n for n in nodes if n["complexity"] in ("medium", "high")]
    rep_map = {r["node_id"]: r for r in visual_reps}
    covered = [n for n in substantive if n["node_id"] in rep_map and rep_map[n["node_id"]].get("capability_id")]
    cov_ratio = round(len(covered) / float(len(substantive)), 4) if substantive else 1.0

    graph = {
        "schema_version": "kh03.explanation_graph.v1",
        "run_id": input_analysis["run_id"],
        "selected_skill_id": skill_id,
        "nodes": nodes,
        "edges": edges,
        "numeric_checks": num_checks,
        "visual_representations": visual_reps,
        "viewer_questions": questions[:5],
        "coverage_ratio": cov_ratio,
    }
    assert_kh03_schema(graph, "explanation_graph")

    if run_dir is not None:
        cov_doc = verify_explanation_coverage(graph)
        q_lines = [
            f"# Viewer Comprehension Questions — `{input_analysis['run_id']}` (`{skill_id}`)",
            "",
            "> Derived from explanation graph for independent comprehension QA (NOT a live audience study).",
            "",
        ]
        for q in graph["viewer_questions"]:
            q_lines.append(f"### `{q['question_id']}`")
            q_lines.append(f"- **Question:** {q['question']}")
            q_lines.append(f"- **Expected Grounded Answer:** {q['expected_answer']}")
            q_lines.append(f"- **Evidence Nodes:** `{', '.join(q['evidence_node_ids'])}` | **Frames:** `{q.get('target_frame_range')}`")
            q_lines.append("")
        q_md = "\n".join(q_lines)
        if ctx is not None:
            ctx.check_cancelled()
            ctx.publish_json("EXPLANATION_GRAPH.json", graph, run_dir / "analysis" / "EXPLANATION_GRAPH.json", schema_name="explanation_graph")
            ctx.publish_json("EXPLANATION_COVERAGE.json", cov_doc, run_dir / "analysis" / "EXPLANATION_COVERAGE.json")
            ctx.publish_text("VIEWER_QUESTIONS.md", q_md, run_dir / "analysis" / "VIEWER_QUESTIONS.md")
        else:
            write_json_atomic(run_dir / "analysis" / "EXPLANATION_GRAPH.json", graph)
            write_json_atomic(run_dir / "analysis" / "EXPLANATION_COVERAGE.json", cov_doc)
            (run_dir / "analysis" / "VIEWER_QUESTIONS.md").write_text(q_md, encoding="utf-8")

    return graph


def verify_explanation_coverage(explanation_graph: dict[str, Any]) -> dict[str, Any]:
    """Audit whether every substantive claim has an accurate visual representation and valid arithmetic."""
    nodes = explanation_graph.get("nodes", [])
    reps = {r["node_id"]: r for r in explanation_graph.get("visual_representations", [])}
    missing = [n["node_id"] for n in nodes if n["complexity"] in ("medium", "high") and n["node_id"] not in reps]
    num_ok = bool(explanation_graph.get("numeric_checks", {}).get("all_valid", True))
    cov = float(explanation_graph.get("coverage_ratio", 0.0))
    return {
        "schema_version": "kh03.explanation_coverage.v1",
        "total_nodes": len(nodes),
        "substantive_nodes": len([n for n in nodes if n["complexity"] in ("medium", "high")]),
        "missing_visual_node_ids": missing,
        "coverage_ratio": cov,
        "numeric_arithmetic_valid": num_ok,
        "pass": (len(missing) == 0 and num_ok and cov >= 0.85),
    }


def evaluate_comprehension(
    explanation_graph: dict[str, Any],
    visual_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check whether planned/compiled visual events actually cover the explanation graph's viewer questions."""
    q_results: list[dict[str, Any]] = []
    for q in explanation_graph.get("viewer_questions", []):
        fr = q.get("target_frame_range") or [0, 999999]
        f0, f1 = int(fr[0]), int(fr[1])
        supporting_evs = [
            ev["event_id"] for ev in visual_events
            if int(ev["start_frame"]) < f1 and int(ev["end_frame"]) > f0
        ]
        q_results.append({
            "question_id": q["question_id"],
            "supported_by_visual_events": bool(supporting_evs),
            "supporting_event_ids": supporting_evs,
        })
    supported = sum(1 for r in q_results if r["supported_by_visual_events"])
    ratio = round(supported / float(max(1, len(q_results))), 4)
    return {
        "evaluation_class": "DETERMINISTIC_STRUCTURAL_PROXY_NOT_HUMAN_STUDY",
        "questions_total": len(q_results),
        "questions_supported": supported,
        "support_ratio": ratio,
        "details": q_results,
        "pass": (ratio >= 0.80 and bool(explanation_graph.get("numeric_checks", {}).get("all_valid", True))),
    }
