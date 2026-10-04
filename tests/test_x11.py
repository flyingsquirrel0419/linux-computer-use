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
import subprocess
import sys
import time

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


def test_keymap_recovers_after_abrupt_child_exit(monkeypatch):
    """A killed typer cannot leave its borrowed keycode on the X server."""
    from lcu import display, keymap_state

    monkeypatch.setenv("DISPLAY", os.environ["LCU_TEST_DISPLAY"])
    child = subprocess.Popen(
        [sys.executable, "-c", """
import time
from lcu import input as inp, keymap_state, keys
with keymap_state.session() as journal:
    km = inp._Keymap(journal)
    km.bind([keys.char_keysym('한')])
    print(km.spares()[0], flush=True)
    time.sleep(60)
"""],
        env={**os.environ, "DISPLAY": os.environ["LCU_TEST_DISPLAY"]},
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        code_line = child.stdout.readline()
        assert code_line, child.stderr.read()
        code = int(code_line)
        assert any(display.get_core_display().get_keyboard_mapping(code, 1)[0])
        child.kill()
        child.wait(timeout=5)
        keymap_state.recover()
        assert not any(display.get_core_display().get_keyboard_mapping(code, 1)[0])
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_accessibility_excludes_apps_on_another_display(monkeypatch):
    from lcu import a11y

    other_display = ":100"
    xserver = subprocess.Popen(["Xvfb", other_display, "-screen", "0", "800x600x24", "-nolisten", "tcp"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    apps = []
    try:
        for _ in range(50):
            if subprocess.run(["xinput", "list"], env={**os.environ, "DISPLAY": other_display},
                              capture_output=True).returncode == 0:
                break
            time.sleep(0.1)
        app_code = """
import gi
import sys
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
window = Gtk.Window(title=sys.argv[1])
window.add(Gtk.Entry())
window.show_all()
print('ready', flush=True)
Gtk.main()
"""
        for target, title in ((other_display, "lcu-other-display-test"),
                              (os.environ["LCU_TEST_DISPLAY"], "lcu-local-display-test")):
            app = subprocess.Popen([sys.executable, "-c", app_code, title],
                                   env={**os.environ, "DISPLAY": target, "GTK_MODULES": "atk-bridge"},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            apps.append(app)
            assert app.stdout.readline() == "ready\n", app.stderr.read()
        monkeypatch.setenv("DISPLAY", os.environ["LCU_TEST_DISPLAY"])
        def visible_on_bus():
            desk = a11y.Atspi.get_desktop(0)
            titles = [
                desk.get_child_at_index(i).get_child_at_index(0).get_name() or ""
                for i in range(desk.get_child_count())
                if desk.get_child_at_index(i).get_child_count() > 0
            ]
            return any("lcu-other-display-test" in title for title in titles) and any(
                "lcu-local-display-test" in title for title in titles)
        for _ in range(30):
            if visible_on_bus():
                break
            time.sleep(0.1)
        assert visible_on_bus(), "test app never appeared on the shared AT-SPI bus"
        shown = [row["title"] for row in a11y.list_windows()]
        assert not any("lcu-other-display-test" in title for title in shown)
        assert any("lcu-local-display-test" in title for title in shown)
    finally:
        for app in apps:
            if app.poll() is None:
                app.terminate()
                app.wait(timeout=5)
        xserver.terminate()
        xserver.wait(timeout=5)
