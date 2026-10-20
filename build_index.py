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
Python 3.9+ standard library only; Pagefind runs through npx (Node 18+).
"""
import argparse, html, json, os, re, shutil, subprocess, sys, time, urllib.request
from html.parser import HTMLParser

BLOG = 'https://www.awakeningtoreality.com'
PAGEFIND = 'pagefind@1.5.2'
# Posts only. The blog's Pages section (/p/...) holds backups of old, outdated
# posts and must never appear in search.
FEEDS = ('/feeds/posts/full',)
STATIC_PAGE = re.compile(r'^https?://[^/]+/p/')
PAGE_SIZE = 100

def log(*parts):
    print('[atr-search]', *parts, flush=True)

def fetch_json(url, tries=4):
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'atr-search-index/1.0 (+https://www.awakeningtoreality.com)'})
            with urllib.request.urlopen(req, timeout=60) as res:
                return json.loads(res.read().decode('utf-8'))
        except Exception as error:  # network hiccup: retry with back-off
            log('fetch failed (attempt %d of %d): %s %s' % (attempt, tries, url, error))
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
                log(feed, 'reports', total, 'items')
            batch = data.get('entry', []) or []
            if not batch:
                break
            entries.extend(batch)
            log(feed, 'items', start, 'to', start + len(batch) - 1)
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
    cmd = ['npx', '-y', PAGEFIND, '--site', site, '--output-path', out, '--force-language', language, '--quiet']
    log('running', ' '.join(cmd))
    subprocess.run(cmd, check=True, shell=(os.name == 'nt'))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='public')
    ap.add_argument('--work', default='build-site')
    ap.add_argument('--feed-file', help='use a saved feed JSON (tests) instead of the live blog')
    ap.add_argument('--blog', default=BLOG, help='blog address (tests use a local copy)')
    ap.add_argument('--min-posts', type=int, default=50, help='refuse to publish an index with fewer posts')
    ap.add_argument('--keep-static-pages', action='store_true', help=argparse.SUPPRESS)  # tests of the theme's own /p/ filter only
    args = ap.parse_args()
    started = time.time()
    entries = json.load(open(args.feed_file, encoding='utf-8'))['feed']['entry'] if args.feed_file else all_entries(args.blog.rstrip('/'))
    records = [r for r in (entry_record(e) for e in entries) if r['link'] and r['blocks']]
    pages = [r for r in records if STATIC_PAGE.match(r['link'])]
    if pages and not args.keep_static_pages:
        records = [r for r in records if not STATIC_PAGE.match(r['link'])]
        log('left out', len(pages), 'Blogger Pages (/p/ addresses)')
    if len(records) < (1 if args.feed_file else args.min_posts):
        sys.exit('[atr-search] only %d posts found; refusing to publish a near-empty index' % len(records))
    shutil.rmtree(args.work, ignore_errors=True); shutil.rmtree(args.out, ignore_errors=True)
    groups = {'en': records, 'zh': [r for r in records if r['zh']], 'ja': [r for r in records if r['ja']]}
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
        for name in os.listdir(out):          # the theme draws its own results: drop Pagefind's UI files
            if re.search(r'-ui\.(js|css)$', name):
                os.remove(os.path.join(out, name))
    status = {'built': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'posts': len(records), 'pages_left_out': 0 if args.keep_static_pages else len(pages),
              'chinese': len(groups['zh']), 'japanese': len(groups['ja']), 'seconds': round(time.time() - started, 1),
              'indexes': ['pagefind/'] + ['pagefind-%s/' % k for k in ('zh', 'ja') if groups[k]]}
    with open(os.path.join(args.out, 'status.json'), 'w', encoding='utf-8') as f:
        json.dump(status, f, indent=1)
    with open(os.path.join(args.out, 'index.html'), 'w', encoding='utf-8') as f:
        f.write('<!doctype html><meta charset="utf-8"><title>AtR search index</title><p>Search index for '
                '<a href="%s">Awakening to Reality</a>. Built %s from %d posts.</p>' % (BLOG, status['built'], len(records)))
    open(os.path.join(args.out, '.nojekyll'), 'w').close()
    log('done:', json.dumps(status))

if __name__ == '__main__':
    main()
