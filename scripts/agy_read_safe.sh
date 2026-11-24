#!/bin/zsh
# Hardened non-bypass wrapper for YeJe-cpu/SeeCut scripts/agy_read.sh (KH-02 P0)
# - OMITS --dangerously-skip-permissions
# - Validates input file, rejects symlinks and forbidden/outside-allowlist paths
# - Checks SEECUT_DENY_EXTERNAL_MEDIA=1 transmission policy gate
# - Verifies mktemp, cp, free temp disk space, and ownership-marked trap cleanup
# - Uses strict argv (no shell eval of user question) and propagates exact return code

set -uo pipefail

if [ $# -lt 2 ] || [ $# -gt 3 ]; then
  print -u2 "Usage: agy_read_safe.sh <file> \"<question>\" [timeout_sec]"
  exit 2
fi

F="$1"
Q="$2"
T="${3:-540}"

if [ "${SEECUT_DENY_EXTERNAL_MEDIA:-0}" = "1" ]; then
  print -u2 "BLOCKED_MEDIA_TRANSMISSION: External media transmission is disabled by policy (SEECUT_DENY_EXTERNAL_MEDIA=1)."
  exit 4
fi

if [ -z "$Q" ]; then
  print -u2 "Error: question must be non-empty"
  exit 2
fi

if ! [[ "$T" =~ '^[0-9]+$' ]] || [ "$T" -lt 1 ] || [ "$T" -gt 1800 ]; then
  print -u2 "Error: timeout_sec must be an integer between 1 and 1800 (got: $T)"
  exit 2
fi

if [ -L "$F" ]; then
  print -u2 "Error: symlink input rejected for safety: $F"
  exit 2
fi

if [ ! -f "$F" ] || [ ! -r "$F" ]; then
  print -u2 "Error: readable regular file required: $F"
  exit 2
fi

# Resolve canonical realpath and validate allowlist + sensitive-path denylist
REAL_F="$(python3 -c '
import os, sys
p = os.path.realpath(sys.argv[1])
if os.path.islink(sys.argv[1]):
    sys.exit(10)
home = os.path.realpath(os.path.expanduser("~"))
deny_prefixes = [
    "/etc", "/private/etc", "/var/db",
    os.path.join(home, ".ssh"),
    os.path.join(home, ".aws"),
    os.path.join(home, ".gnupg"),
    os.path.join(home, ".kube"),
]
for d in deny_prefixes:
    if p == d or p.startswith(d + os.sep):
        sys.exit(11)
base = os.path.basename(p)
if base in (".env", "id_rsa", "id_ed25519", "credentials", "credentials.json"):
    sys.exit(11)
allow_raw = os.environ.get("SEECUT_ALLOWED_MEDIA_ROOTS", "").strip()
if allow_raw:
    roots = [os.path.realpath(os.path.expanduser(r)) for r in allow_raw.split(":") if r.strip()]
    if not any(p == r or p.startswith(r + os.sep) for r in roots):
        sys.exit(12)
print(p)
' "$F")"
VAL_RC=$?
if [ "$VAL_RC" -ne 0 ] || [ -z "$REAL_F" ]; then
  print -u2 "Error: input path rejected by path/allowlist policy (code=$VAL_RC): $F"
  exit 2
fi

AGY="${AGY_BIN:-$(command -v agy 2>/dev/null || echo "")}"
if [ -z "$AGY" ] || [ ! -x "$AGY" ]; then
  [ -x "$HOME/.gemini/bin/agy" ] && AGY="$HOME/.gemini/bin/agy"
fi
if [ -z "$AGY" ] || [ ! -x "$AGY" ]; then
  [ -x "$HOME/.local/bin/agy" ] && AGY="$HOME/.local/bin/agy"
fi
if [ -z "$AGY" ] || [ ! -x "$AGY" ]; then
  print -u2 "Error: agy CLI not found"
  exit 3
fi

# Verify bounded temp disk availability (>= 200 MiB or file_size * 2 + 50 MiB)
python3 -c '
import os, shutil, sys
src = sys.argv[1]
tmp_root = os.environ.get("TMPDIR") or "/tmp"
sz = os.path.getsize(src)
free = shutil.disk_usage(tmp_root).free
need = max(200 * 1024 * 1024, sz * 2 + 50 * 1024 * 1024)
if free < need:
    sys.stderr.write(f"Error: insufficient temp disk space ({free} < {need} bytes)\n")
    sys.exit(5)
' "$REAL_F" || exit $?

D="$(mktemp -d -t seecut_read)" || { print -u2 "Error: mktemp failed"; exit 3; }
MARKER="$D/.seecut_agy_read_owned_$$"
print -r -- "$$" > "$MARKER" || { print -u2 "Error: failed writing ownership marker"; rmdir "$D" 2>/dev/null || true; exit 3; }

cleanup() {
  if [ -n "${D:-}" ] && [ -d "$D" ] && [ -f "$D/.seecut_agy_read_owned_$$" ]; then
    rm -rf "$D"
  fi
}
trap cleanup EXIT INT TERM

SAFE_NAME="$(python3 -c '
import os, re, sys
b = os.path.basename(sys.argv[1])
stem, ext = os.path.splitext(b)
stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "media"
ext = re.sub(r"[^A-Za-z0-9.]+", "", ext)[:12]
print(f"{stem}{ext}")
' "$REAL_F")"

if ! cp -- "$REAL_F" "$D/$SAFE_NAME"; then
  print -u2 "Error: failed to copy input media into isolated temp dir"
  exit 3
fi

if [ -n "${JIANJI_PROXY:-}" ]; then
  export https_proxy="$JIANJI_PROXY" http_proxy="$JIANJI_PROXY" HTTPS_PROXY="$JIANJI_PROXY" HTTP_PROXY="$JIANJI_PROXY"
fi

PROMPT="看 @${SAFE_NAME} 。${Q}
直接凭观看作答，禁止启动任何后台 OCR/转录/采样子任务，不要读取其它文件。"

(
  cd "$D" || exit 3
  perl -e 'my $t = shift; alarm $t; exec @ARGV or die "exec failed: $!\n";' -- \
    "$((T + 60))" \
    "$AGY" \
    -p "$PROMPT" \
    --new-project \
    --add-dir . \
    --model "${AGY_MODEL:-gemini-3.8-flash-high}" \
    --print-timeout "${T}s"
)
RC=$?
exit "$RC"
