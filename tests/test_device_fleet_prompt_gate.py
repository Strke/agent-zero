import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._device_fleet.helpers import registry as registry_module
from plugins._device_fleet.helpers.registry import DeviceRegistry

GATE_PATH = (
    PROJECT_ROOT
    / "plugins"
    / "_device_fleet"
    / "extensions"
    / "python"
    / "_functions"
    / "_11_tools_prompt"
    / "build_prompt"
    / "end"
    / "_80_device_tools_gate.py"
)

PROMPTS = {
    "agent.system.tool.device_list.md": "### device_list\nlist devices",
    "agent.system.tool.device_call.md": "### device_call\ncall a device",
}


class _GateAgent:
    def read_prompt(self, file: str, **kwargs) -> str:
        return PROMPTS.get(file, "")


def _load_gate_class():
    spec = importlib.util.spec_from_file_location(
        "test_device_fleet_gate", GATE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.DeviceToolsGate


def _result_text() -> str:
    return "\n\n".join(PROMPTS.values()) + "\n\n### other_tool\nstays"


def test_gate_removes_prompts_without_devices(tmp_path: Path) -> None:
    registry_module.set_default(DeviceRegistry(tmp_path))
    try:
        gate = _load_gate_class()(agent=_GateAgent())
        data = {"result": _result_text()}
        gate.execute(data=data)
        assert "### device_list" not in data["result"]
        assert "### device_call" not in data["result"]
        assert "### other_tool" in data["result"]
    finally:
        registry_module.set_default(None)


def test_gate_keeps_prompts_with_devices(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    registry.create_device(name="server-01")
    registry_module.set_default(registry)
    try:
        gate = _load_gate_class()(agent=_GateAgent())
        data = {"result": _result_text()}
        gate.execute(data=data)
        assert "### device_list" in data["result"]
        assert "### device_call" in data["result"]
    finally:
        registry_module.set_default(None)
