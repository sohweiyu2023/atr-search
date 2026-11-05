# AtR search journey audit — local candidate, 2026-11-04

## Version and ownership

Current local source includes the unpublished privacy/recovery follow-up plus this
journey pass. It is identified by the source hashes in the acceptance checkpoint,
not by the clone's old HEAD. Parent independently confirmed remote main as
`fd843f733fd19fb0cf53e64aca42273195c714d0`. The last live deployment evidence is
historical run `41174581477` from October 31; no new production request was made.
No current installed/public package or production outcome is inferred from local
tests. Existing installed Pagefind 1.5.2 is reused, without dependency acceptance.

The Blogger widget is embedded in the separate theme. Verified local references:

- User Downloads reference `theme-AtR-v18.40.xml`, SHA256 `6ff4f3d4b06222a22e842afbbb90edadc902691a7c47954e86b7e06c2653aecb`.
- User Downloads reference `AtR-v18.40-Package.zip`, SHA256 `fd0a47111b69ae5c60b339a5c72e548e2e44fefc370a4528276f8236bab7a922`.
- Widget reference within the ZIP: `AtR-v18.40-Package/qa/src/fulltext-search.js`,
  with historical fixture `qa/search-browser-fixture.html`.

These are readable historical sources, not a verified current theme candidate.
The theme is user-managed: saved room history says the user uploaded a new theme
themselves on October 20, while the accompanying search update changed only
`build_index.py`. No current separate maintainer or owning agent has been
identified. A later coordinated read-only test of the current public widget can
verify integration without editing it. No shared theme/XML/HTML, Downloads file
or Blogger publication is edited here.

## Entry-point inventory and primary outcomes

| Entry/control | Owner | Outcome / coverage |
| --- | --- | --- |
| Blog `/search`, archive field and submit | User-managed Blogger theme; separate maintainer unresolved | Current-widget typing, Enter, clearing and URL behavior blocked on authoritative source. Historical reference only. |
| Full-text region, highlighting, counts | User-managed Blogger theme | Real engine artifacts tested synthetically; actual widget display/current styling not accepted. |
| Chinese/Japanese routing and duplicate filtering | Theme + index contract | Real generated en/zh/ja API results tested. Theme routing/dedup controls need its current artifact. |
| Show more, filters, focus after results | User-managed Blogger theme | Not tested on current widget; do not replace it with a synthetic interface. |
| Empty query, no matches, loading/error/fallback | User-managed Blogger theme | Engine no-match and missing-import errors tested. Actual widget states/Google fallback remain unverified. |
| Article link, browser Back, close/reopen | User-managed Blogger theme | Generated metadata destination and local synthetic article activation tested; actual article controls/history unverified. |
| Generated index landing page | This repository | Last-success heading/time/counts, search/history/status links, recent/stale/unknown states rendered headlessly. |
| Download public status JSON | This repository / browser native download | Actual saved generated JSON parsed and link target checked. Native download interaction remains unverified. |
| CLI `--help`, setup and feed-file build | This repository | Actual child CLI processes: help, valid multilingual feed, missing dependency instruction, empty/malformed failures. No live feed fetched. |
| CLI repeat, failure, cancel and saved output | This repository | Repeat builds and versioned backup; last good output survives empty/malformed failures and injected cancellation. Promotion/rollback regression checks. |
| Work/output/diagnostics path selection | This repository | Overlaps, source roots and existing files rejected; separate generated paths required. |
| GitHub Actions Run/Cancel/history/auth/Pages setup | Repository owner + GitHub UI | No remote action. Current control state, sign-in and gates not inferred. Test-only CI reserved for final candidate. |
| Failure evidence, reviewed retention/export/read | This repository, API-only | Existing source tests cover metadata/privacy/recovery. There is no shipping support-management GUI in this repo. |
| Durable private body storage / OIDC | Separate approved infrastructure scope | Proposal only. No sink, key, trust or access configured. |

The generated landing page contains three native links, not disabled pseudo-build
buttons. It states why a stale time is not a confirmed error, and directs the
operator to history rather than initiating a request. The actual Blogger form's
disabled controls and recovery explanations belong to the user-managed theme,
outside this repository's source; no separate owning agent is asserted.

## Concrete local fixes

- Build replacement in a sibling staging directory; promote only after index and
  status validation. Preserve old generated output under a versioned backup.
- Restore old output if promotion fails or is cancelled before completion.
  Surface a fixed recovery instruction if restoration itself fails.
- Refuse overlapping output/work/diagnostic paths, source roots and existing files
  selected as output directories. Never recursively remove old output/backups.
- Add an owned semantic status page with UTC last-success time, accessible native
  links, focus indication, no-JavaScript fallback and an explicit freshness policy.
- Give a fixed first-run dependency instruction rather than a generic failure.
- Preserve the previous source version under
  `.diagnostics/backups/journey-20261104-01/` before edits.

## Strongest evidence and its limits

`tests/run_local_journeys.py` builds real Pagefind indexes from three synthetic
articles, serves them only on loopback, runs the actual browser JS/WASM API and
checks expected titles/destinations for English `quiet mind`, Chinese `缘起` and
Japanese `悟り`. Repeat/no-match behavior and a local iframe article activation
are checked. The missing-index import is an injected local error, not a provider
failure. The API harness is explicitly **not** the Blogger widget.

An isolated headless installed Chrome profile renders the candidate's own status
page. Desktop width is measured, not assumed from command-line dimensions. A
390px iframe supplies a measured mobile viewport because Windows headless Chrome
clamps the top-level width. Evidence includes DOM, screenshots and JSON states;
links wrap without horizontal overflow, programmatic focus works and recovery
content is reachable in scroll order. Native Tab/Enter, screen-reader use, wheel
input, actual downloads, and live Back/close/reopen need a later coordinated pass.

Initial local failures are retained: sandbox Pagefind write permission failure,
then a harness that ended before asynchronous WASM completion and mismeasured
mobile width. The corrected harness gates completion and measures its frame;
these failed attempts are not acceptance passes.

## Release gaps / handoffs

1. Obtain the exact latest user-managed theme artifact/hash, or coordinated
   read-only access to the current public widget. No separate maintainer has been
   identified. Audit its full search/results/navigation/recovery journey without
   theme edits; do not ask the user to substitute for missing prechecks.
2. Review this changed builder/status-page/privacy candidate and final local
   evidence. Run GitHub validation only for the final production-ready candidate;
   no intermediate hosted CI, source publication or deployment in this pass.
3. Coordinate native keyboard/download/history checks after CloudScribe releases
   shared foreground. No desktop/browser foreground was taken for this audit.
4. Resolve the private-retention policy/sink before claiming durable full-provider
   explanations. Metadata-only defaults intentionally lose unreviewed details at
   process exit; the S3/OIDC proposal is not configured.
5. New staging/recovery and visible status changes are local only. Existing main,
   schedules, permissions, production artifacts and public packages are unchanged.

## Lessons to carry into other projects

- Inventory entry points and outcomes before declaring completion. Verify first
  run, setup instructions, disabled controls, normal use, empty/error states,
  recovery, cancellation, repeats, close/reopen and saved/exported output.
- Green unit tests do not prove a user journey. Test the actual candidate UI and
  generated artifacts, with hashes and viewport/input evidence; identify any
  simulated provider behavior explicitly.
- Measure viewport and asynchronous completion. A screenshot command succeeding
  does not prove the requested viewport or a finished search.
- Keep the previous working output until its replacement is validated. Cancellation
  and failed updates must not silently destroy the user's last usable result.
- Distinguish source, local artifact, main, installed build, production and package.
  A historical theme or deployed widget cannot accept a new local candidate.
- Respect ownership and foreground coordination. Missing current source is a
  handoff requirement, not permission to overwrite another task's shared HTML.
- Retain safe, useful failure evidence with explicit missing/truncated/unavailable
  states. Do not fabricate discarded provider explanations or replay paid calls.
- Keep private bodies/secrets out of public logs/artifacts; prove actual persisted
  output and recovery. Full durable retention needs its own approved access design.
- Iterate locally; use hosted CI at the final candidate stage. Do not turn testing
  into redundant deployments, repeated provider requests or new access grants.

## Final local candidate review corrections

Independent review reproduced unsafe selection of unmanaged nonempty work/output
folders, mixed-case `.git`, and the private evidence directory. Those locations
are now rejected before writes. Managed roots use explicit ownership markers;
legacy/unmarked folders are preserved and require new/empty paths rather than
guessed migration. Work sessions use fresh subfolders; the work root and its
operator notes are never recursively deleted. The output ownership marker holds
only a generated identity/generation, not request content or secrets.

Cancellation before promotion restores the previous output. If replacement has
already completed when interruption/error is observed, the CLI reports that
committed state and tells the operator to inspect status before retrying. A hard
kill between the two directory renames cannot be rolled back by dead code: the
old version remains in its backup. Missing output plus a recognized backup now
blocks a blind restart pending inspection/restoration. Tests cover rollback
failure and this recovery guard; no zero-downtime or hard-kill auto-recovery claim
is made.

Freshness now recalculates each minute and on focus/visibility without a fetch.
The browser fixture advances its clock by 13 hours and fires focus, verifies an
in-place recent-to-stale transition, restores the clock, and verifies recent
again. This is a synthetic event/clock check, not a literal 12-hour live test.
The final harness reads status after the repeat build and fingerprints runtime
sources plus its own assertions before execution, rejecting a mid-run change.

These corrections supersede the earlier 53-test/first-journey snapshot. Final
review and acceptance evidence must point to the refreshed hash manifest and
latest artifact. The user-managed theme remains unmodified; no current separate
theme maintainer/agent has been identified.

