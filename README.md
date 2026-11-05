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

For a provider-free local build, pass a saved synthetic Blogger feed:

```sh
python build_index.py --feed-file sample.json --out public --work build-site --diagnostics .diagnostics/build.json
```

The output, work and diagnostic paths must be separate, generated locations.
Do not select source folders. A complete replacement is staged and validated
before publication. Failed/empty/malformed/cancelled builds keep the previous
output. Successful replacement keeps a versioned sibling directory named
`.atr-search-output-backup-...`; these backups are not published and have
operator-managed cleanup. If automatic restoration fails, inspect that backup
before retrying. Never overwrite a source directory to recover an index.

Nonempty work/output folders require this builder's ownership marker. Legacy
unmarked folders are not guessed to be disposable: preserve them and choose new
or empty `--out`/`--work` paths. Work builds use new session subfolders and never
recursively delete the work root. Missing output with a recognized backup blocks
a blind restart until the backup is inspected/restored. If interruption happens
after a complete replacement was observed, the CLI says so; inspect current
status before retrying instead of assuming the old version is active.

The generated landing page describes the last successful build, links to blog
search, build history and downloadable public status JSON, and marks an index
older than 12 hours as stale. It does not know the outcome of later workflow
runs. Before triggering a GitHub rebuild, inspect existing runs to avoid duplicate
work. A missing initial deployment is a setup state, not a successful search.
Freshness recalculates each minute and when the page regains focus/visibility;
no provider request is made to calculate it.

`atr-search` owns the builder, published index/status artifacts and workflows.
The embedded search form, full-text panel, pagination, fallback and article-page
controls belong to the user-managed Blogger theme. No current separate maintainer
is identified. Whole-widget acceptance needs a read-only test of the current public
widget or its authoritative current source; historical themes are not that proof.

For local journey evidence with an existing installed Chrome binary (no package
download, real provider or shared browser profile), run:

```sh
python tests/run_local_journeys.py --chrome "PATH_TO_INSTALLED_CHROME" --out .diagnostics/journey-UNIQUE_VERSION
```

This uses synthetic loopback fixtures, actual Pagefind output and isolated
headless profiles. It checks CLI recovery, generated status-page rendering and
the search-engine integration contract. It does not test the Blogger widget,
native Tab/Enter, actual browser downloads or a live deployment. See
`JOURNEY-LESSONS.md` for the inventory, ownership boundaries and release gaps.
