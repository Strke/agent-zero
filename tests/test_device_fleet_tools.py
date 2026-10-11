import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._device_fleet.helpers import protocol
from plugins._device_fleet.helpers import hub as hub_module
from plugins._device_fleet.helpers import pairing as pairing_module
from plugins._device_fleet.helpers import registry as registry_module
from plugins._device_fleet.helpers.hub import DeviceHub
from plugins._device_fleet.helpers.pairing import PairingStore
from plugins._device_fleet.helpers.registry import DeviceRegistry
from plugins._device_fleet.tools.device_call import DeviceCall
from plugins._device_fleet.tools.device_list import DeviceList
from plugins._device_fleet.tools.device_manage import DeviceManage


class _FakeAgent:
    def __init__(self) -> None:
        self.context = SimpleNamespace(id="test-context")

    def read_prompt(self, file: str, **kwargs) -> str:
        return ""


@pytest.fixture(autouse=True)
def _clean_defaults():
    yield
    registry_module.set_default(None)
    hub_module.set_default(None)
    pairing_module.set_default(None)


def _tool(cls, args):
    return cls(
        agent=_FakeAgent(),
        name=cls.__name__,
        method=None,
        args=args,
        message="",
        loop_data=None,
    )


def _hello(device_id: str, name: str):
    raw = {
        "protocol_version": protocol.PROTOCOL_VERSION,
        "device": {"id": device_id, "name": name, "token": "t" * 32},
        "runtime": {"os": "linux", "arch": "x86_64"},
        "tools": [{"name": "system_info", "title": "info", "description": "info"}],
        "policy": {"exec_enabled": False},
    }
    hello, error = protocol.normalize_hello(raw)
    assert error is None
    return hello


@pytest.mark.asyncio
async def test_device_list_empty(tmp_path: Path) -> None:
    registry_module.set_default(DeviceRegistry(tmp_path))
    response = await _tool(DeviceList, {}).execute()
    assert "No devices are paired" in response.message


@pytest.mark.asyncio
async def test_device_list_with_device(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, _ = registry.create_device(name="server-01")
    registry_module.set_default(registry)
    hub_module.set_default(DeviceHub())

    response = await _tool(DeviceList, {}).execute()
    assert "server-01" in response.message
    assert device["id"] in response.message
    assert "offline" in response.message


@pytest.mark.asyncio
async def test_device_manage_pair_start(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    registry_module.set_default(registry)
    pairing_module.set_default(PairingStore(registry))

    response = await _tool(DeviceManage, {"action": "pair_start", "name": "srv"}).execute()
    assert "Pairing code:" in response.message
    assert "a0_node join" in response.message
    assert "--name \"srv\"" in response.message


@pytest.mark.asyncio
async def test_device_manage_remove(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, _ = registry.create_device(name="box")
    registry_module.set_default(registry)
    hub_module.set_default(DeviceHub())

    response = await _tool(DeviceManage, {"action": "remove", "device": "box"}).execute()
    assert "removed" in response.message
    assert registry.get(device["id"]) is None


@pytest.mark.asyncio
async def test_device_manage_unknown_action() -> None:
    response = await _tool(DeviceManage, {"action": "explode"}).execute()
    assert "unknown action" in response.message


@pytest.mark.asyncio
async def test_device_call_roundtrip(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, _ = registry.create_device(name="server-01")
    registry_module.set_default(registry)

    emitter_events: list[tuple[str, str, dict]] = []

    async def emitter(sid, event, data):
        emitter_events.append((sid, event, data))

    hub = DeviceHub()
    hub.set_emitter(emitter)
    hub.register_hello(
        "sid1",
        device=device,
        hello=_hello(device["id"], "server-01"),
        heartbeat_seconds=30,
    )
    hub_module.set_default(hub)

    tool = _tool(
        DeviceCall,
        {"device": "server-01", "tool": "system_info", "arguments": {}},
    )
    task = asyncio.create_task(tool.execute())
    await asyncio.sleep(0.05)
    request_id = emitter_events[-1][2]["request_id"]
    hub.resolve_result("sid1", {"request_id": request_id, "result": {"hostname": "srv"}})

    response = await task
    assert "[server-01] system_info" in response.message
    assert "hostname" in response.message


@pytest.mark.asyncio
async def test_device_call_unknown_device(tmp_path: Path) -> None:
    registry_module.set_default(DeviceRegistry(tmp_path))
    hub_module.set_default(DeviceHub())
    response = await _tool(
        DeviceCall, {"device": "ghost", "tool": "system_info"}
    ).execute()
    assert "unknown device" in response.message


@pytest.mark.asyncio
async def test_device_call_invalid_arguments_json(tmp_path: Path) -> None:
    registry_module.set_default(DeviceRegistry(tmp_path))
    hub_module.set_default(DeviceHub())
    response = await _tool(
        DeviceCall,
        {"device": "x", "tool": "system_info", "arguments": "{not json"},
    ).execute()
    assert "not valid JSON" in response.message


@pytest.mark.asyncio
async def test_device_call_offline_device(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    registry.create_device(name="box")
    registry_module.set_default(registry)
    hub_module.set_default(DeviceHub())

    response = await _tool(
        DeviceCall, {"device": "box", "tool": "system_info"}
    ).execute()
    assert "DEVICE_OFFLINE" in response.message
