from __future__ import annotations

NODE_VERSION = "0.1.0"
PROTOCOL_VERSION = 1
NAMESPACE = "/ws"
HANDLER_PATH = "plugins/_device_fleet/ws_device"

EVENT_HELLO = "node_hello"
EVENT_HEARTBEAT = "node_heartbeat"
EVENT_TOOL_INVOKE = "node_tool_invoke"
EVENT_TOOL_CANCEL = "node_tool_cancel"
EVENT_TOOL_RESULT = "node_tool_result"
EVENT_TOOL_ERROR = "node_tool_error"

__version__ = NODE_VERSION
