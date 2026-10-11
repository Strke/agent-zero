from __future__ import annotations

import json
from typing import Any

from helpers.tool import Response, Tool

from plugins._device_fleet.helpers import hub as hub_module
from plugins._device_fleet.helpers import registry as registry_module


class DeviceCall(Tool):
    async def execute(self, **kwargs: Any) -> Response:
        device_ref = str(self.args.get("device") or "").strip()
        tool_name = str(self.args.get("tool") or "").strip()
        if not device_ref:
            return self._fail("device is required; use device_list to see paired devices")
        if not tool_name:
            return self._fail("tool is required; use the tool names from device_list")

        arguments, error = self._parse_arguments(self.args.get("arguments"))
        if error:
            return self._fail(error)

        registry = registry_module.get_default()
        device, error = self._resolve_device(registry, device_ref)
        if error:
            return self._fail(error)

        timeout, error = self._parse_timeout(self.args.get("timeout_seconds"))
        if error:
            return self._fail(error)

        config = self._config()
        max_concurrent = int(config.get("max_concurrent_invocations_per_device", 4) or 4)
        result = await hub_module.get_default().invoke(
            device["id"],
            tool_name,
            arguments,
            timeout_seconds=timeout,
            max_concurrent=max_concurrent,
        )

        if not result.get("ok"):
            return self._render_error(device, tool_name, result.get("error") or {})

        rendered = self._render_result(
            device, tool_name, result.get("result"), int(config.get("result_max_chars", 60000) or 60000)
        )
        return Response(message=rendered, break_loop=False)

    def _parse_arguments(
        self, value: Any
    ) -> tuple[dict[str, Any] | None, str | None]:
        if value is None:
            return {}, None
        if isinstance(value, dict):
            return value, None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}, None
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                return None, f"arguments is not valid JSON: {exc}"
            if isinstance(parsed, dict):
                return parsed, None
            return None, "arguments must be a JSON object"
        return None, "arguments must be a JSON object"

    def _resolve_device(
        self, registry: registry_module.DeviceRegistry, reference: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        device = registry.get(reference)
        if device is not None:
            if not device.get("enabled", True):
                return None, (
                    f"device {device.get('name')!r} is disabled; enable it with "
                    "device_manage action=enable"
                )
            return device, None
        matches = registry.name_matches(reference)
        if len(matches) > 1:
            return None, (
                f"multiple devices are named {reference!r}: {', '.join(matches)}; "
                "use the device id instead"
            )
        return None, (
            f"unknown device {reference!r}; call device_list to see paired devices"
        )

    def _parse_timeout(self, value: Any) -> tuple[float, str | None]:
        config = self._config()
        default = float(config.get("tool_timeout_seconds", 120) or 120)
        maximum = float(config.get("max_tool_timeout_seconds", 600) or 600)
        if value is None or value == "":
            return default, None
        try:
            requested = float(value)
        except (TypeError, ValueError):
            return default, f"timeout_seconds must be a number, got {value!r}"
        if requested <= 0:
            return default, "timeout_seconds must be positive"
        return min(requested, maximum), None

    def _config(self) -> dict[str, Any]:
        try:
            from helpers import plugins

            return plugins.get_plugin_config("_device_fleet", agent=self.agent) or {}
        except Exception:
            return {}

    def _render_result(
        self, device: dict[str, Any], tool_name: str, result: Any, max_chars: int
    ) -> str:
        header = f"[{device.get('name')}] {tool_name}"
        try:
            body = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            body = str(result)
        truncated = ""
        if len(body) > max_chars:
            body = body[:max_chars]
            truncated = f"\n[result truncated at {max_chars} characters]"
        return f"{header}\n{body}{truncated}"

    def _render_error(
        self, device: dict[str, Any], tool_name: str, error: dict[str, Any]
    ) -> Response:
        code = str(error.get("code") or "TOOL_FAILED")
        message = str(error.get("message") or "device tool failed")
        details = error.get("details")
        suffix = ""
        if isinstance(details, dict) and details.get("available_tools"):
            suffix = (
                "; available tools: " + ", ".join(str(t) for t in details["available_tools"])
            )
        return Response(
            message=(
                f"[{device.get('name')}] {tool_name} failed ({code}): {message}{suffix}"
            ),
            break_loop=False,
        )

    def _fail(self, message: str) -> Response:
        return Response(message=f"device_call: {message}", break_loop=False)
