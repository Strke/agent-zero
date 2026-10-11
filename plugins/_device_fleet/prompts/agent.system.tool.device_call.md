### device_call
run one tool on a paired remote device and return its data or resources
args: `device`, `tool`, `arguments`, optional `timeout_seconds`
- `device`: device id or exact name from device_list
- `tool`: remote tool name advertised by that device, e.g. `system_info`, `exec_command`, `list_dir`, `read_file`, `write_file`, `search_text`, `move_file`, `delete_file`
- `arguments`: json object with the arguments for the remote tool; required
- `timeout_seconds`: optional deadline override, bounded by configuration
notes:
- `exec_command` is disabled on devices unless the owner enabled it at install time; the error explains when it is off
- `read_file` returns utf-8 text with `next_offset` continuation for larger files; writes are limited in size per call
- offline devices fail fast; call device_list if unsure which devices are online
example:
~~~json
{
  "thoughts": ["I need the OS release of server-01."],
  "headline": "Reading remote file",
  "tool_name": "device_call",
  "tool_args": {
    "device": "server-01",
    "tool": "read_file",
    "arguments": {"path": "/etc/os-release"}
  }
}
~~~
