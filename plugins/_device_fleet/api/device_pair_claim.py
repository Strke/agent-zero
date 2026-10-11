from __future__ import annotations

import json
from typing import Any

from flask import Response

from helpers.api import ApiHandler

from plugins._device_fleet.helpers import pairing as pairing_module

_STATUS_BY_CODE = {
    "INVALID_CODE": 404,
    "EXPIRED_CODE": 404,
    "TOO_MANY_ATTEMPTS": 429,
    "RATE_LIMITED": 429,
    "MAX_DEVICES": 409,
    "TOO_MANY_CODES": 429,
    "CODE_GENERATION_FAILED": 500,
}

_RUNTIME_FIELDS = ("os", "arch", "hostname", "user", "python", "node_version")
_RUNTIME_VALUE_MAX = 200
_MAX_TOOLS = 64
_MAX_TOOL_NAME = 64
_MAX_POLICY_BYTES = 16 * 1024
_POLICY_ROOT_MAX = 512


def _clean_policy(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    policy: dict[str, Any] = {}
    policy["exec_enabled"] = bool(raw.get("exec_enabled"))
    policy["read_only"] = bool(raw.get("read_only"))
    roots_raw = raw.get("roots")
    if isinstance(roots_raw, list):
        roots = []
        for entry in roots_raw[:16]:
            text = str(entry or "").strip()[:_POLICY_ROOT_MAX]
            if text:
                roots.append(text)
        policy["roots"] = roots
    return policy


class DevicePairClaim(ApiHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    @classmethod
    def requires_api_key(cls) -> bool:
        return False

    async def process(self, input: dict[str, Any], request: Any) -> Response:
        code = str(input.get("code") or "")
        name = str(input.get("name") or "").strip()

        runtime: dict[str, str] = {}
        raw_runtime = input.get("runtime")
        if isinstance(raw_runtime, dict):
            for key in _RUNTIME_FIELDS:
                value = str(raw_runtime.get(key) or "").strip()[:_RUNTIME_VALUE_MAX]
                if value:
                    runtime[key] = value

        policy = _clean_policy(input.get("policy"))
        if policy is not None and len(json.dumps(policy)) > _MAX_POLICY_BYTES:
            policy = None

        tools: list[str] | None = None
        tools_raw = input.get("tools")
        if isinstance(tools_raw, list):
            tools = [
                str(entry).strip()[:_MAX_TOOL_NAME]
                for entry in tools_raw[:_MAX_TOOLS]
                if str(entry).strip()
            ]

        try:
            result, error = pairing_module.get_default().claim(
                code,
                name=name or str(runtime.get("hostname") or "device"),
                runtime=runtime,
                policy=policy,
                tools=tools,
            )
        except pairing_module.PairingError as exc:
            result, error = None, {"code": exc.code, "message": exc.message}

        if error is not None:
            status = _STATUS_BY_CODE.get(str(error.get("code")), 400)
            return Response(
                json.dumps({"ok": False, "error": error}),
                status=status,
                mimetype="application/json",
            )

        return Response(
            json.dumps({"ok": True, **(result or {})}),
            status=201,
            mimetype="application/json",
        )
