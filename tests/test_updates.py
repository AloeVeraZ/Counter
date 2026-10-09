from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from counter.updates import Updates, REPOSITORY


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.commit = 'a' * 40
        (self.root / 'INSTALL_COMMIT').write_text(self.commit)
        self.updates = Updates(self.root)
        self.addCleanup(self.temp.cleanup)

    def test_current_release_and_new_commit(self):
        with patch('counter.updates.subprocess.run', return_value=SimpleNamespace(stdout='b'*40+'\trefs/heads/main\n')) as run:
            self.updates._check()
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0],['git','ls-remote',REPOSITORY,'refs/heads/main'])
        status = self.updates.snapshot()
        self.assertEqual(status['installed'], 'aaaaaaa')
        self.assertEqual(status['latest'], 'bbbbbbb')
        self.assertTrue(status['available'])
        self.assertFalse(status['checking'])

    def test_network_error_and_malformed_response_never_claim_current(self):
        for result in [subprocess.TimeoutExpired('git',20), SimpleNamespace(stdout='not a commit')]:
            kwargs = {'side_effect':result} if isinstance(result,Exception) else {'return_value':result}
            with patch('counter.updates.subprocess.run',**kwargs):
                self.updates._check()
            status = self.updates.snapshot()
            self.assertFalse(status['available'])
            self.assertTrue(status['error'])

    def test_matching_commit_reports_no_update(self):
        with patch('counter.updates.subprocess.run',return_value=SimpleNamespace(stdout=self.commit+'\trefs/heads/main\n')):
            self.updates._check()
        self.assertFalse(self.updates.snapshot()['available'])

    def test_installer_invokes_only_fixed_helper_with_no_arguments(self):
        helper = self.root / 'counter-update'
        helper.write_text('test helper')
        with patch('counter.updates.HELPER',helper), patch('counter.updates.subprocess.run',return_value=SimpleNamespace(returncode=0,stderr='')) as run:
            self.assertIn('Update started',self.updates.start())
            self.assertEqual(run.call_args.args[0],['sudo','-n',str(helper)])

    def test_missing_or_refused_helper_is_reported(self):
        helper = self.root / 'counter-update'
        with patch('counter.updates.HELPER',helper):
            with self.assertRaises(ValueError): self.updates.start()
            helper.write_text('test helper')
            with patch('counter.updates.subprocess.run',return_value=SimpleNamespace(returncode=1,stderr='sudo refused')):
                with self.assertRaisesRegex(RuntimeError,'sudo refused'): self.updates.start()
