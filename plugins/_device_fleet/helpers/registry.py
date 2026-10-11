from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

REGISTRY_VERSION = 1
DEVICE_TOKEN_BYTES = 32
DEVICE_ID_BYTES = 12


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DeviceRegistryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DeviceRegistry:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "registry.json"
        self._lock = threading.RLock()

    def _empty(self) -> dict[str, Any]:
        return {"version": REGISTRY_VERSION, "devices": {}}

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._empty()
        except OSError:
            return self._empty()
        try:
            data = json.loads(raw)
        except ValueError:
            self._quarantine_unlocked()
            return self._empty()
        if not isinstance(data, dict) or data.get("version") != REGISTRY_VERSION:
            self._quarantine_unlocked()
            return self._empty()
        if not isinstance(data.get("devices"), dict):
            data["devices"] = {}
        return data

    def _quarantine_unlocked(self) -> None:
        target = self.path.with_name(
            f"{self.path.name}.corrupt-{int(time.time())}-{secrets.token_hex(2)}"
        )
        try:
            os.replace(self.path, target)
        except OSError:
            return
        try:
            from helpers.print_style import PrintStyle

            PrintStyle.warning(
                f"[device-fleet] device registry was unreadable; "
                f"preserved a copy at {target}"
            )
        except Exception:
            pass

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.base_dir, 0o700)
        except OSError:
            pass
        fd, tmp_path = tempfile.mkstemp(
            prefix=".registry-", suffix=".tmp", dir=str(self.base_dir)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            data = self._read_unlocked()
            devices = [copy.deepcopy(entry) for entry in data["devices"].values()]
        return sorted(devices, key=lambda entry: str(entry.get("name", "")).lower())

    def count(self) -> int:
        with self._lock:
            return len(self._read_unlocked()["devices"])

    def get(self, identifier: str) -> dict[str, Any] | None:
        text = str(identifier or "").strip()
        if not text:
            return None
        with self._lock:
            data = self._read_unlocked()
            entry = data["devices"].get(text)
            if entry is not None:
                return copy.deepcopy(entry)
            lowered = text.lower()
            matches = [
                candidate
                for candidate in data["devices"].values()
                if str(candidate.get("name", "")).lower() == lowered
            ]
        if len(matches) == 1:
            return copy.deepcopy(matches[0])
        return None

    def name_matches(self, name: str) -> list[str]:
        lowered = str(name or "").strip().lower()
        if not lowered:
            return []
        with self._lock:
            data = self._read_unlocked()
            return [
                str(candidate.get("id", ""))
                for candidate in data["devices"].values()
                if str(candidate.get("name", "")).lower() == lowered
            ]

    def create_device(
        self,
        *,
        name: str,
        runtime: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        tools: list[str] | None = None,
    ) -> tuple[dict[str, Any], str]:
        token = secrets.token_urlsafe(DEVICE_TOKEN_BYTES)
        device_id = "device_" + secrets.token_urlsafe(DEVICE_ID_BYTES)
        record = {
            "id": device_id,
            "name": (str(name or "").strip() or "device")[:64],
            "token_hash": _hash_token(token),
            "created_at": time.time(),
            "last_seen": 0.0,
            "enabled": True,
            "runtime": copy.deepcopy(runtime) if isinstance(runtime, dict) else {},
            "policy": copy.deepcopy(policy) if isinstance(policy, dict) else {},
            "tools": list(tools) if isinstance(tools, list) else [],
        }
        with self._lock:
            data = self._read_unlocked()
            data["devices"][device_id] = record
            self._write_unlocked(data)
        return copy.deepcopy(record), token

    def verify_token(self, device_id: str, token: str) -> bool:
        if not device_id or not token:
            return False
        with self._lock:
            data = self._read_unlocked()
            entry = data["devices"].get(device_id)
        if entry is None:
            return False
        expected = str(entry.get("token_hash", ""))
        if not expected:
            return False
        return hmac.compare_digest(expected, _hash_token(token))

    def touch(
        self,
        device_id: str,
        *,
        runtime: dict[str, Any] | None = None,
        tools: list[str] | None = None,
        policy: dict[str, Any] | None = None,
        mark_seen: bool = True,
    ) -> dict[str, Any] | None:
        with self._lock:
            data = self._read_unlocked()
            entry = data["devices"].get(device_id)
            if entry is None:
                return None
            if mark_seen:
                entry["last_seen"] = time.time()
            if isinstance(runtime, dict) and runtime:
                entry["runtime"] = copy.deepcopy(runtime)
            if isinstance(policy, dict) and policy:
                entry["policy"] = copy.deepcopy(policy)
            if isinstance(tools, list):
                entry["tools"] = list(tools)
            self._write_unlocked(data)
            return copy.deepcopy(entry)

    def set_enabled(self, device_id: str, enabled: bool) -> bool:
        with self._lock:
            data = self._read_unlocked()
            entry = data["devices"].get(device_id)
            if entry is None:
                return False
            entry["enabled"] = bool(enabled)
            self._write_unlocked(data)
            return True

    def remove(self, device_id: str) -> bool:
        with self._lock:
            data = self._read_unlocked()
            if device_id not in data["devices"]:
                return False
            data["devices"].pop(device_id, None)
            self._write_unlocked(data)
            return True


_default: DeviceRegistry | None = None
_default_lock = threading.Lock()


def get_default() -> DeviceRegistry:
    global _default
    with _default_lock:
        if _default is None:
            from helpers import files

            base = files.get_abs_path(files.USER_DIR, "devices")
            _default = DeviceRegistry(base)
        return _default


def set_default(registry: DeviceRegistry | None) -> None:
    global _default
    with _default_lock:
        _default = registry
