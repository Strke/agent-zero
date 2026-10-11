from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

from .config import node_home

SERVICE_NAME = "a0-node"


def _run_command() -> str:
    return f"{sys.executable} -m a0_node run"


def systemd_unit_text() -> str:
    home = node_home()
    return f"""[Unit]
Description=Agent Zero device node
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
ExecStart={_run_command()}
Environment=A0_NODE_HOME={home}
WorkingDirectory={home}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def launchd_plist_bytes() -> bytes:
    home = node_home()
    return plistlib.dumps(
        {
            "Label": "ai.agent-zero.a0-node",
            "ProgramArguments": [sys.executable, "-m", "a0_node", "run"],
            "EnvironmentVariables": {"A0_NODE_HOME": str(home)},
            "WorkingDirectory": str(home),
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(home / "service.out.log"),
            "StandardErrorPath": str(home / "service.err.log"),
        }
    )


def windows_scheduled_task_command() -> list[str]:
    home = node_home()
    inner = f'set "A0_NODE_HOME={home}" && "{sys.executable}" -m a0_node run'
    action_argument = inner.replace("'", "''")
    script = (
        "$action = New-ScheduledTaskAction -Execute 'cmd.exe' "
        f"-Argument '/c {action_argument}'; "
        "$trigger = New-ScheduledTaskTrigger -AtLogOn; "
        "$settings = New-ScheduledTaskSettingsSet -RestartCount 999 "
        "-RestartInterval (New-TimeSpan -Minutes 1) "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit (New-TimeSpan -Seconds 0); "
        "Register-ScheduledTask -TaskName 'A0Node' -Action $action "
        "-Trigger $trigger -Settings $settings "
        "-Description 'Agent Zero device node' -Force"
    )
    return ["powershell", "-NoProfile", "-Command", script]


def install_service() -> list[str]:
    messages: list[str] = []
    node_home().mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        target = Path("~/Library/LaunchAgents/ai.agent-zero.a0-node.plist").expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(launchd_plist_bytes())
        messages.append(f"wrote {target}")
        messages.append(f"load it with: launchctl load -w {target}")
    elif sys.platform.startswith("linux"):
        target = Path("~/.config/systemd/user").expanduser() / f"{SERVICE_NAME}.service"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(systemd_unit_text(), encoding="utf-8")
        messages.append(f"wrote {target}")
        messages.append("enable it with: systemctl --user daemon-reload && systemctl --user enable --now a0-node")
        messages.append("for background use after logout: loginctl enable-linger $USER")
    elif sys.platform == "win32":
        command = windows_scheduled_task_command()
        messages.append(
            "run this in a PowerShell prompt (no admin needed for the current user):"
        )
        messages.append("  " + subprocess.list2cmdline(command))
        messages.append(
            "the task starts at logon and restarts the node automatically after a crash"
        )
    else:
        messages.append(f"unsupported platform for automatic service install: {sys.platform}")
        messages.append(f"run the node manually: {_run_command()}")
    return messages


def uninstall_service() -> list[str]:
    messages: list[str] = []
    if sys.platform == "darwin":
        target = Path("~/Library/LaunchAgents/ai.agent-zero.a0-node.plist").expanduser()
        messages.append(f"unload with: launchctl unload -w {target}")
        if target.exists():
            target.unlink()
            messages.append(f"removed {target}")
    elif sys.platform.startswith("linux"):
        target = Path("~/.config/systemd/user").expanduser() / f"{SERVICE_NAME}.service"
        messages.append("stop it with: systemctl --user disable --now a0-node")
        if target.exists():
            target.unlink()
            messages.append(f"removed {target}")
    elif sys.platform == "win32":
        messages.append(
            "remove it with: Unregister-ScheduledTask -TaskName A0Node -Confirm:$false"
        )
    else:
        messages.append("no service was installed for this platform")
    return messages
