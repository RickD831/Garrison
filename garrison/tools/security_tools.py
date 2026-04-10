"""
tools/security_tools.py — Security and threat hunting tools

Tools: get_firewall_rules, get_scheduled_tasks, get_startup_items,
       get_local_admins, get_rdp_sessions, get_suid_binaries,
       get_last_modified_configs
"""

from __future__ import annotations

from core.connection import HostConnection


def get_firewall_rules(conn: HostConnection) -> str:
    """Return active firewall rules, flagging anything recently changed or permissive."""
    if conn.os == "linux":
        cmd = (
            "echo '=== UFW Status ==='; "
            "if command -v ufw &>/dev/null; then ufw status verbose 2>/dev/null; "
            "else echo 'ufw not installed'; fi; "
            "echo ''; "
            "echo '=== iptables (INPUT chain) ==='; "
            "iptables -L INPUT -n -v 2>/dev/null || echo 'iptables not available'; "
            "echo ''; "
            "echo '=== ip6tables (INPUT chain) ==='; "
            "ip6tables -L INPUT -n -v 2>/dev/null || echo 'ip6tables not available'"
        )
        return conn.run(cmd)
    else:
        cmd = (
            "Write-Output '=== Enabled Inbound Rules ==='; "
            "Get-NetFirewallRule -Direction Inbound -Enabled True "
            "| Select-Object DisplayName, Action, Profile, "
            "  @{N='Protocol';E={(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $_).Protocol}}, "
            "  @{N='LocalPort';E={(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $_).LocalPort}} "
            "| Sort-Object Action "
            "| Format-Table -AutoSize; "
            "Write-Output ''; "
            "Write-Output '=== Recently Modified Rules (last 7 days) ==='; "
            "Get-NetFirewallRule "
            "| Where-Object { $_.PolicyStoreSourceType -eq 'Local' } "
            "| Select-Object DisplayName, Enabled, Direction, Action "
            "| Format-Table -AutoSize"
        )
        return conn.run(cmd)


def get_scheduled_tasks(conn: HostConnection) -> str:
    """Return scheduled tasks/cron jobs. Key persistence mechanism for attackers."""
    if conn.os == "linux":
        cmd = (
            "echo '=== Root crontab ==='; "
            "crontab -l 2>/dev/null || echo 'No root crontab'; "
            "echo ''; "
            "echo '=== /etc/crontab ==='; "
            "cat /etc/crontab 2>/dev/null || echo 'Not found'; "
            "echo ''; "
            "echo '=== /etc/cron.d/ ==='; "
            "ls /etc/cron.d/ 2>/dev/null && cat /etc/cron.d/* 2>/dev/null || echo 'Empty'; "
            "echo ''; "
            "echo '=== User crontabs ==='; "
            "for user in $(cut -f1 -d: /etc/passwd); do "
            "  crontab -u $user -l 2>/dev/null | grep -v '^#' | grep -v '^$' "
            "  | while read line; do echo \"$user: $line\"; done; "
            "done; "
            "echo ''; "
            "echo '=== Systemd Timers ==='; "
            "systemctl list-timers --all --no-pager 2>/dev/null | head -30"
        )
        return conn.run(cmd)
    else:
        cmd = (
            "Get-ScheduledTask "
            "| Where-Object { $_.TaskPath -notlike '\\Microsoft\\*' } "
            "| Select-Object TaskName, TaskPath, State, "
            "  @{N='LastRun';E={(Get-ScheduledTaskInfo -TaskName $_.TaskName -TaskPath $_.TaskPath "
            "    -ErrorAction SilentlyContinue).LastRunTime}}, "
            "  @{N='NextRun';E={(Get-ScheduledTaskInfo -TaskName $_.TaskName -TaskPath $_.TaskPath "
            "    -ErrorAction SilentlyContinue).NextRunTime}}, "
            "  @{N='RunAs';E={$_.Principal.UserId}} "
            "| Format-Table -AutoSize"
        )
        return conn.run(cmd)


def get_startup_items(conn: HostConnection) -> str:
    """Return programs configured to run at startup/login. Classic persistence vector."""
    if conn.os == "linux":
        cmd = (
            "echo '=== Systemd enabled units ==='; "
            "systemctl list-unit-files --state=enabled --no-pager 2>/dev/null | head -40; "
            "echo ''; "
            "echo '=== /etc/rc.local ==='; "
            "cat /etc/rc.local 2>/dev/null || echo 'Not present'; "
            "echo ''; "
            "echo '=== ~/.bashrc / ~/.profile additions ==='; "
            "grep -v '^#' /root/.bashrc /root/.profile 2>/dev/null | grep -v '^$' | head -20"
        )
        return conn.run(cmd)
    else:
        cmd = (
            "Write-Output '=== Registry Run Keys (HKLM) ==='; "
            "Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run' "
            "  -ErrorAction SilentlyContinue; "
            "Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce' "
            "  -ErrorAction SilentlyContinue; "
            "Write-Output ''; "
            "Write-Output '=== Registry Run Keys (HKCU) ==='; "
            "Get-ItemProperty 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run' "
            "  -ErrorAction SilentlyContinue; "
            "Write-Output ''; "
            "Write-Output '=== Startup Folder ==='; "
            "Get-ChildItem "
            "  'C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\StartUp' "
            "  -ErrorAction SilentlyContinue | Select-Object Name, LastWriteTime; "
            "Get-ChildItem "
            "  \"$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\" "
            "  -ErrorAction SilentlyContinue | Select-Object Name, LastWriteTime; "
            "Write-Output ''; "
            "Write-Output '=== Scheduled Tasks at Startup ==='; "
            "Get-ScheduledTask "
            "| Where-Object { $_.Triggers.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger' "
            "    -or $_.Triggers.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' } "
            "| Select-Object TaskName, TaskPath, "
            "  @{N='RunAs';E={$_.Principal.UserId}} "
            "| Format-Table -AutoSize"
        )
        return conn.run(cmd)


def get_local_admins(conn: HostConnection) -> str:
    """Return members of the local Administrators/sudo group."""
    if conn.os == "linux":
        cmd = (
            "echo '=== sudo group ==='; "
            "getent group sudo 2>/dev/null || echo 'No sudo group'; "
            "echo ''; "
            "echo '=== wheel group ==='; "
            "getent group wheel 2>/dev/null || echo 'No wheel group'; "
            "echo ''; "
            "echo '=== sudoers file (non-comment lines) ==='; "
            "grep -v '^#' /etc/sudoers 2>/dev/null | grep -v '^$'; "
            "echo ''; "
            "echo '=== sudoers.d/ ==='; "
            "ls /etc/sudoers.d/ 2>/dev/null && "
            "  grep -v '^#' /etc/sudoers.d/* 2>/dev/null | grep -v '^$' || "
            "  echo 'Empty or not found'"
        )
        return conn.run(cmd)
    else:
        cmd = (
            "Write-Output '=== Local Administrators ==='; "
            "net localgroup Administrators 2>&1; "
            "Write-Output ''; "
            "Write-Output '=== Recent Admin Group Changes (Event 4732/4733) ==='; "
            "$since = (Get-Date).AddDays(-30); "
            "Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4732,4733; StartTime=$since} "
            "  -MaxEvents 20 -ErrorAction SilentlyContinue "
            "| Select-Object TimeCreated, Id, "
            "  @{N='User';E={$_.Properties[0].Value}}, "
            "  @{N='Group';E={$_.Properties[2].Value}}, "
            "  @{N='ChangedBy';E={$_.Properties[6].Value}} "
            "| Format-Table -AutoSize"
        )
        return conn.run(cmd)


def get_rdp_sessions(conn: HostConnection) -> str:
    """Return active and recent RDP sessions (Windows). Event IDs 4778/4779."""
    if conn.os == "linux":
        return (
            "RDP session tracking is Windows-specific. "
            "For Linux remote access, use get_recent_logins to see SSH sessions."
        )
    else:
        cmd = (
            "Write-Output '=== Current RDP Sessions ==='; "
            "qwinsta 2>&1; "
            "Write-Output ''; "
            "Write-Output '=== Recent RDP Reconnects (4778) ==='; "
            "$since = (Get-Date).AddDays(-7); "
            "Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4778; StartTime=$since} "
            "  -MaxEvents 20 -ErrorAction SilentlyContinue "
            "| Select-Object TimeCreated, "
            "  @{N='User';E={$_.Properties[0].Value}}, "
            "  @{N='Domain';E={$_.Properties[1].Value}}, "
            "  @{N='SessionName';E={$_.Properties[2].Value}}, "
            "  @{N='ClientName';E={$_.Properties[4].Value}}, "
            "  @{N='ClientIP';E={$_.Properties[5].Value}} "
            "| Format-Table -AutoSize; "
            "Write-Output ''; "
            "Write-Output '=== Recent RDP Disconnects (4779) ==='; "
            "Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4779; StartTime=$since} "
            "  -MaxEvents 20 -ErrorAction SilentlyContinue "
            "| Select-Object TimeCreated, "
            "  @{N='User';E={$_.Properties[0].Value}}, "
            "  @{N='ClientName';E={$_.Properties[4].Value}}, "
            "  @{N='ClientIP';E={$_.Properties[5].Value}} "
            "| Format-Table -AutoSize"
        )
        return conn.run(cmd)


def get_suid_binaries(conn: HostConnection) -> str:
    """Find SUID/SGID binaries on Linux. Classic privilege escalation vector."""
    if conn.os == "windows":
        return (
            "SUID binaries are a Linux concept. "
            "For Windows privilege escalation vectors, use get_local_admins or get_startup_items."
        )
    else:
        cmd = (
            "echo '=== SUID binaries ==='; "
            "find / -perm -4000 -type f 2>/dev/null | sort; "
            "echo ''; "
            "echo '=== SGID binaries ==='; "
            "find / -perm -2000 -type f 2>/dev/null | sort"
        )
        return conn.run(cmd)


def get_last_modified_configs(conn: HostConnection, days: int = 7) -> str:
    """Return recently modified config files. Useful for detecting unauthorized changes."""
    if conn.os == "linux":
        cmd = (
            f"echo '=== /etc files modified in last {days} days ==='; "
            f"find /etc -type f -mtime -{days} -not -name '*.swp' 2>/dev/null "
            f"| xargs ls -la 2>/dev/null | sort -k6,7; "
            f"echo ''; "
            f"echo '=== /usr/local/bin modifications ==='; "
            f"find /usr/local/bin /usr/local/sbin -type f -mtime -{days} 2>/dev/null "
            f"| xargs ls -la 2>/dev/null"
        )
        return conn.run(cmd)
    else:
        cmd = (
            f"$since = (Get-Date).AddDays(-{days}); "
            f"Write-Output '=== Recently modified files in System32 ==='; "
            f"Get-ChildItem 'C:\\Windows\\System32' -File "
            f"| Where-Object {{ $_.LastWriteTime -gt $since }} "
            f"| Sort-Object LastWriteTime -Descending "
            f"| Select-Object -First 30 Name, LastWriteTime, Length "
            f"| Format-Table -AutoSize; "
            f"Write-Output ''; "
            f"Write-Output '=== Recently modified files in Program Files ==='; "
            f"Get-ChildItem 'C:\\Program Files', 'C:\\Program Files (x86)' -Recurse -File "
            f"  -ErrorAction SilentlyContinue "
            f"| Where-Object {{ $_.LastWriteTime -gt $since }} "
            f"| Sort-Object LastWriteTime -Descending "
            f"| Select-Object -First 20 FullName, LastWriteTime "
            f"| Format-Table -AutoSize"
        )
        return conn.run(cmd)
