from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from links import local_file_target, markdown_link_at


class MarkdownFileLinkTests(unittest.TestCase):
    def test_finds_link_from_description_with_brackets_and_parens_in_path(self) -> None:
        text = (
            "See [sidenav](/Users/yar/worktree/webapp/app/[lang]/(app)/sidenav.tsx:33)."
        )

        link = markdown_link_at(text, text.index("sidenav"))

        self.assertIsNotNone(link)
        assert link is not None
        self.assertEqual(
            "/Users/yar/worktree/webapp/app/[lang]/(app)/sidenav.tsx:33",
            link.destination,
        )

    def test_finds_link_when_destination_is_clicked(self) -> None:
        text = "[file](/tmp/a(b(c)).py:7:2)"

        link = markdown_link_at(text, text.index("b(c)"))

        self.assertIsNotNone(link)
        assert link is not None
        self.assertEqual("/tmp/a(b(c)).py:7:2", link.destination)

    def test_ignores_images_and_reference_links(self) -> None:
        self.assertIsNone(markdown_link_at("![image](/tmp/a.png)", 3))
        self.assertIsNone(markdown_link_at("[reference][id]", 3))

    def test_normalizes_existing_file_with_encoded_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "source.py")
            path.touch()

            self.assertEqual(f"{path}:33:4", local_file_target(f"{path}:33:4"))

    def test_decodes_file_url_and_rejects_web_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "source file.py")
            path.touch()
            url_path = str(path).replace(" ", "%20")

            self.assertEqual(
                f"{path}:9",
                local_file_target(f"file://{url_path}#L9"),
            )
        self.assertIsNone(local_file_target("https://example.com/source.py:9"))

    def test_rejects_missing_and_relative_files(self) -> None:
        self.assertIsNone(local_file_target("relative.py:1"))
        self.assertIsNone(local_file_target(os.path.join(tempfile.gettempdir(), "missing.py:1")))


if __name__ == "__main__":
    unittest.main()
