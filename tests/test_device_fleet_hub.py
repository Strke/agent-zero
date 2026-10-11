import asyncio
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._device_fleet.helpers import protocol
from plugins._device_fleet.helpers.hub import DeviceHub


def _hello():
    raw = {
        "protocol_version": protocol.PROTOCOL_VERSION,
        "device": {"id": "device_abc", "name": "server-01", "token": "t" * 32},
        "runtime": {"os": "linux"},
        "tools": [{"name": "system_info", "title": "info", "description": "info"}],
        "policy": {},
    }
    hello, error = protocol.normalize_hello(raw)
    assert error is None
    return hello


class _Emitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    async def __call__(self, sid: str, event: str, data: dict) -> None:
        self.events.append((sid, event, data))


def _hub_with_session(emitter: _Emitter) -> DeviceHub:
    hub = DeviceHub()
    hub.set_emitter(emitter)
    hub.register_hello(
        "sid1",
        device={"id": "device_abc", "name": "server-01"},
        hello=_hello(),
        heartbeat_seconds=30,
    )
    return hub


@pytest.mark.asyncio
async def test_invoke_roundtrip() -> None:
    emitter = _Emitter()
    hub = _hub_with_session(emitter)

    task = asyncio.create_task(
        hub.invoke("device_abc", "system_info", {}, timeout_seconds=2)
    )
    await asyncio.sleep(0.05)
    assert emitter.events
    sid, event, data = emitter.events[-1]
    assert sid == "sid1"
    assert event == protocol.EVENT_TOOL_INVOKE
    assert data["tool"] == "system_info"

    assert hub.resolve_result(
        "sid1", {"request_id": data["request_id"], "result": {"hostname": "srv"}}
    )
    result = await task
    assert result == {"ok": True, "result": {"hostname": "srv"}}


@pytest.mark.asyncio
async def test_invoke_offline_device() -> None:
    hub = DeviceHub()
    result = await hub.invoke("device_missing", "system_info", {}, timeout_seconds=1)
    assert result["ok"] is False
    assert result["error"]["code"] == "DEVICE_OFFLINE"
    assert result["error"]["category"] == protocol.ERROR_OFFLINE


@pytest.mark.asyncio
async def test_invoke_unknown_tool() -> None:
    emitter = _Emitter()
    hub = _hub_with_session(emitter)
    result = await hub.invoke("device_abc", "rm_rf", {}, timeout_seconds=1)
    assert result["ok"] is False
    assert result["error"]["code"] == "UNKNOWN_TOOL"
    assert result["error"]["details"]["available_tools"] == ["system_info"]


@pytest.mark.asyncio
async def test_invoke_error_resolution() -> None:
    emitter = _Emitter()
    hub = _hub_with_session(emitter)
    task = asyncio.create_task(
        hub.invoke("device_abc", "system_info", {}, timeout_seconds=2)
    )
    await asyncio.sleep(0.05)
    request_id = emitter.events[-1][2]["request_id"]

    assert hub.resolve_error(
        "sid1",
        {
            "request_id": request_id,
            "error": {"code": "EXEC_DISABLED", "message": "no", "category": "permission"},
        },
    )
    result = await task
    assert result["ok"] is False
    assert result["error"]["code"] == "EXEC_DISABLED"


@pytest.mark.asyncio
async def test_invoke_timeout_emits_cancel() -> None:
    emitter = _Emitter()
    hub = _hub_with_session(emitter)
    result = await hub.invoke("device_abc", "system_info", {}, timeout_seconds=0.01)
    assert result["ok"] is False
    assert result["error"]["code"] == "TIMEOUT"
    assert any(event == protocol.EVENT_TOOL_CANCEL for _, event, _ in emitter.events)


@pytest.mark.asyncio
async def test_disconnect_fails_pending_invocations() -> None:
    emitter = _Emitter()
    hub = _hub_with_session(emitter)
    task = asyncio.create_task(
        hub.invoke("device_abc", "system_info", {}, timeout_seconds=5)
    )
    await asyncio.sleep(0.05)

    hub.disconnect("sid1")
    result = await task
    assert result["ok"] is False
    assert result["error"]["code"] == "DEVICE_OFFLINE"


def test_reconnect_replaces_session() -> None:
    emitter = _Emitter()
    hub = _hub_with_session(emitter)
    hub.register_hello(
        "sid2",
        device={"id": "device_abc", "name": "server-01"},
        hello=_hello(),
        heartbeat_seconds=30,
    )
    assert hub.get_session("device_abc").sid == "sid2"
    assert hub.session_for_sid("sid1") is None


def test_stale_sessions_are_not_online() -> None:
    emitter = _Emitter()
    hub = _hub_with_session(emitter)
    session = hub.get_session("device_abc")
    session.last_heartbeat -= 3600
    assert not hub.is_online("device_abc")
    assert hub.online_ids() == set()


def test_heartbeat_updates_session() -> None:
    emitter = _Emitter()
    hub = _hub_with_session(emitter)
    session = hub.get_session("device_abc")
    session.last_heartbeat -= 3600
    assert hub.heartbeat("sid1")
    assert hub.is_online("device_abc")
    assert not hub.heartbeat("unknown-sid")
