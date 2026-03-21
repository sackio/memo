#!/bin/bash
# Stop hook: extract knowledge from the completed exchange and auto-store/update memos.
# Runs after every Claude response. Skips short exchanges and gracefully no-ops if the
# memo server is unreachable.

MEMO_PORT="${MEMO_PORT:-8000}"
MEMO_URL="http://localhost:${MEMO_PORT}"
CONFIG_FILE="${HOME}/.memo/hooks.env"

[ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"
[ "${MEMO_AUTO_STORE:-true}" = "false" ] && exit 0

INPUT=$(cat)

# Avoid re-entering if we're already inside a Stop hook cycle
STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)
[ "$STOP_ACTIVE" = "true" ] && exit 0

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""' 2>/dev/null)

# Extract text content from a message's content field (string or content-block array).
# Filters out tool_use and tool_result blocks — we only want human-readable text.
extract_text() {
  echo "$1" | jq -r '
    if type == "string" then .
    elif type == "array" then
      [.[] | select(.type == "text") | .text] | join("\n")
    else "" end
  ' 2>/dev/null
}

TRANSCRIPT=$(echo "$INPUT" | jq -c '.transcript // []' 2>/dev/null)

# Build readable content from the last 3 user/assistant pairs (up to 6 messages).
# Truncate each message to 3000 chars to keep the payload manageable.
CONTENT=$(echo "$TRANSCRIPT" | jq -r '
  .[-6:] |
  map(
    .role as $r |
    (if (.content | type) == "string" then .content
     elif (.content | type) == "array"
     then [.content[] | select(.type == "text") | .text] | join("\n")
     else "" end) as $t |
    select(($t | length) > 10) |
    (if $r == "user" then "User" else "Assistant" end) + ": " + ($t | .[0:3000])
  ) | join("\n\n---\n\n")
' 2>/dev/null)

# Skip if the combined exchange is too short to be meaningful
MIN_LEN="${MEMO_AUTO_STORE_MIN_LEN:-200}"
[ -z "$CONTENT" ] || [ "${#CONTENT}" -lt "$MIN_LEN" ] && exit 0

RESULT=$(curl -sf --max-time 45 -X POST "${MEMO_URL}/auto-store" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
    --arg content "$CONTENT" \
    --arg session_id "$SESSION_ID" \
    '{content: $content, session_id: $session_id}')" \
  2>/dev/null)

[ $? -ne 0 ] || [ -z "$RESULT" ] && exit 0

ACTION=$(echo "$RESULT" | jq -r '.action // "skipped"' 2>/dev/null)
TITLE=$(echo "$RESULT" | jq -r '.title // ""' 2>/dev/null)

case "$ACTION" in
  created)
    echo "[memo] stored: ${TITLE:-untitled}"
    ;;
  updated)
    echo "[memo] updated: ${TITLE:-untitled}"
    ;;
esac
