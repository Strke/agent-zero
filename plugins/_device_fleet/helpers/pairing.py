from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from plugins._device_fleet.helpers import registry as registry_module

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_GROUP_SIZE = 5
CODE_GROUPS = 2
CLAIM_RATE_WINDOW_SECONDS = 60.0


@dataclass
class PairingCode:
    code: str
    created_at: float
    expires_at: float
    attempts: int = 0


class PairingError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _canonical(code: str) -> str:
    cleaned = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    if len(cleaned) != CODE_GROUP_SIZE * CODE_GROUPS:
        return ""
    groups = [
        cleaned[index : index + CODE_GROUP_SIZE]
        for index in range(0, len(cleaned), CODE_GROUP_SIZE)
    ]
    return "-".join(groups)


def _generate_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_GROUP_SIZE * CODE_GROUPS))
    return "-".join(
        raw[index : index + CODE_GROUP_SIZE]
        for index in range(0, len(raw), CODE_GROUP_SIZE)
    )


class PairingStore:
    def __init__(
        self,
        registry: registry_module.DeviceRegistry,
        *,
        ttl_seconds: int = 600,
        max_attempts: int = 5,
        max_active: int = 8,
        max_claims_per_minute: int = 10,
        max_devices: int = 32,
    ) -> None:
        self.registry = registry
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.max_active = max(1, int(max_active))
        self.max_claims_per_minute = max(1, int(max_claims_per_minute))
        self.max_devices = max(1, int(max_devices))
        self._codes: dict[str, PairingCode] = {}
        self._claim_times: deque[float] = deque()
        self._lock = threading.RLock()

    def _prune_locked(self, now: float) -> None:
        expired = [code for code, entry in self._codes.items() if now > entry.expires_at]
        for code in expired:
            self._codes.pop(code, None)
        while self._claim_times and now - self._claim_times[0] > CLAIM_RATE_WINDOW_SECONDS:
            self._claim_times.popleft()

    def create_code(self) -> PairingCode:
        with self._lock:
            now = time.time()
            self._prune_locked(now)
            if len(self._codes) >= self.max_active:
                raise PairingError(
                    "TOO_MANY_CODES",
                    "too many active pairing codes; wait for one to expire",
                    status=429,
                )
            for _ in range(10):
                code = _generate_code()
                if code not in self._codes:
                    break
            else:
                raise PairingError(
                    "CODE_GENERATION_FAILED", "could not allocate a pairing code", status=500
                )
            entry = PairingCode(
                code=code,
                created_at=now,
                expires_at=now + self.ttl_seconds,
            )
            self._codes[code] = entry
            return entry

    def active_codes(self) -> list[PairingCode]:
        with self._lock:
            self._prune_locked(time.time())
            return list(self._codes.values())

    def claim(
        self,
        code: str,
        *,
        name: str,
        runtime: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        tools: list[str] | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        canonical = _canonical(code)
        if not canonical:
            return None, {
                "code": "INVALID_CODE",
                "message": "pairing code format is invalid",
            }

        with self._lock:
            now = time.time()
            self._prune_locked(now)

            self._claim_times.append(now)
            recent = [ts for ts in self._claim_times if now - ts <= CLAIM_RATE_WINDOW_SECONDS]
            if len(recent) > self.max_claims_per_minute:
                return None, {
                    "code": "RATE_LIMITED",
                    "message": "too many pairing attempts; try again later",
                }

            entry = self._codes.get(canonical)
            if entry is None:
                return None, {
                    "code": "INVALID_CODE",
                    "message": "pairing code is unknown, expired, or already used",
                }
            if now > entry.expires_at:
                self._codes.pop(canonical, None)
                return None, {
                    "code": "EXPIRED_CODE",
                    "message": "pairing code has expired",
                }

            entry.attempts += 1
            if entry.attempts > self.max_attempts:
                self._codes.pop(canonical, None)
                return None, {
                    "code": "TOO_MANY_ATTEMPTS",
                    "message": "pairing code was invalidated after too many attempts",
                }

            if self.registry.count() >= self.max_devices:
                return None, {
                    "code": "MAX_DEVICES",
                    "message": "device limit reached; remove a device before pairing a new one",
                }

            device, token = self.registry.create_device(
                name=name,
                runtime=runtime,
                policy=policy,
                tools=tools,
            )
            self._codes.pop(canonical, None)

        return {
            "device_id": device["id"],
            "device_token": token,
            "device_name": device["name"],
            "protocol_version": 1,
        }, None


_default: PairingStore | None = None
_default_lock = threading.Lock()


def get_default() -> PairingStore:
    global _default
    with _default_lock:
        if _default is None:
            _default = PairingStore(registry_module.get_default())
        return _default


def set_default(store: PairingStore | None) -> None:
    global _default
    with _default_lock:
        _default = store
