"""Codex subprocess bridge based on `codex app-server`."""

import json
import logging
import os
import shlex
import shutil
import signal
import subprocess
import threading
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import sublime

logger = logging.getLogger(__name__)


def _is_debug_logging_enabled() -> bool:
    try:
        level = sublime.load_settings('Codex.sublime-settings').get('log_level', '')
        return isinstance(level, str) and level.lower() == 'debug'
    except Exception:
        return False


def _project_settings() -> dict:
    try:
        window = sublime.active_window()
        view = window.active_view() if window else None
        if view:
            return view.settings().get('codex', {}) or {}
    except Exception:
        pass
    return {}


def _find_workspace_root(path: str | None) -> str | None:
    if not path:
        return None
    current = os.path.abspath(path)
    if not os.path.isdir(current):
        current = os.path.dirname(current)

    while True:
        if os.path.exists(os.path.join(current, '.git')):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _best_workspace_cwd(project_folders: list[str], active_file_dir: str | None) -> str:
    if active_file_dir:
        active_dir = os.path.abspath(active_file_dir)
        for folder in project_folders:
            folder_abs = os.path.abspath(folder)
            if active_dir == folder_abs or active_dir.startswith(folder_abs + os.sep):
                return folder_abs

        git_root = _find_workspace_root(active_dir)
        if git_root:
            return git_root

        return active_dir

    if project_folders:
        return os.path.abspath(project_folders[0])

    return os.path.abspath(os.getcwd())


def kill_process_tree(root_pid: int) -> None:  # pragma: no cover
    if os.name == 'nt':  # pragma: no cover
        try:
            startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
            subprocess.Popen(
                ['taskkill', '/PID', str(root_pid), '/T', '/F'],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
            ).communicate()
        except Exception as exc:
            logger.debug('taskkill failed: %s', exc)
        return

    try:
        output = subprocess.check_output(['ps', '-o', 'pid=', '-o', 'ppid=', '-A'], text=True)
    except Exception as exc:
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

    try:
        pgid = os.getpgid(root_pid)
        if pgid > 0:
            os.killpg(pgid, signal.SIGKILL)
    except Exception:
        pass

    for pid in descendants:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

    try:
        os.kill(root_pid, signal.SIGKILL)
    except Exception:
        pass


class _CodexBridge:
    """Manage a single `codex app-server` process and adapt it to plugin events."""

    def __init__(self) -> None:
        settings = sublime.load_settings('Codex.sublime-settings')
        token: str = settings.get('token', '')  # type: ignore[name-defined]
        self._window = sublime.active_window()
        self._debug_log_file: str = settings.get('debug_log_file', '/tmp/codex_sublime_bridge.log')  # type: ignore[name-defined]

        project_folders = self._window.folders() if self._window else []
        self._project_folders = [os.path.abspath(p) for p in project_folders]

        active_file_dir: str | None = None
        try:
            view = self._window.active_view() if self._window else None
            fn = view.file_name() if view else None
            if fn:
                active_file_dir = os.path.abspath(os.path.dirname(fn))
        except Exception:
            active_file_dir = None

        self._cwd = _best_workspace_cwd(self._project_folders, active_file_dir)

        env = os.environ.copy()
        if token and token != '<your-token>':
            env['OPENAI_API_KEY'] = token

        popen_kwargs: dict[str, Any] = {
            'stdin': subprocess.PIPE,
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE,
            'text': True,
            'encoding': 'utf-8',
            'errors': 'replace',
            'bufsize': 1,
            'env': env,
            'cwd': self._cwd,
        }
        if os.name != 'nt':
            popen_kwargs['start_new_session'] = True
        if os.name == 'nt':  # pragma: no cover
            startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
            popen_kwargs['startupinfo'] = startupinfo
            popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        raw_cmd = settings.get('codex_path') or 'codex'  # type: ignore[arg-type]
        if isinstance(raw_cmd, str):
            cmd = shlex.split(raw_cmd)
        elif isinstance(raw_cmd, (list, tuple)):
            cmd = list(raw_cmd)
        else:
            raise TypeError('codex_path must be str or list[str]')

        executable = cmd[0]
        if not os.path.isabs(executable):
            cmd[0] = shutil.which(executable)
            if cmd[0] is None:
                raise RuntimeError(f'which({executable}) did not yield anything')

        conf = _project_settings()
        global_settings = sublime.load_settings('Codex.sublime-settings')

        model = conf.get('model') or global_settings.get('model')
        if isinstance(model, str) and model:
            cmd.extend(['--config', f'model={model}'])

        sandbox_mode = conf.get('sandbox_mode') or global_settings.get('sandbox_mode')
        if isinstance(sandbox_mode, str) and sandbox_mode:
            cmd.extend(['--config', f'sandbox_mode={sandbox_mode}'])

        approval_policy = conf.get('approval_policy') or global_settings.get('approval_policy')
        if isinstance(approval_policy, str) and approval_policy:
            cmd.extend(['--config', f'approval_policy={approval_policy}'])

        allow_network = conf.get('sandbox_network_access') or global_settings.get('sandbox_network_access')
        if isinstance(allow_network, bool):
            cmd.extend(['--config', f'sandbox_workspace_write.network_access={str(allow_network).lower()}'])

        extra_perms = conf.get('permissions', [])
        if isinstance(extra_perms, str):
            extra_perms = [extra_perms]
        extra_perms = [os.path.abspath(p) for p in (extra_perms or []) if isinstance(p, str)]
        extra_perms = [p for p in extra_perms if os.path.abspath(p) != self._cwd]
        if extra_perms:
            roots_json = json.dumps(extra_perms, ensure_ascii=False)
            cmd.extend(['--config', f'sandbox_workspace_write.writable_roots={roots_json}'])

        cmd.append('app-server')
        logger.debug('Launching Codex app-server (cwd=%s): %s', self._cwd, cmd)
        self._trace('launch app-server cwd=%s cmd=%s', self._cwd, cmd)
        self.proc = subprocess.Popen(cmd, **popen_kwargs)

        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._waiters_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self._pending: list[tuple[dict[str, Any], Callable[[dict[str, Any]], None] | None]] = []
        self._rpc_waiters: dict[str, dict[str, Any]] = {}
        self._callbacks: dict[str, Callable[[dict[str, Any]], None]] = {}
        self._active_msg_id: str | None = None
        self._last_msg_id: str | None = None
        self._last_cb: Callable[[dict[str, Any]], None] | None = None
        self._pending_approvals: dict[str, dict[str, Any]] = {}
        self._reported_v2_turn_errors: set[str] = set()
        self._protocol = 'legacy'
        self._bootstrap_error: str | None = None
        self._recent_stderr: deque[str] = deque(maxlen=20)
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._bootstrap_failed = threading.Event()

        self._session_id = self._ensure_session_id(self._window)

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stderr_reader.start()

        self._bootstrap = threading.Thread(target=self._bootstrap_session, daemon=True)
        self._bootstrap.start()

    def terminate(self) -> None:
        self._stopped.set()
        self._discard_pending(reason='bridge_stopped', log_error=False)
        self._ready.clear()
        if self.proc.poll() is None:
            kill_process_tree(self.proc.pid)
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                kill_process_tree(self.proc.pid)
        else:
            kill_process_tree(self.proc.pid)

    def send(self, obj: dict[str, Any], cb: Callable[[dict[str, Any]], None] | None = None) -> None:
        if self._bootstrap_failed.is_set():
            if cb is not None:
                cb({'msg': self._error_payload(reason='bridge_not_ready')})
            return

        if self._stopped.is_set():
            if cb is not None:
                cb({'msg': self._error_payload(reason='bridge_stopped', default_message='Codex bridge is not running')})
            return

        if not self._ready.is_set():
            op_type = None
            try:
                op_type = obj.get('op', {}).get('type')
            except Exception:
                pass
            logger.debug('bridge not ready – queueing %s', op_type or 'message')
            with self._pending_lock:
                self._pending.append((obj, cb))
            return
        self._send_now(obj, cb)

    def _bootstrap_session(self) -> None:
        try:
            self._trace('bootstrap: initialize start')
            self._send_request_sync(
                'initialize',
                {
                    'clientInfo': {
                        'name': 'codex_sublime',
                        'title': 'Codex Sublime',
                        'version': '1.0.0',
                    },
                    'capabilities': {
                        'experimentalApi': True,
                    },
                },
                timeout=20.0,
            )
            self._trace('bootstrap: initialize ok')
            self._send_json({'method': 'initialized'})
            self._trace('bootstrap: initialized sent')

            conversation_id = self._bootstrap_modern_protocol()
            if conversation_id:
                self._protocol = 'threads'
                self._trace('bootstrap: using threads protocol conversation_id=%s', conversation_id)
            else:
                conversation_id = self._bootstrap_legacy_protocol()
                self._protocol = 'legacy'
                self._trace('bootstrap: using legacy protocol conversation_id=%s', conversation_id)

            self._session_id = conversation_id
            self._persist_session_id(self._window, conversation_id)

            self._ready.set()
            self._flush_pending()
            self._trace('bootstrap: ready set')
        except Exception as exc:
            logger.exception('Failed to bootstrap app-server bridge: %s', exc)
            self._trace('bootstrap: failed error=%s', exc)
            self._bootstrap_error = self._format_bootstrap_error(exc)
            self._bootstrap_failed.set()
            self._stopped.set()
            self._discard_pending(reason='bridge_not_ready', log_error=True)
            try:
                if self.proc.poll() is None:
                    kill_process_tree(self.proc.pid)
            except Exception:
                pass

    def _request_key(self, request_id: Any) -> str:
        return str(request_id)

    def _send_request_sync(self, method: str, params: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        waiter = threading.Event()
        slot: dict[str, Any] = {}
        with self._waiters_lock:
            self._rpc_waiters[self._request_key(request_id)] = {'event': waiter, 'slot': slot}

        self._send_json({'id': request_id, 'method': method, 'params': params})
        self._trace('rpc sync sent method=%s id=%s', method, request_id)

        if not waiter.wait(timeout=timeout):
            with self._waiters_lock:
                self._rpc_waiters.pop(self._request_key(request_id), None)
            raise TimeoutError(f'timeout waiting for {method}')

        error = slot.get('error')
        if error is not None:
            self._trace('rpc sync error method=%s id=%s error=%s', method, request_id, error)
            raise RuntimeError(f'{method} failed: {error}')
        result = slot.get('result')
        self._trace('rpc sync ok method=%s id=%s', method, request_id)
        if isinstance(result, dict):
            return result
        return {}

    def _send_request_async(
        self,
        method: str,
        params: dict[str, Any],
        *,
        on_response: Callable[[dict[str, Any]], None] | None = None,
        on_error: Callable[[Any], None] | None = None,
    ) -> None:
        request_id = str(uuid.uuid4())
        with self._waiters_lock:
            self._rpc_waiters[self._request_key(request_id)] = {
                'on_response': on_response,
                'on_error': on_error,
            }
        self._send_json({'id': request_id, 'method': method, 'params': params})
        self._trace('rpc async sent method=%s id=%s', method, request_id)

    def _send_request_nowait(self, method: str, params: dict[str, Any]) -> None:
        request_id = str(uuid.uuid4())
        self._send_json({'id': request_id, 'method': method, 'params': params})

    def _send_json(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload) + '\n'
        with self._write_lock:
            try:
                assert self.proc.stdin is not None
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            except BrokenPipeError:
                logger.error('Broken pipe while sending data – process dead?')

    def _bootstrap_modern_protocol(self) -> str | None:
        conversation_id: str | None = None
        modern_supported = True

        if self._session_id:
            try:
                self._trace('bootstrap: thread/resume start session_id=%s', self._session_id)
                resumed = self._send_request_sync(
                    'thread/resume',
                    {'threadId': self._session_id},
                    timeout=20.0,
                )
                conversation_id = self._extract_thread_id(resumed)
                self._trace('bootstrap: thread/resume ok conversation_id=%s', conversation_id)
            except Exception as exc:
                if self._is_unsupported_method_error(exc, 'thread/resume'):
                    modern_supported = False
                    self._trace('bootstrap: thread/resume unsupported')
                else:
                    logger.debug('thread/resume failed for %s; creating a new thread', self._session_id)
                    self._trace('bootstrap: thread/resume failed for session_id=%s', self._session_id)

        if modern_supported and not conversation_id:
            try:
                self._trace('bootstrap: thread/start start')
                created = self._send_request_sync('thread/start', {}, timeout=20.0)
                conversation_id = self._extract_thread_id(created)
                self._trace('bootstrap: thread/start ok conversation_id=%s', conversation_id)
            except Exception as exc:
                if self._is_unsupported_method_error(exc, 'thread/start'):
                    modern_supported = False
                    self._trace('bootstrap: thread/start unsupported')
                else:
                    raise

        if not modern_supported:
            return None
        if not isinstance(conversation_id, str) or not conversation_id:
            raise RuntimeError('threadId missing in app-server response')
        return conversation_id

    def _bootstrap_legacy_protocol(self) -> str:
        conversation_id: str | None = None
        if self._session_id:
            try:
                self._trace('bootstrap: resumeConversation start session_id=%s', self._session_id)
                resumed = self._send_request_sync(
                    'resumeConversation',
                    {'conversationId': self._session_id},
                    timeout=20.0,
                )
                conversation_id = resumed.get('conversationId')
                self._trace('bootstrap: resumeConversation ok conversation_id=%s', conversation_id)
            except Exception:
                logger.debug('resumeConversation failed for %s; creating a new conversation', self._session_id)
                self._trace('bootstrap: resumeConversation failed for session_id=%s', self._session_id)

        if not conversation_id:
            self._trace('bootstrap: newConversation start')
            created = self._send_request_sync('newConversation', {}, timeout=20.0)
            conversation_id = created.get('conversationId')
            self._trace('bootstrap: newConversation ok conversation_id=%s', conversation_id)

        if not isinstance(conversation_id, str) or not conversation_id:
            raise RuntimeError('conversationId missing in app-server response')

        self._send_request_sync(
            'addConversationListener',
            {
                'conversationId': conversation_id,
                'experimentalRawEvents': False,
            },
            timeout=20.0,
        )
        self._trace('bootstrap: addConversationListener ok conversation_id=%s', conversation_id)
        return conversation_id

    @staticmethod
    def _extract_thread_id(result: dict[str, Any]) -> str | None:
        thread = result.get('thread')
        if isinstance(thread, dict):
            thread_id = thread.get('id')
            if isinstance(thread_id, str) and thread_id:
                return thread_id
        thread_id = result.get('threadId')
        if isinstance(thread_id, str) and thread_id:
            return thread_id
        return None

    @staticmethod
    def _is_unsupported_method_error(exc: Exception, method: str) -> bool:
        text = str(exc)
        return (
            f'unknown variant `{method}`' in text
            or f'Unsupported server request: {method}' in text
            or 'Method not found' in text
        )

    def _send_rpc_response(self, request_id: Any, result: dict[str, Any]) -> None:
        self._send_json({'id': request_id, 'result': result})

    def _send_rpc_error(self, request_id: Any, message: str, code: int = -32601) -> None:
        self._send_json({'id': request_id, 'error': {'code': code, 'message': message}})

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            raw = line.rstrip()
            logger.debug(raw)
            idx = raw.find('{')
            if idx < 0:
                continue
            try:
                packet: dict[str, Any] = json.loads(raw[idx:])
            except json.JSONDecodeError as exc:
                logger.info('Parse error: %s %s', exc, raw[idx:])
                continue

            if 'id' in packet and ('result' in packet or 'error' in packet):
                key = self._request_key(packet.get('id'))
                waiter_data: dict[str, Any] | None = None
                with self._waiters_lock:
                    waiter_data = self._rpc_waiters.pop(key, None)
                if waiter_data is not None:
                    waiter = waiter_data.get('event')
                    slot = waiter_data.get('slot')
                    if isinstance(waiter, threading.Event) and isinstance(slot, dict):
                        slot['result'] = packet.get('result')
                        slot['error'] = packet.get('error')
                        waiter.set()
                    else:
                        err = packet.get('error')
                        if err is None:
                            cb = waiter_data.get('on_response')
                            if callable(cb):
                                sublime.set_timeout(lambda _cb=cb, _r=packet.get('result'): _cb(_r or {}), 0)
                        else:
                            cb = waiter_data.get('on_error')
                            if callable(cb):
                                sublime.set_timeout(lambda _cb=cb, _e=err: _cb(_e), 0)
                if packet.get('error') is not None:
                    self._trace('rpc response error id=%s error=%s', packet.get('id'), packet.get('error'))
                continue

            method = packet.get('method')
            if method and 'id' in packet:
                self._trace('server request method=%s id=%s', method, packet.get('id'))
                self._handle_server_request(packet)
                continue
            if method:
                self._trace('notification method=%s', method)
                self._handle_notification(packet)
                continue

        if not self._ready.is_set():
            self._discard_pending()
            self._trace('read loop ended before ready')

    def _stderr_loop(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            raw = line.rstrip()
            if not raw:
                continue
            self._recent_stderr.append(raw)
            logger.error('[codex stderr] %s', raw)
            self._trace('codex stderr: %s', raw)

    def _handle_notification(self, packet: dict[str, Any]) -> None:
        method = packet.get('method')
        params = packet.get('params')
        if not isinstance(method, str):
            return
        if not isinstance(params, dict):
            return

        if not method.startswith('codex/event/'):
            self._handle_v2_notification(method, params)
            return

        event = dict(params)
        event.pop('conversationId', None)
        event.pop('conversation_id', None)
        msg = event.get('msg', {})
        msg_type = msg.get('type') if isinstance(msg, dict) else None

        # App-server can duplicate approval prompts in notifications and
        # dedicated server requests. We must only process the request path,
        # because it carries the request id required for a valid response.
        if msg_type in ('exec_approval_request', 'apply_patch_approval_request'):
            self._trace('event suppressed duplicate approval msg_type=%s', msg_type)
            return

        if self._should_suppress(msg_type):
            self._trace('event suppressed msg_type=%s', msg_type)
            return
        self._trace('event dispatch msg_type=%s id=%s', msg_type, event.get('id'))
        self._dispatch_event(event)

    def _handle_v2_notification(self, method: str, params: dict[str, Any]) -> None:
        event: dict[str, Any] | None = None

        if method == 'item/agentMessage/delta':
            item_id = str(params.get('itemId') or '')
            delta = params.get('delta')
            if item_id and isinstance(delta, str) and delta:
                event = {
                    'id': item_id,
                    'msg': {
                        'type': 'agent_message_content_delta',
                        'delta': delta,
                        'itemId': item_id,
                    },
                }

        elif method == 'item/started':
            event = self._adapt_v2_item_event(params.get('item'), completed=False)

        elif method == 'item/completed':
            event = self._adapt_v2_item_event(params.get('item'), completed=True)

        elif method == 'error':
            turn_id = str(params.get('turnId') or '')
            if turn_id:
                self._reported_v2_turn_errors.add(turn_id)
            event = self._adapt_v2_error(params.get('error'), turn_id)

        elif method == 'turn/completed':
            turn = params.get('turn')
            if not isinstance(turn, dict):
                return

            turn_id = str(turn.get('id') or '')
            error = turn.get('error')
            if isinstance(error, dict) and turn_id not in self._reported_v2_turn_errors:
                error_event = self._adapt_v2_error(error, turn_id)
                if error_event is not None:
                    self._dispatch_event(error_event)

            self._reported_v2_turn_errors.discard(turn_id)
            task_complete = {
                'id': turn_id,
                'msg': {
                    'type': 'task_complete',
                    'last_agent_message': self._last_agent_message(turn),
                },
            }
            if self._should_suppress('task_complete'):
                self._complete_active_turn()
            else:
                self._dispatch_event(task_complete)
            return

        if event is None:
            return

        msg = event.get('msg', {})
        msg_type = msg.get('type') if isinstance(msg, dict) else None
        if self._should_suppress(msg_type):
            self._trace('v2 event suppressed method=%s msg_type=%s', method, msg_type)
            return
        self._trace('v2 event dispatch method=%s msg_type=%s id=%s', method, msg_type, event.get('id'))
        self._dispatch_event(event)

    def _adapt_v2_item_event(self, raw_item: Any, *, completed: bool) -> dict[str, Any] | None:
        if not isinstance(raw_item, dict):
            return None

        item = raw_item
        item_id = str(item.get('id') or '')
        item_type = item.get('type')

        if item_type == 'agentMessage':
            if not completed:
                return None
            return {
                'id': item_id,
                'msg': {
                    'type': 'agent_message',
                    'text': str(item.get('text') or ''),
                    'itemId': item_id,
                },
            }

        if item_type == 'commandExecution':
            if completed:
                return {
                    'id': item_id,
                    'msg': {
                        'type': 'exec_command_end',
                        'exit_code': item.get('exitCode'),
                        'stdout': str(item.get('aggregatedOutput') or ''),
                        'stderr': '',
                    },
                }
            return {
                'id': item_id,
                'msg': {
                    'type': 'exec_command_begin',
                    'command': [str(item.get('command') or '')],
                    'cwd': item.get('cwd'),
                },
            }

        if item_type == 'fileChange':
            changes = item.get('changes') or []
            if completed:
                return {
                    'id': item_id,
                    'msg': {
                        'type': 'patch_apply_end',
                        'success': item.get('status') == 'completed',
                        'changes': changes,
                    },
                }
            return {
                'id': item_id,
                'msg': {
                    'type': 'patch_apply_begin',
                    'auto_approved': False,
                    'changes': changes,
                },
            }

        if item_type == 'mcpToolCall':
            if completed:
                result = {'Err': item.get('error')} if item.get('error') else {'Ok': item.get('result') or {}}
                return {
                    'id': item_id,
                    'msg': {
                        'type': 'mcp_tool_call_end',
                        'result': result,
                    },
                }
            return {
                'id': item_id,
                'msg': {
                    'type': 'mcp_tool_call_begin',
                    'call_id': item_id,
                    'server': item.get('server'),
                    'tool': item.get('tool'),
                    'arguments': item.get('arguments') or {},
                },
            }

        return None

    def _adapt_v2_error(self, raw_error: Any, turn_id: str) -> dict[str, Any] | None:
        if not isinstance(raw_error, dict):
            return None

        message = raw_error.get('message')
        if isinstance(message, str):
            try:
                nested = json.loads(message)
                nested_error = nested.get('error') if isinstance(nested, dict) else None
                if isinstance(nested_error, dict) and nested_error.get('message'):
                    message = nested_error['message']
            except (TypeError, ValueError):
                pass

        return {
            'id': turn_id,
            'msg': {
                'type': 'error',
                'reason': raw_error.get('codexErrorInfo'),
                'message': message or 'Codex turn failed',
            },
        }

    @staticmethod
    def _last_agent_message(turn: dict[str, Any]) -> str | None:
        items = turn.get('items')
        if not isinstance(items, list):
            return None
        for item in reversed(items):
            if isinstance(item, dict) and item.get('type') == 'agentMessage':
                text = item.get('text')
                if isinstance(text, str):
                    return text
        return None

    def _handle_server_request(self, packet: dict[str, Any]) -> None:
        method = packet.get('method')
        request_id = packet.get('id')
        params = packet.get('params', {}) or {}
        if not isinstance(method, str) or request_id is None or not isinstance(params, dict):
            return

        if method == 'execCommandApproval':
            event_id = str(params.get('callId') or uuid.uuid4())
            with self._state_lock:
                self._pending_approvals[event_id] = {'request_id': request_id, 'kind': 'legacy_exec'}
            self._dispatch_event(
                {
                    'id': event_id,
                    'msg': {
                        'type': 'exec_approval_request',
                        'command': params.get('command', []),
                        'cwd': params.get('cwd'),
                        'reason': params.get('reason'),
                    },
                }
            )
            self._trace('approval request legacy exec event_id=%s', event_id)
            return

        if method == 'applyPatchApproval':
            event_id = str(params.get('callId') or uuid.uuid4())
            with self._state_lock:
                self._pending_approvals[event_id] = {'request_id': request_id, 'kind': 'legacy_patch'}
            self._dispatch_event(
                {
                    'id': event_id,
                    'msg': {
                        'type': 'apply_patch_approval_request',
                        'changes': params.get('fileChanges', {}),
                        'reason': params.get('reason'),
                    },
                }
            )
            self._trace('approval request legacy patch event_id=%s', event_id)
            return

        if method == 'item/commandExecution/requestApproval':
            item_id = str(params.get('itemId') or '')
            approval_id = params.get('approvalId')
            if approval_id is not None:
                event_id = f'{item_id}:{approval_id}'
            else:
                event_id = item_id or str(uuid.uuid4())
            with self._state_lock:
                self._pending_approvals[event_id] = {'request_id': request_id, 'kind': 'v2_exec'}
            command = params.get('command') or []
            if isinstance(command, str):
                command = [command]
            self._dispatch_event(
                {
                    'id': event_id,
                    'msg': {
                        'type': 'exec_approval_request',
                        'command': command,
                        'cwd': params.get('cwd'),
                        'reason': params.get('reason'),
                    },
                }
            )
            self._trace('approval request v2 exec event_id=%s', event_id)
            return

        if method == 'item/fileChange/requestApproval':
            event_id = str(params.get('itemId') or uuid.uuid4())
            with self._state_lock:
                self._pending_approvals[event_id] = {'request_id': request_id, 'kind': 'v2_patch'}
            self._dispatch_event(
                {
                    'id': event_id,
                    'msg': {
                        'type': 'apply_patch_approval_request',
                        'changes': params.get('changes', {}),
                        'reason': params.get('reason'),
                    },
                }
            )
            self._trace('approval request v2 patch event_id=%s', event_id)
            return

        if method == 'item/tool/requestUserInput':
            event_id = str(params.get('itemId') or uuid.uuid4())
            self._dispatch_event(
                {
                    'id': event_id,
                    'msg': {
                        'type': 'request_user_input',
                        'questions': params.get('questions', []),
                        'message': 'Tool requested user input; Sublime client returned empty answers.',
                    },
                }
            )
            self._send_rpc_response(request_id, {'answers': {}})
            self._trace('tool requestUserInput auto-answered event_id=%s', event_id)
            return

        if method == 'item/tool/call':
            event_id = str(params.get('callId') or uuid.uuid4())
            self._dispatch_event(
                {
                    'id': event_id,
                    'msg': {
                        'type': 'dynamic_tool_call_request',
                        'tool': params.get('tool'),
                        'arguments': params.get('arguments'),
                        'message': 'Dynamic tool calls are not implemented in Sublime client.',
                    },
                }
            )
            self._send_rpc_response(
                request_id,
                {
                    'contentItems': [
                        {
                            'type': 'inputText',
                            'text': 'Dynamic tool calls are not implemented in this Sublime client.',
                        }
                    ],
                    'success': False,
                },
            )
            self._trace('tool call auto-declined event_id=%s', event_id)
            return

        self._send_rpc_error(request_id, f'Unsupported server request: {method}')
        self._trace('unsupported server request method=%s', method)

    def _send_now(self, obj: dict[str, Any], cb: Callable[[dict[str, Any]], None] | None) -> None:
        op = obj.get('op') if isinstance(obj, dict) else None
        if not isinstance(op, dict):
            return
        op_type = op.get('type')

        if op_type == 'user_input':
            msg_id = str(obj.get('id') or uuid.uuid4())
            if cb is not None:
                # Prevent stale callback accumulation when previous turn
                # never emitted an explicit completion event to the UI.
                if self._active_msg_id:
                    self._callbacks.pop(self._active_msg_id, None)
                self._callbacks[msg_id] = cb
                self._active_msg_id = msg_id
                self._last_msg_id = msg_id
                self._last_cb = cb

            if self._protocol == 'threads':
                items = self._normalize_turn_input_items(op.get('items'))
                self._send_request_async(
                    'turn/start',
                    {
                        'threadId': self._session_id,
                        'input': items,
                    },
                    on_error=lambda _err, _msg_id=msg_id: self._handle_user_input_request_error(_msg_id, _err),
                )
                self._trace('user_input queued to turn/start msg_id=%s', msg_id)
            else:
                items = self._normalize_input_items(op.get('items'))
                self._send_request_async(
                    'sendUserMessage',
                    {
                        'conversationId': self._session_id,
                        'items': items,
                    },
                    on_error=lambda _err, _msg_id=msg_id: self._handle_user_input_request_error(_msg_id, _err),
                )
                self._trace('user_input queued to sendUserMessage msg_id=%s', msg_id)
            return

        if op_type in {'exec_approval', 'patch_approval'}:
            approval_event_id = str(op.get('id', ''))
            with self._state_lock:
                approval = self._pending_approvals.pop(approval_event_id, None)
            if approval is None:
                logger.warning('No pending approval for event id %s', approval_event_id)
                self._trace('approval response missing pending event_id=%s', approval_event_id)
                return

            decision = op.get('decision', 'denied')
            kind = approval.get('kind')
            request_id = approval.get('request_id')
            if request_id is None:
                return

            if kind in {'legacy_exec', 'legacy_patch'}:
                self._send_rpc_response(request_id, {'decision': decision})
                self._trace('approval response sent legacy kind=%s event_id=%s decision=%s', kind, approval_event_id, decision)
                return

            mapped = {
                'approved': 'accept',
                'approved_for_session': 'acceptForSession',
                'denied': 'decline',
                'abort': 'cancel',
            }.get(decision, 'decline')
            self._send_rpc_response(request_id, {'decision': mapped})
            self._trace('approval response sent v2 kind=%s event_id=%s decision=%s mapped=%s', kind, approval_event_id, decision, mapped)
            return

    def _handle_user_input_request_error(self, msg_id: str, error: Any) -> None:
        cb = self._callbacks.get(msg_id)
        if cb is None:
            return
        self._callbacks.pop(msg_id, None)
        if self._active_msg_id == msg_id:
            self._active_msg_id = None
        if self._last_msg_id == msg_id:
            self._last_msg_id = None
            self._last_cb = None
        cb(
            {
                'id': msg_id,
                'msg': {
                    'type': 'error',
                    'reason': 'send_user_message_failed',
                    'message': f'sendUserMessage failed: {error}',
                },
            }
        )
        self._trace('sendUserMessage failed msg_id=%s error=%s', msg_id, error)

    def _normalize_input_items(self, raw_items: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        items = raw_items if isinstance(raw_items, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get('type')
            if t == 'text':
                if isinstance(item.get('data'), dict):
                    out.append(item)
                    continue
                text = item.get('text', '')
                out.append({'type': 'text', 'data': {'text': str(text), 'text_elements': []}})
            elif t == 'image':
                if isinstance(item.get('data'), dict):
                    out.append(item)
                    continue
                image_url = item.get('image_url')
                if image_url:
                    out.append({'type': 'image', 'data': {'image_url': str(image_url)}})
            elif t == 'localImage':
                if isinstance(item.get('data'), dict):
                    out.append(item)
                    continue
                path = item.get('path')
                if path:
                    out.append({'type': 'localImage', 'data': {'path': str(path)}})
        return out

    def _normalize_turn_input_items(self, raw_items: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        items = raw_items if isinstance(raw_items, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get('type')
            if item_type == 'text':
                text = item.get('text', '')
                out.append({'type': 'text', 'text': str(text)})
            elif item_type == 'image':
                image_url = item.get('image_url')
                if image_url:
                    out.append({'type': 'image', 'imageUrl': str(image_url)})
            elif item_type in {'localImage', 'local_image'}:
                path = item.get('path')
                if path:
                    out.append({'type': 'localImage', 'path': str(path)})
        return out

    def _should_suppress(self, msg_type: Any) -> bool:
        project_conf = _project_settings()
        if 'suppress_events' in project_conf:
            suppress = project_conf.get('suppress_events', [])
        else:
            suppress = sublime.load_settings('Codex.sublime-settings').get('suppress_events', [])
        if isinstance(suppress, str):
            suppress = [suppress]
        if not isinstance(suppress, list):
            return False
        return msg_type in suppress

    def _dispatch_event(self, event: dict[str, Any]) -> None:
        msg = event.get('msg', {}) if isinstance(event, dict) else {}
        msg_type = msg.get('type') if isinstance(msg, dict) else None

        dispatch_cb: Callable[[dict[str, Any]], None] | None = None
        active_id = self._active_msg_id
        if active_id and active_id in self._callbacks:
            dispatch_cb = self._callbacks[active_id]
        elif self._last_cb is not None:
            dispatch_cb = self._last_cb

        if dispatch_cb is not None:
            if msg_type in ('assistant_message', 'task_complete', 'turn_aborted'):
                self._complete_active_turn()
            sublime.set_timeout(lambda _e=event, _c=dispatch_cb: _c(_e), 0)
        else:
            self._trace('event dropped no callback msg_type=%s id=%s', msg_type, event.get('id'))

    def _complete_active_turn(self) -> None:
        active_id = self._active_msg_id
        if active_id:
            self._callbacks.pop(active_id, None)
        self._active_msg_id = None
        self._last_msg_id = None
        self._last_cb = None

    def _flush_pending(self) -> None:
        with self._pending_lock:
            pending = self._pending
            self._pending = []
        if not pending:
            return
        logger.debug('bridge ready – flushing %d queued message(s)', len(pending))
        self._trace('flushing pending count=%d', len(pending))
        for queued_obj, queued_cb in pending:
            self._send_now(queued_obj, queued_cb)

    def _discard_pending(self, *, reason: str = 'bridge_not_ready', log_error: bool = True) -> None:
        with self._pending_lock:
            pending = self._pending
            self._pending = []
        if not pending:
            return
        if log_error:
            logger.error('bridge terminated before ready – dropping %d queued message(s)', len(pending))
            self._trace('discard pending count=%d reason=%s', len(pending), reason)
        for _, queued_cb in pending:
            if queued_cb is not None:
                try:
                    queued_cb({'msg': self._error_payload(reason=reason)})
                except Exception:
                    logger.debug('callback raised after bridge shutdown', exc_info=True)

    def _error_payload(self, *, reason: str, default_message: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'type': 'error',
            'reason': reason,
        }
        message = self._bootstrap_error or default_message
        if message:
            payload['message'] = message
        if self._recent_stderr:
            payload['stderr'] = '\n'.join(self._recent_stderr)
        payload['protocol'] = self._protocol
        return payload

    def _format_bootstrap_error(self, exc: Exception) -> str:
        detail = str(exc)
        if self._is_unsupported_method_error(exc, 'newConversation') or self._is_unsupported_method_error(
            exc, 'resumeConversation'
        ):
            return (
                'Codex app-server rejected the legacy conversation bootstrap methods. '
                'The plugin is speaking an older protocol than the installed codex-cli.'
            )
        return f'Failed to bootstrap Codex app-server: {detail}'

    @staticmethod
    def _ensure_session_id(window: sublime.Window | None) -> str:  # type: ignore[name-defined]
        if window is None:
            return str(uuid.uuid4())
        data = window.project_data()
        if data is None:
            return str(uuid.uuid4())
        settings_block = data.get('settings') or {}
        codex_cfg = settings_block.get('codex') or {}
        session_id: str | None = codex_cfg.get('session_id')
        if not session_id:
            session_id = str(uuid.uuid4())
            codex_cfg['session_id'] = session_id
            settings_block['codex'] = codex_cfg
            data['settings'] = settings_block
            window.set_project_data(data)
        return session_id

    @staticmethod
    def _persist_session_id(window: sublime.Window | None, session_id: str) -> None:  # type: ignore[name-defined]
        if window is None:
            return
        data = window.project_data()
        if data is None:
            return
        settings_block = data.get('settings') or {}
        codex_cfg = settings_block.get('codex') or {}
        codex_cfg['session_id'] = session_id
        settings_block['codex'] = codex_cfg
        data['settings'] = settings_block
        window.set_project_data(data)

    def _trace(self, message: str, *args: Any) -> None:
        if not _is_debug_logging_enabled():
            return
        try:
            text = message % args if args else message
        except Exception:
            text = f'{message} {args!r}'
        line = f'[{datetime.now(UTC).isoformat()}] {text}\n'
        try:
            with open(self._debug_log_file, 'a', encoding='utf-8') as fh:
                fh.write(line)
        except Exception:
            pass
