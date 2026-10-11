import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._device_fleet.helpers import protocol


def _hello(**overrides):
    payload = {
        "protocol_version": protocol.PROTOCOL_VERSION,
        "device": {
            "id": "device_abc",
            "name": "server-01",
            "token": "t" * 32,
        },
        "runtime": {"os": "linux", "arch": "x86_64", "hostname": "srv"},
        "tools": [
            {
                "name": "read_file",
                "title": "Read file",
                "description": "read a file",
                "input_schema": {"type": "object"},
            }
        ],
        "policy": {"exec_enabled": True, "read_only": False, "roots": ["/srv"]},
    }
    payload.update(overrides)
    return payload


def test_normalize_valid_hello() -> None:
    hello, error = protocol.normalize_hello(_hello())
    assert error is None
    assert hello is not None
    assert hello["device"]["name"] == "server-01"
    assert hello["tool_names"] == ["read_file"]
    assert hello["runtime"]["os"] == "linux"
    assert hello["policy"]["exec_enabled"] is True


def test_reject_wrong_protocol_version() -> None:
    hello, error = protocol.normalize_hello(_hello(protocol_version=99))
    assert hello is None
    assert error["code"] == "PROTOCOL_MISMATCH"
    assert error["category"] == protocol.ERROR_PROTOCOL


def test_reject_missing_token() -> None:
    hello, error = protocol.normalize_hello(_hello(device={"id": "x", "name": "n"}))
    assert hello is None
    assert error["code"] == "INVALID_HELLO"


def test_reject_non_object_payload() -> None:
    hello, error = protocol.normalize_hello("nope")
    assert hello is None
    assert error["code"] == "INVALID_HELLO"
    assert error["category"] == protocol.ERROR_VALIDATION


def test_tools_are_sanitized_and_capped() -> None:
    tools = [
        {"name": f"tool_{index}", "description": "x" * 5000} for index in range(200)
    ]
    hello, error = protocol.normalize_hello(_hello(tools=tools))
    assert error is None
    assert hello is not None
    assert len(hello["tools"]) == protocol.MAX_TOOLS
    assert all(len(entry["description"]) <= 2000 for entry in hello["tools"])


def test_error_payload_shape() -> None:
    payload = protocol.error_payload(
        "X", "boom", category=protocol.ERROR_TIMEOUT, retryable=True
    )
    assert payload == {
        "code": "X",
        "message": "boom",
        "category": "timeout",
        "retryable": True,
    }
