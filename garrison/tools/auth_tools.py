"""
tools/auth_tools.py — Authentication and user session tools

Tools: get_recent_logins, get_sudo_activity, get_logged_in_users
"""

from __future__ import annotations

from core.connection import HostConnection


def get_recent_logins(conn: HostConnection, hours: int = 24) -> str:
    """Return recent login successes and failures."""
    if conn.os == "linux":
        cmd = (
            f"echo '=== Recent logins (last) ==='; "
            f"last -n 20 -F; "
            f"echo ''; "
            f"echo '=== Auth failures (last {hours}h) ==='; "
            f"journalctl --no-pager --since='-{hours}h' "
            f"  -u sshd --grep='Failed|Invalid|Disconnected' --output=short-iso -n 30"
        )
        return conn.run(cmd)
    else:
        cmd = (
            f"$since = (Get-Date).AddHours(-{hours}); "
            f"Write-Output '=== Login Successes (4624) ==='; "
            f"Get-WinEvent -FilterHashtable @{{LogName='Security'; Id=4624; StartTime=$since}} "
            f"  -MaxEvents 20 -ErrorAction SilentlyContinue "
            f"| Select-Object TimeCreated, "
            f"  @{{N='User';E={{$_.Properties[5].Value}}}}, "
            f"  @{{N='LogonType';E={{$_.Properties[8].Value}}}}, "
            f"  @{{N='SourceIP';E={{$_.Properties[18].Value}}}} "
            f"| Format-Table -AutoSize; "
            f"Write-Output ''; "
            f"Write-Output '=== Login Failures (4625) ==='; "
            f"Get-WinEvent -FilterHashtable @{{LogName='Security'; Id=4625; StartTime=$since}} "
            f"  -MaxEvents 20 -ErrorAction SilentlyContinue "
            f"| Select-Object TimeCreated, "
            f"  @{{N='User';E={{$_.Properties[5].Value}}}}, "
            f"  @{{N='SourceIP';E={{$_.Properties[19].Value}}}} "
            f"| Format-Table -AutoSize"
        )
        return conn.run(cmd)


def get_sudo_activity(conn: HostConnection, hours: int = 24) -> str:
    """Return sudo/privilege escalation events."""
    if conn.os == "linux":
        cmd = (
            f"journalctl --no-pager --since='-{hours}h' "
            f"  _COMM=sudo --output=short-iso -n 50"
        )
        return conn.run(cmd)
    else:
        # Event ID 4672 — Special privileges assigned to new logon
        cmd = (
            f"$since = (Get-Date).AddHours(-{hours}); "
            f"Get-WinEvent -FilterHashtable @{{LogName='Security'; Id=4672; StartTime=$since}} "
            f"  -MaxEvents 50 -ErrorAction SilentlyContinue "
            f"| Select-Object TimeCreated, "
            f"  @{{N='User';E={{$_.Properties[1].Value}}}}, "
            f"  @{{N='Domain';E={{$_.Properties[2].Value}}}} "
            f"| Format-Table -AutoSize"
        )
        return conn.run(cmd)


def get_logged_in_users(conn: HostConnection) -> str:
    """Return currently logged-in users."""
    if conn.os == "linux":
        return conn.run("who -a")
    else:
        return conn.run("query user 2>&1")
