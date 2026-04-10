#!/bin/bash
# garrison.sh — start, stop, status, logs for all Garrison components
# Usage: ./garrison.sh [start|stop|restart|status|logs|gary]
#
# Supports both macOS (dev) and Linux (production).
# On Linux, garrison-agent runs as a systemd service.
# On macOS, garrison-agent runs as a backgrounded process with a PID file.
# Ollama is never managed here on macOS — use Ollama.app or start it manually.

set -e

GARRISON_ROOT="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$GARRISON_ROOT/docker-compose.yml"
PID_FILE="$GARRISON_ROOT/.garrison-agent.pid"
SERVER_PID_FILE="$GARRISON_ROOT/.garrison-server.pid"
LOG_DIR="$GARRISON_ROOT/logs"
AGENT_LOG="$LOG_DIR/indexer.log"
SERVER_LOG="$LOG_DIR/server.log"

OS_TYPE="$(uname -s)"   # Linux or Darwin

# ── Helpers ───────────────────────────────────────────────────────────────────

_ollama_running() {
    curl -sf http://localhost:11434/api/tags > /dev/null 2>&1
}

_agent_pid() {
    [ -f "$PID_FILE" ] && cat "$PID_FILE" || echo ""
}

_agent_running() {
    local pid
    pid="$(_agent_pid)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# ── Platform-specific service management ─────────────────────────────────────

_start_ollama() {
    if [ "$OS_TYPE" = "Linux" ]; then
        sudo systemctl start ollama
    else
        # macOS: Ollama.app manages itself. Just verify it's up.
        if ! _ollama_running; then
            echo "[garrison] Ollama is not running. Start Ollama.app or run 'ollama serve' first."
            exit 1
        fi
    fi
}

_stop_ollama() {
    if [ "$OS_TYPE" = "Linux" ]; then
        sudo systemctl stop ollama
    else
        echo "[garrison] Ollama not managed on macOS — stop it manually if needed."
    fi
}

_ollama_status() {
    if [ "$OS_TYPE" = "Linux" ]; then
        systemctl is-active ollama 2>/dev/null && echo "ollama: running" || echo "ollama: stopped"
    else
        _ollama_running && echo "ollama: running (http://localhost:11434)" || echo "ollama: stopped"
    fi
}

_start_agent() {
    if [ "$OS_TYPE" = "Linux" ]; then
        sudo systemctl start garrison-agent
    else
        if _agent_running; then
            echo "[garrison] garrison-agent already running (pid $(_agent_pid))"
            return
        fi
        mkdir -p "$LOG_DIR"
        cd "$GARRISON_ROOT"
        source venv/bin/activate
        nohup python collector/indexer.py >> "$AGENT_LOG" 2>&1 &
        echo $! > "$PID_FILE"
        echo "[garrison] garrison-agent started (pid $(cat $PID_FILE)) — logs: $AGENT_LOG"
    fi
}

_stop_agent() {
    if [ "$OS_TYPE" = "Linux" ]; then
        sudo systemctl stop garrison-agent
    else
        local pid
        pid="$(_agent_pid)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            rm -f "$PID_FILE"
            echo "[garrison] garrison-agent stopped."
        else
            echo "[garrison] garrison-agent is not running."
            rm -f "$PID_FILE"
        fi
    fi
}

_agent_status() {
    if [ "$OS_TYPE" = "Linux" ]; then
        systemctl is-active garrison-agent 2>/dev/null && echo "garrison-agent: running" || echo "garrison-agent: stopped"
    else
        if _agent_running; then
            echo "garrison-agent: running (pid $(_agent_pid))"
        else
            echo "garrison-agent: stopped"
        fi
    fi
}

_logs_agent() {
    if [ "$OS_TYPE" = "Linux" ]; then
        journalctl -u garrison-agent -f
    else
        if [ -f "$AGENT_LOG" ]; then
            tail -f "$AGENT_LOG"
        else
            echo "No log file yet: $AGENT_LOG"
            echo "Start the agent first with: ./garrison.sh start"
        fi
    fi
}

# ── Gary API server (Open WebUI backend) ─────────────────────────────────────

_server_pid() {
    [ -f "$SERVER_PID_FILE" ] && cat "$SERVER_PID_FILE" || echo ""
}

_server_running() {
    local pid
    pid="$(_server_pid)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

_start_server() {
    if _server_running; then
        echo "[garrison] gary-server already running (pid $(_server_pid))"
        return
    fi
    mkdir -p "$LOG_DIR"
    cd "$GARRISON_ROOT"
    source venv/bin/activate
    nohup python server.py >> "$SERVER_LOG" 2>&1 &
    echo $! > "$SERVER_PID_FILE"
    echo "[garrison] gary-server started (pid $(cat $SERVER_PID_FILE)) — logs: $SERVER_LOG"
    echo "[garrison] Open WebUI connection: http://host.docker.internal:8000/v1"
}

_stop_server() {
    local pid
    pid="$(_server_pid)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        rm -f "$SERVER_PID_FILE"
        echo "[garrison] gary-server stopped."
    else
        echo "[garrison] gary-server is not running."
        rm -f "$SERVER_PID_FILE"
    fi
}

_server_status() {
    if _server_running; then
        echo "gary-server:    running (pid $(_server_pid)) — http://localhost:8000"
    else
        echo "gary-server:    stopped"
    fi
}

_logs_server() {
    if [ -f "$SERVER_LOG" ]; then
        tail -f "$SERVER_LOG"
    else
        echo "No server log yet. Start with: ./garrison.sh server start"
    fi
}

_logs_ollama() {
    if [ "$OS_TYPE" = "Linux" ]; then
        journalctl -u ollama -f
    else
        echo "Ollama logs are in the macOS Console app (filter by 'ollama')."
        echo "Or run 'ollama serve' in a terminal to see output directly."
    fi
}

# ── Commands ──────────────────────────────────────────────────────────────────

cmd="${1:-status}"

case "$cmd" in
  start)
    echo "[garrison] Starting containers (Qdrant + Open WebUI)..."
    docker-compose -f "$COMPOSE_FILE" up -d
    echo "[garrison] Starting Ollama..."
    _start_ollama
    echo "[garrison] Starting garrison-agent (log collector)..."
    _start_agent
    echo "[garrison] Starting gary-server (Open WebUI backend)..."
    _start_server
    echo "[garrison] All services started."
    ;;

  stop)
    echo "[garrison] Stopping gary-server..."
    _stop_server
    echo "[garrison] Stopping garrison-agent..."
    _stop_agent
    echo "[garrison] Stopping containers..."
    docker-compose -f "$COMPOSE_FILE" down
    _stop_ollama
    echo "[garrison] Stopped."
    ;;

  restart)
    "$0" stop
    sleep 2
    "$0" start
    ;;

  status)
    echo "=== Garrison Status ==="
    echo ""
    echo "-- Docker containers --"
    docker-compose -f "$COMPOSE_FILE" ps
    echo ""
    echo "-- Ollama --"
    _ollama_status
    echo ""
    echo "-- Gary (collector) --"
    _agent_status
    echo ""
    echo "-- Gary (API server) --"
    _server_status
    echo ""
    echo "-- Open WebUI --"
    echo "  http://localhost:3000"
    echo ""
    echo "-- Qdrant --"
    echo "  http://localhost:6333/dashboard"
    ;;

  server)
    # Manage the Gary API server independently
    subcmd="${2:-status}"
    case "$subcmd" in
      start)   _start_server ;;
      stop)    _stop_server ;;
      restart) _stop_server; sleep 1; _start_server ;;
      status)  _server_status ;;
      logs)    _logs_server ;;
      *)
        echo "Usage: $0 server [start|stop|restart|status|logs]"
        ;;
    esac
    ;;

  logs)
    service="${2:-garrison-agent}"
    case "$service" in
      qdrant)
        docker-compose -f "$COMPOSE_FILE" logs -f qdrant
        ;;
      webui)
        docker-compose -f "$COMPOSE_FILE" logs -f open-webui
        ;;
      gary|agent|garrison-agent)
        _logs_agent
        ;;
      server|gary-server)
        _logs_server
        ;;
      ollama)
        _logs_ollama
        ;;
      *)
        echo "Usage: $0 logs [qdrant|webui|gary|server|ollama]"
        ;;
    esac
    ;;

  gary)
    # Launch Gary interactively in the terminal
    cd "$GARRISON_ROOT"
    source venv/bin/activate
    python agent.py "${@:2}"
    ;;

  *)
    echo "Usage: $0 [start|stop|restart|status|logs|server|gary]"
    echo ""
    echo "  start              Start all Garrison services"
    echo "  stop               Stop all Garrison services"
    echo "  restart            Restart all services"
    echo "  status             Show status of all components"
    echo "  logs [svc]         Tail logs (qdrant, webui, gary, server, ollama)"
    echo "  server [cmd]       Manage Gary API server (start|stop|restart|status|logs)"
    echo "  gary [query]       Talk to Gary directly in the terminal"
    echo ""
    echo "Running on: $OS_TYPE"
    ;;
esac
