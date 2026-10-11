import json
import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._device_fleet.api.device_pair_claim import DevicePairClaim
from plugins._device_fleet.helpers import pairing as pairing_module
from plugins._device_fleet.helpers import registry as registry_module
from plugins._device_fleet.helpers.pairing import PairingStore
from plugins._device_fleet.helpers.registry import DeviceRegistry


@pytest.fixture(autouse=True)
def _clean_defaults():
    yield
    registry_module.set_default(None)
    pairing_module.set_default(None)


def _handler() -> DevicePairClaim:
    return DevicePairClaim(app=None, thread_lock=threading.RLock())


def _body(response) -> dict:
    return json.loads(response.get_data(as_text=True))


@pytest.mark.asyncio
async def test_claim_creates_device_with_limits(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    registry_module.set_default(registry)
    store = PairingStore(registry)
    pairing_module.set_default(store)
    code = store.create_code()

    response = await _handler().process(
        {
            "code": code.code,
            "name": "x" * 200,
            "runtime": {"os": "linux", "hostname": "h" * 500},
            "policy": {"exec_enabled": True, "roots": ["/srv"]},
            "tools": ["system_info", "read_file"],
        },
        request=None,
    )

    assert response.status_code == 201
    payload = _body(response)
    assert payload["ok"] is True
    assert payload["device_token"]
    device = registry.get(payload["device_id"])
    assert len(device["name"]) == 64
    assert len(device["runtime"]["hostname"]) == 200
    assert device["policy"]["exec_enabled"] is True
    assert device["tools"] == ["system_info", "read_file"]


@pytest.mark.asyncio
async def test_claim_invalid_code_returns_404(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    registry_module.set_default(registry)
    pairing_module.set_default(PairingStore(registry))

    response = await _handler().process({"code": "AAAAA-AAAAA", "name": "x"}, request=None)
    assert response.status_code == 404
    assert _body(response)["ok"] is False


@pytest.mark.asyncio
async def test_claim_requires_code(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    registry_module.set_default(registry)
    pairing_module.set_default(PairingStore(registry))

    response = await _handler().process({}, request=None)
    assert response.status_code == 404
