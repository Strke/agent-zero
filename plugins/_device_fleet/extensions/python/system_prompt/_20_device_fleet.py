from __future__ import annotations

from typing import Any

from helpers.extension import Extension
from helpers.print_style import PrintStyle


class DeviceFleetPrompt(Extension):
    async def execute(
        self,
        system_prompt: list[str] = [],
        **kwargs: Any,
    ) -> None:
        if not self.agent:
            return
        try:
            from plugins._device_fleet.helpers import device_prompt
            from plugins._device_fleet.helpers import hub as hub_module
            from plugins._device_fleet.helpers import registry as registry_module

            devices = registry_module.get_default().list_devices()
            if not devices:
                return
            show_offline = self._show_offline()
            prompt = device_prompt.render_fleet_prompt(
                self.agent,
                devices,
                hub_module.get_default(),
                show_offline=show_offline,
            )
            if prompt:
                system_prompt.append(prompt)
        except Exception as exc:
            PrintStyle.error(f"device fleet prompt: {exc}")

    def _show_offline(self) -> bool:
        try:
            from helpers import plugins

            config = plugins.get_plugin_config("_device_fleet", agent=self.agent) or {}
            return bool(config.get("prompt_show_offline_devices", True))
        except Exception:
            return True
