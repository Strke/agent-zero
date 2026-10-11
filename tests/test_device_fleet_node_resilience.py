import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

NODE_ROOT = PROJECT_ROOT / "plugins" / "_device_fleet" / "node"
if str(NODE_ROOT) not in sys.path:
    sys.path.insert(0, str(NODE_ROOT))

from a0_node import service
from a0_node.client import A0NodeClient, HandshakeRejected


def _client(tmp_path: Path) -> A0NodeClient:
    return A0NodeClient(
        {
            "version": 1,
            "hub_url": "http://127.0.0.1:1",
            "device_id": "device_test",
            "device_token": "t" * 32,
            "name": "resilience-test",
            "policy": {"roots": [str(tmp_path)], "exec_enabled": False},
        }
    )


def _drive_waits(client: A0NodeClient, stop_after: int) -> list[float]:
    waits: list[float] = []

    def fake_wait(timeout: float) -> bool:
        waits.append(timeout)
        return len(waits) >= stop_after

    client._stop.wait = fake_wait  # type: ignore[method-assign]
    return waits


def test_rejected_handshake_never_exits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("a0_node.client.random.uniform", lambda a, b: 0.0)
    client = _client(tmp_path)
    waits = _drive_waits(client, stop_after=3)
    calls: list[int] = []

    def fake_serve() -> float:
        calls.append(1)
        if len(calls) == 1:
            raise HandshakeRejected("AUTH_FAILED", "revoked", category="auth")
        return 5.0

    client._serve = fake_serve  # type: ignore[method-assign]

    assert client.run_forever() == 0
    assert len(calls) == 3
    assert waits[0] == 1.0
    assert waits[1] == 2.0
    assert waits[2] == 4.0


def test_stable_session_resets_backoff(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("a0_node.client.random.uniform", lambda a, b: 0.0)
    client = _client(tmp_path)
    waits = _drive_waits(client, stop_after=3)
    responses = [ConnectionError("down"), 60.0, 5.0]

    def fake_serve() -> float:
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    client._serve = fake_serve  # type: ignore[method-assign]

    assert client.run_forever() == 0
    assert waits[0] == 1.0
    assert waits[1] == 1.0
    assert waits[2] == 2.0


def test_backoff_is_capped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("a0_node.client.random.uniform", lambda a, b: 0.0)
    client = _client(tmp_path)
    waits = _drive_waits(client, stop_after=8)

    def fake_serve() -> float:
        raise ConnectionError("still down")

    client._serve = fake_serve  # type: ignore[method-assign]

    client.run_forever()
    assert waits[-1] == 30.0


def test_handshake_hint_covers_auth_and_protocol() -> None:
    from a0_node.client import _handshake_hint

    assert "pairing code" in _handshake_hint(
        HandshakeRejected("AUTH_FAILED", "no", category="auth")
    )
    assert "protocol" in _handshake_hint(
        HandshakeRejected("PROTOCOL_MISMATCH", "no", category="protocol")
    )
    assert _handshake_hint(HandshakeRejected("WEIRD", "no")) == ""


def test_systemd_unit_waits_for_network() -> None:
    text = service.systemd_unit_text()
    assert "Wants=network-online.target" in text
    assert "Restart=always" in text


def test_windows_task_has_logon_trigger_and_restart() -> None:
    command = service.windows_scheduled_task_command()
    joined = " ".join(command)
    assert "New-ScheduledTaskTrigger -AtLogOn" in joined
    assert "-RestartCount 999" in joined
    assert "RestartInterval" in joined
    assert "Register-ScheduledTask -TaskName 'A0Node'" in joined
