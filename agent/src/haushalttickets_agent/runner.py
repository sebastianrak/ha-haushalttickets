"""One nightly run: fetch the work, draft or implement each ticket, report.

The runner is plain code; only the drafting and implementing steps are the AI. The
runner alone talks to the ticket server and to git, exports the configuration
before and after each change, checks the configuration after YAML changes and puts
the old YAML back if the check fails (decision 52).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import time
from typing import Any

from . import claude, prompts
from .backup import Caller, Repo, export
from .config import Config
from .hamcp import write_mcp_config
from .tickets import Tickets, TicketsError
from .yamlkeys import top_level

_LOGGER = logging.getLogger(__name__)
# Leave room to report after the last step.
_RESERVE_SECONDS = 300

TEXTS = {
    "de": {
        "draft_ready": "Entwurf liegt bereit (Abschnitt „KI-Entwürfe“). {comment}",
        "not_feasible": "So nicht machbar – Begründung im Entwurf. {comment}",
        "implemented": "Umgesetzt (Commit {commit}). {comment}",
        "restart": "Wirksam erst nach Neuladen/Neustart durch euch.",
        "new_draft": "Die Umsetzung müsste vom freigegebenen Entwurf abweichen; nichts "
        "geändert. Neuer Entwurf liegt bereit, die Freigabe ist zurückgenommen. {comment}",
        "partial": "Es wurde schon etwas geändert: {objects} (Commit {commit}).",
        "failed": "Nicht geschafft: {reason}",
        "retry": "Nächste Nacht versuche ich es erneut.",
        "reverted": "Die Konfigurationsprüfung schlug fehl; die YAML-Änderungen sind "
        "zurückgenommen (Commits {commit} → {revert}). Fehler: {errors}",
        "limit": "Nutzungslimit erreicht.",
        "time": "Zeitgrenze erreicht.",
    },
    "en": {
        "draft_ready": "Draft ready (section “AI drafts”). {comment}",
        "not_feasible": "Not feasible like this – see the draft. {comment}",
        "implemented": "Implemented (commit {commit}). {comment}",
        "restart": "Takes effect after you reload or restart.",
        "new_draft": "Implementing would deviate from the approved draft; nothing "
        "changed. A new draft is ready and the approval withdrawn. {comment}",
        "partial": "Some changes were made already: {objects} (commit {commit}).",
        "failed": "Not done: {reason}",
        "retry": "I will try again next night.",
        "reverted": "The configuration check failed; the YAML changes are undone "
        "(commits {commit} → {revert}). Errors: {errors}",
        "limit": "Usage limit reached.",
        "time": "Time limit reached.",
    },
}


@dataclass
class Stats:
    """What the run cost and did."""

    cost_usd: float = 0.0
    turns: int = 0
    calls: int = 0
    tickets: int = 0
    notes: list[str] = field(default_factory=list)

    def add(self, result: claude.ClaudeResult) -> None:
        """Count one AI call."""
        self.cost_usd += result.cost_usd
        self.turns += result.turns
        self.calls += 1

    def usage(self) -> dict[str, Any]:
        """For the run log on the server."""
        return {
            "cost_usd": round(self.cost_usd, 4),
            "turns": self.turns,
            "calls": self.calls,
            "tickets": self.tickets,
        }


class Stop(Exception):
    """End the run early (usage limit, time limit)."""

    def __init__(self, outcome: str, reason: str) -> None:
        """Initialize."""
        super().__init__(reason)
        self.outcome = outcome
        self.reason = reason


ClaudeRunner = Callable[..., claude.ClaudeResult]


class Runner:
    """Works through the queue the server hands out."""

    def __init__(
        self,
        config: Config,
        tickets: Tickets,
        ha: Caller,
        repo: Repo,
        *,
        ai: ClaudeRunner = claude.run,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize."""
        self.config = config
        self.tickets = tickets
        self.ha = ha
        self.repo = repo
        self.ai = ai
        self.clock = clock
        self.deadline = clock() + config.time_limit_minutes * 60
        self.texts = TEXTS["de" if config.language.startswith("de") else "en"]
        self.stats = Stats()
        self.names: dict[str, str] = {}
        self.run_id = 0
        self.yaml: dict[str, str] = {}

    # ---------------------------------------------------------------- helpers

    def _remaining(self) -> float:
        return self.deadline - self.clock() - _RESERVE_SECONDS

    def _dir(self, name: str) -> Path:
        path = self.config.run_dir / f"run-{self.run_id}" / name
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def _call(
        self, item: dict[str, Any], kind: str, **kwargs: Any
    ) -> claude.ClaudeResult:
        remaining = self._remaining()
        if remaining < 60:
            raise Stop("aborted", self.texts["time"])
        ticket_id = item["ticket"]["id"]
        result = self.ai(
            self.config,
            phase=kind,
            prompt=prompts.ticket_prompt(item, self.names),
            system_prompt=prompts.system_prompt(kind, self.config.language),
            timeout=remaining,
            workdir=self._dir(f"{kind}-{ticket_id}"),
            **kwargs,
        )
        self.stats.add(result)
        return result

    def _post(self, ticket_id: int, body: dict[str, Any]) -> None:
        try:
            self.tickets.result(ticket_id, {"run_id": self.run_id, **body})
        except TicketsError as err:
            # E.g. the ticket was closed meanwhile; note it and go on.
            _LOGGER.warning("Result for #%s refused: %s", ticket_id, err)
            self.stats.notes.append(f"#{ticket_id}: {err}")

    def _failed(
        self, ticket_id: int, phase: str, reason: str, *, retry: bool, extra: str = ""
    ) -> None:
        text = self.texts["failed"].format(reason=reason[:1500])
        if extra:
            text += "\n" + extra
        if retry:
            text += "\n" + self.texts["retry"]
        self._post(
            ticket_id,
            {"kind": "failed", "phase": phase, "retry": retry, "comment": text},
        )

    async def _export(self, message: str) -> str:
        self.yaml = await export(self.ha, self.repo.path)
        commit = self.repo.commit(message)
        if error := self.repo.push():
            self.stats.notes.append(f"git push: {error}")
        return commit[:12]

    # ---------------------------------------------------------------- steps

    async def draft(self, item: dict[str, Any]) -> None:
        """Write a (new) draft for a ticket."""
        ticket_id = item["ticket"]["id"]
        mcp = write_mcp_config(
            self.config, self._dir("mcp") / "draft.json", yaml_edit=False
        )
        result = self._call(item, "draft", mcp_config=mcp, schema=prompts.DRAFT_SCHEMA)
        if result.rate_limited or result.timed_out:
            self._failed(ticket_id, "draft", result.error, retry=True)
            raise Stop(
                "quota_low" if result.rate_limited else "aborted",
                self.texts["limit"] if result.rate_limited else self.texts["time"],
            )
        data = result.data or {}
        if not result.ok or not data.get("draft"):
            self._failed(ticket_id, "draft", result.error or "no draft", retry=False)
            return
        key = "draft_ready" if data.get("feasible", True) else "not_feasible"
        self._post(
            ticket_id,
            {
                "kind": "draft",
                "draft": data["draft"],
                "comment": self.texts[key]
                .format(comment=data.get("comment", ""))
                .strip(),
            },
        )

    async def implement(self, item: dict[str, Any]) -> None:
        """Carry out an approved draft."""
        ticket = item["ticket"]
        ticket_id = ticket["id"]
        before = await self._export(f"Vor #{ticket_id} (Lauf {self.run_id})")
        before_yaml = dict(self.yaml)
        journal = self._dir("journal") / f"{ticket_id}.jsonl"
        journal.unlink(missing_ok=True)
        mcp = write_mcp_config(
            self.config, self._dir("mcp") / "implement.json", yaml_edit=True
        )
        result = self._call(
            item,
            "implement",
            mcp_config=mcp,
            schema=prompts.IMPLEMENT_SCHEMA,
            journal=journal,
        )
        writes = _read_journal(journal)
        data = result.data or {}
        if data.get("status") == "implemented" and data.get("objects") and not writes:
            # The AI reports changes the guard never saw: the hook did not run.
            self.stats.notes.append(
                f"#{ticket_id}: guard journal empty although changes are reported - "
                "check /etc/claude-code/managed-settings.json"
            )
        objects = list(dict.fromkeys([*(data.get("objects") or []), *_objects(writes)]))
        summary = (data.get("summary") or ticket["title"])[:200]
        after = await self._export(f"#{ticket_id}: {summary}")
        yaml_writes = [w for w in writes if w["tool"] == "ha_config_set_yaml"]

        if yaml_writes:
            errors = await self._config_errors()
            if errors is not None:
                await self._restore(yaml_writes, before_yaml)
                revert = await self._export(f"#{ticket_id}: zurückgenommen")
                self._failed(
                    ticket_id,
                    "implement",
                    self.texts["reverted"].format(
                        commit=after, revert=revert, errors=errors[:800]
                    ),
                    retry=False,
                )
                return

        partial = (
            self.texts["partial"].format(
                objects=", ".join(objects) or "?", commit=after
            )
            if writes and after != before
            else ""
        )
        if result.rate_limited or result.timed_out:
            # Retrying makes sense only if nothing was changed yet.
            self._failed(
                ticket_id, "implement", result.error, retry=not writes, extra=partial
            )
            raise Stop(
                "quota_low" if result.rate_limited else "aborted",
                self.texts["limit"] if result.rate_limited else self.texts["time"],
            )
        status = data.get("status") if result.ok else "failed"
        if status == "implemented":
            needs_restart = bool(data.get("needs_restart")) or bool(yaml_writes)
            comment = self.texts["implemented"].format(
                commit=after, comment=data.get("comment", "")
            )
            if needs_restart:
                comment += "\n" + self.texts["restart"]
            self._post(
                ticket_id,
                {
                    "kind": "implemented",
                    "comment": comment.strip(),
                    "change": {
                        "summary": summary,
                        "objects": objects[:200],
                        "git_commit": after,
                        "needs_restart": needs_restart,
                    },
                },
            )
        elif status == "needs_new_draft" and data.get("new_draft"):
            comment = self.texts["new_draft"].format(comment=data.get("comment", ""))
            if partial:
                comment += "\n" + partial
            self._post(
                ticket_id,
                {
                    "kind": "draft",
                    "draft": data["new_draft"],
                    "comment": comment.strip(),
                },
            )
        else:
            reason = data.get("comment") or result.error or "unknown"
            self._failed(ticket_id, "implement", reason, retry=False, extra=partial)

    async def _config_errors(self) -> str | None:
        """None if Home Assistant's configuration check passes, else the errors."""
        health = await self.ha.call("ha_get_system_health", include="config_check")
        check = _find(health, "is_valid")
        if check is None:
            return "configuration check gave no answer"
        if check.get("is_valid"):
            return None
        return json.dumps(check.get("errors") or check, ensure_ascii=False)

    async def _restore(
        self, writes: list[dict[str, Any]], before: dict[str, str]
    ) -> None:
        """Put back the top-level keys the AI wrote, from the export before."""
        done: set[tuple[str, str]] = set()
        for write in writes:
            args = write["input"]
            file = args.get("file") or "configuration.yaml"
            key = args["yaml_path"]
            if (file, key) in done:
                continue
            done.add((file, key))
            old = top_level(before.get(file, ""))
            if key in old:
                await self.ha.call(
                    "ha_config_set_yaml",
                    file=file,
                    yaml_path=key,
                    action="replace",
                    content=old[key],
                )
            else:
                await self.ha.call(
                    "ha_config_set_yaml", file=file, yaml_path=key, action="remove"
                )

    # ---------------------------------------------------------------- run

    def _probe(self) -> None:
        """A tiny call first: is Claude reachable and is there usage left?"""
        result = self.ai(
            self.config,
            phase="probe",
            prompt="Antworte nur mit OK.",
            system_prompt="Antworte nur mit OK.",
            timeout=min(180.0, max(self._remaining(), 1.0)),
            workdir=self._dir("probe"),
        )
        self.stats.add(result)
        if result.rate_limited:
            raise Stop("quota_low", f"{self.texts['limit']} {result.error}")
        if not result.ok:
            raise Stop("failed", f"Probe: {result.error}")

    async def run(self) -> str:
        """The whole run; returns the outcome."""
        items = self.tickets.work()
        self.run_id = self.tickets.start_run()
        outcome = "done"
        try:
            if not items:
                outcome = "no_work"
                return outcome
            self.names = self.tickets.users()
            self._probe()
            self.repo.ensure()
            await self._ensure_label()
            await self._export(f"Stand vor Lauf {self.run_id}")
            for item in items:
                self.stats.tickets += 1
                if item["kind"] == "implement":
                    await self.implement(item)
                else:
                    await self.draft(item)
        except Stop as stop:
            outcome = stop.outcome
            self.stats.notes.insert(0, stop.reason)
        except Exception as err:  # always report the run
            _LOGGER.exception("Nightly run failed")
            outcome = "failed"
            self.stats.notes.insert(0, f"{type(err).__name__}: {err}")
        finally:
            try:
                self.tickets.end_run(
                    self.run_id,
                    outcome,
                    "\n".join(self.stats.notes),
                    self.stats.usage(),
                )
            except TicketsError:
                _LOGGER.exception("Could not report the end of run %s", self.run_id)
        return outcome

    async def _ensure_label(self) -> None:
        labels = await self.ha.call("ha_config_get_label")
        items = labels.get("labels", []) if isinstance(labels, dict) else labels
        if not any(label.get("label_id") == self.config.label_id for label in items):
            await self.ha.call(
                "ha_config_set_label",
                name=self.config.label_name,
                icon="mdi:robot-outline",
                description="Vom Nachtlauf angelegt oder geändert (Haushalttickets)",
            )


def _read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text("utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def _objects(writes: list[dict[str, Any]]) -> list[str]:
    """Rough object names from the journal (the AI's list comes first)."""
    names = []
    for write in writes:
        args = write["input"]
        if write["tool"] == "ha_config_set_yaml":
            names.append(
                f"{args.get('file') or 'configuration.yaml'}:{args.get('yaml_path')}"
            )
        else:
            for key in (
                "entity_id",
                "identifier",
                "url_path",
                "script_id",
                "helper_id",
            ):
                if isinstance(args.get(key), str):
                    names.append(args[key])
                    break
    return names


def _find(value: Any, key: str) -> dict[str, Any] | None:
    """First mapping (depth-first) that holds ``key``."""
    if isinstance(value, dict):
        if key in value:
            return value
        for child in value.values():
            if (found := _find(child, key)) is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            if (found := _find(child, key)) is not None:
                return found
    return None
