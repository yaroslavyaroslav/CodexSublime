import unittest

from catalog import dynamic_tool_namespace


class DynamicToolCatalogTests(unittest.TestCase):
    def test_declares_narrow_sublime_namespace(self) -> None:
        namespace = dynamic_tool_namespace()

        self.assertEqual(namespace['type'], 'namespace')
        self.assertEqual(namespace['name'], 'sublime')
        self.assertEqual(
            [tool['name'] for tool in namespace['tools']],
            ['open_diff', 'set_annotations', 'clear_annotations'],
        )
        descriptions = ' '.join(tool['description'] for tool in namespace['tools'])
        self.assertIn('Deleted-only files are ignored', descriptions)
        self.assertIn('Do not annotate deleted-only lines', descriptions)


if __name__ == '__main__':
    unittest.main()
