#!/usr/bin/env python3
"""Build the Awakening to Reality full-text search index (Pagefind) from the
Blogger feed. Runs by itself on GitHub Actions every few hours (see
.github/workflows/build-search.yml); you never need to run it by hand.

  python build_index.py --out public            # whole blog
  python build_index.py --out public --feed-file sample.json   # offline test

Output (published to GitHub Pages):
  public/pagefind/      every post and page (one index, any language)
  public/pagefind-zh/   posts with Chinese text, split into Chinese words
  public/pagefind-ja/   posts with Japanese text
  public/status.json    when it ran, how many posts, any problems
Blogger Pages (/p/... addresses: old backups) are never indexed.
Python standard library only; run npm ci before building to install locked Pagefind.
"""
import argparse, html, json, os, re, shutil, subprocess, sys, time, urllib.request
from pathlib import Path
from html.parser import HTMLParser
from diagnostics import BuildFailure, Diagnostics, validate_index

BLOG = 'https://www.awakeningtoreality.com'
PAGEFIND = Path(__file__).resolve().parent / 'node_modules' / 'pagefind' / 'lib' / 'runner' / 'bin.cjs'
# Posts only. The blog's Pages section (/p/...) holds backups of old, outdated
# posts and must never appear in search.
FEEDS = ('/feeds/posts/full',)
STATIC_PAGE = re.compile(r'^https?://[^/]+/p/')
PAGE_SIZE = 100
DIAGNOSTICS = None

def event(name, level='info', **counts):
    if DIAGNOSTICS is not None:
        DIAGNOSTICS.emit(name, level, **counts)

def fetch_json(url, tries=4):
    started = time.monotonic()
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'atr-search-index/1.0 (+https://www.awakeningtoreality.com)'})
            with urllib.request.urlopen(req, timeout=60) as res:
                result = json.loads(res.read().decode('utf-8'))
            event('fetch_succeeded', attempt=attempt, seconds=round(time.monotonic() - started, 3))
            return result
        except Exception as error:  # network hiccup: retry with back-off
            code = getattr(error, 'code', 0)
            event('fetch_failed', level='error' if attempt == tries else 'warning',
                  attempt=attempt, max_attempts=tries, http_status=code if isinstance(code, int) else 0,
                  timeout=isinstance(error, TimeoutError) or isinstance(getattr(error, 'reason', None), TimeoutError),
                  invalid_json=isinstance(error, (ValueError, UnicodeError)),
                  seconds=round(time.monotonic() - started, 3), retry_seconds=5 * attempt if attempt < tries else 0)
            if attempt == tries:
                raise
            time.sleep(5 * attempt)

def all_entries(blog=BLOG):
    """Every post and page. Blogger may return fewer items per request than
    asked for, so paging follows the feed's own total."""
    entries = []
    for feed in FEEDS:
        start, total = 1, None
        while total is None or start <= total:
            url = '%s%s?alt=json&max-results=%d&start-index=%d' % (blog, feed, PAGE_SIZE, start)
            data = fetch_json(url).get('feed', {})
            if total is None:
                total = int(data.get('openSearch$totalResults', {}).get('$t', 0) or 0)
                event('feed_total', expected=total)
            batch = data.get('entry', []) or []
            if not batch:
                if start <= total:
                    event('feed_ended_early', level='warning', expected=total, received=start - 1)
                break
            entries.extend(batch)
            event('feed_batch', first=start, last=start + len(batch) - 1, received=len(batch))
            start += len(batch)
    return entries

class TextOut(HTMLParser):
    """Post HTML -> paragraphs of plain text (no styles, scripts, players)."""
    SKIP = {'script', 'style', 'template', 'noscript', 'iframe', 'svg', 'object', 'embed', 'audio', 'video', 'button', 'select'}
    BLOCK = {'p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'tr', 'td', 'th', 'dt', 'dd', 'br', 'section', 'article', 'figcaption', 'pre', 'hr', 'table', 'ul', 'ol'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0; self.blocks = []; self.current = []
    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP: self.depth += 1
        elif tag in self.BLOCK: self.flush()
    def handle_startendtag(self, tag, attrs):
        if tag in self.BLOCK: self.flush()
    def handle_endtag(self, tag):
        if tag in self.SKIP: self.depth = max(0, self.depth - 1)
        elif tag in self.BLOCK: self.flush()
    def handle_data(self, data):
        if not self.depth: self.current.append(data)
    def flush(self):
        text = re.sub(r'\s+', ' ', ''.join(self.current)).strip()
        if text: self.blocks.append(text)
        self.current = []

def paragraphs(markup):
    parser = TextOut(); parser.feed(markup or ''); parser.close(); parser.flush()
    return parser.blocks

HAN = re.compile(r'[㐀-鿿豈-﫿]')
KANA = re.compile(r'[぀-ヿ]')

def entry_record(entry):
    link = next((l['href'] for l in entry.get('link', []) if l.get('rel') == 'alternate'), '')
    title = html.unescape(entry.get('title', {}).get('$t', '')) or 'Untitled post'
    blocks = paragraphs(entry.get('content', {}).get('$t', ''))
    text = ' '.join(blocks)
    letters = max(1, len(re.findall(r'\w', text)))
    han, kana = len(HAN.findall(text)), len(KANA.findall(text))
    return {
        'link': link, 'title': title, 'blocks': blocks,
        'date': entry.get('published', {}).get('$t', '')[:10],
        'labels': [c.get('term', '') for c in entry.get('category', []) if c.get('term')],
        'zh': han >= 40 and han / letters >= 0.05 and kana < han * 0.1,
        'ja': kana >= 40,
    }

def page_html(rec):
    """One indexed page per post; Pagefind keeps the real post address, date and labels."""
    def attr(value):
        return html.escape(value, quote=True)
    tags = ''.join('<span data-pagefind-filter="label:%s"></span>' % attr(l.replace(',', ' ')) for l in rec['labels'])
    body = ''.join('<p>%s</p>' % html.escape(b) for b in rec['blocks'])
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>%s</title></head><body>'
            '<main data-pagefind-body><span data-pagefind-meta="link:%s"></span><span data-pagefind-meta="date:%s"></span>%s'
            '<h1 data-pagefind-meta="title">%s</h1>%s</main></body></html>') % (
        html.escape(rec['title']), attr(rec['link']), attr(rec['date']), tags, html.escape(rec['title']), body)

def run_pagefind(site, out, language):
    if not PAGEFIND.is_file():
        event('index_dependency_missing', level='error')
        raise RuntimeError('Pagefind is not installed. Run npm ci --ignore-scripts first.')
    cmd = ['node', str(PAGEFIND), '--site', site, '--output-path', out, '--force-language', language, '--quiet']
    started = time.monotonic()
    event('index_started', chinese=language == 'zh', japanese=language == 'ja')
    # Pagefind output can contain titles/paths. Keep diagnostics aggregate-only.
    result = subprocess.run(cmd, capture_output=True)
    event('index_process_finished', level='info' if result.returncode == 0 else 'error',
          exit_code=result.returncode, seconds=round(time.monotonic() - started, 3),
          stderr_bytes=len(result.stderr), stdout_bytes=len(result.stdout),
          permission_denied=b'PermissionDenied' in result.stderr,
          process_panicked=b'panicked' in result.stderr)
    if result.returncode:
        raise BuildFailure('index_process_failed')
    if result.stderr:
        event('index_stderr_present', level='warning', stderr_bytes=len(result.stderr),
              stemming_unavailable=b"doesn't support stemming" in result.stderr)

def build(args):
    started = time.monotonic()
    DIAGNOSTICS.stage = 'fetch'
    if args.feed_file:
        with open(args.feed_file, encoding='utf-8') as fixture:
            entries = json.load(fixture)['feed']['entry']
    else:
        entries = all_entries(args.blog.rstrip('/'))
    DIAGNOSTICS.stage = 'records'
    records = []
    skipped = {'missing_link': 0, 'empty_content': 0, 'static_pages': 0}
    pages = 0
    for entry in entries:
        rec = entry_record(entry)
        if not rec['link']:
            skipped['missing_link'] += 1
        elif not rec['blocks']:
            skipped['empty_content'] += 1
        else:
            if STATIC_PAGE.match(rec['link']):
                pages += 1
                if not args.keep_static_pages:
                    skipped['static_pages'] += 1
                    continue
            records.append(rec)
    event('records_selected', received=len(entries), indexed=len(records), **skipped)
    minimum = 1 if args.feed_file else args.min_posts
    if len(records) < minimum:
        event('insufficient_records', level='error', actual=len(records), minimum=minimum)
        raise BuildFailure('insufficient_records')
    DIAGNOSTICS.stage = 'index'
    shutil.rmtree(args.work, ignore_errors=True); shutil.rmtree(args.out, ignore_errors=True)
    groups = {'en': records, 'zh': [r for r in records if r['zh']], 'ja': [r for r in records if r['ja']]}
    event('language_counts', all_languages=len(records), chinese=len(groups['zh']), japanese=len(groups['ja']))
    for lang, recs in groups.items():
        if not recs:
            continue
        site = os.path.join(args.work, lang)
        os.makedirs(site, exist_ok=True)
        for i, rec in enumerate(recs):
            with open(os.path.join(site, '%05d.html' % i), 'w', encoding='utf-8') as f:
                f.write(page_html(rec))
        out = os.path.join(args.out, 'pagefind' if lang == 'en' else 'pagefind-' + lang)
        run_pagefind(site, out, lang)
        DIAGNOSTICS.stage = 'validation'
        event('index_validated', chinese=lang == 'zh', japanese=lang == 'ja', **validate_index(out, lang, len(recs)))
        DIAGNOSTICS.stage = 'index'
        for name in os.listdir(out):
            if re.search(r'-ui\.(js|css)$', name):
                os.remove(os.path.join(out, name))
    DIAGNOSTICS.stage = 'status'
    status = {'built': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'posts': len(records), 'pages_left_out': 0 if args.keep_static_pages else pages,
              'chinese': len(groups['zh']), 'japanese': len(groups['ja']), 'seconds': round(time.monotonic() - started, 1),
              'indexes': ['pagefind/'] + ['pagefind-%s/' % k for k in ('zh', 'ja') if groups[k]]}
    with open(os.path.join(args.out, 'status.json'), 'w', encoding='utf-8') as f:
        json.dump(status, f, indent=1)
    with open(os.path.join(args.out, 'index.html'), 'w', encoding='utf-8') as f:
        f.write('<!doctype html><meta charset="utf-8"><title>AtR search index</title><p>Search index for '
                '<a href="%s">Awakening to Reality</a>. Built %s from %d posts.</p>' % (BLOG, status['built'], len(records)))
    open(os.path.join(args.out, '.nojekyll'), 'w').close()
    event('status_written', posts=len(records), indexes=len(status['indexes']))

def main():
    global DIAGNOSTICS
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='public')
    ap.add_argument('--work', default='build-site')
    ap.add_argument('--feed-file', help='use a saved feed JSON (tests) instead of the live blog')
    ap.add_argument('--blog', default=BLOG, help='blog address (tests use a local copy)')
    ap.add_argument('--min-posts', type=int, default=50, help='refuse to publish an index with fewer posts')
    ap.add_argument('--diagnostics', default='.diagnostics/build.json', help='aggregate diagnostic report outside published output')
    ap.add_argument('--keep-static-pages', action='store_true', help=argparse.SUPPRESS)  # tests of the theme's own /p/ filter only
    args = ap.parse_args()
    DIAGNOSTICS = Diagnostics(args.diagnostics)
    DIAGNOSTICS.emit('build_started', offline=bool(args.feed_file))
    try:
        build(args)
    except (Exception, KeyboardInterrupt) as error:
        # Never dump exception messages, URLs, commands, article text or tracebacks.
        cancelled = isinstance(error, KeyboardInterrupt)
        if isinstance(error, BuildFailure):
            event(error.reason, level='error')
        event('build_interrupted' if cancelled else 'build_error', level='warning' if cancelled else 'error', interrupted=cancelled,
              validation_failure=isinstance(error, BuildFailure), io_failure=isinstance(error, OSError),
              invalid_data=isinstance(error, (ValueError, KeyError, TypeError)))
        DIAGNOSTICS.finish('cancelled' if cancelled else 'failed')
        return 130 if cancelled else 1
    DIAGNOSTICS.stage = 'complete'
    DIAGNOSTICS.finish('succeeded')
    return 0

if __name__ == '__main__':
    sys.exit(main())
