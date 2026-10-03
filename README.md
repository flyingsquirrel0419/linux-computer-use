# linux-computer-use

Claude Code·Codex 등 MCP를 지원하는 에이전트가 **리눅스 X11 데스크톱**을 직접 조작하게 해주는 stdio MCP 서버.
외부 바이너리(xdotool, scrot 등) 없이 Python + XTest + AT-SPI로 동작한다.

## 툴
| 분류 | 툴 |
|---|---|
| 보기 | `screenshot`(영역 확대 가능), `screen_info`, `cursor_position`, `active_window` |
| 마우스 | `click`(더블/트리플, 수정키), `mouse_move`, `drag`, `mouse_down/up`, `scroll` |
| 키보드 | `type_text`(한글·이모지 포함 유니코드), `key`("ctrl+shift+t", "alt+F4"...), `hold_key` — `expect_window`로 포커스 불일치 시 전송 거부 |
| 접근성 | `list_windows`, `ui_tree`, `click_element`, `set_text` |
| 기타 | `wait` |

- 모든 좌표는 **스크린샷 이미지 좌표계**. 긴 변 1280px로 축소되며 서버가 실제 픽셀로 변환한다 (`LCU_MAX_LONG_EDGE`로 조정).
- 동작 툴은 `screenshot_after=true`로 결과 화면을 같은 호출에서 받을 수 있다.
- 스크린샷의 빨간 십자가 마우스 포인터.

## 가상 마우스 (에이전트 전용 포인터)
Codex computer use처럼 에이전트가 **자기 마우스와 키보드**를 갖는다. X Input 2의 멀티 포인터(MPX)로
`lcu-<pid>`라는 두 번째 포인터·키보드 쌍을 만들고, 서버의 모든 XTest 입력을 그쪽으로 보낸다.

- 사용자 마우스는 움직이지 않고 키보드 포커스도 바뀌지 않는다. 에이전트가 일하는 동안 사용자도 계속 작업할 수 있다.
- 에이전트 클릭은 창을 앞으로 올리거나 활성화하지 않는다. 키 입력은 **에이전트 포인터 아래 창**으로 간다.
- 에이전트마다 포인터가 따로 생긴다. Claude Code와 Codex를 동시에 띄우면 커서도 두 개다. 서버가 끝나면 포인터를 지우고,
  비정상 종료로 남은 포인터는 다음 실행 때 정리한다.
- 화면에는 **Codex computer use 커서와 같은 모양과 움직임**의 오버레이 커서가 그려진다.
  - 모양: Codex `AgentCursor` 윤곽(14px, 핫스팟은 화살표 끝이 아니라 **중앙**), 반투명 그라데이션 채움, 1.55px 테두리, 아래쪽 글로우
  - 이동: 20개 후보 중 고른 3차 곡선 경로 + 스프링(감쇠 0.9). 196px 이상 이동하면 진행 방향으로 늘어나고(×1.38/×0.82) 회전(최대 76°)
  - 클릭: 250ms 동안 눌림 펄스(크기 −10%). 대기(생각) 중에는 살짝 흔들린다
  - 색은 배경화면에서 뽑는다(Codex와 같은 방식). 오버레이는 클릭을 통과시킨다
  - 글리프 좌표와 모션 상수는 maka-agent가 Codex 앱 바이너리에서 복원한 값이다(Apache-2.0, [NOTICE](NOTICE) 참고)

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `LCU_VIRTUAL_POINTER` | `1` | `0`이면 사용자 마우스를 같이 씀 |
| `LCU_OVERLAY` | `1` | `0`이면 오버레이 커서 끔 |
| `LCU_GLIDE` | `1` | `0`이면 곡선·스프링 이동 없이 즉시 이동 |
| `LCU_CURSOR_COLOR` | 배경화면 | `#rrggbb` 고정 색 |
| `LCU_CURSOR_LABEL` | 없음 | 이름표 문구, `auto`면 클라이언트 이름(Claude/Codex) |
| `LCU_CURSOR_ICON` | Codex 글리프 | 직접 만든 PNG/SVG 아이콘 |
| `LCU_CURSOR_HOTSPOT` | `0,0` | 아이콘 안에서 클릭 지점(px) |
| `LCU_CURSOR_SIZE` | `28` | 커스텀 아이콘 높이(px) |
| `LCU_CURSOR_SCALE` | `1.0` | 커서 배율(1.0 = 14px) |

환경변수는 MCP 등록의 `env`에 넣는다. Codex는 `[mcp_servers.linux-cu.env]`, Claude Code는 `claude mcp add -e KEY=VAL ...`.
`xinput` 패키지가 필요하며, 없으면 자동으로 공유 포인터 모드로 동작한다.
한계: 에이전트는 화면에 보이는 창만 조작할 수 있다. 가려진 창은 클릭이 위쪽 창으로 간다.

## 설치 (한 번에)
```bash
git clone https://github.com/flyingsquirrel0419/linux-computer-use ~/Documents/linux-computer-use
~/Documents/linux-computer-use/install.sh
```
`install.sh`는 venv 생성(시스템 PyGObject 사용) → Claude Code·Codex에 MCP 서버 등록 → 스킬을
`~/.claude/skills`, `~/.codex/skills`에 심링크한다. 다시 실행해도 안전하다.

## 스킬
[`skills/linux-computer-use/SKILL.md`](skills/linux-computer-use/SKILL.md)는 Anthropic computer use 방식의 작업 지침이다:
보기 → 찾기(접근성 트리 우선) → 한 동작 → 검증 루프, 좌표 규칙, 입력 전 포커스 확인, 대기, 막혔을 때 대처,
화면 속 지시문을 데이터로 취급하기, 되돌릴 수 없는 동작 전 사용자 확인.

## 수동 설치
```bash
uv venv --python /usr/bin/python3 --system-site-packages   # 시스템 PyGObject(gi) 사용
uv sync
```
필요 시스템 패키지(Ubuntu): `python3-gi gir1.2-atspi-2.0 at-spi2-core` (보통 기본 설치됨).

## 등록
Claude Code:
```bash
claude mcp add -s user linux-cu -- uv --directory /home/flyingsquirrel/Documents/linux-computer-use run lcu
```
Codex (`~/.codex/config.toml`):
```toml
[mcp_servers.linux-cu]
command = "uv"
args = ["--directory", "/home/flyingsquirrel/Documents/linux-computer-use", "run", "lcu"]
startup_timeout_sec = 30
tool_timeout_sec = 120
default_tools_approval_mode = "approve"   # 매 호출 승인 생략 (codex exec에선 필수)
```
`DISPLAY`, `XAUTHORITY`, `DBUS_SESSION_BUS_ADDRESS`는 비어 있으면 서버가 자동 감지한다(`src/lcu/env.py`).

## 접근성 트리 활성화
GTK 앱은 대개 바로 보인다. 안 보이면:
```bash
gsettings set org.gnome.desktop.interface toolkit-accessibility true
```
Chrome/Chromium/Electron 앱(VS Code, Slack 등)은 `--force-renderer-accessibility` 플래그로 실행해야 트리가 나온다.

## 한글 입력기(ibus)
ibus-hangul이 한글 모드면 합성 키 입력의 영문이 한글로 조합된다. `type_text`는 입력 동안 ibus 엔진을
`xkb:us::eng`로 바꿨다가 원래 엔진으로 복원한다(복원 후 한/영 상태는 ibus-hangul의 `initial-input-mode`로 돌아감).
레이아웃에 없는 문자(한글 등)는 빈 keycode에 유니코드 keysym을 임시 바인딩해 입력한다.
끄려면 `LCU_IME_BYPASS=0`.

## 한계
- **X11 전용.** Wayland 세션은 XTest/화면 캡처가 막혀 있어 동작하지 않는다(XWayland 앱만 부분 동작).
- 안전장치 없음: 에이전트가 실제 데스크톱을 그대로 조작한다. 격리가 필요하면 `Xvfb :99` 같은 별도 디스플레이에서
  `DISPLAY=:99`로 서버를 띄우면 된다.

## 테스트
```bash
uv run python scripts/smoke_mcp.py   # gedit을 띄워둔 상태에서 MCP로 툴 호출
```
