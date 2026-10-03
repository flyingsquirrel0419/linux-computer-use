"""Keep the MCP session alive across server crashes and exits.

MCP hosts (Claude Code, Codex) spawn a stdio server once and never reconnect:
if the process exits, the tools are gone for the rest of the session. This
supervisor is what the host launches instead. It runs the real server
(`python -m lcu.server`) as a child and relays newline-delimited JSON-RPC
between the two. When the child exits for any reason:

- requests it was still handling get a JSON-RPC error ("retry"), so the
  host doesn't wait forever;
- a new child is started (with backoff if it keeps dying) and the host's
  original `initialize` request + `notifications/initialized` are replayed
  to it; the replayed initialize response is swallowed;
- messages that arrive while the child is restarting are queued and sent
  once the new child is initialised.

The supervisor exits when the host closes stdin or signals it, and then
stops the child (which removes its virtual pointer).

Env: LCU_SUPERVISOR_LOG=/path/file to log restarts (default: stderr).
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time

CHILD = [sys.executable, "-m", "lcu.server"]
REPLAY_ID = "lcu-supervisor-replay-init"
RETRY_ERROR = -32000

_out_lock = threading.Lock()


def _log(msg: str) -> None:
    line = f"[lcu-supervisor {time.strftime('%H:%M:%S')}] {msg}\n"
    path = os.environ.get("LCU_SUPERVISOR_LOG")
    try:
        if path:
            with open(path, "a") as f:
                f.write(line)
        else:
            sys.stderr.write(line)
            sys.stderr.flush()
    except OSError:
        pass


def _to_host(raw: bytes) -> None:
    with _out_lock:
        sys.stdout.buffer.write(raw if raw.endswith(b"\n") else raw + b"\n")
        sys.stdout.buffer.flush()


class Supervisor:
    def __init__(self):
        self.lock = threading.Lock()
        self.child: subprocess.Popen | None = None
        self.ready = False            # child initialised and accepting traffic
        self.queue: list[bytes] = []  # host messages waiting for a ready child
        self.pending: dict = {}       # host request id -> method, in flight
        self.init_req: bytes | None = None
        self.init_note: bytes | None = None
        self.stopping = False
        self.restarts: list[float] = []

    # ------------------------------------------------------------ child
    def start_child(self) -> None:
        with self.lock:
            if self.stopping:
                return
            self.child = subprocess.Popen(CHILD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=None, bufsize=0)
            child = self.child
            # first start: the host's own initialize flows through normally.
            # restarts: replay it ourselves before letting traffic through.
            if self.init_req is None:
                # host hasn't initialised yet: plain relay from the start
                self.ready = True
                self._flush(child)
            else:
                self.ready = False
                msg = json.loads(self.init_req)
                msg["id"] = REPLAY_ID
                self._write(child, json.dumps(msg).encode())
        threading.Thread(target=self.pump_child, args=(child,), daemon=True).start()

    def _send(self, child: subprocess.Popen, raw: bytes) -> bool:
        """Write a host message to the child; track it if it's a request.
        Caller holds self.lock. Only messages actually handed to the child
        count as in flight, so a queued request is never failed twice."""
        if not self._write(child, raw):
            return False
        try:
            msg = json.loads(raw)
        except ValueError:
            return True
        if isinstance(msg, dict) and msg.get("method") and "id" in msg:
            self.pending[msg["id"]] = msg["method"]
        return True

    def _flush(self, child: subprocess.Popen) -> None:
        queued, self.queue = self.queue, []
        for i, q in enumerate(queued):
            if not self._send(child, q):
                self.queue = queued[i:]
                return

    def _write(self, child: subprocess.Popen, raw: bytes) -> bool:
        try:
            child.stdin.write(raw if raw.endswith(b"\n") else raw + b"\n")
            child.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def pump_child(self, child: subprocess.Popen) -> None:
        """child stdout -> host, until the child goes away."""
        for raw in iter(child.stdout.readline, b""):
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == REPLAY_ID and "method" not in msg:
                with self.lock:  # replayed init answered: reconnect complete
                    if self.init_note:
                        self._write(child, self.init_note)
                    self.ready = True
                    self._flush(child)
                _log(f"server restarted (pid {child.pid}), session restored")
                continue
            if isinstance(msg, dict) and "id" in msg and "method" not in msg:
                with self.lock:
                    self.pending.pop(msg["id"], None)
            _to_host(raw)
        child.wait()
        self.on_child_exit(child)

    def on_child_exit(self, child: subprocess.Popen) -> None:
        with self.lock:
            if child is not self.child:
                return
            self.ready = False
            lost, self.pending = self.pending, {}
            stopping = self.stopping
        if stopping:
            return
        _log(f"server exited with code {child.returncode}; restarting")
        for rid, method in lost.items():
            _to_host(json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "error": {"code": RETRY_ERROR,
                          "message": f"linux-cu server restarted while handling {method}; please retry"},
            }).encode())
        # backoff: immediate at first, up to 10 s if it keeps crashing
        now = time.monotonic()
        self.restarts = [t for t in self.restarts if now - t < 60] + [now]
        delay = min(10.0, 0.25 * 2 ** max(0, len(self.restarts) - 3)) if len(self.restarts) > 2 else 0.0
        if delay:
            _log(f"{len(self.restarts)} restarts in 60 s; waiting {delay:.1f}s")
            time.sleep(delay)
        self.start_child()

    # ------------------------------------------------------------- host
    def from_host(self, raw: bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            msg = None
        with self.lock:
            if isinstance(msg, dict):
                method = msg.get("method")
                if method == "initialize":
                    self.init_req = raw
                elif method == "notifications/initialized":
                    self.init_note = raw
            child = self.child
            if self.ready and child is not None and child.poll() is None and self._send(child, raw):
                return
            self.queue.append(raw)

    def stop(self) -> None:
        with self.lock:
            self.stopping = True
            child = self.child
        if child is not None and child.poll() is None:
            try:
                child.stdin.close()
            except OSError:
                pass
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()


def main() -> None:
    sup = Supervisor()

    def on_signal(signum, _frame):
        sup.stop()
        os._exit(128 + signum)

    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, on_signal)

    sup.start_child()
    for raw in iter(sys.stdin.buffer.readline, b""):
        if raw.strip():
            sup.from_host(raw)
    sup.stop()  # host closed stdin: shut down for real


if __name__ == "__main__":
    main()
