from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_HOME = "~/.a0-node"
CONFIG_VERSION = 1

DEFAULT_POLICY: dict[str, Any] = {
    "exec_enabled": False,
    "read_only": False,
    "roots": [],
    "max_read_bytes": 262144,
    "max_write_bytes": 262144,
    "max_list_entries": 500,
    "max_search_results": 200,
    "exec_timeout_seconds": 30,
    "max_exec_timeout_seconds": 3600,
    "max_output_bytes": 1048576,
}


class ConfigError(Exception):
    pass


def node_home() -> Path:
    override = os.environ.get("A0_NODE_HOME")
    if override:
        return Path(override).expanduser()
    return Path(DEFAULT_HOME).expanduser()


def config_path() -> Path:
    return node_home() / "config.json"


def audit_path() -> Path:
    return node_home() / "audit.jsonl"


def load_config() -> dict[str, Any]:
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(
            "node is not joined yet; run `python -m a0_node join --hub ... --code ...` first"
        ) from None
    except ValueError as exc:
        raise ConfigError(f"config file is invalid: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError("config file is invalid: expected an object")
    if data.get("version") != CONFIG_VERSION:
        raise ConfigError("config file version is not supported; join again")
    if not data.get("hub_url") or not data.get("device_id") or not data.get("device_token"):
        raise ConfigError("config file is incomplete; join again")
    return data


def save_config(data: dict[str, Any]) -> Path:
    home = node_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(home, 0o700)
    except OSError:
        pass
    fd, tmp_path = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=str(home))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, config_path())
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return config_path()


def merged_policy(config: dict[str, Any]) -> dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    raw = config.get("policy")
    if isinstance(raw, dict):
        policy.update(raw)
    return policy
