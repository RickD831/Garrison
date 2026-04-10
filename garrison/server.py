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


def _sse_content(completion_id: str, text: str) -> str:
    """Build an OpenAI-format SSE content delta chunk."""
    chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [
            {"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}
        ],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def _sse_reasoning(completion_id: str, text: str) -> str:
    """
    Emit a reasoning_content delta. Open WebUI renders reasoning_content
    in a collapsible 'Thinking' section separate from the main message.
    Falls back gracefully on clients that don't understand it.
    """
    chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "reasoning_content": text},
                "finish_reason": None,
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def _sse_stop(completion_id: str) -> str:
    chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    return f"data: {json.dumps(chunk)}\n\n"


# Pretty labels for each tool — shown in the "Gary is..." status pill
_TOOL_LABELS = {
    "get_recent_logs":                "Pulling recent logs",
    "get_log_errors_summary":         "Summarizing log errors",
    "search_logs":                    "Searching logs",
    "get_event_log_sources":          "Listing log sources",
    "get_recent_logins":              "Checking login history",
    "get_sudo_activity":              "Reviewing sudo activity",
    "get_logged_in_users":            "Checking active sessions",
    "get_running_services":           "Listing running services",
    "get_failed_services":            "Checking failed services",
    "get_top_processes":              "Inspecting top processes",
    "get_open_ports":                 "Scanning open ports",
    "get_installed_software":         "Enumerating installed software",
    "get_host_health":                "Running health check",
    "get_disk_health":                "Checking disk health",
    "check_host_reachable":           "Pinging host",
    "get_windows_updates":            "Checking for updates",
    "get_firewall_rules":             "Reading firewall rules",
    "get_scheduled_tasks":            "Inspecting scheduled tasks",
    "get_startup_items":              "Listing startup items",
    "get_local_admins":               "Checking local admins",
    "get_rdp_sessions":               "Reviewing RDP sessions",
    "get_suid_binaries":              "Scanning SUID binaries",
    "get_last_modified_configs":      "Looking for recent config changes",
    "get_active_connections":         "Mapping active connections",
    "get_dns_config":                 "Reading DNS config",
    "get_network_interfaces":         "Listing network interfaces",
    "get_listening_sockets_by_process":"Mapping listeners to binaries",
    "get_host_summary":               "Building host summary",
    "compare_hosts":                  "Comparing hosts",
    "get_patch_delta":                "Computing patch delta",
    "search_log_history":             "Searching log history (RAG)",
    "list_hosts":                     "Listing hosts",
}


def _friendly_tool_label(tool_name: str) -> str:
    return _TOOL_LABELS.get(tool_name, f"Running {tool_name}")


def _stream_response(messages: list[dict]) -> Iterator[str]:
    """
    Stream Gary's intermediate tool calls as live content chunks, then
    stream the final answer. Uses LangGraph's stream() with
    stream_mode='updates' to intercept each node's output as it happens.

    Strategy for Open WebUI compatibility:
      1. Emit tool activity as BOTH reasoning_content (for clients that
         support collapsible thinking blocks) AND visible italic content
         (as a universal fallback that every OpenAI client renders).
      2. Emit a horizontal rule separator before the final answer.
      3. Emit the final answer as regular content.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"

    final_text = ""
    seen_tool_call_ids: set[str] = set()
    emitted_header = False

    def _status_line(text: str) -> Iterator[str]:
        """Emit one status update as both reasoning_content and visible italic content."""
        nonlocal emitted_header
        yield _sse_reasoning(completion_id, text + "\n")
        # Lazy header for the visible fallback
        if not emitted_header:
            yield _sse_content(completion_id, "> _Gary is working..._\n")
            emitted_header = True
        yield _sse_content(completion_id, f"> _• {text}_\n")

    try:
        for status in _status_line("Thinking..."):
            yield status

        for event in gary.stream(
            {"messages": messages},
            stream_mode="updates",
        ):
            # event is {node_name: node_output}
            for node_name, node_output in event.items():
                if not isinstance(node_output, dict):
                    continue
                node_messages = node_output.get("messages", []) or []

                if node_name in ("model", "agent"):
                    # LLM just produced output — could be tool calls or final answer
                    for msg in node_messages:
                        tool_calls = getattr(msg, "tool_calls", None) or []
                        for tc in tool_calls:
                            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                            tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                            if tc_id and tc_id not in seen_tool_call_ids and tc_name:
                                seen_tool_call_ids.add(tc_id)
                                for chunk in _status_line(_friendly_tool_label(tc_name)):
                                    yield chunk
                        # If this message has text content and no tool calls, it's the final answer
                        content = getattr(msg, "content", "") or ""
                        if content and not tool_calls:
                            final_text = content

                elif node_name == "tools":
                    # Tool just returned
                    for msg in node_messages:
                        tc_name = getattr(msg, "name", None)
                        if tc_name:
                            for chunk in _status_line(
                                f"{_friendly_tool_label(tc_name)} ✓"
                            ):
                                yield chunk
    except Exception as e:
        logger.error("Stream error: %s", e)
        final_text = f"I encountered an error while processing your request: {e}"

    if not final_text:
        final_text = "(no response)"

    # Separator + final answer
    if emitted_header:
        yield _sse_content(completion_id, "\n---\n\n")
    yield _sse_content(completion_id, final_text)
    yield _sse_stop(completion_id)
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
