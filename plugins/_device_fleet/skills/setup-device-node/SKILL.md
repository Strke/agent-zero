---
name: setup-device-node
description: Pair a computer or server with Device Fleet by installing the a0-node agent.
---

# Setup Device Node

## Boundary

Use this skill only to pair machines the user explicitly owns or administers. The node grants remote file access and, when explicitly enabled, remote command execution on that machine.

Never enable `--enable-exec` without the user asking for remote command execution on that device.

## 1. Get a pairing code

Call `device_manage` with `action=pair_start`, a short `name`, and the `hub_url` the device can reach:

- same machine or LAN: the exact URL the user opens Agent Zero with
- remote machine: the public Tunnel URL

Never guess the URL or port; ask the user to copy it from their browser when unsure.

## 2. Install the node on the target machine

The node source lives in the Agent Zero installation at `plugins/_device_fleet/node`.
Use `python3` 3.10+. If Agent Zero can reach the machine over SSH, use the code
execution tool to copy the folder and run the commands there; otherwise give the
user the commands to run on that machine.

```bash
# on the target machine, inside the copied node/ directory
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## 3. Join the hub

Run join with the code from step 1. The hub URL must be `https` unless it points
to localhost.

```bash
python -m a0_node join --hub <AGENT ZERO URL> --code <CODE> --name <NAME>
```

Optional device policies, chosen with the user:

- `--enable-exec` allow remote shell commands (default: disabled)
- `--workspace PATH` change the writable workspace (default: `~/a0-workspace`)
- `--add-root PATH` add another allowed root (repeatable)
- `--read-only` reject all writes

## 4. Keep it running

```bash
python -m a0_node run                 # foreground test
python -m a0_node install-service     # background service instructions
```

Follow the printed service commands (`systemctl --user`, `launchctl`, or
`schtasks`) to enable it.

## 5. Verify

Call `device_list`. The device should appear as online. Then test with
`device_call`, for example `system_info`, or `read_file` on a small path inside
its allowed roots.

## Troubleshooting

- Not in `device_list`: the node is not connected. Check the node output and that
  the hub URL matches the address in the browser exactly.
- `AUTH_FAILED`: the device was removed or disabled; create a new pairing code.
- `refusing plain http for a non-loopback hub`: use the Tunnel URL or an HTTPS
  address.
- `EXEC_DISABLED`: command execution is off for that device; re-join with
  `--enable-exec` only if the user wants it.
- `PATH_OUTSIDE_ROOTS`: the path is outside the device's allowed roots; use an
  allowed path or re-join with `--add-root`.
