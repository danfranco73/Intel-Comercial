# Intelligence memory containment (independent live-baseline patch)

Base: c223959. Branch: fix/intelligence-bounded-memory. This patch deliberately
excludes the separate BI contract/provisioning integration commits.

## Problem and scope

The sales daily-brief and drilldown context loads a continuous roughly 13-month
range, masters, coverage evidence and a serialized fingerprint. Output pagination
does not constrain that input. The existing tactical-dashboard chunking path is
not used here. A confirmed instance out-of-memory event motivates containment;
this patch does not attribute all prior latency to that cause.

Both endpoints now admit at most one Intelligence calculation per process,
without waiting, while also acquiring the existing shared heavy-analysis slot
with zero wait. Other endpoints retain their existing admission behavior.

The original successful calculation and monetary formulas are unchanged. A full
input that exceeds the budget is rejected, never sampled/truncated into a report.
This is a containment patch, NOT a scalable aggregation implementation or proof
that the first large real report can now run. Large universes will return 503.

## Limits

- 5,000 rows per input cursor/ClickHouse result, plus one sentinel to reject
  overflow. 20,000 cumulative input rows per request, including metadata reads.
- 8 MiB cumulative recursively estimated Python input size, 256 KiB per decoded
  row/document, bounded nesting/container sizes. Estimates intentionally count
  shared values repeatedly. They are not a process RSS quota.
- Mongo: batch size 1, server maxTimeMS 2,000, CSOT 5 seconds including getMore,
  bounded aggregate result and allowDiskUse=False. A BSON document must still be
  decoded before its size is checked (Mongo's individual document limit applies).
- ClickHouse: a separate thread-local client using existing configuration, two-
  second connection/read timeouts and query_retries=0. SQL SETTINGS enforce
  throw-on-overflow, 2 seconds server execution, 128 MiB server query memory,
  2 million rows/128 MiB scanned, 5,001 result rows/4 MiB result bytes, one thread
  and 128-row target blocks. SQL settings avoid silent driver-side filtering;
  unsupported/forbidden settings cause failure, not a weaker retry.
- No ClickHouse-to-Mongo fallback in the Intelligence loader, even on failure.
- A cooperative five-second application deadline is checked during reads and
  before returning. It cannot preempt a Python calculation, DNS resolution or
  every blocking native call. This is not a hard five-second response SLA.

Masters, authorization lookups, coverage documents, objectives and planning reads
also use the bounded read-only database wrapper. Coverage still computes its
existing whole-history counts, under query bounds, to preserve certification
semantics; a timeout must not turn into complete coverage. This may reject work
on a slow/large database, which is preferable to an invented complete report.

A limit or admission rejection returns HTTP 503 with
INTELLIGENCE_CAPACITY_EXCEEDED and no report fields. Other database failures keep
the existing sanitized INTELLIGENCE_UNAVAILABLE response. No new permissions,
credentials, migrations, indexes, persistent settings, schedules or BI writes.

## Verification

Windows synthetic regression: 202 passed.

    python -B -m pytest -p no:cacheprovider tests --ignore=tests/test_sales_phase1_repair.py --ignore=tests/test_sales_v2_pilot.py -q

The two excluded modules need Unix fcntl (25 parameterized cases not executed).
No production queries or CI claims. The small-fixture reports reconcile exactly
for company/seller/client/product dimensions; context repeats remain stable.
Existing brief, incomplete coverage, scoped authorization and objectives tests
pass. New tests cover overflow before enrichment, oversized masters/documents,
byte budgets, stream closure, concurrency, deadline cleanup, no fallback, server
settings and sanitized rejection without partial metrics.

A lazy ten-million-row synthetic source is stopped at row 5,001 for each endpoint.
tracemalloc peaks on the rejection path: brief 1,280,226 bytes; drilldown 1,221,545
bytes. Those figures measure Python allocations in this test, not whole-process
RSS, native driver buffers, successful maximum-size analysis or Render behavior.

## Remaining work before a large real report

Do not raise limits to fit a large source. A scalable implementation must replace
the retained detail with bounded storage aggregation/streaming summaries while
preserving seller reassignment, arbitrary filters, distinct buyers, full-period
comparisons, completeness evidence, stable context IDs and legacy explanations.
It needs cross-backend equivalence tests including refunds, monetary bases,
company-scoped masters and high-cardinality populations. The independent additive
BI contract also requires full-month buyer/risk universes before ranking; do not
silently drop those when combining patches.

Review/cherry-pick alongside the integration branch separately and rerun its
larger suite; no automatic merge has been performed. Deployment requires explicit
approval. Server setting support and actual memory headroom remain unverified;
this patch does not bound unrelated endpoints or total application RSS.
