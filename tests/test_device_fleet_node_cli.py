import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

NODE_ROOT = PROJECT_ROOT / "plugins" / "_device_fleet" / "node"
if str(NODE_ROOT) not in sys.path:
    sys.path.insert(0, str(NODE_ROOT))

from a0_node import __main__ as cli


def test_https_hub_is_normalized() -> None:
    assert (
        cli.normalize_hub_url("https://mac.tailnet.ts.net/")
        == "https://mac.tailnet.ts.net"
    )


def test_bare_host_defaults_to_https() -> None:
    assert cli.normalize_hub_url("mac.tailnet.ts.net") == "https://mac.tailnet.ts.net"


@pytest.mark.parametrize(
    "value",
    ["http://localhost:5080", "http://127.0.0.1:5080", "http://127.0.0.2:5080"],
)
def test_loopback_http_is_allowed(value: str) -> None:
    assert cli.normalize_hub_url(value) == value


def test_plain_http_non_loopback_rejected_by_default() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.normalize_hub_url("http://100.64.0.1:5080")
    assert "refusing plain http" in str(excinfo.value)


def test_plain_http_allowed_with_flag() -> None:
    assert (
        cli.normalize_hub_url("http://100.64.0.1:5080", allow_plain_http=True)
        == "http://100.64.0.1:5080"
    )


def test_https_unaffected_by_flag() -> None:
    assert (
        cli.normalize_hub_url("https://mac.tailnet.ts.net", allow_plain_http=True)
        == "https://mac.tailnet.ts.net"
    )


def test_parser_accepts_allow_plain_http() -> None:
    args = cli.build_parser().parse_args(
        [
            "join",
            "--hub",
            "http://100.64.0.1:5080",
            "--code",
            "ABCDE-FGHIJ",
            "--allow-plain-http",
        ]
    )
    assert args.allow_plain_http is True


def test_join_persists_allow_plain_http(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("A0_NODE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        cli,
        "claim_pairing",
        lambda hub, code, name, policy: {
            "device_id": "device_test",
            "device_token": "t" * 32,
            "device_name": name,
        },
    )
    args = cli.build_parser().parse_args(
        [
            "join",
            "--hub",
            "http://100.64.0.1:5080",
            "--code",
            "ABCDE-FGHIJ",
            "--name",
            "ubuntu-server",
            "--allow-plain-http",
            "--workspace",
            str(tmp_path / "ws"),
        ]
    )

    assert cli.command_join(args) == 0

    config = json.loads(
        (tmp_path / "home" / "config.json").read_text(encoding="utf-8")
    )
    assert config["allow_plain_http"] is True
    assert config["hub_url"] == "http://100.64.0.1:5080"
    assert config["name"] == "ubuntu-server"
    assert config["policy"]["exec_enabled"] is False


def test_join_rejects_plain_http_without_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("A0_NODE_HOME", str(tmp_path / "home"))
    args = cli.build_parser().parse_args(
        [
            "join",
            "--hub",
            "http://100.64.0.1:5080",
            "--code",
            "ABCDE-FGHIJ",
            "--workspace",
            str(tmp_path / "ws"),
        ]
    )

    with pytest.raises(SystemExit):
        cli.command_join(args)
    assert not (tmp_path / "home" / "config.json").exists()
