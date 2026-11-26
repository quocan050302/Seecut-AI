"""KH-02.R1 Executable Tests R1-09..R1-14: Local SFX Scan, MCP Discovery, License/API Terms, Safe Download & Concurrent Dedup."""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from scripts.kh02.contracts import hash_artifact
from scripts.kh02.sfx_assets import (
    LocalProvider,
    McpProvider,
    assert_not_test_only_in_production,
    audit_cache_integrity,
    build_upstream_ledger,
    dedup_assets,
    discover_local_library,
    emit_sfx_asset_artifacts,
    ensure_cue_assets,
    measure_onset,
    probe_sfx,
    safe_download,
    verify_license,
)


def _make_test_wav(path: Path, *, freq: int = 880, dur: float = 0.35, delay: float = 0.05) -> Path:
    """Create a tiny TEST_ONLY PCM WAV tone with controlled leading silence for onset & download tests."""
    import math
    import struct
    import wave

    sr = 48000
    n = int(round(dur * sr))
    buf = bytearray()
    for i in range(n):
        t = i / float(sr)
        val = int(0.35 * 32767 * math.sin(2 * math.pi * freq * t)) if delay <= t <= delay + 0.18 else 0
        buf += struct.pack("<h", val)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))
    return path


class TestR1SfxProvider(unittest.TestCase):
    def test_r1_09_local_scan_bounded_symlink_html_dedup_and_cache_integrity(self) -> None:
        """R1-09: bounded local SFX scan rejects symlinks/HTML/empty/unlicensed, deduplicates SHA, and detects cache drift."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lib = root / "sfx_lib"
            lib.mkdir()
            wav1 = _make_test_wav(lib / "whoosh_air.wav", freq=600)
            # Duplicate exact bytes with different filename
            wav2 = lib / "whoosh_copy.wav"
            wav2.write_bytes(wav1.read_bytes())
            # Fake HTML masquerading as .wav
            (lib / "fake_error.wav").write_text("<!DOCTYPE html><html>404</html>", encoding="utf-8")
            # Empty 0-byte file
            (lib / "empty.wav").write_bytes(b"")
            # Symlink / junction pointing outside
            out_dir = root / "outside_dir"
            out_dir.mkdir()
            outside = _make_test_wav(out_dir / "outside.wav", freq=400)
            try:
                (lib / "sym_escape.wav").symlink_to(outside)
            except OSError:
                if os.name == "nt":
                    subprocess.run(["cmd", "/c", "mklink", "/J", str(lib / "sym_escape_dir"), str(out_dir)], check=True, capture_output=True)
                else:
                    raise

            # Explicit license manifest for approved files
            manifest = {
                "whoosh_air.wav": {"license_name": "CC0 1.0", "license_evidence": "https://creativecommons.org/publicdomain/zero/1.0/", "category": "whoosh"},
                "whoosh_copy.wav": {"license_name": "CC0 1.0", "license_evidence": "https://creativecommons.org/publicdomain/zero/1.0/", "category": "whoosh"},
            }
            (lib / "SFX_LICENSE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")

            scan = discover_local_library([lib], max_entries=50, max_depth=3)
            self.assertEqual(len(scan["assets"]), 1)  # wav1 + wav2 deduped to 1 canonical SHA
            self.assertEqual(len(scan["duplicates"]), 1)
            self.assertGreaterEqual(len(scan["rejected"]), 3)  # fake HTML, empty, symlink all rejected

            # Cache integrity passes, then fails on 1-byte tamper
            idx_doc = {"assets": scan["assets"]}
            ok_aud = audit_cache_integrity(lib, idx_doc)
            self.assertTrue(ok_aud["pass"])
            wav1.write_bytes(wav1.read_bytes() + b"\x00")
            bad_aud = audit_cache_integrity(lib, idx_doc)
            self.assertFalse(bad_aud["pass"])
            self.assertEqual(len(bad_aud["drifted"]), 1)

            # Refuse HOME scan
            home_scan = discover_local_library([Path.home()])
            self.assertEqual(len(home_scan["assets"]), 0)
            self.assertTrue(any("HOME" in r["reason"] for r in home_scan["rejected"]))

    def test_r1_10_mcp_tool_discovery_and_mock_error_handling(self) -> None:
        """R1-10: MCP tools/list discovery, missing config -> BLOCKED_PROVIDER_CONFIG, mock 401/403/429/offline/timeout."""
        # 1. Unconfigured MCP
        p_unconf = McpProvider("sfxmint", server_config={"configured": False})
        self.assertEqual(p_unconf.status()["status"], "BLOCKED_PROVIDER_CONFIG")
        self.assertFalse(p_unconf.capabilities()["can_download"])

        # 2. Search-only (no API key -> 0 download quota per SFXMint live docs)
        def invoker_search_only(tool: str, args: dict) -> dict:
            if tool == "tools/list":
                return {"tools": [{"name": "search_sfx"}, {"name": "download_sfx"}]}
            return {"http_status": 200, "results": []}

        p_nokey = McpProvider("sfxmint", server_config={"configured": True, "api_key_present": False}, tool_invoker=invoker_search_only)
        self.assertEqual(p_nokey.status()["status"], "SEARCH_ONLY")
        self.assertEqual(p_nokey.download("a1", "/tmp")["status"], "BLOCKED_AUTH")

        # 3. 401 / 403 / 429 / timeout / offline without endless retries
        for code, exp in ((401, "BLOCKED_AUTH"), (403, "BLOCKED_AUTH"), (429, "BLOCKED_QUOTA")):
            def _mk(c: int):
                def _inv(tool: str, args: dict) -> dict:
                    if tool == "tools/list":
                        return {"tools": [{"name": "search_sfx"}, {"name": "download_sfx"}]}
                    return {"http_status": c, "retry_after": "60"}
                return _inv
            p_err = McpProvider("mock_err", server_config={"configured": True, "api_key_present": True}, tool_invoker=_mk(code))
            self.assertEqual(p_err.search("whoosh")["status"], exp)
            self.assertEqual(p_err.download("x1", "/tmp")["status"], exp)

        def _inv_timeout(tool: str, args: dict) -> dict:
            if tool == "tools/list":
                return {"tools": [{"name": "search_sfx"}]}
            raise TimeoutError("simulated timeout")

        p_to = McpProvider("mock_to", server_config={"configured": True, "api_key_present": True}, tool_invoker=_inv_timeout)
        self.assertEqual(p_to.search("whoosh")["status"], "TIMEOUT")

        p_off = McpProvider("mock_off", server_config={"configured": True, "api_key_present": True}, offline=True)
        self.assertEqual(p_off.status()["status"], "OFFLINE")
        self.assertEqual(p_off.search("whoosh")["status"], "OFFLINE")

    def test_r1_11_license_and_provider_api_terms_separation(self) -> None:
        """R1-11: CC0 vs CC-BY attribution, NC/ND/UNKNOWN/no-evidence rejection, Freesound free API terms vs CC0, TEST_ONLY guard."""
        # Unknown / missing evidence rejected (never self-certify from filename)
        self.assertFalse(verify_license({"license_name": "UNKNOWN"})["approved"])
        self.assertFalse(verify_license({"license_name": "CC0", "license_evidence": ""})["approved"])

        # ND rejected everywhere
        self.assertEqual(verify_license({"license_name": "CC-BY-ND 4.0", "license_evidence": "url", "author": "Bob"})["license_status"], "REJECTED_ND_INCOMPATIBLE")

        # NC blocked for commercial
        self.assertEqual(
            verify_license({"license_name": "CC-BY-NC 4.0", "license_evidence": "url", "author": "Bob"}, intended_purpose="COMMERCIAL_AND_NONCOMMERCIAL")["license_status"],
            "REJECTED_NC_INCOMPATIBLE",
        )

        # CC-BY missing attribution rejected; with author approved
        self.assertEqual(verify_license({"license_name": "CC-BY 4.0", "license_evidence": "url"})["license_status"], "REJECTED_MISSING_ATTRIBUTION")
        self.assertTrue(verify_license({"license_name": "CC-BY 4.0", "license_evidence": "url", "author": "Alice"})["approved"])

        # Freesound free API noncommercial restriction NOT waived by CC0 file!
        fs_comm = verify_license(
            {"license_name": "CC0 1.0", "license_evidence": "https://freesound.org/s/1", "provider_terms_note": "FREESOUND_FREE_API_NONCOMMERCIAL"},
            intended_purpose="COMMERCIAL_AND_NONCOMMERCIAL",
        )
        self.assertFalse(fs_comm["approved"])
        self.assertEqual(fs_comm["license_status"], "REJECTED_PROVIDER_TERMS")

        # TEST_ONLY blocked in production
        with self.assertRaises(ValueError):
            assert_not_test_only_in_production([{"asset_id": "gen_tone", "test_only": True, "approved_purpose": "TEST_ONLY"}], production_mode=True)

    def test_r1_12_safe_download_security_ssrf_redirects_fake_html_and_part_cleanup(self) -> None:
        """R1-12: safe_download blocks SSRF/unlisted hosts/fake HTML/0-byte/oversized/redirects and leaves zero .part files."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_wav = _make_test_wav(root / "valid.wav", freq=700, dur=0.30, delay=0.04)
            wav_bytes = src_wav.read_bytes()

            class _Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, fmt, *args):
                    pass
                def do_GET(self):
                    if self.path == "/good.wav":
                        self.send_response(200)
                        self.send_header("Content-Type", "audio/wav")
                        self.end_headers()
                        self.wfile.write(wav_bytes)
                    elif self.path == "/fake.wav":
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(b"<html><body>Login required</body></html>")
                    elif self.path == "/empty.wav":
                        self.send_response(200)
                        self.end_headers()
                    elif self.path == "/big.wav":
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(b"0" * 50000)
                    elif self.path == "/loop":
                        self.send_response(302)
                        self.send_header("Location", "/loop")
                        self.end_headers()
                    elif self.path == "/quota":
                        self.send_response(429)
                        self.send_header("Retry-After", "120")
                        self.end_headers()

            srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                dl_dir = root / "cache"
                # 1. SSRF / non-HTTPS blocked when allow_local_test_http=False
                r_ssrf = safe_download(f"http://127.0.0.1:{port}/good.wav", dl_dir, allow_local_test_http=False)
                self.assertFalse(r_ssrf["ok"])
                self.assertEqual(r_ssrf["status"], "REJECTED_SCHEME")

                r_host = safe_download("https://evil.example.invalid/a.wav", dl_dir)
                self.assertFalse(r_host["ok"])
                self.assertEqual(r_host["status"], "REJECTED_HOST")

                # 2. Valid WAV download verified by ffprobe & atomic content-hash rename
                r_ok = safe_download(f"http://127.0.0.1:{port}/good.wav", dl_dir, allow_local_test_http=True)
                self.assertTrue(r_ok["ok"], r_ok)
                self.assertTrue(Path(r_ok["final_path"]).is_file())
                self.assertAlmostEqual(r_ok["onset"]["onset_sec"], 0.04, delta=0.025)

                # 3. Fake HTML, empty, oversized, redirect loop, 429 rejected + zero leftover .part files
                self.assertEqual(safe_download(f"http://127.0.0.1:{port}/fake.wav", dl_dir, allow_local_test_http=True)["status"], "INVALID_AUDIO_PAYLOAD")
                self.assertEqual(safe_download(f"http://127.0.0.1:{port}/empty.wav", dl_dir, allow_local_test_http=True)["status"], "EMPTY_PAYLOAD")
                self.assertEqual(safe_download(f"http://127.0.0.1:{port}/big.wav", dl_dir, allow_local_test_http=True, max_bytes=1024)["status"], "DOWNLOAD_FAILED_OR_OVERSIZED")
                self.assertEqual(safe_download(f"http://127.0.0.1:{port}/loop", dl_dir, allow_local_test_http=True, max_redirects=2)["status"], "REJECTED_REDIRECT_LIMIT")
                r_429 = safe_download(f"http://127.0.0.1:{port}/quota", dl_dir, allow_local_test_http=True)
                self.assertEqual(r_429["status"], "RATE_LIMITED_429")
                self.assertEqual(r_429["retry_after"], "120")

                self.assertEqual(list(dl_dir.glob("*.part")), [])
            finally:
                srv.shutdown()
                srv.server_close()

    def test_r1_13_concurrent_dedup_quota_and_deterministic_manifest(self) -> None:
        """R1-13: duplicate concurrent cue acquisitions download identical asset ONCE; deterministic order & quota enforcement."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wav_a = _make_test_wav(root / "a.wav", freq=500)
            wav_b = _make_test_wav(root / "b.wav", freq=900)
            dl_calls = {"count": 0}
            lock = threading.Lock()

            class _Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, fmt, *args):
                    pass
                def do_GET(self):
                    with lock:
                        dl_calls["count"] += 1
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(wav_a.read_bytes() if self.path == "/a.wav" else wav_b.read_bytes())

            srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                def _invoker(tool: str, args: dict) -> dict:
                    if tool == "tools/list":
                        return {"tools": [{"name": "search_sfx"}, {"name": "download_sfx"}]}
                    if tool == "search_sfx":
                        q = args.get("query", "")
                        aid = "rem_whoosh_1" if "whoosh" in q else "rem_pop_2"
                        return {"http_status": 200, "results": [{
                            "provider_asset_id": aid, "category": "whoosh" if "whoosh" in q else "pop",
                            "license_name": "CC0 1.0", "license_evidence": "https://creativecommons.org/publicdomain/zero/1.0/",
                        }]}
                    if tool == "download_sfx":
                        fn = "a.wav" if args["asset_id"] == "rem_whoosh_1" else "b.wav"
                        return {"http_status": 200, "download_url": f"http://127.0.0.1:{port}/{fn}"}
                    return {}

                prov = McpProvider("mock_mcp", server_config={"configured": True, "api_key_present": True, "allow_local_test_http": True}, tool_invoker=_invoker)
                # Two concurrent cues requesting the EXACT same whoosh asset + one cue exceeding max_downloads=1
                reqs = [
                    {"cue_id": "c1", "category": "whoosh", "query": "whoosh air"},
                    {"cue_id": "c2", "category": "whoosh", "query": "whoosh air"},
                    {"cue_id": "c3", "category": "pop", "query": "soft pop"},
                ]
                res = ensure_cue_assets(reqs, remote_provider=prov, cache_dir=root / "cache", max_downloads=1, workers=2)
                self.assertEqual(dl_calls["count"], 1)  # Strict single real download!
                self.assertEqual(res["downloads_used"], 1)
                self.assertEqual(res["resolved_by_cue"]["c3"]["status"], "BLOCKED_QUOTA")
                # Emit all 6 R1.03 contract files and verify upstream ledger compatibility
                emitted = emit_sfx_asset_artifacts(
                    root / "out",
                    assets=res["assets"],
                    provider_statuses=[prov.status()],
                    missing_categories=res["missing_categories"],
                    acquisition_log=res["acquisition_log"],
                    query_plan=res["query_plan"],
                    cache_dir=root / "cache",
                    overall_status="MCP_LIVE_READY",
                    next_action="None",
                )
                for k in ("asset_index", "provider_status", "query_plan", "cache_manifest", "acquisition_log", "rights_ledger_csv"):
                    self.assertTrue(emitted[k].is_file())
                ledger = build_upstream_ledger(res["assets"], production_mode=True)
                self.assertEqual(len(ledger), 1)
                self.assertIn("trim_start", ledger[0])
                self.assertIn("first_hit_len", ledger[0])
            finally:
                srv.shutdown()
                srv.server_close()

    def test_r1_14_local_only_fallback_vs_blocked_mcp_honesty(self) -> None:
        """R1-14: no key -> BLOCKED_PROVIDER_CONFIG (not live PASS); local library works independently without faking MCP live."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lib = root / "local_ok"
            lib.mkdir()
            _make_test_wav(lib / "click_soft.wav", freq=1200)
            (lib / "SFX_LICENSE_MANIFEST.json").write_text(
                json.dumps({"*": {"license_name": "CC0 1.0", "license_evidence": "local_cc0_pack_v1", "category": "click"}}),
                encoding="utf-8",
            )
            loc = LocalProvider([lib])
            mcp = McpProvider("sfxmint", server_config={"configured": False})
            self.assertEqual(loc.status()["status"], "READY")
            self.assertEqual(mcp.status()["status"], "BLOCKED_PROVIDER_CONFIG")

            res = ensure_cue_assets(
                [{"cue_id": "q1", "category": "click"}],
                local_provider=loc, remote_provider=mcp, cache_dir=root / "cache",
            )
            self.assertEqual(res["resolved_by_cue"]["q1"]["status"], "RESOLVED_LOCAL")
            self.assertEqual(res["downloads_used"], 0)


if __name__ == "__main__":
    unittest.main()
