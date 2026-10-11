# Device Fleet Plugin DOX

## Purpose

- Own the bundled multi-device hub: pairing, persistent device registry, device WebSocket bridge, fleet prompt awareness, and agent-facing device tools.
- Keep the device side (`node/`) an auditable pure-Python agent that runs on controlled machines without inbound ports.

## Ownership

- `api/` owns the device WebSocket handler (`ws_device.py`) and the public pairing claim endpoint (`device_pair_claim.py`).
- `helpers/` owns protocol constants/validation, registry persistence, pairing codes, the live session hub, and fleet prompt rendering.
- `tools/` owns `device_list`, `device_call`, and `device_manage` agent tools.
- `prompts/` owns tool prompts (`agent.system.tool.device_*.md`) and the fleet awareness template (`agent.system.device_fleet.md`).
- `extensions/` owns system-prompt awareness injection and conditional tool-prompt gating.
- `node/` owns the a0-node agent installed on devices.
- `skills/setup-device-node/` owns the agent-executable provisioning workflow.

## Local Contracts

- Device WebSocket events use the `node_*` prefix and the Socket.IO `/ws` namespace; the handler is activated through `auth.handlers` path `plugins/_device_fleet/ws_device`.
- The handler keeps `requires_auth`, `requires_csrf`, and `requires_api_key` disabled; every non-hello event is ignored until `node_hello` authenticates a per-device token, and hello failures are rate limited per SID.
- Pairing codes are single-use, expiring, high-entropy, and the only credential a new device needs besides the pairing flow; device tokens are stored hashed in `usr/devices/registry.json` (0600) and compared with `hmac.compare_digest`.
- `device_call` must validate the device, online state, advertised tool name, argument object, and timeout bounds before invoking the hub.
- Fleet prompt text appears only when at least one device is paired; `device_list` and `device_call` tool prompts are removed from the tools prompt when the registry is empty, while `device_manage` stays visible so pairing can start on a fresh install.
- Never log or echo device tokens; `device_manage` output may contain pairing codes only.
- The node refuses plain `http` hub URLs for non-loopback hosts and never opens inbound listeners.

## Work Guidance

- Keep protocol changes paired between hub (`helpers/protocol.py`, `api/ws_device.py`) and node (`node/a0_node/`).
- Preserve the separation between transport concerns (Socket.IO envelope) and tool payloads (`node_tool_*` events).
- Prefer extending the node tool list over widening existing tool semantics; update `ops.TOOL_DESCRIPTORS` and prompts together.

## Verification

- Run `pytest tests/test_device_fleet_*.py` after any change here.
- For tool or prompt changes, run the related A0 tests (`tests/test_tool_policy.py`, `tests/test_tools_prompt*` when present).
- For node changes, run the node policy/ops tests that import `plugins/_device_fleet/node`.

## Child DOX Index

No child DOX files.
