"""A nightly run end to end, with fake ticket server, Home Assistant and AI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from haushalttickets_agent import claude
from haushalttickets_agent.backup import Repo
from haushalttickets_agent.config import Config
from haushalttickets_agent.runner import Runner

CONFIG_YAML = "default_config:\n\nknx: !include knx.yaml\n"
KNX = "knx:\n  light:\n    - name: Flur\n      address: 1/1/1\n"


def ticket(ticket_id: int, kind: str = "home_assistant") -> dict[str, Any]:
    return {
        "id": ticket_id,
        "type": kind,
        "title": f"Ticket {ticket_id}",
        "description": "",
        "requested_by_id": "u-robin",
        "assignee_ids": [],
        "checklist": [],
    }


class FakeTickets:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.results: list[tuple[int, dict[str, Any]]] = []
        self.ended: tuple | None = None

    def work(self):
        return self.items

    def users(self):
        return {"u-robin": "Robin"}

    def start_run(self):
        return 7

    def end_run(self, run_id, outcome, notes, usage):
        self.ended = (run_id, outcome, notes, usage)

    def result(self, ticket_id, body):
        self.results.append((ticket_id, body))
        return {}


class FakeHa:
    """Home Assistant as ha-mcp shows it; YAML files can change."""

    def __init__(self) -> None:
        self.files = {
            "configuration.yaml": CONFIG_YAML,
            "automations.yaml": "[]\n",
            "scripts.yaml": "{}\n",
            "scenes.yaml": "[]\n",
            "packages/knx.yaml": KNX,
        }
        self.valid = True
        self.labels: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, /, **args: Any) -> Any:
        self.calls.append((name, args))
        if name == "ha_read_file":
            return {"success": True, "content": self.files[args["path"]]}
        if name == "ha_list_files":
            return {
                "files": [
                    {"path": p, "is_dir": False}
                    for p in self.files
                    if p.startswith("packages/")
                ]
            }
        if name == "ha_config_get_dashboard":
            if args.get("list_only"):
                return {
                    "dashboards": [{"url_path": "dashboard-tickets", "mode": "storage"}]
                }
            return {"config": {"views": [{"title": args["url_path"]}]}}
        if name == "ha_config_list_helpers":
            return {
                "helpers": [{"id": "nacht", "helper_type": "schedule"}],
                "has_more": False,
            }
        if name == "ha_config_get_label":
            return {"labels": self.labels}
        if name == "ha_config_set_label":
            self.labels.append({"label_id": "ki_hausmeister", "name": args["name"]})
            return {"success": True}
        if name == "ha_get_system_health":
            return {
                "health_info": {
                    "config_check": {"is_valid": self.valid, "errors": "bad knx"}
                }
            }
        if name == "ha_config_set_yaml":
            if args["action"] == "replace":
                self.files[args["file"]] = f"knx:\n{_indent(args['content'])}\n"
            return {"success": True}
        raise AssertionError(name)


def _indent(text: str) -> str:
    return "\n".join("  " + line for line in text.splitlines())


class FakeAi:
    """Scripted AI: one answer per call, may write YAML through the 'journal'."""

    def __init__(self, ha: FakeHa, answers: list[dict[str, Any]]) -> None:
        self.ha = ha
        self.answers = answers
        self.calls: list[dict[str, Any]] = []

    def __call__(self, config, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        for write in answer.pop("writes", []):
            self.ha.files[write["file"]] = write["new_text"]
            with Path(kwargs["journal"]).open("a") as journal:
                journal.write(
                    json.dumps({"tool": "ha_config_set_yaml", "input": write["input"]})
                    + "\n"
                )
        return claude.ClaudeResult(**answer)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        tickets_url="http://t",
        tickets_token="r",
        ha_url="https://ha",
        ha_token="h",
        state_dir=tmp_path / "state",
    )


def make(config: Config, items, answers):
    tickets = FakeTickets(items)
    ha = FakeHa()
    ai = FakeAi(ha, answers)
    runner = Runner(config, tickets, ha, Repo(config.repo_dir), ai=ai)
    return runner, tickets, ha, ai


def log(repo: Path) -> list[str]:
    return subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()


PROBE = {"ok": True, "text": "OK"}


def test_no_work(config: Config) -> None:
    runner, tickets, _, ai = make(config, [], [])
    assert asyncio.run(runner.run()) == "no_work"
    assert tickets.ended[1] == "no_work"
    assert ai.calls == []  # no probe without work


def test_draft_and_implementation(config: Config) -> None:
    items = [
        {"kind": "implement", "ticket": ticket(4), "comments": [],
         "draft": {"version": 2, "created_at": "x", "body": "Licht Flur in knx.yaml"}},
        {"kind": "draft", "ticket": ticket(5, "purchase"), "comments": [], "draft": None},
    ]  # fmt: skip
    new_knx = KNX + "    - name: Bad\n      address: 1/1/2\n"
    answers = [
        PROBE,
        {
            "ok": True,
            "cost_usd": 1.5,
            "data": {
                "status": "implemented",
                "summary": "KNX-Licht Bad",
                "comment": "Bitte Licht Bad testen.",
                "objects": ["light.bad"],
                "needs_restart": False,
            },
            "writes": [
                {
                    "file": "packages/knx.yaml",
                    "new_text": new_knx,
                    "input": {
                        "file": "packages/knx.yaml",
                        "yaml_path": "knx",
                        "action": "replace",
                    },
                }
            ],
        },
        {
            "ok": True,
            "data": {
                "draft": "Optionen …",
                "comment": "Drei Geräte.",
                "feasible": True,
            },
        },
    ]
    runner, tickets, ha, ai = make(config, items, answers)
    assert asyncio.run(runner.run()) == "done"

    implemented = tickets.results[0]
    assert implemented[0] == 4
    body = implemented[1]
    assert body["kind"] == "implemented" and body["run_id"] == 7
    assert body["change"]["needs_restart"] is True  # YAML changed
    assert body["change"]["objects"] == ["light.bad", "packages/knx.yaml:knx"]
    assert body["change"]["git_commit"] in body["comment"]
    assert "Neuladen/Neustart" in body["comment"]
    assert tickets.results[1] == (
        5,
        {"run_id": 7, "kind": "draft", "draft": "Optionen …",
         "comment": "Entwurf liegt bereit (Abschnitt „KI-Entwürfe“). Drei Geräte."},
    )  # fmt: skip
    assert tickets.ended[1] == "done"
    assert tickets.ended[3]["cost_usd"] == 1.5
    assert tickets.ended[3]["tickets"] == 2

    repo = config.repo_dir
    assert (repo / "files/packages/knx.yaml").read_text() == new_knx
    assert json.loads((repo / "dashboards/dashboard-tickets.json").read_text())[
        "config"
    ]
    # "Vor #4" had nothing new after "Stand vor Lauf 7": no empty commit.
    assert log(repo)[:2] == ["#4: KNX-Licht Bad", "Stand vor Lauf 7"]
    assert any(c[0] == "ha_config_set_label" for c in ha.calls)
    # Phases got the right tools and inputs.
    assert [c["phase"] for c in ai.calls] == ["probe", "implement", "draft"]
    assert ai.calls[1]["journal"] is not None
    assert "freigegebener_entwurf" in ai.calls[1]["prompt"]


def test_failed_check_puts_yaml_back(config: Config) -> None:
    items = [{"kind": "implement", "ticket": ticket(4), "comments": [],
              "draft": {"version": 1, "created_at": "x", "body": "x"}}]  # fmt: skip
    answers = [
        PROBE,
        {
            "ok": True,
            "data": {
                "status": "implemented",
                "summary": "s",
                "comment": "c",
                "objects": [],
                "needs_restart": True,
            },
            "writes": [
                {
                    "file": "packages/knx.yaml",
                    "new_text": "knx:\n  light: [kaputt\n",
                    "input": {
                        "file": "packages/knx.yaml",
                        "yaml_path": "knx",
                        "action": "replace",
                    },
                }
            ],
        },
    ]
    runner, tickets, ha, _ = make(config, items, answers)
    ha.valid = False
    asyncio.run(runner.run())
    body = tickets.results[0][1]
    assert body["kind"] == "failed" and body["phase"] == "implement"
    assert body["retry"] is False
    assert "zurückgenommen" in body["comment"]
    restore = [c for c in ha.calls if c[0] == "ha_config_set_yaml"]
    assert restore[0][1]["action"] == "replace"
    assert restore[0][1]["content"] == "light:\n  - name: Flur\n    address: 1/1/1"
    assert ha.files["packages/knx.yaml"] == KNX
    assert log(config.repo_dir)[0] == "#4: zurückgenommen"


def test_deviation_stores_a_new_draft(config: Config) -> None:
    items = [{"kind": "implement", "ticket": ticket(4), "comments": [],
              "draft": {"version": 1, "created_at": "x", "body": "x"}}]  # fmt: skip
    answers = [
        PROBE,
        {"ok": True, "data": {"status": "needs_new_draft", "summary": "s",
                              "comment": "light.bad gibt es nicht.", "objects": [],
                              "needs_restart": False, "new_draft": "v2"}},
    ]  # fmt: skip
    runner, tickets, _, _ = make(config, items, answers)
    asyncio.run(runner.run())
    body = tickets.results[0][1]
    assert body["kind"] == "draft" and body["draft"] == "v2"
    assert "light.bad gibt es nicht." in body["comment"]


def test_usage_limit_stops_and_keeps_the_request(config: Config) -> None:
    items = [
        {"kind": "draft", "ticket": ticket(1), "comments": [], "draft": None},
        {"kind": "draft", "ticket": ticket(2), "comments": [], "draft": None},
    ]
    answers = [
        PROBE,
        {"ok": False, "rate_limited": True, "error": "usage limit reached"},
    ]
    runner, tickets, _, ai = make(config, items, answers)
    assert asyncio.run(runner.run()) == "quota_low"
    assert tickets.results == [
        (1, {"run_id": 7, "kind": "failed", "phase": "draft", "retry": True,
             "comment": "Nicht geschafft: usage limit reached\nNächste Nacht versuche ich es erneut."}),
    ]  # fmt: skip
    assert len(ai.calls) == 2  # ticket 2 waits for the next night
    assert tickets.ended[2].startswith("Nutzungslimit erreicht.")


def test_probe_failure_reports_the_run(config: Config) -> None:
    items = [{"kind": "draft", "ticket": ticket(1), "comments": [], "draft": None}]
    answers = [{"ok": False, "error": "Invalid bearer token"}]
    runner, tickets, _, _ = make(config, items, answers)
    assert asyncio.run(runner.run()) == "failed"
    assert tickets.results == []
    assert "Invalid bearer token" in tickets.ended[2]


def test_time_limit(config: Config) -> None:
    items = [{"kind": "draft", "ticket": ticket(1), "comments": [], "draft": None}]
    runner, tickets, _, ai = make(config, items, [PROBE])
    now = [0.0]
    runner.clock = lambda: now[0]
    runner.deadline = 400.0  # 100 s left after the reserve; the probe "takes" 50 s

    original = ai.__call__

    def slow(config, **kwargs):
        now[0] += 50
        return original(config, **kwargs)

    runner.ai = slow
    assert asyncio.run(runner.run()) == "aborted"
    assert tickets.results == []  # untouched; stays in the queue
