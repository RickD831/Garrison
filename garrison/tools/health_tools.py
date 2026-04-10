"""
tools/health_tools.py — Host health and reachability tools

Tools: get_host_health, check_host_reachable, get_disk_health, get_windows_updates
"""

from __future__ import annotations

import socket

from core.connection import HostConnection


def get_host_health(conn: HostConnection) -> str:
    """Return memory, CPU load, uptime, and disk summary for the host."""
    if conn.os == "linux":
        cmd = (
            "echo '=== Memory ==='; free -h; "
            "echo ''; "
            "echo '=== CPU Load ==='; uptime; "
            "echo ''; "
            "echo '=== Disk ==='; df -h --output=source,size,used,avail,pcent,target "
            "  | grep -v tmpfs | grep -v udev"
        )
        return conn.run(cmd)
    else:
        cmd = (
            "$os = Get-CimInstance Win32_OperatingSystem; "
            "$cpu = Get-CimInstance Win32_Processor | "
            "  Measure-Object -Property LoadPercentage -Average; "
            "$totalMem = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2); "
            "$freeMem  = [math]::Round($os.FreePhysicalMemory / 1MB, 2); "
            "$usedMem  = $totalMem - $freeMem; "
            "$uptime   = (Get-Date) - $os.LastBootUpTime; "
            "Write-Output ('=== Memory ==='); "
            "Write-Output ('Total: ' + $totalMem + ' GB  Used: ' + $usedMem + ' GB  Free: ' + $freeMem + ' GB'); "
            "Write-Output ''; "
            "Write-Output '=== CPU Load ==='; "
            "Write-Output ('Average CPU: ' + $cpu.Average + '%'); "
            "Write-Output ('Uptime: ' + [math]::Floor($uptime.TotalDays) + 'd ' + $uptime.Hours + 'h ' + $uptime.Minutes + 'm'); "
            "Write-Output ''; "
            "Write-Output '=== Disk ==='; "
            "Get-PSDrive -PSProvider FileSystem "
            "| Where-Object {$_.Used -ne $null} "
            "| Select-Object Name, "
            "  @{N='Used(GB)';E={[math]::Round($_.Used/1GB,1)}}, "
            "  @{N='Free(GB)';E={[math]::Round($_.Free/1GB,1)}}, "
            "  @{N='Total(GB)';E={[math]::Round(($_.Used+$_.Free)/1GB,1)}} "
            "| Format-Table -AutoSize"
        )
        return conn.run(cmd)


def get_disk_health(conn: HostConnection) -> str:
    """Return detailed disk usage, highlighting filesystems over 80% full."""
    if conn.os == "linux":
        return conn.run(
            "df -h --output=source,size,used,avail,pcent,target "
            "| grep -v tmpfs | grep -v udev"
        )
    else:
        return conn.run(
            "Get-Volume "
            "| Where-Object {$_.DriveLetter -ne $null} "
            "| Select-Object DriveLetter, FileSystemLabel, "
            "  @{N='Size(GB)';E={[math]::Round($_.Size/1GB,1)}}, "
            "  @{N='Used(GB)';E={[math]::Round(($_.Size-$_.SizeRemaining)/1GB,1)}}, "
            "  @{N='Free(GB)';E={[math]::Round($_.SizeRemaining/1GB,1)}}, "
            "  @{N='Used%';E={[math]::Round((($_.Size-$_.SizeRemaining)/$_.Size)*100,1)}} "
            "| Format-Table -AutoSize"
        )


def check_host_reachable(conn: HostConnection) -> str:
    """Ping the host and check that the expected service port is open."""
    import subprocess
    import sys

    host = conn.address
    port = conn.port

    # Ping (platform-neutral)
    ping_flag = "-n" if sys.platform == "win32" else "-c"
    ping_result = subprocess.run(
        ["ping", ping_flag, "3", "-W", "2", host],
        capture_output=True, text=True, timeout=15
    )
    ping_ok = ping_result.returncode == 0

    # TCP port check
    port_ok = False
    try:
        s = socket.create_connection((host, port), timeout=5)
        s.close()
        port_ok = True
    except OSError:
        pass

    lines = [
        f"Host:   {conn.name} ({host})",
        f"Ping:   {'OK' if ping_ok else 'FAILED'}",
        f"Port {port}: {'OPEN' if port_ok else 'CLOSED/FILTERED'}",
        f"Status: {'REACHABLE' if ping_ok and port_ok else 'UNREACHABLE or SERVICE DOWN'}",
    ]
    return "\n".join(lines)


def get_windows_updates(conn: HostConnection) -> str:
    """Return pending and recently installed Windows/system updates."""
    if conn.os == "linux":
        cmd = (
            "echo '=== Pending updates ==='; "
            "if command -v apt &>/dev/null; then "
            "  apt list --upgradable 2>/dev/null | grep -v 'Listing...'; "
            "elif command -v yum &>/dev/null; then "
            "  yum check-update 2>/dev/null; "
            "elif command -v dnf &>/dev/null; then "
            "  dnf check-update 2>/dev/null; "
            "else echo 'No supported package manager found'; fi; "
            "echo ''; "
            "echo '=== Last 10 installed updates ==='; "
            "if command -v apt &>/dev/null; then "
            "  grep 'install ' /var/log/dpkg.log 2>/dev/null | tail -10; "
            "elif command -v rpm &>/dev/null; then "
            "  rpm -qa --last 2>/dev/null | head -10; "
            "fi"
        )
        return conn.run(cmd)
    else:
        cmd = (
            "Write-Output '=== Installed Hotfixes/Patches ==='; "
            "Get-HotFix "
            "| Sort-Object InstalledOn -Descending "
            "| Select-Object -First 20 HotFixID, Description, InstalledOn, InstalledBy "
            "| Format-Table -AutoSize; "
            "Write-Output ''; "
            "Write-Output '=== Pending Windows Updates ==='; "
            "$UpdateSession = New-Object -ComObject Microsoft.Update.Session; "
            "$UpdateSearcher = $UpdateSession.CreateUpdateSearcher(); "
            "try { "
            "  $Results = $UpdateSearcher.Search('IsInstalled=0'); "
            "  if ($Results.Updates.Count -eq 0) { "
            "    Write-Output 'No pending updates.'; "
            "  } else { "
            "    $Results.Updates | Select-Object Title, MsrcSeverity, "
            "      @{N='Size(MB)';E={[math]::Round($_.MaxDownloadSize/1MB,1)}} "
            "    | Format-Table -AutoSize; "
            "  } "
            "} catch { Write-Output 'Windows Update COM not available: ' + $_.Exception.Message }"
        )
        return conn.run(cmd)
