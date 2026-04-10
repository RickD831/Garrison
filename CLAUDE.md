# Garrison — Project Context

Garrison is a local LLM agent for monitoring Windows Server and Ubuntu Linux hosts.
Built for Monterey Superior Court, designed to be shareable to other agencies.
The LLM agent inside Garrison is named **Gary**.
All data stays on-prem. No cloud calls. No exceptions.

---

## Current Status (as of April 2026)

**The codebase is fully built and working.** All files exist under `garrison/`.
Gary has been tested live against a Windows 11 host via WinRM.

**What's working:**
- Gary boots and responds to natural language queries
- WinRM connection to Windows hosts (port 5985 HTTP for POC)
- 16 tools all registered and callable by the LLM
- Context retention across a conversation (session active host)
- Qdrant + Open WebUI running in Docker via Colima
- `manage.py` CLI (promote, list-discovered, validate-config)

**What's next before production:**
- Switch Windows transport to HTTPS (port 5986) — needs self-signed cert on each target
- Generate SSH key pairs for Linux hosts (`ssh-keygen -t ed25519 -f ~/.ssh/monitor_{hostname}`)
- Start the log collector (`./garrison.sh start`) so RAG history builds up
- Replace the test password with a real service account credential
- Move from MacBook to Linux server — `garrison.sh` auto-detects OS and uses systemd on Linux

---

## Naming

- **Garrison** — the platform. The deployable thing. What gets handed to another agency.
  Use "Garrison" in docs, config files, service names, and repo name (`garrison/`).
- **Gary** — the LLM agent inside Garrison. The conversational layer operators talk to.
  Use `gary` as the agent variable name in code. "Ask Gary." "Gary flagged this."

---

## Stack

| Component | Choice |
|---|---|
| LLM runtime | Ollama — `gemma4:e4b` (fast), `gemma4:26b` (thorough), `qwen2.5:14b` (best tool use) |
| Agent framework | LangGraph 1.x via `langchain.agents.create_agent` |
| Transport — Windows | WinRM via `pypsrp` — port 5985 (HTTP/POC) or 5986 (HTTPS/prod) |
| Transport — Linux | SSH via `paramiko`, port 22 |
| Vector store | Qdrant (local Docker) |
| Embeddings | `nomic-embed-text` via Ollama |
| Chat interface | Open WebUI (port 3000) or CLI (`python agent.py`) |
| Container runtime | Colima on macOS, Docker on Linux |

---

## Critical Library Notes — Read Before Touching agent.py

The installed versions are **LangChain 1.x / LangGraph 1.x**, not the 0.3 era.
The API changed significantly. These are the correct imports:

```python
# CORRECT for LangChain 1.x
from langchain.agents import create_agent          # NOT langgraph.prebuilt.create_react_agent
from langchain_core.tools import StructuredTool    # NOT langchain.tools.Tool
from langchain_ollama import ChatOllama

# create_agent signature
gary = create_agent(llm, tools, system_prompt="...")   # NOT prompt=, NOT AgentExecutor

# Invocation — returns messages, not {"output": ...}
result = gary.invoke({"messages": [{"role": "user", "content": query}]})
output = result["messages"][-1].content
```

**Do NOT use:**
- `AgentExecutor` — removed in LangChain 1.x
- `create_react_agent` from `langgraph.prebuilt` — deprecated, moved to `langchain.agents` as `create_agent`
- `Tool(name=..., func=...)` — use `StructuredTool.from_function()` instead
- `PromptTemplate` for agent prompts — pass `system_prompt=` string directly

---

## Architecture — 4 Layers

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
  Collection: host_logs
  Shared log index — both OS collectors write here
  nomic-embed-text embeddings, 5-min poll
```

**Key rule:** OS is detected at call time and stored on the HostConnection object.
Tools never branch at the agent level — same tool name, two implementations inside.

---

## File Layout

```
garrison/
├── agent.py                   # Main entrypoint — Gary, all tools, CLI loop
├── server.py                  # FastAPI OpenAI-compatible API — connects Open WebUI to Gary
├── manage.py                  # CLI: promote, list-discovered, validate-config
├── garrison.sh                # start/stop/status/logs — macOS + Linux aware
├── docker-compose.yml         # Qdrant (6333) + Open WebUI (3000)
├── requirements.txt
├── agency.yaml.example        # template — copy to agency.yaml (gitignored)
├── .env.example               # template — copy to .env (gitignored)
├── .gitignore
├── ollama.service             # Linux systemd unit for Ollama (production)
├── garrison-agent.service     # Linux systemd unit for collector (production)
│
├── core/
│   ├── config.py              # Loads agency.yaml + .env, builds HostConnection objects
│   ├── connection.py          # HostConnection dataclass — WinRM / SSH / local transports
│   └── discovery.py          # discover_host() — probe, detect OS, try creds, stage
│
├── tools/
│   ├── log_tools.py           # get_recent_logs, get_log_errors_summary, search_logs
│   ├── auth_tools.py          # get_recent_logins, get_sudo_activity, get_logged_in_users
│   ├── process_tools.py       # get_running_services, get_failed_services, get_top_processes,
│   │                          #   get_open_ports, get_installed_software
│   ├── health_tools.py        # get_host_health, check_host_reachable, get_disk_health
│   └── rag_tool.py            # search_log_history — Qdrant semantic search
│
└── collector/
    └── indexer.py             # Background log embedder — polls all hosts, writes to Qdrant
```

---

## Authentication Model

**Windows:**
- Local service account `svc_monitor` on each Windows host
- WinRM HTTP port 5985 for POC, HTTPS port 5986 for production
- SSL auto-detected: `ssl = (port != 5985)` in `core/connection.py`
- Password stored in `.env` as `WIN_SVC_PASSWORD`, referenced by name in `agency.yaml`
- Per-host auth overrides supported (e.g. DMZ hosts with different accounts)

**Linux:**
- Per-host ed25519 SSH key pairs
- Key path pattern: `~/.ssh/monitor_{hostname}` (hostname substituted at runtime)
- Service account `svc_monitor` on each Linux target
- No password in `.env` for SSH — key path in `agency.yaml` defaults block

**Secrets rule:** Credentials ONLY in `.env`. `agency.yaml` holds env var names, not values.
`.env` and `agency.yaml` are both in `.gitignore` and never committed.

---

## agency.yaml Structure

```yaml
agency:
  name: "Monterey Superior Court"

defaults:
  windows:
    transport: winrm
    port: 5985          # switch to 5986 + HTTPS for production
    auth:
      method: service_account
      username: "svc_monitor"
      password_env: "WIN_SVC_PASSWORD"   # resolved from .env
  linux:
    transport: ssh
    port: 22
    auth:
      method: key
      username: "svc_monitor"
      key_path: "~/.ssh/monitor_{hostname}"   # {hostname} substituted at runtime

hosts:
  - name: "fileserver01"
    address: "192.168.10.20"
    os: windows

  - name: "appserver01"
    address: "192.168.10.30"
    os: linux
```

---

## Session State (agent.py)

`agent.py` keeps a module-level `_session` dict:

```python
_session = {"active_host": None}
```

Every time `_resolve_host()` successfully resolves a host, it stores the connection in
`_session["active_host"]`. Subsequent tool calls with empty/vague host identifiers fall
back to the active host automatically. This prevents Gary from asking "which host?" on
every follow-up question in a conversation.

---

## Discovery Flow

When a user asks about a host NOT in agency.yaml:

1. `discover_host(address)` — ping + probe ports 22, 5985, 5986
2. Port 5985/5986 open → Windows. Else → Linux.
3. Try default credentials from `agency.yaml` defaults block
4. **Success:** run the query + write to `discovered.yaml`
5. **Auth fail:** report which `.env` key needs to be set
6. **Unreachable:** report that

**Never auto-write to `agency.yaml`.** Operator reviews discovered.yaml and runs:

```bash
python manage.py promote <hostname>    # moves to agency.yaml
python manage.py list-discovered       # show staging queue
python manage.py validate-config       # verify all hosts + credentials resolve
```

---

## Tools (32 registered in agent.py)

Each tool accepts `conn: HostConnection` and branches internally by `conn.os`.

### Logs (`tools/log_tools.py`)
| Tool | Windows | Linux |
|---|---|---|
| `get_recent_logs` | `Get-WinEvent` System/App | `journalctl -p warning` |
| `get_log_errors_summary` | Event Log errors grouped by source | `journalctl` grouped by unit |
| `search_logs` | `Get-WinEvent -Message` filter | `journalctl --grep` |
| `get_event_log_sources` | `Get-WinEvent -ListLog` + top providers | journald units + `/var/log/*.log` |

### Auth (`tools/auth_tools.py`)
| Tool | Windows | Linux |
|---|---|---|
| `get_recent_logins` | Event IDs 4624/4625 | `auth.log`, `last`, `wtmp` |
| `get_sudo_activity` | Event ID 4672 | `journalctl _COMM=sudo` |
| `get_logged_in_users` | `query user` | `who -a` |

### Processes & Services (`tools/process_tools.py`)
| Tool | Windows | Linux |
|---|---|---|
| `get_running_services` | `Get-Service \| Where Running` | `systemctl list-units --active` |
| `get_failed_services` | `Get-Service \| Where Stopped` | `systemctl --failed` |
| `get_top_processes` | `Get-Process \| Sort CPU` | `ps aux --sort=-%cpu` |
| `get_open_ports` | `Get-NetTCPConnection` | `ss -tunlp` |
| `get_installed_software` | `Get-Package` / `winget` | `dpkg -l` / `rpm -qa` |

### Health (`tools/health_tools.py`)
| Tool | Windows | Linux |
|---|---|---|
| `get_host_health` | `CimInstance Win32_OS` + `Get-PSDrive` | `free`, `df`, `uptime` |
| `get_disk_health` | `Get-Volume` | `df -h` |
| `check_host_reachable` | ping + TCP socket | ping + TCP socket |
| `get_windows_updates` | `Get-HotFix` + Update COM search | `apt list --upgradable` / `yum check-update` |

### Security (`tools/security_tools.py`)
| Tool | Windows | Linux |
|---|---|---|
| `get_firewall_rules` | `Get-NetFirewallRule` inbound enabled | `ufw status` + `iptables -L INPUT` |
| `get_scheduled_tasks` | `Get-ScheduledTask` non-Microsoft | cron + `/etc/cron.d` + systemd timers |
| `get_startup_items` | Registry Run keys + Startup folders | systemd enabled + `rc.local` |
| `get_local_admins` | `net localgroup Administrators` + 4732/4733 | `sudo`/`wheel` groups + sudoers |
| `get_rdp_sessions` | `qwinsta` + Event 4778/4779 | (not applicable — use get_recent_logins) |
| `get_suid_binaries` | (not applicable) | `find / -perm -4000` |
| `get_last_modified_configs` | System32 + Program Files by mtime | `/etc` files by mtime |

### Network (`tools/network_tools.py`)
| Tool | Windows | Linux |
|---|---|---|
| `get_active_connections` | `Get-NetTCPConnection -State Established` | `ss -tunp state established` |
| `get_dns_config` | `Get-DnsClientServerAddress` + hosts file | `/etc/resolv.conf` + systemd-resolved |
| `get_network_interfaces` | `Get-NetIPAddress` + `Get-NetAdapter` + routes | `ip addr` + `ip route` |
| `get_listening_sockets_by_process` | `Get-NetTCPConnection -State Listen` + binary path | `ss -tlnp` + `/proc/pid/exe` |

### Summary / Comparison (`tools/summary_tools.py`)
| Tool | Description |
|---|---|
| `get_host_summary` | Full situation report — health, failed services, ports, connections, log errors, logins |
| `compare_hosts` | Side-by-side diff of services, ports, software count between two hosts |
| `get_patch_delta` | Package-by-package version diff between two hosts |

### RAG / Inventory
| Tool | Description |
|---|---|
| `search_log_history` | Qdrant semantic search over historical log data |
| `list_hosts` | Lists all hosts in agency.yaml |

---

## macOS vs Linux Deployment

`garrison.sh` detects `uname -s` and branches:

| Concern | macOS (dev) | Linux (production) |
|---|---|---|
| Ollama | Check it's running, warn if not | `sudo systemctl start ollama` |
| Collector | `nohup` + PID file → `logs/indexer.log` | `sudo systemctl start garrison-agent` |
| Log tailing | `tail -f logs/indexer.log` | `journalctl -u garrison-agent -f` |

On macOS, `docker compose` is **hyphenated** (`docker-compose`) when using Colima.
The `docker-compose.yml` works on both — `extra_hosts: host.docker.internal:host-gateway`
is a Linux workaround that is silently ignored on macOS Docker Desktop / Colima.

`local` transport is Linux-only and guarded in `core/connection.py`. On macOS,
the agent host is never a monitored host — only remote WinRM/SSH targets are monitored.

---

## Setup — Mac (dev)

```bash
# Prerequisites
brew install python@3.11 colima docker docker-compose
colima start
ollama pull llama3.1:8b && ollama pull nomic-embed-text

# Project
cd garrison/
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Config
cp agency.yaml.example agency.yaml   # fill in real hosts
cp .env.example .env                  # fill in real credentials

# Start services
docker-compose up -d                  # Qdrant + Open WebUI
python manage.py validate-config      # verify setup

# Run Gary
python agent.py                       # interactive CLI
# or
./garrison.sh gary                    # same thing via management script
```

## Setup — Linux (production)

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Copy service files
sudo cp ollama.service /etc/systemd/system/
sudo cp garrison-agent.service /etc/systemd/system/
# Edit both files: replace YOUR_USER with the actual service account

# Install Docker
# (follow docker.com/linux install for your distro)

# Project
sudo mkdir -p /opt/garrison && sudo chown $USER /opt/garrison
cd /opt/garrison
git clone <repo> .
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp agency.yaml.example agency.yaml && cp .env.example .env
# Fill in agency.yaml and .env

sudo systemctl daemon-reload
sudo systemctl enable ollama garrison-agent
./garrison.sh start
```

---

## Windows Target Setup (run as Administrator on each Windows host)

```powershell
# Enable WinRM
Enable-PSRemoting -Force -SkipNetworkProfileCheck

# Create service account (if not done)
net user svc_monitor <password> /add

# Grant required permissions
net localgroup "Event Log Readers" svc_monitor /add
net localgroup "Performance Monitor Users" svc_monitor /add
net localgroup "Remote Management Users" svc_monitor /add

# Allow agent host IP
Set-Item WSMan:\localhost\Client\TrustedHosts -Value "<agent-host-ip>" -Force

# Open firewall (if needed)
New-NetFirewallRule -DisplayName "WinRM HTTP" -Direction Inbound -Protocol TCP -LocalPort 5985 -Action Allow

# Verify
netstat -an | findstr 5985    # should show LISTENING
```

---

## Collector (collector/indexer.py)

- Polls every 5 minutes (env: `GARRISON_POLL_INTERVAL`)
- Windows: `Get-WinEvent` last N minutes → text chunks
- Linux: `journalctl --since=-Nm` → text chunks
- Chunk size: 20 lines per document (env: `GARRISON_CHUNK_SIZE`)
- Deduplication: MD5 hash stored in Qdrant payload
- Writes to Qdrant collection `host_logs` with metadata: `host`, `os`, `collected_at`
- Embedding model: `nomic-embed-text` via Ollama

To start manually: `python collector/indexer.py`
To start via script: `./garrison.sh start`

---

## Open WebUI Integration (server.py)

`server.py` is a FastAPI app that exposes Gary as an OpenAI-compatible API so
Open WebUI can talk to the full agent (all 32 tools) instead of raw Ollama.

**Run it:**
```bash
python server.py          # starts on port 8000
./garrison.sh server start   # same, backgrounded with PID file
```

**Connect Open WebUI:**
1. http://localhost:3000 → profile → Settings → Admin Panel → Connections
2. OpenAI API → `+` → URL: `http://host.docker.internal:8000/v1`, Key: `garrison`
3. Select **gary** as the model in the chat

**Health check:** `curl http://localhost:8000/health`

**How it works:**
- Open WebUI POSTs to `/v1/chat/completions` with the full message history
- `server.py` passes the messages to Gary via `gary.stream(stream_mode="updates")`
- Every tool Gary invokes is intercepted and emitted as a live status line
- Final answer is streamed as regular content after a `---` separator
- Blocking (non-streaming) mode still works via `stream: false`

**Live activity trail:**
When `stream: true`, users see Gary's work in real time before the final answer:

```
> _Gary is working..._
> _• Thinking..._
> _• Running health check_
> _• Running health check ✓_
> _• Checking failed services_
> _• Checking failed services ✓_
> _• Scanning open ports_
> _• Scanning open ports ✓_

---

Here's the report for win11-test:
- Memory: 8.2 GB used of 16 GB...
```

Tool labels come from `_TOOL_LABELS` in `server.py` — add a friendly label
there whenever a new tool is added to the registry. The activity trail is
emitted as BOTH visible italic content (universal fallback) and
`reasoning_content` (rendered as a collapsible "Thinking" block by newer
Open WebUI builds).

**garrison.sh commands:**
```bash
./garrison.sh server start     # start API server
./garrison.sh server stop      # stop it
./garrison.sh server status    # check if running
./garrison.sh logs server      # tail server.log
./garrison.sh start            # starts everything including server
```

**garrison.sh commands:**
```bash
./garrison.sh server start     # start API server
./garrison.sh server stop      # stop it
./garrison.sh server status    # check if running
./garrison.sh logs server      # tail server.log
./garrison.sh start            # starts everything including server
```

---

## What NOT to Do

- Never put credentials in `agency.yaml`, code, or any committed file
- Never auto-write to `agency.yaml` — use `discovered.yaml` staging
- Never branch on OS at the agent/dispatcher level — branch inside each tool
- Never hardcode host addresses or usernames
- Never call the agent anything other than Gary in code (`gary = create_agent(...)`)
- Never call the platform anything other than Garrison in docs and config
- Never use `AgentExecutor`, `create_react_agent` from langgraph, or `Tool()` — see library notes above
- Never commit `data/` — it's Qdrant/WebUI Docker volume data, gitignored
