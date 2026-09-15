import json
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import Field

from geo_agent.contracts import Contract, digest
from geo_agent.conversation import ChatRequest
from geo_agent.workflow import Conflict


class InputText(Contract):
    type: Literal["input_text"]
    text: str = Field(min_length=1, max_length=4000)


class InputMessage(Contract):
    type: Literal["message"] = "message"
    role: Literal["user"]
    content: str | list[InputText]


class ResponseRequest(Contract):
    input: str | list[InputMessage]
    model: str = "geo-evidence-agent"
    stream: bool = False
    previous_response_id: str | None = None
    store: bool = True

    def message(self) -> str:
        if isinstance(self.input, str):
            return self.input
        if len(self.input) != 1:
            raise HTTPException(422, "Send one new user message; use previous_response_id for history")
        content = self.input[0].content
        return content if isinstance(content, str) else "\n".join(part.text for part in content)


def conversation_router(service_for, authenticate) -> APIRouter:
    router = APIRouter()

    @router.head("/responses")
    @router.head("/v1/responses")
    def discover_responses() -> Response:
        return Response(status_code=200, headers={"Allow": "HEAD, POST"})

    @router.post("/responses")
    @router.post("/v1/responses")
    async def responses(body: ResponseRequest, owner: str = Depends(authenticate),
                        idempotency_key: str | None = Header(default=None)):
        service = service_for(owner)
        text = body.message()
        if not text.strip() or len(text) > 4000:
            raise HTTPException(422, "Message must contain 1 to 4000 characters")
        if not body.store:
            raise HTTPException(422, "This local development agent requires approved local conversation retention")
        if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
            raise HTTPException(422, "Invalid idempotency key")
        signature = digest(body.model_dump(mode="json", exclude={"stream"}))
        key = idempotency_key or signature
        chats = service.chats
        with chats.store.connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS chat_response_requests (owner TEXT, policy_id TEXT, request_key TEXT, signature TEXT, chat_id TEXT, revision INTEGER, PRIMARY KEY(owner, policy_id, request_key))")
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT signature, chat_id, revision FROM chat_response_requests WHERE owner = ? AND policy_id = ? AND request_key = ?", (owner, chats.policy.policy_id, key)).fetchone()
            if row:
                if row[0] != signature:
                    raise Conflict("Response idempotency key is bound to different input")
                chat_id, revision = row[1:]
            else:
                if body.previous_response_id:
                    try:
                        prefix, chat_id, revision_text = body.previous_response_id.split("_")
                        revision = int(revision_text)
                        if prefix != "resp" or revision < 1:
                            raise ValueError
                    except ValueError:
                        raise HTTPException(422, "Invalid local previous_response_id") from None
                    chat = chats._read(connection, chat_id, owner)
                    if chat["revision"] != revision:
                        raise Conflict("Continue from the latest conversation response")
                else:
                    chats.store._read(connection, chats.policy.run_id, owner)
                    chat_id = str(uuid.uuid4())
                    revision = 0
                    chat = {"chat_id": chat_id, "run_id": chats.policy.run_id, "revision": 0, "turns": []}
                    connection.execute("INSERT INTO chats VALUES (?, ?, ?, ?)", (chat_id, owner, chats.policy.policy_id, json.dumps(chat)))
                connection.execute("INSERT INTO chat_response_requests VALUES (?, ?, ?, ?, ?, ?)", (owner, chats.policy.policy_id, key, signature, chat_id, revision))
        result = await service.respond(chat_id, owner, ChatRequest(message=text, idempotency_key=key, expected_revision=revision))
        turn = next(turn for turn in result["turns"] if turn["idempotency_key"] == key)
        if turn["status"] == "running":
            raise HTTPException(409, "Turn still running; inspect the conversation before retrying")
        if turn["status"] == "failed":
            raise HTTPException(502, turn["error"])
        response_id = f"resp_{chat_id}_{revision + 1}"
        item = {"id": f"msg_{chat_id}_{revision + 1}", "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": turn["answer"], "annotations": []}]}
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for call in turn["calls"]:
            for name in usage:
                usage[name] += (call.get("usage") or {}).get(name, 0)
        response = {"id": response_id, "object": "response", "created_at": int(time.time()), "status": "completed",
                    "model": "geo-evidence-agent", "output": [item], "usage": usage, "error": None,
                    "metadata": {"chat_id": chat_id, "run_id": result["run_id"]}, "previous_response_id": body.previous_response_id}
        if not body.stream:
            return response

        async def events():
            pending = {**response, "status": "in_progress", "output": []}
            sequence = [
                {"type": "response.created", "response": pending},
                {"type": "response.in_progress", "response": pending},
                {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "content": []}},
                {"type": "response.content_part.added", "item_id": item["id"], "output_index": 0, "content_index": 0,
                 "part": {"type": "output_text", "text": "", "annotations": []}},
                {"type": "response.output_text.delta", "item_id": item["id"], "output_index": 0, "content_index": 0, "delta": turn["answer"]},
                {"type": "response.output_text.done", "item_id": item["id"], "output_index": 0, "content_index": 0, "text": turn["answer"]},
                {"type": "response.content_part.done", "item_id": item["id"], "output_index": 0, "content_index": 0, "part": item["content"][0]},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": response},
            ]
            for index, event in enumerate(sequence):
                yield f"event: {event['type']}\ndata: {json.dumps({**event, 'sequence_number': index})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    return router