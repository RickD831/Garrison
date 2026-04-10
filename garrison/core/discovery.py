"""
core/discovery.py — Host discovery

discover_host() probes an unknown address, detects OS, tries default
credentials, and stages the result in discovered.yaml.

Never auto-writes to agency.yaml. Use manage.py promote to move a
discovered host into the main inventory.
"""

from __future__ import annotations

import logging
import socket
from pathlib import Path
from typing import Optional

import yaml

from core.connection import HostConnection

logger = logging.getLogger(__name__)

DISCOVERED_FILE = Path("discovered.yaml")
PROBE_TIMEOUT = 3  # seconds per port probe


def discover_host(
    address: str,
    config,  # AgencyConfig — avoid circular import with string annotation
    name: Optional[str] = None,
) -> tuple[HostConnection | None, str]:
    """
    Probe an unknown host, detect OS, try default credentials.

    Returns:
        (HostConnection, message) on success
        (None, message) on failure
    """
    host_name = name or address

    # Step 1: Ping / TCP reachability
    logger.info("Probing %s ...", address)
    open_ports = _probe_ports(address, [22, 5985, 5986])

    if not open_ports and not _is_pingable(address):
        return None, f"Host {address} is unreachable (no ping response, no open ports)."

    # Step 2: Detect OS
    os_type = _detect_os(open_ports)
    logger.info("Detected OS: %s (open ports: %s)", os_type, open_ports)

    # Step 3: Build connection with default credentials
    try:
        conn = _build_discovery_conn(host_name, address, os_type, open_ports, config)
    except EnvironmentError as e:
        return None, (
            f"Host {address} appears to be {os_type} but the required credential "
            f"is not set in .env:\n  {e}"
        )

    # Step 4: Test the connection
    try:
        result = conn.run("echo garrison-probe-ok" if os_type == "linux" else "Write-Output 'garrison-probe-ok'")
        if "garrison-probe-ok" not in result:
            return None, f"Connected to {address} but probe command returned unexpected output: {result!r}"
    except Exception as e:
        return None, (
            f"Authentication failed on {address} ({os_type}). "
            f"Check that the service account credentials are correct.\n"
            f"Error: {e}"
        )

    # Step 5: Stage in discovered.yaml
    _stage_host(host_name, address, os_type, conn, config)
    return conn, (
        f"Discovered {address} as {os_type} host '{host_name}'. "
        f"Staged in discovered.yaml. Run `python manage.py promote {host_name}` "
        f"to add it to agency.yaml."
    )


# ── Internal helpers ─────────────────────────────────────────────────────────


def _probe_ports(address: str, ports: list[int]) -> list[int]:
    open_ports = []
    for port in ports:
        try:
            s = socket.create_connection((address, port), timeout=PROBE_TIMEOUT)
            s.close()
            open_ports.append(port)
        except OSError:
            pass
    return open_ports


def _is_pingable(address: str) -> bool:
    import subprocess, sys
    flag = "-n" if sys.platform == "win32" else "-c"
    result = subprocess.run(
        ["ping", flag, "2", "-W", "1", address],
        capture_output=True, timeout=10
    )
    return result.returncode == 0


def _detect_os(open_ports: list[int]) -> str:
    if 5985 in open_ports or 5986 in open_ports:
        return "windows"
    return "linux"


def _build_discovery_conn(
    name: str,
    address: str,
    os_type: str,
    open_ports: list[int],
    config,
) -> HostConnection:
    transport = "winrm" if os_type == "windows" else "ssh"
    port = 5986 if (os_type == "windows" and 5986 in open_ports) else (5985 if os_type == "windows" else 22)
    auth = config.default_auth(os_type)

    return HostConnection(
        name=name,
        address=address,
        os=os_type,
        transport=transport,
        auth=auth,
        port=port,
    )


def _stage_host(
    name: str,
    address: str,
    os_type: str,
    conn: HostConnection,
    config,
) -> None:
    existing: dict = {}
    if DISCOVERED_FILE.exists():
        with open(DISCOVERED_FILE) as f:
            existing = yaml.safe_load(f) or {}

    hosts: list[dict] = existing.get("hosts", [])

    # Update if already staged, otherwise append
    for h in hosts:
        if h["name"] == name or h["address"] == address:
            h.update({"name": name, "address": address, "os": os_type})
            break
    else:
        hosts.append({"name": name, "address": address, "os": os_type})

    existing["hosts"] = hosts
    with open(DISCOVERED_FILE, "w") as f:
        yaml.dump(existing, f, default_flow_style=False)

    logger.info("Staged %s in %s", name, DISCOVERED_FILE)


def load_discovered() -> list[dict]:
    """Return list of staged host entries from discovered.yaml."""
    if not DISCOVERED_FILE.exists():
        return []
    with open(DISCOVERED_FILE) as f:
        data = yaml.safe_load(f) or {}
    return data.get("hosts", [])
