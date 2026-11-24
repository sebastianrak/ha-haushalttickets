"""Running Claude Code headless (``claude -p``) for one step of the nightly run."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from .config import Config
from .tools import allowed, denied

_LIMIT = re.compile(
    r"usage limit|rate limit|rate_limit|limit reached|too many requests|\b429\b",
    re.IGNORECASE,
)


@dataclass
class ClaudeResult:
    """Outcome of one ``claude -p`` call."""

    ok: bool
    data: dict[str, Any] | None = None
    text: str = ""
    error: str = ""
    rate_limited: bool = False
    timed_out: bool = False
    cost_usd: float = 0.0
    turns: int = 0
    usage: dict[str, Any] = field(default_factory=dict)


def command(
    config: Config,
    *,
    phase: str,
    system_prompt: str,
    mcp_config: Path | None,
    schema: dict[str, Any] | None,
) -> list[str]:
    """Arguments of the call; the prompt itself goes to stdin."""
    args = [
        config.claude_command,
        "-p",
        "--output-format",
        "json",
        "--model",
        config.model,
        "--append-system-prompt",
        system_prompt,
        "--disallowedTools",
        ",".join(denied(phase)),
    ]
    tools = allowed(phase)
    if tools:
        args += ["--allowedTools", ",".join(tools)]
    if mcp_config is not None:
        args += ["--mcp-config", str(mcp_config), "--strict-mcp-config"]
    if schema is not None:
        args += ["--json-schema", json.dumps(schema)]
    return args


def environment(phase: str, journal: Path | None, workdir: Path) -> dict[str, str]:
    """Minimal environment: no Home Assistant or ticket token reaches the AI."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(workdir)),
        "LANG": "C.UTF-8",
        "HT_AGENT_PHASE": phase,
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
    }
    if token := os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    if journal is not None:
        env["HT_AGENT_JOURNAL"] = str(journal)
    return env


def parse(stdout: str, stderr: str, returncode: int) -> ClaudeResult:
    """Read the JSON result ``claude -p --output-format json`` prints."""
    try:
        out = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
    except ValueError:
        out = {}
    text = str(out.get("result") or "")
    usage = out.get("usage") or {}
    result = ClaudeResult(
        ok=returncode == 0 and not out.get("is_error") and bool(out),
        text=text,
        cost_usd=float(out.get("total_cost_usd") or 0),
        turns=int(out.get("num_turns") or 0),
        usage=usage if isinstance(usage, dict) else {},
    )
    structured = out.get("structured_output")
    if isinstance(structured, dict):
        result.data = structured
    elif result.ok and text:
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            result.data = parsed
    if not result.ok:
        result.error = (text or stderr or stdout or f"exit {returncode}").strip()[
            -2000:
        ]
        result.rate_limited = bool(_LIMIT.search(result.error))
    elif result.data is None and out.get("subtype") not in (None, "success"):
        result.ok = False
        result.error = f"claude ended with {out.get('subtype')}"
    return result


def run(
    config: Config,
    *,
    phase: str,
    prompt: str,
    system_prompt: str,
    timeout: float,
    workdir: Path,
    mcp_config: Path | None = None,
    schema: dict[str, Any] | None = None,
    journal: Path | None = None,
) -> ClaudeResult:
    """One headless call in an empty working directory."""
    workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        done = subprocess.run(
            command(
                config,
                phase=phase,
                system_prompt=system_prompt,
                mcp_config=mcp_config,
                schema=schema,
            ),
            input=prompt,
            capture_output=True,
            text=True,
            cwd=workdir,
            env=environment(phase, journal, workdir),
            timeout=max(timeout, 1),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ClaudeResult(ok=False, timed_out=True, error="time limit reached")
    except OSError as err:
        return ClaudeResult(ok=False, error=f"claude could not start: {err}")
    return parse(done.stdout, done.stderr, done.returncode)
