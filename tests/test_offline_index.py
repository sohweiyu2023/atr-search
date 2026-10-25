"""Exercise the locked Pagefind CLI with a synthetic multilingual Blogger feed."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OfflineIndexTest(unittest.TestCase):
    def test_multilingual_index_and_static_page_exclusion(self):
        def entry(slug, text):
            return {
                'link': [{'rel': 'alternate', 'href': 'https://example.invalid/' + slug}],
                'title': {'$t': 'Synthetic <title>'},
                'content': {'$t': '<p>' + text + '</p><script>excluded_script_marker</script>'},
                'published': {'$t': '2026-10-25T00:00:00Z'},
                'category': [{'term': 'test,label'}],
            }
        feed = {'feed': {'entry': [
            entry('2026/english.html', 'quiet mind synthetic searchable example'),
            entry('2026/chinese.html', '觉醒现实明心见性' * 20),
            entry('2026/japanese.html', 'こころのめざめ' * 20),
            entry('p/old-backup.html', 'must not be indexed'),
        ]}}
        with tempfile.TemporaryDirectory(prefix='atr-offline-') as tmp:
            work = Path(tmp)
            fixture = work / 'feed.json'
            fixture.write_text(json.dumps(feed, ensure_ascii=False), encoding='utf-8')
            out = work / 'public'
            site = work / 'site'
            subprocess.run([sys.executable, str(ROOT / 'build_index.py'), '--feed-file', str(fixture),
                            '--out', str(out), '--work', str(site)], check=True, cwd=ROOT)
            status = json.loads((out / 'status.json').read_text(encoding='utf-8'))
            self.assertEqual(status['posts'], 3)
            self.assertEqual(status['pages_left_out'], 1)
            self.assertEqual(status['chinese'], 1)
            self.assertEqual(status['japanese'], 1)
            self.assertEqual(status['indexes'], ['pagefind/', 'pagefind-zh/', 'pagefind-ja/'])
            for index in status['indexes']:
                self.assertTrue((out / index / 'pagefind.js').is_file())
                self.assertTrue(any((out / index).rglob('*.pf_fragment')))
                self.assertFalse(any((out / index).glob('*-ui.js')))
            text = ''.join(p.read_text(encoding='utf-8') for p in site.rglob('*.html'))
            self.assertNotIn('old-backup', text)
            self.assertNotIn('excluded_script_marker', text)
            self.assertIn('&lt;title&gt;', text)
            self.assertTrue((out / '.nojekyll').is_file())


if __name__ == '__main__':
    unittest.main()
