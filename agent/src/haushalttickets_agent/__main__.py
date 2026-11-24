"""Command line: ``haushalttickets-nightly [run|probe|export|check]``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__, claude
from .backup import Repo, export
from .config import Config, ConfigError
from .hamcp import HaMcp
from .runner import Runner, _find
from .tickets import Tickets, TicketsError


def main(argv: list[str] | None = None) -> int:
    """Run the command line."""
    parser = argparse.ArgumentParser(prog="haushalttickets-nightly")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="the nightly run (default; started by the timer)")
    sub.add_parser("probe", help="check Claude, the ticket server and Home Assistant")
    sub.add_parser("export", help="export the configuration and commit it")
    sub.add_parser("check", help="run Home Assistant's configuration check")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = Config.from_env()
    except ConfigError as err:
        print(f"Configuration: {err}", file=sys.stderr)
        return 2
    command = args.command or "run"
    return asyncio.run(_COMMANDS[command](config))


async def _run(config: Config) -> int:
    tickets = Tickets(config.tickets_url, config.tickets_token)
    repo = Repo(config.repo_dir, config.git_remote, config.git_ssh_key)
    async with HaMcp(config) as ha:
        outcome = await Runner(config, tickets, ha, repo).run()
    print(f"Outcome: {outcome}")
    return 0 if outcome in ("done", "no_work") else 1


async def _probe(config: Config) -> int:
    ok = True
    tickets = Tickets(config.tickets_url, config.tickets_token)
    try:
        items = tickets.work()
        print(f"Ticket server: OK, {len(items)} ticket(s) waiting")
    except TicketsError as err:
        print(f"Ticket server: {err}")
        ok = False
    try:
        async with HaMcp(config) as ha:
            labels = await ha.call("ha_config_get_label")
            count = len(labels.get("labels", [])) if isinstance(labels, dict) else "?"
            print(f"Home Assistant (ha-mcp): OK, {count} labels")
            content = await ha.call("ha_read_file", path="configuration.yaml")
            print(f"File tools: OK, configuration.yaml {len(content['content'])} chars")
    except Exception as err:  # report every problem
        print(f"Home Assistant (ha-mcp): {type(err).__name__}: {err}")
        ok = False
    config.run_dir.mkdir(parents=True, exist_ok=True)
    result = claude.run(
        config,
        phase="probe",
        prompt="Antworte nur mit OK.",
        system_prompt="Antworte nur mit OK.",
        timeout=180,
        workdir=config.run_dir / "probe",
    )
    if result.ok:
        print(f"Claude: OK ({result.text.strip()[:40]})")
    else:
        print(f"Claude: {result.error}")
        ok = False
    return 0 if ok else 1


async def _export(config: Config) -> int:
    repo = Repo(config.repo_dir, config.git_remote, config.git_ssh_key)
    repo.ensure()
    async with HaMcp(config, yaml_edit=False) as ha:
        await export(ha, repo.path)
    commit = repo.commit("Export von Hand")
    error = repo.push()
    print(f"Commit {commit[:12]}" + (f"; push failed: {error}" if error else ""))
    return 0 if error is None else 1


async def _check(config: Config) -> int:
    async with HaMcp(config, yaml_edit=False) as ha:
        health = await ha.call("ha_get_system_health", include="config_check")
    check = _find(health, "is_valid") or {}
    print(check)
    return 0 if check.get("is_valid") else 1


_COMMANDS = {"run": _run, "probe": _probe, "export": _export, "check": _check}


if __name__ == "__main__":
    sys.exit(main())
