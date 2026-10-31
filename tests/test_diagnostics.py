"""Failure injection and privacy tests; no network or visitor data."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_index as builder
from diagnostics import BuildFailure, Diagnostics, validate_index
import workflow_summary
from workflow_summary import render

MARKER = 'PRIVATE_MARKER_DO_NOT_LOG'


class DiagnosticTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root / 'diagnostics.json'
        self.console = io.StringIO()
        self.previous = builder.DIAGNOSTICS
        self.addCleanup(setattr, builder, 'DIAGNOSTICS', self.previous)
        builder.DIAGNOSTICS = Diagnostics(self.report)

    def run_main(self, entries):
        fixture = self.root / 'feed.json'
        fixture.write_text(json.dumps({'feed': {'entry': entries}}), encoding='utf-8')
        argv = ['build_index.py', '--feed-file', str(fixture), '--out', str(self.root / 'public'),
                '--work', str(self.root / 'site'), '--diagnostics', str(self.report)]
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(self.console):
            return builder.main()

    def test_retry_recovers_without_url_or_exception_text(self):
        response = io.BytesIO(b'{"feed": {}}')
        with patch.object(builder.urllib.request, 'urlopen', side_effect=[urllib.error.URLError(MARKER), response]), \
                patch.object(builder.time, 'sleep') as sleep, contextlib.redirect_stdout(self.console):
            result = builder.fetch_json('https://example.invalid/?token=' + MARKER)
        self.assertEqual(result, {'feed': {}})
        sleep.assert_called_once_with(5)
        self.assertEqual([x['event'] for x in builder.DIAGNOSTICS.events], ['fetch_failed', 'fetch_succeeded'])
        self.assertEqual(builder.DIAGNOSTICS.events[0]['level'], 'warning')
        self.assertNotIn(MARKER, self.console.getvalue())

    def test_exhausted_fetch_is_error_and_no_response_body(self):
        error = urllib.error.HTTPError('https://example.invalid/' + MARKER, 503, MARKER, {}, None)
        with patch.object(builder.urllib.request, 'urlopen', side_effect=error), \
                patch.object(builder.time, 'sleep'), contextlib.redirect_stdout(self.console):
            with self.assertRaises(urllib.error.HTTPError):
                builder.fetch_json('https://example.invalid/' + MARKER, tries=2)
        row = builder.DIAGNOSTICS.events[-1]
        self.assertEqual((row['level'], row['http_status'], row['retry_seconds']), ('error', 503, 0))
        self.assertNotIn(MARKER, self.console.getvalue())

    def test_wrapped_fetch_timeout_is_identified_and_redacted(self):
        error = urllib.error.URLError(TimeoutError(MARKER))
        with patch.object(builder.urllib.request, 'urlopen', side_effect=error), contextlib.redirect_stdout(self.console):
            with self.assertRaises(urllib.error.URLError):
                builder.fetch_json('https://example.invalid/' + MARKER, tries=1)
        row = builder.DIAGNOSTICS.events[-1]
        self.assertTrue(row['timeout'])
        self.assertEqual(row['level'], 'error')
        self.assertNotIn(MARKER, self.console.getvalue())

    def test_malformed_fetch_json_is_identified_and_redacted(self):
        with patch.object(builder.urllib.request, 'urlopen', return_value=io.BytesIO(MARKER.encode())), contextlib.redirect_stdout(self.console):
            with self.assertRaises(ValueError):
                builder.fetch_json('https://example.invalid/' + MARKER, tries=1)
        self.assertTrue(builder.DIAGNOSTICS.events[-1]['invalid_json'])
        self.assertNotIn(MARKER, self.console.getvalue())

    def test_premature_feed_end_is_warning(self):
        with patch.object(builder, 'fetch_json', side_effect=[{'feed': {'openSearch$totalResults': {'$t': '2'}, 'entry': [{}]}}, {'feed': {}}]), \
                contextlib.redirect_stdout(self.console):
            self.assertEqual(len(builder.all_entries()), 1)
        self.assertEqual(builder.DIAGNOSTICS.events[-1]['event'], 'feed_ended_early')
        self.assertEqual(builder.DIAGNOSTICS.warnings, 1)

    def test_skip_reasons_and_failure_preserve_previous_output(self):
        out = self.root / 'public'
        out.mkdir()
        (out / 'status.json').write_text('previous', encoding='utf-8')
        entries = [
            {'content': {'$t': MARKER}},
            {'link': [{'rel': 'alternate', 'href': 'https://example.invalid/' + MARKER}]},
            {'link': [{'rel': 'alternate', 'href': 'https://example.invalid/p/' + MARKER}], 'content': {'$t': MARKER}},
        ]
        self.assertEqual(self.run_main(entries), 1)
        report = json.loads(self.report.read_text(encoding='utf-8'))
        counts = next(e for e in report['events'] if e['event'] == 'records_selected')
        self.assertEqual([counts[k] for k in ('missing_link', 'empty_content', 'static_pages')], [1, 1, 1])
        self.assertEqual(report['outcome'], 'failed')
        self.assertEqual(report['events'][-1]['level'], 'error')
        self.assertEqual((out / 'status.json').read_text(), 'previous')
        self.assertNotIn(MARKER, self.report.read_text() + self.console.getvalue())

    def test_subprocess_failure_records_exit_code_not_stderr(self):
        with patch.object(builder, 'PAGEFIND', self.report), patch.object(Path, 'is_file', return_value=True), \
                patch.object(builder.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7, MARKER.encode(), MARKER.encode())), \
                contextlib.redirect_stdout(self.console):
            with self.assertRaises(BuildFailure):
                builder.run_pagefind('unused', 'unused', 'en')
        self.assertEqual(builder.DIAGNOSTICS.events[-1]['exit_code'], 7)
        self.assertNotIn(MARKER, self.console.getvalue())

    def test_main_records_failed_validation_without_status(self):
        entries = [{'link': [{'rel': 'alternate', 'href': 'https://example.invalid/' + MARKER}], 'content': {'$t': MARKER}}]
        with patch.object(builder, 'run_pagefind'), patch.object(builder, 'validate_index', side_effect=BuildFailure(MARKER)):
            self.assertEqual(self.run_main(entries), 1)
        report = json.loads(self.report.read_text())
        self.assertEqual((report['outcome'], report['stage']), ('failed', 'validation'))
        self.assertFalse((self.root / 'public' / 'status.json').exists())
        self.assertNotIn(MARKER, self.report.read_text() + self.console.getvalue())

    def test_manifest_checks_counts_and_assets(self):
        out = self.root / 'index'
        out.mkdir()
        manifest = {'languages': {'en': {'hash': 'en_test', 'wasm': 'en', 'page_count': 2}}}
        (out / 'pagefind-entry.json').write_text(json.dumps(manifest))
        with self.assertRaises(BuildFailure):
            validate_index(out, 'en', 1)
        with self.assertRaises(BuildFailure):
            validate_index(out, 'en', 2)
        for name in ('pagefind.js', 'pagefind.en_test.pf_meta', 'wasm.en.pagefind', 'fragment/en_test.pf_fragment', 'index/en_test.pf_index'):
            target = out / name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(b'fixture')
        self.assertEqual(validate_index(out, 'en', 2)['pages'], 2)
        manifest['languages']['en']['wasm'] = None
        (out / 'wasm.unknown.pagefind').write_bytes(b'fixture')
        (out / 'pagefind-entry.json').write_text(json.dumps(manifest))
        self.assertEqual(validate_index(out, 'en', 2)['pages'], 2)
        manifest['languages']['en']['hash'] = '../outside'
        (out / 'pagefind-entry.json').write_text(json.dumps(manifest))
        with self.assertRaises(BuildFailure):
            validate_index(out, 'en', 2)

    def test_diagnostic_write_failure_does_not_hide_console_outcome(self):
        builder.DIAGNOSTICS.path = self.root  # a directory cannot be replaced by a report
        with contextlib.redirect_stdout(self.console):
            builder.DIAGNOSTICS.finish('succeeded')
        self.assertIn('build_succeeded', self.console.getvalue())
        self.assertIn('diagnostic_write_failed', self.console.getvalue())

    def test_broken_console_keeps_report_and_outcome(self):
        with patch('builtins.print', side_effect=BrokenPipeError(MARKER)):
            builder.DIAGNOSTICS.emit('test_event', count=1)
            builder.DIAGNOSTICS.finish('succeeded')
        report = json.loads(self.report.read_text())
        self.assertEqual(report['outcome'], 'succeeded')
        self.assertEqual(report['console_write_errors'], 2)
        self.assertNotIn(MARKER, self.report.read_text())

    def test_summary_write_error_has_no_raw_path_or_message(self):
        with patch.dict('os.environ', {'GITHUB_STEP_SUMMARY': str(self.root / MARKER)}), \
                patch.object(Path, 'open', side_effect=PermissionError(MARKER)), contextlib.redirect_stdout(self.console):
            workflow_summary.main()
        self.assertIn('summary file could not be written', self.console.getvalue())
        self.assertNotIn(MARKER, self.console.getvalue())

    def test_cancelled_build_records_terminal_outcome(self):
        entries = [{'link': [{'rel': 'alternate', 'href': 'https://example.invalid/' + MARKER}], 'content': {'$t': MARKER}}]
        with patch.object(builder, 'run_pagefind', side_effect=KeyboardInterrupt(MARKER)):
            self.assertEqual(self.run_main(entries), 130)
        report = json.loads(self.report.read_text())
        self.assertEqual(report['outcome'], 'cancelled')
        self.assertEqual(report['events'][-1]['event'], 'build_cancelled')
        self.assertEqual(report['events'][-1]['level'], 'warning')
        self.assertFalse((self.root / 'public' / 'status.json').exists())
        self.assertNotIn(MARKER, self.report.read_text() + self.console.getvalue())

    def test_context_and_summary_exclude_untrusted_fields(self):
        with patch.dict('os.environ', {'GITHUB_RUN_ID': MARKER, 'GITHUB_SHA': MARKER, 'GITHUB_RUN_ATTEMPT': MARKER}):
            diagnostics = Diagnostics(self.report)
        self.assertNotIn(MARKER, json.dumps(diagnostics.context))
        summary = render({'index': {'outcome': 'failure', 'outputs': {'secret': MARKER}},
                          'artifact': {'outcome': MARKER}, MARKER: {'outcome': 'failure'}}, MARKER, MARKER)
        self.assertIn('| index | failure |', summary)
        self.assertIn('| artifact | unavailable |', summary)
        self.assertNotIn(MARKER, summary)


if __name__ == '__main__':
    unittest.main()
