# cnwatch

[![ci](https://github.com/yuluo554/cnwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/yuluo554/cnwatch/actions/workflows/ci.yml)

**A-share announcement watchboard for a self-selected list — local, deterministic, no LLM.**

Maintain 20–100 self-selected stocks, pull their announcements incrementally from
the public CNINFO (巨潮资讯网) query endpoints, tag every item with the official
26-category enumeration plus four keyword patches, grade it into a product-defined
risk priority, and produce a "stock × category" dual-view daily report as Markdown
and a single-file HTML page.

> **本工具仅聚合公开信息，不构成投资建议。**
> This tool aggregates public information only. It is not investment advice.

---

## Why

A-share investors with a 20–100 name watchlist face the same noise every day:
dozens of announcement titles, hundreds of PDF pages, and only a handful of items
that actually matter today. Existing open-source tools stop at "crawl and download
the PDF" — they do not classify, do not rank, and do not produce a daily artifact.

cnwatch takes a watchlist as input, uses the exchange's own category enumeration as
the taxonomy, and emits a local report. No account, no cloud, no telemetry, no
server, no Redis/Kafka, no model calls.

## Status

| Milestone | Theme | State |
|---|---|---|
| M0 | Plan and skeleton | ✅ CLI contract, SQLite schema, encoding guard, falsifiable self-test, tests, CI |
| M1 | Data first | ✅ frozen snapshots, stock master, rule package v1, truth ledger |
| M2 | Fetch and dedupe | ✅ watchlist + incremental fetch, offline replay |
| M3 | Rule engine | ✅ 26 categories + 4 patches + risk grading |
| M4 | Daily report | ✅ dual view + baseline comparison |
| M5 | Archive and notify | ✅ on-demand PDF + optional text + alerts off by default |
| M6 | Release | ✅ `v0.1.0` — tag, GitHub Release, downloadable archive + checksum, CI on two platforms |
| run (end-to-end) | ⬜ not implemented — exits 3, writes nothing | — |

`v0.1.0` is the first release, published from a commit whose CI run is green on
all legs (ubuntu/windows × Python 3.12). The release ships
`cnwatch-0.1.0.zip` — a `git archive` of the tag — next to a sibling
`cnwatch-0.1.0.zip.sha256`. What was verified for this release, and what is
explicitly **not** done yet, is recorded in `plan/RELEASE-M6.md`.

One command is still a placeholder: `run` (end-to-end orchestration) exits with
code `3` and writes nothing. That is temporary and intentional — it chains
commands that already work individually; see `plan/06-里程碑.md` M5 偏差.

## Quick start

Requires Python 3.12+.

```bash
pip install -e ".[dev]"

# Verify the environment and the installation surface.
cnwatch selftest
```

A healthy run prints one line per check and ends with `result: pass`.

To start from the released archive instead of a checkout:

```bash
gh release download v0.1.0 --repo yuluo554/cnwatch --pattern 'cnwatch-0.1.0.zip*'
sha256sum -c cnwatch-0.1.0.zip.sha256
unzip cnwatch-0.1.0.zip
cd cnwatch-0.1.0
pip install -e .
cnwatch selftest
```

`cnwatch-0.1.0.zip` is `git archive` of the `v0.1.0` tag, so it contains
exactly the tracked tree — `src/`, `tests/`, `tools/`, `plan/` and `data/` —
and the sibling `cnwatch-0.1.0.zip.sha256` holds the SHA-256 that the release
notes also state.

Without installing, run it from the repository root with the source tree on the
path:

```bash
PYTHONPATH=src python -m cnwatch selftest
```

(Run `pip install -e .` first if you prefer plain `python -m cnwatch`; the
`src/` layout is deliberately not importable from the repository root alone.)

Runtime files live under the current directory by default. Point them elsewhere
with `--home PATH` or `CNWATCH_HOME`:

```bash
cnwatch --home ./runtime selftest
```

## CLI

```
cnwatch [--home PATH] <command> [options]

  watchlist   自选清单管理 (M2)
  fetch       逐只增量抓取 (M2)
  classify    规则打标 (M3)
  report      生成日报 (M4)
  archive     按需归档 PDF (M5)
  notify      发送提醒，默认关闭 (M5)
  run         端到端编排 (未实现，退出码 3)
  selftest    环境与安装面自检 (M0)
```

Exit codes are part of the contract:

| Code | Meaning |
|---|---|
| 0 | completed |
| 1 | completed with a reported degradation (partial results, e.g. some stocks failed) |
| 2 | bad usage or unusable input; nothing was produced |
| 3 | the command is not implemented yet (`run` only) |

### Watchlist

```bash
# import a CSV (required columns: code, group; optional: name, note, org_id)
cnwatch watchlist import --file data/fixtures/watchlist-sample.csv

cnwatch watchlist add    --code 300750 --group 观察 --note "关注中"
cnwatch watchlist edit   --code 300750 --group 风险
cnwatch watchlist remove --code 300750
cnwatch watchlist list               # also: --group 持仓
cnwatch watchlist search --query gzmt # code prefix > name contains > pinyin prefix
cnwatch watchlist list --audit        # append-only change trail
cnwatch watchlist sync-master         # refresh names/orgIds, keep your groups and notes
```

Groups are restricted to `持仓 / 观察 / 风险`; any other value exits `2`. The
`orgId` always comes from the frozen stock master — `300750` is `GD165627`, not
a guessable prefix — and a code that is not in the master is refused rather than
stored with a derived identifier. A CSV row that is rejected does not block the
other rows (exit `1`, with the rejected lines named); a file-level problem
(missing column, non-UTF-8, empty) imports nothing (exit `2`).

### Fetch

```bash
# real fetch: one request per stock, paged, throttled, retried
cnwatch fetch

# replay the frozen snapshots offline (no network) — this is what the tests run
cnwatch fetch --snapshots-dir data/snapshots --today 2026-10-16

# print the policy parsed from data/README.md section 4
cnwatch fetch --show-config

# explicit market-wide sweep for one day (never becomes the watchlist cursor)
cnwatch fetch --all --date 2026-10-16
```

The window starts one day before the last successful run (`window_end`) and ends
"today"; `--today` pins that date so a run is reproducible. The first run has no
cursor: it looks back a documented number of days and is reported as a **baseline**
(stored rows, but `new 0`), so the first report never looks like a wall of news.

The summary always states requests, pages, new/already-known/dropped counts, and
names every failed stock with its reason and attempt count. A stock whose declared
`totalAnnouncement` disagrees with what was actually collected is a failure of that
stock — half a stock is never stored as if it were complete.

`data/` (the frozen stock master, snapshots and truth ledgers) is not part of the
wheel. A source checkout or editable install finds it automatically; any other
install needs `--data-dir <checkout>/data` or `CNWATCH_DATA`, and says so instead
of guessing.

### Classify

```bash
# label everything stored that has no label, or an older rule version
cnwatch classify

# only announcements on or after a date (older rows keep their labels)
cnwatch classify --since 2026-10-16
```

Each row becomes `{category, tags[], risk_level}` from the frozen rule package in
`src/cnwatch/resources/rules/rules-v1.json`, and the package version is written
into `classifications.rules_version` — a package upgrade relabels the stored rows
on the next run without any manual reset.

The decision uses the **title only**. The official 26 categories are tried in
`priority` order (ties broken by id) and the first hit wins; the four keyword
patches (诉讼·仲裁 / 减持 / 回购 / 质押) are scanned independently and every hit
lands in `tags`, in that fixed order. Risk is `high > mid > low` over the winning
category and the tags. A row that matches nothing is still stored, as
`unclassified`, with an explicit risk level — skipping it would make the report's
"未分类" count permanently zero.

`announcementType` is not an input: the numeric codes have no verified mapping to
the 26 categories, and a static AST check fails the build if the field appears in
the classify package at all. Categories that matched but lost are written to
`out/logs/classify-<version>.log`, not to the database and not to the report.

A missing, unreadable or incomplete rule package (not 26 categories, not the four
patches in order) exits `2` **before the database is opened**, so a broken package
cannot leave a half-labelled table behind.

### Report

```bash
# today, in the exchange timezone (UTC+8)
cnwatch report

# an explicit day, with a pinned timestamp so two runs are byte-identical
cnwatch report --date 2026-10-16 --generated-at 2026-10-16T12:00:00Z
```
Two files land in `out/reports/<date>/`: `daily.md` and a single-file
`daily.html` (stylesheet inlined, no external resource of any kind — the page
opens on a laptop with no network). Both are rendered from one assembled
document, so the two formats cannot disagree about content.

The report has two views of the same items — **by stock** and **by category**.
They are two partitions produced by the same function, so the id sets are equal
by construction; a test asserts it anyway, and also asserts the counts match per
group so a duplicated row cannot hide behind set equality.

Every item shows its risk level, date, title, category (plus any keyword tags)
and a link to the original filing on CNINFO. The risk printed is the stored one:
the report renders existing labels and never re-runs the matcher. A static check
fails the build if the report package imports the classify package at all.

Comparison against the previous report day is a set difference against
`report_baseline_items`:

- **First run**: no earlier baseline exists, so a baseline is recorded silently
  and `added` is `0`. The page says so explicitly instead of presenting the
  whole history as today's news.
- **Later runs**: `added` = today − previous day, `removed` = previous day −
  today. Removed items are counted and listed, never deleted; `removed_at` stays
  the fetch layer's field.

Only `source_kind='watchlist'` rows enter the report. A `fetch --all` market
sweep stores the whole market under a different source and never mixes in.

Exit codes: `0` normal, `1` a report was produced but the covering fetch run
failed some stocks (the header is marked incomplete and the failed codes are
listed), `2` no database, or the day has neither rows nor a baseline. A day with
rows that were never classified also exits `2` and points at `cnwatch classify`
— the report will not invent a label.

### Archive

```bash
# one named filing, or one stock, or everything graded high
cnwatch archive --announcement-id 1225601360
cnwatch archive --code 601318 --code 300750
cnwatch archive --risk high
cnwatch archive --all-high

# also extract the first pages of the text layer
cnwatch archive --code 601318 --text

# print the policy parsed from data/README.md section 4
cnwatch archive --show-config
```

Archiving is **on demand**: nothing is downloaded unless a filter names it, and a
run with no filter exits `2`. Files land in
`data/pdf/<code>/<date>/<announcement_id>.PDF`, and `archive_records` stores the
local path, the byte size, the download time, the text status and the excerpt.
`--announcement-id` is the one way to name a row outside the watchlist; the
`--risk` / `--category` filters never reach a market-wide sweep, for the same
reason the report does not.

Re-archiving a filing whose file is already on disk with the recorded size
performs **no download and writes no second row** — the row is updated only when
the local file is missing or the wrong size. A run with `--text` after a run
without it recovers the text from the local file, again without downloading.

Text extraction is optional and ordered PyMuPDF → `pdftotext` → nothing (the
core install has no dependencies). Asking for text when neither backend exists
exits `2` with an install hint and writes **nothing at all** — no PDF, no row, no
empty excerpt. A filing that downloads but yields no usable text is recorded as
`text_status=unavailable` with an empty excerpt and the run still exits `0`; the
text layer of real filings is genuinely inconsistent, so an empty extraction is
reported as such rather than dressed up as success. The excerpt is for a human
reading an alert and is never a classification input — a static check fails the
build if the archive package imports the matcher at all.

### Notify

```bash
# default: nothing is sent, nothing is read, nothing is contacted
cnwatch notify

# compose the message and print it without sending
cnwatch notify --channel desktop --dry-run --date 2026-10-16

cnwatch notify --channel webhook --webhook-url https://example.invalid/hook
cnwatch notify --channel email --email-host smtp.example.invalid --email-to me@example.invalid
```

The default channel is `none`, and it is read from `data/README.md` section 4
rather than hard-coded. With `none` selected the command returns before it opens
the database, creates a directory or builds a channel object: zero external calls
by construction, not by a code path that happens to do nothing.

A message lists the day's classified watchlist items (risk first, capped, with the
disclaimer from the single definition in `cnwatch/disclaimer.py`). A day with
nothing stored sends nothing and exits `0`.

A failed send exits `1`, appends one line to `out/logs/notify.log`, and changes
nothing else: the notification layer has no write path to the database, and the
daily report regenerates byte-for-byte after a failure.

## Data source and measured limits

Data source: 巨潮资讯网 (CNINFO) public query endpoints. All measurements below
were taken on 2026-10-17; they are recorded in `data/README.md` together with the
six hard constraints that shape the implementation.

- `POST http://www.cninfo.com.cn/new/hisAnnouncement/query` — announcement search.
- `GET http://www.cninfo.com.cn/new/data/szse_stock.json` — stock master with
  `code/pinyin/category/orgId/zwjc`. Despite the file name it covers Shanghai,
  Shenzhen and Beijing listings.
- `https://static.cninfo.com.cn/new/js/app/disclosure/notice/new-notice.js` — the
  official 26-category enumeration.
- `http://static.cninfo.com.cn/<adjunctUrl>` — direct PDF links.

Measured constraints that are **not** optimisations but requirements:

1. The query accepts **one stock per request**. Comma-joining several stocks only
   returns the first one, in any order, and repeating the parameter does not help.
2. `announcementTime` has date precision only, so the incremental window must
   overlap by one day; deduplication absorbs the overlap.
3. `announcementId` is the only stable key. It is the primary key in SQLite.
4. `pageSize` is capped at 30 server-side; requesting 100 still returns 30.
5. `column=szse|sse|bj` returns identical results, so there is one code path.
6. Requests are throttled, retried with exponential backoff, and a single stock's
   failure only fails that stock. A failed fetch is never reported as "no news".

Known limitations, stated plainly:

- `announcementType` numeric codes have **no verified mapping** to the 26
  categories. They therefore do not participate in classification.
- Annual reports, Q1 reports and supervisory-board notices can legitimately be
  absent in a 30-day window. That is disclosure seasonality, not an outage.
- PDF text layers are inconsistent (some filings extract cleanly, some are
  garbled). Text extraction is optional and never used as a classification input.
- The upstream service has a documented history of 502/504 responses. Runs that
  hit it report a degraded result instead of pretending everything succeeded.

## Design rules

- **Deterministic**: no LLM, no random sampling, no relevance scores. Same input
  and same rule package produce the same `category/tags/risk_level`.
- **Local only**: single process, SQLite, files on disk. No accounts, no uploads.
- **On demand, never bulk**: archiving downloads only what a filter names, and
  notifications are off (`none`) until a channel is chosen explicitly.
- **Zero core dependencies**: fetching, tagging and reporting use the standard
  library. `PyMuPDF` is an optional extra for PDF text extraction only.
- **Fail visibly**: unknown groups, missing `orgId`, unusable CSV and untagged
  items are reported, never silently defaulted.
- **Report layer renders, never decides**: the daily report consumes existing
  classifications; it does not recompute them.

## Repository layout

```
src/cnwatch/       CLI, schema, paths, encoding guard, self-test, resources
tests/             contract and behaviour tests
plan/              plan documents 00–07 and per-milestone handoffs
data/              data evidence, snapshots, fixtures, truth ledgers
out/               runtime output (ignored)
data/pdf/          downloaded originals (ignored)
```

## Development

```bash
pip install -e ".[dev]"
python -m pytest -q
```

Tests must pass on Python 3.12. CI runs two combinations
(ubuntu/windows × 3.12).

## Scope

In scope: watchlist, incremental fetch, official-category tagging, risk priority,
dual-view daily report, on-demand PDF archive, optional notifications. A
market-wide sweep exists only as an explicit fallback.

Out of scope: prices and charts, trading, recommendations, cloud sync, accounts,
telemetry, full-text search, fund announcements, Hong Kong/US listings, and
redistributing announcement full text or PDFs.

## Disclaimer

This project aggregates publicly accessible announcement metadata. It does not
provide investment advice, ratings, return forecasts or buy/sell signals. The
`high/mid/low` label is a product-defined screening priority, not a regulatory or
investment rating. Announcement content, availability and accuracy are controlled
by the original disclosing party and the exchange; verify anything material
against the original filing.

## License

MIT. See `LICENSE`.
