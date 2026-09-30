import asyncio
import importlib.metadata
import json
import os
import tempfile
from pathlib import Path

from mcp import Client, StdioServerParameters


WORKSPACE = Path(__file__).resolve().parents[2]
CONFIG_PATH = WORKSPACE / ".vscode" / "mcp.json"
EXPECTED_TOOL_COUNT = 20


def expand_workspace(value: str) -> str:
    return value.replace("${workspaceFolder}", str(WORKSPACE))


async def verify() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    server = config["servers"]["geoAgent"]
    if server["type"] != "stdio":
        raise RuntimeError("geoAgent must use the stdio transport")

    command = Path(expand_workspace(server["command"]))
    if not command.is_file():
        raise RuntimeError(
            "The configured interpreter does not exist. Run "
            "'GEO: Bootstrap Python Environment' first."
        )

    environment = os.environ.copy()
    environment.update({
        name: expand_workspace(str(value))
        for name, value in server.get("env", {}).items()
    })
    with tempfile.TemporaryDirectory(prefix="geo-vscode-mcp-") as data_dir:
        environment["GEO_DATA_DIR"] = data_dir
        parameters = StdioServerParameters(
            command=str(command),
            args=[expand_workspace(str(value)) for value in server.get("args", [])],
            env=environment,
            cwd=expand_workspace(server.get("cwd", "${workspaceFolder}")),
        )
        async with Client(parameters) as client:
            tools = await client.list_tools()
            if len(tools.tools) != EXPECTED_TOOL_COUNT:
                raise RuntimeError(
                    f"Expected {EXPECTED_TOOL_COUNT} tools, found {len(tools.tools)}"
                )
            capabilities = await client.call_tool("geo_get_capabilities", {})
            if capabilities.is_error is True or capabilities.structured_content is None:
                raise RuntimeError("geo_get_capabilities did not return structured content")

    print(json.dumps({
        "server": "geoAgent",
        "transport": "stdio",
        "mcp_version": importlib.metadata.version("mcp"),
        "tool_count": EXPECTED_TOOL_COUNT,
        "capabilities": "ok",
    }, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(verify())
