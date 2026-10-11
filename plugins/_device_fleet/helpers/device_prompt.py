from __future__ import annotations

import time
from typing import Any

from plugins._device_fleet.helpers import hub as hub_module


def _format_age(last_seen: float, now: float) -> str:
    if not last_seen:
        return "never connected"
    delta = max(0.0, now - float(last_seen))
    if delta < 60:
        return "last seen just now"
    if delta < 3600:
        return f"last seen {int(delta // 60)}m ago"
    if delta < 86400:
        return f"last seen {int(delta // 3600)}h ago"
    return f"last seen {int(delta // 86400)}d ago"


def _runtime_label(runtime: dict[str, Any]) -> str:
    parts = [str(runtime.get("os") or ""), str(runtime.get("arch") or "")]
    label = " ".join(part for part in parts if part).strip()
    return label or "unknown os"


def device_lines(
    devices: list[dict[str, Any]],
    hub: hub_module.DeviceHub,
    *,
    show_offline: bool = True,
) -> list[str]:
    now = time.time()
    lines: list[str] = []
    for device in devices:
        device_id = str(device.get("id") or "")
        if not device_id:
            continue
        name = str(device.get("name") or "device")
        session = hub.get_session(device_id)
        online = bool(session) and not session.is_stale(now)
        if not online and not show_offline:
            continue
        runtime = session.runtime if session is not None else device.get("runtime") or {}
        tools = (
            list(session.tool_names)
            if session is not None
            else list(device.get("tools") or [])
        )
        status = "online" if online else f"offline, {_format_age(device.get('last_seen', 0.0), now)}"
        tools_label = ", ".join(tools) if tools else "capabilities unknown"
        lines.append(
            f"- {name} (id: {device_id}, {_runtime_label(runtime)}, {status}): {tools_label}"
        )
    return lines


def fleet_table(
    devices: list[dict[str, Any]],
    hub: hub_module.DeviceHub,
    *,
    show_offline: bool = True,
) -> str:
    lines = device_lines(devices, hub, show_offline=show_offline)
    if not lines:
        return "no devices are currently online"
    return "\n".join(lines)


def render_fleet_prompt(
    agent: Any,
    devices: list[dict[str, Any]],
    hub: hub_module.DeviceHub,
    *,
    show_offline: bool = True,
) -> str:
    table = fleet_table(devices, hub, show_offline=show_offline)
    try:
        return agent.read_prompt(
            "agent.system.device_fleet.md",
            device_table=table,
            device_count=len(devices),
            online_count=len(hub.online_ids()),
        ).strip()
    except Exception:
        return ""
