"""
tools/summary_tools.py — Aggregate and comparison tools

Tools: get_host_summary, compare_hosts, get_patch_delta

These tools call other tool functions internally to produce
higher-level briefings and cross-host comparisons.
"""

from __future__ import annotations

from core.connection import HostConnection


def get_host_summary(conn: HostConnection) -> str:
    """
    Full situation report for a host in one shot.
    Combines health, errors, auth events, open ports, failed services,
    and active connections into a single formatted briefing.
    Gary can call this when someone asks "how's X doing?" without
    needing to chain 5+ tool calls manually.
    """
    from tools.health_tools import get_host_health, get_disk_health
    from tools.process_tools import get_failed_services, get_open_ports
    from tools.auth_tools import get_recent_logins
    from tools.log_tools import get_log_errors_summary
    from tools.network_tools import get_active_connections

    sections = []

    sections.append("=" * 60)
    sections.append(f"HOST SUMMARY — {conn.name} ({conn.os.upper()}) — {conn.address}")
    sections.append("=" * 60)

    def _section(title: str, fn, *args, **kwargs) -> str:
        try:
            result = fn(conn, *args, **kwargs) if not args and not kwargs else fn(conn, *args, **kwargs)
            return f"\n--- {title} ---\n{result.strip()}" if result.strip() else f"\n--- {title} ---\n(no data)"
        except Exception as e:
            return f"\n--- {title} ---\nError: {e}"

    sections.append(_section("HEALTH", get_host_health))
    sections.append(_section("FAILED SERVICES", get_failed_services))
    sections.append(_section("OPEN PORTS", get_open_ports))
    sections.append(_section("ACTIVE CONNECTIONS (sample)", get_active_connections))
    sections.append(_section("LOG ERRORS (last 24h)", get_log_errors_summary))
    sections.append(_section("RECENT LOGINS", get_recent_logins))

    return "\n".join(sections)


def compare_hosts(conn_a: HostConnection, conn_b: HostConnection) -> str:
    """
    Side-by-side comparison of two hosts.
    Diffs running services, open ports, installed software counts,
    and health metrics. Useful for "why is server02 different from server01?"
    """
    from tools.health_tools import get_host_health
    from tools.process_tools import get_running_services, get_open_ports, get_installed_software
    from tools.health_tools import get_disk_health

    def _get(fn, conn) -> set[str]:
        try:
            raw = fn(conn)
            return set(line.strip() for line in raw.splitlines() if line.strip())
        except Exception as e:
            return {f"ERROR: {e}"}

    lines = []
    lines.append("=" * 60)
    lines.append(f"HOST COMPARISON: {conn_a.name} vs {conn_b.name}")
    lines.append("=" * 60)

    # Health side-by-side
    lines.append("\n--- HEALTH ---")
    try:
        lines.append(f"\n[{conn_a.name}]\n{get_host_health(conn_a).strip()}")
    except Exception as e:
        lines.append(f"\n[{conn_a.name}] Error: {e}")
    try:
        lines.append(f"\n[{conn_b.name}]\n{get_host_health(conn_b).strip()}")
    except Exception as e:
        lines.append(f"\n[{conn_b.name}] Error: {e}")

    # Services diff
    lines.append("\n--- RUNNING SERVICES DIFF ---")
    svc_a = _get(get_running_services, conn_a)
    svc_b = _get(get_running_services, conn_b)
    only_a = svc_a - svc_b
    only_b = svc_b - svc_a
    if only_a:
        lines.append(f"\nOnly on {conn_a.name}:")
        lines.extend(f"  {s}" for s in sorted(only_a)[:20])
    if only_b:
        lines.append(f"\nOnly on {conn_b.name}:")
        lines.extend(f"  {s}" for s in sorted(only_b)[:20])
    if not only_a and not only_b:
        lines.append("  Running services are identical.")

    # Ports diff
    lines.append("\n--- OPEN PORTS DIFF ---")
    ports_a = _get(get_open_ports, conn_a)
    ports_b = _get(get_open_ports, conn_b)
    only_ports_a = ports_a - ports_b
    only_ports_b = ports_b - ports_a
    if only_ports_a:
        lines.append(f"\nOnly on {conn_a.name}:")
        lines.extend(f"  {p}" for p in sorted(only_ports_a)[:20])
    if only_ports_b:
        lines.append(f"\nOnly on {conn_b.name}:")
        lines.extend(f"  {p}" for p in sorted(only_ports_b)[:20])
    if not only_ports_a and not only_ports_b:
        lines.append("  Open ports are identical.")

    # Software count comparison
    lines.append("\n--- INSTALLED SOFTWARE ---")
    try:
        sw_a = get_installed_software(conn_a).splitlines()
        sw_b = get_installed_software(conn_b).splitlines()
        lines.append(f"  {conn_a.name}: {len(sw_a)} packages")
        lines.append(f"  {conn_b.name}: {len(sw_b)} packages")
        lines.append("  (run get_patch_delta for full diff)")
    except Exception as e:
        lines.append(f"  Error: {e}")

    return "\n".join(lines)


def get_patch_delta(conn_a: HostConnection, conn_b: HostConnection) -> str:
    """
    Compare installed software between two hosts to find patch/version gaps.
    Highlights packages present on one host but missing or different on the other.
    """
    from tools.process_tools import get_installed_software

    def _parse_packages(conn: HostConnection) -> dict[str, str]:
        """Return {package_name: version} dict."""
        packages = {}
        try:
            raw = get_installed_software(conn)
            for line in raw.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    packages[parts[0].lower()] = parts[1]
        except Exception:
            pass
        return packages

    pkgs_a = _parse_packages(conn_a)
    pkgs_b = _parse_packages(conn_b)

    lines = []
    lines.append("=" * 60)
    lines.append(f"PATCH DELTA: {conn_a.name} vs {conn_b.name}")
    lines.append("=" * 60)
    lines.append(f"\n{conn_a.name}: {len(pkgs_a)} packages")
    lines.append(f"{conn_b.name}: {len(pkgs_b)} packages")

    # Packages only on A
    only_a = sorted(set(pkgs_a) - set(pkgs_b))
    if only_a:
        lines.append(f"\n--- Only on {conn_a.name} ({len(only_a)} packages) ---")
        for pkg in only_a[:30]:
            lines.append(f"  {pkg} {pkgs_a[pkg]}")
        if len(only_a) > 30:
            lines.append(f"  ... and {len(only_a) - 30} more")

    # Packages only on B
    only_b = sorted(set(pkgs_b) - set(pkgs_a))
    if only_b:
        lines.append(f"\n--- Only on {conn_b.name} ({len(only_b)} packages) ---")
        for pkg in only_b[:30]:
            lines.append(f"  {pkg} {pkgs_b[pkg]}")
        if len(only_b) > 30:
            lines.append(f"  ... and {len(only_b) - 30} more")

    # Version differences
    common = set(pkgs_a) & set(pkgs_b)
    version_diffs = [(p, pkgs_a[p], pkgs_b[p]) for p in common if pkgs_a[p] != pkgs_b[p]]
    if version_diffs:
        lines.append(f"\n--- Version differences ({len(version_diffs)} packages) ---")
        lines.append(f"  {'Package':<30} {conn_a.name:<20} {conn_b.name:<20}")
        lines.append("  " + "-" * 70)
        for pkg, va, vb in sorted(version_diffs)[:30]:
            lines.append(f"  {pkg:<30} {va:<20} {vb:<20}")

    if not only_a and not only_b and not version_diffs:
        lines.append("\nNo differences found — both hosts have identical packages.")

    return "\n".join(lines)
