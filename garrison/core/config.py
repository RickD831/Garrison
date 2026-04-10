"""
core/config.py — Configuration loader

Loads agency.yaml + .env, resolves credential env vars,
applies defaults inheritance, and builds HostConnection objects.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

from core.connection import HostConnection


def load_config(
    config_path: str | Path = "agency.yaml",
    env_path: str | Path = ".env",
) -> "AgencyConfig":
    env_file = Path(env_path)
    if env_file.exists():
        load_dotenv(env_file)
    return AgencyConfig(Path(config_path))


class AgencyConfig:
    def __init__(self, config_path: Path) -> None:
        if not config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {config_path}\n"
                f"Copy agency.yaml.example to agency.yaml and fill in your hosts."
            )
        with open(config_path) as f:
            raw = yaml.safe_load(f)

        self._raw = raw
        self.agency_name: str = raw.get("agency", {}).get("name", "Unknown Agency")
        self._defaults: dict = raw.get("defaults", {})
        self._hosts_raw: list[dict] = raw.get("hosts", [])

    # ── Public API ───────────────────────────────────────────────────────────

    def get_host(self, name: str) -> HostConnection:
        """Return a HostConnection for the named host. Raises KeyError if not found."""
        for entry in self._hosts_raw:
            if entry["name"] == name:
                return self._build_connection(entry)
        raise KeyError(f"Host '{name}' not found in agency.yaml")

    def get_host_by_address(self, address: str) -> HostConnection:
        """Return a HostConnection by IP/hostname. Raises KeyError if not found."""
        for entry in self._hosts_raw:
            if entry["address"] == address:
                return self._build_connection(entry)
        raise KeyError(f"No host with address '{address}' in agency.yaml")

    def all_hosts(self) -> list[HostConnection]:
        return [self._build_connection(e) for e in self._hosts_raw]

    def host_names(self) -> list[str]:
        return [e["name"] for e in self._hosts_raw]

    def default_auth(self, os_type: str) -> dict:
        """Return resolved auth dict for the given OS default."""
        defaults = self._defaults.get(os_type, {})
        return self._resolve_auth(defaults.get("auth", {}), placeholder="")

    def default_transport(self, os_type: str) -> str:
        return self._defaults.get(os_type, {}).get("transport", "ssh" if os_type == "linux" else "winrm")

    def default_port(self, os_type: str) -> int:
        return self._defaults.get(os_type, {}).get("port", 22 if os_type == "linux" else 5986)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _build_connection(self, entry: dict) -> HostConnection:
        os_type: str = entry["os"]
        os_defaults = self._defaults.get(os_type, {})

        transport: str = entry.get("transport") or os_defaults.get("transport", "ssh")
        port: int = int(entry.get("port") or os_defaults.get("port", 22 if os_type == "linux" else 5986))

        # Merge auth: host-level overrides os defaults
        default_auth = copy.deepcopy(os_defaults.get("auth", {}))
        host_auth = entry.get("auth", {})
        merged_auth = {**default_auth, **host_auth}

        # Substitute {hostname} in key_path
        if "key_path" in merged_auth:
            merged_auth["key_path"] = merged_auth["key_path"].replace(
                "{hostname}", entry["name"]
            )
            merged_auth["key_path"] = os.path.expanduser(merged_auth["key_path"])

        resolved_auth = self._resolve_auth(merged_auth, entry["name"])

        return HostConnection(
            name=entry["name"],
            address=entry["address"],
            os=os_type,
            transport=transport,
            auth=resolved_auth,
            port=port,
        )

    def _resolve_auth(self, auth: dict, placeholder: str) -> dict:
        """Resolve password_env references to actual values from environment."""
        resolved = dict(auth)
        if "password_env" in resolved:
            env_key = resolved.pop("password_env")
            value = os.environ.get(env_key)
            if value is None:
                raise EnvironmentError(
                    f"Credential env var '{env_key}' is not set. "
                    f"Add it to your .env file."
                )
            resolved["password"] = value
        return resolved
