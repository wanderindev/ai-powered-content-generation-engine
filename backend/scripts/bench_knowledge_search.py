"""Benchmark knowledge retrieval latency (#626, epic #617).

Runs :func:`app.services.knowledge.retrieval.search` over a set of bilingual
Panama-history queries and prints p50 / p95 per stage (embed, vector, fts,
fuse, rerank) and in total, in milliseconds. The ticket's acceptance bar is
hybrid without rerank under 300 ms p95 on the prod corpus.

**Read-only.** It issues SELECTs only (the session is rolled back at the end)
and its only external calls are Voyage query embeddings (and reranks with
``--rerank``), which cost fractions of a cent. Every run embeds again, so the
embed column is real network latency, not a cache hit.

Usage (from ``backend/``):
    python -m scripts.bench_knowledge_search                       # hybrid, 3 runs per query
    python -m scripts.bench_knowledge_search --mode fts --runs 5
    python -m scripts.bench_knowledge_search --queries my_queries.txt --rerank
    python -m scripts.bench_knowledge_search --visibility internal

``--queries FILE`` takes one query per line (blank lines and ``#`` comments
are skipped).
"""

import argparse
import logging
import math
import sys
from pathlib import Path

from app.core.database import SessionLocal
from app.services.knowledge import retrieval

STAGES = ("embed", "vector", "fts", "fuse", "rerank", "total")

DEFAULT_QUERIES = [
    "Who built the Panama Railroad?",
    "¿Quién construyó el ferrocarril de Panamá?",
    "Why did the French canal effort fail?",
    "¿Por qué fracasó el canal francés?",
    "Henry Morgan sack of Panama Viejo 1671",
    "saqueo de Panamá Viejo por Henry Morgan",
    "separation of Panama from Colombia in 1903",
    "separación de Panamá de Colombia",
    "yellow fever and malaria during canal construction",
    "fiebre amarilla durante la construcción del canal",
    "Portobelo fairs and the Spanish treasure fleet",
    "ferias de Portobelo",
    "Martyrs' Day January 9 1964",
    "Día de los Mártires",
    "Torrijos-Carter Treaties",
    "tratados Torrijos-Carter",
    "Guna Yala revolution of 1925",
    "revolución tule",
    "Vasco Núñez de Balboa sighting the Pacific",
    "Casco Antiguo colonial churches",
]


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    index = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return ordered[index]


def _load_queries(path: str | None) -> list[str]:
    if not path:
        return DEFAULT_QUERIES
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queries", help="File with one query per line")
    parser.add_argument("--runs", type=int, default=3, help="Runs per query (default 3)")
    parser.add_argument("--mode", choices=retrieval.MODES, default="hybrid")
    parser.add_argument("--rerank", action="store_true", help="Also time the Voyage rerank")
    parser.add_argument("--visibility", choices=retrieval.VISIBILITIES, default="public")
    parser.add_argument("--k", type=int, default=retrieval.DEFAULT_K)
    args = parser.parse_args(argv)

    # The per-call INFO line would drown the table.
    logging.getLogger("app").setLevel(logging.WARNING)

    queries = _load_queries(args.queries)
    if not queries:
        print("No queries.", file=sys.stderr)
        return 1

    samples: dict[str, list[float]] = {stage: [] for stage in STAGES}
    degraded: set[str] = set()
    empty = 0
    db = SessionLocal()
    try:
        # One untimed warm-up so connection setup is not in the numbers.
        retrieval.search(db, queries[0], mode=args.mode, visibility=args.visibility, k=args.k)
        db.rollback()
        for _ in range(max(1, args.runs)):
            for query in queries:
                result = retrieval.search(
                    db,
                    query,
                    mode=args.mode,
                    rerank=args.rerank,
                    visibility=args.visibility,
                    k=args.k,
                )
                db.rollback()  # read-only; also releases the connection between calls
                degraded.update(result.degraded)
                empty += not result.results
                for stage in STAGES:
                    if stage in result.timings_ms:
                        samples[stage].append(result.timings_ms[stage])
    finally:
        db.rollback()
        db.close()

    calls = len(samples["total"])
    print(
        f"mode={args.mode} rerank={args.rerank} visibility={args.visibility} "
        f"queries={len(queries)} runs={args.runs} calls={calls} empty_results={empty}"
    )
    if degraded:
        print(f"DEGRADED: {', '.join(sorted(degraded))}")
    print(f"{'stage':<8} {'n':>5} {'p50 ms':>9} {'p95 ms':>9} {'max ms':>9}")
    for stage in STAGES:
        values = samples[stage]
        if not values:
            continue
        print(
            f"{stage:<8} {len(values):>5} {_percentile(values, 50):>9.1f} "
            f"{_percentile(values, 95):>9.1f} {max(values):>9.1f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
