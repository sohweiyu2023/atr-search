"""Bounded, redacted, local-only upstream failure evidence. Never emit bodies to stdout."""
import datetime as dt
import html
import json
import os
from pathlib import Path
import platform
import re
import tempfile
import time
import subprocess
import threading
import base64
import uuid
from urllib.parse import urlsplit, unquote, quote

BODY_LIMIT = 16384
FILE_LIMIT = 131072
MAX_FILES = 7
MAX_AGE_SECONDS = 7 * 86400
SAFE_KEYS = set(('error errors message description error_description reason code status type domain '
                 'details cause causes hint help retry_after request_id correlation_id trace_id '
                 'quota limit limit_value current maximum retryable innererror inner_error metadata '
                 'violations localizedmessage error_code status_code error_type failure '
                 'quota_metric quota_id quota_limit quota_limit_value links').split())
ID_HEADERS = ('x-request-id', 'x-correlation-id', 'x-goog-request-id', 'x-amzn-requestid', 'cf-ray', 'traceparent', 'retry-after')
COMPONENTS = {'blogger_fetch_parse', 'blogger_error_body_read', 'blogger_error_close', 'pagefind_launcher',
              'pagefind_stderr', 'pagefind_stdout', 'pagefind_warning', 'support_export'} | {
                  'build_' + phase for phase in ('startup', 'fetch', 'records', 'index', 'validation', 'status', 'publish', 'complete')}


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class Redactor:
    def __init__(self, secrets=()):
        env = [v for k, v in os.environ.items() if re.search(r'(TOKEN|SECRET|PASSWORD|API_KEY|AUTHORIZATION|COOKIE)', k, re.I) and len(v) >= 4]
        self.secrets = []
        self.add_sensitive(env + list(secrets))

    def add_sensitive(self, values):
        variants = set(self.secrets)
        for value in values:
            if not isinstance(value, str) or len(value) < 4:
                continue
            raw = value.encode('utf-8')
            variants.update((value, quote(value, safe=''), html.escape(value),
                             raw.hex(), raw.hex().upper(),
                             base64.b64encode(raw).decode(), base64.urlsafe_b64encode(raw).decode()))
            variants.update(v.rstrip('=') for v in tuple(variants) if v.endswith('='))
        self.secrets = sorted(variants, key=len, reverse=True)

    def collect_sensitive(self, value, private=False, depth=0):
        if depth > 12:
            return
        if isinstance(value, dict):
            for key, item in list(value.items())[:64]:
                self.collect_sensitive(item, private or key.lower() not in SAFE_KEYS, depth + 1)
        elif isinstance(value, list):
            for item in value[:32]:
                self.collect_sensitive(item, private, depth + 1)
        elif private and isinstance(value, str):
            self.add_sensitive([value])

    def text(self, value):
        value = str(value)
        # Normalize common nested encodings before applying field/prose filters.
        for _ in range(3):
            normalized = html.unescape(unquote(value))
            normalized = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), normalized)
            if normalized == value:
                break
            value = normalized
        for secret in self.secrets:
            if not secret:
                continue
            value = value.replace(secret, '[redacted]')
            # A bounded read can cut a known secret at the end of the retained prefix.
            position = value.rfind(secret[:8]) if len(secret) >= 8 else -1
            if position >= 0 and secret.startswith(value[position:]):
                value = value[:position] + '[redacted-partial]'
        value = re.sub(r'(?i)((?:submitted|provided|received|original|user)\s+(?:text|query|prompt|payload|content|input|body)\s+(?:was|is|contained|equals?)\s*)(.*?)(?=\s+(?:because|due to|but)\b|$)', r'\1[redacted-echo]', value)
        if re.search(r'(?i)\b(submitted|provided|received|process|rejected|input|query|prompt|payload|content)\b', value):
            value = re.sub(r'''[«“`"']([^»”`"'\n]+)[»”`"']''', '[redacted-echo]', value)
        value = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)', '[redacted-key]', value, flags=re.S)
        value = re.sub(r'(?i)\bBearer\s+[^\s,;<>]+', 'Bearer [redacted]', value)
        value = re.sub(r'''(?ix)\b(?:authorization|cookie|set-cookie|password|passwd|token|api[_-]?key|secret)\b["']?\s*[=:]\s*(?:"[^"]*(?:"|$)|'[^']*(?:'|$)|[^\s,;<>"']+)''', '[redacted-credential]', value)
        value = re.sub(r'(?im)\b(?:query|prompt|input|payload|request[_ -]?body|user[_ -]?content)["\x27]?\s*[:=][^\r\n]*', '[redacted-payload]', value)
        value = re.sub(r'\b(?:gh[pousr]_|github_pat_|sk-(?:proj-)?)[A-Za-z0-9_-]{8,}', '[redacted-token]', value)
        value = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]*)?', '[redacted-jwt]', value)
        value = re.sub(r'https?://[^\s<>"\x27]+', '[redacted-url]', value)
        value = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[redacted-email]', value)
        value = re.sub(r'(?:[A-Za-z]:\\|/(?:home|Users|tmp|var|workspace)/)[^\s<>"\x27]*', '[redacted-path]', value)
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value)

    def structured(self, value, depth=0):
        if depth > 8:
            return '[truncated-depth]'
        if isinstance(value, dict):
            result = {}
            omitted = 0
            for key, item in list(value.items())[:64]:
                if key.lower() in SAFE_KEYS:
                    result[key.lower()] = self.structured(item, depth + 1)
                else:
                    omitted += 1
            if omitted:
                result['redacted_fields'] = omitted
            if len(value) > 64:
                result['truncated_fields'] = len(value) - 64
            return result
        if isinstance(value, list):
            result = [self.structured(v, depth + 1) for v in value[:32]]
            if len(value) > 32:
                result.append('[truncated-items]')
            return result
        if isinstance(value, str):
            return self.text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return '[unavailable-type]'


def endpoint(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            return 'unavailable'
        path = parsed.path if parsed.path == '/feeds/posts/full' else '/[path-redacted]'
        host = parsed.hostname if parsed.hostname in {'www.awakeningtoreality.com', 'awakeningtoreality.com'} else '[host-redacted]'
        return parsed.scheme + '://' + host + path
    except ValueError:
        return 'unavailable'


def partial_json(text):
    """Recover only complete values with their ancestors, never regex-match leaf names."""
    decoder = json.JSONDecoder()
    def parse(position, depth=0):
        while position < len(text) and text[position].isspace():
            position += 1
        if depth > 12 or position >= len(text):
            raise ValueError('prefix-ended')
        if text[position] not in '{[':
            return decoder.raw_decode(text, position)
        opening = text[position]
        result = {} if opening == '{' else []
        position += 1
        for _ in range(64):
            while position < len(text) and text[position].isspace():
                position += 1
            if position >= len(text):
                return result, position
            if text[position] == ('}' if opening == '{' else ']'):
                return result, position + 1
            try:
                if opening == '{':
                    key, position = decoder.raw_decode(text, position)
                    if not isinstance(key, str):
                        return result, len(text)
                    while position < len(text) and text[position].isspace():
                        position += 1
                    if position >= len(text) or text[position] != ':':
                        return result, len(text)
                    item, position = parse(position + 1, depth + 1)
                    result[key] = item
                else:
                    item, position = parse(position, depth + 1)
                    result.append(item)
            except (ValueError, RecursionError):
                return result, len(text)
            while position < len(text) and text[position].isspace():
                position += 1
            if position < len(text) and text[position] == ',':
                position += 1
            elif position < len(text) and text[position] == ('}' if opening == '{' else ']'):
                return result, position + 1
            else:
                return result, position
        return result, len(text)
    return parse(0)[0]


def explanation(raw, redactor, *, truncated=False, payload=False):
    if payload:
        return {'state': 'redacted', 'reason': 'successful-response-payload', 'truncated': truncated}
    if raw is None:
        return {'state': 'unavailable', 'reason': 'no-response-body-received', 'truncated': False}
    if not raw:
        return {'state': 'missing', 'reason': 'empty-body', 'truncated': truncated}
    text = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else str(raw)
    try:
        parsed = json.loads(text)
        redactor.collect_sensitive(parsed)
        detail = redactor.structured(parsed)
        kind = 'json'
    except (ValueError, RecursionError):
        if text.lstrip().startswith(('{', '[')):
            # A cut/malformed JSON document must not bypass field-level payload redaction.
            try:
                parsed = partial_json(text)
                redactor.collect_sensitive(parsed)
                detail = redactor.structured(parsed)
            except (ValueError, RecursionError):
                detail = {'state': 'unavailable'}
            return {'state': 'truncated' if truncated else 'received', 'format': 'incomplete-json',
                    'detail': detail, 'redacted': True, 'truncated': truncated, 'review_required': True,
                    'reason': 'complete-prefix-values-with-ancestors-only'}
        # Remove active/hidden markup; preserve visible upstream error-page explanation.
        text = re.sub(r'<(script|style|template|textarea|form)\b[^>]*>.*?</\1>', '', text, flags=re.I | re.S)
        text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
        detail = redactor.text(text)
        kind = 'text'
    serialized = json.dumps(detail, ensure_ascii=True)
    if len(serialized) > BODY_LIMIT:
        # Do not serialize cut JSON or arbitrary partial strings.
        return {'state': 'truncated', 'reason': 'redacted-explanation-exceeds-limit', 'format': kind,
                'prefix': redactor.text(serialized[:BODY_LIMIT // 2]), 'truncated': True}
    nested_truncated = '[truncated-' in serialized or 'truncated_fields' in serialized
    return {'state': 'received', 'format': kind, 'detail': detail, 'truncated': truncated or nested_truncated, 'review_required': True,
            'redacted': '[redacted' in serialized or 'redacted_fields' in serialized,
            'limits': {'bytes': BODY_LIMIT, 'depth': 8, 'items': 32, 'fields': 64}}


class EvidenceStore:
    def __init__(self, path, build_id, *, public_root='public', secrets=()):
        self.directory = Path(path).resolve() / 'evidence'
        self.public_root = Path(public_root).resolve()
        if self.directory.is_relative_to(self.public_root):
            raise ValueError('evidence must remain outside published output')
        self.build_id = build_id if re.fullmatch(r'[a-f0-9]{1,40}', build_id) else 'local'
        self.instance_id = uuid.uuid4().hex
        self.report_path = self.directory / (self.build_id + '-' + self.instance_id + '.json')
        self.redactor = Redactor(secrets)
        self.records = []
        self.write_failed = False
        self.dropped_records = 0
        self.next_sequence = 1
        self.persisted_sequences = set()
        self.persisted_detail_sequences = set()

    def capture(self, component, *, error=None, url=None, status=None, headers=None,
                raw=None, truncated=False, payload=False, seconds=None, code=None):
        try:
            safe_explanation = explanation(raw, self.redactor, truncated=truncated, payload=payload)
            causes = []
            seen = set()
            current = error
            while current is not None and id(current) not in seen and len(causes) < 5:
                seen.add(id(current))
                if isinstance(current, KeyboardInterrupt):
                    message = 'execution interrupted'
                elif payload and isinstance(current, (ValueError, TypeError, KeyError, UnicodeError)):
                    message = ('JSON syntax invalid at line %d column %d' % (current.lineno, current.colno)
                               if isinstance(current, json.JSONDecodeError) else 'payload parse/schema validation failed; values redacted')
                else:
                    message = self.redactor.text(str(current))
                causes.append({'type': type(current).__name__, 'message': message[:BODY_LIMIT],
                               'message_truncated': len(message) > BODY_LIMIT,
                               'review_required': True,
                               'errno': getattr(current, 'errno', None) if isinstance(getattr(current, 'errno', None), int) else None})
                current = current.__cause__ or getattr(current, 'reason', None) or (None if current.__suppress_context__ else current.__context__)
                if not isinstance(current, BaseException):
                    if current is not None:
                        message = self.redactor.text(current)
                        causes.append({'type': 'provider_reason', 'message': message[:BODY_LIMIT], 'message_truncated': len(message) > BODY_LIMIT})
                    break
            ids = {}
            challenge = {'state': 'missing' if headers is not None else 'unavailable'}
            if headers:
                headers = {str(k).lower(): v for k, v in headers.items()}
                for key in ID_HEADERS:
                    value = headers.get(key)
                    if value is not None:
                        value = self.redactor.text(value)
                        ids[key] = value[:256] if re.fullmatch(r'[A-Za-z0-9._:/-]{1,256}', value) else '[redacted-invalid-id]'
                challenge = {'state': 'missing'}
                if 'www-authenticate' in headers:
                    value = headers['www-authenticate']
                    challenge = {'state': 'received', 'scheme': 'Bearer' if str(value).lower().startswith('bearer') else 'other'}
                    for name in ('error', 'error_description'):
                        match = re.search(r'\b' + name + r'="([^"]*)"', str(value))
                        if match:
                            challenge[name] = self.redactor.text(match.group(1))[:1024]
            record = {'utc': utc(), 'record_sequence': self.next_sequence, 'build_id': self.build_id, 'component': self.redactor.text(component)[:128],
                      'component_version': 'safe-evidence/1', 'python_version': platform.python_version(),
                      'pagefind_version': '1.5.2', 'node_version': 'unavailable-not-queried',
                      'endpoint': endpoint(url) if url else 'local',
                      'status': status if isinstance(status, int) else 'unavailable',
                      'code': code if isinstance(code, int) else 'unavailable',
                      'duration_seconds': round(seconds, 3) if isinstance(seconds, (int, float)) else 'unavailable',
                      'correlation_ids': ids or {'state': 'missing' if headers is not None else 'unavailable'},
                      'authentication_challenge': challenge,
                      'causes': causes or [{'state': 'unavailable'}],
                      'cause_chain_truncated': current is not None and len(causes) >= 5,
                      'explanation': safe_explanation, 'review_required': True, 'detail_persisted': False}
            self.records.append(record)
            self.next_sequence += 1
            self.dropped_records += max(0, len(self.records) - 16)
            self.records = self.records[-16:]
            self._persist()
        except Exception:
            self.write_failed = True  # Never mask the operation or emit raw failure data.

    def _persist(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise OSError('unsafe evidence directory')
        payload = {'schema': 2, 'retention': {'days': 7, 'files': MAX_FILES, 'bytes_per_file': FILE_LIMIT}}
        while True:
            payload['records'] = [dict(row, detail_persisted=True) if row.get('operator_reviewed') else self._metadata(row) for row in self.records]
            payload['dropped_records'] = self.dropped_records
            data = json.dumps(payload, ensure_ascii=True, indent=2).encode()
            if len(data) <= FILE_LIMIT:
                break
            if len(self.records) <= 1:
                row = self.records[0]
                row['explanation'] = {'state': 'truncated', 'reason': 'record-byte-limit', 'review_required': True}
                for cause in row['causes']:
                    if 'message' in cause:
                        cause['message'] = cause['message'][:512]
                        cause['message_truncated'] = True
                row['record_details_truncated'] = True
                if len(json.dumps(payload, ensure_ascii=True, indent=2).encode()) > FILE_LIMIT:
                    raise ValueError('minimal evidence size limit')
                continue
            self.records.pop(0)
            self.dropped_records += 1
        fd, name = tempfile.mkstemp(dir=self.directory, prefix='.pending-')
        try:
            with os.fdopen(fd, 'wb') as target:
                target.write(data)
            os.replace(name, self.report_path)
            self.persisted_sequences = {row['record_sequence'] for row in self.records}
            self.persisted_detail_sequences = {row['record_sequence'] for row in self.records if row.get('operator_reviewed')}
            for row in self.records:
                row['detail_persisted'] = row['record_sequence'] in self.persisted_detail_sequences
        finally:
            Path(name).unlink(missing_ok=True)
        files = sorted((p for p in self.directory.glob('*.json') if re.fullmatch(r'(?:[a-f0-9]{1,40}|local)(?:-[a-f0-9]{32})?\.json', p.name) and not p.is_symlink()), key=lambda p: p.stat().st_mtime, reverse=True)
        for index, path in enumerate(files):
            if index >= MAX_FILES or time.time() - path.stat().st_mtime > MAX_AGE_SECONDS:
                path.unlink()

    @staticmethod
    def read_retained(path, *, public_root='public'):
        """Bounded read for local operator inspection; no restore, export, execution or trust promotion."""
        path = Path(path)
        if path.is_symlink() or path.resolve().is_relative_to(Path(public_root).resolve()):
            raise ValueError('retained report location rejected')
        try:
            with path.open('rb') as source:
                raw = source.read(FILE_LIMIT + 1)
            if len(raw) > FILE_LIMIT:
                raise ValueError('report too large')
            report = json.loads(raw.decode('utf-8'))
            if (not isinstance(report, dict) or report.get('schema') != 2 or
                    not isinstance(report.get('records'), list) or len(report['records']) > 16 or
                    not all(isinstance(row, dict) for row in report['records'])):
                raise ValueError('invalid report structure')
            return report
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise ValueError('retained report unreadable or invalid') from None

    @staticmethod
    def _metadata(row):
        """No provider text, IDs, custom exception names or arbitrary component strings."""
        result = {k: row[k] for k in ('utc', 'record_sequence', 'build_id', 'component_version', 'python_version',
                                     'pagefind_version', 'node_version', 'status', 'code', 'duration_seconds')}
        result['component'] = row['component'] if row['component'] in COMPONENTS else 'unclassified'
        result.update(review_required=True, detail_persisted=False,
                      explanation={'state': 'withheld', 'reason': 'provider-detail-not-included',
                                   'persistence_policy': 'explicit-operator-review-required'})
        return result

    def retain_reviewed(self, sequence):
        """Explicit operator attestation; never invoked by unattended builder capture."""
        row = next((row for row in self.records if row['record_sequence'] == sequence), None)
        if row is None:
            raise ValueError('record unavailable for review')
        row['operator_reviewed'] = True
        row['review_required'] = False
        try:
            self._persist()
        except Exception:
            self.write_failed = True

    def export(self, destination, *, reviewed=False):
        """Default export excludes provider-controlled prose; reviewed=True requires operator review."""
        destination = Path(destination).resolve()
        if destination.is_relative_to(self.public_root):
            raise ValueError('support export cannot enter published output')
        records = [dict(row) if reviewed else self._metadata(row) for row in self.records]
        for row in records:
            row['persisted'] = row['record_sequence'] in self.persisted_sequences
            row['detail_persisted'] = row['record_sequence'] in self.persisted_detail_sequences
        data = json.dumps({'schema': 2, 'operator_reviewed': reviewed,
                           'persistence': {'write_failed': self.write_failed, 'retained_sequences': sorted(self.persisted_sequences),
                                           'retained_detail_sequences': sorted(self.persisted_detail_sequences)},
                           'records': records}, ensure_ascii=True)
        # Re-scrub strings individually, preserving schema and numeric fields.
        def scrub(value):
            if isinstance(value, str):
                return self.redactor.text(value)
            if isinstance(value, list):
                return [scrub(v) for v in value]
            if isinstance(value, dict):
                return {k: scrub(v) for k, v in value.items()}
            return value
        data = json.dumps(scrub(json.loads(data)), ensure_ascii=True).encode()
        if len(data) > FILE_LIMIT:
            raise ValueError('support export exceeds limit')
        created_identity = None
        try:
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            info = os.fstat(fd)
            created_identity = (info.st_dev, info.st_ino)
            with os.fdopen(fd, 'wb') as target:
                target.write(data)
        except OSError as error:
            if created_identity is not None:
                try:
                    info = destination.stat(follow_symlinks=False)
                    if (info.st_dev, info.st_ino) == created_identity:
                        destination.unlink()
                except OSError:
                    pass
            self.capture('support_export', error=error, code=error.errno)
            raise RuntimeError('support_export_write_failed') from None


def run_bounded(cmd, timeout=None):
    """Drain both pipes without deadlock; retain only bounded in-memory prefixes."""
    chunks = [bytearray(), bytearray()]
    counts = [0, 0]
    read_errors = [False, False]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        def drain(pipe, index):
            try:
                while True:
                    data = pipe.read(4096)
                    if not data:
                        break
                    counts[index] += len(data)
                    remaining = max(0, BODY_LIMIT - len(chunks[index]))
                    chunks[index].extend(data[:remaining])
            except OSError:
                read_errors[index] = True
        threads = [threading.Thread(target=drain, args=(pipe, i), daemon=True)
                   for i, pipe in enumerate((process.stdout, process.stderr))]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        for thread in threads:
            thread.join(timeout=2 if timed_out else None)
        result = subprocess.CompletedProcess(cmd, process.returncode, bytes(chunks[0]), bytes(chunks[1]))
        result.output_bytes = counts
        result.output_truncated = [counts[i] > len(chunks[i]) for i in range(2)]
        result.output_unavailable = [read_errors[i] or threads[i].is_alive() for i in range(2)]
        result.timed_out = timed_out
        return result
