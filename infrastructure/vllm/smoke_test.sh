#!/usr/bin/env bash
# Checks that vLLM is up and returns a structured tool call, not plain text.
# Run while vLLM is running:  bash infrastructure/vllm/smoke_test.sh
set -euo pipefail

BASE="${LLM_BASE_URL:-http://localhost:8000/v1}"
MODEL="${LLM_MODEL:-squad-scout-llm}"

echo "== models =="
curl -sf "$BASE/models" | python3 -c 'import json,sys; print([m["id"] for m in json.load(sys.stdin)["data"]])'

echo "== tool call =="
RESP=$(curl -sf "$BASE/chat/completions" -H 'content-type: application/json' -d @- <<EOF
{
  "model": "$MODEL",
  "temperature": 0,
  "messages": [{"role": "user", "content": "How many FPL points does Bukayo Saka have?"}],
  "tools": [{
    "type": "function",
    "function": {
      "name": "search_players",
      "description": "Find Premier League players in Fantasy Premier League by name.",
      "parameters": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Player name"}},
        "required": ["name"]
      }
    }
  }]
}
EOF
)

echo "$RESP" | python3 -c '
import json, sys
msg = json.load(sys.stdin)["choices"][0]["message"]
calls = msg.get("tool_calls") or []
if calls:
    for c in calls:
        print("PASS: tool call ->", c["function"]["name"], c["function"]["arguments"])
else:
    print("FAIL: no tool_calls. Model replied with text:\n", msg.get("content"))
    sys.exit(1)
'
