"""Read-only smoke test: start the server over MCP stdio and call tools that
don't click or type. Usage: uv run python scripts/smoke_mcp.py [--direct]

By default it goes through the supervisor (lcu-supervised), like the
registered server; --direct talks to lcu.server itself. A screenshot is
saved to /tmp/lcu_smoke.png. Set DISPLAY=:99 to point it at a test display.
"""

import asyncio
import base64
import json
import os
import pathlib
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = pathlib.Path(__file__).resolve().parents[1]


def save_screenshot(data: str) -> pathlib.Path:
    with tempfile.NamedTemporaryFile(prefix="lcu_smoke_", suffix=".png",
                                     delete=False) as stream:
        stream.write(base64.b64decode(data))
        return pathlib.Path(stream.name)


async def main():
    entry = "lcu" if "--direct" in sys.argv else "lcu-supervised"
    # pass our environment through: the MCP client otherwise drops DISPLAY and
    # the server would autodetect the desktop session's display instead
    params = StdioServerParameters(command="uv", args=["--directory", str(ROOT), "run", entry],
                                   env=dict(os.environ))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print(f"{len(tools)} tools:", ", ".join(tools))
            info = json.loads((await s.call_tool("screen_info", {})).content[0].text)
            print("screen_info:", info)
            print("active_window:", (await s.call_tool("active_window", {})).content[0].text)
            windows = json.loads((await s.call_tool("list_windows", {})).content[0].text)
            print(f"list_windows: {len(windows)} windows")
            tree = (await s.call_tool("ui_tree", {"active_window_only": True})).content[0].text
            print("ui_tree (active window):", tree.splitlines()[0] if tree else "(empty)")
            img = (await s.call_tool("screenshot", {})).content[0]
            out = save_screenshot(img.data)
            print(f"screenshot: {img.mimeType} -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
