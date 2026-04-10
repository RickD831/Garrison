#!/usr/bin/env python3
"""
server.py — FastAPI OpenAI-compatible API wrapper for Gary

Exposes Gary's full agent (all 16 tools) behind an OpenAI-compatible
/v1/chat/completions endpoint so Open WebUI can talk to Gary exactly
like it would talk to any LLM — but every message goes through the
full monitoring agent with live tool calls.

Run:
    python server.py               # default port 8000
    PORT=8001 python server.py     # custom port

In Open WebUI:
    Settings → Connections → OpenAI API
    URL: http://host.docker.internal:8000/v1
    Key: garrison   (any non-empty string)
    Then select "gary" as the model.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Iterator

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Import Gary — this also loads config and registers all tools
from agent import build_agent, _cfg, _session, _invoke

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("garrison.server")

PORT = int(os.environ.get("PORT", "8000"))
MODEL_ID = "gary"

app = FastAPI(title="Garrison — Gary API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Build Gary once at startup — shared across all requests
gary = build_agent()
logger.info("Gary agent loaded (model: %s)", os.environ.get("OLLAMA_MODEL", "gemma4:e4b"))


# ── OpenAI-compatible schema ──────────────────────────────────────────────────

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: str = MODEL_ID
    messages: list[Message]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/v1/models")
def list_models():
    """Return Gary as the only available model."""
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_ID,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "garrison",
                "description": f"Gary — Garrison monitoring agent for {_cfg.agency_name}",
            }
        ],
    }


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest):
    """
    Handle a chat completion request from Open WebUI.
    Extracts the latest user message and runs it through Gary.
    The full message history is passed so Gary has conversation context.
    """
    if not req.messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    # Build the full message list for Gary (preserves conversation context)
    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    logger.info("Query: %s", messages[-1]["content"][:120])

    if req.stream:
        return StreamingResponse(
            _stream_response(messages),
            media_type="text/event-stream",
        )
    else:
        return _blocking_response(messages)


# ── Response helpers ──────────────────────────────────────────────────────────

def _run_gary(messages: list[dict]) -> str:
    """Run messages through the Gary agent and return the final text response."""
    try:
        result = gary.invoke({"messages": messages})
        msgs = result.get("messages", [])
        if msgs:
            last = msgs[-1]
            return last.content if hasattr(last, "content") else str(last)
        return "(no response)"
    except Exception as e:
        logger.error("Gary agent error: %s", e)
        return f"I encountered an error while processing your request: {e}"


def _blocking_response(messages: list[dict]) -> dict:
    content = _run_gary(messages)
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": sum(len(m["content"].split()) for m in messages),
            "completion_tokens": len(content.split()),
            "total_tokens": sum(len(m["content"].split()) for m in messages) + len(content.split()),
        },
    }


def _stream_response(messages: list[dict]) -> Iterator[str]:
    """
    Yield SSE chunks. Gary doesn't stream internally, so we fake it by
    sending the full response as a single chunk then closing the stream.
    This keeps Open WebUI happy while avoiding a full streaming rewrite.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    content = _run_gary(messages)

    # Send content as a single delta chunk
    chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(chunk)}\n\n"

    # Send stop chunk
    stop_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(stop_chunk)}\n\n"
    yield "data: [DONE]\n\n"


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "agent": "gary",
        "agency": _cfg.agency_name,
        "hosts": _cfg.host_names(),
        "model": os.environ.get("OLLAMA_MODEL", "gemma4:e4b"),
    }


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Garrison API server on port %d", PORT)
    logger.info("Open WebUI connection: http://host.docker.internal:%d/v1", PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
