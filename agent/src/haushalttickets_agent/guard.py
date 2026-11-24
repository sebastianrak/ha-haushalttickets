"""PreToolUse hook: checks every Home Assistant tool call of the AI.

Registered in ``/etc/claude-code/managed-settings.json`` for ``mcp__ha__.*`` so the
AI cannot change or skip it. Reads the call from stdin and denies it with a reason,
or stays silent (the tool allowlist decides). Allowed writes are appended to the
journal named by ``HT_AGENT_JOURNAL`` so the runner knows what to put back.

Any error here denies the call: a hook that crashes must not let a write through.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from .tools import HA_READ, HA_WRITE, PREFIX

# Decision 52: packages/ and configuration.yaml, but not the homeassistant: block.
_YAML_FILE = re.compile(r"^(configuration\.yaml|packages/[A-Za-z0-9_\-]+\.ya?ml)$")
_YAML_FORBIDDEN_KEYS = frozenset(
    {"homeassistant", "automation", "script", "scene", "default_config", "frontend"}
)
_ENTITY_ALLOWED = frozenset(
    {
        "entity_id",
        "area_id",
        "labels",
        "label_operation",
        "categories",
        "name",
        "icon",
        "BestPracticeKey",
        "MandatoryBPS",
    }
)


class Deny(Exception):
    """The call is not allowed."""


def check(tool_name: str, tool_input: dict[str, Any], phase: str) -> None:
    """Raise Deny if the AI may not make this call."""
    if not tool_name.startswith(PREFIX):
        return  # not Home Assistant; the allowlist decides
    name = tool_name.removeprefix(PREFIX)
    if name in HA_READ:
        return
    if name not in HA_WRITE:
        raise Deny(f"{name} is not allowed in the nightly run.")
    if phase != "implement":
        raise Deny("Drafts only read Home Assistant; changes need an approval.")
    if name == "ha_config_set_yaml":
        _check_yaml(tool_input)
    elif name == "ha_set_entity":
        _check_entity(tool_input)
    elif (
        name == "ha_config_set_helper"
        and tool_input.get("helper_type") == "config_subentry"
    ):
        raise Deny("Integration settings (config subentries) are off limits.")


def _check_yaml(args: dict[str, Any]) -> None:
    file = str(args.get("file") or "configuration.yaml")
    if ".." in file or not _YAML_FILE.match(file):
        raise Deny(
            f"YAML file {file} is off limits (packages/ and configuration.yaml)."
        )
    key = str(args.get("yaml_path") or "")
    if not key or "." in key:
        raise Deny("Only whole top-level keys may be written.")
    if key in _YAML_FORBIDDEN_KEYS:
        raise Deny(f"The key {key} is off limits.")
    if args.get("action") not in ("add", "replace"):
        raise Deny("The nightly run does not remove configuration.")
    if "!include" in str(args.get("content") or ""):
        raise Deny("No !include in content written by the nightly run.")


def _check_entity(args: dict[str, Any]) -> None:
    extra = sorted(
        key
        for key, value in args.items()
        if value is not None and key not in _ENTITY_ALLOWED
    )
    if extra:
        raise Deny(f"Not allowed on entities at night: {', '.join(extra)}.")
    if args.get("labels") is not None and args.get("label_operation") != "add":
        raise Deny("Labels may only be added (label_operation='add').")
    if isinstance(args.get("entity_id"), list):
        raise Deny("One entity per call.")


def _journal(path: str, tool_name: str, tool_input: dict[str, Any]) -> None:
    entry = {
        "at": datetime.now(UTC).isoformat(),
        "tool": tool_name.removeprefix(PREFIX),
        "input": tool_input,
    }
    with Path(path).open("a", encoding="utf-8") as journal:
        journal.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def decide(event: dict[str, Any], env: dict[str, str]) -> str | None:
    """Reason to deny, or None to let the allowlist decide (and journal writes)."""
    tool_name = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return "Malformed tool input."
    phase = env.get("HT_AGENT_PHASE", "")
    try:
        check(tool_name, tool_input, phase)
    except Deny as err:
        return str(err)
    if tool_name.removeprefix(PREFIX) in HA_WRITE and env.get("HT_AGENT_JOURNAL"):
        _journal(env["HT_AGENT_JOURNAL"], tool_name, tool_input)
    return None


def main() -> int:
    """Hook entry point."""
    try:
        reason = decide(json.load(sys.stdin), dict(os.environ))
    except Exception as err:  # fail closed
        reason = f"Guard error: {err}"
    if reason is None:
        return 0
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    print(reason, file=sys.stderr)
    # Exit code 2 blocks the call even if the JSON above is not understood.
    return 2


if __name__ == "__main__":
    sys.exit(main())
