# a0-node

Auditable Python device agent for Agent Zero's Device Fleet. Copy this folder to a
machine you want Agent Zero to operate; the node connects outbound to Agent Zero
over WebSocket and exposes a small, permission-bound tool set.

## Install

Requires Python 3.10+.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Join

Create a pairing code in Agent Zero (`device_manage` action `pair_start`), then:

```bash
python -m a0_node join --hub https://your-agent-zero-host --code XXXXX-XXXXX --name my-server
```

Useful options:

- `--enable-exec` allow remote shell commands (default: disabled)
- `--workspace PATH` change the writable workspace (default: `~/a0-workspace`)
- `--add-root PATH` add another allowed root (repeatable)
- `--read-only` reject all writes
- `--allow-plain-http` allow a plain `http` hub URL on a non-loopback host; use
  only when the transport is already encrypted, for example over Tailscale

The hub URL must be `https` unless it points to localhost or
`--allow-plain-http` is set.

## Run

```bash
python -m a0_node run
```

Install as a background service:

```bash
python -m a0_node install-service
```

The service templates use `systemd` (Linux), `launchd` (macOS), or a
`Register-ScheduledTask` PowerShell command (Windows, logon trigger with
automatic restart after a crash). The displayed commands are printed; nothing
runs in the background until you execute them.

The node never gives up on the hub: if Agent Zero is offline, the process keeps
waiting with exponential backoff (1s up to 30s) and reconnects automatically
once the hub is back, even if the hub was restarted. If the device is rejected
because it was removed or disabled, the node keeps retrying and logs how to
pair it again.

## Security model

- No inbound listener; one outbound WSS/WS connection.
- Per-device token issued during pairing, stored in `~/.a0-node/config.json` (0600).
- File access is restricted to configured roots; symlink escapes are rejected.
- Command execution is opt-in; commands run as the current user with timeouts,
  output caps, process-group termination, and an audit log (`~/.a0-node/audit.jsonl`).
- The source is plain Python; review it before installing.

## Tools exposed to Agent Zero

`system_info`, `exec_command`, `list_dir`, `read_file`, `write_file`,
`search_text`, `move_file`, `delete_file`.

## Troubleshooting

- `connection rejected: AUTH_FAILED` — the device was removed or disabled in
  Agent Zero; pair it again.
- `refusing plain http for a non-loopback hub` — expose Agent Zero over HTTPS
  (for example through its Tunnel) or use localhost.
- Origin errors on connect — make sure `--hub` exactly matches the address you
  open in the browser.
