"""The guard hook and the phase tool lists."""

from __future__ import annotations

import io
import json
import subprocess
import sys

import pytest

from haushalttickets_agent import guard, tools

P = tools.PREFIX
IMPLEMENT = {"HT_AGENT_PHASE": "implement"}


def deny(name: str, args: dict, env: dict | None = None) -> str | None:
    return guard.decide({"tool_name": P + name, "tool_input": args}, env or IMPLEMENT)


def yaml(**args):
    base = {
        "file": "packages/knx.yaml",
        "yaml_path": "knx",
        "action": "replace",
        "content": "light: []",
    }
    return deny("ha_config_set_yaml", {**base, **args})


def test_reading_is_free_in_both_phases() -> None:
    for phase in ("draft", "implement"):
        assert deny("ha_search", {"query": "licht"}, {"HT_AGENT_PHASE": phase}) is None


def test_drafts_never_write() -> None:
    reason = deny("ha_config_set_automation", {}, {"HT_AGENT_PHASE": "draft"})
    assert "approval" in reason
    # No phase at all (hook started outside the runner): no writes either.
    assert deny("ha_set_entity", {"entity_id": "light.x"}, {"PATH": ""}) is not None


@pytest.mark.parametrize(
    "name",
    ["ha_call_service", "ha_restart", "ha_write_file", "ha_delete_file",
     "ha_config_remove_automation", "ha_set_integration", "ha_manage_backup"],
)  # fmt: skip
def test_everything_else_is_refused(name: str) -> None:
    assert "not allowed" in deny(name, {})


def test_yaml_rules() -> None:
    assert yaml() is None
    assert yaml(file="configuration.yaml", yaml_path="command_line") is None
    assert "off limits" in yaml(file="secrets.yaml")
    assert "off limits" in yaml(file="templates.yaml")
    assert "off limits" in yaml(file="packages/../secrets.yaml")
    assert "off limits" in yaml(file="custom_components/x/__init__.py")
    assert "off limits" in yaml(file="configuration.yaml", yaml_path="homeassistant")
    assert "top-level" in yaml(yaml_path="lovelace.dashboards.x")
    assert "remove" in yaml(action="remove")
    assert "!include" in yaml(content="light: !include lights.yaml")


def test_entity_rules() -> None:
    ok = {
        "entity_id": "light.flur",
        "area_id": "flur",
        "labels": ["ki_hausmeister"],
        "label_operation": "add",
    }
    assert deny("ha_set_entity", ok) is None
    assert "new_entity_id" in deny("ha_set_entity", {**ok, "new_entity_id": "light.x"})
    assert "expose_to" in deny(
        "ha_set_entity", {**ok, "expose_to": {"cloud.alexa": True}}
    )
    assert "enabled" in deny("ha_set_entity", {**ok, "enabled": False})
    assert "added" in deny("ha_set_entity", {**ok, "label_operation": "set"})
    assert "One entity" in deny("ha_set_entity", {"entity_id": ["a.b", "c.d"]})


def test_config_subentries_are_off_limits() -> None:
    assert deny("ha_config_set_helper", {"helper_type": "template"}) is None
    assert "subentries" in deny(
        "ha_config_set_helper", {"helper_type": "config_subentry"}
    )


def test_writes_are_journaled(tmp_path) -> None:
    journal = tmp_path / "j.jsonl"
    env = {**IMPLEMENT, "HT_AGENT_JOURNAL": str(journal)}
    assert deny("ha_config_set_automation", {"identifier": "new"}, env) is None
    assert deny("ha_search", {"query": "x"}, env) is None
    entries = [json.loads(line) for line in journal.read_text().splitlines()]
    assert [e["tool"] for e in entries] == ["ha_config_set_automation"]


def test_hook_process_fails_closed(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert guard.main() == 2
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_as_a_process(tmp_path) -> None:
    event = {"tool_name": P + "ha_restart", "tool_input": {}}
    done = subprocess.run(
        [sys.executable, "-m", "haushalttickets_agent.guard"],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env={"HT_AGENT_PHASE": "implement", "PATH": ""},
        check=False,
    )
    assert done.returncode == 2
    assert "ha_restart" in done.stderr


def test_phase_tool_lists() -> None:
    draft = tools.allowed("draft")
    implement = tools.allowed("implement")
    assert "WebFetch" in draft and "WebFetch" not in implement
    assert (
        P + "ha_config_set_yaml" in implement and P + "ha_config_set_yaml" not in draft
    )
    assert "WebSearch" in tools.denied("implement")
    assert "Bash" in tools.denied("draft")
    assert tools.allowed("probe") == []
