# Multi-Device Control (Device Fleet)

Agent Zero can operate other computers and servers through the bundled
`_device_fleet` plugin. Each paired machine runs `a0-node`, a small auditable
Python agent that connects outbound to Agent Zero; Agent Zero then knows which
devices it has and can read their files, inspect their systems, and (when the
owner enables it) run commands on them.

## Concepts

- **Hub** — your Agent Zero instance; it keeps the device registry in
  `usr/devices/registry.json` and routes `device_call` requests.
- **Node** — the `a0-node` agent on a controlled machine
  (`plugins/_device_fleet/node`). It opens one outbound WebSocket, exposes a
  small tool set, and honors a local policy (roots, exec on/off, read-only).
- **Pairing code** — a single-use, expiring code that is the only credential a
  new device needs besides network access.

## Quick start

1. In a chat, ask Agent Zero to pair a device, or call the tool directly:

   ```json
   {"action": "pair_start", "name": "server-01"}
   ```

   Agent Zero answers with a pairing code and the `a0_node join` command.

2. On the target machine (Python 3.10+), copy the `node/` folder from
   `plugins/_device_fleet/` and install it:

   ```bash
   cd node
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Join the hub with the code from step 1:

   ```bash
   python -m a0_node join --hub https://your-agent-zero --code XXXXX-XXXXX --name server-01
   python -m a0_node install-service
   ```

4. Verify in chat with `device_list`; the device appears with its operating
   system and advertised tools.

## Using devices

- `device_list` — inventory with online status and capabilities.
- `device_call` — run one tool on one device:

  ```json
  {
    "device": "server-01",
    "tool": "read_file",
    "arguments": {"path": "/etc/os-release"}
  }
  ```

- `device_manage` — `pair_start`, `remove`, `enable`, `disable`, `list`.

While at least one device is paired, the agent's system prompt includes a
device fleet section, so the agent is aware of its devices without asking.

## Node tools

| Tool | Purpose |
| --- | --- |
| `system_info` | OS, hostname, user, allowed roots, disk usage |
| `exec_command` | Run a shell command (disabled by default) |
| `list_dir` | List files with depth/glob filters |
| `read_file` | Read UTF-8 text with offset/limit continuation |
| `write_file` | Atomic writes with optional sha256 precondition |
| `search_text` | Regex search across text files |
| `move_file` | Move/rename inside allowed roots |
| `delete_file` | Delete files; directories need `recursive=true` |

## Security model

- One outbound WebSocket per device; no inbound ports.
- Per-device tokens are stored hashed on the hub; removing a device revokes it.
- The hub URL must be HTTPS unless it is localhost.
- File access is restricted to configured roots; `..` and symlink escapes are
  rejected.
- Command execution is opt-in per device; commands run as the user that started
  the node, with timeouts, output caps, process-group termination, and an audit
  log at `~/.a0-node/audit.jsonl`.
- The node is plain Python source; review it before installing.

## Configuration

Plugin defaults live in `plugins/_device_fleet/default_config.yaml` and can be
overridden in the plugin settings UI: tool timeout, heartbeat, device limit,
pairing code TTL, offline device visibility in the prompt, and result size.

## Troubleshooting

- Device never appears: check the node output; the URL must exactly match the
  address used in the browser, and `AUTH_FAILED` means the pairing was revoked
  or the device was disabled.
- `DEVICE_OFFLINE`: the node is not connected; start it or check the network.
- `EXEC_DISABLED`: re-join the device with `--enable-exec` if the user wants
  command execution.
- `PATH_OUTSIDE_ROOTS`: extend the device roots with `--add-root` or use an
  allowed path.
