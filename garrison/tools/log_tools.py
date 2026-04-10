"""
tools/log_tools.py — Log retrieval and search tools

Tools: get_recent_logs, get_log_errors_summary, search_logs, get_event_log_sources
Each tool branches internally on conn.os. Same interface for both platforms.
"""

from __future__ import annotations

from core.connection import HostConnection


def get_recent_logs(conn: HostConnection, hours: int = 1, lines: int = 100) -> str:
    """Return recent warning/error log entries from the past N hours."""
    if conn.os == "linux":
        cmd = f"journalctl --no-pager -p warning --since='-{hours}h' -n {lines} --output=short-iso"
        return conn.run(cmd)
    else:
        cmd = (
            f"$since = (Get-Date).AddHours(-{hours}); "
            f"Get-WinEvent -FilterHashtable @{{LogName='System','Application'; "
            f"Level=1,2,3; StartTime=$since}} -MaxEvents {lines} -ErrorAction SilentlyContinue "
            f"| Select-Object TimeCreated, Id, LevelDisplayName, ProviderName, Message "
            f"| Format-List"
        )
        return conn.run(cmd)


def get_log_errors_summary(conn: HostConnection, hours: int = 24) -> str:
    """Return a summary of errors grouped by service/source for the past N hours."""
    if conn.os == "linux":
        cmd = (
            f"journalctl --no-pager -p err --since='-{hours}h' --output=short-iso "
            f"| awk '{{print $5}}' | sort | uniq -c | sort -rn | head -20"
        )
        return conn.run(cmd)
    else:
        cmd = (
            f"$since = (Get-Date).AddHours(-{hours}); "
            f"Get-WinEvent -FilterHashtable @{{LogName='System','Application','Security'; "
            f"Level=1,2; StartTime=$since}} -ErrorAction SilentlyContinue "
            f"| Group-Object ProviderName "
            f"| Sort-Object Count -Descending "
            f"| Select-Object -First 20 Count, Name "
            f"| Format-Table -AutoSize"
        )
        return conn.run(cmd)


def search_logs(conn: HostConnection, query: str, hours: int = 24) -> str:
    """Full-text search across recent log entries matching the query string."""
    # Basic sanitization — strip shell metacharacters
    safe_query = query.replace('"', '').replace("'", '').replace(';', '').replace('`', '')

    if conn.os == "linux":
        cmd = f'journalctl --no-pager --since="-{hours}h" --grep="{safe_query}" --output=short-iso -n 50'
        return conn.run(cmd)
    else:
        cmd = (
            f"$since = (Get-Date).AddHours(-{hours}); "
            f"Get-WinEvent -FilterHashtable @{{LogName='System','Application','Security'; "
            f"StartTime=$since}} -ErrorAction SilentlyContinue "
            f"| Where-Object {{ $_.Message -like '*{safe_query}*' }} "
            f"| Select-Object -First 50 TimeCreated, Id, LevelDisplayName, ProviderName, Message "
            f"| Format-List"
        )
        return conn.run(cmd)


def get_event_log_sources(conn: HostConnection) -> str:
    """
    Return a list of available log sources / providers on the host.
    Useful for discovering what's actually being logged before drilling in.
    """
    if conn.os == "linux":
        cmd = (
            "echo '=== Systemd journal units (top 30 by volume) ==='; "
            "journalctl --no-pager --output=short-iso --since='-24h' 2>/dev/null "
            "| awk '{print $4}' "
            "| sort | uniq -c | sort -rn | head -30; "
            "echo ''; "
            "echo '=== /var/log files ==='; "
            "ls -lh /var/log/*.log 2>/dev/null | head -20"
        )
        return conn.run(cmd)
    else:
        cmd = (
            "Write-Output '=== Available Event Logs ==='; "
            "Get-WinEvent -ListLog * -ErrorAction SilentlyContinue "
            "| Where-Object { $_.RecordCount -gt 0 } "
            "| Sort-Object RecordCount -Descending "
            "| Select-Object -First 30 LogName, RecordCount, LogMode "
            "| Format-Table -AutoSize; "
            "Write-Output ''; "
            "Write-Output '=== Top Providers in System Log (last 24h) ==='; "
            "$since = (Get-Date).AddHours(-24); "
            "Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$since} "
            "  -ErrorAction SilentlyContinue "
            "| Group-Object ProviderName "
            "| Sort-Object Count -Descending "
            "| Select-Object -First 20 Count, Name "
            "| Format-Table -AutoSize"
        )
        return conn.run(cmd)
