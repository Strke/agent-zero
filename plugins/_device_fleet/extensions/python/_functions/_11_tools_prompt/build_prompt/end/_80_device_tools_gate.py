from __future__ import annotations

import re
from typing import Any

from helpers.extension import Extension

GATED_PROMPTS = {
    "device_list": "agent.system.tool.device_list.md",
    "device_call": "agent.system.tool.device_call.md",
}


class DeviceToolsGate(Extension):
    def execute(self, data: dict[str, Any] = {}, **kwargs: Any) -> None:
        if self.agent is None:
            return
        if not isinstance(data, dict):
            return
        result = data.get("result")
        if not isinstance(result, str):
            return

        from plugins._device_fleet.helpers import registry as registry_module

        if registry_module.get_default().list_devices():
            return

        for prompt_file in GATED_PROMPTS.values():
            try:
                prompt = self.agent.read_prompt(prompt_file).strip()
            except Exception:
                continue
            if prompt:
                result = _remove_prompt(result, prompt)
        data["result"] = result


def _remove_prompt(result: str, prompt: str) -> str:
    if prompt not in result:
        return result
    return re.sub(r"\n{3,}", "\n\n", result.replace(prompt, "")).rstrip()
