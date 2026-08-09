from __future__ import annotations

import sys
import threading
import types
import unittest
from unittest.mock import patch


if 'sublime' not in sys.modules:
    sublime = types.ModuleType('sublime')
    sublime.Window = object
    sublime.Phantom = object
    sublime.PhantomSet = object
    sys.modules['sublime'] = sublime

from plugin.codex_bridge import _CodexBridge


class FakeRuntime:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, tool, arguments, done) -> None:
        self.calls.append((tool, arguments))
        done(types.SimpleNamespace(success=True, text='opened'))


class CodexBridgeAgentToolTests(unittest.TestCase):
    @staticmethod
    def active_bridge(protocol='threads'):
        bridge = object.__new__(_CodexBridge)
        bridge._protocol = protocol
        bridge._session_id = 'thread-1'
        bridge._state_lock = threading.Lock()
        bridge._active_msg_id = 'message-1'
        bridge._active_turn_id = None
        bridge._interrupt_requested = False
        return bridge

    def test_registers_canonical_skill_root_with_app_server(self) -> None:
        bridge = object.__new__(_CodexBridge)
        bridge._trace = lambda *_args: None
        requests = []
        bridge._send_request_sync = lambda method, params, timeout: requests.append(
            (method, params, timeout)
        ) or {}

        with patch('plugin.codex_bridge.explain_diff_skill_root', return_value='/skills'):
            bridge._register_agent_skill_roots()

        self.assertEqual(requests, [(
            'skills/extraRoots/set', {'extraRoots': ['/skills']}, 20.0,
        )])

    def test_starts_new_thread_with_dynamic_tool_namespace(self) -> None:
        bridge = object.__new__(_CodexBridge)
        bridge._session_id = None
        bridge._cwd = '/workspace'
        bridge._trace = lambda *_args: None
        requests = []

        def send_request(method, params, timeout):
            requests.append((method, params, timeout))
            return {'thread': {'id': 'thread-1'}}

        bridge._send_request_sync = send_request

        self.assertEqual(bridge._bootstrap_modern_protocol(), 'thread-1')
        method, params, timeout = requests[0]
        self.assertEqual(method, 'thread/start')
        self.assertEqual(timeout, 20.0)
        self.assertEqual(params['cwd'], '/workspace')
        self.assertEqual(params['dynamicTools'][0]['name'], 'sublime')
        self.assertEqual(
            [tool['name'] for tool in params['dynamicTools'][0]['tools']],
            ['open_diff', 'set_annotations', 'clear_annotations', 'close_views'],
        )

    def test_normalizes_skill_turn_input(self) -> None:
        bridge = object.__new__(_CodexBridge)

        normalized = bridge._normalize_turn_input_items([
            {'type': 'text', 'text': 'Explain'},
            {'type': 'skill', 'name': 'sublime-explain-diff', 'path': '/tmp/SKILL.md'},
        ])

        self.assertEqual(normalized[1], {
            'type': 'skill', 'name': 'sublime-explain-diff', 'path': '/tmp/SKILL.md',
        })

    def test_dispatches_namespaced_dynamic_tool_and_returns_result(self) -> None:
        bridge = object.__new__(_CodexBridge)
        bridge._sublime_tools = FakeRuntime()
        bridge._state_lock = __import__('threading').Lock()
        bridge._pending_approvals = {}
        bridge._trace = lambda *_args: None
        bridge._dispatch_event = lambda _event: None
        responses = []
        bridge._send_rpc_response = lambda request_id, result: responses.append((request_id, result))

        bridge._handle_server_request({
            'id': 9,
            'method': 'item/tool/call',
            'params': {
                'namespace': 'sublime', 'tool': 'open_diff',
                'arguments': {'base_ref': 'main'}, 'callId': 'call-1',
            },
        })

        self.assertEqual(bridge._sublime_tools.calls, [('open_diff', {'base_ref': 'main'})])
        self.assertEqual(responses, [(9, {
            'contentItems': [{'type': 'inputText', 'text': 'opened'}], 'success': True,
        })])

    def test_interrupts_active_turn_for_this_bridge(self) -> None:
        bridge = self.active_bridge()
        requests = []
        bridge._send_request_async = lambda method, params, **callbacks: requests.append(
            (method, params)
        )
        bridge._record_active_turn({'id': 'turn-1'})

        self.assertTrue(bridge.interrupt_active_turn())
        self.assertEqual(requests, [(
            'turn/interrupt', {'threadId': 'thread-1', 'turnId': 'turn-1'},
        )])

    def test_defers_interrupt_until_turn_start_returns_id(self) -> None:
        bridge = self.active_bridge()
        requests = []
        bridge._send_request_async = lambda method, params, **callbacks: requests.append(
            (method, params)
        )

        self.assertTrue(bridge.interrupt_active_turn())
        self.assertEqual(requests, [])

        bridge._record_active_turn({'id': 'turn-1'})
        self.assertEqual(requests, [(
            'turn/interrupt', {'threadId': 'thread-1', 'turnId': 'turn-1'},
        )])

        bridge._record_active_turn({'id': 'turn-1'})
        self.assertEqual(len(requests), 1)

    def test_interrupts_legacy_conversation(self) -> None:
        bridge = self.active_bridge(protocol='legacy')
        requests = []
        bridge._send_request_async = lambda method, params, **callbacks: requests.append(
            (method, params)
        )

        self.assertTrue(bridge.interrupt_active_turn())
        self.assertEqual(requests, [(
            'interruptConversation', {'conversationId': 'thread-1'},
        )])


if __name__ == '__main__':
    unittest.main()
