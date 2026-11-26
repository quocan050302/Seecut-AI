"""KH-03.R4.3-02/03/04 Tests — Six Universal Semantic Motion Components, Non-Finance Units, Non-Spoiler & Collisions."""

from __future__ import annotations

import unittest

import numpy as np

from scripts.kh03.motion_components import (
    PALETTE_PRESETS,
    SEMANTIC_COMPONENT_KINDS,
    evaluate_spatial_collisions,
    render_component_onto_rgb,
    validate_component_payload,
)


class TestKH03R43CompositionMotionSuite(unittest.TestCase):
    def test_r43_04_all_six_semantic_components_animate_on_non_finance_units(self) -> None:
        """All 6 new semantic primitives validate & produce genuine pixel change across reveals using non-finance units (min/kcal/GB)."""
        self.assertEqual(len(SEMANTIC_COMPONENT_KINDS), 6)
        specs = [
            {
                "event_id": "dep_time", "kind": "resource_depletion", "start_sec": 0.0, "end_sec": 5.0,
                "kicker": "DAILY FOCUS BUDGET", "title": "120 min Study Block", "start_balance": 120, "unit": "min",
                "expected_remaining": 0, "full_frame": True,
                "deltas": [
                    {"label": "Notifications", "amount": 45, "reveal_sec": 1.0, "anim_sec": 0.4},
                    {"label": "Context switches", "amount": 75, "reveal_sec": 2.5, "anim_sec": 0.4},
                ],
                "depleted_callout": "0 MIN FOCUS LEFT",
            },
            {
                "event_id": "alloc_kcal", "kind": "resource_allocation", "start_sec": 0.0, "end_sec": 4.0,
                "kicker": "ATHLETE RECOVERY", "title": "Macro Split", "total": 2000, "unit": "kcal", "full_frame": True,
                "branches": [
                    {"label": "Carbohydrates", "amount": 1100, "color_role": "positive", "reveal_sec": 0.5},
                    {"label": "Protein & Fats", "amount": 900, "color_role": "accent", "reveal_sec": 1.5},
                ],
            },
            {
                "event_id": "contrast_gb", "kind": "contrast_transformation", "start_sec": 0.0, "end_sec": 4.0,
                "kicker": "STORAGE AUDIT", "title": "Raw vs Compressed", "unit": "GB", "full_frame": True,
                "scenario_a": {"label": "Uncompressed", "summary": "Full intermediate frames", "outcome_value": 48, "unit": "GB"},
                "scenario_b": {"label": "Streaming Proxy", "summary": "Bounded queue + proxy", "outcome_value": 4, "unit": "GB"},
                "reveal_b_sec": 1.5,
            },
            {
                "event_id": "spot_ill", "kind": "evidence_spotlight", "start_sec": 0.0, "end_sec": 3.0,
                "provenance": "ILLUSTRATIVE", "illustrative_label": "ILLUSTRATIVE DIAGRAM",
                "title": "Thermal Dissipation", "callout": "Heat spreads evenly across copper fins.",
            },
            {
                "event_id": "chain_hist", "kind": "causal_chain", "start_sec": 0.0, "end_sec": 4.0,
                "kicker": "HISTORY MECHANISM",
                "nodes": [
                    {"label": "Printing press spreads", "relation": "lowers book cost", "reveal_sec": 0.3},
                    {"label": "Literacy accelerates", "relation": "enables scientific exchange", "reveal_sec": 1.4},
                    {"label": "Rapid experimentation", "reveal_sec": 2.5},
                ],
            },
            {
                "event_id": "dec_cook", "kind": "decision_branch", "start_sec": 0.0, "end_sec": 4.0,
                "prompt": "SEARING TEMPERATURE", "unit": "min",
                "choices": [
                    {"choice": "Low Heat", "value": 12, "outcome": "Steams surface", "role": "danger", "reveal_sec": 0.4},
                    {"choice": "High Sear", "value": 3, "outcome": "Crisp crust", "role": "positive", "reveal_sec": 1.6},
                ],
            },
        ]

        pal = PALETTE_PRESETS["luxury_editorial"]
        for ev in specs:
            val = validate_component_payload(ev)
            self.assertTrue(val["pass"], f"{ev['event_id']} failed validation: {val}")
            f_early = np.full((640, 360, 3), 24, dtype=np.uint8)
            f_late = np.full((640, 360, 3), 24, dtype=np.uint8)
            ok1 = render_component_onto_rgb(f_early, ev, 0.45, default_palette=pal)
            ok2 = render_component_onto_rgb(f_late, ev, 2.85, default_palette=pal)
            self.assertTrue(ok1 and ok2)
            mad = float(np.mean(np.abs(f_late.astype(np.float32) - f_early.astype(np.float32))))
            self.assertGreater(mad, 0.5, f"{ev['kind']} must genuinely animate between early and late timestamps (mad={mad})")

    def test_r43_05_non_spoiler_depletion_and_swept_spatial_collision_guard(self) -> None:
        """Depletion delta must NOT appear before its reveal_sec; swept collision audit catches protected hand/face overlap."""
        ev = {
            "event_id": "dep_ns", "kind": "resource_depletion", "start_sec": 0.0, "end_sec": 4.0,
            "start_balance": 100, "unit": "pts", "full_frame": False, "y_norm": 0.68, "h_norm": 0.28,
            "deltas": [{"label": "Penalty", "amount": 50, "reveal_sec": 2.0, "anim_sec": 0.3}],
        }
        f_before_a = np.full((640, 360, 3), 30, dtype=np.uint8)
        f_before_b = np.full((640, 360, 3), 30, dtype=np.uint8)
        f_after = np.full((640, 360, 3), 30, dtype=np.uint8)
        render_component_onto_rgb(f_before_a, ev, 0.8)
        render_component_onto_rgb(f_before_b, ev, 1.8)
        render_component_onto_rgb(f_after, ev, 2.4)
        # Stable during hold before reveal_sec=2.0, then changes meaningfully after reveal_sec!
        self.assertLess(float(np.mean(np.abs(f_before_b.astype(np.float32) - f_before_a.astype(np.float32)))), 0.05)
        self.assertGreater(float(np.mean(np.abs(f_after.astype(np.float32) - f_before_b.astype(np.float32)))), 1.0)

        # Collision detection: overlapping protected hand box fails, clear box passes
        occ = [{"label": "hand_action", "start_sec": 0.5, "end_sec": 3.5, "x0": 0.1, "y0": 0.60, "x1": 0.5, "y1": 0.85, "max_overlap": 0.05}]
        bad_col = evaluate_spatial_collisions([ev], occupancy_windows=occ)
        self.assertFalse(bad_col["pass"])
        self.assertEqual(bad_col["collision_count"], 1)

        ev_safe = {**ev, "y_norm": 0.04, "h_norm": 0.20}
        good_col = evaluate_spatial_collisions([ev_safe], occupancy_windows=occ)
        self.assertTrue(good_col["pass"])


if __name__ == "__main__":
    unittest.main()
