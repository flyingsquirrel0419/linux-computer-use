"""Integration tests against a real X server over MCP stdio.

They need DISPLAY pointing at a throwaway X server with a window manager
(MPX master creation needs one), e.g. `scripts/ci_x11.sh`. They click on the
display, so never point them at your desktop.
"""

import asyncio
import json
import os
import pathlib
import signal

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = pathlib.Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    os.environ.get("LCU_TEST_DISPLAY") is None,
    reason="set LCU_TEST_DISPLAY to a disposable X display (see scripts/ci_x11.sh)",
)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 120))


def params(entry: str) -> StdioServerParameters:
    env = {**os.environ, "DISPLAY": os.environ["LCU_TEST_DISPLAY"], "LCU_IME_BYPASS": "0"}
    return StdioServerParameters(command="uv", args=["--directory", str(ROOT), "run", entry], env=env)


async def _call_json(s: ClientSession, name: str, args: dict | None = None) -> dict:
    res = await s.call_tool(name, args or {})
    assert not res.isError, res.content
    return json.loads(res.content[0].text)


def test_tools_and_virtual_pointer():
    async def go():
        async with stdio_client(params("lcu")) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert len(names) == 18
            assert {"screenshot", "click", "type_text", "ui_tree", "click_element"} <= names
            info = await _call_json(s, "screen_info")
            assert info["virtual_pointer"] is True, info["error"]
            assert info["name"].startswith("lcu-")
            img = (await s.call_tool("screenshot", {})).content[0]
            assert img.type == "image" and img.mimeType == "image/png"
    run(go())


def test_click_lands_where_asked():
    async def go():
        async with stdio_client(params("lcu")) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            for x, y in [(100, 100), (700, 400), (50, 600)]:
                res = await s.call_tool("click", {"x": x, "y": y})
                assert not res.isError
                pos = await _call_json(s, "cursor_position")
                assert abs(pos["x"] - x) <= 1 and abs(pos["y"] - y) <= 1
    run(go())


def test_supervisor_restores_session_after_crash():
    async def go():
        async with stdio_client(params("lcu-supervised")) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            pid1 = int((await _call_json(s, "screen_info"))["name"].split("-")[1])
            os.kill(pid1, signal.SIGKILL)
            info = None
            for _ in range(5):  # the first call may hit the dying child
                try:
                    info = await _call_json(s, "screen_info")
                    break
                except Exception:
                    await asyncio.sleep(0.5)
            assert info is not None, "session not restored"
            pid2 = int(info["name"].split("-")[1])
            assert pid2 != pid1
            assert len((await s.list_tools()).tools) == 18
    run(go())
