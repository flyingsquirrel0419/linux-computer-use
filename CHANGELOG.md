# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Dates are release
dates in UTC.

## [Unreleased]

## [0.1.1] - 2026-10-03

### Fixed

- On distros whose `python3` is 3.10 or 3.11 (Ubuntu 22.04, Debian 12), `uv sync` no longer replaces the venv created by `install.sh` with a uv-managed Python 3.12. The `.python-version` pin that caused it is removed. Before this fix, the distro PyGObject couldn't load, so the accessibility tree and the agent cursor didn't work. If you installed on such a system, re-run `install.sh`.

### Added

- CI on Python 3.10, 3.11 and 3.12, each in a distro container that ships that version: lint, unit tests, and X11 integration tests on Xvfb + openbox. The integration tests cover the tool list, virtual pointer creation, click accuracy and supervisor crash recovery.
- `scripts/ci_x11.sh` to run the test suite locally on a throwaway display.
- A demo GIF in the README, showing a multi-app task (terminal, file manager, text editor, git) done through the MCP tools, and `scripts/record_demo.sh` to re-record it on a throwaway display with a fake HOME.
- README badges for CI, release, license, Python version and downloads.

## [0.1.0] - 2026-10-03

First release.

### Added

- An MCP server for Linux X11 desktops with 18 tools:
  - **Seeing:** screenshots (with region zoom), screen info, cursor position, active window.
  - **Mouse:** click, move, drag, button down/up, scroll.
  - **Keyboard:** Unicode typing, key chords, held keys.
  - **Accessibility:** `list_windows`, `ui_tree`, `click_element`, `set_text` over AT-SPI.
  - **Other:** `wait`.

  It is pure Python, with no xdotool or scrot.
- A virtual pointer: each agent gets its own X Input 2 master pointer and keyboard, so the user's mouse and keyboard focus are left alone. Agent keystrokes go to the window under the agent's pointer, and `expect_window` refuses to send keys to the wrong window.
- An agent cursor overlay with the Codex computer-use glyph and motion: spring glide along a planned arc, stretch and rotation on long moves, a press pulse, an idle wiggle and a wallpaper-derived colour. The glyph and motion constants are ported from maka-agent (Apache-2.0); see `NOTICE`.
- Typing for characters missing from the keyboard layout (Hangul, CJK, emoji), using batched spare-keycode bindings. While typing, the ibus Hangul input mode is bypassed.
- `lcu-supervised`, which restarts the server when it exits and replays the MCP handshake, so the tools survive crashes mid-session.
- The `linux-computer-use` skill, installable through `install.sh` (Claude Code and Codex) or as a Claude Code plugin from this repository's marketplace.
- `install.sh` to set up the venv, register the server with Claude Code and Codex, and link the skill.
- A README in English, Korean, Japanese, Simplified Chinese and Spanish.
- The Apache-2.0 license.

[Unreleased]: https://github.com/flyingsquirrel0419/linux-computer-use/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/flyingsquirrel0419/linux-computer-use/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/flyingsquirrel0419/linux-computer-use/releases/tag/v0.1.0
