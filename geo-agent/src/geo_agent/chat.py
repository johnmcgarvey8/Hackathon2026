import argparse
import os
import uuid
from pathlib import Path

import httpx

from geo_agent.__main__ import load_environment


def send_message(client: httpx.Client, chat_id: str, message: str, key: str) -> dict:
    current = client.get(f"/conversations/{chat_id}")
    current.raise_for_status()
    response = client.post(f"/conversations/{chat_id}/messages", json={
        "message": message, "expected_revision": current.json()["revision"], "idempotency_key": key,
    })
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Talk to the local, read-only GEO evidence agent.")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--conversation", help="Resume a saved conversation ID")
    parser.add_argument("--message", help="Send one message and exit")
    arguments = parser.parse_args()
    load_environment()
    data_dir = Path(os.environ.get("GEO_DATA_DIR", Path(__file__).resolve().parents[2] / ".data"))
    token = os.environ.get("GEO_API_TOKEN")
    if token is None:
        try:
            token = (data_dir / "local-api-token").read_text(encoding="ascii").strip()
        except OSError:
            parser.exit(1, "Start the local GEO server first; its token is not available.\n")
    with httpx.Client(base_url=f"http://127.0.0.1:{arguments.port}", timeout=310,
                      headers={"Authorization": f"Bearer {token}"}, trust_env=False, follow_redirects=False) as client:
        try:
            policy = client.get("/chat-policy")
            policy.raise_for_status()
            budget = policy.json()["budget"]
            print(f"Chat requests remaining: {budget['remaining']}/{budget['limit']}. Read-only; no evaluation or publishing tools.")
            if arguments.conversation:
                chat_id = arguments.conversation
            else:
                response = client.post("/conversations")
                response.raise_for_status()
                chat_id = response.json()["chat_id"]
            print(f"Conversation: {chat_id}")
            while True:
                try:
                    message = arguments.message if arguments.message is not None else input("You (or /exit): ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if message == "/exit":
                    break
                if not message or len(message) > 4000:
                    print("Enter 1 to 4000 characters.")
                    if arguments.message is not None:
                        break
                    continue
                result = send_message(client, chat_id, message, str(uuid.uuid4()))
                turn = result["turns"][-1]
                print("GEO: " + (turn["answer"] or turn["error"] or "Turn is still running."))
                if turn["status"] == "failed":
                    print(f"Saved failure: conversation {chat_id}, revision {result['revision']}. No automatic retry; a new message uses the remaining budget.")
                if arguments.message is not None or turn["status"] == "running":
                    break
        except httpx.HTTPError as error:
            status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else "unavailable"
            parser.exit(1, f"Local chat request failed (HTTP {status}). Inspect the saved conversation before retrying.\n")


if __name__ == "__main__":
    main()