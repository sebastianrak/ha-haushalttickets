"""A stand-in for ha-mcp over stdio (tests only)."""

import os

from mcp.server.fastmcp import FastMCP

server = FastMCP("fake-ha-mcp")


@server.tool()
def ha_read_file(path: str) -> dict:
    """Return a file like ha-mcp does."""
    return {"success": True, "path": path, "content": f"# {path}\n"}


@server.tool()
def ha_config_set_label(name: str, icon: str | None = None) -> dict:
    """Takes a tool argument called name."""
    return {"success": True, "label": {"name": name, "icon": icon}}


@server.tool()
def ha_whoami() -> dict:
    """What the server got in its environment."""
    return {
        "url": os.environ.get("HOMEASSISTANT_URL"),
        "yaml": os.environ.get("ENABLE_YAML_CONFIG_EDITING"),
        "confirm": os.environ.get("ENABLE_YAML_EDIT_CONFIRM"),
        "cwd": os.getcwd(),
        "settings_ui": os.environ.get("HA_MCP_DISABLE_SETTINGS_UI"),
    }


@server.tool()
def ha_fails() -> dict:
    """A failing call."""
    return {"success": False, "error": "nope"}


if __name__ == "__main__":
    server.run()
