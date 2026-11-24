#!/bin/zsh
# Audited no-bypass wrapper for YeJe-cpu/SeeCut scripts/agy_read.sh
# Preserves exact upstream isolated temp directory, timeout, prompt anti-subtask rules, and model,
# while OMITTING --dangerously-skip-permissions (verified exit 0 on Antigravity CLI 1.5.15).
# Upstream reference: ~/Projects/SeeCut-Olivia-Standalone/third_party/SeeCut/skill/seecut/scripts/agy_read.sh (unmodified).

F="$1"; Q="$2"; T="${3:-540}"
[ -f "$F" ] || { echo "Usage: agy_read_safe.sh <file> \"<question>\" [timeout_sec]"; exit 2; }
[ -n "$JIANJI_PROXY" ] && export https_proxy=$JIANJI_PROXY http_proxy=$JIANJI_PROXY HTTPS_PROXY=$JIANJI_PROXY HTTP_PROXY=$JIANJI_PROXY
D=$(mktemp -d -t seecut_read)
cp "$F" "$D/"
N=$(basename "$F")
AGY="${AGY_BIN:-$(command -v agy 2>/dev/null || echo "$HOME/.gemini/bin/agy")}"
[ -x "$AGY" ] || AGY="$HOME/.local/bin/agy"
[ -x "$AGY" ] || { echo "Error: agy CLI not found"; rm -rf "$D"; exit 3; }

cd "$D" && perl -e "alarm $((T+60)); exec @ARGV" "$AGY" -p "看 @$N 。$Q
直接凭观看作答，禁止启动任何后台 OCR/转录/采样子任务，不要读取其它文件。" --new-project --add-dir . --model gemini-3.8-flash-high --print-timeout ${T}s
RC=$?
rm -rf "$D"
exit $RC
