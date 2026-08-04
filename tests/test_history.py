import unittest

from history import PromptHistorySession


class PromptHistorySessionTests(unittest.TestCase):
    def test_previous_and_next_restore_draft(self):
        session = PromptHistorySession()
        history = ["first", "second"]

        self.assertEqual("second", session.previous(history, "draft"))
        self.assertEqual("first", session.previous(history, "ignored"))
        self.assertEqual("second", session.next(history))
        self.assertEqual("draft", session.next(history))
        self.assertFalse(session.browsing)

    def test_empty_history_is_a_noop(self):
        session = PromptHistorySession()
        self.assertIsNone(session.previous([], "draft"))
        self.assertIsNone(session.next([]))


if __name__ == "__main__":
    unittest.main()

