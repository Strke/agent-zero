from __future__ import annotations

from typing import Any

from helpers.tool import Response, Tool

from plugins._device_fleet.helpers import hub as hub_module
from plugins._device_fleet.helpers import pairing as pairing_module
from plugins._device_fleet.helpers import registry as registry_module


class DeviceManage(Tool):
    async def execute(self, **kwargs: Any) -> Response:
        action = str(self.args.get("action") or "").strip().lower()
        if not action:
            return self._fail("action is required (pair_start, remove, enable, disable, list)")
        handlers = {
            "pair_start": self._pair_start,
            "remove": self._remove,
            "enable": lambda: self._set_enabled(True),
            "disable": lambda: self._set_enabled(False),
            "list": self._list,
        }
        handler = handlers.get(action)
        if handler is None:
            return self._fail(
                f"unknown action {action!r}; use pair_start, remove, enable, disable, or list"
            )
        return handler()

    def _pair_start(self) -> Response:
        registry = registry_module.get_default()
        config = self._config()
        max_devices = int(config.get("max_devices", 32) or 32)
        if registry.count() >= max_devices:
            return self._fail(
                f"device limit reached ({max_devices}); remove a device before pairing a new one"
            )

        ttl_seconds = int(config.get("pair_code_ttl_seconds", 600) or 600)
        max_attempts = int(config.get("pair_code_max_attempts", 5) or 5)
        store = pairing_module.get_default()
        store.ttl_seconds = ttl_seconds
        store.max_attempts = max_attempts
        store.max_devices = max_devices

        try:
            entry = store.create_code()
        except pairing_module.PairingError as exc:
            return self._fail(exc.message)

        name_hint = str(self.args.get("name") or "").strip()
        name_part = f' --name "{name_hint}"' if name_hint else ""
        hub_hint = str(self.args.get("hub_url") or "").strip()
        hub_url = hub_hint or "<AGENT ZERO URL>"
        minutes = max(1, ttl_seconds // 60)
        message = (
            f"Pairing code: {entry.code} (single-use, valid for {minutes} minutes)\n\n"
            "On the target device, install the node agent (copy the node/ folder from "
            "plugins/_device_fleet or use the setup-device-node skill) and run:\n"
            f"  python -m a0_node join --hub {hub_url} --code {entry.code}{name_part}\n\n"
            "Notes:\n"
            "- replace <AGENT ZERO URL> with the address the device can reach "
            "(same network address or the public Tunnel URL)\n"
            "- add --enable-exec to allow remote commands on that device, and "
            "--workspace PATH to change the writable root\n"
            "- the device appears in device_list after its node connects"
        )
        return Response(message=message, break_loop=False)

    def _remove(self) -> Response:
        device, error = self._resolve_device()
        if error:
            return self._fail(error)
        device_id = device["id"]
        hub_module.get_default().disconnect_device(device_id)
        registry_module.get_default().remove(device_id)
        return Response(
            message=(
                f"Device {device.get('name')!r} ({device_id}) was removed and disconnected. "
                "Its token is revoked; pair it again to restore access."
            ),
            break_loop=False,
        )

    def _set_enabled(self, enabled: bool) -> Response:
        device, error = self._resolve_device()
        if error:
            return self._fail(error)
        device_id = device["id"]
        if not registry_module.get_default().set_enabled(device_id, enabled):
            return self._fail(f"could not update device {device_id}")
        if not enabled:
            hub_module.get_default().disconnect_device(device_id)
        state = "enabled" if enabled else "disabled"
        return Response(
            message=f"Device {device.get('name')!r} ({device_id}) is now {state}.",
            break_loop=False,
        )

    def _list(self) -> Response:
        from plugins._device_fleet.helpers import device_prompt

        registry = registry_module.get_default()
        devices = registry.list_devices()
        if not devices:
            return Response(
                message=(
                    "No devices are paired with this Agent Zero instance yet. "
                    "Use action=pair_start to create a pairing code."
                ),
                break_loop=False,
            )
        hub = hub_module.get_default()
        table = device_prompt.fleet_table(devices, hub)
        return Response(
            message=f"Paired devices: {len(devices)} total.\n{table}",
            break_loop=False,
        )

    def _resolve_device(self) -> tuple[dict[str, Any] | None, str | None]:
        reference = str(self.args.get("device") or "").strip()
        if not reference:
            return None, "device is required (id or exact name from device_list)"
        registry = registry_module.get_default()
        device = registry.get(reference)
        if device is not None:
            return device, None
        matches = registry.name_matches(reference)
        if len(matches) > 1:
            return None, (
                f"multiple devices are named {reference!r}: {', '.join(matches)}; "
                "use the device id instead"
            )
        return None, f"unknown device {reference!r}; call device_list first"

    def _config(self) -> dict[str, Any]:
        try:
            from helpers import plugins

            return plugins.get_plugin_config("_device_fleet", agent=self.agent) or {}
        except Exception:
            return {}

    def _fail(self, message: str) -> Response:
        return Response(message=f"device_manage: {message}", break_loop=False)
