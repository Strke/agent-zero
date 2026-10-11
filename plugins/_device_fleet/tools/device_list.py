from __future__ import annotations

from typing import Any

from helpers.tool import Response, Tool

from plugins._device_fleet.helpers import device_prompt
from plugins._device_fleet.helpers import hub as hub_module
from plugins._device_fleet.helpers import registry as registry_module


class DeviceList(Tool):
    async def execute(self, **kwargs: Any) -> Response:
        registry = registry_module.get_default()
        devices = registry.list_devices()
        if not devices:
            return Response(
                message=(
                    "No devices are paired with this Agent Zero instance yet. "
                    "Use device_manage with action=pair_start to create a pairing code "
                    "for a new device."
                ),
                break_loop=False,
            )

        hub = hub_module.get_default()
        show_offline = self._show_offline()
        online = len(hub.online_ids())
        table = device_prompt.fleet_table(devices, hub, show_offline=show_offline)
        return Response(
            message=(
                f"Paired devices: {len(devices)} total, {online} online.\n{table}"
            ),
            break_loop=False,
        )

    def _show_offline(self) -> bool:
        try:
            from helpers import plugins

            config = plugins.get_plugin_config("_device_fleet", agent=self.agent) or {}
            return bool(config.get("prompt_show_offline_devices", True))
        except Exception:
            return True
