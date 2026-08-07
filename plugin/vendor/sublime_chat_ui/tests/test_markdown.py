import unittest

from markdown import fenced_code, selection_markdown


class MarkdownTests(unittest.TestCase):
    def test_fenced_code_normalizes_trailing_newlines(self):
        self.assertEqual("```py\nprint(1)\n```\n\n", fenced_code("print(1)\n", "py"))

    def test_selection_markdown_can_include_a_path(self):
        self.assertEqual(
            "**src/main.py**\n\n```python\npass\n```\n\n",
            selection_markdown("pass", "src/main.py", "python"),
        )


if __name__ == "__main__":
    unittest.main()

