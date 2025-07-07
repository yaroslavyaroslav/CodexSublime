"""
Place this file in Packages/User, restart Sublime Text,
then select text and run “Codex: Prompt” from the Command Palette.
"""

import json
import os
import subprocess
import threading
import uuid

import sublime
import sublime_plugin

CODEX_BIN = os.getenv('CODEX_BIN', '/opt/homebrew/bin/codex')
MODEL = os.getenv('CODEX_MODEL', 'codex-mini-latest')


class _CodexBridge:
    def __init__(self):
        # Prepare environment for the Codex subprocess
        env = os.environ.copy()
        env['OPENAI_API_KEY'] = OPENAI_API_KEY
        if not OPENAI_API_KEY:
            sublime.error_message(
                'Missing environment variable: OPENAI_API_KEY. '
                'Create an API key (https://platform.openai.com) and export it as an environment variable.'
            )
            raise RuntimeError('Missing OPENAI_API_KEY')
        self.proc = subprocess.Popen(
            [CODEX_BIN, 'proto'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        self._lock = threading.Lock()
        self._callbacks = {}
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._configure_session()

    # --------------------------------------------------------------------- I/O
    def send(self, obj, cb):
        line = json.dumps(obj) + '\n'
        with self._lock:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
        if cb:
            self._callbacks[obj['id']] = cb

    def _read_loop(self):
        for line in self.proc.stdout:
            raw = line.rstrip()
            print('[Codex raw]', raw)
            # extract JSON payload (strip ANSI/color or log prefixes)
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
            # trigger callback for reasoning or final messages
            if call_id in self._callbacks and msg_type in (
                'assistant_message',
                'agent_reasoning',
                'agent_message',
            ):
                cb = self._callbacks[call_id]
                # after final message or agent_message, clear callback
                if msg_type in ('assistant_message', 'agent_message'):
                    del self._callbacks[call_id]
                sublime.set_timeout(lambda e=event, c=cb: c(e), 0)

    # ----------------------------------------------------------------- session
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
        key = "__global__"
    else:
        key = window.id()

    bridge = _bridges.get(key)
    if bridge is None:
        bridge = _CodexBridge()
        _bridges[key] = bridge
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
            bridge = _bridges.pop(key, None)
            if bridge is not None:
                try:
                    bridge.proc.terminate()
                except Exception:
                    pass


# ------------------------------------------------------------------- unload


def plugin_unloaded():
    """Terminate all running Codex subprocesses when the plugin is reloaded."""

    for key, bridge in list(_bridges.items()):
        try:
            bridge.proc.terminate()
        except Exception:
            pass
        _bridges.pop(key, None)
