#!/bin/bash
# PreToolUse hook: injects memo topic index once per session

MEMO_PORT="${MEMO_PORT:-8000}"
MEMO_URL="http://localhost:${MEMO_PORT}"
CONFIG_FILE="${HOME}/.memo/hooks.env"

[ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"
[ "${MEMO_PREWORK_RECALL:-true}" = "false" ] && exit 0

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && SESSION_ID="${PPID}"

MARKER="/tmp/.memo-prework-${SESSION_ID}"
[ -f "$MARKER" ] && exit 0
touch "$MARKER"

INDEX=$(curl -sf --max-time 5 "${MEMO_URL}/index" 2>/dev/null)
[ $? -ne 0 ] || [ -z "$INDEX" ] && exit 0

COUNT=$(echo "$INDEX" | jq 'length' 2>/dev/null)
[ -z "$COUNT" ] || [ "$COUNT" -eq 0 ] && exit 0

ITEMS=$(echo "$INDEX" | jq -r '.[] |
  (.title // (.id | .[0:8] + "...")) +
  (if (.tags | length) > 0 then " [" + (.tags | join(", ")) + "]" else "" end)' |
  head -50 | sed 's/^/  - /')

echo "---MEMO INDEX (${COUNT} stored)---"
echo "$ITEMS"
echo "Use memo_context or POST /context for full retrieval."
echo "---END MEMO INDEX---"
