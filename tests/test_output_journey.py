"""User-output recovery and staging checks; synthetic files, no provider traffic."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagnostics import BuildFailure
from output_publish import staged_output, validate_paths, mark_owned, PublishInterrupted
from status_page import render_status


class OutputJourneyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / 'public'
        self.work = self.root / 'work'
        self.diagnostic = self.root / 'private' / 'build.json'

    def previous(self):
        self.out.mkdir()
        (self.out / 'status.json').write_text('previous status')
        (self.out / 'index.html').write_text('previous working index')
        mark_owned(self.out, 'output')

    def test_failure_keeps_previous_output_and_leaves_no_stage(self):
        self.previous()
        with self.assertRaisesRegex(RuntimeError, 'synthetic index failure'):
            with staged_output(self.out, self.work, self.diagnostic) as stage:
                (stage / 'index.html').write_text('partial replacement')
                raise RuntimeError('synthetic index failure')
        self.assertEqual((self.out / 'index.html').read_text(), 'previous working index')
        self.assertFalse(list(self.root.glob('.atr-search-stage-*')))

    def test_success_and_repeat_preserve_versioned_previous_output(self):
        self.previous()
        for value in ('new complete index', 'next complete index'):
            with staged_output(self.out, self.work, self.diagnostic) as stage:
                (stage / 'index.html').write_text(value)
            self.assertEqual((self.out / 'index.html').read_text(), value)
        backups = list(self.root.glob('.atr-search-output-backup-*'))
        self.assertEqual(len(backups), 2)
        self.assertEqual({(p / 'index.html').read_text() for p in backups}, {'previous working index', 'new complete index'})

    def test_promotion_error_restores_old_output(self):
        self.previous()
        original = os.replace
        def fail_stage(source, destination):
            if Path(source).name.startswith('.atr-search-stage-'):
                raise PermissionError('synthetic promotion failure')
            return original(source, destination)
        with patch('output_publish.os.replace', side_effect=fail_stage):
            with self.assertRaises(BuildFailure):
                with staged_output(self.out, self.work, self.diagnostic) as stage:
                    (stage / 'index.html').write_text('new')
        self.assertEqual((self.out / 'index.html').read_text(), 'previous working index')

    def test_overlapping_paths_are_rejected_before_touching_output(self):
        self.previous()
        for work, diagnostic in ((self.out / 'work', self.diagnostic), (self.work, self.out / 'private.json')):
            with self.assertRaises(BuildFailure):
                with staged_output(self.out, work, diagnostic): pass
            self.assertEqual((self.out / 'index.html').read_text(), 'previous working index')

    def test_cancel_during_promotion_restores_previous_output(self):
        self.previous()
        original = os.replace
        def cancel_stage(source, destination):
            if Path(source).name.startswith('.atr-search-stage-'):
                raise KeyboardInterrupt()
            return original(source, destination)
        with patch('output_publish.os.replace', side_effect=cancel_stage):
            with self.assertRaises(KeyboardInterrupt):
                with staged_output(self.out, self.work, self.diagnostic) as stage:
                    (stage / 'index.html').write_text('replacement')
        self.assertEqual((self.out / 'index.html').read_text(), 'previous working index')

    def test_existing_file_is_not_replaced_by_generated_directory(self):
        self.out.write_text('unrelated file')
        with self.assertRaises(BuildFailure):
            with staged_output(self.out, self.work, self.diagnostic): pass
        self.assertEqual(self.out.read_text(), 'unrelated file')

    def test_unowned_work_output_git_and_evidence_are_not_touched(self):
        self.previous()
        for name in ('source-folder', '.GiT'):
            target=self.root/name;target.mkdir();(target/'source.txt').write_text('keep')
            with self.assertRaises(BuildFailure) as caught: validate_paths(self.out,target,self.diagnostic)
            self.assertEqual(caught.exception.reason,'unsafe_output_paths' if name=='.GiT' else 'unowned_generated_paths')
            self.assertEqual((target/'source.txt').read_text(),'keep')
        other=self.root/'unowned-output';other.mkdir();(other/'notes.txt').write_text('keep output')
        with self.assertRaises(BuildFailure) as caught: validate_paths(other,self.work,self.diagnostic)
        self.assertEqual(caught.exception.reason,'unowned_generated_paths')
        evidence=self.diagnostic.parent/'evidence';evidence.mkdir(parents=True);(evidence/'reviewed.json').write_text('keep evidence')
        with self.assertRaises(BuildFailure) as caught: validate_paths(self.out,evidence,self.diagnostic)
        self.assertEqual(caught.exception.reason,'unsafe_output_paths')
        self.assertEqual((evidence/'reviewed.json').read_text(),'keep evidence')

    def test_interruption_after_commit_is_observed_not_blindly_rolled_back(self):
        self.previous();original=os.replace
        def interrupt_after(source,destination):
            result=original(source,destination)
            if Path(source).name.startswith('.atr-search-stage-'): raise KeyboardInterrupt()
            return result
        with patch('output_publish.os.replace',side_effect=interrupt_after):
            with self.assertRaises(PublishInterrupted):
                with staged_output(self.out,self.work,self.diagnostic) as stage:
                    (stage/'index.html').write_text('complete new output')
        self.assertEqual((self.out/'index.html').read_text(),'complete new output')
        self.assertEqual(len(list(self.root.glob('.atr-search-output-backup-*'))),1)

    def test_missing_output_with_owned_backup_blocks_blind_restart(self):
        self.previous();backup=self.root/'.atr-search-output-backup-public-test';os.replace(self.out,backup)
        with self.assertRaises(BuildFailure) as caught: validate_paths(self.out,self.work,self.diagnostic)
        self.assertEqual(caught.exception.reason,'output_recovery_required')
        self.assertEqual((backup/'index.html').read_text(),'previous working index')

    def test_failed_rollback_preserves_backup_and_requires_recovery(self):
        self.previous();original=os.replace
        def fail_both(source,destination):
            if Path(source).name.startswith(('.atr-search-stage-','.atr-search-output-backup-')):
                raise PermissionError('synthetic blocked promotion/restore')
            return original(source,destination)
        with patch('output_publish.os.replace',side_effect=fail_both):
            with self.assertRaises(BuildFailure) as caught:
                with staged_output(self.out,self.work,self.diagnostic) as stage:
                    (stage/'index.html').write_text('new')
        self.assertEqual(caught.exception.reason,'output_rollback_failed')
        self.assertFalse(self.out.exists())
        self.assertEqual(len(list(self.root.glob('.atr-search-output-backup-*'))),1)
        with self.assertRaises(BuildFailure) as caught: validate_paths(self.out,self.work,self.diagnostic)
        self.assertEqual(caught.exception.reason,'output_recovery_required')

    def test_status_page_links_and_accessible_fallback(self):
        page = render_status({'built':'2026-11-04T00:00:00Z','posts':3,'chinese':1,'japanese':1})
        for marker in ('lang="en"', 'name="viewport"', 'aria-label="Search and update links"',
                       'role="status"', 'aria-live="polite"', 'download="atr-search-status.json"',
                       'Last successful update', '<noscript>', 'data-state="unknown"'):
            self.assertIn(marker, page)
        self.assertNotIn('fetch(', page)
        self.assertNotIn('localStorage', page)
        self.assertNotIn('navigator.sendBeacon', page)


if __name__ == '__main__':
    unittest.main()
