# Contributing to linux-computer-use

Thanks for helping out. This guide covers how to get a working checkout, run the same checks as CI, and send a change.

## Questions and issues

- **Questions, bugs and ideas:** use [GitHub Issues](https://github.com/flyingsquirrel0419/linux-computer-use/issues). Labels such as `bug`, `enhancement`, `question`, `documentation` and `accessibility` help with triage. If you want somewhere to start, look for [`good first issue`](https://github.com/flyingsquirrel0419/linux-computer-use/labels/good%20first%20issue) and [`help wanted`](https://github.com/flyingsquirrel0419/linux-computer-use/labels/help%20wanted).
- **Bug reports:** include your distro and desktop (X11 session, window manager), the output of `screen_info`, the app you were driving, and what you expected to happen. Screenshots of your desktop can show private information, so crop or redact them before you post.
- **Security problems:** don't open a public issue. Report them privately as described in [SECURITY.md](SECURITY.md).

## Development setup

You need Linux with an X11 server, the system `python3` (3.10 or newer) with PyGObject and AT-SPI, [`uv`](https://docs.astral.sh/uv/) and `xinput`. On Ubuntu or Debian:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-atspi-2.0 at-spi2-core xinput
git clone https://github.com/flyingsquirrel0419/linux-computer-use
cd linux-computer-use
uv venv --python /usr/bin/python3 --system-site-packages   # the distro PyGObject is used, not a pip build
uv sync                                                    # also installs the dev group (pytest)
```

The venv must keep running the system interpreter. Don't add a `.python-version` file: uv would replace the venv with a different Python that can't load the distro PyGObject (see [CHANGELOG 0.1.1](CHANGELOG.md#011---2026-10-03)).

To try your checkout with Claude Code or Codex, run `./install.sh` from it. It registers this directory as the `linux-cu` server. Start a new agent session after changing server code.

## Checks

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs these on every push to `main` and on pull requests, on Python 3.10, 3.11 and 3.12. Run them locally before opening a PR:

```bash
uvx ruff check --select F,E9,PLE src scripts tests     # lint
uv run python -m pytest tests/test_units.py            # unit tests, no X server needed
dbus-run-session -- scripts/ci_x11.sh -v               # full suite on a throwaway display
claude plugin validate --strict .                      # plugin manifests, if you changed .claude-plugin/
```

`scripts/ci_x11.sh` starts its own `Xvfb` and window manager (openbox or muffin; one is needed to create the agent's pointer) on `:99`, then runs pytest there. Install them with `sudo apt install xvfb openbox dbus`.

> [!WARNING]
> The integration tests in `tests/test_x11.py` click and type on the display they are given. Only run them through `scripts/ci_x11.sh`, or with `LCU_TEST_DISPLAY` set to a disposable display, **never** against your own desktop. The same goes for anything you try by hand: start a test display (`Xvfb :99` plus a window manager) and run the server with `DISPLAY=:99`.

A read-only check against any display, including your own, is also available. It lists tools, reads screen info and saves a screenshot to `/tmp/lcu_smoke.png`. It doesn't click or type, but the agent cursor appears briefly:

```bash
uv run python scripts/smoke_mcp.py
```

## Making changes

- **Code layout:** the README's [Development](README.md#development) section maps each module (server, supervisor, virtual pointer, input, capture, AT-SPI, cursor overlay).
- **Tests:** a change in behaviour should come with a test. Pure logic goes in `tests/test_units.py`. Behaviour that needs an X server goes in `tests/test_x11.py`, which talks to the server over MCP like a real client.
- **Coordinates and input:** coordinates in tool arguments and results are always in screenshot space, and the agent's keystrokes go to the window under its own pointer. Keep both invariants when you touch `server.py`, `input.py` or `vpointer.py`.
- **README:** it exists in five languages (`README.md`, `README.ko.md`, `README.ja.md`, `README.zh-CN.md`, `README.es.md`) with the same section structure. Update all of them. If you can't translate, update `README.md`, say in the PR which translations are missing, and keep the headings and anchors aligned.
- **Changelog:** add user-visible changes to the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md).
- **Skill:** if a change affects how an agent should use the tools, update [`skills/linux-computer-use/SKILL.md`](skills/linux-computer-use/SKILL.md).
- **Demo GIF:** if you change the cursor or the demo flow, re-record [`docs/demo.gif`](docs/demo.gif) with `scripts/record_demo.sh`. It runs on its own throwaway display with a fake HOME, and needs Xvfb, muffin, nemo, gedit, gnome-terminal and ffmpeg.

## Commits and pull requests

- Branch from `main` and open a pull request against `main`. CI must pass.
- Keep each PR to one change. Describe what changed, why, and which checks you ran, including whether you ran `scripts/ci_x11.sh`.
- Commit messages in this repository use a short imperative summary line (for example "Fix first remapped character lost or mistyped in type_text"), then a body that explains the problem and the fix when that isn't obvious.

## Releasing (maintainers)

1. Move the `Unreleased` entries in `CHANGELOG.md` under a new version heading, and update the comparison links.
2. Bump the version in `pyproject.toml` and `.claude-plugin/plugin.json`, then run `uv lock`.
3. Commit as `Release vX.Y.Z`, push, and wait for CI to pass.
4. Tag `vX.Y.Z`, then create a GitHub release with the skill archive attached:

   ```bash
   (cd skills && zip -qrX ../dist/linux-computer-use-skill.zip linux-computer-use)
   gh release create vX.Y.Z dist/linux-computer-use-skill.zip --title vX.Y.Z --notes-file NOTES.md --verify-tag
   ```

## License

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE), as described in section 5 of the license.
