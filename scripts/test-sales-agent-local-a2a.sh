#!/bin/bash

# Test Local Sales Agent via A2A (JSON-RPC 2.0)

set -e

SALES_AGENT_URL="http://localhost:8094/a2a"
API_KEY="HVn6P9PWLykPgOKEWVuo5OpMP5fz8nDP"

echo "🔗 Testing Local Sales Agent via A2A (JSON-RPC 2.0)"
echo "Endpoint: $SALES_AGENT_URL"
echo ""

# Send message to get products
echo "📦 Step 1: Send message to get products..."
MESSAGE_ID=$(uuidgen)
MESSAGE_RESPONSE=$(curl -s -X POST "$SALES_AGENT_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "'"$MESSAGE_ID"'",
        "role": "user",
        "parts": [
          {
            "type": "text",
            "text": "Find advertising products for: increase brand awareness for Wonderstruck at https://wonderstruck.org/about/"
          }
        ]
      }
    }
  }')

echo "Message response:"
echo "$MESSAGE_RESPONSE" | jq '.'
echo ""

# Extract task ID if available
TASK_ID=$(echo "$MESSAGE_RESPONSE" | jq -r '.result.task.id // empty')

if [ -n "$TASK_ID" ]; then
  echo "📋 Step 2: Check task status..."
  echo "Task ID: $TASK_ID"

  STATUS_RESPONSE=$(curl -s -X POST "$SALES_AGENT_URL" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $API_KEY" \
    -d '{
      "jsonrpc": "2.0",
      "id": 2,
      "method": "tasks/get",
      "params": {
        "id": "'"$TASK_ID"'"
      }
    }')

  echo "Task status:"
  echo "$STATUS_RESPONSE" | jq '.result.task.status'
  echo ""

  # Extract artifacts if completed
  if echo "$STATUS_RESPONSE" | jq -e '.result.task.artifacts' > /dev/null 2>&1; then
    echo "📊 Task artifacts:"
    echo "$STATUS_RESPONSE" | jq '.result.task.artifacts'
  fi
else
  echo "⚠️ No task ID returned (might be synchronous response)"
fi
