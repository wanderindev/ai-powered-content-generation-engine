"""Related-articles regression check (#697): snapshot and compare ``find_related``.

There is no related-articles eval; this replays the #629 comparison. ``snap``
writes the top-4 related articles of every public article (read-only) and
prints the same-category share, the max in-degree and the median centroid
similarity; ``diff`` compares two snapshots (overlap@4, unchanged top 1,
identical lists). Run it before and after a reindex.

Usage (from ``backend/``):
    python -m scripts.eval_rag.related_snapshot snap before.json
    python -m scripts.eval_rag.related_snapshot diff before.json after.json
"""

import json
import sys
from collections import Counter

from app.core.database import SessionLocal
from app.models.article import Article
from app.models.category import Category
from app.services.knowledge.related import find_related
from app.services.knowledge.retrieval import public_article_criteria
from sqlalchemy import select


def snapshot(path):
    db = SessionLocal()
    rows = db.execute(
        select(Article.id, Article.category_id)
        .join(Category, Category.id == Article.category_id)
        .where(*public_article_criteria())
    ).all()
    cats = {r.id: r.category_id for r in rows}
    out = {}
    for aid in sorted(cats):
        hits = find_related(db, aid) or []
        out[aid] = [[h.article_id, round(h.similarity, 4)] for h in hits]
    db.close()
    json.dump({"categories": cats, "related": out}, open(path, "w"))
    metrics(path)


def metrics(path):
    data = json.load(open(path))
    cats = {int(k): v for k, v in data["categories"].items()}
    rel = {int(k): v for k, v in data["related"].items()}
    pairs = [(a, h[0]) for a, hs in rel.items() for h in hs]
    same = sum(cats.get(a) == cats.get(b) for a, b in pairs) / max(len(pairs), 1)
    indeg = Counter(b for _, b in pairs)
    sims = sorted(h[1] for hs in rel.values() for h in hs)
    print(
        f"{path}: articles={len(rel)} pairs={len(pairs)} same_category={same:.3f} "
        f"max_in_degree={max(indeg.values())} median_sim={sims[len(sims) // 2]:.4f}"
    )


def diff(a, b):
    ra = {int(k): [h[0] for h in v] for k, v in json.load(open(a))["related"].items()}
    rb = {int(k): [h[0] for h in v] for k, v in json.load(open(b))["related"].items()}
    common = [k for k in ra if k in rb]
    overlap = sum(len(set(ra[k]) & set(rb[k])) for k in common) / sum(len(ra[k]) for k in common)
    top1 = sum(ra[k][:1] == rb[k][:1] for k in common) / len(common)
    identical = sum(ra[k] == rb[k] for k in common)
    print(f"overlap@4={overlap:.3f} top1_same={top1:.3f} identical_lists={identical}/{len(common)}")


if __name__ == "__main__":
    if sys.argv[1] == "snap":
        snapshot(sys.argv[2])
    else:
        metrics(sys.argv[2])
        metrics(sys.argv[3])
        diff(sys.argv[2], sys.argv[3])
