# atr-search

Full-text search index for https://www.awakeningtoreality.com, rebuilt every 3
hours by GitHub Actions from the blog feed and published with GitHub Pages.
Built with [Pagefind](https://pagefind.app).

Use Python 3.15.1 and Node 26.12.1 (the project pins are in `.python-version`
and `.nvmrc`). Install the exact dependency graph before building:

```sh
npm ci --ignore-scripts
python -m unittest discover -s tests -v
python build_index.py --out public
```

The test builds English, Chinese and Japanese indexes from synthetic local data
and excludes old Blogger Pages. It does not fetch the live blog or publish.
Pagefind 1.5.2 is pinned in `package.json` and `package-lock.json`; builds use
the installed CLI without downloading packages through npx. CI actions are
pinned to the verified stable release commits. No Python packages are required.
