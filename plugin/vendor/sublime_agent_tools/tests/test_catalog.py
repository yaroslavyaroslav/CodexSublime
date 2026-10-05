import unittest

from catalog import dynamic_tool_namespace


class DynamicToolCatalogTests(unittest.TestCase):
    def test_declares_narrow_sublime_namespace(self) -> None:
        namespace = dynamic_tool_namespace()

        self.assertEqual(namespace['type'], 'namespace')
        self.assertEqual(namespace['name'], 'sublime')
        self.assertEqual(
            [tool['name'] for tool in namespace['tools']],
            ['open_diff', 'set_annotations', 'clear_annotations', 'list_views', 'close_views'],
        )
        descriptions = ' '.join(tool['description'] for tool in namespace['tools'])
        descriptions_lower = descriptions.lower()
        self.assertIn('Deleted-only files are ignored', descriptions)
        self.assertIn('Do not annotate deleted-only lines', descriptions)
        self.assertIn('dirty views are always skipped', descriptions_lower)
        self.assertIn('group', descriptions_lower)
        self.assertIn('Never use Sublime CLI bulk-close commands', descriptions)

        annotations = next(tool for tool in namespace['tools'] if tool['name'] == 'set_annotations')
        schema = annotations['inputSchema']
        self.assertEqual(schema['required'], ['files'])
        self.assertNotIn('path', schema['properties'])
        file_schema = schema['properties']['files']['items']
        self.assertEqual(file_schema['required'], ['path', 'annotations'])
        self.assertIn('expected_change_count', file_schema['properties'])


if __name__ == '__main__':
    unittest.main()
