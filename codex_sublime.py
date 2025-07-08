"""
Place this file in Packages/User, restart Sublime Text,
then select text and run “Codex: Prompt” from the Command Palette.
"""

import json
import os
import signal
import subprocess
import threading
import uuid

# For recursive process-tree termination (POSIX-only)
from typing import List

import sublime
import sublime_plugin

# NOTE: The prints sprinkled throughout this file are *intentional* and are
# meant to aid troubleshooting.  They can be removed or redirected to the
# Sublime console once the lifecycle logic is verified to work as expected.

CODEX_BIN = os.getenv('CODEX_BIN', '/opt/homebrew/bin/codex')
MODEL = os.getenv('CODEX_MODEL', 'codex-mini-latest')


class _CodexBridge:
    def __init__(self):
        print('[CodexBridge] launching subprocess')
        # Prepare environment for the Codex subprocess
        env = os.environ.copy()
        env['OPENAI_API_KEY'] = OPENAI_API_KEY
        if not OPENAI_API_KEY:
            sublime.error_message(
                'Missing environment variable: OPENAI_API_KEY. '
                'Create an API key (https://platform.openai.com) and export it as an environment variable.'
            )
            raise RuntimeError('Missing OPENAI_API_KEY')
        popen_kwargs = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=True,  # make the subprocess the leader of a new process group
        )

        self.proc = subprocess.Popen([CODEX_BIN, 'proto'], **popen_kwargs)
        self._lock = threading.Lock()
        self._callbacks = {}
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._configure_session()

    def terminate(self):
        """Terminate the Codex subprocess (if still running)."""

        if self.proc.poll() is None:
            print('[CodexBridge] terminating subprocess PID', self.proc.pid)
            # 1. First, try to kill any children/descendants while the original
            #    process is still alive so their PPIDs are intact.
            _kill_process_tree(self.proc.pid)

            try:
                # Ask politely first.
                self.proc.terminate()
            except Exception as e:
                print('[CodexBridge] terminate() failed:', e)

            # Give the process a moment to exit.
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                print('[CodexBridge] subprocess did not exit in time; killing')

            # Try killing its original process group (may still contain
            # stubborn descendants if they kept the same PGID).
            # pgid = None
            # try:
            #     pgid = os.getpgid(self.proc.pid)
            #     print('[CodexBridge] sending SIGKILL to process group', pgid)
            #     os.killpg(pgid, signal.SIGKILL)
            # except Exception as e:
            #     print('[CodexBridge] killpg() failed:', e)

            # As a belt-and-braces measure, call tree-killer once more in case
            # new descendants appeared after the first pass or adopted a new
            # parent.
            _kill_process_tree(self.proc.pid)

        else:
            print('[CodexBridge] subprocess already exited (might have left children)')

        # Regardless of whether the root proc is still alive, attempt to kill
        # any stray children that may have been spawned and detached.
        _kill_process_tree(self.proc.pid)

    # --------------------------------------------------------------------- I/O
    def send(self, obj, cb):
        """Send a JSON message to the Codex subprocess.

        `obj` must include a unique "id" so we can route replies.  `cb` is an
        optional callback that will be invoked with the parsed event whenever
        we receive a related message from Codex.
        """

        line = json.dumps(obj) + '\n'
        with self._lock:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
        if cb:
            self._callbacks[obj['id']] = cb

    # ----------------------------------------------------------------- reader
    def _read_loop(self):
        """Background thread that reads stdout from Codex and dispatches events."""

        for line in self.proc.stdout:
            raw = line.rstrip()
            print('[Codex raw]', raw)

            # Attempt to extract JSON payload (strip potential ANSI / prefixes)
            idx = raw.find('{')
            if idx < 0:
                continue
            payload = raw[idx:]
            try:
                event = json.loads(payload)
            except Exception as e:
                print('[Codex parse error]', e, payload)
                continue

            call_id = event.get('id')
            msg = event.get('msg', {})
            msg_type = msg.get('type')

            if call_id in self._callbacks and msg_type in (
                'assistant_message',
                'agent_reasoning',
                'agent_message',
            ):
                cb = self._callbacks[call_id]
                # For final messages, drop callback afterwards
                if msg_type in ('assistant_message', 'agent_message'):
                    del self._callbacks[call_id]
                sublime.set_timeout(lambda e=event, c=cb: c(e), 0)

    # -------------------------------------------------------------- session
    def _configure_session(self):
        cfg_id = str(uuid.uuid4())
        self.send(
            {
                'id': cfg_id,
                'op': {
                    'type': 'configure_session',
                    'model': 'codex-mini-latest',
                    'approval_policy': 'unless-allow-listed',
                    'sandbox_policy': {'permissions': [], 'mode': 'read-only'},
                    'cwd': '.',
                },
            },
            cb=None,
        )


# ---------------------------------------------------------------------------
# Helper to recursively kill all descendant processes (POSIX).


def _kill_process_tree(root_pid: int):
    """Recursively SIGKILL *root_pid* and all its children (best effort)."""

    try:
        # Build child map: parent_pid -> [child_pid]
        output = subprocess.check_output(['ps', '-o', 'pid=', '-o', 'ppid=', '-A'], text=True)
    except Exception as exc:
        print('[CodexBridge] ps enumeration failed:', exc)
        return

    children_map = {}
    for line in output.strip().splitlines():
        try:
            pid_str, ppid_str = line.strip().split(None, 1)
            pid = int(pid_str)
            ppid = int(ppid_str)
            children_map.setdefault(ppid, []).append(pid)
        except ValueError:
            continue

    # BFS to gather descendants
    to_visit: List[int] = [root_pid]
    descendants: List[int] = []
    while to_visit:
        current = to_visit.pop()
        for child in children_map.get(current, []):
            descendants.append(child)
            to_visit.append(child)

    # Kill descendants first, then root.
    for pid in descendants:
        try:
            print('[CodexBridge] SIGKILL child', pid)
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

    # Finally kill the root.
    # Attempt to kill root last.
    try:
        print('[CodexBridge] SIGKILL root', root_pid)
        os.kill(root_pid, signal.SIGKILL)
    except Exception:
        pass


# Keep a separate Codex bridge (and hence a separate Codex subprocess / session)
# for each Sublime Text window.  This allows having independent conversations
# per-window while ensuring that subsequent prompts in the same window are sent
# to the existing session instead of spawning a new Codex instance every time.

_bridges = {}


def _get_bridge(window):
    """Return a bridge bound to the given window.

    A new bridge (Codex subprocess) is created the first time a window sends a
    prompt.  Subsequent prompts coming from the same window will reuse the
    existing bridge, guaranteeing a single Codex instance per window.
    """

    # In some rare situations `window` may be ``None`` (e.g. during unit tests
    # or if the command is executed without an active window).  Fall back to a
    # shared global bridge in that scenario so the command continues to work.
    if window is None:
        key = '__global__'
    else:
        key = window.id()

    bridge = _bridges.get(key)
    if bridge is None:
        print('[Codex] creating new bridge for window', key)
        bridge = _CodexBridge()
        _bridges[key] = bridge
    else:
        print('[Codex] reusing existing bridge for window', key)
    return bridge


# ===================================================================== command
class CodexPromptCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        print('wtf')
        prompt = self._collect_prompt()
        if not prompt:
            sublime.status_message('Select some text first')
            return

        bridge = _get_bridge(self.view.window())
        msg_id = str(uuid.uuid4())
        bridge.send(
            {
                'id': msg_id,
                'op': {
                    'type': 'user_input',
                    'items': [{'type': 'text', 'text': prompt}],
                },
            },
            cb=lambda event: self._handle_event(event, prompt),
        )

    # ------------------------------------------------------------- helpers ----
    def _collect_prompt(self):
        view = self.view
        for region in view.sel():
            if not region.empty():
                return view.substr(region)
        return None

    def _handle_event(self, event, prompt):
        msg = event.get('msg', {})
        msg_type = msg.get('type')
        # collect any text from assistant or reasoning messages
        if msg_type == 'assistant_message':
            items = msg.get('items', [])
            text_items = [i.get('text', '') for i in items if i.get('type') == 'text']
        elif 'text' in msg:
            text_items = [msg.get('text', '')]
        elif 'message' in msg:
            text_items = [msg.get('message', '')]
        elif 'last_agent_message' in msg:
            text_items = [msg.get('last_agent_message', '')]
        elif 'command' in msg:
            text_items = [msg.get('command', '')]
        elif 'stdout' in msg:
            text_items = [msg.get('stdout', '')]
        elif 'stderr' in msg:
            text_items = [msg.get('stderr', '')]
        else:
            return
        # nothing to show
        if not any(text_items):
            return
        window = self.view.window()
        panel = window.find_output_panel('codex') or window.create_output_panel('codex')
        panel.set_read_only(False)
        content = '>>> ' + prompt + '\n' + ''.join(text_items) + '\n\n'
        panel.run_command('append', {'characters': content})
        panel.set_read_only(True)
        window.run_command('show_panel', {'panel': 'output.codex'})


# ============================================================= lifecycle ----
class CodexWindowEventListener(sublime_plugin.EventListener):
    """Clean up Codex bridges when their associated window is closed."""

    # We cannot directly listen for a window-close event with the standard
    # EventListener API, so we approximate it by responding to *any* view being
    # closed and checking whether that was the last view in its window.  If a
    # window becomes empty, Sublime will close it next, so we tidy up beforehand.

    def on_pre_close(self, view):  # type: ignore[override]
        window = view.window()
        if window is None:
            return

        # If this view is the last one remaining in the window, the window is
        # about to disappear.  Clean up the bridge.
        if len(window.views()) <= 1:
            key = window.id()
            print('[CodexWindowEventListener] on_pre_close triggered for window', key)
            bridge = _bridges.pop(key, None)
            if bridge is not None:
                bridge.terminate()


# ---------------------------------------------------------------- watchdog --

# In case the view/window close events we listen to above are not sufficient on
# certain platforms or ST builds, run a small periodic watchdog that compares
# the set of still-open window ids against the keys in _bridges and terminates
# orphaned Codex subprocesses.  This ensures cleanup happens eventually even if
# we miss an event.


def _watchdog_tick():
    live_window_ids = {w.id() for w in sublime.windows()}
    stale_keys = [wid for wid in list(_bridges.keys()) if wid not in live_window_ids and wid != '__global__']

    for wid in stale_keys:
        bridge = _bridges.pop(wid, None)
        if bridge is not None:
            print('[Codex] watchdog terminating orphaned bridge for window', wid)
            bridge.terminate()

    # Schedule next run
    sublime.set_timeout(_watchdog_tick, 5_000)

    # Some builds of Sublime Text may not fire on_pre_close for certain window
    # closing paths.  Add a fallback using on_close so that we can observe which
    # callbacks are triggered.  The logic is identical but with additional
    # logging so you can compare behaviour in the console.

    def on_close(self, view):  # type: ignore[override]
        window = view.window()
        if window is None:
            print('[CodexWindowEventListener] on_close – view had no window')
            return

        if len(window.views()) == 0:
            key = window.id()
            print('[CodexWindowEventListener] on_close triggered for window', key)
            bridge = _bridges.pop(key, None)
            if bridge is not None:
                bridge.terminate()


# ------------------------------------------------------------------- unload


def plugin_unloaded():
    """Terminate all running Codex subprocesses when the plugin is reloaded."""

    print('[Codex] plugin_unloaded – cleaning up bridges')
    for key, bridge in list(_bridges.items()):
        bridge.terminate()
        _bridges.pop(key, None)


def plugin_loaded():
    print('[Codex] plugin_loaded – plugin is active')
    # kick off watchdog
    _watchdog_tick()
