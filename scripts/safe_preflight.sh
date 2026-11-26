#!/bin/zsh
# seecut-ai hardened safe preflight wrapper (KH-02 P0):
# - NEVER passes --dangerously-skip-permissions
# - NEVER invokes mutable `npx hyperframes@latest`
# - Checks explicit pinned executables/versions directly (does not trust upstream exit 0)
# - Distinguishes --offline (code/local tools) from live agy gate
# - Parses exact normalized "OK" response (rejects "NOT OK" / substring false positives)
# - Emits structured JSON PRE_FLIGHT report with secret redaction
# Exit codes:
#   0 = all required checks for requested profile PASSED
#   1 = one or more required local/contract checks FAILED
#   2 = blocked environment / auth / region / timeout / missing upstream

set -uo pipefail

PROJECT_ROOT="${SEECUT_STANDALONE_ROOT:-$HOME/Projects/SeeCut-Olivia-Standalone}"
SEECUT_ROOT="${SEECUT_UPSTREAM_ROOT:-$PROJECT_ROOT/third_party/SeeCut}"
TOOLS_ROOT="${SEECUT_TOOLS_ROOT:-$PROJECT_ROOT/tools}"

MODE="live"
BUILD_DIR=""
JSON_OUT=""
SKIP_AGY_PING="${SKIP_AGY_PING:-0}"
AGY_TIMEOUT="${AGY_PING_TIMEOUT:-60}"

while [ $# -gt 0 ]; do
  case "$1" in
    --offline)
      MODE="offline"
      SKIP_AGY_PING=1
      shift
      ;;
    --live)
      MODE="live"
      shift
      ;;
    --skip-agy-ping)
      SKIP_AGY_PING=1
      shift
      ;;
    --json-out)
      JSON_OUT="${2:-}"
      shift 2
      ;;
    --build-dir)
      BUILD_DIR="${2:-}"
      shift 2
      ;;
    --timeout)
      AGY_TIMEOUT="${2:-60}"
      shift 2
      ;;
    -h|--help)
      printf '%s\n' "Usage: safe_preflight.sh [--offline|--live] [--skip-agy-ping] [--json-out <path>] [--build-dir <dir>] [--timeout <sec>]"
      exit 0
      ;;
    *)
      if [ -z "$BUILD_DIR" ]; then
        BUILD_DIR="$1"
        shift
      else
        printf '%s\n' "Unknown argument: $1" >&2
        exit 1
      fi
      ;;
  esac
done

export PATH="$TOOLS_ROOT/node_modules/.bin:$HOME/.gemini/bin:$HOME/.local/bin:$PATH"

# Collect check results into JSON via python3 / python (guaranteed deterministic JSON output across macOS/Linux/Windows)
PYTHON_SYS="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
if [ -n "$PYTHON_SYS" ]; then
  if ! "$PYTHON_SYS" -c "import sys" >/dev/null 2>&1; then
    PYTHON_SYS="$(command -v python 2>/dev/null || true)"
  fi
fi
if [ -z "$PYTHON_SYS" ]; then
  printf '%s\n' '{"status":"FAIL","exit_code":1,"error":"python3/python not found on PATH"}' >&2
  exit 1
fi

export PF_PROJECT_ROOT="$PROJECT_ROOT"
export PF_SEECUT_ROOT="$SEECUT_ROOT"
export PF_TOOLS_ROOT="$TOOLS_ROOT"
export PF_MODE="$MODE"
export PF_BUILD_DIR="$BUILD_DIR"
export PF_SKIP_AGY_PING="$SKIP_AGY_PING"
export PF_AGY_TIMEOUT="$AGY_TIMEOUT"
export PF_JSON_OUT="$JSON_OUT"

"$PYTHON_SYS" - <<'PY'
import json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

project_root = Path(os.environ["PF_PROJECT_ROOT"]).expanduser()
seecut_root = Path(os.environ["PF_SEECUT_ROOT"]).expanduser()
tools_root = Path(os.environ["PF_TOOLS_ROOT"]).expanduser()
mode = os.environ["PF_MODE"]
build_dir = os.environ.get("PF_BUILD_DIR", "")
skip_agy_ping = os.environ.get("PF_SKIP_AGY_PING", "0") == "1"
try:
    agy_timeout = int(os.environ.get("PF_AGY_TIMEOUT", "60"))
    if agy_timeout < 5 or agy_timeout > 300:
        agy_timeout = 60
except ValueError:
    agy_timeout = 60
json_out = os.environ.get("PF_JSON_OUT", "")


def redact(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"(https?://)([^:@/\s]+):([^@/\s]+)@", r"\1***:***@", text)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^\s\"']+", r"\1=[REDACTED]", text)
    text = re.sub(r"\b(AIza[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{16,}|ghp_[0-9A-Za-z]{20,}|Bearer\s+[0-9A-Za-z._-]{12,})\b", "[REDACTED]", text)
    return text


def run_cmd(cmd, timeout=15, cwd=None):
    try:
        eff_cmd = list(cmd)
        if os.name == "nt" and eff_cmd and os.path.isfile(eff_cmd[0]):
            try:
                head = Path(eff_cmd[0]).read_bytes()[:2]
                if head == b"#!":
                    bash_exe = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
                    if bash_exe and os.path.isfile(bash_exe):
                        eff_cmd = [bash_exe, *eff_cmd]
            except Exception:
                pass
        p = subprocess.run(eff_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)
        return p.returncode, redact(p.stdout), redact(p.stderr)
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except OSError as e:
        return 127, "", str(e)


checks = []
failed = False
blocked = False


def add_check(cid, required, status, detail, extra=None):
    global failed, blocked
    rec = {"id": cid, "required": required, "status": status, "detail": detail}
    if extra:
        rec.update(extra)
    checks.append(rec)
    if required:
        if status == "FAIL":
            failed = True
        elif status == "BLOCKED_ENV":
            blocked = True


# 1. Upstream pinned checkout verification (read-only)
upstream_scripts = seecut_root / "skill" / "seecut" / "scripts"
required_upstream = [
    upstream_scripts / "render.mjs",
    upstream_scripts / "geom-check.mjs",
    upstream_scripts / "schemas" / "check.json",
    upstream_scripts / "schemas" / "pair.json",
]
missing_up = [str(p) for p in required_upstream if not p.is_file()]
if missing_up:
    add_check("upstream_seecut", True, "BLOCKED_ENV", f"Missing pinned upstream files: {', '.join(missing_up)}")
else:
    add_check("upstream_seecut", True, "PASS", f"Verified upstream SeeCut scripts at {upstream_scripts}")

# 2. Node >= 22
node_bin = shutil.which("node")
if not node_bin:
    add_check("node", True, "FAIL", "node executable not found on PATH")
else:
    rc, out, err = run_cmd([node_bin, "-v"])
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)", out.strip())
    if rc == 0 and m and int(m.group(1)) >= 22:
        add_check("node", True, "PASS", f"{out.strip()} ({node_bin})")
    else:
        add_check("node", True, "FAIL", f"node>=22 required, got rc={rc} output={out.strip() or err.strip()}")

# 3. ffmpeg & ffprobe
for tool in ("ffmpeg", "ffprobe"):
    tbin = shutil.which(tool)
    if not tbin:
        add_check(tool, True, "FAIL", f"{tool} not found on PATH")
    else:
        rc, out, err = run_cmd([tbin, "-version"])
        line = (out or err).splitlines()[0] if (out or err).splitlines() else ""
        if rc == 0 and line:
            add_check(tool, True, "PASS", f"{line} ({tbin})")
        else:
            add_check(tool, True, "FAIL", f"{tool} failed rc={rc}: {err.strip()}")

# 4. Python + isolated faster-whisper venv
venv_py = Path(os.environ.get("SEECUT_VENV_PYTHON", str(tools_root / ".venv" / "bin" / "python")))
py_check_bin = str(venv_py) if venv_py.is_file() and os.access(venv_py, os.X_OK) else shutil.which("python3")
if not py_check_bin:
    add_check("python_faster_whisper", True, "FAIL", "python3 / venv python not found")
else:
    rc, out, err = run_cmd([py_check_bin, "-c", "import faster_whisper; print(faster_whisper.__version__)"])
    if rc == 0 and out.strip():
        add_check("python_faster_whisper", True, "PASS", f"faster-whisper {out.strip()} via {py_check_bin}")
    else:
        add_check("python_faster_whisper", True, "FAIL", f"faster_whisper unavailable in {py_check_bin}: {err.strip()[:160]}")

# 5. Pinned hyperframes (NO npx @latest fallback)
hf_bin = os.environ.get("HYPERFRAMES_BIN") or str(tools_root / "node_modules" / ".bin" / "hyperframes")
hf_pkg = tools_root / "node_modules" / "hyperframes" / "package.json"
if not (os.path.isfile(hf_bin) and os.access(hf_bin, os.X_OK)):
    # Also check explicit PATH binary only if NOT npx
    found_hf = shutil.which("hyperframes")
    if found_hf:
        hf_bin = found_hf
if not (hf_bin and os.path.isfile(hf_bin) and os.access(hf_bin, os.X_OK)):
    add_check("hyperframes_pinned", True, "FAIL", "Pinned hyperframes CLI missing (npx @latest fallback strictly disabled)")
else:
    ver = "unknown"
    if hf_pkg.is_file():
        try:
            ver = json.loads(hf_pkg.read_text(encoding="utf-8")).get("version", "unknown")
        except Exception:
            pass
    rc, out, err = run_cmd([hf_bin, "--version"], timeout=15)
    if rc == 0:
        add_check("hyperframes_pinned", True, "PASS", f"hyperframes@{ver} ({hf_bin})", {"version": ver})
    else:
        add_check("hyperframes_pinned", True, "FAIL", f"hyperframes --version failed rc={rc}: {err.strip()[:160]}")

# 6. Pinned playwright-core & Chromium headless shell
pw_pkg = tools_root / "node_modules" / "playwright-core" / "package.json"
chrome_bin = os.environ.get("JIANJI_CHROME", "")
if not chrome_bin:
    roots = [Path.home() / "Library/Caches/ms-playwright", Path.home() / ".cache/ms-playwright"]
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright")
    for root in roots:
        if root.is_dir():
            for d in sorted(root.iterdir(), reverse=True):
                if (d.name.startswith("chromium_headless_shell") or d.name.startswith("chromium-")) and d.is_dir():
                    for sub in d.iterdir():
                        for exe in ("chrome-headless-shell", "headless_shell", "chrome-headless-shell.exe", "chrome.exe"):
                            cand = sub / exe
                            if cand.is_file():
                                chrome_bin = str(cand)
                                break
                        if chrome_bin:
                            break
                if chrome_bin:
                    break
        if chrome_bin:
            break
if pw_pkg.is_file() and chrome_bin and os.path.isfile(chrome_bin):
    pw_ver = json.loads(pw_pkg.read_text(encoding="utf-8")).get("version", "unknown")
    add_check("playwright_chromium", True, "PASS", f"playwright-core@{pw_ver}, chrome={chrome_bin}")
else:
    add_check("playwright_chromium", True, "FAIL", f"playwright-core ({pw_pkg.is_file()}) or headless chromium ({bool(chrome_bin)}) missing")

# 7. Optional assets (never fail core)
sfx_dir = Path(os.environ.get("JIANJI_SFX_DIR", str(Path.home() / "Downloads/音效"))).expanduser()
add_check("optional_sfx_library", False, "PASS" if sfx_dir.is_dir() else "OPTIONAL_ABSENT",
          f"SFX dir {'present' if sfx_dir.is_dir() else 'absent (SFX_NOT_AVAILABLE, optional)'}")

# 8. Live agy CLI ping (NO --dangerously-skip-permissions; exact normalized "OK" match)
agy_bin = os.environ.get("AGY_BIN") or shutil.which("agy") or ""
if not agy_bin:
    for cand in (Path.home() / ".gemini/bin/agy", Path.home() / ".local/bin/agy"):
        if cand.is_file() and os.access(cand, os.X_OK):
            agy_bin = str(cand)
            break

if mode == "offline" or skip_agy_ping:
    add_check("agy_live_ping", False, "SKIPPED_OFFLINE",
              f"Live agy ping skipped (mode={mode}, skip_agy_ping={skip_agy_ping}); binary={'present' if agy_bin else 'absent'}")
else:
    if not agy_bin or not os.access(agy_bin, os.X_OK):
        add_check("agy_live_ping", True, "BLOCKED_ENV", "agy CLI binary not found or not executable")
    else:
        td = tempfile.mkdtemp(prefix="seecut_safe_ping_")
        marker = Path(td) / ".seecut_ping_owned"
        marker.write_text(str(os.getpid()), encoding="utf-8")
        try:
            cmd = [
                agy_bin,
                "-p", "Reply only two letters: OK",
                "--new-project",
                "--add-dir", ".",
                "--model", os.environ.get("AGY_MODEL", "gemini-3.8-flash-high"),
                "--print-timeout", f"{agy_timeout}s",
            ]
            rc, out, err = run_cmd(cmd, timeout=agy_timeout + 15, cwd=td)
            combined = f"{out}\n{err}".strip()
            # Extract exact normalized response token
            extracted = out.strip()
            try:
                parsed = json.loads(extracted)
                if isinstance(parsed, dict) and isinstance(parsed.get("response"), str):
                    extracted = parsed["response"].strip()
            except Exception:
                pass
            # Strip wrapping whitespace / quotes / trailing period only; NEVER accept "NOT OK"
            normalized = re.sub(r"^[\s\"'`]+|[\s\"'`.!]+$", "", extracted)
            if rc == 124 or re.search(r"time(d)? ?out|deadline", combined, re.I):
                add_check("agy_live_ping", True, "BLOCKED_ENV", f"agy ping timeout (rc={rc})")
            elif re.search(r"Authentication required|not logged in|please (re-?)?log ?in", combined, re.I):
                add_check("agy_live_ping", True, "BLOCKED_ENV", f"agy authentication required (rc={rc})")
            elif re.search(r"location is not supported|not eligible", combined, re.I):
                add_check("agy_live_ping", True, "BLOCKED_ENV", f"agy region unsupported (rc={rc})")
            elif rc == 0 and normalized == "OK":
                add_check("agy_live_ping", True, "PASS", f"Exact normalized OK verified without --dangerously-skip-permissions ({agy_bin})")
            else:
                add_check("agy_live_ping", True, "FAIL",
                          f"agy ping invalid (rc={rc}, normalized={normalized!r}; exact 'OK' required, 'NOT OK'/substrings rejected)")
        finally:
            if marker.is_file():
                shutil.rmtree(td, ignore_errors=True)

# 9. Upstream --dangerously-skip-permissions audit (read-only informational)
bypass_hits = []
if upstream_scripts.is_dir():
    for p in sorted(upstream_scripts.glob("*")):
        if p.is_file():
            try:
                for idx, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if "dangerously-skip-permissions" in line:
                        bypass_hits.append(f"{p.name}:{idx}")
            except Exception:
                pass

exit_code = 1 if failed else (2 if blocked else 0)
status = "FAIL" if failed else ("BLOCKED_ENV" if blocked else "PASS")
report = {
    "schema_version": "kh02.preflight.v1",
    "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "mode": mode,
    "status": status,
    "exit_code": exit_code,
    "no_bypass_enforced": True,
    "npx_latest_disabled": True,
    "checks": checks,
    "upstream_bypass_audit": bypass_hits,
}

payload = json.dumps(report, ensure_ascii=False, indent=2)
if json_out:
    out_p = Path(json_out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(payload + "\n", encoding="utf-8")

print(payload)
sys.exit(exit_code)
PY
exit $?
