"""Stage a complete index before replacing output; preserve the previous local version."""
from contextlib import contextmanager
import datetime as dt
import os
from pathlib import Path
import shutil
import tempfile
import uuid
import json
from diagnostics import BuildFailure

MARKERS = {'output': '.atr-search-output.json', 'work': '.atr-search-work.json'}


class PublishInterrupted(KeyboardInterrupt):
    """The process was interrupted after its complete new output was observed."""


def mark_owned(path, kind, generation=None):
    payload = {'owner': 'atr-search', 'format': 1, 'kind': kind}
    if generation:
        payload['generation'] = generation
    (Path(path) / MARKERS[kind]).write_text(json.dumps(payload), encoding='utf-8')


def owned(path, kind, generation=None):
    marker = Path(path) / MARKERS[kind]
    try:
        if marker.is_symlink() or marker.stat().st_size > 2048:
            return False
        value = json.loads(marker.read_text(encoding='utf-8'))
        return (value.get('owner') == 'atr-search' and value.get('format') == 1 and
                value.get('kind') == kind and (generation is None or value.get('generation') == generation))
    except (OSError, ValueError, AttributeError):
        return False


def validate_paths(out, work, diagnostics):
    final = Path(out).absolute()
    raw_work = Path(work).absolute()
    working = raw_work.resolve()
    diagnostic = Path(diagnostics).resolve()
    evidence = (Path(diagnostics).absolute().parent / 'evidence').resolve()
    current = Path.cwd().resolve()
    for target, kind in ((final, 'output'), (raw_work, 'work')):
        resolved = target.resolve()
        if (target.is_symlink() or getattr(target, 'is_junction', lambda: False)() or
                (target.exists() and not target.is_dir()) or resolved == Path.home().resolve() or resolved == resolved.parent or
                current.is_relative_to(resolved) or '.git' in {part.lower() for part in resolved.parts} or
                resolved.is_relative_to(evidence) or evidence.is_relative_to(resolved)):
            raise BuildFailure('unsafe_output_paths')
        try:
            if target.exists() and next(target.iterdir(), None) is not None and not owned(target, kind):
                raise BuildFailure('unowned_generated_paths')
        except OSError as error:
            raise BuildFailure('unsafe_output_paths') from error
    resolved = final.resolve()
    if (working.is_relative_to(resolved) or resolved.is_relative_to(working) or
            diagnostic.is_relative_to(resolved) or diagnostic.is_relative_to(working)):
        raise BuildFailure('unsafe_output_paths')
    if not final.exists() and final.parent.exists():
        prefix = '.atr-search-output-backup-' + final.name + '-'
        if any(path.name.startswith(prefix) and path.is_dir() and owned(path, 'output') for path in final.parent.iterdir()):
            raise BuildFailure('output_recovery_required')
    return final


@contextmanager
def staged_output(out, work, diagnostics, warn=lambda: None):
    final = validate_paths(out, work, diagnostics)
    final.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.atr-search-stage-', dir=final.parent))
    generation = uuid.uuid4().hex
    backup = None
    try:
        mark_owned(stage, 'output', generation)
        yield stage
        try:
            if final.exists():
                stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                backup = final.with_name('.atr-search-output-backup-' + final.name + '-' + stamp + '-' + uuid.uuid4().hex[:8])
                os.replace(final, backup)
            os.replace(stage, final)
        except BaseException as error:
            if not stage.exists() and final.is_dir() and owned(final, 'output', generation):
                if isinstance(error, KeyboardInterrupt):
                    raise PublishInterrupted() from error
                raise BuildFailure('output_committed_after_error') from error
            if backup is not None and backup.exists() and not final.exists():
                try:
                    os.replace(backup, final)
                except OSError:
                    raise BuildFailure('output_rollback_failed') from error
            if isinstance(error, OSError):
                raise BuildFailure('output_publish_failed') from error
            raise
    finally:
        # Only this freshly created stage may be recursively removed; never old output/backups.
        if stage.exists():
            try:
                if stage.is_symlink():
                    stage.unlink()
                elif stage.name.startswith('.atr-search-stage-') and stage.resolve().parent == final.parent.resolve():
                    shutil.rmtree(stage)
            except OSError:
                warn()
