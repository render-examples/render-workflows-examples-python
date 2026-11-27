"""
Chat with the API from a terminal.

Opens a conversation with the first ticket in tickets.json, sends a follow-up,
and prints each reply. Set API_URL to your API service's URL (it defaults to
http://localhost:8000).
"""

import json
import os
import pathlib
import time
import urllib.error
import urllib.request
import uuid

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{API_URL}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def send(conversation_id: str, content: str) -> dict:
    """Send a message and wait for its turn to finish."""
    print(f"\nYou: {content}")
    status, body = call(
        "POST",
        f"/conversations/{conversation_id}/messages",
        # A new ID per message. Resending with the same ID is safe.
        {"client_message_id": str(uuid.uuid4()), "content": content},
    )
    if status not in (200, 202):
        raise SystemExit(f"POST failed ({status}): {body}")
    message_id = body["message_id"]

    while True:
        _, conversation = call("GET", f"/conversations/{conversation_id}/messages")
        mine = next(m for m in conversation["messages"] if m["id"] == message_id)
        if mine["status"] in ("done", "failed"):
            break
        time.sleep(2)

    if mine["status"] == "failed":
        print(f"Turn failed: {mine['error']}")
        return mine
    reply = next(m for m in conversation["messages"] if m["reply_to"] == message_id)
    print(f"Support: {reply['content']}")
    return reply


if __name__ == "__main__":
    ticket = json.loads((pathlib.Path(__file__).parent / "tickets.json").read_text())[0]
    conversation_id = f"chat-{uuid.uuid4().hex[:8]}"
    print(f"Conversation {conversation_id}")
    send(
        conversation_id,
        f"{ticket['subject']}. {ticket['body']} My account is {ticket['account_id']}.",
    )
    send(conversation_id, "Thanks. Can you confirm the refund request is open?")
