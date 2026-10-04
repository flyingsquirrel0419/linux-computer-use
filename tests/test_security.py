"""Security boundaries that can be checked without a live desktop."""

import json
import os
import base64
import pathlib
import subprocess
from types import SimpleNamespace

import pytest

from lcu import keymap_state

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_accessibility_rejects_other_display_and_stale_element(monkeypatch):
    from lcu import a11y

    class App:
        def get_application(self):
            return self

        def get_process_id(self):
            return 123

    app = App()
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(a11y, "_process_display", lambda pid: ":0")
    assert not a11y._on_configured_display(app)
    a11y._cache[1] = app
    with pytest.raises(RuntimeError, match="configured X display"):
        a11y.get(1)

    monkeypatch.setattr(a11y, "_process_display", lambda pid: ":99.0")
    assert a11y.get(1) is app
    a11y._cache.clear()


def test_accessibility_enumeration_filters_other_display(monkeypatch):
    from lcu import a11y

    class App:
        def __init__(self, pid):
            self.pid = pid

        def get_application(self):
            return self

        def get_process_id(self):
            return self.pid

    apps = [App(1), App(2), App(0)]
    desktop = SimpleNamespace(get_child_count=lambda: len(apps),
                              get_child_at_index=lambda index: apps[index])
    monkeypatch.setattr(a11y, "Atspi", SimpleNamespace(get_desktop=lambda _: desktop))
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(a11y, "_process_display", lambda pid: {1: ":99", 2: ":0"}[pid])
    assert list(a11y._apps()) == [apps[0]]


def test_accessibility_text_change_does_not_grab_focus(monkeypatch):
    from lcu import a11y

    class Editable:
        focused = False
        value = "old"

        def get_editable_text_iface(self):
            return self

    acc = Editable()
    monkeypatch.setattr(a11y, "get", lambda _id: acc)
    monkeypatch.setattr(a11y.Atspi.EditableText, "set_text_contents",
                        lambda obj, value: setattr(obj, "value", value) or True)
    assert a11y.set_text(1, "new")
    assert acc.value == "new" and not acc.focused


def test_keymap_recovery_restores_only_our_mapping(monkeypatch, tmp_path):
    class Display:
        mapping = {250: [0, 0], 251: [42, 42]}

        def get_keyboard_mapping(self, code, _count):
            return [self.mapping[code]]

        def change_keyboard_mapping(self, code, rows):
            self.mapping[code] = rows[0]

        def sync(self):
            pass

    fake = Display()
    tmp_path.chmod(0o700)
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(keymap_state, "_directory", lambda: tmp_path)
    monkeypatch.setattr(keymap_state.display, "get_core_display", lambda: fake)
    _, journal = keymap_state._paths()
    keymap_state.write(journal, [
        {"code": 250, "before": [0, 0], "bound": [123, 123]},
        {"code": 251, "before": [0, 0], "bound": [123, 123]},
    ])
    fake.mapping[250] = [123, 123]
    keymap_state.recover()
    assert fake.mapping == {250: [0, 0], 251: [42, 42]}
    assert not journal.exists()


def test_keymap_recovery_handles_crash_between_journal_and_rebind(monkeypatch, tmp_path):
    class Display:
        mapping = {250: [111, 0]}

        def get_keyboard_mapping(self, code, _count):
            return [self.mapping[code]]

        def change_keyboard_mapping(self, code, rows):
            self.mapping[code] = rows[0]

        def sync(self):
            pass

    fake = Display()
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(keymap_state, "_directory", lambda: tmp_path)
    monkeypatch.setattr(keymap_state.display, "get_core_display", lambda: fake)
    _, journal = keymap_state._paths()
    keymap_state.write(journal, [
        {"code": 250, "before": [0, 0], "bound": [222, 222], "prior": [111, 111]},
    ])
    keymap_state.recover()
    assert fake.mapping[250] == [0, 0]
    assert not journal.exists()


def test_keymap_journal_is_private_and_atomic(monkeypatch, tmp_path):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(keymap_state, "_directory", lambda: tmp_path)
    _, journal = keymap_state._paths()
    keymap_state.write(journal, [{"code": 250, "before": [0], "bound": [123]}])
    assert journal.stat().st_mode & 0o077 == 0
    assert json.loads(journal.read_text())[0]["code"] == 250
    assert not any(path.name.startswith(".keymap-") for path in tmp_path.iterdir())


def test_smoke_screenshot_uses_private_tempfile():
    from scripts.smoke_mcp import save_screenshot

    path = save_screenshot(base64.b64encode(b"private desktop pixels").decode())
    try:
        assert path.read_bytes() == b"private desktop pixels"
        assert os.stat(path).st_mode & 0o077 == 0
    finally:
        path.unlink()


def test_installer_only_migrates_its_own_server(tmp_path):
    home = tmp_path / "home"
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        f'[mcp_servers.linux-cu]\nargs = ["--directory", "{ROOT}", "run", "lcu"]\n'
        '[mcp_servers.other]\nargs = ["--directory", "/tmp/other", "run", "lcu"]\n'
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("claude", "codex"):
        script = binaries / name
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "PATH": f"{binaries}:{os.environ['PATH']}"}
    subprocess.run(["bash", str(ROOT / "install.sh")], env=env, check=True,
                   capture_output=True, text=True)
    result = config.read_text()
    assert f'[mcp_servers.linux-cu]\nargs = ["--directory", "{ROOT}", "run", "lcu-supervised"]' in result
    assert '[mcp_servers.other]\nargs = ["--directory", "/tmp/other", "run", "lcu"]' in result
    assert (config.parent / "config.toml.bak-lcu").exists()


def test_installer_preserves_same_named_server_in_another_checkout(tmp_path):
    home = tmp_path / "home"
    config = home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    original = '[mcp_servers.linux-cu]\nargs = ["--directory", "/tmp/another-project", "run", "lcu"]\n'
    config.write_text(original)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    script = binaries / "codex"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "PATH": f"{binaries}:{os.environ['PATH']}"}
    subprocess.run(["bash", str(ROOT / "install.sh"), "--auto-approve"], env=env, check=True,
                   capture_output=True, text=True)
    assert config.read_text() == original
    assert not (config.parent / "config.toml.bak-lcu").exists()


def test_supervisor_does_not_claim_an_ambiguous_action_failed(monkeypatch):
    from lcu import supervisor

    sent = []
    monkeypatch.setattr(supervisor, "_to_host", sent.append)
    monkeypatch.setattr(supervisor, "_log", lambda _: None)
    sup = supervisor.Supervisor()
    child = type("Child", (), {"returncode": -9})()
    sup.child = child
    sup.pending[1] = "tools/call"
    monkeypatch.setattr(sup, "start_child", lambda: None)
    sup.on_child_exit(child)
    error = json.loads(sent[0])
    assert "outcome unknown" in error["error"]["message"]
    assert "inspect the desktop before retrying" in error["error"]["message"]


def test_supervisor_shutdown_can_reenter_its_main_thread_lock(monkeypatch):
    from lcu import supervisor

    sup = supervisor.Supervisor()
    with sup.lock:
        sup.stop()
    assert sup.stopping
