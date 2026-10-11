import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._device_fleet.api import ws_device as ws_device_module
from plugins._device_fleet.api.ws_device import WsDevice
from plugins._device_fleet.helpers import protocol
from plugins._device_fleet.helpers import hub as hub_module
from plugins._device_fleet.helpers import registry as registry_module
from plugins._device_fleet.helpers.registry import DeviceRegistry


class _FakeSocketIO:
    async def emit(self, *args, **kwargs):
        return None

    async def disconnect(self, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def _clean_defaults():
    ws_device_module._hello_failures.clear()
    yield
    ws_device_module._hello_failures.clear()
    registry_module.set_default(None)
    hub_module.set_default(None)


def _handler() -> WsDevice:
    return WsDevice(_FakeSocketIO(), threading.RLock())


def _hello_payload(device_id: str, token: str) -> dict:
    return {
        "protocol_version": protocol.PROTOCOL_VERSION,
        "device": {"id": device_id, "name": "server-01", "token": token},
        "runtime": {"os": "linux"},
        "tools": [{"name": "system_info", "title": "info", "description": "info"}],
        "policy": {},
    }


@pytest.mark.asyncio
async def test_hello_accepts_valid_token(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, token = registry.create_device(name="server-01")
    registry_module.set_default(registry)
    hub_module.set_default(hub_module.DeviceHub())

    ack = await _handler().process(
        protocol.EVENT_HELLO, _hello_payload(device["id"], token), "sid1"
    )
    assert ack["ok"] is True
    assert ack["data"]["heartbeat_seconds"] >= 5
    assert hub_module.get_default().is_online(device["id"])
    assert registry.get(device["id"])["tools"] == ["system_info"]


@pytest.mark.asyncio
async def test_hello_rejects_bad_token(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, _ = registry.create_device(name="server-01")
    registry_module.set_default(registry)
    hub_module.set_default(hub_module.DeviceHub())

    ack = await _handler().process(
        protocol.EVENT_HELLO, _hello_payload(device["id"], "x" * 32), "sid1"
    )
    assert ack["ok"] is False
    assert ack["error"]["code"] == "AUTH_FAILED"
    assert not hub_module.get_default().is_online(device["id"])


@pytest.mark.asyncio
async def test_hello_rejects_disabled_device(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, token = registry.create_device(name="server-01")
    registry.set_enabled(device["id"], False)
    registry_module.set_default(registry)
    hub_module.set_default(hub_module.DeviceHub())

    ack = await _handler().process(
        protocol.EVENT_HELLO, _hello_payload(device["id"], token), "sid1"
    )
    assert ack["ok"] is False
    assert ack["error"]["code"] == "AUTH_FAILED"


@pytest.mark.asyncio
async def test_heartbeat_and_results_require_session(tmp_path: Path) -> None:
    registry_module.set_default(DeviceRegistry(tmp_path))
    hub_module.set_default(hub_module.DeviceHub())
    handler = _handler()

    assert await handler.process(protocol.EVENT_HEARTBEAT, {}, "sid1") == {"ok": False}
    assert await handler.process(
        protocol.EVENT_TOOL_RESULT, {"request_id": "abc"}, "sid1"
    ) == {"ok": False}
    assert await handler.process("webui_something", {}, "sid1") is None


@pytest.mark.asyncio
async def test_hello_is_rate_limited_after_failures(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, _ = registry.create_device(name="server-01")
    registry_module.set_default(registry)
    hub_module.set_default(hub_module.DeviceHub())
    handler = _handler()

    payload = _hello_payload(device["id"], "x" * 32)
    for _ in range(5):
        ack = await handler.process(protocol.EVENT_HELLO, payload, "sid1")
        assert ack["ok"] is False
    ack = await handler.process(protocol.EVENT_HELLO, payload, "sid1")
    assert ack["ok"] is False
    assert ack["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_disconnect_clears_session(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, token = registry.create_device(name="server-01")
    registry_module.set_default(registry)
    hub_module.set_default(hub_module.DeviceHub())
    handler = _handler()

    await handler.process(
        protocol.EVENT_HELLO, _hello_payload(device["id"], token), "sid1"
    )
    assert hub_module.get_default().is_online(device["id"])

    await handler.on_disconnect("sid1")
    assert not hub_module.get_default().is_online(device["id"])
    assert hub_module.get_default().session_for_sid("sid1") is None
