# cnwatch

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
| M3 | Rule engine | ⬜ 26 categories + 4 patches + risk grading |
| M4 | Daily report | ⬜ dual view + baseline comparison |
| M5 | Archive and notify | ⬜ on-demand PDF + alerts |
| M6 | Release | ⬜ tag + GitHub Release + release ledger |

Commands that belong to a future milestone currently exit with code `3` and write
nothing. That is temporary and intentional: see `plan/03-架构与技术选型.md` §七.

## Quick start

Requires Python 3.8+.

```bash
pip install -e ".[dev]"

# Verify the environment and the installation surface.
cnwatch selftest
```

A healthy run prints one line per check and ends with `result: pass`.

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
  notify      发送提醒 (M5)
  run         端到端编排 (M4)
  selftest    环境与安装面自检 (M0)
```

Exit codes are part of the contract:

| Code | Meaning |
|---|---|
| 0 | completed |
| 1 | completed with a reported degradation (partial results, e.g. some stocks failed) |
| 2 | bad usage or unusable input; nothing was produced |
| 3 | the command belongs to a milestone that is not implemented yet |

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

Tests must pass on Python 3.8 and 3.12. CI runs four combinations
(ubuntu/windows × 3.8/3.12).

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
