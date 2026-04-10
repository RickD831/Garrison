"""
tools/process_tools.py — Process, service, port, and software tools

Tools: get_running_services, get_failed_services, get_top_processes,
       get_open_ports, get_installed_software
"""

from __future__ import annotations

from core.connection import HostConnection


def get_running_services(conn: HostConnection) -> str:
    """List all currently running/active services."""
    if conn.os == "linux":
        return conn.run(
            "systemctl list-units --type=service --state=active --no-pager "
            "--no-legend | awk '{print $1, $3, $4}'"
        )
    else:
        return conn.run(
            "Get-Service | Where-Object {$_.Status -eq 'Running'} "
            "| Select-Object Name, DisplayName, Status | Format-Table -AutoSize"
        )


def get_failed_services(conn: HostConnection) -> str:
    """List services that have failed or stopped unexpectedly."""
    if conn.os == "linux":
        return conn.run(
            "systemctl --failed --no-pager --no-legend "
            "| awk '{print $1, $2, $3, $4}'"
        )
    else:
        return conn.run(
            "Get-Service | Where-Object {$_.Status -eq 'Stopped'} "
            "| Select-Object Name, DisplayName, Status, StartType "
            "| Where-Object {$_.StartType -ne 'Disabled'} "
            "| Format-Table -AutoSize"
        )


def get_top_processes(conn: HostConnection, count: int = 20) -> str:
    """Return the top N processes by CPU usage."""
    if conn.os == "linux":
        return conn.run(
            f"ps aux --sort=-%cpu | head -n {count + 1}"
        )
    else:
        return conn.run(
            f"Get-Process | Sort-Object CPU -Descending "
            f"| Select-Object -First {count} Name, Id, CPU, WorkingSet, Description "
            f"| Format-Table -AutoSize"
        )


def get_open_ports(conn: HostConnection) -> str:
    """List listening TCP/UDP ports with associated processes."""
    if conn.os == "linux":
        return conn.run("ss -tunlp")
    else:
        return conn.run(
            "Get-NetTCPConnection -State Listen "
            "| Select-Object LocalAddress, LocalPort, OwningProcess, "
            "  @{N='ProcessName';E={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Name}} "
            "| Sort-Object LocalPort "
            "| Format-Table -AutoSize"
        )


def get_installed_software(conn: HostConnection) -> str:
    """List installed packages/software."""
    if conn.os == "linux":
        # Try dpkg first (Debian/Ubuntu), fall back to rpm (RHEL/CentOS)
        return conn.run(
            "if command -v dpkg &>/dev/null; then "
            "  dpkg -l | grep '^ii' | awk '{print $2, $3}'; "
            "elif command -v rpm &>/dev/null; then "
            "  rpm -qa --queryformat '%{NAME} %{VERSION}\\n' | sort; "
            "else echo 'No supported package manager found'; fi"
        )
    else:
        return conn.run(
            "Get-Package -ErrorAction SilentlyContinue "
            "| Select-Object Name, Version, ProviderName "
            "| Sort-Object Name "
            "| Format-Table -AutoSize"
        )
