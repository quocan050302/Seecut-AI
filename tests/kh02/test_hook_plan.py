"""KH-02.R1 Executable Tests R1-07..R1-08: Hook Truthfulness, Opening Selection & Multi-Niche Pacing Contrast."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.kh02.planner import (
    audit_payoff,
    build_editor_brief,
    build_hook_candidates,
    choose_opening,
    emit_hook_and_pacing_artifacts,
    evaluate_hook_truthfulness,
    plan_attention_beats,
    resolve_pacing_budget,
)


def _mock_inventory() -> dict:
    return {
        "schema_version": "kh02.source_inventory.v1",
        "run_id": "test_hook",
        "assets": [
            {
                "asset_id": "aroll_01",
                "canonical_path": "/tmp/aroll.mp4",
                "kind": "a_roll",
                "rights": "OWNER_SUPPLIED_NONCOMMERCIAL",
                "usable_in_render": True,
                "sha256": "a" * 64,
                "media": {"width": 1080, "height": 1920, "fps_num": 24, "fps_den": 1, "nb_frames": 480, "duration_sec": 20.0, "has_audio": True},
            },
            {
                "asset_id": "ui_proof_01",
                "canonical_path": "/tmp/ui.png",
                "kind": "real_ui",
                "rights": "CAPTURED_REAL_EVIDENCE_MASKED",
                "usable_in_render": True,
                "sha256": "b" * 64,
                "media": {"width": 1080, "height": 1200, "fps_num": 24, "fps_den": 1, "nb_frames": 1, "duration_sec": 0.04, "has_audio": False},
            },
        ],
    }


def _mock_brief(mode: str = "product_demo", profile: str = "editorial_pro", energy: str | None = None) -> dict:
    raw = {
        "brief_id": f"b_{mode}",
        "mode": mode,
        "profile": profile,
        "objective": "Showcase authentic workflow without hype",
        "audience": "Creators",
        "platform": "reels",
        "aspect_ratio": "9:16",
        "language": "en",
        "tone": "direct" if mode == "product_demo" else "calm",
        "key_message": "Real storefront details in 20 seconds",
        "sensitive_claims": ["unverified revenue"],
        "license_decision": "NONCOMMERCIAL_PERSONAL_EVALUATION_PERMITTED",
        "sources": [{"asset_id": "aroll_01", "path": "aroll.mp4", "kind": "a_roll", "rights": "OWNER_SUPPLIED_NONCOMMERCIAL"}],
    }
    if energy:
        raw["energy_mode"] = energy
    return build_editor_brief(raw)


class TestR1HookAndPacing(unittest.TestCase):
    def test_r1_07_hook_truthfulness_blocks_fake_claims_spoilers_and_speech_reorder(self) -> None:
        """R1-07: generate 2-3 distinct openings; refuse fake claim, spoiler, unsupported asset & unauthorized speech reorder; calm opening valid."""
        inv = _mock_inventory()
        tr = {
            "words": [
                {"word_id": "w_0001", "text": "Here", "start_sec": 0.4, "end_sec": 0.6},
                {"word_id": "w_0002", "text": "is", "start_sec": 0.65, "end_sec": 0.8},
                {"word_id": "w_0003", "text": "the", "start_sec": 0.85, "end_sec": 1.0},
                {"word_id": "w_0004", "text": "page.", "start_sec": 1.05, "end_sec": 1.4},
            ],
            "phrases": [{"phrase_id": "ph_1", "text": "Here is the page.", "start_sec": 0.4, "end_sec": 1.4, "word_ids": ["w_0001", "w_0002", "w_0003", "w_0004"]}],
        }
        brief = _mock_brief("product_demo", "editorial_pro")
        opts = build_hook_candidates(brief, inv, transcript=tr, cut_policy="PRESERVE_SCRIPT_TRIM")
        self.assertGreaterEqual(len(opts), 3)
        self.assertEqual(len({o["option_id"] for o in opts}), len(opts))

        # Negative 1: fabricated viral / scarcity claim rejected
        ok_fake, iss_fake = evaluate_hook_truthfulness(
            {"option_id": "fake", "strategy": "10x guaranteed viral guaranteed", "opening_line": "Only 2 left!", "opening_visual": "x", "evidence_asset_ids": ["aroll_01"], "source_word_ids": ["w_0001"], "preserves_original_speech": True},
            brief=brief, inventory=inv, transcript=tr, cut_policy="PRESERVE_SCRIPT_TRIM",
        )
        self.assertFalse(ok_fake)
        self.assertTrue(any("fabricated_or_clickbait_claim" in i for i in iss_fake))

        # Negative 2: unsupported/dangling evidence asset rejected
        ok_asset, iss_asset = evaluate_hook_truthfulness(
            {"option_id": "bad_a", "strategy": "proof", "opening_line": "Here", "opening_visual": "x", "evidence_asset_ids": ["nonexistent_asset"], "source_word_ids": ["w_0001"], "preserves_original_speech": True},
            brief=brief, inventory=inv, transcript=tr, cut_policy="PRESERVE_SCRIPT_TRIM",
        )
        self.assertFalse(ok_asset)
        self.assertTrue(any("unsupported_evidence_asset" in i for i in iss_asset))

        # Negative 3: unauthorized speech reorder under PRESERVE_SCRIPT_TRIM rejected
        ok_reord, iss_reord = evaluate_hook_truthfulness(
            {"option_id": "reord", "strategy": "jump", "opening_line": "page.", "opening_visual": "x", "evidence_asset_ids": ["aroll_01"], "source_word_ids": ["w_0004"], "preserves_original_speech": True},
            brief=brief, inventory=inv, transcript=tr, cut_policy="PRESERVE_SCRIPT_TRIM",
        )
        self.assertFalse(ok_reord)
        self.assertTrue(any("unauthorized_speech_reorder" in i for i in iss_reord))

        # Negative 4: suspense spoiler rejected
        susp_brief = _mock_brief("narrative", "editorial_pro", energy="suspense")
        ok_spoil, iss_spoil = evaluate_hook_truthfulness(
            {"option_id": "spoil", "strategy": "ending reveal", "opening_line": "Here", "opening_visual": "x", "evidence_asset_ids": ["aroll_01"], "source_word_ids": ["w_0001"], "reveals_payoff_early": True, "preserves_original_speech": True},
            brief=susp_brief, inventory=inv, transcript=tr, cut_policy="LOCK_VOICE",
        )
        self.assertFalse(ok_spoil)
        self.assertIn("suspense_spoiler_violation", iss_spoil)

        # Positive: calm documentary opening valid with zero forced SFX
        calm_brief = _mock_brief("documentary", "minimal_documentary")
        calm_opts = build_hook_candidates(calm_brief, inv, transcript=tr, cut_policy="LOCK_VOICE")
        calm_dec = choose_opening(calm_opts, brief=calm_brief)
        self.assertEqual(calm_dec["selected_option"]["opening_audio_role"], "intentional_silence_voice_only")

    def test_r1_08_pacing_contrast_and_readable_hold_vs_empty_waiting(self) -> None:
        """R1-08: distinct pacing budgets across calm vs fast profiles; editorial_intent_only; readable hold accepted, empty waiting flagged."""
        inv = _mock_inventory()
        fast_brief = _mock_brief("product_demo", "editorial_pro", energy="kinetic")
        calm_brief = _mock_brief("documentary", "minimal_documentary", energy="calm")

        p_fast = resolve_pacing_budget(fast_brief, total_duration_sec=18.0)
        p_calm = resolve_pacing_budget(calm_brief, total_duration_sec=45.0)
        self.assertTrue(p_fast["editorial_intent_only"])
        self.assertTrue(p_calm["editorial_intent_only"])
        self.assertLess(p_fast["static_review_threshold_sec"], p_calm["static_review_threshold_sec"])
        self.assertFalse(p_fast["allow_calm_opening"])
        self.assertTrue(p_calm["allow_calm_opening"])

        beats = [
            {"beat_id": "b1", "start_frame": 0, "end_frame": 48, "semantic_function": "hook", "focus_subject": "Hook", "planned_information_change": "Open", "priority": 5, "intentional_hold": False},
            # 3.5s empty waiting beat (not marked intentional_hold) -> rehook_reason flagged in fast kinetic mode!
            {"beat_id": "b2", "start_frame": 48, "end_frame": 132, "semantic_function": "mechanism", "focus_subject": "Long wait", "planned_information_change": "Wait", "priority": 3, "intentional_hold": False},
            # 3.0s intentional readable payoff hold -> planned_quiet_hold=True, accepted cleanly!
            {"beat_id": "b3", "start_frame": 132, "end_frame": 204, "semantic_function": "payoff", "focus_subject": "Payoff diagram", "planned_information_change": "Clear result", "priority": 5, "intentional_hold": True},
        ]
        att_fast = plan_attention_beats(beats, brief=fast_brief, fps=24.0)
        self.assertIsNotNone(att_fast[1]["rehook_reason"])
        self.assertTrue(att_fast[2]["planned_quiet_hold"])

        opts = build_hook_candidates(fast_brief, inv)
        dec = choose_opening(opts, brief=fast_brief)
        payoff = audit_payoff(dec, beats, brief=fast_brief)
        self.assertTrue(payoff["promise_payoff_linked"])

        with tempfile.TemporaryDirectory() as td:
            emitted = emit_hook_and_pacing_artifacts(
                Path(td), run_id="r1_hook_test", brief=fast_brief, hook_decision=dec,
                pacing_budget=p_fast, attention_beats=att_fast, payoff_audit=payoff, beats=beats, fps=24.0,
            )
            for k in ("hook_options", "pacing_plan", "editorial_coverage", "hook_decision_md", "attention_beats_md"):
                self.assertTrue(emitted[k].is_file())


if __name__ == "__main__":
    unittest.main()

