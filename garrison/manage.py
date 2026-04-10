#!/usr/bin/env python3
"""
manage.py — Garrison management CLI

Commands:
  promote <hostname>     Move a host from discovered.yaml to agency.yaml
  list-discovered        Show all staged hosts pending promotion
  validate-config        Validate agency.yaml and test credential resolution
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


AGENCY_FILE = Path("agency.yaml")
DISCOVERED_FILE = Path("discovered.yaml")


def cmd_list_discovered(args) -> None:
    if not DISCOVERED_FILE.exists():
        print("discovered.yaml does not exist — no hosts staged yet.")
        return

    with open(DISCOVERED_FILE) as f:
        data = yaml.safe_load(f) or {}

    hosts = data.get("hosts", [])
    if not hosts:
        print("No hosts staged for promotion.")
        return

    print(f"{'NAME':<20} {'ADDRESS':<20} {'OS':<10}")
    print("-" * 52)
    for h in hosts:
        print(f"{h.get('name',''):<20} {h.get('address',''):<20} {h.get('os',''):<10}")
    print(f"\n{len(hosts)} host(s) staged. Use `python manage.py promote <name>` to add to agency.yaml.")


def cmd_promote(args) -> None:
    hostname = args.hostname

    if not DISCOVERED_FILE.exists():
        print(f"Error: discovered.yaml not found. Nothing to promote.")
        sys.exit(1)

    with open(DISCOVERED_FILE) as f:
        discovered = yaml.safe_load(f) or {}

    staged = discovered.get("hosts", [])
    target = next((h for h in staged if h["name"] == hostname), None)
    if target is None:
        print(f"Error: '{hostname}' not found in discovered.yaml.")
        print("Run `python manage.py list-discovered` to see what's staged.")
        sys.exit(1)

    # Load agency.yaml
    if not AGENCY_FILE.exists():
        print(f"Error: agency.yaml not found. Copy agency.yaml.example to agency.yaml first.")
        sys.exit(1)

    with open(AGENCY_FILE) as f:
        agency = yaml.safe_load(f) or {}

    agency_hosts: list[dict] = agency.get("hosts", [])

    # Check for duplicate
    if any(h["name"] == hostname for h in agency_hosts):
        print(f"'{hostname}' already exists in agency.yaml. No changes made.")
        sys.exit(0)

    agency_hosts.append(target)
    agency["hosts"] = agency_hosts

    with open(AGENCY_FILE, "w") as f:
        yaml.dump(agency, f, default_flow_style=False)

    # Remove from discovered.yaml
    remaining = [h for h in staged if h["name"] != hostname]
    discovered["hosts"] = remaining
    with open(DISCOVERED_FILE, "w") as f:
        yaml.dump(discovered, f, default_flow_style=False)

    print(f"Promoted '{hostname}' to agency.yaml.")
    if remaining:
        print(f"{len(remaining)} host(s) still staged in discovered.yaml.")
    else:
        print("discovered.yaml is now empty.")


def cmd_validate_config(args) -> None:
    from core.config import load_config

    print(f"Validating {AGENCY_FILE} ...")

    try:
        cfg = load_config()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Config load failed: {e}")
        sys.exit(1)

    print(f"Agency: {cfg.agency_name}")
    print(f"Hosts:  {len(cfg.host_names())}")
    print()

    errors = []
    for name in cfg.host_names():
        try:
            conn = cfg.get_host(name)
            print(f"  OK  {name:<20} {conn.os:<8} {conn.transport}  {conn.address}:{conn.port}")
        except EnvironmentError as e:
            errors.append((name, str(e)))
            print(f"  ERR {name:<20} — {e}")
        except Exception as e:
            errors.append((name, str(e)))
            print(f"  ERR {name:<20} — {e}")

    print()
    if errors:
        print(f"{len(errors)} error(s) found. Fix the issues above and re-run.")
        sys.exit(1)
    else:
        print("All hosts valid.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="Garrison management CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list-discovered
    sub.add_parser("list-discovered", help="Show staged hosts pending promotion")

    # promote
    p = sub.add_parser("promote", help="Promote a discovered host to agency.yaml")
    p.add_argument("hostname", help="Name of the host to promote")

    # validate-config
    sub.add_parser("validate-config", help="Validate agency.yaml and credential resolution")

    args = parser.parse_args()

    if args.command == "list-discovered":
        cmd_list_discovered(args)
    elif args.command == "promote":
        cmd_promote(args)
    elif args.command == "validate-config":
        cmd_validate_config(args)


if __name__ == "__main__":
    main()
