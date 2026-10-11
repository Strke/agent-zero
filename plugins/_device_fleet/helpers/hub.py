from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from plugins._device_fleet.helpers import protocol

Emitter = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class HelloError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        category: str = protocol.ERROR_VALIDATION,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.category = category
        self.retryable = retryable

    def payload(self) -> dict[str, Any]:
        return protocol.error_payload(
            self.code, self.message, category=self.category, retryable=self.retryable
        )


@dataclass
class PendingInvoke:
    request_id: str
    sid: str
    tool: str
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future
    timeout_seconds: float
    created_at: float


@dataclass
class DeviceSession:
    sid: str
    device_id: str
    name: str
    runtime: dict[str, Any]
    tools: dict[str, dict[str, Any]]
    tool_names: list[str]
    policy: dict[str, Any]
    connected_at: float
    last_heartbeat: float
    heartbeat_seconds: int = 30
    in_flight: dict[str, PendingInvoke] = field(default_factory=dict)

    def is_stale(self, now: float | None = None) -> bool:
        moment = now if now is not None else time.time()
        stale_after = max(90.0, self.heartbeat_seconds * 3.0)
        return moment - self.last_heartbeat > stale_after


def _set_future_result(future: asyncio.Future, value: Any) -> None:
    if not future.done():
        future.set_result(value)


async def _manager_emitter(sid: str, event: str, data: dict[str, Any]) -> None:
    from helpers.ws_manager import get_shared_ws_manager

    manager = get_shared_ws_manager()
    await manager.emit_to(
        protocol.NAMESPACE,
        sid,
        event,
        data,
        handler_id=protocol.HANDLER_ID,
        max_payload_bytes=protocol.MAX_FRAME_BYTES,
    )


class DeviceHub:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, DeviceSession] = {}
        self._sid_index: dict[str, str] = {}
        self._emitter: Emitter | None = None

    def set_emitter(self, emitter: Emitter | None) -> None:
        self._emitter = emitter

    async def _emit(self, sid: str, event: str, data: dict[str, Any]) -> None:
        emitter = self._emitter or _manager_emitter
        await emitter(sid, event, data)

    def register_hello(
        self,
        sid: str,
        *,
        device: dict[str, Any],
        hello: dict[str, Any],
        heartbeat_seconds: int = 30,
    ) -> dict[str, Any]:
        device_id = str(device.get("id", ""))
        if not device_id:
            raise HelloError("INVALID_DEVICE", "device record is missing an id")

        now = time.time()
        tools = {
            str(descriptor.get("name")): descriptor
            for descriptor in hello.get("tools", [])
            if isinstance(descriptor, dict) and descriptor.get("name")
        }
        session = DeviceSession(
            sid=sid,
            device_id=device_id,
            name=str(device.get("name") or hello.get("device", {}).get("name") or "device"),
            runtime=dict(hello.get("runtime") or {}),
            tools=tools,
            tool_names=list(hello.get("tool_names") or []),
            policy=dict(hello.get("policy") or {}),
            connected_at=now,
            last_heartbeat=now,
            heartbeat_seconds=max(5, int(heartbeat_seconds)),
        )

        with self._lock:
            existing = self._sessions.get(device_id)
            if existing is not None and existing.sid != sid:
                self._fail_session_locked(
                    existing, "device reconnected from a newer session"
                )
                self._sessions.pop(device_id, None)
                self._sid_index.pop(existing.sid, None)
            self._sessions[device_id] = session
            self._sid_index[sid] = device_id

        return {
            "protocol_version": protocol.PROTOCOL_VERSION,
            "hub_name": protocol.HUB_NAME,
            "heartbeat_seconds": session.heartbeat_seconds,
            "server_time": now,
        }

    def heartbeat(self, sid: str) -> bool:
        with self._lock:
            device_id = self._sid_index.get(sid)
            session = self._sessions.get(device_id) if device_id else None
            if session is None:
                return False
            session.last_heartbeat = time.time()
            return True

    def resolve_result(self, sid: str, payload: dict[str, Any]) -> bool:
        return self._resolve(
            sid, payload, lambda pending: {"ok": True, "result": payload.get("result")}
        )

    def resolve_error(self, sid: str, payload: dict[str, Any]) -> bool:
        raw_error = payload.get("error")
        if isinstance(raw_error, dict):
            error = {
                "code": str(raw_error.get("code") or "TOOL_FAILED"),
                "message": str(raw_error.get("message") or "device tool failed"),
                "category": str(raw_error.get("category") or protocol.ERROR_EXECUTION),
                "retryable": bool(raw_error.get("retryable")),
            }
            if isinstance(raw_error.get("details"), dict):
                error["details"] = raw_error["details"]
        else:
            error = protocol.error_payload(
                "TOOL_FAILED", str(raw_error or "device tool failed")
            )
        return self._resolve(sid, payload, lambda pending: {"ok": False, "error": error})

    def _resolve(
        self, sid: str, payload: dict[str, Any], factory: Callable[[PendingInvoke], dict]
    ) -> bool:
        request_id = str(payload.get("request_id") or "")
        if not request_id:
            return False
        with self._lock:
            device_id = self._sid_index.get(sid)
            session = self._sessions.get(device_id) if device_id else None
            if session is None:
                return False
            pending = session.in_flight.pop(request_id, None)
        if pending is None:
            return False
        pending.loop.call_soon_threadsafe(_set_future_result, pending.future, factory(pending))
        return True

    def disconnect(self, sid: str) -> None:
        with self._lock:
            device_id = self._sid_index.pop(sid, None)
            if device_id is None:
                return
            session = self._sessions.get(device_id)
            if session is not None and session.sid == sid:
                self._sessions.pop(device_id, None)
                self._fail_session_locked(session, "device disconnected")

    def disconnect_device(self, device_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(device_id, None)
            if session is None:
                return False
            self._sid_index.pop(session.sid, None)
            self._fail_session_locked(session, "device was removed or disabled")
            return True

    def _fail_session_locked(self, session: DeviceSession, reason: str) -> None:
        pending = list(session.in_flight.values())
        session.in_flight.clear()
        error = protocol.error_payload(
            "DEVICE_OFFLINE",
            reason,
            category=protocol.ERROR_OFFLINE,
            retryable=True,
        )
        for entry in pending:
            entry.loop.call_soon_threadsafe(
                _set_future_result, entry.future, {"ok": False, "error": error}
            )

    def get_session(self, device_id: str) -> DeviceSession | None:
        with self._lock:
            return self._sessions.get(device_id)

    def session_for_sid(self, sid: str) -> DeviceSession | None:
        with self._lock:
            device_id = self._sid_index.get(sid)
            return self._sessions.get(device_id) if device_id else None

    def is_online(self, device_id: str) -> bool:
        session = self.get_session(device_id)
        return bool(session) and not session.is_stale()

    def online_ids(self) -> set[str]:
        now = time.time()
        with self._lock:
            return {
                device_id
                for device_id, session in self._sessions.items()
                if not session.is_stale(now)
            }

    async def invoke(
        self,
        device_id: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float,
        max_concurrent: int = 4,
    ) -> dict[str, Any]:
        session = self.get_session(device_id)
        if session is None or session.is_stale():
            return {
                "ok": False,
                "error": protocol.error_payload(
                    "DEVICE_OFFLINE",
                    "device is not connected; check device_list",
                    category=protocol.ERROR_OFFLINE,
                    retryable=True,
                ),
            }

        if tool not in session.tools:
            return {
                "ok": False,
                "error": protocol.error_payload(
                    "UNKNOWN_TOOL",
                    f"device does not advertise tool '{tool}'",
                    category=protocol.ERROR_VALIDATION,
                    details={"available_tools": list(session.tools)},
                ),
            }

        with self._lock:
            if len(session.in_flight) >= max(1, int(max_concurrent)):
                return {
                    "ok": False,
                    "error": protocol.error_payload(
                        "DEVICE_BUSY",
                        "device already has the maximum number of in-flight operations",
                        category=protocol.ERROR_BUSY,
                        retryable=True,
                    ),
                }
            loop = asyncio.get_running_loop()
            request_id = uuid.uuid4().hex
            pending = PendingInvoke(
                request_id=request_id,
                sid=session.sid,
                tool=tool,
                loop=loop,
                future=loop.create_future(),
                timeout_seconds=float(timeout_seconds),
                created_at=time.time(),
            )
            session.in_flight[request_id] = pending
            sid = session.sid

        timeout_seconds = max(1.0, float(timeout_seconds))
        try:
            await self._emit(
                sid,
                protocol.EVENT_TOOL_INVOKE,
                {
                    "protocol_version": protocol.PROTOCOL_VERSION,
                    "request_id": request_id,
                    "tool": tool,
                    "arguments": arguments,
                    "timeout_ms": int(timeout_seconds * 1000),
                },
            )
            return await asyncio.wait_for(pending.future, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            await self._safe_cancel(sid, request_id)
            return {
                "ok": False,
                "error": protocol.error_payload(
                    "TIMEOUT",
                    f"device did not answer '{tool}' within {timeout_seconds:.0f}s",
                    category=protocol.ERROR_TIMEOUT,
                    retryable=True,
                ),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": protocol.error_payload(
                    "TRANSPORT_ERROR",
                    f"could not reach device: {exc}",
                    category=protocol.ERROR_OFFLINE,
                    retryable=True,
                ),
            }
        finally:
            with self._lock:
                session.in_flight.pop(request_id, None)

    async def _safe_cancel(self, sid: str, request_id: str) -> None:
        try:
            await self._emit(
                sid,
                protocol.EVENT_TOOL_CANCEL,
                {"protocol_version": protocol.PROTOCOL_VERSION, "request_id": request_id},
            )
        except Exception:
            pass


_default: DeviceHub | None = None
_default_lock = threading.Lock()


def get_default() -> DeviceHub:
    global _default
    with _default_lock:
        if _default is None:
            _default = DeviceHub()
        return _default


def set_default(hub: DeviceHub | None) -> None:
    global _default
    with _default_lock:
        _default = hub
