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

CURL_RC=$?

# A failed call is NOT a skip. `curl -sf` swallows the body and the old
# `.action // "skipped"` default rendered a dead server, a malformed reply and a
# deliberate "not worth storing" identically — so an agent banked nothing and was
# told nothing. Log it durably; still exit 0 so the hook never fails the turn.
MEMO_LOG="${MEMO_ERROR_LOG:-$HOME/.memo/auto-store-errors.log}"
mkdir -p "$(dirname "$MEMO_LOG")" 2>/dev/null
if [ "$CURL_RC" -ne 0 ] || [ -z "$RESULT" ]; then
  echo "$(date -Iseconds) session=$SESSION_ID TRANSPORT_FAIL curl_rc=$CURL_RC (nothing was stored)" >> "$MEMO_LOG"
  echo "[memo] WARNING: auto-store unreachable (curl $CURL_RC) — nothing was stored" >&2
  exit 0
fi

ACTION=$(echo "$RESULT" | jq -r '.action // "malformed"' 2>/dev/null)
TITLE=$(echo "$RESULT" | jq -r '.title // ""' 2>/dev/null)

case "$ACTION" in
  created)
    echo "[memo] stored: ${TITLE:-untitled}"
    ;;
  updated)
    echo "[memo] updated: ${TITLE:-untitled}"
    ;;
  error)
    # Loud on purpose. This is the case that used to be invisible.
    KIND=$(echo "$RESULT" | jq -r '.error_kind // "provider_error"' 2>/dev/null)
    REASON=$(echo "$RESULT" | jq -r '.reason // ""' 2>/dev/null)
    echo "$(date -Iseconds) session=$SESSION_ID PROVIDER_FAIL kind=$KIND (nothing was stored) :: $REASON" >> "$MEMO_LOG"
    if [ "$KIND" = "payment_required" ]; then
      echo "[memo] ⛔ NOTHING WAS STORED — OpenRouter credit exhausted (402). Needs a top-up; retrying will not help." >&2
    else
      echo "[memo] ⛔ NOTHING WAS STORED — auto-store provider failure ($KIND). See $MEMO_LOG" >&2
    fi
    ;;
  malformed)
    echo "$(date -Iseconds) session=$SESSION_ID MALFORMED_RESPONSE (nothing was stored)" >> "$MEMO_LOG"
    echo "[memo] WARNING: auto-store returned an unreadable response — nothing was stored" >&2
    ;;
esac
