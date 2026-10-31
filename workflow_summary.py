"""Summarize fixed workflow phases; never print step outputs or raw errors."""
import json
import os
from pathlib import Path
import re

PHASES = ('checkout', 'python_setup', 'node_setup', 'dependencies', 'tests',
          'index', 'pages_config', 'artifact', 'keepalive')
OUTCOMES = {'success', 'failure', 'cancelled', 'skipped'}


def console(text):
    try:
        print(text)
    except (OSError, ValueError):
        pass  # The native Actions result remains authoritative if the log sink is gone.


def render(steps, run_id, attempt):
    run_id = run_id if re.fullmatch(r'[0-9]{1,24}', run_id) else 'unavailable'
    attempt = attempt if re.fullmatch(r'[0-9]{1,6}', attempt) else 'unavailable'
    lines = ['## AtR build diagnostics', '', f'Run {run_id}; attempt {attempt}.', '',
             '| Phase | Outcome |', '| --- | --- |']
    for phase in PHASES:
        value = steps.get(phase, {})
        result = value.get('outcome') if isinstance(value, dict) else None
        result = result if isinstance(result, str) and result in OUTCOMES else 'unavailable'
        lines.append(f'| {phase} | {result} |')
    lines.extend(['', 'Warnings remain distinct from failed steps. Aggregate index events are in the index step log.',
                  'No visitor queries, article content, URLs, credentials or step outputs are included.'])
    return '\n'.join(lines) + '\n'


def main():
    try:
        steps = json.loads(os.environ.get('ATR_STEP_RESULTS', '{}'))
        if not isinstance(steps, dict):
            steps = {}
    except ValueError:
        steps = {}
    summary = render(steps, os.environ.get('GITHUB_RUN_ID', ''), os.environ.get('GITHUB_RUN_ATTEMPT', ''))
    console(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        try:
            with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a', encoding='utf-8') as target:
                target.write(summary)
        except OSError:
            console('::warning::AtR summary file could not be written; use the original step logs.')


if __name__ == '__main__':
    main()
