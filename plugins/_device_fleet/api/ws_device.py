from __future__ import annotations

import threading
import time
from typing import Any

from helpers.print_style import PrintStyle
from helpers.ws import WsHandler

from plugins._device_fleet.helpers import hub as hub_module
from plugins._device_fleet.helpers import protocol
from plugins._device_fleet.helpers import registry as registry_module

HELLO_FAILURE_LIMIT = 5
HELLO_FAILURE_WINDOW_SECONDS = 300.0

_failure_lock = threading.Lock()
_hello_failures: dict[str, list[float]] = {}


class WsDevice(WsHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    @classmethod
    def requires_api_key(cls) -> bool:
        return False

    async def on_connect(self, sid: str) -> None:
        PrintStyle.debug(f"[device-fleet] /ws connected: {sid}")

    async def on_disconnect(self, sid: str) -> None:
        hub_module.get_default().disconnect(sid)
        with _failure_lock:
            _hello_failures.pop(sid, None)

    async def process(
        self, event: str, data: dict[str, Any], sid: str
    ) -> dict[str, Any] | None:
        hub = hub_module.get_default()
        if event == protocol.EVENT_HELLO:
            return self._process_hello(sid, data)
        if event == protocol.EVENT_HEARTBEAT:
            return {"ok": hub.heartbeat(sid)}
        if event == protocol.EVENT_TOOL_RESULT:
            return {"ok": hub.resolve_result(sid, data)}
        if event == protocol.EVENT_TOOL_ERROR:
            return {"ok": hub.resolve_error(sid, data)}
        return None

    def _process_hello(self, sid: str, data: dict[str, Any]) -> dict[str, Any]:
        if self._rate_limited(sid):
            return {
                "ok": False,
                "error": protocol.error_payload(
                    "RATE_LIMITED",
                    "too many rejected hello attempts from this connection",
                    category=protocol.ERROR_AUTH,
                ),
            }

        hello, error = protocol.normalize_hello(data)
        if error is not None:
            return self._hello_failure(sid, error)

        device = registry_module.get_default().get(hello["device"]["id"])
        if (
            device is None
            or not device.get("enabled", True)
            or not registry_module.get_default().verify_token(
                device["id"], hello["device"]["token"]
            )
        ):
            return self._hello_failure(
                sid,
                protocol.error_payload(
                    "AUTH_FAILED",
                    "device token is unknown, revoked, or disabled",
                    category=protocol.ERROR_AUTH,
                ),
            )

        heartbeat = self._heartbeat_seconds()
        try:
            ready = hub_module.get_default().register_hello(
                sid,
                device=device,
                hello=hello,
                heartbeat_seconds=heartbeat,
            )
        except hub_module.HelloError as exc:
            return self._hello_failure(sid, exc.payload())

        registry_module.get_default().touch(
            device["id"],
            runtime=hello["runtime"],
            tools=hello["tool_names"],
            policy=hello["policy"],
        )
        with _failure_lock:
            _hello_failures.pop(sid, None)
        PrintStyle.debug(
            f"[device-fleet] device connected: {device.get('name')} ({device.get('id')})"
        )
        return {"ok": True, "data": ready}

    def _hello_failure(self, sid: str, error: dict[str, Any]) -> dict[str, Any]:
        with _failure_lock:
            now = time.time()
            attempts = [
                ts
                for ts in _hello_failures.get(sid, [])
                if now - ts <= HELLO_FAILURE_WINDOW_SECONDS
            ]
            attempts.append(now)
            _hello_failures[sid] = attempts
        PrintStyle.warning(
            f"[device-fleet] rejected device hello from {sid}: {error.get('code')}"
        )
        return {"ok": False, "error": error}

    def _rate_limited(self, sid: str) -> bool:
        with _failure_lock:
            now = time.time()
            attempts = [
                ts
                for ts in _hello_failures.get(sid, [])
                if now - ts <= HELLO_FAILURE_WINDOW_SECONDS
            ]
            if attempts:
                _hello_failures[sid] = attempts
            return len(attempts) >= HELLO_FAILURE_LIMIT

    def _heartbeat_seconds(self) -> int:
        try:
            from helpers import plugins

            config = plugins.get_plugin_config("_device_fleet") or {}
            value = int(config.get("heartbeat_seconds", 30))
        except Exception:
            value = 30
        return max(5, min(300, value))
