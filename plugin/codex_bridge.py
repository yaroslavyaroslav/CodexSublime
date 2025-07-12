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

        # Decide working directory: first project folder or current.
        window = sublime.active_window()
        project_folders = window.folders() if window else []

        # Normalize all project folders to absolute paths so they can be used
        # safely within the sandbox *permissions* list later on.
        self._project_folders = [os.path.abspath(p) for p in project_folders]

        # The *cwd* for the Codex subprocess is still the first project folder
        # (if any) to preserve existing behaviour, otherwise it falls back to
        # the current working directory.
        self._cwd = os.path.abspath(self._project_folders[0] if self._project_folders else os.getcwd())

        logger.debug('Launching Codex subprocess (cwd=%s)', self._cwd)

        env = os.environ.copy()
        env['OPENAI_API_KEY'] = OPENAI_API_KEY

        popen_kwargs: dict[str, Any] = {
            'stdin': subprocess.PIPE,
            'stdout': subprocess.PIPE,
            'stderr': subprocess.STDOUT,
            'text': True,
            'bufsize': 1,
            'env': env,
            'cwd': self._cwd,
            'start_new_session': True,  # separate process-group leader
        }

        codex_bin: str = settings.get('codex_path', '/opt/homebrew/bin/codex')  # type: ignore[arg-type]
        self.proc = subprocess.Popen(
            [codex_bin, 'proto'],
            **popen_kwargs,
        )

        self._lock = threading.Lock()
        self._callbacks: dict[str, callable[[dict[str, Any]], None]] = {}

        # Determine and (if possible) persist a *session_id* so that we can
        # resume the same chat after restarting Sublime Text (provided the
        # user works inside a saved project).
        self._session_id = self._ensure_session_id(window)

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

    # ------------------------------------------------ session persistence --

    @staticmethod
    def _ensure_session_id(window: Optional['sublime.Window']) -> str:  # type: ignore[name-defined]
        """Return a stable *session_id* for *window* and persist it in the
        current project if possible.

        If *window* is associated with a saved Sublime project we store the
        identifier under ``project_data()['codex']['session_id']`` so that it
        can be reused after restarting Sublime Text.  For ad-hoc / folder
        windows we simply generate a fresh UUID on every launch (because there
        is no place to persist it without creating an unsaved project file).
        """

        # No window or no project → return a fresh ID each time.
        if window is None:
            return str(uuid.uuid4())

        data = window.project_data()
        if data is None:
            return str(uuid.uuid4())

        settings_block = data.get('settings') or {}
        codex_cfg = settings_block.get('codex') or {}

        session_id: Optional[str] = codex_cfg.get('session_id')

        if not session_id:
            session_id = str(uuid.uuid4())
            codex_cfg['session_id'] = session_id
            settings_block['codex'] = codex_cfg
            data['settings'] = settings_block
            window.set_project_data(data)

        return session_id

    # -------------------------------------------------------- configuration --

    def _configure_session(self) -> None:
        cfg_id = self._session_id  # stable across restarts in projects
        cwd = self._cwd

        conf = _project_settings()

        extra_perms = conf.get('permissions', [])
        if isinstance(extra_perms, str):
            extra_perms = [extra_perms]

        # Combine default sandbox roots with all project folders and any
        # additional permissions specified by the user.  We purposefully do
        # not attempt to de-duplicate entries – the underlying sandbox logic
        # typically handles that, and the cost of a few duplicates is
        # negligible.
        permissions = ['/private/tmp', cwd] + self._project_folders + extra_perms

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
