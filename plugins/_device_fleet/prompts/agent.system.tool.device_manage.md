### device_manage
manage the device fleet: pairing codes, removal, and enable/disable
args: `action`, plus action-specific fields
- `pair_start`: create a single-use pairing code; optional `name` hint and `hub_url`; returns the join command for the new device
- `remove`: `device` (id or exact name); revokes the token and disconnects the device
- `enable` / `disable`: `device` (id or exact name)
- `list`: current devices and status
notes:
- a pairing code is the only credential a new device needs besides network access; share it only with the intended machine owner
- after the node is started on the device it appears in device_list and in the device fleet system prompt
- removing a device requires pairing it again to restore access
example:
~~~json
{
  "thoughts": ["The user wants to add their laptop to the fleet."],
  "headline": "Creating pairing code",
  "tool_name": "device_manage",
  "tool_args": {"action": "pair_start", "name": "laptop"}
}
~~~
