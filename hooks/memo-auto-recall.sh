#!/bin/bash
# UserPromptSubmit hook: semantically recalls relevant memos

MEMO_PORT="${MEMO_PORT:-8000}"
MEMO_URL="http://localhost:${MEMO_PORT}"
MIN_SCORE="${MEMO_RECALL_MIN_SCORE:-0.5}"
TOKEN_BUDGET="${MEMO_RECALL_TOKEN_BUDGET:-2000}"
CONFIG_FILE="${HOME}/.memo/hooks.env"

[ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"
[ "${MEMO_AUTO_RECALL:-true}" = "false" ] && exit 0

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty' 2>/dev/null)

# Skip if prompt is too short to be meaningful
[ -z "$PROMPT" ] || [ "${#PROMPT}" -lt 15 ] && exit 0

RESULT=$(curl -sf --max-time 5 -X POST "${MEMO_URL}/context" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg q "$PROMPT" --argjson tb "$TOKEN_BUDGET" --argjson ms "$MIN_SCORE" \
    '{query: $q, token_budget: $tb, min_score: $ms, limit_per_query: 5}')" \
  2>/dev/null)

[ $? -ne 0 ] || [ -z "$RESULT" ] && exit 0

DOC_COUNT=$(echo "$RESULT" | jq -r '.doc_count // 0' 2>/dev/null)
[ "$DOC_COUNT" -eq 0 ] && exit 0

CONTENT=$(echo "$RESULT" | jq -r '.content // empty' 2>/dev/null)
[ -z "$CONTENT" ] && exit 0

echo "---RECALLED MEMO CONTEXT (${DOC_COUNT} docs)---"
echo "$CONTENT"
echo "---END RECALLED MEMOS---"
