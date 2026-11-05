"""Assert bytes actually persisted/exported using synthetic upstream failures only."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import base64

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_index as builder
from diagnostics import Diagnostics
from safe_evidence import BODY_LIMIT, FILE_LIMIT, SAFE_KEYS, EvidenceStore, run_bounded

SECRET = 'canary-secret-value-9c3ab781'
PAYLOAD = 'private-visitor-query-or-article-content'


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = io.StringIO()
        self.addCleanup(setattr, builder, 'DIAGNOSTICS', builder.DIAGNOSTICS)
        self.addCleanup(setattr, builder, 'EVIDENCE', builder.EVIDENCE)
        builder.DIAGNOSTICS = Diagnostics(self.root / 'build.json')
        self.store = EvidenceStore(self.root / 'private', '123abc', public_root=self.root / 'public', secrets=(SECRET,))
        builder.EVIDENCE = self.store

    def persisted(self, *, reviewed=False):
        if reviewed:
            # Synthetic-only operator review; canaries checked before explicit retention.
            memory = json.dumps(self.store.records)
            self.assertNotIn(SECRET, memory)
            self.assertNotIn(PAYLOAD, memory)
            for sequence in [row['record_sequence'] for row in self.store.records]:
                self.store.retain_reviewed(sequence)
        files = list(self.store.directory.glob('*.json'))
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding='utf-8')
        self.assertNotIn(SECRET, text)
        self.assertNotIn(PAYLOAD, text)
        self.assertLessEqual(files[0].stat().st_size, FILE_LIMIT)
        return json.loads(text)['records']

    def test_nested_http_provider_reason_ids_and_secrets(self):
        body = json.dumps({'error': {'code': 'RATE_LIMITED', 'message': 'Quota exhausted; retry after window.',
                           'details': [{'reason': 'daily request cap reached', 'cause': {'message': 'retry after 60 seconds', 'token': SECRET},
                                        'violations': [{'description': 'quota metric exhausted', 'subject': PAYLOAD}]}],
                           'request': {'query': PAYLOAD}, 'password': SECRET}}).encode()
        error = urllib.error.HTTPError('https://user:' + SECRET + '@www.awakeningtoreality.com/feeds/posts/full?token=' + SECRET,
                                       429, 'Bearer ' + SECRET, {'x-request-id': 'req-safe-123', 'set-cookie': SECRET}, io.BytesIO(body))
        self.addCleanup(error.close)
        with patch.object(builder.urllib.request, 'urlopen', side_effect=error), contextlib.redirect_stdout(self.out):
            with self.assertRaises(urllib.error.HTTPError):
                builder.fetch_json(error.url, tries=1)
        rec = self.persisted(reviewed=True)[0]
        self.assertEqual(rec['status'], 429)
        self.assertEqual(rec['correlation_ids']['x-request-id'], 'req-safe-123')
        self.assertEqual(rec['endpoint'], 'https://www.awakeningtoreality.com/feeds/posts/full')
        self.assertEqual(rec['explanation']['detail']['error']['code'], 'RATE_LIMITED')
        self.assertIn('daily request cap reached', json.dumps(rec))
        self.assertIn('retry after 60 seconds', json.dumps(rec))
        self.assertIn('quota metric exhausted', json.dumps(rec))
        self.assertIn('redacted_fields', json.dumps(rec))
        self.assertNotIn(SECRET, self.out.getvalue())
        self.assertNotIn('Quota exhausted', self.out.getvalue())
        self.assertTrue(rec['utc'].endswith('+00:00'))

    def test_nonjson_error_and_nested_causal_chain(self):
        inner = TimeoutError('upstream socket deadline exceeded; token=' + SECRET)
        outer = urllib.error.URLError(inner)
        self.store.capture('fetch', error=outer, raw=b'<h1>Gateway timeout</h1><p>edge route unavailable</p>', status=504, seconds=1.5)
        rec = self.persisted(reviewed=True)[0]
        self.assertIn('edge route unavailable', json.dumps(rec))
        self.assertEqual([c['type'] for c in rec['causes']], ['URLError', 'TimeoutError'])
        self.assertEqual(rec['duration_seconds'], 1.5)

    def test_auth_challenge_preserves_explanation_not_cookie_or_realm(self):
        self.store.capture('fetch', status=401, headers={'WWW-Authenticate': 'Bearer realm="' + SECRET + '", error="invalid_token", error_description="The credential has expired; refresh access"', 'X-Request-ID': 'safe-request-22', 'Set-Cookie': SECRET})
        rec = self.persisted(reviewed=True)[0]
        self.assertEqual(rec['authentication_challenge']['error'], 'invalid_token')
        self.assertEqual(rec['authentication_challenge']['error_description'], 'The credential has expired; refresh access')
        self.assertEqual(rec['correlation_ids']['x-request-id'], 'safe-request-22')
        self.assertNotIn('realm', json.dumps(rec))

    def test_successful_response_parse_failure_never_persists_payload(self):
        response = io.BytesIO((PAYLOAD + SECRET).encode())
        with patch.object(builder.urllib.request, 'urlopen', return_value=response), contextlib.redirect_stdout(self.out):
            with self.assertRaises(ValueError):
                builder.fetch_json('https://www.awakeningtoreality.com/feeds/posts/full', tries=1)
        rec = self.persisted(reviewed=True)[0]
        self.assertEqual(rec['explanation']['state'], 'redacted')
        self.assertEqual(rec['causes'][0]['type'], 'JSONDecodeError')

    def test_oversize_response_is_bounded_and_marks_truncation(self):
        body = b'{"message":"upstream capacity exceeded", "request":"' + PAYLOAD.encode() + b'X' * BODY_LIMIT + b'"}'
        error = urllib.error.HTTPError('https://example.invalid', 503, 'busy', {}, io.BytesIO(body))
        self.addCleanup(error.close)
        with patch.object(builder.urllib.request, 'urlopen', side_effect=error), contextlib.redirect_stdout(self.out):
            with self.assertRaises(urllib.error.HTTPError):
                builder.fetch_json('https://example.invalid', tries=1)
        rec = self.persisted(reviewed=True)[0]
        self.assertTrue(rec['explanation']['truncated'])
        self.assertIn('upstream capacity exceeded', json.dumps(rec))

    def test_oversize_success_feed_is_not_retained(self):
        with patch.object(builder, 'FEED_LIMIT', 20), patch.object(builder.urllib.request, 'urlopen', return_value=io.BytesIO(PAYLOAD.encode() * 10)), contextlib.redirect_stdout(self.out):
            with self.assertRaises(ValueError):
                builder.fetch_json('https://example.invalid', tries=1)
        rec = self.persisted(reviewed=True)[0]
        self.assertEqual(rec['explanation']['state'], 'redacted')
        self.assertTrue(rec['explanation']['truncated'])

    def test_subprocess_stderr_reason_is_persisted_not_console(self):
        result = subprocess.CompletedProcess([], 9, b'', ('dependency missing; token=' + SECRET).encode())
        with patch.object(builder, 'PAGEFIND', self.root), patch.object(Path, 'is_file', return_value=True), \
                patch.object(builder, 'run_bounded', return_value=result), contextlib.redirect_stdout(self.out):
            with self.assertRaises(Exception):
                builder.run_pagefind('unused', 'unused', 'en')
        rows = self.persisted(reviewed=True)
        self.assertIn('dependency missing', json.dumps(rows))
        self.assertNotIn('dependency missing', self.out.getvalue())
        self.assertEqual(rows[0]['code'], 9)

    def test_missing_empty_and_redacted_fields_are_distinct(self):
        for raw in (None, b'', b'{"error":{"message":"denied","input":"sensitive"}}'):
            self.store.capture('fetch', raw=raw)
        rows = self.persisted(reviewed=True)
        self.assertEqual([r['explanation']['state'] for r in rows], ['unavailable', 'missing', 'received'])
        self.assertIn('redacted_fields', json.dumps(rows[-1]))

    def test_export_is_redacted_exclusive_and_refuses_public_tree(self):
        self.store.capture('fetch', raw=('provider explanation; Bearer ' + SECRET).encode())
        target = self.root / 'support' / 'bundle.json'
        self.store.export(target, reviewed=True)
        text = target.read_text()
        self.assertIn('provider explanation', text)
        self.assertNotIn(SECRET, text)
        with self.assertRaisesRegex(RuntimeError, '^support_export_write_failed$'):
            self.store.export(target)
        with self.assertRaises(ValueError):
            self.store.export(self.root / 'public' / 'leak.json')
        with self.assertRaises(ValueError):
            EvidenceStore(self.root / 'public', '123abc', public_root=self.root / 'public')
        if os.name != 'nt':
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_logger_failure_never_masks_provider_error_or_leaks_secret(self):
        error = urllib.error.URLError('token=' + SECRET)
        with patch.object(builder.urllib.request, 'urlopen', side_effect=error), \
                patch('safe_evidence.os.replace', side_effect=PermissionError(SECRET)), contextlib.redirect_stdout(self.out):
            with self.assertRaises(urllib.error.URLError) as caught:
                builder.fetch_json('https://example.invalid', tries=1)
        self.assertIs(caught.exception, error)
        self.assertTrue(self.store.write_failed)
        self.assertIn('failure_evidence_write_failed', self.out.getvalue())
        self.assertNotIn(SECRET, self.out.getvalue())
        self.assertFalse(list(self.store.directory.glob('*')))

    def test_body_read_failure_preserves_original_http_error_and_safe_cause(self):
        error = urllib.error.HTTPError('https://example.invalid', 502, 'upstream gateway failed', {}, io.BytesIO())
        self.addCleanup(error.close)
        with patch.object(error, 'read', side_effect=TimeoutError('body stream timeout; token=' + SECRET)), \
                patch.object(builder.urllib.request, 'urlopen', side_effect=error), contextlib.redirect_stdout(self.out):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                builder.fetch_json('https://example.invalid', tries=1)
        self.assertIs(caught.exception, error)
        rows = self.persisted(reviewed=True)
        self.assertEqual(rows[0]['component'], 'blogger_error_body_read')
        self.assertIn('body stream timeout', json.dumps(rows))
        self.assertEqual(rows[-1]['explanation']['state'], 'unavailable')

    def test_schema_exception_values_are_redacted_not_logged_as_explanation(self):
        self.store.capture('records', error=KeyError(PAYLOAD), payload=True)
        rec = self.persisted(reviewed=True)[0]
        self.assertIn('values redacted', rec['causes'][0]['message'])

    def test_environment_secret_and_html_payload_canaries(self):
        with patch.dict(os.environ, {'ATR_TEST_TOKEN': SECRET}):
            store = EvidenceStore(self.root / 'private', '123abc', public_root=self.root / 'public')
        self.store = store
        body = ('<h1>Upstream denied request</h1><form><textarea>' + PAYLOAD + '</textarea></form>'
                '<p>Bearer ' + SECRET + '</p><p>operator@example.invalid C:\\Users\\private\\file</p>')
        store.capture('fetch', raw=body.encode(), status=403)
        rec = self.persisted(reviewed=True)[0]
        text = json.dumps(rec)
        self.assertIn('Upstream denied request', text)
        self.assertNotIn('operator@example.invalid', text)
        self.assertNotIn('C:\\\\Users', text)

    def test_nonjson_quoted_password_and_query_line_are_redacted(self):
        self.store.capture('fetch', raw=('Gateway unavailable\npassword="multi word private value"\nquery: ' + PAYLOAD).encode())
        text = json.dumps(self.persisted(reviewed=True))
        self.assertIn('Gateway unavailable', text)
        self.assertNotIn('multi word private value', text)

    def test_encoded_known_secrets_are_removed_from_retained_and_reviewed_export(self):
        variants = [base64.b64encode(SECRET.encode()).decode(), base64.urlsafe_b64encode(SECRET.encode()).decode().rstrip('='),
                    ''.join('%%%02X' % ord(c) for c in SECRET), ''.join('&#%d;' % ord(c) for c in SECRET),
                    ''.join('\\u%04x' % ord(c) for c in SECRET), SECRET.encode().hex()]
        self.store.capture('fetch', raw=json.dumps({'error': {'message': 'Upstream quota rejected credentials ' + ' '.join(variants)}}).encode())
        record = json.dumps(self.persisted(reviewed=True))
        self.assertIn('Upstream quota', record)
        destination = self.root / 'reviewed.json'
        self.store.export(destination, reviewed=True)
        for variant in variants:
            self.assertNotIn(variant, record + destination.read_text())

    def test_malformed_json_retains_ancestors_and_excludes_request_message(self):
        body = ('{"request":{"message":"' + PAYLOAD + '"},"error":{"message":"provider quota exhausted"},"padding":"').encode()
        self.store.capture('fetch', raw=body, truncated=True)
        rec = self.persisted(reviewed=True)[0]
        self.assertIn('provider quota exhausted', json.dumps(rec))
        self.assertTrue(rec['explanation']['truncated'])

    def test_declared_payload_echo_and_quoted_novel_prose_are_redacted(self):
        self.store.capture('fetch', raw=json.dumps({'error': {'message': 'We received ' + PAYLOAD + ' and rejected it because quota is exhausted'}, 'request': {'text': PAYLOAD}}).encode())
        self.store.capture('fetch', raw=('The submitted text was «' + PAYLOAD + '» because capacity is unavailable').encode())
        text = json.dumps(self.persisted(reviewed=True))
        self.assertIn('quota is exhausted', text)
        self.assertIn('capacity is unavailable', text)

    def test_default_export_withholds_all_parallel_unverified_prose(self):
        novel = 'unlabelled-private-personal-value'
        self.store.capture('fetch', error=RuntimeError('We received ' + novel), raw=('Rejected ' + novel).encode(),
                           headers={'WWW-Authenticate': 'Bearer error="invalid_token", error_description="' + novel + '"'})
        local = self.persisted()[0]
        self.assertTrue(local['review_required'])
        self.assertNotIn(novel, json.dumps(local))
        target = self.root / 'default-export.json'
        self.store.export(target)
        data = json.loads(target.read_text())
        self.assertNotIn(novel, target.read_text())
        self.assertNotIn('causes', data['records'][0])
        self.assertNotIn('authentication_challenge', data['records'][0])
        self.assertEqual(data['records'][0]['explanation']['state'], 'withheld')

    def test_unclassified_multilingual_prose_never_reaches_default_disk_or_export(self):
        novel = 'novel-personal-prose-value'
        for prose in ('We received ' + novel + ' yesterday', '\u6536\u5230\u7684\u6587\u672c\u662f' + novel,
                      '\u53d7\u4fe1\u3057\u305f\u6587\u7ae0' + novel, base64.b64encode(novel.encode()).decode()):
            self.store.capture(novel, error=RuntimeError(prose), raw=json.dumps({'message': prose}).encode(),
                               headers={'X-Request-ID': novel, 'WWW-Authenticate': 'Bearer error_description="'+prose+'"'})
        text = self.store.report_path.read_text()
        self.assertNotIn(novel, text)
        self.assertNotIn(base64.b64encode(novel.encode()).decode(), text)
        self.assertTrue(all(row['component']=='unclassified' for row in json.loads(text)['records']))
        self.assertTrue(any(novel in json.dumps(row) for row in self.store.records))
        target = self.root / 'default-prose.json'
        self.store.export(target)
        self.assertNotIn(novel, target.read_text())
        self.assertFalse(any(row['detail_persisted'] for row in json.loads(target.read_text())['records']))

    def test_reviewed_retention_is_explicit_and_provenance_tracks_failed_write(self):
        self.store.capture('blogger_fetch_parse', raw=b'Known safe synthetic quota explanation', status=429)
        initial = json.loads(self.store.report_path.read_text())['records'][0]
        self.assertFalse(initial['detail_persisted'])
        self.assertNotIn('quota explanation', json.dumps(initial))
        with patch('safe_evidence.os.replace', side_effect=PermissionError('synthetic write failure')):
            self.store.retain_reviewed(1)
        self.assertNotIn(1, self.store.persisted_detail_sequences)
        self.assertFalse(self.store.records[0]['detail_persisted'])
        self.store.retain_reviewed(1)
        saved = json.loads(self.store.report_path.read_text())['records'][0]
        self.assertTrue(saved['operator_reviewed'])
        self.assertTrue(saved['detail_persisted'])
        self.assertIn('quota explanation', json.dumps(saved))
        with self.assertRaises(ValueError): self.store.retain_reviewed(999)

    def test_reviewed_export_does_not_imply_full_details_were_retained(self):
        self.store.capture('blogger_fetch_parse', raw=b'safe synthetic provider explanation')
        target = self.root / 'reviewed-memory.json'
        self.store.export(target, reviewed=True)
        record = json.loads(target.read_text())['records'][0]
        self.assertTrue(record['persisted'])
        self.assertFalse(record['detail_persisted'])
        self.assertIn('provider explanation', json.dumps(record))

    def test_restart_preserves_prior_reviewed_report_and_explicit_recovery(self):
        self.store.capture('blogger_fetch_parse', raw=b'Safe synthetic quota reason survives restart', status=429)
        self.store.retain_reviewed(1)
        old_path = self.store.report_path
        old_bytes = old_path.read_bytes()
        restarted = EvidenceStore(self.root / 'private', '123abc', public_root=self.root / 'public')
        self.assertNotEqual(restarted.report_path, old_path)
        self.assertEqual(restarted.records, [])
        restarted.capture('blogger_fetch_parse', raw=b'new unreviewed detail', status=503)
        self.assertEqual(old_path.read_bytes(), old_bytes)
        recovered = EvidenceStore.read_retained(old_path, public_root=self.root / 'public')
        self.assertIn('quota reason survives restart', json.dumps(recovered))
        self.assertTrue(recovered['records'][0]['detail_persisted'])
        self.assertEqual(restarted.records[0]['record_sequence'], 1)
        self.assertEqual(len(list(self.store.directory.glob('*.json'))), 2)

    def test_unreviewed_detail_not_recoverable_after_restart_is_explicit(self):
        novel = 'synthetic-unreviewed-detail-lost-at-exit'
        self.store.capture('blogger_fetch_parse', raw=novel.encode())
        report = EvidenceStore.read_retained(self.store.report_path, public_root=self.root / 'public')
        restarted = EvidenceStore(self.root / 'private', '123abc', public_root=self.root / 'public')
        self.assertNotIn(novel, json.dumps(report))
        self.assertFalse(report['records'][0]['detail_persisted'])
        self.assertEqual(restarted.records, [])
        self.assertEqual(report['records'][0]['explanation']['state'], 'withheld')

    def test_recovery_rejects_oversize_bad_schema_and_public_location(self):
        target = self.root / 'report.json'
        target.write_bytes(b'x' * (FILE_LIMIT + 1))
        with self.assertRaisesRegex(ValueError, '^retained report unreadable or invalid$'):
            EvidenceStore.read_retained(target)
        target.write_text('{"schema":1,"records":[]}')
        with self.assertRaises(ValueError): EvidenceStore.read_retained(target)
        public = self.root / 'public'
        public.mkdir()
        target = public / 'report.json'
        target.write_text('{"schema":2,"records":[]}')
        with self.assertRaises(ValueError): EvidenceStore.read_retained(target, public_root=public)

    def test_partial_export_is_removed_without_removing_preexisting_destination(self):
        self.store.capture('fetch', raw=b'provider reason')
        target = self.root / 'partial.json'
        original = os.fdopen
        first = True
        class PartialWriter:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def write(self, data):
                self.stream.write(data[:30])
                self.stream.flush()
                raise PermissionError('synthetic write failure')
            def __exit__(self, *args): self.stream.close()
        def fdopen(fd, mode):
            nonlocal first
            stream = original(fd, mode)
            if first:
                first = False
                return PartialWriter(stream)
            return stream
        with patch('safe_evidence.os.fdopen', side_effect=fdopen):
            with self.assertRaises(RuntimeError): self.store.export(target)
        self.assertFalse(target.exists())
        target.write_text('preserve existing')
        with self.assertRaises(RuntimeError): self.store.export(target)
        self.assertEqual(target.read_text(), 'preserve existing')

    def test_retention_uses_final_encoded_size_and_counts_eviction(self):
        node = [dict.fromkeys(sorted(SAFE_KEYS)[:22], 'x') for _ in range(32)]
        for _ in range(4): node = {'details': node}
        body = json.dumps(node).encode()
        for _ in range(5):
            self.store.capture('fetch', raw=body)
            self.store.retain_reviewed(self.store.records[-1]['record_sequence'])
        self.assertFalse(self.store.write_failed)
        payload = json.loads(self.store.report_path.read_text())
        self.assertGreater(payload['dropped_records'], 0)
        self.assertLess(len(payload['records']), 5)
        self.assertEqual(payload['records'][-1]['record_sequence'], 5)
        self.assertLessEqual(self.store.report_path.stat().st_size, FILE_LIMIT)

    def test_export_marks_memory_record_not_successfully_persisted(self):
        self.store.capture('fetch', raw=b'first')
        with patch('safe_evidence.os.replace', side_effect=PermissionError('synthetic logger failure')):
            self.store.capture('fetch', raw=b'second')
        target = self.root / 'memory.json'
        self.store.export(target)
        payload = json.loads(target.read_text())
        self.assertEqual([r['persisted'] for r in payload['records']], [True, False])
        self.assertTrue(payload['persistence']['write_failed'])

    def test_export_write_failure_is_fixed_error_and_retains_safe_cause(self):
        self.store.capture('fetch', raw=b'upstream unavailable')
        with patch('safe_evidence.os.open', side_effect=PermissionError(13, 'access denied ' + SECRET)):
            with self.assertRaisesRegex(RuntimeError, '^support_export_write_failed$'):
                self.store.export(self.root / 'support.json')
        # The capture attempted while os.open is broken is marked, not falsely claimed persisted.
        self.assertTrue(self.store.write_failed)
        self.assertNotIn(SECRET, next(self.store.directory.glob('*.json')).read_text())

    def test_retention_and_secret_at_truncation_boundary(self):
        for i in range(9):
            target = self.store.directory
            target.mkdir(parents=True, exist_ok=True)
            old = target / (str(i + 100) + '.json')
            old.write_text('{}')
            os.utime(old, (time.time() - 8 * 86400, time.time() - 8 * 86400))
        self.store.capture('fetch', raw=('error token at end: ' + SECRET[:12]).encode(), truncated=True)
        self.persisted(reviewed=True)
        self.assertNotIn(SECRET[:12], next(self.store.directory.glob('*.json')).read_text())
        self.assertEqual(len(list(self.store.directory.glob('*.json'))), 1)

    def test_fresh_file_count_and_nested_truncation_are_explicit(self):
        self.store.directory.mkdir(parents=True)
        for i in range(10):
            (self.store.directory / (str(i + 200) + '.json')).write_text('{}')
        self.store.capture('fetch', raw=json.dumps({'errors': [{'message': 'safe detail'}] * 40}).encode())
        self.assertLessEqual(len(list(self.store.directory.glob('*.json'))), 7)
        self.store.retain_reviewed(1)
        data = json.loads(self.store.report_path.read_text())
        self.assertTrue(data['records'][0]['explanation']['truncated'])

    def test_real_synthetic_subprocess_is_bounded_and_timeout_explicit(self):
        result = run_bounded([sys.executable, '-c', "import sys;sys.stderr.write('x'*50000);sys.stdout.write('y'*50000)"])
        self.assertEqual(len(result.stderr), BODY_LIMIT)
        self.assertEqual(result.output_bytes, [50000, 50000])
        self.assertEqual(result.output_truncated, [True, True])
        timeout = run_bounded([sys.executable, '-c', 'import time;time.sleep(5)'], timeout=0.05)
        self.assertTrue(timeout.timed_out)


if __name__ == '__main__':
    unittest.main()
