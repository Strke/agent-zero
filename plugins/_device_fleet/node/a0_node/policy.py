from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .config import audit_path, merged_policy

AUDIT_MAX_BYTES = 5 * 1024 * 1024


class PolicyError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        category: str = "permission",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.category = category
        self.retryable = retryable

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "category": self.category,
            "retryable": self.retryable,
        }


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


class Policy:
    def __init__(self, config: dict[str, Any]) -> None:
        policy = merged_policy(config)
        self.exec_enabled = bool(policy.get("exec_enabled", False))
        self.read_only = bool(policy.get("read_only", False))
        self.roots = self._build_roots(policy.get("roots"))
        self.max_read_bytes = _clamp_int(policy.get("max_read_bytes"), 262144, 1024, 4 * 1024 * 1024)
        self.max_write_bytes = _clamp_int(policy.get("max_write_bytes"), 262144, 1024, 4 * 1024 * 1024)
        self.max_list_entries = _clamp_int(policy.get("max_list_entries"), 500, 1, 5000)
        self.max_search_results = _clamp_int(policy.get("max_search_results"), 200, 1, 2000)
        self.exec_timeout_seconds = _clamp_int(policy.get("exec_timeout_seconds"), 30, 1, 86400)
        self.max_exec_timeout_seconds = _clamp_int(
            policy.get("max_exec_timeout_seconds"), 3600, 1, 86400
        )
        self.max_output_bytes = _clamp_int(policy.get("max_output_bytes"), 1048576, 1024, 16 * 1024 * 1024)
        self._audit_path = audit_path()

    @staticmethod
    def _build_roots(raw: Any) -> list[Path]:
        roots: list[Path] = []
        if isinstance(raw, list):
            for entry in raw:
                text = str(entry or "").strip()
                if not text:
                    continue
                try:
                    roots.append(Path(text).expanduser().resolve())
                except OSError:
                    continue
        if not roots:
            roots = [Path("~/a0-workspace").expanduser().resolve()]
        unique: list[Path] = []
        for root in roots:
            if root not in unique:
                unique.append(root)
        return unique

    def resolve_path(self, raw: Any, *, write: bool = False) -> Path:
        text = str(raw or "").strip()
        if not text:
            raise PolicyError("INVALID_PATH", "path is required", category="validation")
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = self.roots[0] / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()

        for root in self.roots:
            if resolved == root or root in resolved.parents:
                if write:
                    self.check_write()
                return resolved
        raise PolicyError(
            "PATH_OUTSIDE_ROOTS",
            f"path {resolved} is outside the allowed roots: "
            + ", ".join(str(root) for root in self.roots),
        )

    def check_write(self) -> None:
        if self.read_only:
            raise PolicyError(
                "READ_ONLY",
                "this device is configured read-only; writes are not allowed",
            )

    def check_exec(self) -> None:
        if not self.exec_enabled:
            raise PolicyError(
                "EXEC_DISABLED",
                "remote command execution is disabled on this device; "
                "the owner can enable it with --enable-exec at join time",
            )

    def clamp_exec_timeout(self, requested: Any) -> int:
        if requested in (None, ""):
            return self.exec_timeout_seconds
        try:
            value = int(float(requested))
        except (TypeError, ValueError):
            return self.exec_timeout_seconds
        if value <= 0:
            return self.exec_timeout_seconds
        return max(1, min(value, self.max_exec_timeout_seconds))

    def audit(
        self,
        tool: str,
        *,
        ok: bool,
        duration_ms: float,
        args_summary: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        entry = {
            "ts": time.time(),
            "tool": tool,
            "ok": bool(ok),
            "duration_ms": round(float(duration_ms), 2),
        }
        if args_summary:
            entry["args"] = args_summary
        if error:
            entry["error"] = str(error)[:500]
        try:
            if self._audit_path.exists() and self._audit_path.stat().st_size > AUDIT_MAX_BYTES:
                backup = self._audit_path.with_suffix(".jsonl.1")
                try:
                    os.replace(self._audit_path, backup)
                except OSError:
                    pass
            self._audit_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self._audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass
