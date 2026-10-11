import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

NODE_ROOT = PROJECT_ROOT / "plugins" / "_device_fleet" / "node"
if str(NODE_ROOT) not in sys.path:
    sys.path.insert(0, str(NODE_ROOT))

from a0_node.ops import ExecContext, dispatch
from a0_node.policy import Policy, PolicyError


def _policy(tmp_path: Path, **overrides) -> Policy:
    policy = {"roots": [str(tmp_path)]}
    policy.update(overrides)
    return Policy({"policy": policy})


def test_system_info(tmp_path: Path) -> None:
    result = dispatch(_policy(tmp_path), "system_info", {})
    assert result["node_version"]
    assert str(tmp_path) in result["roots"]
    assert result["exec_enabled"] is False


def test_write_read_roundtrip(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    target = tmp_path / "notes" / "hello.txt"

    write = dispatch(
        policy, "write_file", {"path": str(target), "content": "hello world"}
    )
    assert write["created"] is True
    assert write["bytes_written"] == 11

    read = dispatch(policy, "read_file", {"path": str(target)})
    assert read["content"] == "hello world"
    assert read["truncated"] is False
    assert read["binary"] is False


def test_read_file_pagination(tmp_path: Path) -> None:
    policy = _policy(tmp_path, max_read_bytes=1024)
    target = tmp_path / "big.txt"
    target.write_text("x" * 3000, encoding="utf-8")

    first = dispatch(policy, "read_file", {"path": str(target)})
    assert first["content"] == "x" * 1024
    assert first["truncated"] is True
    assert first["next_offset"] == 1024

    second = dispatch(policy, "read_file", {"path": str(target), "offset": 1024})
    assert second["content"] == "x" * 1024
    assert second["truncated"] is True

    last = dispatch(policy, "read_file", {"path": str(target), "offset": 2048})
    assert last["content"] == "x" * 952
    assert last["truncated"] is False
    assert last["next_offset"] is None


def test_read_file_binary_detection(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\x00\x01\x02")
    result = dispatch(policy, "read_file", {"path": str(target)})
    assert result["binary"] is True
    assert "content" not in result


def test_path_outside_roots_rejected(tmp_path: Path) -> None:
    policy = _policy(tmp_path / "workspace")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(PolicyError) as excinfo:
        dispatch(policy, "read_file", {"path": str(outside)})
    assert excinfo.value.code == "PATH_OUTSIDE_ROOTS"


def test_traversal_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = _policy(workspace)

    with pytest.raises(PolicyError):
        dispatch(policy, "read_file", {"path": "../outside.txt"})


def test_relative_path_resolves_inside_workspace(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    (tmp_path / "rel.txt").write_text("relative", encoding="utf-8")
    result = dispatch(policy, "read_file", {"path": "rel.txt"})
    assert result["content"] == "relative"


def test_read_only_blocks_writes(tmp_path: Path) -> None:
    policy = _policy(tmp_path, read_only=True)
    with pytest.raises(PolicyError) as excinfo:
        dispatch(policy, "write_file", {"path": str(tmp_path / "x.txt"), "content": "x"})
    assert excinfo.value.code == "READ_ONLY"


def test_write_size_limit(tmp_path: Path) -> None:
    policy = _policy(tmp_path, max_write_bytes=1024)
    with pytest.raises(PolicyError) as excinfo:
        dispatch(
            policy,
            "write_file",
            {"path": str(tmp_path / "x.txt"), "content": "y" * 2000},
        )
    assert excinfo.value.code == "PAYLOAD_TOO_LARGE"


def test_write_precondition(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    target = tmp_path / "file.txt"
    target.write_text("original", encoding="utf-8")

    with pytest.raises(PolicyError) as excinfo:
        dispatch(
            policy,
            "write_file",
            {
                "path": str(target),
                "content": "new",
                "expected_sha256": "0" * 64,
            },
        )
    assert excinfo.value.code == "STALE_WRITE"


def test_exec_disabled_by_default(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    with pytest.raises(PolicyError) as excinfo:
        dispatch(policy, "exec_command", {"command": "echo hi"})
    assert excinfo.value.code == "EXEC_DISABLED"


@pytest.mark.skipif(os.name == "nt", reason="posix shell test")
def test_exec_command_runs_when_enabled(tmp_path: Path) -> None:
    policy = _policy(tmp_path, exec_enabled=True)
    result = dispatch(
        policy, "exec_command", {"command": "echo hello && echo err >&2"}, ExecContext()
    )
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]
    assert "err" in result["stderr"]
    assert result["timed_out"] is False


@pytest.mark.skipif(os.name == "nt", reason="posix shell test")
def test_exec_timeout_kills_process(tmp_path: Path) -> None:
    policy = _policy(tmp_path, exec_enabled=True, exec_timeout_seconds=1)
    result = dispatch(policy, "exec_command", {"command": "sleep 30"}, ExecContext())
    assert result["timed_out"] is True
    assert result["duration_seconds"] < 10


def test_list_dir_and_glob(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.md").write_text("b", encoding="utf-8")
    (tmp_path / "sub" / "c.txt").write_text("c", encoding="utf-8")

    listing = dispatch(policy, "list_dir", {"path": str(tmp_path)})
    assert listing["entry_count"] == 3

    filtered = dispatch(policy, "list_dir", {"path": str(tmp_path), "glob": "*.txt"})
    names = {Path(entry["path"]).name for entry in filtered["entries"]}
    assert names == {"a.txt", "sub"}

    deep = dispatch(policy, "list_dir", {"path": str(tmp_path), "depth": 2})
    paths = {Path(entry["path"]).name for entry in deep["entries"]}
    assert {"a.txt", "b.md", "sub", "c.txt"} <= paths


def test_search_text(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    (tmp_path / "one.py").write_text("import os\nprint('needle')\n", encoding="utf-8")
    (tmp_path / "two.txt").write_text("nothing here\n", encoding="utf-8")

    result = dispatch(policy, "search_text", {"path": str(tmp_path), "query": "needle"})
    assert result["match_count"] == 1
    assert result["matches"][0]["line"] == 2

    filtered = dispatch(
        policy, "search_text", {"path": str(tmp_path), "query": "nothing", "glob": "*.py"}
    )
    assert filtered["match_count"] == 0


def test_move_and_delete(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    source = tmp_path / "one.txt"
    source.write_text("data", encoding="utf-8")
    destination = tmp_path / "moved" / "two.txt"

    move = dispatch(
        policy, "move_file", {"source": str(source), "destination": str(destination)}
    )
    assert move["destination"] == str(destination)
    assert destination.exists()

    source.write_text("again", encoding="utf-8")

    with pytest.raises(PolicyError) as excinfo:
        dispatch(
            policy,
            "move_file",
            {"source": str(source), "destination": str(destination)},
        )
    assert excinfo.value.code == "DESTINATION_EXISTS"
    assert destination.read_text(encoding="utf-8") == "data"

    dispatch(
        policy,
        "move_file",
        {"source": str(source), "destination": str(destination), "overwrite": True},
    )
    assert destination.read_text(encoding="utf-8") == "again"

    delete = dispatch(policy, "delete_file", {"path": str(destination)})
    assert delete["deleted"] is True
    assert not destination.exists()

    (tmp_path / "dir").mkdir()
    with pytest.raises(PolicyError) as excinfo:
        dispatch(policy, "delete_file", {"path": str(tmp_path / "dir")})
    assert excinfo.value.code == "IS_A_DIRECTORY"
    assert dispatch(
        policy, "delete_file", {"path": str(tmp_path / "dir"), "recursive": True}
    )["deleted"] is True


def test_unknown_tool(tmp_path: Path) -> None:
    with pytest.raises(PolicyError) as excinfo:
        dispatch(_policy(tmp_path), "self_destruct", {})
    assert excinfo.value.code == "UNKNOWN_TOOL"
