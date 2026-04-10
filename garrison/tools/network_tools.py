"""
tools/network_tools.py — Network and connectivity tools

Tools: get_active_connections, get_dns_config,
       get_network_interfaces, get_listening_sockets_by_process
"""

from __future__ import annotations

from core.connection import HostConnection


def get_active_connections(conn: HostConnection) -> str:
    """
    Return established outbound/inbound connections with remote IPs and process names.
    More useful than open ports for detecting beaconing or lateral movement.
    """
    if conn.os == "linux":
        cmd = (
            "echo '=== Established TCP connections ==='; "
            "ss -tunp state established 2>/dev/null; "
            "echo ''; "
            "echo '=== Connections by remote IP (top talkers) ==='; "
            "ss -tn state established 2>/dev/null "
            "| awk 'NR>1 {print $5}' "
            "| cut -d: -f1 "
            "| sort | uniq -c | sort -rn | head -20"
        )
        return conn.run(cmd)
    else:
        cmd = (
            "Get-NetTCPConnection -State Established "
            "| Select-Object LocalAddress, LocalPort, RemoteAddress, RemotePort, "
            "  @{N='ProcessName';E={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Name}}, "
            "  @{N='ProcessPath';E={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Path}} "
            "| Sort-Object RemoteAddress "
            "| Format-Table -AutoSize"
        )
        return conn.run(cmd)


def get_dns_config(conn: HostConnection) -> str:
    """Return current DNS server configuration. Flag unexpected DNS servers."""
    if conn.os == "linux":
        cmd = (
            "echo '=== /etc/resolv.conf ==='; "
            "cat /etc/resolv.conf 2>/dev/null; "
            "echo ''; "
            "echo '=== systemd-resolved (if active) ==='; "
            "systemd-resolve --status 2>/dev/null | grep -A5 'DNS Servers' | head -20; "
            "echo ''; "
            "echo '=== /etc/hosts (non-comment entries) ==='; "
            "grep -v '^#' /etc/hosts | grep -v '^$'"
        )
        return conn.run(cmd)
    else:
        cmd = (
            "Write-Output '=== DNS Client Configuration ==='; "
            "Get-DnsClientServerAddress "
            "| Where-Object { $_.ServerAddresses.Count -gt 0 } "
            "| Select-Object InterfaceAlias, AddressFamily, ServerAddresses "
            "| Format-Table -AutoSize; "
            "Write-Output ''; "
            "Write-Output '=== hosts file entries ==='; "
            "Get-Content 'C:\\Windows\\System32\\drivers\\etc\\hosts' "
            "| Where-Object { $_ -notmatch '^#' -and $_.Trim() -ne '' }"
        )
        return conn.run(cmd)


def get_network_interfaces(conn: HostConnection) -> str:
    """Return all network interfaces with IPs and MAC addresses."""
    if conn.os == "linux":
        cmd = (
            "echo '=== Network interfaces ==='; "
            "ip addr show 2>/dev/null || ifconfig -a 2>/dev/null; "
            "echo ''; "
            "echo '=== Routing table ==='; "
            "ip route show 2>/dev/null || netstat -rn 2>/dev/null"
        )
        return conn.run(cmd)
    else:
        cmd = (
            "Write-Output '=== Network Adapters ==='; "
            "Get-NetIPAddress "
            "| Where-Object { $_.AddressState -eq 'Preferred' } "
            "| Select-Object InterfaceAlias, AddressFamily, IPAddress, PrefixLength "
            "| Format-Table -AutoSize; "
            "Write-Output ''; "
            "Write-Output '=== MAC Addresses ==='; "
            "Get-NetAdapter "
            "| Select-Object Name, MacAddress, Status, LinkSpeed "
            "| Format-Table -AutoSize; "
            "Write-Output ''; "
            "Write-Output '=== Default Gateway ==='; "
            "Get-NetRoute -DestinationPrefix '0.0.0.0/0' "
            "| Select-Object InterfaceAlias, NextHop "
            "| Format-Table -AutoSize"
        )
        return conn.run(cmd)


def get_listening_sockets_by_process(conn: HostConnection) -> str:
    """
    Return listening sockets mapped to full binary paths.
    Deeper than get_open_ports — useful for detecting suspicious listeners
    whose binary path doesn't match the expected location.
    """
    if conn.os == "linux":
        cmd = (
            "echo '=== Listening sockets with binary paths ==='; "
            "ss -tlnp 2>/dev/null | while read -r line; do "
            "  echo \"$line\"; "
            "done; "
            "echo ''; "
            "echo '=== PID to binary path mapping ==='; "
            "ss -tlnp 2>/dev/null "
            "| grep -oP 'pid=\\K[0-9]+' "
            "| sort -u "
            "| while read pid; do "
            "    path=$(readlink -f /proc/$pid/exe 2>/dev/null || echo 'unknown'); "
            "    echo \"PID $pid: $path\"; "
            "  done"
        )
        return conn.run(cmd)
    else:
        cmd = (
            "Get-NetTCPConnection -State Listen "
            "| Select-Object LocalAddress, LocalPort, OwningProcess, "
            "  @{N='ProcessName';E={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Name}}, "
            "  @{N='ProcessPath';E={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Path}}, "
            "  @{N='Company';E={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Company}} "
            "| Sort-Object LocalPort "
            "| Format-Table -AutoSize"
        )
        return conn.run(cmd)
