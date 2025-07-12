"""Codex subprocess bridge.

This module is responsible for starting the Codex CLI process, sending JSON
messages and routing replies back to interested listeners.

It is a *refactored* extraction of the original implementation that previously
resided in ``codex_sublime.py``.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import uuid
from typing import Any, Dict, Optional

import sublime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Project-level settings helpers
# ---------------------------------------------------------------------------


def _project_settings() -> dict:  # noqa: D401 – helper
    """Return the ``view.settings()['codex']`` mapping if available."""

    try:
        window = sublime.active_window()
        view = window.active_view() if window else None
        if view:
            return view.settings().get('codex', {}) or {}
    except Exception:  # noqa: BLE001 – may run in unit-tests
        pass
    return {}


# ---------------------------------------------------------------------------
# Public helpers -------------------------------------------------------------


def kill_process_tree(root_pid: int) -> None:  # pragma: no cover — platform-specific
    """Best-effort recursive *SIGKILL* ``root_pid`` and all descendants (POSIX).

    On macOS / Linux we rely on ``ps`` because ``psutil`` might not be
    available inside Sublime's bundled Python.
    """

    try:
        output = subprocess.check_output(['ps', '-o', 'pid=', '-o', 'ppid=', '-A'], text=True)
    except Exception as exc:  # noqa: BLE001 — broad but intentional; we merely log.
        logger.error('ps enumeration failed: %s', exc)
        return

    children_map: dict[int, list[int]] = {}
    for line in output.strip().splitlines():
        try:
            pid_str, ppid_str = line.strip().split(None, 1)
            pid = int(pid_str)
            ppid = int(ppid_str)
            children_map.setdefault(ppid, []).append(pid)
        except ValueError:
            continue

    to_visit: list[int] = [root_pid]
    descendants: list[int] = []
    while to_visit:
        current = to_visit.pop()
        for child in children_map.get(current, []):
            descendants.append(child)
            to_visit.append(child)

    # Attempt to kill the entire process-group first – many children may share
    # the same PGID.  We do this *after* enumerating so we can still discover
    # descendants before their PPIDs change to 1 (init) once the root exits.

    try:
        pgid = os.getpgid(root_pid)
        if pgid > 0:
            os.killpg(pgid, signal.SIGKILL)
    except Exception:
        # Ignore – best effort only.
        pass

    # Children first, then root.
    for pid in descendants:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass

    try:
        os.kill(root_pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Bridge implementation ------------------------------------------------------


class _CodexBridge:
    """Manage a *single* Codex subprocess and JSON wire-protocol session."""

    # Future: turn into a context-manager and expose a public API – see PLAN.md

    def __init__(self) -> None:
        settings = sublime.load_settings('Codex.sublime-settings')
        OPENAI_API_KEY: str = settings.get('token', '')  # type: ignore[name-defined]
        if not OPENAI_API_KEY:
            raise RuntimeError(
                'Missing OPENAI_API_KEY – make sure to create an API key '
                'and expose it either in the environment or the plugin settings.'
            )

        logger.debug('launching subprocess')

        env = os.environ.copy()
        env['OPENAI_API_KEY'] = OPENAI_API_KEY

        popen_kwargs: dict[str, Any] = {
            'stdin': subprocess.PIPE,
            'stdout': subprocess.PIPE,
            'stderr': subprocess.STDOUT,
            'text': True,
            'bufsize': 1,
            'env': env,
            'start_new_session': True,  # separate process-group leader
        }

        codex_bin: str = settings.get('codex_path', '/opt/homebrew/bin/codex')  # type: ignore[arg-type]
        self.proc = subprocess.Popen(
            [codex_bin, 'proto'],
            **popen_kwargs,
        )

        self._lock = threading.Lock()
        self._callbacks: dict[str, callable[[dict[str, Any]], None]] = {}

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        # Do initial session configuration.
        self._configure_session()

    # --------------------------------------------------------------------- API

    def terminate(self) -> None:
        """Attempt to stop the Codex subprocess and all its descendants."""

        if self.proc.poll() is None:
            # Kill descendants first, then the root process.
            kill_process_tree(self.proc.pid)

            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                kill_process_tree(self.proc.pid)
        else:
            # Process exited – still try to reap stragglers.
            kill_process_tree(self.proc.pid)

    # ------------------------------------------------------------------ I/O --

    def send(self, obj: Dict[str, Any], cb: Optional[callable[[dict[str, Any]], None]] = None) -> None:
        """Send a JSON *obj* to Codex and optionally register *cb* for replies."""

        line = json.dumps(obj) + '\n'
        with self._lock:
            # The process may have crashed; guard against a broken pipe.
            try:
                assert self.proc.stdin is not None  # for mypy
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            except BrokenPipeError:
                logger.error('Broken pipe while sending data – process dead?')
                return

        if cb:
            self._callbacks[obj['id']] = cb

    # -------------------------------------------------------------- internal --

    def _read_loop(self) -> None:
        """Reader thread – dispatch lines coming from the Codex process."""

        assert self.proc.stdout is not None  # for type-checking

        for line in self.proc.stdout:
            raw = line.rstrip()
            logger.debug(raw)

            idx = raw.find('{')
            if idx < 0:
                continue

            try:
                event: dict[str, Any] = json.loads(raw[idx:])
            except json.JSONDecodeError as exc:
                logger.error('Parse error: %s %s', exc, raw[idx:])
                continue

            call_id = event.get('id')
            msg = event.get('msg', {})
            msg_type = msg.get('type')

            if call_id in self._callbacks:
                cb = self._callbacks[call_id]
                if msg_type in ('assistant_message', 'agent_message'):
                    del self._callbacks[call_id]

                sublime.set_timeout(lambda _e=event, _c=cb: _c(_e), 0)

    # -------------------------------------------------------- configuration --

    def _configure_session(self) -> None:
        cfg_id = str(uuid.uuid4())
        window = sublime.active_window()
        folders = window.folders() if window else []
        cwd = folders[0] if folders else os.getcwd()
        cwd = os.path.abspath(cwd)
        logger.debug('Codex cwd: %s', cwd)

        conf = _project_settings()

        extra_perms = conf.get('permissions', [])
        if isinstance(extra_perms, str):
            extra_perms = [extra_perms]

        permissions = ['/private/tmp', '/opt/homebrew', cwd] + extra_perms

        self.send(
            {
                'id': cfg_id,
                'op': {
                    'type': 'configure_session',
                    'model': conf.get('model', 'o3'),
                    'approval_policy': conf.get('approval_policy', 'on-failure'),
                    'provider': {
                        'name': conf.get('provider_name', 'openai'),
                        'base_url': conf.get('base_url', 'https://api.openai.com/v1'),
                        'wire_api': conf.get('wire_api', 'responses'),
                        'env_key': conf.get('env_key', 'OPENAI_API_KEY'),
                    },
                    'sandbox_policy': {
                        'permissions': permissions,
                        'mode': conf.get('sandbox_mode', 'read-only'),
                    },
                    'cwd': cwd,
                },
            },
            cb=None,
        )
