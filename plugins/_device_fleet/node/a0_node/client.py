from __future__ import annotations

import random
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import socketio

from . import (
    EVENT_HEARTBEAT,
    EVENT_HELLO,
    EVENT_TOOL_CANCEL,
    EVENT_TOOL_ERROR,
    EVENT_TOOL_INVOKE,
    EVENT_TOOL_RESULT,
    HANDLER_PATH,
    NAMESPACE,
    NODE_VERSION,
    PROTOCOL_VERSION,
)
from .ops import TOOL_DESCRIPTORS, ExecContext, args_summary, dispatch
from .policy import Policy, PolicyError

PRINT_PREFIX = "[a0-node]"
INVOKE_WORKERS = 4
STABLE_SESSION_SECONDS = 30.0
BACKOFF_MAX_SECONDS = 30.0


def log(message: str) -> None:
    print(f"{PRINT_PREFIX} {message}", flush=True)


class HandshakeRejected(Exception):
    def __init__(self, code: str, message: str, *, category: str = "") -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.category = category


def _handshake_hint(exc: HandshakeRejected) -> str:
    if exc.code == "AUTH_FAILED" or exc.category == "auth":
        return (
            "the device may have been removed or disabled on the hub; if this is "
            "unexpected, create a new pairing code and run join again; the node keeps "
            "waiting and retrying in the meantime"
        )
    if exc.code == "PROTOCOL_MISMATCH" or exc.category == "protocol":
        return (
            "hub and node protocol versions do not match; update the node source, "
            "then restart the node; retrying in the meantime"
        )
    return ""


def build_hello(config: dict[str, Any], policy: Policy) -> dict[str, Any]:
    import platform
    import socket as socket_module

    return {
        "protocol_version": PROTOCOL_VERSION,
        "device": {
            "id": config["device_id"],
            "name": config.get("name") or socket_module.gethostname(),
            "token": config["device_token"],
        },
        "runtime": {
            "os": platform.system().lower(),
            "arch": platform.machine(),
            "hostname": socket_module.gethostname(),
            "python": platform.python_version(),
            "node_version": NODE_VERSION,
        },
        "capabilities": {"tools": [descriptor["name"] for descriptor in TOOL_DESCRIPTORS]},
        "tools": TOOL_DESCRIPTORS,
        "policy": {
            "exec_enabled": policy.exec_enabled,
            "read_only": policy.read_only,
            "roots": [str(root) for root in policy.roots],
        },
    }


def _ack_data(ack: Any) -> dict[str, Any] | None:
    if not isinstance(ack, dict):
        return None
    results = ack.get("results")
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict) and first.get("ok"):
            data = first.get("data")
            return data if isinstance(data, dict) else None
    return None


def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, dict) and ("request_id" in data or "tool" in data):
        return data
    return payload


class A0NodeClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.policy = Policy(config)
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(
            max_workers=INVOKE_WORKERS, thread_name_prefix="a0-node-op"
        )
        self._lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._heartbeat_stop: threading.Event | None = None
        self._sio: socketio.Client | None = None

    def request_stop(self) -> None:
        self._stop.set()
        sio = self._sio
        if sio is not None:
            try:
                sio.disconnect()
            except Exception:
                pass

    def close(self) -> None:
        self.request_stop()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def run_forever(self) -> int:
        backoff = 1.0
        while not self._stop.is_set():
            connected_seconds = 0.0
            try:
                connected_seconds = self._serve()
            except HandshakeRejected as exc:
                log(f"hub rejected the handshake: {exc}")
                hint = _handshake_hint(exc)
                if hint:
                    log(hint)
            except Exception as exc:
                log(f"connection error: {exc}")
            if connected_seconds >= STABLE_SESSION_SECONDS:
                backoff = 1.0
            if self._stop.wait(backoff + random.uniform(0.0, 0.5)):
                break
            backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)
        return 0

    def _serve(self) -> float:
        sio = socketio.Client(logger=False, engineio_logger=False, reconnection=False)
        self._sio = sio

        @sio.on(EVENT_TOOL_INVOKE, namespace=NAMESPACE)
        def _on_invoke(payload: Any) -> None:
            self._handle_invoke(payload if isinstance(payload, dict) else {})

        @sio.on(EVENT_TOOL_CANCEL, namespace=NAMESPACE)
        def _on_cancel(payload: Any) -> None:
            self._handle_cancel(payload if isinstance(payload, dict) else {})

        @sio.on("disconnect", namespace=NAMESPACE)
        def _on_disconnect(reason: Any = None) -> None:
            log("disconnected from hub")

        try:
            sio.connect(
                self.config["hub_url"],
                auth={"handlers": [HANDLER_PATH]},
                namespaces=[NAMESPACE],
                transports=["websocket"],
                wait_timeout=15,
            )
        except Exception as exc:
            self._sio = None
            raise ConnectionError(str(exc)) from exc

        connected_at = 0.0
        try:
            ack = sio.call(
                EVENT_HELLO,
                build_hello(self.config, self.policy),
                namespace=NAMESPACE,
                timeout=20,
            )
            payload = _ack_data(ack)
            if not isinstance(payload, dict) or not payload.get("ok"):
                error = payload.get("error") if isinstance(payload, dict) else None
                error = error if isinstance(error, dict) else {}
                code = str(error.get("code") or "HANDSHAKE_FAILED")
                message = str(error.get("message") or "hub rejected the handshake")
                category = str(error.get("category") or "")
                raise HandshakeRejected(code, message, category=category)

            connected_at = time.monotonic()
            ready = payload.get("data") or {}
            heartbeat = int(ready.get("heartbeat_seconds") or 30)
            log(
                f"connected to {self.config['hub_url']} as {self.config.get('name')} "
                f"(tools: {len(TOOL_DESCRIPTORS)})"
            )
            self._start_heartbeat(sio, heartbeat)
            sio.wait()
        finally:
            self._stop_heartbeat()
            try:
                sio.disconnect()
            except Exception:
                pass
            self._sio = None
        return max(0.0, time.monotonic() - connected_at)

    def _start_heartbeat(self, sio: socketio.Client, interval_seconds: int) -> None:
        stop = threading.Event()
        self._heartbeat_stop = stop

        def loop() -> None:
            while not stop.wait(max(5, interval_seconds)):
                try:
                    sio.emit(
                        EVENT_HEARTBEAT,
                        {"protocol_version": PROTOCOL_VERSION, "time": time.time()},
                        namespace=NAMESPACE,
                    )
                except Exception:
                    return

        threading.Thread(target=loop, name="a0-node-heartbeat", daemon=True).start()

    def _stop_heartbeat(self) -> None:
        stop = self._heartbeat_stop
        if stop is not None:
            stop.set()
            self._heartbeat_stop = None

    def _handle_invoke(self, payload: dict[str, Any]) -> None:
        data = _unwrap(payload)
        request_id = str(data.get("request_id") or "")
        tool = str(data.get("tool") or "")
        arguments = data.get("arguments")
        if not request_id or not tool:
            return
        if not isinstance(arguments, dict):
            arguments = {}
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[request_id] = cancel_event
        self._executor.submit(self._run_tool, request_id, tool, arguments, cancel_event)

    def _handle_cancel(self, payload: dict[str, Any]) -> None:
        data = _unwrap(payload)
        request_id = str(data.get("request_id") or "")
        if not request_id:
            return
        cancel_event = None
        process = None
        with self._lock:
            cancel_event = self._cancel_events.get(request_id)
            process = self._processes.get(request_id)
        if cancel_event is not None:
            cancel_event.set()
        if process is not None:
            try:
                import os
                import signal

                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                        capture_output=True,
                        check=False,
                    )
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except Exception:
                pass

    def _register_process(self, request_id: str, proc: subprocess.Popen | None) -> None:
        with self._lock:
            if proc is None:
                self._processes.pop(request_id, None)
                return
            self._processes[request_id] = proc
            cancel_event = self._cancel_events.get(request_id)
        if cancel_event is not None and cancel_event.is_set():
            try:
                proc.kill()
            except Exception:
                pass

    def _run_tool(
        self,
        request_id: str,
        tool: str,
        arguments: dict[str, Any],
        cancel_event: threading.Event,
    ) -> None:
        started = time.monotonic()
        ok = False
        error_text: str | None = None
        context = ExecContext(
            cancel_event=cancel_event,
            register_process=lambda proc: self._register_process(request_id, proc),
        )
        try:
            result = dispatch(self.policy, tool, arguments, context)
            ok = True
            self._emit(
                EVENT_TOOL_RESULT,
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": request_id,
                    "result": result,
                },
            )
        except PolicyError as exc:
            error_text = exc.message
            self._emit(
                EVENT_TOOL_ERROR,
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": request_id,
                    "error": exc.payload(),
                },
            )
        except Exception as exc:
            error_text = str(exc)
            self._emit(
                EVENT_TOOL_ERROR,
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": request_id,
                    "error": {
                        "code": "TOOL_CRASHED",
                        "message": str(exc),
                        "category": "internal",
                        "retryable": False,
                    },
                },
            )
        finally:
            duration = (time.monotonic() - started) * 1000
            self.policy.audit(
                tool,
                ok=ok,
                duration_ms=duration,
                args_summary=args_summary(tool, arguments),
                error=error_text,
            )
            with self._lock:
                self._cancel_events.pop(request_id, None)
                self._processes.pop(request_id, None)

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        sio = self._sio
        if sio is None:
            return
        try:
            sio.emit(event, data, namespace=NAMESPACE)
        except Exception:
            pass
