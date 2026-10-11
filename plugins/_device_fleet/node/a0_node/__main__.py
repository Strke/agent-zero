from __future__ import annotations

import argparse
import json
import platform
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import NODE_VERSION
from .client import A0NodeClient
from .config import (
    CONFIG_VERSION,
    DEFAULT_POLICY,
    ConfigError,
    config_path,
    load_config,
    node_home,
    save_config,
)
from .ops import TOOL_DESCRIPTORS
from .service import install_service, uninstall_service

PAIR_CLAIM_PATH = "/api/plugins/_device_fleet/device_pair_claim"


def normalize_hub_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        raise SystemExit("--hub is required")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise SystemExit("hub url must use http or https")
    host = (parts.hostname or "").lower()
    if not host:
        raise SystemExit("hub url must include a host")
    loopback = host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")
    if parts.scheme != "https" and not loopback:
        raise SystemExit(
            "refusing plain http for a non-loopback hub; use https (tunnel or certificate)"
        )
    path = parts.path.rstrip("/")
    return f"{parts.scheme}://{parts.netloc}{path}"


def build_runtime() -> dict[str, str]:
    return {
        "os": platform.system().lower(),
        "arch": platform.machine(),
        "hostname": socket.gethostname(),
        "python": platform.python_version(),
        "node_version": NODE_VERSION,
    }


def claim_pairing(
    hub_url: str, code: str, name: str, policy: dict[str, Any]
) -> dict[str, Any]:
    payload = {
        "code": code,
        "name": name,
        "runtime": build_runtime(),
        "policy": {
            "exec_enabled": bool(policy.get("exec_enabled", False)),
            "read_only": bool(policy.get("read_only", False)),
            "roots": [str(root) for root in policy.get("roots", [])],
        },
        "tools": [descriptor["name"] for descriptor in TOOL_DESCRIPTORS],
    }
    request = urllib.request.Request(
        hub_url + PAIR_CLAIM_PATH,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"a0-node/{NODE_VERSION}",
        },
        method="POST",
    )
    body = ""
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit(
            f"pairing failed (HTTP {exc.code}): {_error_message(body) or exc.reason}"
        ) from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"could not reach hub {hub_url}: {exc.reason}") from None
    try:
        data = json.loads(body)
    except ValueError:
        raise SystemExit(f"pairing failed: unexpected response: {body[:200]}") from None
    if not isinstance(data, dict) or not data.get("ok"):
        raise SystemExit(f"pairing failed: {_error_message(body)}")
    return data


def _error_message(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body.strip()[:200]
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or body)
    return str(body)[:200]


def command_join(args: argparse.Namespace) -> int:
    hub_url = normalize_hub_url(args.hub)
    code = str(args.code or "").strip()
    if not code:
        raise SystemExit("--code is required")

    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    roots = [workspace]
    for extra in args.add_root or []:
        roots.append(Path(extra).expanduser().resolve())

    policy: dict[str, Any] = dict(DEFAULT_POLICY)
    policy["roots"] = [str(root) for root in roots]
    policy["exec_enabled"] = bool(args.enable_exec)
    policy["read_only"] = bool(args.read_only)
    if args.exec_timeout:
        policy["exec_timeout_seconds"] = int(args.exec_timeout)

    name = str(args.name or "").strip() or socket.gethostname()
    result = claim_pairing(hub_url, code, name, policy)

    config = {
        "version": CONFIG_VERSION,
        "hub_url": hub_url,
        "device_id": result["device_id"],
        "device_token": result["device_token"],
        "name": result.get("device_name") or name,
        "created_at": time.time(),
        "policy": {
            "roots": policy["roots"],
            "exec_enabled": policy["exec_enabled"],
            "read_only": policy["read_only"],
            "exec_timeout_seconds": policy.get("exec_timeout_seconds", 30),
        },
    }
    path = save_config(config)
    print(f"[a0-node] joined hub {hub_url} as {config['name']} ({config['device_id']})")
    print(f"[a0-node] config written to {path}")
    if not policy["exec_enabled"]:
        print("[a0-node] remote command execution is disabled (add --enable-exec to allow it)")
    print(f"[a0-node] writable roots: {', '.join(policy['roots'])}")
    print("[a0-node] start it now with: python -m a0_node run")
    print("[a0-node] or install a background service with: python -m a0_node install-service")
    return 0


def command_run(args: argparse.Namespace) -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[a0-node] {exc}", file=sys.stderr)
        return 2
    client = A0NodeClient(config)

    def _handle_signal(signum: int, frame: Any) -> None:
        print("[a0-node] stopping", flush=True)
        client.request_stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        return client.run_forever()
    finally:
        client.close()


def command_status(args: argparse.Namespace) -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[a0-node] {exc}", file=sys.stderr)
        return 2
    policy = config.get("policy") or {}
    print(f"name:       {config.get('name')}")
    print(f"device id:  {config.get('device_id')}")
    print(f"hub:        {config.get('hub_url')}")
    print(f"roots:      {', '.join(str(root) for root in policy.get('roots', [])) or '-'}")
    print(f"exec:       {'enabled' if policy.get('exec_enabled') else 'disabled'}")
    print(f"read only:  {'yes' if policy.get('read_only') else 'no'}")
    print(f"config:     {config_path()}")
    print(f"home:       {node_home()}")
    return 0


def command_install_service(args: argparse.Namespace) -> int:
    try:
        load_config()
    except ConfigError as exc:
        print(f"[a0-node] {exc}", file=sys.stderr)
        return 2
    for message in install_service():
        print(f"[a0-node] {message}")
    return 0


def command_uninstall_service(args: argparse.Namespace) -> int:
    for message in uninstall_service():
        print(f"[a0-node] {message}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="a0_node", description="Agent Zero device node"
    )
    parser.add_argument("--version", action="version", version=f"a0-node {NODE_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    join = subparsers.add_parser("join", help="pair this device with an Agent Zero hub")
    join.add_argument("--hub", required=True, help="Agent Zero base URL (https for remote hosts)")
    join.add_argument("--code", required=True, help="pairing code from device_manage")
    join.add_argument("--name", default="", help="display name for this device")
    join.add_argument(
        "--workspace",
        default="~/a0-workspace",
        help="writable workspace root (default: ~/a0-workspace)",
    )
    join.add_argument(
        "--add-root",
        action="append",
        default=[],
        help="additional allowed root; repeatable",
    )
    join.add_argument(
        "--enable-exec",
        action="store_true",
        help="allow remote command execution on this device",
    )
    join.add_argument(
        "--read-only",
        action="store_true",
        help="reject all write operations on this device",
    )
    join.add_argument(
        "--exec-timeout",
        type=int,
        default=0,
        help="default command timeout in seconds",
    )
    join.set_defaults(func=command_join)

    run = subparsers.add_parser("run", help="run the node in the foreground")
    run.set_defaults(func=command_run)

    status = subparsers.add_parser("status", help="show the local node configuration")
    status.set_defaults(func=command_status)

    install = subparsers.add_parser("install-service", help="install a background service")
    install.set_defaults(func=command_install_service)

    uninstall = subparsers.add_parser("uninstall-service", help="remove the background service")
    uninstall.set_defaults(func=command_uninstall_service)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
