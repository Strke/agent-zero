import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._device_fleet.helpers.registry import DeviceRegistry


def test_create_and_lookup(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, token = registry.create_device(
        name="server-01", runtime={"os": "linux", "arch": "x86_64"}
    )

    assert device["id"].startswith("device_")
    assert device["name"] == "server-01"
    assert device["enabled"] is True
    assert registry.verify_token(device["id"], token)
    assert not registry.verify_token(device["id"], token + "x")
    assert not registry.verify_token("device_missing", token)
    assert registry.get(device["id"])["id"] == device["id"]
    assert registry.get("server-01")["id"] == device["id"]
    assert registry.get("SERVER-01")["id"] == device["id"]
    assert registry.count() == 1


def test_token_is_stored_hashed(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, token = registry.create_device(name="box")

    raw = (tmp_path / "registry.json").read_text(encoding="utf-8")
    assert token not in raw
    payload = json.loads(raw)
    stored = payload["devices"][device["id"]]
    assert stored["token_hash"] != token
    assert len(stored["token_hash"]) == 64
    mode = (tmp_path / "registry.json").stat().st_mode & 0o777
    assert mode == 0o600


def test_persistence_roundtrip(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, token = registry.create_device(name="persisted")

    reopened = DeviceRegistry(tmp_path)
    assert reopened.get(device["id"])["name"] == "persisted"
    assert reopened.verify_token(device["id"], token)


def test_touch_updates_metadata(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, _ = registry.create_device(name="box")

    updated = registry.touch(
        device["id"],
        runtime={"os": "darwin"},
        tools=["system_info", "read_file"],
        policy={"exec_enabled": True},
    )
    assert updated is not None
    assert updated["runtime"] == {"os": "darwin"}
    assert updated["tools"] == ["system_info", "read_file"]
    assert updated["last_seen"] > 0
    assert updated["policy"]["exec_enabled"] is True


def test_enable_disable_and_remove(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    device, token = registry.create_device(name="box")

    assert registry.set_enabled(device["id"], False)
    assert registry.get(device["id"])["enabled"] is False
    assert registry.verify_token(device["id"], token)

    assert registry.remove(device["id"])
    assert registry.count() == 0
    assert registry.get(device["id"]) is None
    assert not registry.remove(device["id"])


def test_ambiguous_name_returns_none(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    registry.create_device(name="dup")
    registry.create_device(name="dup")

    assert registry.get("dup") is None
    assert len(registry.name_matches("dup")) == 2


@pytest.mark.parametrize("content", ["not json", "[]", '{"version": 99}'])
def test_corrupt_registry_is_treated_as_empty(tmp_path: Path, content: str) -> None:
    (tmp_path / "registry.json").write_text(content, encoding="utf-8")
    registry = DeviceRegistry(tmp_path)
    assert registry.count() == 0


def test_corrupt_registry_is_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("not json", encoding="utf-8")
    registry = DeviceRegistry(tmp_path)

    assert registry.list_devices() == []

    quarantined = list(tmp_path.glob("registry.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "not json"
    assert not path.exists()

    device, token = registry.create_device(name="after-quarantine")
    assert registry.verify_token(device["id"], token)
