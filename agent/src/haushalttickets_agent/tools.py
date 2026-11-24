"""Which tools the AI gets in each phase (decisions 51, 52 and 55).

The lists go to ``claude --allowedTools``; anything else is refused because nobody
can approve a prompt at night. The guard hook checks the arguments of the writing
tools on top of that.
"""

from __future__ import annotations

SERVER = "ha"  # name of the ha-mcp server in the MCP config
PREFIX = f"mcp__{SERVER}__"

# Reading Home Assistant, both phases. No camera images, calendars or to-do lists.
HA_READ = (
    "ha_config_get_automation",
    "ha_config_get_category",
    "ha_config_get_dashboard",
    "ha_config_get_label",
    "ha_config_get_scene",
    "ha_config_get_script",
    "ha_config_get_yaml",
    "ha_config_list_groups",
    "ha_config_list_helpers",
    "ha_eval_template",
    "ha_get_automation_traces",
    "ha_get_device",
    "ha_get_entity",
    "ha_get_history",
    "ha_get_integration",
    "ha_get_logs",
    "ha_get_overview",
    "ha_get_skill_guide",
    "ha_get_state",
    "ha_get_system_health",
    "ha_get_zone",
    "ha_list_files",
    "ha_list_floors_areas",
    "ha_list_services",
    "ha_read_file",
    "ha_search",
)

# Changing Home Assistant, implementation phase only; never deleting.
HA_WRITE = (
    "ha_config_set_automation",
    "ha_config_set_dashboard",
    "ha_config_set_helper",
    "ha_config_set_scene",
    "ha_config_set_script",
    "ha_config_set_yaml",
    "ha_set_entity",
)

# Built-in tools: web only while drafting; no shell, no local files, no subagents.
WEB = ("WebSearch", "WebFetch")
BUILTIN_DENIED = (
    "Bash",
    "BashOutput",
    "KillShell",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "Glob",
    "Grep",
    "LS",
    "Task",
    "Agent",
    "TodoWrite",
    "SlashCommand",
    "Skill",
)


def allowed(phase: str) -> list[str]:
    """Tools the AI may use in a phase (``draft`` or ``implement``)."""
    tools = [PREFIX + name for name in HA_READ]
    if phase == "draft":
        return [*tools, *WEB]
    if phase == "implement":
        return [*tools, *(PREFIX + name for name in HA_WRITE)]
    if phase == "probe":
        return []
    raise ValueError(phase)


def denied(phase: str) -> list[str]:
    """Tools refused outright in a phase."""
    tools = list(BUILTIN_DENIED)
    if phase != "draft":
        tools += WEB
    return tools
