"""Top-level keys of a Home Assistant YAML file, as text.

Used to put back what the nightly run changed in ``configuration.yaml`` or a
package when Home Assistant's configuration check fails. Works on lines, not on a
parsed document, so tags such as ``!secret`` and ``!include`` and all comments
stay exactly as they were.
"""

from __future__ import annotations

import re
import textwrap

_KEY = re.compile(r"^(?P<key>[A-Za-z0-9_][^\s:#]*):(?P<rest>(?:\s.*)?)$")


def top_level(text: str) -> dict[str, str]:
    """Map each top-level key to the text of its value.

    A block value is returned dedented (as ``ha_config_set_yaml`` expects it), an
    inline value (``key: !include x.yaml``) as written after the colon.
    """
    keys: dict[str, list[str]] = {}
    inline: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        match = _KEY.match(line)
        if match:
            current = match["key"]
            keys[current] = []
            rest = match["rest"].strip()
            if rest and not rest.startswith("#"):
                inline[current] = rest
            continue
        if current is not None:
            keys[current].append(line)
    values: dict[str, str] = {}
    for key, lines in keys.items():
        # Comments and blank lines at column 0 after a block belong to the gap
        # before the next key, not to the value.
        while lines and (not lines[-1].strip() or lines[-1].startswith("#")):
            lines.pop()
        block = textwrap.dedent("\n".join(lines)).strip("\n")
        if key in inline:
            values[key] = inline[key] if not block else f"{inline[key]}\n{block}"
        else:
            values[key] = block
    return values
