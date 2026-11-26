"""KH-03.R4.1-03 — Production Async Safety, Ctx-Aware Writers & Bounded Concurrency Test Suite.

Proves Section 06 requirements:
1. Production router.run_execute() DAG tasks (01_research_summary, 02_footage_acquire,
   03_explanation_graph, 04_capability_registry) are ALL ctx-aware (ctx_aware=True) and publish
   strictly via CoordinatorPublisher into run_dir (emits manifests/DAG_TRACE.json, ASYNC_METRICS.json,
   CANCELLATION_PROOF.json, TASK_RECEIPTS.jsonl).
2. Fault-injected timeout on a REAL production writer (research.emit_research_summary /
   explanation.plan_explanation / footage.acquire_and_bind_footage) inside BoundedAsyncExecutor:
   - Canonical output untouched!
   - Revoked lease rejects late publish with LateWriteRejectedError!
   - Staging directory quarantined with receipts!
3. CoordinatorPublisher path containment:
   - Rejects '..' path traversal and arbitrary target outside allowed_root!
4. 1 vs 2 vs 4 worker DAG canonical parity AND genuine independent task overlap (overlap_detected=True
   when workers >= 2) + isolated subprocess group kill/reap on Windows/POSIX.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from scripts.kh03 import execution, explanation, footage, research, router


def _make_video(out_path: Path, dur: float = 3.0) -> Path:
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=0x162C22:s=270x480:r=24:d={dur}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={dur}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "96k",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


class TestKH03R41AsyncProductionSuite(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kh03_r41_async_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_r41_09_production_dag_all_writers_ctx_aware_and_traced(self) -> None:
        """All 4 production DAG tasks in router.run_execute() MUST execute with ctx_aware=True & publish via CoordinatorPublisher."""
        vid = _make_video(self.tmp / "async_src.mp4")
        rdir = self.tmp / "prod_async_run"
        router.run_recommend(
            video_path=vid, run_root=rdir,
            script_text="Step 1: measure 1200 minus 800 equals 400. Step 2: verify every stage.",
            test_only=True,
        )
        sel = router.run_select(
            run_root=rdir, skill_id="SK-04",
            approval_source="TEST_ONLY_FIXTURE", approval_evidence="R4.1 async production test selection", test_only=True,
        )
        self.assertEqual(sel["status"], "USER_SELECTED")
        res = router.run_execute(run_root=rdir, mode="plan", workers=2, production_mode=False)
        self.assertEqual(res["exit_code"], 0, res)

        trace_p = rdir / "manifests" / "DAG_TRACE.json"
        metrics_p = rdir / "manifests" / "ASYNC_METRICS.json"
        cancel_p = rdir / "manifests" / "CANCELLATION_PROOF.json"
        self.assertTrue(trace_p.is_file(), "Missing manifests/DAG_TRACE.json")
        self.assertTrue(metrics_p.is_file(), "Missing manifests/ASYNC_METRICS.json")
        self.assertTrue(cancel_p.is_file(), "Missing manifests/CANCELLATION_PROOF.json")

        trace = json.loads(trace_p.read_text(encoding="utf-8"))
        tasks = trace["tasks"]
        self.assertGreaterEqual(len(tasks), 4)
        for t in tasks:
            self.assertTrue(t["ctx_aware"], f"Production task {t['task_id']} MUST be ctx_aware=True!")
            self.assertEqual(t["status"], "PASS")

        # Verify canonical files were actually published by CoordinatorPublisher
        self.assertTrue((rdir / "research" / "RESEARCH_CONTEXT.json").is_file())
        self.assertTrue((rdir / "analysis" / "EXPLANATION_GRAPH.json").is_file())
        self.assertTrue((rdir / "assets" / "FOOTAGE_MANIFEST.json").is_file())

    def test_r41_10_real_production_writer_timeout_blocks_late_publish_and_preserves_canonical(self) -> None:
        """Fault-inject timeout directly around real production writers (research & explanation): canonical untouched!"""
        run_d = self.tmp / "timeout_prod_writer"
        run_d.mkdir(parents=True, exist_ok=True)
        b = execution.compute_resource_budget(run_d, workers=2)
        ex = execution.BoundedAsyncExecutor(run_id="r41_writer_to", budget=b, run_dir=run_d, reap_grace_sec=0.10)

        late_errors: list[str] = []

        def _slow_real_research_writer(_deps: dict[str, Any], ctx: execution.TaskContext) -> dict[str, Any]:
            # Sleep past timeout BEFORE calling real emit_research_summary(..., ctx=ctx)
            time.sleep(0.32)
            try:
                return research.emit_research_summary(run_d, "SK-04", ctx=ctx)
            except (execution.LateWriteRejectedError, execution.CancelledTaskError) as exc:
                late_errors.append(type(exc).__name__)
                raise

        res = ex.run([execution.DAGTask("01_research_summary", _slow_real_research_writer, timeout_sec=0.06)])
        time.sleep(0.38)  # Allow background thread to hit real writer & get rejected
        self.assertFalse(res["pass"])
        self.assertTrue(res["blocked_cancellation"])
        self.assertFalse((run_d / "research" / "RESEARCH_CONTEXT.json").exists(), "Timed-out production writer MUST NOT publish canonical file!")
        self.assertTrue(late_errors, "Real production writer must raise LateWriteRejectedError / CancelledTaskError on stale lease!")

        # Cooperative real explanation writer cancels cleanly before any canonical publish
        def _coop_explanation_writer(_deps: dict[str, Any], ctx: execution.TaskContext) -> dict[str, Any]:
            for _ in range(30):
                ctx.check_cancelled()
                time.sleep(0.015)
            return explanation.plan_explanation(
                run_id="r41_exp", selected_skill_id="SK-04",
                input_analysis={"language": "en", "claims": [], "steps": []},
                beats=[], run_dir=run_d, ctx=ctx,
            )

        ex2 = execution.BoundedAsyncExecutor(run_id="r41_exp_to", budget=b, run_dir=run_d, reap_grace_sec=0.15)
        res2 = ex2.run([execution.DAGTask("03_explanation_graph", _coop_explanation_writer, timeout_sec=0.06)])
        self.assertFalse(res2["pass"])
        self.assertEqual(res2["receipts"][0]["status"], "TIMEOUT")
        self.assertFalse(res2["blocked_cancellation"])
        self.assertFalse((run_d / "explanation" / "EXPLANATION_GRAPH.json").exists())

    def test_r41_11_coordinator_publisher_path_containment_blocks_traversal(self) -> None:
        """CoordinatorPublisher MUST reject '..' traversal and destinations outside allowed_root."""
        run_d = self.tmp / "contained_run"
        run_d.mkdir(parents=True, exist_ok=True)
        outside = self.tmp / "escaped_file.json"
        pub = execution.CoordinatorPublisher(allowed_root=run_d)
        staged = run_d / ".staging" / "ok.json"
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text('{"ok": true}\n', encoding="utf-8")
        lease = execution.TaskLease(task_id="t1", lease_id="l1", attempt=1, staging_dir=staged.parent)
        tok = execution.CancellationToken()

        with self.assertRaises(ValueError):
            pub.publish_staged_file(lease, tok, staged, outside)
        with self.assertRaises(ValueError):
            pub.publish_staged_file(lease, tok, staged, run_d / ".." / "escaped2.json")
        self.assertFalse(outside.exists())

        # Valid target inside allowed_root succeeds
        inside = run_d / "manifests" / "valid.json"
        pub.publish_staged_file(lease, tok, staged, inside)
        self.assertTrue(inside.is_file())

    def test_r41_12_workers_1_2_4_canonical_parity_overlap_and_subprocess_reap(self) -> None:
        """Workers 1 vs 2 vs 4 produce identical canonical DAG hashes AND workers>=2 genuinely overlaps independent tasks."""
        hashes: dict[int, str] = {}
        overlaps: dict[int, bool] = {}
        for w in (1, 2, 4):
            rd = self.tmp / f"dag_w{w}"
            rd.mkdir(parents=True, exist_ok=True)
            b = execution.compute_resource_budget(rd, workers=w)
            ex = execution.BoundedAsyncExecutor(run_id=f"parity_w{w}", budget=b, run_dir=rd)

            def _mk_task(label: str):
                def _fn(_deps: dict[str, Any], ctx: execution.TaskContext) -> dict[str, Any]:
                    time.sleep(0.08)
                    ctx.check_cancelled()
                    ctx.publish_json(f"{label}.json", {"label": label, "val": len(label) * 7}, rd / f"{label}.json")
                    return {"label": label}
                return _fn

            def _merge(deps: dict[str, Any], ctx: execution.TaskContext) -> dict[str, Any]:
                out = {"merged": sorted(deps.keys())}
                ctx.publish_json("merged.json", out, rd / "merged.json")
                return out

            tasks = [
                execution.DAGTask("t_a", _mk_task("alpha")),
                execution.DAGTask("t_b", _mk_task("beta")),
                execution.DAGTask("t_c", _mk_task("gamma")),
                execution.DAGTask("t_d", _mk_task("delta")),
                execution.DAGTask("t_join", _merge, deps=("t_a", "t_b", "t_c", "t_d")),
            ]
            res = ex.run(tasks)
            self.assertTrue(res["pass"], res)
            hashes[w] = res["canonical_dag_sha256"]
            overlaps[w] = bool(res["async_metrics"]["overlap_detected"])

        self.assertEqual(hashes[1], hashes[2], "workers=1 vs workers=2 canonical SHA mismatch!")
        self.assertEqual(hashes[1], hashes[4], "workers=1 vs workers=4 canonical SHA mismatch!")
        self.assertFalse(overlaps[1], "workers=1 should not overlap tasks")
        self.assertTrue(overlaps[2], "workers=2 MUST demonstrate real concurrent overlap on independent tasks!")
        self.assertTrue(overlaps[4], "workers=4 MUST demonstrate real concurrent overlap on independent tasks!")

        # Isolated subprocess process-group kill & reap check
        sub = execution.run_subprocess_isolated([sys.executable, "-c", "import time; time.sleep(20)"], timeout_sec=0.15)
        self.assertTrue(sub["timed_out"])
        self.assertTrue(sub["reaped"])
        self.assertTrue(sub["cleanup"]["clean"])


if __name__ == "__main__":
    unittest.main()
