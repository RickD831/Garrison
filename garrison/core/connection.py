"""
core/connection.py — HostConnection abstraction

Wraps WinRM (pypsrp), SSH (paramiko), and local (subprocess) transports
behind a single .run() interface. Tools never branch on transport type;
they branch on conn.os (windows vs linux).
"""

from __future__ import annotations

import platform
import subprocess
import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class HostConnection:
    name: str
    address: str
    os: Literal["windows", "linux"]
    transport: Literal["winrm", "ssh", "local"]
    auth: dict = field(default_factory=dict)
    port: int = 0  # 0 means use transport default

    def __post_init__(self) -> None:
        if self.transport == "local" and platform.system() != "Linux":
            raise ValueError(
                "local transport is only supported on Linux agent hosts. "
                "Configure a remote transport (winrm or ssh) to monitor hosts from macOS."
            )
        if self.port == 0:
            self.port = 5986 if self.transport == "winrm" else 22

    def run(self, command: str) -> str:
        """Execute command on target host, return stdout as string."""
        if self.transport == "winrm":
            return self._run_winrm(command)
        elif self.transport == "ssh":
            return self._run_ssh(command)
        elif self.transport == "local":
            return self._run_local(command)
        else:
            raise ValueError(f"Unknown transport: {self.transport}")

    # ── WinRM ────────────────────────────────────────────────────────────────

    def _run_winrm(self, command: str) -> str:
        try:
            from pypsrp.client import Client
        except ImportError:
            raise RuntimeError("pypsrp is required for WinRM transport: pip install pypsrp")

        password = self.auth.get("password", "")
        username = self.auth.get("username", "svc_monitor")

        use_ssl = self.port != 5985  # 5985 = HTTP, 5986 = HTTPS
        client = Client(
            self.address,
            username=username,
            password=password,
            port=self.port,
            ssl=use_ssl,
            cert_validation=False,
        )
        stdout, stderr, had_errors = client.execute_ps(command)
        if had_errors and stderr and isinstance(stderr, str) and stderr.strip():
            logger.warning("WinRM stderr on %s: %s", self.name, stderr)
        return stdout or ""

    # ── SSH ──────────────────────────────────────────────────────────────────

    def _run_ssh(self, command: str) -> str:
        try:
            import paramiko
        except ImportError:
            raise RuntimeError("paramiko is required for SSH transport: pip install paramiko")

        username = self.auth.get("username", "svc_monitor")
        key_path = self.auth.get("key_path", "")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            connect_kwargs: dict = dict(
                hostname=self.address,
                port=self.port,
                username=username,
                timeout=30,
            )
            if key_path:
                connect_kwargs["key_filename"] = key_path
            else:
                password = self.auth.get("password")
                if password:
                    connect_kwargs["password"] = password

            client.connect(**connect_kwargs)
            _, stdout, stderr = client.exec_command(command, timeout=60)
            output = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            if err:
                logger.debug("SSH stderr on %s: %s", self.name, err)
            return output
        finally:
            client.close()

    # ── Local ────────────────────────────────────────────────────────────────

    def _run_local(self, command: str) -> str:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0 and result.stderr:
            logger.debug("Local stderr: %s", result.stderr)
        return result.stdout

    def __repr__(self) -> str:
        return f"HostConnection(name={self.name!r}, os={self.os!r}, transport={self.transport!r}, address={self.address!r})"
