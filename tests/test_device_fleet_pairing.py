import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._device_fleet.helpers.pairing import PairingStore
from plugins._device_fleet.helpers.registry import DeviceRegistry


def _store(tmp_path: Path, **kwargs) -> PairingStore:
    registry = DeviceRegistry(tmp_path)
    return PairingStore(registry, **kwargs)


def test_claim_success_creates_device(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = store.create_code()

    result, error = store.claim(entry.code, name="server-01")

    assert error is None
    assert result is not None
    assert result["device_id"].startswith("device_")
    assert store.registry.verify_token(result["device_id"], result["device_token"])
    device = store.registry.get(result["device_id"])
    assert device["name"] == "server-01"


def test_code_is_single_use(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = store.create_code()
    result, error = store.claim(entry.code, name="box")
    assert error is None

    second, second_error = store.claim(entry.code, name="box2")
    assert second is None
    assert second_error["code"] == "INVALID_CODE"


def test_unknown_code_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result, error = store.claim("AAAAA-AAAAA", name="box")
    assert result is None
    assert error["code"] == "INVALID_CODE"


def test_expired_code_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = store.create_code()
    entry.expires_at = time.time() - 1

    result, error = store.claim(entry.code, name="box")
    assert result is None
    assert error["code"] == "INVALID_CODE"


def test_max_devices_enforced(tmp_path: Path) -> None:
    store = _store(tmp_path, max_devices=1)
    first = store.create_code()
    assert store.claim(first.code, name="one")[1] is None

    second = store.create_code()
    result, error = store.claim(second.code, name="two")
    assert result is None
    assert error["code"] == "MAX_DEVICES"


def test_claim_rate_limit(tmp_path: Path) -> None:
    store = _store(tmp_path, max_claims_per_minute=2, max_devices=10)
    codes = [store.create_code().code for _ in range(3)]

    assert store.claim(codes[0], name="a")[1] is None
    assert store.claim(codes[1], name="b")[1] is None
    result, error = store.claim(codes[2], name="c")
    assert result is None
    assert error["code"] == "RATE_LIMITED"


def test_code_format_is_canonicalized(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = store.create_code()
    normalized = entry.code.replace("-", "").lower()

    result, error = store.claim(normalized, name="box")
    assert error is None
    assert result is not None
