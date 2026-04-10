#!/usr/bin/env python3
"""
agent.py — Garrison main entrypoint

Launches Gary, the LangChain ReAct agent that answers natural language
questions about monitored hosts.

Usage:
  python agent.py                        # interactive CLI
  python agent.py "check fileserver01"   # single query
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from langchain.agents import create_agent as create_react_agent
from langchain_ollama import ChatOllama
from langchain_core.tools import tool as lc_tool, StructuredTool

from core.config import load_config
from core.discovery import discover_host
from tools.log_tools import (
    get_recent_logs,
    get_log_errors_summary,
    search_logs,
    get_event_log_sources,
)
from tools.auth_tools import get_recent_logins, get_sudo_activity, get_logged_in_users
from tools.process_tools import (
    get_running_services,
    get_failed_services,
    get_top_processes,
    get_open_ports,
    get_installed_software,
)
from tools.health_tools import (
    get_host_health,
    check_host_reachable,
    get_disk_health,
    get_windows_updates,
)
from tools.security_tools import (
    get_firewall_rules,
    get_scheduled_tasks,
    get_startup_items,
    get_local_admins,
    get_rdp_sessions,
    get_suid_binaries,
    get_last_modified_configs,
)
from tools.network_tools import (
    get_active_connections,
    get_dns_config,
    get_network_interfaces,
    get_listening_sockets_by_process,
)
from tools.summary_tools import get_host_summary, compare_hosts, get_patch_delta
from tools.rag_tool import search_log_history

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("garrison.agent")

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


# ── Config ────────────────────────────────────────────────────────────────────

try:
    _cfg = load_config()
except FileNotFoundError as e:
    print(f"Error: {e}")
    sys.exit(1)


# ── Session state ─────────────────────────────────────────────────────────────

# Tracks the active host for the current conversation so Gary doesn't
# ask "which host?" repeatedly after one has been established.
_session: dict = {"active_host": None}


# ── Tool factory ──────────────────────────────────────────────────────────────

def _resolve_host(host_identifier: str):
    """
    Given a name or IP from the LLM, return a HostConnection.
    Falls back to the session active host if identifier is empty/generic.
    Remembers the resolved host for the rest of the conversation.
    """
    host_identifier = host_identifier.strip()

    # If the model passes nothing useful, fall back to active host
    if not host_identifier or host_identifier.lower() in ("", "none", "unknown", "the host", "this host"):
        if _session["active_host"]:
            return _session["active_host"]
        raise ValueError("No host specified. Please tell me which host you want to check.")

    # Try by name
    try:
        conn = _cfg.get_host(host_identifier)
        _session["active_host"] = conn
        return conn
    except KeyError:
        pass

    # Try by address
    try:
        conn = _cfg.get_host_by_address(host_identifier)
        _session["active_host"] = conn
        return conn
    except KeyError:
        pass

    # Attempt discovery
    conn, message = discover_host(host_identifier, _cfg)
    if conn is None:
        raise ValueError(message)
    print(f"[discovery] {message}")
    _session["active_host"] = conn
    return conn


def _make_tool(name: str, description: str, fn):
    """
    Wrap a tool function so the LLM passes a host identifier as the input.
    The tool resolves the host and calls fn(conn).
    """
    def _run(host: str) -> str:
        try:
            conn = _resolve_host(host)
            return fn(conn)
        except Exception as e:
            return f"Error: {e}"

    return StructuredTool.from_function(func=_run, name=name, description=description)


def _make_tool_with_hours(name: str, description: str, fn, default_hours: int = 24):
    """For tools that accept host + optional hours parameter (parsed from input)."""
    def _run(inp: str) -> str:
        parts = inp.split(",", 1)
        host = parts[0].strip()
        hours = default_hours
        if len(parts) > 1:
            try:
                hours = int(parts[1].strip())
            except ValueError:
                pass
        try:
            conn = _resolve_host(host)
            return fn(conn, hours=hours)
        except Exception as e:
            return f"Error: {e}"
    return StructuredTool.from_function(func=_run, name=name, description=description)


# ── Tool registry ─────────────────────────────────────────────────────────────

def _compare_hosts_tool(inp: str) -> str:
    parts = inp.split(",", 1)
    if len(parts) < 2:
        return "Input format: 'host_a, host_b'"
    try:
        conn_a = _resolve_host(parts[0].strip())
        conn_b = _resolve_host(parts[1].strip())
        return compare_hosts(conn_a, conn_b)
    except Exception as e:
        return f"Error: {e}"


def _patch_delta_tool(inp: str) -> str:
    parts = inp.split(",", 1)
    if len(parts) < 2:
        return "Input format: 'host_a, host_b'"
    try:
        conn_a = _resolve_host(parts[0].strip())
        conn_b = _resolve_host(parts[1].strip())
        return get_patch_delta(conn_a, conn_b)
    except Exception as e:
        return f"Error: {e}"


def _last_modified_configs_tool(inp: str) -> str:
    parts = inp.split(",", 1)
    host = parts[0].strip()
    days = 7
    if len(parts) > 1:
        try:
            days = int(parts[1].strip())
        except ValueError:
            pass
    try:
        conn = _resolve_host(host)
        return get_last_modified_configs(conn, days=days)
    except Exception as e:
        return f"Error: {e}"


def build_tools() -> list:
    return [
        _make_tool_with_hours(
            "get_recent_logs",
            "Get recent warning/error log entries from a host. "
            "Input: 'hostname' or 'hostname, hours'. Returns last N hours of warnings/errors.",
            get_recent_logs,
        ),
        _make_tool_with_hours(
            "get_log_errors_summary",
            "Get a grouped summary of log errors by service/source for a host. "
            "Input: 'hostname' or 'hostname, hours'.",
            get_log_errors_summary,
        ),
        StructuredTool.from_function(
            func=lambda inp: _search_logs_tool(inp),
            name="search_logs",
            description=(
                "Full-text search across recent logs on a host. "
                "Input: 'hostname, search_term'. Example: 'appserver01, authentication failure'."
            ),
        ),
        _make_tool(
            "get_recent_logins",
            "Get recent login successes and failures for a host. Input: hostname.",
            get_recent_logins,
        ),
        _make_tool(
            "get_sudo_activity",
            "Get recent sudo/privilege escalation activity on a host. Input: hostname.",
            get_sudo_activity,
        ),
        _make_tool(
            "get_logged_in_users",
            "Get currently logged-in users on a host. Input: hostname.",
            get_logged_in_users,
        ),
        _make_tool(
            "get_running_services",
            "List all running/active services on a host. Input: hostname.",
            get_running_services,
        ),
        _make_tool(
            "get_failed_services",
            "List failed or unexpectedly stopped services on a host. Input: hostname.",
            get_failed_services,
        ),
        _make_tool(
            "get_top_processes",
            "List top processes by CPU usage on a host. Input: hostname.",
            get_top_processes,
        ),
        _make_tool(
            "get_open_ports",
            "List listening TCP/UDP ports with process names on a host. Input: hostname.",
            get_open_ports,
        ),
        _make_tool(
            "get_installed_software",
            "List installed software/packages on a host. Input: hostname.",
            get_installed_software,
        ),
        _make_tool(
            "get_host_health",
            "Get memory, CPU load, uptime, and disk summary for a host. Input: hostname.",
            get_host_health,
        ),
        _make_tool(
            "get_disk_health",
            "Get detailed disk usage for a host, highlighting full filesystems. Input: hostname.",
            get_disk_health,
        ),
        _make_tool(
            "check_host_reachable",
            "Check if a host is pingable and its service port is open. Input: hostname or IP address.",
            check_host_reachable,
        ),
        StructuredTool.from_function(
            func=lambda inp: _rag_tool(inp),
            name="search_log_history",
            description=(
                "Semantic search over historical log data in Qdrant. Use this for questions about "
                "past events, trends, or patterns — e.g. 'Has this error occurred before?', "
                "'What was happening on fileserver01 last week?'. "
                "Input: natural language query, optionally prefixed with 'hostname: query'."
            ),
        ),
        # ── Logs ──
        _make_tool(
            "get_event_log_sources",
            "List available log sources/providers on a host to see what's being logged. Input: hostname.",
            get_event_log_sources,
        ),
        # ── Health / Updates ──
        _make_tool(
            "get_windows_updates",
            "Get pending and recently installed Windows updates / Linux package updates. Input: hostname.",
            get_windows_updates,
        ),
        # ── Security ──
        _make_tool(
            "get_firewall_rules",
            "Get active firewall rules (ufw/iptables on Linux, Get-NetFirewallRule on Windows). Input: hostname.",
            get_firewall_rules,
        ),
        _make_tool(
            "get_scheduled_tasks",
            "Get scheduled tasks/cron jobs — key persistence mechanism for attackers. Input: hostname.",
            get_scheduled_tasks,
        ),
        _make_tool(
            "get_startup_items",
            "Get programs configured to run at startup/login. Classic persistence vector. Input: hostname.",
            get_startup_items,
        ),
        _make_tool(
            "get_local_admins",
            "Get members of local Administrators / sudo / wheel groups. Input: hostname.",
            get_local_admins,
        ),
        _make_tool(
            "get_rdp_sessions",
            "Get active and recent RDP sessions (Windows only). Input: hostname.",
            get_rdp_sessions,
        ),
        _make_tool(
            "get_suid_binaries",
            "Find SUID/SGID binaries on Linux — classic privilege escalation vector. Input: hostname.",
            get_suid_binaries,
        ),
        StructuredTool.from_function(
            func=_last_modified_configs_tool,
            name="get_last_modified_configs",
            description=(
                "Get recently modified config files for detecting unauthorized changes. "
                "Input: 'hostname' or 'hostname, days'."
            ),
        ),
        # ── Network ──
        _make_tool(
            "get_active_connections",
            "Get established TCP connections with remote IPs and process names. "
            "Useful for detecting beaconing or lateral movement. Input: hostname.",
            get_active_connections,
        ),
        _make_tool(
            "get_dns_config",
            "Get DNS server configuration and hosts file. Flag unexpected DNS servers. Input: hostname.",
            get_dns_config,
        ),
        _make_tool(
            "get_network_interfaces",
            "Get all network interfaces with IPs, MAC addresses, and routes. Input: hostname.",
            get_network_interfaces,
        ),
        _make_tool(
            "get_listening_sockets_by_process",
            "Get listening sockets mapped to full binary paths. Deeper than get_open_ports — "
            "useful for detecting suspicious listeners. Input: hostname.",
            get_listening_sockets_by_process,
        ),
        # ── Summary / Comparison ──
        _make_tool(
            "get_host_summary",
            "Full situation report for a host — health, errors, logins, ports, services, "
            "and connections in one briefing. Use this when asked 'how's X doing?'. Input: hostname.",
            get_host_summary,
        ),
        StructuredTool.from_function(
            func=_compare_hosts_tool,
            name="compare_hosts",
            description=(
                "Side-by-side comparison of two hosts — diffs services, ports, software, health. "
                "Input: 'host_a, host_b'."
            ),
        ),
        StructuredTool.from_function(
            func=_patch_delta_tool,
            name="get_patch_delta",
            description=(
                "Compare installed software between two hosts to find patch/version gaps. "
                "Input: 'host_a, host_b'."
            ),
        ),
        # ── Inventory ──
        StructuredTool.from_function(
            func=lambda _="": "\n".join(
                f"{c.name:<20} {c.os:<8} {c.address}" for c in _cfg.all_hosts()
            ) or "No hosts configured.",
            name="list_hosts",
            description="List all configured hosts in agency.yaml. No input needed.",
        ),
    ]


def _search_logs_tool(inp: str) -> str:
    parts = inp.split(",", 1)
    if len(parts) < 2:
        return "Input format: 'hostname, search_term'"
    host, query = parts[0].strip(), parts[1].strip()
    try:
        conn = _resolve_host(host)
        return search_logs(conn, query)
    except Exception as e:
        return f"Error: {e}"


def _rag_tool(inp: str) -> str:
    host_filter = None
    query = inp.strip()
    if ":" in inp:
        parts = inp.split(":", 1)
        candidate = parts[0].strip()
        # Only treat as host prefix if it looks like a hostname (no spaces)
        if " " not in candidate:
            host_filter = candidate
            query = parts[1].strip()
    try:
        return search_log_history(query, host_filter=host_filter)
    except Exception as e:
        return f"Error: {e}"


# ── Prompt ────────────────────────────────────────────────────────────────────

def _system_prompt() -> str:
    host_list = ", ".join(_cfg.host_names()) or "none yet"
    return (
        f"You are Gary, the IT monitoring agent for {_cfg.agency_name}.\n"
        f"Known hosts: {host_list}\n"
        "You have tools to query Windows and Linux hosts for logs, health, services, ports, and user activity.\n"
        "All data is on-premises. Never make external network calls.\n\n"
        "CONTEXT RULES:\n"
        "- Once a host is mentioned (by name or IP), remember it for the rest of the conversation.\n"
        "- Never ask 'which host?' if a host was already established earlier in the conversation — use it.\n"
        "- If the user gives you an IP or hostname in response to one of your questions, use it immediately "
        "to answer the original question. Do NOT run check_host_reachable — run the tool the user originally asked for.\n"
        "- If only one host exists in inventory, always default to it without asking.\n\n"
        "TOOL USAGE RULES:\n"
        "- For login/session questions (who logged in, last login, login history), use get_recent_logins.\n"
        "- For questions about a specific username, pass the hostname to get_recent_logins and filter "
        "the results yourself — look for that username in the output.\n"
        "- Only use check_host_reachable when explicitly asked if a host is up/reachable.\n"
        "- For health questions, use get_host_health. For services, use get_running_services.\n\n"
        "RESPONSE RULES:\n"
        "- Always respond in plain conversational English. Never output raw JSON or data structures.\n"
        "- Summarize tool output in human-readable sentences and bullet points.\n"
        "- Include specific facts from tool results (names, numbers, timestamps).\n"
        "- Flag security concerns clearly with WARNING.\n"
        "- Be concise — bullets for lists, not walls of text."
    )


# ── Agent builder ─────────────────────────────────────────────────────────────

def build_agent():
    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0,
    )
    tools = build_tools()
    # LangGraph 1.x: create_react_agent returns a CompiledGraph
    gary = create_react_agent(llm, tools, system_prompt=_system_prompt())
    return gary


# ── CLI ───────────────────────────────────────────────────────────────────────

def _invoke(gary, query: str) -> str:
    """Run a query through Gary and return the final text response."""
    result = gary.invoke({"messages": [{"role": "user", "content": query}]})
    messages = result.get("messages", [])
    # Last message is the AI's final answer
    if messages:
        last = messages[-1]
        return last.content if hasattr(last, "content") else str(last)
    return "(no response)"


def main() -> None:
    print(f"Garrison — {_cfg.agency_name}")
    print(f"Gary is online (model: {OLLAMA_MODEL})")
    print(f"Hosts: {', '.join(_cfg.host_names()) or 'none configured'}")
    print("Type 'quit' or Ctrl+C to exit.\n")

    gary = build_agent()

    # Single query mode
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        print("\n" + _invoke(gary, query))
        return

    # Interactive mode
    while True:
        try:
            query = input("Gary> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break
        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            print("Goodbye.")
            break
        try:
            print("\n" + _invoke(gary, query) + "\n")
        except KeyboardInterrupt:
            print("\n(interrupted)")
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
