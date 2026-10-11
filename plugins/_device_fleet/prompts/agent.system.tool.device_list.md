### device_list
list devices paired with this Agent Zero instance with online status, operating system, and advertised capabilities
args: none
use it before operating devices whenever the inventory or status may have changed
example:
~~~json
{
  "thoughts": ["I need to see which devices are available before reading files."],
  "headline": "Listing device fleet",
  "tool_name": "device_list",
  "tool_args": {}
}
~~~
