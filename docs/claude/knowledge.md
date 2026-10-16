# Knowledge Index (`knowledge_chunks`) and Retrieval

> Split out of the root `CLAUDE.md` to keep it under Claude Code's size
> limit; same rules, same authority. The design and the measurements live in
> `docs/design/rag-knowledge-layer.md`. This file holds the operational traps.

### The Vector Stage Scans HNSW Iteratively, or the Filter Starves It (#711, #722)
HNSW returns at most `hnsw.ef_search` nearest index entries, and pgvector
applies the query's `WHERE` **after** that. `retrieval._vector_stage` sets
`ef_search = max(40, limit)` (50 for the default candidate pool), and the
public filter keeps only `ARTICLE` / `ARTICLE_CLAIM` chunks of public
articles: about 46% of the table (6,461 of 14,102 rows on 2026-10-13) is
`VERIFIED_CLAIM`, and every dead index entry also takes a slot. Before #722
the fused pool reaching the reranker was 5–27 chunks instead of 50, and Ask
PIC **silently abstained** on answerable questions (r071 0.48, r149 0.68)
whose chunk never reached the reranker. Nothing errored: it showed up only as
top-1 rerank scores under the 0.74 floor.

**The fix (#722):** `_vector_stage` also runs
`set_config('hnsw.iterative_scan', 'relaxed_order', true)` in the same
statement as `ef_search`, both `SET LOCAL` (this transaction only). The scan
then keeps going until the filtered query has `limit` rows, up to
`hnsw.max_scan_tuples` (see the #729 bullets below). Every
caller shares it: Ask PIC, MCP search, the admin search endpoint, the
suggestion-dedup steer, KB matching and the consistency check. Related
articles and `nearest_articles` (B3 dedup) rank article **centroids** in a
CTE, never touch the HNSW index and are unaffected.

- `relaxed_order` can return rows slightly out of distance order, and every
  caller reads the list as a ranking (RRF rank, vector-mode order, floors), so
  `_vector_stage` re-sorts by `(distance, id)` in Python.
- **Support is checked once per process** from `pg_extension.extversion`
  (needs pgvector >= 0.8; prod runs 0.8.6). On an older version it logs one
  WARNING and runs without it. Never probe it with a trial `SET`: before the
  library is loaded an unknown `hnsw.*` name is accepted as a placeholder, and
  after it errors and aborts the caller's transaction.
- Measured on prod (`docs/portfolio/rag/results/2026-10-13-hnsw-722-summary.md`):
  the live selection now matches the exact scan exactly (0/190 answerable
  abstained at 0.74), for about +10 ms p50 on the vector stage.
- **The scan cap is `retrieval.MAX_SCAN_TUPLES` (50,000, #729)**, set
  `SET LOCAL` in the same `set_config` statement, only when the iterative scan
  is. pgvector's default (20,000 index tuples, dead entries included) was only
  1.4x the table (14,101 rows on 2026-10-13), and a scan that hits the cap
  returns a short pool **silently**, which is the #711 abstain failure again.
  The cap costs nothing on a normal query (the scan stops once `limit` rows
  pass the filter, after ~100 tuples for the public filter); it only bounds a
  filter that matches fewer rows than `limit`, or very sparsely, where the
  scan walks the whole index. A forced full HNSW walk of the 14.1k-row prod
  index took ~120–145 ms p50 / ~300 ms p95 server-side, so 50,000 bounds that
  near 0.5 s. It is a fixed constant, not derived from a per-search `COUNT`
  (a round trip on every search for a value that changes by hundreds of rows
  a week) and not a setting (changing it needs a restart either way).
- **An underfilled pool logs a WARNING** (`knowledge search: vector stage
  underfilled: returned=… of limit=…, matching rows>=…`). Only when the
  stage returns fewer than `limit` rows does `_vector_stage` count the rows the
  filter matches (`LIMIT limit`, no `ORDER BY`, so no HNSW scan; ~2–3 ms
  server-side on prod) and it warns only when more match than came back: a
  filter that genuinely has fewer matches stays quiet. Seeing it means the
  scan stopped early: raise `MAX_SCAN_TUPLES` (and check `n_dead_tup`, since
  dead entries count against the cap). `hnsw.scan_mem_multiplier` (a memory
  cap, default 1 × `work_mem`) can also stop the scan and would log the same.

### Dead Tuples: Unchanged Rows Are Never Written, the Sweep Vacuums (#711, #728)
The indexer (`app/services/knowledge/indexer.py`, `_write`) does not update
re-embedded chunks in place. A changed chunk is `INSERT`ed as a new row, and
the row it replaces is `DELETE`d as stale. A kept row that gains a situating
sentence (#697) is replaced the same way (module docstring). A kept row (hash
matched) is `UPDATE`d **only when a stored column differs** from what the
write would set (`_changed_kept` compares every column of `row_values`):
position, `source_id`, heading or `meta`. Only a row whose unique key
(`source_id`, `chunk_index`) moves steps aside to `chunk_index = -id` first.
Every one of these writes leaves a dead tuple, plus a dead entry in the HNSW
index, until a vacuum; an unchanged row leaves none. Before #728 every kept row
of a source that had *any* change was `UPDATE`d twice, so #714's 3 KB
corrections rewrote all ~6,460 `VERIFIED_CLAIM` rows (13,583 dead tuples).
With the iterative scan dead entries no longer empty the pool, but the scan
walks past each one, so they still cost time.

- **Every run reports `updated=`** next to `kept=` (per source and in the
  `--dry-run` totals as `would update=`). A run leaves about
  `deleted + updated` dead tuples.
- **`scripts.reindex_knowledge` checks after a real run.** It logs
  `knowledge_chunks`' `n_live_tup` / `n_dead_tup` and, when either that count
  or the run's own `deleted + updated` is at least `VACUUM_DEAD_TUPLES`
  (1,000), runs `VACUUM (ANALYZE) knowledge_chunks` itself on an `AUTOCOMMIT`
  connection (VACUUM cannot run in a transaction). A failed vacuum is logged,
  never fails the sweep. `--dry-run` never vacuums. Background reindexes
  (`schedule_reindex`, `reindex_claims`) do not vacuum: they write a few rows,
  and autovacuum triggers at 50 + 0.2 × live rows (about 2,870 for 14k rows).
  To vacuum by hand: `VACUUM (ANALYZE) knowledge_chunks;`. `n_dead_tup` is an
  estimate.
- **Check:** `SELECT n_live_tup, n_dead_tup, last_autovacuum, last_vacuum
  FROM pg_stat_user_tables WHERE relname = 'knowledge_chunks';`
- **Check the effect:** run the selection eval twice with distinct labels,
  once live and once with an exact vector scan:
  `python -m scripts.eval_rag selection --read-only-prod --variants
  rules15-replace --no-rewrite --label <x>-live`, then the same with
  `PGOPTIONS='-c enable_indexscan=off -c enable_bitmapscan=off'` in the
  environment and `--label <x>-exact`. Without distinct labels the second run
  overwrites the first (`<date>-selection-<label>.{md,json}`, no overwrite
  check). If their "Abstain floor" sections disagree, the index is starving
  the pool, and the numbers are not a floor signal.

### Sentence-Less Chunks Stop Being Retried After 3 Attempts (#712)
A chunk whose situating call (#697) returned no sentence carries
`meta["context_attempts"]` / `meta["context_failed_at"]`. After
`context.MAX_ATTEMPTS` (3) per content hash the indexer stops asking and the
article is `unchanged`, so a chunk without a sentence is not necessarily
"pending": check the count before blaming a missed reindex. Editing the chunk
(new hash) starts over; `python -m scripts.reindex_knowledge --all
--retry-context` asks again for the capped ones. Every sweep, and
`--dry-run`, logs the sentence-less total and how many are out of attempts. A
`BudgetExceededError` is not counted. Sentences are keyed by hash only, so
identical chunks share one (`docs/design/rag-knowledge-layer.md`).

### EN/ES Twins: `chunk_index` Mis-Pairs, `meta["twin_key"]` Is Measured Only (#713)
Each language is packed by estimated tokens and Spanish runs longer, so the
same `chunk_index` holds different paragraphs in 52 of 113 bilingual articles
(2026-10-13): 303 of 1,291 index pairs are not translations. Ask PIC's
language dedup (`ask_pic._passages`) still pairs by `chunk_index`
(`PassageSelection.twin_by_index=True`). The #713 measurement of the fix
(`docs/portfolio/rag/results/2026-10-13-twin-key-713.md`) showed no gain in
the answers eval, and the ticket kept the switch only on a gain.

- **The fix is built and stored.** The chunker writes `meta["twin_key"]`: a
  fingerprint of the body's shape (every section's paragraph count), the
  section index and the paragraph span the chunk packs (`chunker.twin_key`).
  Equal keys in one article mean the same source paragraphs; an article whose
  languages differ in shape never pairs (2 of 113). `twin_by_index=False`
  (eval variant `rules15-replace-twin-key`, `answers --no-twin-by-index`)
  pairs by it, and a chunk without a key is never deduplicated. Switching the
  endpoint is a one-line default change, after a new measurement.
- **Writing the key is a meta-only reindex.** `indexer._changed_kept`
  compares `meta`, so `reindex_knowledge --all` rewrites every kept row's `meta` in
  place: no Voyage or Haiku call, sentences kept, one `UPDATE` per row; the
  sweep then vacuums (above). A change to the packing or
  the span format changes every key the same way.
- To measure against a database indexed before the key, the eval takes
  `--derive-twin-keys` (`scripts/eval_rag/twin_keys.py`): it re-chunks each
  retrieved article in memory and copies the key onto the hit with the same
  (language, content). Read-only.
