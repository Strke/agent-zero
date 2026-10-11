from __future__ import annotations

import json
import time
from typing import Any

PROTOCOL_VERSION = 1
NAMESPACE = "/ws"
HANDLER_ID = "plugins._device_fleet.api.ws_device.WsDevice"
HANDLER_PATH = "plugins/_device_fleet/ws_device"
HUB_NAME = "Agent Zero"

MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_HELLO_TOOLS_BYTES = 512 * 1024
MAX_NAME_LENGTH = 64
MAX_TOOL_NAME_LENGTH = 64
MAX_TOOLS = 64
MAX_STRING_LENGTH = 512

EVENT_HELLO = "node_hello"
EVENT_READY = "node_ready"
EVENT_HEARTBEAT = "node_heartbeat"
EVENT_TOOL_INVOKE = "node_tool_invoke"
EVENT_TOOL_CANCEL = "node_tool_cancel"
EVENT_TOOL_RESULT = "node_tool_result"
EVENT_TOOL_ERROR = "node_tool_error"

ERROR_VALIDATION = "validation"
ERROR_PERMISSION = "permission"
ERROR_EXECUTION = "execution"
ERROR_INTERNAL = "internal"
ERROR_TIMEOUT = "timeout"
ERROR_OFFLINE = "offline"
ERROR_BUSY = "busy"
ERROR_AUTH = "auth"
ERROR_PROTOCOL = "protocol"

RUNTIME_FIELDS = ("os", "arch", "hostname", "user", "python", "node_version")


def timestamp() -> float:
    return time.time()


def error_payload(
    code: str,
    message: str,
    *,
    category: str = ERROR_INTERNAL,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "category": category,
        "retryable": retryable,
    }
    if details:
        payload["details"] = details
    return payload


def _clean_scalar(value: Any, limit: int = MAX_STRING_LENGTH) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def normalize_hello(raw: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(raw, dict):
        return None, error_payload(
            "INVALID_HELLO", "hello payload must be an object", category=ERROR_VALIDATION
        )

    try:
        version = int(raw.get("protocol_version"))
    except (TypeError, ValueError):
        version = None
    if version != PROTOCOL_VERSION:
        return None, error_payload(
            "PROTOCOL_MISMATCH",
            f"unsupported protocol version: {raw.get('protocol_version')!r}; hub expects {PROTOCOL_VERSION}",
            category=ERROR_PROTOCOL,
        )

    device = raw.get("device")
    if not isinstance(device, dict):
        return None, error_payload(
            "INVALID_HELLO", "device object is required", category=ERROR_VALIDATION
        )

    device_id = _clean_scalar(device.get("id"), 96)
    token = device.get("token")
    if not isinstance(token, str) or not 16 <= len(token) <= 256:
        return None, error_payload(
            "INVALID_HELLO",
            "device token is missing or malformed",
            category=ERROR_VALIDATION,
        )

    name = _clean_scalar(device.get("name"), MAX_NAME_LENGTH) or "device"

    runtime: dict[str, str] = {}
    runtime_raw = raw.get("runtime")
    if isinstance(runtime_raw, dict):
        for key in RUNTIME_FIELDS:
            value = _clean_scalar(runtime_raw.get(key), MAX_STRING_LENGTH)
            if value:
                runtime[key] = value

    tools: list[dict[str, Any]] = []
    total_bytes = 0
    raw_tools = raw.get("tools")
    if isinstance(raw_tools, list):
        for item in raw_tools:
            if not isinstance(item, dict):
                continue
            tool_name = _clean_scalar(item.get("name"), MAX_TOOL_NAME_LENGTH)
            if not tool_name:
                continue
            descriptor: dict[str, Any] = {
                "name": tool_name,
                "title": _clean_scalar(item.get("title"), 120),
                "description": _clean_scalar(item.get("description"), 2000),
            }
            schema = item.get("input_schema")
            if isinstance(schema, dict):
                descriptor["input_schema"] = schema
            total_bytes += len(json.dumps(descriptor, ensure_ascii=False))
            if total_bytes > MAX_HELLO_TOOLS_BYTES:
                return None, error_payload(
                    "HELLO_TOO_LARGE",
                    "tool descriptors exceed the hello size limit",
                    category=ERROR_VALIDATION,
                )
            tools.append(descriptor)
            if len(tools) >= MAX_TOOLS:
                break

    policy: dict[str, Any] = {}
    policy_raw = raw.get("policy")
    if isinstance(policy_raw, dict):
        policy["exec_enabled"] = bool(policy_raw.get("exec_enabled"))
        policy["read_only"] = bool(policy_raw.get("read_only"))
        roots_raw = policy_raw.get("roots")
        if isinstance(roots_raw, list):
            roots = []
            for entry in roots_raw[:16]:
                cleaned = _clean_scalar(entry, 512)
                if cleaned:
                    roots.append(cleaned)
            policy["roots"] = roots

    hello = {
        "protocol_version": version,
        "device": {"id": device_id, "name": name, "token": token},
        "runtime": runtime,
        "tools": tools,
        "tool_names": [descriptor["name"] for descriptor in tools],
        "policy": policy,
    }
    return hello, None
