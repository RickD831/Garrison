<p align="center">
  <img src="garrison/docs/garrison-logo.png" alt="Garrison" width="360">
</p>

<h1 align="center">Garrison</h1>

<p align="center">
  <em>A local, on-prem LLM agent for monitoring Windows Server and Ubuntu Linux hosts.</em><br>
  <em>Built for Monterey Superior Court. Designed to be shareable to other agencies.</em>
</p>

<p align="center">
  <strong>All data stays on-prem. No cloud calls. No exceptions.</strong>
</p>

---

## What it is

Garrison is the platform. **Gary** is the LLM agent inside it — the conversational
layer operators actually talk to. Ask Gary a question in plain English about any
of your monitored hosts and he'll pick the right tool, run it over WinRM or SSH,
and summarize the result.

- **"Is fileserver01 healthy?"** → runs `get_host_health`, reports memory/CPU/disk
- **"Who logged into appserver02 today?"** → runs `get_recent_logins`, filters events
- **"What changed on dc01 in the last week?"** → runs `get_last_modified_configs`
- **"Compare fileserver01 and fileserver02"** → runs `compare_hosts`, diffs services/ports/software
- **"Has this error happened before?"** → semantic search over Qdrant log history

While Gary works, the chat UI shows a live activity trail so you can see
exactly which tools he's running:

```
> _Gary is working..._
> _• Running health check ✓_
> _• Checking failed services ✓_
> _• Scanning open ports ✓_

---

Here's the report for fileserver01...
```

## Stack

| Component | Choice |
|---|---|
| LLM runtime | Ollama — `gemma4:e4b` (fast), `qwen2.5:14b` (best tool use) |
| Agent framework | LangGraph 1.x via `langchain.agents.create_agent` |
| Transport — Windows | WinRM via `pypsrp` — port 5985 (HTTP/POC) or 5986 (HTTPS/prod) |
| Transport — Linux | SSH via `paramiko`, port 22 |
| Vector store | Qdrant (local Docker) |
| Embeddings | `nomic-embed-text` via Ollama |
| Chat interface | Open WebUI (port 3000) or CLI (`python agent.py`) |
| Container runtime | Colima on macOS, Docker on Linux |

## 32 Tools Across 7 Categories

Every tool works on both Windows and Linux — same name, two implementations inside.
OS is detected at call time, never branched at the agent level.

- **Logs** — `get_recent_logs`, `get_log_errors_summary`, `search_logs`, `get_event_log_sources`
- **Auth** — `get_recent_logins`, `get_sudo_activity`, `get_logged_in_users`
- **Processes & Services** — `get_running_services`, `get_failed_services`, `get_top_processes`, `get_open_ports`, `get_installed_software`
- **Health** — `get_host_health`, `get_disk_health`, `check_host_reachable`, `get_windows_updates`
- **Security** — `get_firewall_rules`, `get_scheduled_tasks`, `get_startup_items`, `get_local_admins`, `get_rdp_sessions`, `get_suid_binaries`, `get_last_modified_configs`
- **Network** — `get_active_connections`, `get_dns_config`, `get_network_interfaces`, `get_listening_sockets_by_process`
- **Summary / Comparison** — `get_host_summary`, `compare_hosts`, `get_patch_delta`
- **RAG / Inventory** — `search_log_history`, `list_hosts`

See [`CLAUDE.md`](CLAUDE.md) for the full platform reference — tool inventory,
architecture, library notes, and setup instructions for both macOS (dev) and
Linux (production).

## Quick start — macOS (dev)

```bash
brew install python@3.11 colima docker docker-compose
colima start
ollama pull gemma4:e4b && ollama pull nomic-embed-text

cd garrison/
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp agency.yaml.example agency.yaml   # edit with your hosts
cp .env.example .env                  # add credentials

docker-compose up -d                  # Qdrant + Open WebUI
python manage.py validate-config      # verify setup

python agent.py                       # interactive CLI
# or
./garrison.sh start                   # start collector + API server
```

## Quick start — Linux (production)

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo cp ollama.service garrison-agent.service /etc/systemd/system/

sudo mkdir -p /opt/garrison && sudo chown $USER /opt/garrison
cd /opt/garrison && git clone https://github.com/RickD831/Garrison .
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp agency.yaml.example agency.yaml && cp .env.example .env

sudo systemctl daemon-reload
sudo systemctl enable ollama garrison-agent
./garrison.sh start
```

## Architecture

```
[Agent host — Mac or Linux server]
  Ollama LLM  →  LangGraph agent (gary)  →  Open WebUI / CLI

[Transport]
  WinRM/pypsrp (Windows, port 5985/5986)    SSH/paramiko (Linux, port 22)
        ↓                                          ↓
[Target hosts]
  Windows: Get-WinEvent, Get-Service,    Linux: journalctl, systemctl,
           Get-Process, CimInstance             ps, ss, free, df

[Qdrant — localhost:6333]
  Collection: host_logs — shared log index for semantic RAG
```

## Security model

- Credentials **only** in `.env` — `agency.yaml` holds env var names, not values
- Both are gitignored and never committed
- Windows: local `svc_monitor` service account per host, WinRM HTTPS for production
- Linux: per-host ed25519 SSH keys at `~/.ssh/monitor_{hostname}`
- New hosts go through `discovered.yaml` staging — operator reviews and promotes
  via `python manage.py promote <hostname>`

## License

See [LICENSE](LICENSE).
