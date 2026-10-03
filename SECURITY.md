# Security policy

## Reporting a vulnerability

Report vulnerabilities privately through **GitHub private vulnerability reporting**:
[open a draft security advisory](https://github.com/flyingsquirrel0419/linux-computer-use/security/advisories/new)
(or go to the repository's **Security** tab and click **Report a vulnerability**).

Please don't open a public issue, pull request or discussion for a vulnerability, and don't post exploit details anywhere public until a fix is released.

A useful report includes:

- **Version:** the release or commit, and how you installed it (`install.sh`, manual registration, plugin).
- **Environment:** distro, X11 session and window manager, and which agent drove the server (Claude Code, Codex, other).
- **Impact:** what an attacker can do and under which conditions.
- **Reproduction:** minimal steps. Run them on a throwaway display (`Xvfb` plus a window manager), not on a desktop holding real data.
- **Evidence:** logs or screenshots with tokens, personal data and unrelated windows removed.

This is a small, maintainer-run project. Reports are handled on a best-effort basis, without a fixed response time. Fixes land on `main` and ship in the next release. Older releases don't get backports. The [CHANGELOG](CHANGELOG.md) and the advisory note security fixes.

## Scope

linux-computer-use deliberately gives an AI agent control of an X11 desktop. Driving the mouse and keyboard and taking screenshots is the purpose of the project, not a vulnerability. Reports are in scope when the software does something it isn't meant to, for example:

- **Wrong target:** input or screenshots reach a display other than the configured one, or the server keeps acting after the host closes the session.
- **Broken input safety:** keystrokes reach a window that `expect_window` should have blocked, or the agent's input escapes its own virtual pointer and keyboard and moves the user's real pointer or focus while `virtual_pointer` is `true`.
- **Things left behind:** after the server exits, a borrowed keycode stays remapped, or an extra input device or pointer stays.
- **Supervisor:** `lcu-supervised` replays or forwards messages so that an action runs twice or goes to the wrong session.
- **Installer:** `install.sh` changes configuration beyond what its README section describes, or follows paths that someone else controls.
- **Code execution:** code or command execution through tool arguments (for example key names, text or element ids), or through environment variables a host would normally pass.
- **Leaks:** the cursor overlay or the server exposes data to other local users.

Out of scope:

- **Prompt injection:** an agent that follows instructions shown on screen. That is a property of the model and the agent host. The [skill](skills/linux-computer-use/SKILL.md) tells agents to treat on-screen text as data. Reports that the guidance itself is missing or misleading are welcome as normal issues.
- **Unsandboxed operation:** the agent can do anything the logged-in user can do on the desktop it controls.
- **Vulnerabilities in dependencies:** report those to the dependency first, unless this project uses it in an unsafe way.

## Security considerations for users

- The server operates your **real desktop**, and screenshots of it are sent to the model provider your agent uses.
- By default Codex asks before each call to these tools. `install.sh --auto-approve` sets `default_tools_approval_mode = "approve"`, so Codex calls them **without asking**. Use it only if you accept that, and turn it off with `install.sh --no-auto-approve`. Registrations made by versions up to 0.1.1 have it on: run `install.sh --no-auto-approve` to turn it off.
- To keep the agent away from your own session, run the server on a separate display (for example `Xvfb :99` plus a window manager) by setting `DISPLAY=:99` in its environment. See the README's [Safety](README.md#safety) section.
