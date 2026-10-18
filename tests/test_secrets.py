"""Fake-key tests runnable by pytest or the standard library's unittest."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import secrets


class SecretTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'fake.env'
        self.env_patch = patch.dict(os.environ, {}, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.path_patch = patch.object(secrets, '_credential_path', return_value=self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def write(self, text):
        self.path.write_bytes(text.encode())

    def test_assignment_formats(self):
        for text in ('OPENCODE_API_KEY=oc_fake', 'export OPENCODE_API_KEY=oc_fake',
                     ' OPENCODE_API_KEY = oc_fake ', 'OPENCODE_API_KEY="oc_fake"',
                     "OPENCODE_API_KEY='oc_fake'", 'OPENCODE_API_KEY=oc_fake\r\n',
                     'OPENCODE_API_KEY=oc_fake # comment',
                     'export OPENCODE_API_KEY = "oc_fake" # comment\r\n'):
            with self.subTest(text=text):
                self.write(text)
                self.assertEqual(secrets.load_opencode_key(), 'oc_fake')

    def test_bare_key(self):
        for text in ('oc_fake', '\n# comment\r\n\r\n' + 'sk-' + 'fake\r\n', ' "oc_fake" '):
            self.write(text)
            self.assertIn(secrets.load_opencode_key(), ('oc_fake', 'sk-' + 'fake'))

    def test_ambiguous_or_missing(self):
        for text in ('', '# comment', 'oc_fake\n' + 'sk-' + 'fake', 'OTHER=oc_fake',
                     'OPENCODE_API_KEY=', 'OPENCODE_API_KEY="unfinished',
                     'OTHER=value\noc_fake'):
            self.write(text)
            with self.assertRaises(RuntimeError):
                secrets.load_opencode_key()

    def test_environment_precedence(self):
        os.environ['OPENCODE_API_KEY'] = 'oc_environment_fake'
        self.assertEqual(secrets.load_opencode_key(), 'oc_environment_fake')

    def test_empty_environment_fallback(self):
        os.environ['OPENCODE_API_KEY'] = ''
        self.write('oc_fake')
        self.assertEqual(secrets.load_opencode_key(), 'oc_fake')

    def test_missing_file(self):
        with self.assertRaises(RuntimeError):
            secrets.load_opencode_key()

    def test_value_equals_and_hash_preserved(self):
        self.write('OPENCODE_API_KEY="oc_fake=hash#literal" # comment')
        self.assertEqual(secrets.load_opencode_key(), 'oc_fake=hash#literal')

    def test_metadata_only(self):
        self.write(' export OPENCODE_API_KEY = "oc_fake" # comment\r\noc_bare_fake\n')
        result = secrets.diagnose_key_file()
        self.assertEqual(result['number_of_lines'], 2)
        first = result['lines'][0]
        self.assertEqual(first['left'], '<sp>export<sp>OPENCODE_API_KEY<sp>')
        self.assertTrue(first['has_cr'] and first['surrounding_quotes'] and first['trailing_comment'])
        self.assertEqual(result['lines'][1], {'length': 12, 'starts_oc_or_sk': True})
        self.assertNotIn('oc_fake', str(result))
        self.assertNotIn('oc_bare_fake', str(result))


if __name__ == '__main__':
    unittest.main()
