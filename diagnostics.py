"""Aggregate build diagnostics; never serialize URLs, content or exception messages."""
import json
import os
from pathlib import Path
import re
import time
import uuid


class BuildFailure(RuntimeError):
    REASONS = {'insufficient_records', 'index_process_failed', 'index_manifest_mismatch',
               'index_assets_missing', 'index_validation_failed'}

    def __init__(self, reason):
        self.reason = reason if reason in self.REASONS else 'build_validation_failed'
        super().__init__(self.reason)


class Diagnostics:
    def __init__(self, path):
        self.path = Path(path)
        self.started = time.monotonic()
        self.stage = 'startup'
        self.events = []
        self.warnings = 0
        self.dropped_events = 0
        self.console_write_errors = 0
        run = os.environ.get('GITHUB_RUN_ID', '')
        attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '')
        sha = os.environ.get('GITHUB_SHA', '')
        self.context = {
            'build_id': run if re.fullmatch(r'[0-9]{1,24}', run) else uuid.uuid4().hex,
            'attempt': int(attempt) if re.fullmatch(r'[0-9]{1,6}', attempt) else 1,
            'job': os.environ.get('GITHUB_JOB') if os.environ.get('GITHUB_JOB') in {'build', 'offline-index'} else 'local',
        }
        if re.fullmatch(r'[0-9a-f]{40}', sha):
            self.context['commit'] = sha

    def emit(self, event, level='info', **counts):
        # Call sites supply fixed event/field names and numeric aggregates only.
        if any(not isinstance(v, (int, float, bool)) for v in counts.values()):
            raise TypeError('diagnostic fields must be numeric aggregates')
        row = dict(self.context, event=event, level=level, stage=self.stage,
                   elapsed_seconds=round(time.monotonic() - self.started, 3), **counts)
        if level == 'warning':
            self.warnings += 1
        if len(self.events) < 1000:
            self.events.append(row)
        else:
            self.dropped_events += 1
        try:
            print('[atr-search] ' + json.dumps(row, sort_keys=True), flush=True)
        except (OSError, ValueError):
            # A closed/broken log sink must not destroy the local diagnostic report.
            self.console_write_errors += 1

    def finish(self, outcome):
        level = {'failed': 'error', 'cancelled': 'warning'}.get(outcome, 'info')
        self.emit('build_' + outcome, level=level, warnings=self.warnings)
        payload = dict(self.context, outcome=outcome, stage=self.stage,
                       seconds=round(time.monotonic() - self.started, 3),
                       warnings=self.warnings, dropped_events=self.dropped_events,
                       console_write_errors=self.console_write_errors,
                       events=self.events)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + '.tmp')
            temporary.write_text(json.dumps(payload, indent=2), encoding='utf-8')
            temporary.replace(self.path)
        except OSError:
            # Console remains available if a local diagnostic file cannot be written.
            self.emit('diagnostic_write_failed', level='warning')


def validate_index(out, language, expected):
    """Check the generated manifest and referenced search assets before publishing."""
    out = Path(out)
    try:
        manifest = json.loads((out / 'pagefind-entry.json').read_text(encoding='utf-8'))
        info = manifest['languages'][language]
        index_hash = info['hash']
        valid_hash = isinstance(index_hash, str) and re.fullmatch(r'[A-Za-z0-9_-]+', index_hash)
        if not valid_hash or info['page_count'] != expected:
            raise BuildFailure('index_manifest_mismatch')
        # Pagefind uses the generic tokenizer when stemming is unavailable (e.g. zh/ja).
        wasm = info['wasm'] if info['wasm'] is not None else 'unknown'
        if not isinstance(wasm, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', wasm):
            raise BuildFailure('index_manifest_mismatch')
        required = [out / 'pagefind.js', out / ('pagefind.' + index_hash + '.pf_meta'),
                    out / ('wasm.' + wasm + '.pagefind')]
        fragments = list((out / 'fragment').glob('*.pf_fragment'))
        indexes = list((out / 'index').glob('*.pf_index'))
        if not fragments or not indexes or any(not p.is_file() or not p.stat().st_size for p in required + fragments + indexes):
            raise BuildFailure('index_assets_missing')
        return {'pages': expected, 'fragments': len(fragments), 'index_chunks': len(indexes),
                'bytes': sum(p.stat().st_size for p in out.rglob('*') if p.is_file())}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise BuildFailure('index_validation_failed') from error
