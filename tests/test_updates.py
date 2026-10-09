from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from counter.updates import Updates, REPOSITORY, STAGES, update_job


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
            self.assertEqual(run.call_args.args[0],['git','ls-remote','--heads',REPOSITORY,'refs/heads/main','refs/heads/testing'])
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

    def test_installer_invokes_only_fixed_helper_with_supported_branch(self):
        helper = self.root / 'counter-update'
        helper.write_text('test helper')
        with patch('counter.updates.HELPER',helper), patch('counter.updates.subprocess.run',return_value=SimpleNamespace(returncode=0,stderr='')) as run:
            self.assertIn('Update started',self.updates.start())
            self.assertEqual(run.call_args.args[0],['sudo','-n',str(helper),'main'])

    def test_missing_or_refused_helper_is_reported(self):
        helper = self.root / 'counter-update'
        with patch('counter.updates.HELPER',helper):
            with self.assertRaises(ValueError): self.updates.start()
            helper.write_text('test helper')
            with patch('counter.updates.subprocess.run',return_value=SimpleNamespace(returncode=1,stderr='sudo refused')):
                with self.assertRaisesRegex(RuntimeError,'sudo refused'): self.updates.start()

    def test_testing_install_checks_testing_and_can_return_to_main(self):
        (self.root / 'INSTALL_REF').write_text('testing')
        updates = Updates(self.root)
        refs = 'b'*40+'\trefs/heads/main\n'+'c'*40+'\trefs/heads/testing\n'
        with patch('counter.updates.subprocess.run', return_value=SimpleNamespace(stdout=refs)):
            updates._check()
        status = updates.snapshot()
        self.assertEqual(status['branch'], 'testing')
        self.assertEqual(status['latest'], 'ccccccc')
        self.assertEqual([row['branch'] for row in status['targets']], ['main', 'testing'])
        self.assertTrue(all(row['available'] for row in status['targets']))
        helper = self.root / 'helper'
        helper.touch()
        with patch('counter.updates.HELPER', helper), patch('counter.updates.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            updates.start('main')
            self.assertEqual(run.call_args.args[0][-1], 'main')
            with self.assertRaisesRegex(ValueError, 'Acknowledge'):
                updates.start('testing')
            updates.start('testing', acknowledge_testing=True)
            self.assertEqual(run.call_args.args[0][-1], 'testing')

    def test_switch_channel_even_when_both_have_the_same_commit(self):
        refs = ''.join(self.commit+'\trefs/heads/'+branch+'\n' for branch in ['main','testing'])
        with patch('counter.updates.subprocess.run', return_value=SimpleNamespace(stdout=refs)):
            self.updates._check()
        rows = self.updates.snapshot()['targets']
        self.assertFalse(rows[0]['available'])
        self.assertTrue(rows[1]['available'])

    def test_unknown_channels_and_unacknowledged_testing_never_invoke_sudo(self):
        with patch('counter.updates.subprocess.run') as run:
            for branch in ['other', '--run', 'testing;reboot', '', [], {}]:
                with self.subTest(branch=branch), self.assertRaises(ValueError):
                    self.updates.start(branch, acknowledge_testing=True)
            for ack in [False, 1, 'true', None]:
                with self.assertRaises(ValueError):
                    self.updates.start('testing', acknowledge_testing=ack)
            run.assert_not_called()

    def test_install_ref_is_validated_and_missing_target_is_unavailable(self):
        (self.root/'INSTALL_REF').write_text('untrusted/ref')
        self.assertEqual(Updates(self.root).branch,'main')
        with patch('counter.updates.subprocess.run',return_value=SimpleNamespace(stdout=self.commit+'\trefs/heads/main\n')):
            self.updates._check()
        self.assertFalse(self.updates.snapshot()['targets'][1]['available'])

    def test_update_progress_completed_failed_and_interrupted(self):
        log = self.root/'update.log'
        with patch('counter.updates.UPDATE_LOG',log):
            self.assertEqual(update_job()['state'],'idle')
            log.write_text('Starting Counter / testing.\nDownloading...\n')
            with patch('counter.updates.subprocess.run',return_value=SimpleNamespace(stdout='active\n')):
                self.assertEqual(update_job()['state'],'running')
            with patch('counter.updates.subprocess.run',return_value=SimpleNamespace(stdout='inactive\n')):
                self.assertEqual(update_job()['state'],'failed')
            log.write_text('Counter update completed.\n')
            self.assertEqual(update_job()['state'],'finished')
            log.write_text('Counter update failed (exit 1).\n')
            self.assertEqual(update_job()['state'],'failed')

    def test_update_job_reports_the_latest_stage_even_after_long_output(self):
        log = self.root/'update.log'
        noise = ''.join(f'apt line {n}\n' for n in range(100))
        log.write_text('==> downloading Counter / testing\n==> checking the Pi\n==> building the Counter release\n'+noise)
        with patch('counter.updates.UPDATE_LOG',log), patch('counter.updates.subprocess.run',return_value=SimpleNamespace(stdout='active\n')):
            job = update_job()
        self.assertEqual(job['stage'],'building the Counter release')
        self.assertEqual(job['progress'],round(100*(STAGES.index('building the Counter release')+1)/(len(STAGES)+1)))
        log.write_text(f'==> {STAGES[-1]}\n')
        with patch('counter.updates.UPDATE_LOG',log), patch('counter.updates.subprocess.run',return_value=SimpleNamespace(stdout='active\n')):
            self.assertLess(update_job()['progress'],100)
        log.write_text(f'==> {STAGES[-1]}\nCounter update completed.\n')
        with patch('counter.updates.UPDATE_LOG',log):
            self.assertEqual(update_job()['progress'],100)
        self.assertEqual(len(job['log']),20)

    def test_every_stage_is_logged_by_the_installer_scripts(self):
        scripts = (Path(__file__).parents[1]/'installer/install.sh').read_text(encoding='utf-8') + \
                  (Path(__file__).parents[1]/'installer/update.sh').read_text(encoding='utf-8')
        for stage in STAGES:
            self.assertTrue(f"begin '{stage}" in scripts or f'echo "==> {stage}' in scripts, stage)
