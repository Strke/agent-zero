import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import socketio
import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

NODE_ROOT = PROJECT_ROOT / "plugins" / "_device_fleet" / "node"
if str(NODE_ROOT) not in sys.path:
    sys.path.insert(0, str(NODE_ROOT))

from flask import Flask

from helpers import ws as ws_helpers
from plugins._device_fleet.helpers import hub as hub_module
from plugins._device_fleet.helpers import registry as registry_module
from plugins._device_fleet.helpers.hub import DeviceHub
from plugins._device_fleet.helpers.registry import DeviceRegistry

from a0_node.client import A0NodeClient


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _ServerHarness:
    def __init__(self, port: int | None = None) -> None:
        self.port = port or _free_port()
        self.app = Flask("device-fleet-e2e")
        self.sio = socketio.AsyncServer(async_mode="asgi")
        self.lock = threading.RLock()
        ws_helpers.register_ws_namespace(self.sio, self.app, self.lock, manager=None)
        self._uvicorn = uvicorn.Server(
            uvicorn.Config(
                socketio.ASGIApp(self.sio),
                host="127.0.0.1",
                port=self.port,
                log_level="error",
                lifespan="off",
            )
        )
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._uvicorn.serve())
            loop.run_until_complete(self.sio.shutdown())
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()

    def start(self) -> int:
        self.thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    return self.port
            time.sleep(0.05)
        raise RuntimeError("uvicorn test server did not start")

    def stop(self) -> None:
        self._uvicorn.should_exit = True
        self.thread.join(timeout=10)

    def install_emitter(self, hub: DeviceHub) -> None:
        async def emit(sid, event, data):
            envelope = {
                "handlerId": "plugins._device_fleet.api.ws_device.WsDevice",
                "data": data,
            }
            await self.sio.emit(event, envelope, to=sid, namespace="/ws")

        hub.set_emitter(emit)


@pytest.fixture(autouse=True)
def _clean_defaults():
    yield
    registry_module.set_default(None)
    hub_module.set_default(None)


def _wait_online(hub: DeviceHub, device_id: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if hub.is_online(device_id):
            return True
        time.sleep(0.1)
    return False


def test_node_client_end_to_end(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path / "devices")
    device, token = registry.create_device(name="e2e-node")
    registry_module.set_default(registry)

    hub = DeviceHub()
    hub_module.set_default(hub)

    harness = _ServerHarness()
    harness.install_emitter(hub)
    port = harness.start()

    client = A0NodeClient(
        {
            "version": 1,
            "hub_url": f"http://127.0.0.1:{port}",
            "device_id": device["id"],
            "device_token": token,
            "name": "e2e-node",
            "policy": {"roots": [str(tmp_path)], "exec_enabled": False},
        }
    )
    client_thread = threading.Thread(target=client.run_forever, daemon=True)
    client_thread.start()

    try:
        assert _wait_online(hub, device["id"]), "node never authenticated"

        result = asyncio.run(
            hub.invoke(device["id"], "system_info", {}, timeout_seconds=10)
        )
        assert result["ok"] is True
        assert result["result"]["node_version"]
        assert str(tmp_path) in result["result"]["roots"]

        (tmp_path / "hello.txt").write_text("from hub", encoding="utf-8")
        read = asyncio.run(
            hub.invoke(
                device["id"],
                "read_file",
                {"path": str(tmp_path / "hello.txt")},
                timeout_seconds=10,
            )
        )
        assert read["ok"] is True
        assert read["result"]["content"] == "from hub"

        disabled = asyncio.run(
            hub.invoke(
                device["id"], "exec_command", {"command": "echo hi"}, timeout_seconds=10
            )
        )
        assert disabled["ok"] is False
        assert disabled["error"]["code"] == "EXEC_DISABLED"
    finally:
        client.request_stop()
        client_thread.join(timeout=5)
        harness.stop()
        assert not _wait_online(hub, device["id"], timeout=3)


def test_node_reconnects_after_hub_restart(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path / "devices")
    device, token = registry.create_device(name="restart-node")
    registry_module.set_default(registry)

    hub = DeviceHub()
    hub_module.set_default(hub)

    harness = _ServerHarness()
    harness.install_emitter(hub)
    port = harness.start()
    restarted: _ServerHarness | None = None

    client = A0NodeClient(
        {
            "version": 1,
            "hub_url": f"http://127.0.0.1:{port}",
            "device_id": device["id"],
            "device_token": token,
            "name": "restart-node",
            "policy": {"roots": [str(tmp_path)], "exec_enabled": False},
        }
    )
    client_thread = threading.Thread(target=client.run_forever, daemon=True)
    client_thread.start()

    try:
        assert _wait_online(hub, device["id"]), "node never authenticated"
        first_sid = hub.get_session(device["id"]).sid

        harness.stop()
        restarted = _ServerHarness(port=port)
        restarted.install_emitter(hub)
        restarted.start()

        deadline = time.time() + 45
        while time.time() < deadline:
            session = hub.get_session(device["id"])
            if (
                session is not None
                and session.sid != first_sid
                and hub.is_online(device["id"])
            ):
                break
            time.sleep(0.2)
        else:
            raise AssertionError("node did not reconnect after hub restart")

        result = asyncio.run(
            hub.invoke(device["id"], "system_info", {}, timeout_seconds=10)
        )
        assert result["ok"] is True
        assert result["result"]["node_version"]
    finally:
        client.request_stop()
        client_thread.join(timeout=5)
        if restarted is not None:
            restarted.stop()
        if harness.thread.is_alive():
            harness.stop()
