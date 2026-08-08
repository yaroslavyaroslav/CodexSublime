import unittest

from phantom_markdown import minihtml


class MinihtmlTests(unittest.TestCase):
    def test_escapes_source_and_renders_small_markdown_subset(self) -> None:
        rendered = minihtml('**Why** `<unsafe>`\n\n*Detail*')

        self.assertIn('<strong>Why</strong>', rendered)
        self.assertIn('<code>&lt;unsafe&gt;</code>', rendered)
        self.assertIn('<em>Detail</em>', rendered)
        self.assertNotIn('<unsafe>', rendered)


if __name__ == '__main__':
    unittest.main()
