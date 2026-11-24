"""Git backup of the Home Assistant configuration (decision 49).

A fixed export - never the AI - writes what the nightly run may change into a git
working copy and commits it before and after every change: the YAML files the
automations, scripts and scenes live in, ``configuration.yaml`` and the packages,
every storage dashboard, the helpers and the labels. ``!secret`` stays a reference;
``secrets.yaml`` and ``.storage`` are never read.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any, Protocol

FILES = ("configuration.yaml", "automations.yaml", "scripts.yaml", "scenes.yaml")


class Caller(Protocol):
    """Anything with ``await call(tool, **args)`` (HaMcp, or a fake in tests)."""

    async def call(self, tool: str, /, **arguments: Any) -> Any:
        """Call a tool."""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") or not text else text + "\n", "utf-8")


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


async def export(ha: Caller, repo: Path) -> dict[str, str]:
    """Write the current configuration into ``repo``; returns the YAML texts."""
    texts: dict[str, str] = {}
    for name in FILES:
        texts[name] = (await ha.call("ha_read_file", path=name))["content"]
    listing = await ha.call("ha_list_files", path="packages/", pattern="*.y*ml")
    for entry in listing.get("files", []):
        if entry.get("is_dir"):
            continue
        path = entry["path"]
        texts[path] = (await ha.call("ha_read_file", path=path))["content"]

    files = repo / "files"
    for path in sorted(files.rglob("*")) if files.exists() else []:
        if path.is_file() and str(path.relative_to(files)) not in texts:
            path.unlink()  # package removed by hand since the last export
    for name, text in texts.items():
        _write(files / name, text)

    dashboards = await ha.call("ha_config_get_dashboard", list_only=True)
    target = repo / "dashboards"
    seen = set()
    for meta in dashboards.get("dashboards", []):
        if meta.get("mode") != "storage":
            continue
        url_path = meta["url_path"]
        data = await ha.call("ha_config_get_dashboard", url_path=url_path)
        seen.add(f"{url_path}.json")
        _write(
            target / f"{url_path}.json",
            _json({"meta": meta, "config": data.get("config")}),
        )
    default = await ha.call("ha_config_get_dashboard", url_path="default")
    seen.add("default.json")
    _write(target / "default.json", _json({"config": default.get("config")}))
    for path in target.glob("*.json"):
        if path.name not in seen:
            path.unlink()

    helpers: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await ha.call(
            "ha_config_list_helpers", helper_type="all", limit=500, offset=offset
        )
        helpers.extend(page.get("helpers", []))
        if not page.get("has_more"):
            break
        offset = page.get("next_offset", offset + 500)
    helpers.sort(
        key=lambda h: (
            h.get("helper_type", ""),
            str(h.get("id") or h.get("entry_id") or ""),
        )
    )
    _write(repo / "helpers.json", _json(helpers))
    labels = await ha.call("ha_config_get_label")
    _write(repo / "labels.json", _json(labels.get("labels", labels)))
    return texts


class Repo:
    """The git working copy; pushes to a private repository if one is set."""

    def __init__(self, path: Path, remote: str = "", ssh_key: str = "") -> None:
        """Initialize."""
        self.path = path
        self.remote = remote
        self.ssh_key = ssh_key

    def _git(self, *args: str, check: bool = True) -> str:
        env = {**os.environ, "LANG": "C.UTF-8"}
        if self.ssh_key:
            env["GIT_SSH_COMMAND"] = (
                f"ssh -i {self.ssh_key} -o IdentitiesOnly=yes "
                "-o StrictHostKeyChecking=accept-new"
            )
        done = subprocess.run(
            ["git", *args],
            cwd=self.path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if check and done.returncode != 0:
            raise RuntimeError(f"git {args[0]}: {done.stderr.strip()}")
        return done.stdout.strip()

    def ensure(self) -> None:
        """Create the working copy on first use."""
        self.path.mkdir(parents=True, exist_ok=True)
        if not (self.path / ".git").exists():
            self._git("init", "-q", "-b", "main")
            self._git("config", "user.name", "KI-Hausmeister")
            self._git("config", "user.email", "ki-hausmeister@localhost")
            _write(
                self.path / "README.md",
                "# Home Assistant configuration (export of the nightly run)\n\n"
                "Written by haushalttickets-agent. Keep this repository private.\n",
            )
        if self.remote:
            remotes = self._git("remote").split()
            if "origin" in remotes:
                self._git("remote", "set-url", "origin", self.remote)
            else:
                self._git("remote", "add", "origin", self.remote)

    def commit(self, message: str) -> str:
        """Commit everything; returns the commit (HEAD if nothing changed)."""
        self._git("add", "-A")
        if self._git("status", "--porcelain"):
            self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "HEAD")

    def push(self) -> str | None:
        """Push to the private repository; returns an error text or None."""
        if not self.remote:
            return None
        try:
            self._git("push", "-q", "origin", "main")
        except (RuntimeError, subprocess.TimeoutExpired) as err:
            return str(err)
        return None
