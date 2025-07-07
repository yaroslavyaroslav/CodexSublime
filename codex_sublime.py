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

CODEX_BIN = os.getenv("CODEX_BIN", "/opt/homebrew/bin/codex")
MODEL = os.getenv("CODEX_MODEL", "codex-mini-latest")


class _CodexBridge:
    def __init__(self):
        self.proc = subprocess.Popen(
            [CODEX_BIN, "proto"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._lock = threading.Lock()
        self._callbacks = {}
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._configure_session()

    # --------------------------------------------------------------------- I/O
    def send(self, obj, cb):
        line = json.dumps(obj) + "\n"
        with self._lock:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
        if cb:
            self._callbacks[obj["id"]] = cb

    def _read_loop(self):
        for line in self.proc.stdout:
            print("[Codex raw]", line.rstrip())
            try:
                event = json.loads(line)
            except Exception as e:
                print("[Codex JSON parse error]", e, line.rstrip())
                continue
            parent = event.get("parent")
            if parent in self._callbacks:
                cb = self._callbacks[parent]
                sublime.set_timeout(lambda e=event, c=cb: c(e), 0)

    # ----------------------------------------------------------------- session
    def _configure_session(self):
        cfg_id = str(uuid.uuid4())
        self.send(
            {
                "id": cfg_id,
                "op": {
                    "type": "configure_session",
                    "model": MODEL,
                    "approval_policy": "unless-allow-listed",
                    "sandbox_policy": {"mode": "read-only"},
                    "cwd": ".",
                },
            },
            cb=None,
        )


_bridge = None


def _get_bridge():
    global _bridge
    if _bridge is None:
        _bridge = _CodexBridge()
    return _bridge


# ===================================================================== command
class CodexPromptCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        prompt = self._collect_prompt()
        if not prompt:
            sublime.status_message("Select some text first")
            return

        bridge = _get_bridge()
        msg_id = str(uuid.uuid4())
        bridge.send(
            {
                "id": msg_id,
                "op": {
                    "type": "user_input",
                    "items": [{"type": "text", "text": prompt}],
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
        if event.get("event") != "assistant_message":
            return
        text_items = [i["text"] for i in event.get("items", []) if i["type"] == "text"]
        if not text_items:
            return

        window = self.view.window()
        panel = window.find_output_panel("codex") or window.create_output_panel("codex")
        panel.run_command("append", {"characters": f">>> {prompt}\n{''.join(text_items)}\n\n"})
        window.run_command("show_panel", {"panel": "output.codex"})
