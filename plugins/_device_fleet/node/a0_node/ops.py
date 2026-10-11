from __future__ import annotations

import fnmatch
import getpass
import hashlib
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import NODE_VERSION
from .policy import Policy, PolicyError

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".tox"}
BINARY_SNIFF_BYTES = 8192


@dataclass
class ExecContext:
    cancel_event: Any = None
    register_process: Callable[[subprocess.Popen | None], None] | None = None


def _decode_limited(raw: bytes, limit: int) -> tuple[str, bool]:
    data = raw or b""
    truncated = len(data) > limit
    if truncated:
        data = data[:limit]
    return data.decode("utf-8", errors="replace"), truncated


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _iter_files(root: Path):
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".git")]
        for name in files:
            yield Path(current) / name


def op_system_info(policy: Policy, args: dict[str, Any], ctx: ExecContext) -> dict[str, Any]:
    disk = None
    try:
        usage = shutil.disk_usage(str(policy.roots[0]))
        disk = {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "path": str(policy.roots[0]),
        }
    except OSError:
        disk = None
    return {
        "os": platform.system().lower(),
        "platform": platform.platform(),
        "arch": platform.machine(),
        "hostname": socket.gethostname(),
        "user": getpass.getuser(),
        "python": platform.python_version(),
        "cwd": str(Path.cwd()),
        "node_version": NODE_VERSION,
        "roots": [str(root) for root in policy.roots],
        "exec_enabled": policy.exec_enabled,
        "read_only": policy.read_only,
        "disk": disk,
        "time": time.time(),
    }


def op_exec_command(policy: Policy, args: dict[str, Any], ctx: ExecContext) -> dict[str, Any]:
    policy.check_exec()
    command = str(args.get("command") or "").strip()
    if not command:
        raise PolicyError("INVALID_ARGUMENTS", "command is required", category="validation")

    cwd_arg = args.get("cwd")
    workdir = policy.resolve_path(cwd_arg) if cwd_arg else policy.roots[0]
    if not workdir.is_dir():
        raise PolicyError("NOT_A_DIRECTORY", f"cwd is not a directory: {workdir}", category="execution")

    timeout = policy.clamp_exec_timeout(args.get("timeout_seconds"))
    popen_kwargs: dict[str, Any] = {
        "shell": True,
        "cwd": str(workdir),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": dict(os.environ),
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    started = time.monotonic()
    proc = subprocess.Popen(command, **popen_kwargs)
    if ctx.register_process is not None:
        ctx.register_process(proc)

    timed_out = False
    cancelled = False
    out: bytes = b""
    err: bytes = b""
    deadline = started + timeout
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                should_stop = False
                if ctx.cancel_event is not None and ctx.cancel_event.is_set():
                    cancelled = True
                    should_stop = True
                elif time.monotonic() >= deadline:
                    timed_out = True
                    should_stop = True
                if not should_stop:
                    continue
                _kill_tree(proc)
                try:
                    out, err = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    out, err = b"", b""
                break
    finally:
        if ctx.register_process is not None:
            ctx.register_process(None)

    duration = time.monotonic() - started
    stdout, stdout_truncated = _decode_limited(out, policy.max_output_bytes)
    stderr, stderr_truncated = _decode_limited(err, policy.max_output_bytes)
    return {
        "command": command,
        "cwd": str(workdir),
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "timeout_seconds": timeout,
        "duration_seconds": round(duration, 3),
    }


def op_list_dir(policy: Policy, args: dict[str, Any], ctx: ExecContext) -> dict[str, Any]:
    target = policy.resolve_path(args.get("path") or ".")
    if not target.exists():
        raise PolicyError("NOT_FOUND", f"path not found: {target}", category="execution")
    if not target.is_dir():
        raise PolicyError("NOT_A_DIRECTORY", f"not a directory: {target}", category="execution")

    try:
        depth = max(1, min(3, int(args.get("depth") or 1)))
    except (TypeError, ValueError):
        depth = 1
    pattern = str(args.get("glob") or "").strip() or None

    entries: list[dict[str, Any]] = []
    truncated = False

    def walk(path: Path, level: int) -> None:
        nonlocal truncated
        if truncated:
            return
        try:
            children = sorted(
                os.scandir(path),
                key=lambda entry: (not entry.is_dir(follow_symlinks=False), entry.name.lower()),
            )
        except OSError:
            return
        for child in children:
            if len(entries) >= policy.max_list_entries:
                truncated = True
                return
            try:
                is_dir = child.is_dir(follow_symlinks=False)
                is_link = child.is_symlink()
                size = child.stat(follow_symlinks=False).st_size
                modified = child.stat(follow_symlinks=False).st_mtime
            except OSError:
                continue
            if pattern and not is_dir and not fnmatch.fnmatch(child.name, pattern):
                continue
            entries.append(
                {
                    "path": child.path,
                    "relative": os.path.relpath(child.path, target),
                    "type": "symlink" if is_link else ("dir" if is_dir else "file"),
                    "size": size,
                    "modified": modified,
                }
            )
            if is_dir and not is_link and level < depth:
                walk(Path(child.path), level + 1)
                if truncated:
                    return

    walk(target, 1)
    return {
        "root": str(target),
        "depth": depth,
        "entries": entries,
        "entry_count": len(entries),
        "truncated": truncated,
    }


def op_read_file(policy: Policy, args: dict[str, Any], ctx: ExecContext) -> dict[str, Any]:
    path = policy.resolve_path(args.get("path"))
    if not path.exists():
        raise PolicyError("NOT_FOUND", f"file not found: {path}", category="execution")
    if path.is_dir():
        raise PolicyError("NOT_A_FILE", f"path is a directory: {path}", category="execution")

    size = path.stat().st_size
    try:
        offset = max(0, int(args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = int(args.get("limit") or policy.max_read_bytes)
    except (TypeError, ValueError):
        limit = policy.max_read_bytes
    limit = max(1, min(limit, policy.max_read_bytes))

    with path.open("rb") as handle:
        sniff = handle.read(min(BINARY_SNIFF_BYTES, size))
        if b"\x00" in sniff:
            return {
                "path": str(path),
                "binary": True,
                "size": size,
                "message": "binary file; use system tools on the device to inspect it",
            }
        handle.seek(min(offset, size))
        chunk = handle.read(limit)
        more = handle.read(1)

    text = chunk.decode("utf-8", errors="replace")
    return {
        "path": str(path),
        "binary": False,
        "offset": offset,
        "bytes_returned": len(chunk),
        "total_size": size,
        "next_offset": offset + len(chunk) if more else None,
        "truncated": bool(more),
        "content": text,
    }


def op_write_file(policy: Policy, args: dict[str, Any], ctx: ExecContext) -> dict[str, Any]:
    path = policy.resolve_path(args.get("path"), write=True)
    content = args.get("content")
    if not isinstance(content, str):
        raise PolicyError("INVALID_ARGUMENTS", "content must be a string", category="validation")
    data = content.encode("utf-8")
    if len(data) > policy.max_write_bytes:
        raise PolicyError(
            "PAYLOAD_TOO_LARGE",
            f"content is {len(data)} bytes; this device allows at most "
            f"{policy.max_write_bytes} bytes per write",
        )
    if path.exists() and path.is_dir():
        raise PolicyError("IS_A_DIRECTORY", f"path is a directory: {path}", category="execution")

    existed = path.exists()
    if existed:
        expected = args.get("expected_sha256")
        if expected:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != str(expected).strip().lower():
                raise PolicyError(
                    "STALE_WRITE",
                    "file changed since it was read (sha256 mismatch); read it again first",
                    category="execution",
                    retryable=True,
                )

    if args.get("create_dirs", True):
        path.parent.mkdir(parents=True, exist_ok=True)

    mode = (path.stat().st_mode & 0o777) if existed else 0o644
    fd, tmp_path = tempfile.mkstemp(prefix=".a0-write-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return {
        "path": str(path),
        "created": not existed,
        "bytes_written": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def op_search_text(policy: Policy, args: dict[str, Any], ctx: ExecContext) -> dict[str, Any]:
    root = policy.resolve_path(args.get("path") or ".")
    if not root.exists():
        raise PolicyError("NOT_FOUND", f"path not found: {root}", category="execution")
    query = str(args.get("query") or "")
    if not query:
        raise PolicyError("INVALID_ARGUMENTS", "query is required", category="validation")
    try:
        regex = re.compile(query, re.IGNORECASE)
    except re.error as exc:
        raise PolicyError("INVALID_PATTERN", f"query is not a valid regex: {exc}", category="validation")
    pattern = str(args.get("glob") or "").strip() or None

    matches: list[dict[str, Any]] = []
    truncated = False
    files_scanned = 0
    for path in _iter_files(root):
        if len(matches) >= policy.max_search_results:
            truncated = True
            break
        if pattern and not fnmatch.fnmatch(path.name, pattern):
            continue
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:BINARY_SNIFF_BYTES]:
            continue
        files_scanned += 1
        text = raw.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not regex.search(line):
                continue
            matches.append(
                {"path": str(path), "line": line_number, "text": line.strip()[:500]}
            )
            if len(matches) >= policy.max_search_results:
                truncated = True
                break

    return {
        "root": str(root),
        "query": query,
        "matches": matches,
        "match_count": len(matches),
        "files_scanned": files_scanned,
        "truncated": truncated,
    }


def op_move_file(policy: Policy, args: dict[str, Any], ctx: ExecContext) -> dict[str, Any]:
    source = policy.resolve_path(args.get("source"), write=True)
    destination = policy.resolve_path(args.get("destination"), write=True)
    if not source.exists():
        raise PolicyError("NOT_FOUND", f"source not found: {source}", category="execution")
    if destination.exists() and not bool(args.get("overwrite", False)):
        raise PolicyError(
            "DESTINATION_EXISTS",
            f"destination already exists: {destination}; pass overwrite=true to replace it",
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return {"source": str(source), "destination": str(destination)}


def op_delete_file(policy: Policy, args: dict[str, Any], ctx: ExecContext) -> dict[str, Any]:
    path = policy.resolve_path(args.get("path"), write=True)
    if not path.exists():
        raise PolicyError("NOT_FOUND", f"path not found: {path}", category="execution")
    recursive = bool(args.get("recursive", False))
    if path.is_dir():
        if not recursive:
            raise PolicyError(
                "IS_A_DIRECTORY",
                f"{path} is a directory; pass recursive=true to delete it with its contents",
            )
        shutil.rmtree(path)
    else:
        path.unlink()
    return {"path": str(path), "deleted": True, "recursive": recursive}


def dispatch(
    policy: Policy,
    tool: str,
    args: dict[str, Any],
    ctx: ExecContext | None = None,
) -> dict[str, Any]:
    context = ctx or ExecContext()
    handler = _HANDLERS.get(tool)
    if handler is None:
        raise PolicyError("UNKNOWN_TOOL", f"unknown tool: {tool}", category="validation")
    if not isinstance(args, dict):
        raise PolicyError("INVALID_ARGUMENTS", "arguments must be an object", category="validation")
    return handler(policy, args, context)


def args_summary(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("command", "path", "source", "destination", "query", "glob"):
        value = args.get(key)
        if value not in (None, ""):
            summary[key] = str(value)[:300]
    return summary


_HANDLERS: dict[str, Callable[[Policy, dict[str, Any], ExecContext], dict[str, Any]]] = {
    "system_info": op_system_info,
    "exec_command": op_exec_command,
    "list_dir": op_list_dir,
    "read_file": op_read_file,
    "write_file": op_write_file,
    "search_text": op_search_text,
    "move_file": op_move_file,
    "delete_file": op_delete_file,
}

TOOL_DESCRIPTORS: list[dict[str, Any]] = [
    {
        "name": "system_info",
        "title": "System information",
        "description": "operating system, hostname, user, python, allowed roots, and disk usage of this device",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "exec_command",
        "title": "Run a shell command",
        "description": "run a shell command on this device and return stdout, stderr, and exit code; disabled unless the owner enabled execution",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "shell command to run"},
                "cwd": {"type": "string", "description": "working directory inside the allowed roots"},
                "timeout_seconds": {"type": "integer", "description": "optional timeout override"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "list_dir",
        "title": "List a directory",
        "description": "list files and directories under an allowed root with optional depth and glob filter",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "depth": {"type": "integer", "minimum": 1, "maximum": 3},
                "glob": {"type": "string"},
            },
        },
    },
    {
        "name": "read_file",
        "title": "Read a text file",
        "description": "read utf-8 text from a file with offset/limit continuation; binary files report metadata only",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "title": "Write a text file",
        "description": "atomically write utf-8 text to a file inside a writable root; optional sha256 precondition",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "create_dirs": {"type": "boolean"},
                "expected_sha256": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "search_text",
        "title": "Search text",
        "description": "regex search across text files under an allowed root with optional glob filter",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "query": {"type": "string"},
                "glob": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "move_file",
        "title": "Move or rename a file",
        "description": "move a file or directory between allowed roots; overwrite must be explicit",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["source", "destination"],
        },
    },
    {
        "name": "delete_file",
        "title": "Delete a file or directory",
        "description": "delete a file, or a directory with recursive=true",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "recursive": {"type": "boolean"},
            },
            "required": ["path"],
        },
    },
]
