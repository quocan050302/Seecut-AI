#!/bin/zsh
# seecut-ai safe preflight wrapper:
# - Never passes --dangerously-skip-permissions
# - Uses pinned workspace-local hyperframes & local agy binary when present
# - Runs upstream preflight.sh with SKIP_AGY_PING=1, then executes a safe non-bypass agy ping
# - Audits all occurrences of --dangerously-skip-permissions in upstream scripts

set -u
PROJECT_ROOT="${SEECUT_STANDALONE_ROOT:-$HOME/Projects/SeeCut-Olivia-Standalone}"
SEECUT_ROOT="${SEECUT_UPSTREAM_ROOT:-$PROJECT_ROOT/third_party/SeeCut}"
BUILD_DIR="${1:-}"

export PATH="$PROJECT_ROOT/tools/node_modules/.bin:$HOME/.gemini/bin:$HOME/.local/bin:$PATH"

print "=== seecut-ai Safe Preflight ==="
print "PROJECT_ROOT=$PROJECT_ROOT"
print "SEECUT_ROOT=$SEECUT_ROOT"

if [ ! -f "$SEECUT_ROOT/skill/seecut/scripts/preflight.sh" ]; then
  print "✗ Missing upstream preflight.sh at $SEECUT_ROOT/skill/seecut/scripts/preflight.sh"
  exit 2
fi

print "\n--- 1. Upstream preflight.sh (SKIP_AGY_PING=1, no permission bypass) ---"
if [ -n "$BUILD_DIR" ]; then
  SKIP_AGY_PING=1 zsh "$SEECUT_ROOT/skill/seecut/scripts/preflight.sh" "$BUILD_DIR"
else
  SKIP_AGY_PING=1 zsh "$SEECUT_ROOT/skill/seecut/scripts/preflight.sh"
fi

print "\n--- 2. Safe agy CLI ping (NO --dangerously-skip-permissions) ---"
AGY_BIN="${AGY_BIN:-$(command -v agy 2>/dev/null || echo "")}"
if [ -z "$AGY_BIN" ] || [ ! -x "$AGY_BIN" ]; then
  print "✗ agy CLI not found on PATH ($HOME/.gemini/bin/agy or $HOME/.local/bin/agy)"
else
  TD=$(mktemp -d -t seecut_safe_ping)
  PING=$(cd "$TD" && perl -e 'alarm 90; exec @ARGV' "$AGY_BIN" -p "Reply only two letters: OK" --new-project --add-dir . --model gemini-3.8-flash-high --print-timeout 60s 2>&1)
  RC=$?
  rm -rf "$TD"
  if [ "$RC" -eq 0 ] && print -r -- "$PING" | grep -q "OK"; then
    print "  ✓ Safe agy ping PASSED without --dangerously-skip-permissions (binary: $AGY_BIN)"
  else
    print "  ✗ Safe agy ping FAILED (exit=$RC): $(print -r -- "$PING" | tail -n 3)"
  fi
fi

print "\n--- 3. Upstream --dangerously-skip-permissions Security Audit ---"
grep -rn "dangerously-skip-permissions" "$SEECUT_ROOT/skill/seecut/scripts/" || print "  None found"
print "Note: Do NOT execute upstream scripts containing --dangerously-skip-permissions without explicit owner security approval."
