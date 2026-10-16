"""Measure how consistently the ES articles render the glossary's terms (#633, epic #618).

For every entry of the committed glossary TSV (``scripts.glossary_tsv``) and
every published article (APPROVED, ``published_at <= now()``, both bodies
present) whose English body mentions the term, it counts the English mentions,
the canonical Spanish rendering and each listed variant in the Spanish body.
A term's **canonical rate** is ``sum(min(canonical, en_mentions)) /
sum(en_mentions)`` over those articles — the share of English mentions matched
by a canonical Spanish one. Reported per term, micro-averaged over all
mentions and macro-averaged over terms.

Two modes:

- ``before`` (default): the stored Spanish bodies. No DeepL call; read-only.
- ``after``: re-translates a seeded sample of those articles' English bodies
  with the glossary on (``TranslationService`` with ``DEEPL_GLOSSARY_ID``) and
  reports the sample's stored ES (before) next to the re-translation (after).
  DeepL bills per character, so the sample stops at ``--max-chars`` English
  characters and the estimated cost is printed first; ``--dry-run`` prints the
  sample and the estimate without calling DeepL. Writes
  ``docs/portfolio/rag/results/<date>-glossary.{md,json}`` (or ``--out``).

Read-only: a ``postgresql_readonly`` session, materialised to plain tuples,
rolled back and closed before any DeepL call; nothing is added or committed.

Usage (from ``backend/``):
    python -m scripts.eval_glossary                              # before, stdout only
    python -m scripts.eval_glossary --out /tmp/glossary-before.md
    python -m scripts.eval_glossary --mode after --max-chars 50000 --dry-run
    python -m scripts.eval_glossary --mode after --max-chars 50000   # paid DeepL call (#664)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.models.article import Article
from app.models.enums import ContentStatus
from sqlalchemy import func
from sqlalchemy.orm import Session

from scripts.glossary_tsv import (
    GLOSSARY_TSV,
    GlossaryEntry,
    GlossaryFormatError,
    count_term,
    load_glossary_tsv,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "portfolio" / "rag" / "results"
DEFAULT_MAX_CHARS = 50_000
DEFAULT_SEED = 633
# DeepL API Pro usage price, USD per million characters (list price; the
# estimate is a ceiling for planning, not billing).
DEEPL_USD_PER_MILLION_CHARS = 25.0


@dataclass(frozen=True)
class EvalDoc:
    id: int
    en: str
    es: str


def load_published(db: Session) -> list[EvalDoc]:
    rows = (
        db.query(Article.id, Article.content, Article.content_es)
        .filter(
            Article.status == ContentStatus.APPROVED.value,
            Article.published_at.isnot(None),
            Article.published_at <= func.now(),
            Article.content.isnot(None),
            Article.content_es.isnot(None),
        )
        .order_by(Article.id)
        .all()
    )
    return [EvalDoc(r.id, r.content, r.content_es) for r in rows]


def measure(entries: list[GlossaryEntry], docs: list[EvalDoc]) -> dict:
    """Per-term and aggregate canonical-rendering metrics over ``docs``."""
    terms = []
    total_mentions = total_canonical = 0
    for e in entries:
        mentions = canonical = articles = articles_canonical = 0
        variants = dict.fromkeys(e.variants, 0)
        for d in docs:
            n_en = count_term(e.en, d.en)
            if not n_en:
                continue
            n_es = count_term(e.es, d.es)
            articles += 1
            mentions += n_en
            canonical += min(n_es, n_en)
            articles_canonical += 1 if n_es else 0
            for v in variants:
                variants[v] += count_term(v, d.es)
        total_mentions += mentions
        total_canonical += canonical
        terms.append(
            {
                "en": e.en,
                "es": e.es,
                "articles": articles,
                "en_mentions": mentions,
                "canonical": canonical,
                "canonical_rate": round(canonical / mentions, 4) if mentions else None,
                "articles_with_canonical": articles_canonical,
                "variants": variants,
            }
        )
    # Unrounded per-term rates, so the macro average is not a mean of roundings.
    rated = [t["canonical"] / t["en_mentions"] for t in terms if t["en_mentions"]]
    return {
        "articles": len(docs),
        "terms": terms,
        "terms_with_mentions": len(rated),
        "en_mentions": total_mentions,
        "canonical": total_canonical,
        "micro_rate": round(total_canonical / total_mentions, 4) if total_mentions else None,
        "macro_rate": round(sum(rated) / len(rated), 4) if rated else None,
    }


def select_sample(docs: list[EvalDoc], *, max_chars: int, seed: int) -> list[EvalDoc]:
    """Seeded random docs, added while the English total stays within ``max_chars``."""
    order = list(docs)
    random.Random(seed).shuffle(order)
    out, used = [], 0
    for d in order:
        if used + len(d.en) > max_chars:
            continue
        out.append(d)
        used += len(d.en)
    return sorted(out, key=lambda d: d.id)


def estimate_deepl_usd(chars: int) -> float:
    return round(chars * DEEPL_USD_PER_MILLION_CHARS / 1e6, 4)


def retranslate(docs: list[EvalDoc], service) -> list[EvalDoc]:
    """The sample's English bodies through ``translate_markdown`` (paid)."""

    async def run():
        return [EvalDoc(d.id, d.en, await service.translate_markdown(d.en)) for d in docs]

    return asyncio.run(run())


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def render_markdown(report: dict) -> str:
    meta = report["meta"]
    lines = [
        f"# Glossary consistency ({meta['mode']}) — {meta['date']}",
        "",
        f"Glossary: {meta['entries']} entries from `{meta['tsv']}`.",
        "",
    ]
    runs = [("before", report["before"])]
    if report.get("after"):
        runs.append(("after", report["after"]))
        lines += [
            f"Sample: {meta['sample_articles']} articles, {meta['sample_chars']} EN characters "
            f"(seed {meta['seed']}, est. ${meta['est_deepl_usd']:.2f}).",
            "",
        ]
    lines += ["| Run | Articles | EN mentions | Canonical | Micro rate | Macro rate |"]
    lines += ["|---|---|---|---|---|---|"]
    for name, m in runs:
        lines.append(
            f"| {name} | {m['articles']} | {m['en_mentions']} | {m['canonical']} | "
            f"{_pct(m['micro_rate'])} | {_pct(m['macro_rate'])} |"
        )
    lines += ["", "| Term | ES | " + " | ".join(f"{n} rate" for n, _ in runs) + " | Variants |"]
    lines += ["|---|---|" + "---|" * len(runs) + "---|"]
    after_terms = {t["en"]: t for t in report["after"]["terms"]} if report.get("after") else {}
    for t in report["before"]["terms"]:
        rates = [_pct(t["canonical_rate"])]
        if after_terms:
            rates.append(_pct(after_terms[t["en"]]["canonical_rate"]))
        variants = ", ".join(f"{v}: {n}" for v, n in t["variants"].items()) or "—"
        lines.append(f"| {t['en']} | {t['es']} | " + " | ".join(rates) + f" | {variants} |")
    return "\n".join(lines) + "\n"


def write_report(report: dict, out: Path) -> tuple[Path, Path]:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(report), encoding="utf-8")
    json_path = out.with_suffix(".json")
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return out, json_path


def _readonly_session() -> Session:
    from app.core.database import engine

    return Session(bind=engine.execution_options(postgresql_readonly=True), autoflush=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("before", "after"), default="before")
    parser.add_argument("--tsv", default=str(GLOSSARY_TSV))
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", help="Markdown path (JSON alongside)")
    parser.add_argument("--dry-run", action="store_true", help="after: sample + estimate only")
    return parser


def main(argv: list[str] | None = None, *, service_factory=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        entries = load_glossary_tsv(Path(args.tsv))
    except (OSError, GlossaryFormatError) as e:
        print(f"Cannot load {args.tsv}: {e}")
        return 2
    if not entries:
        print(f"{args.tsv} has no entries; curate it first (#664).")
        return 2

    db = _readonly_session()
    try:
        docs = load_published(db)
    finally:
        db.rollback()  # read-only; release the connection before any DeepL call
        db.close()

    date = f"{datetime.now(UTC):%Y-%m-%d}"
    meta = {"mode": args.mode, "date": date, "tsv": args.tsv, "entries": len(entries)}
    if args.mode == "before":
        report = {"meta": meta, "before": measure(entries, docs)}
        print(render_markdown(report))
        if args.out:
            md, js = write_report(report, Path(args.out))
            print(f"Wrote {md} and {js}")
        return 0

    sample = select_sample(docs, max_chars=args.max_chars, seed=args.seed)
    chars = sum(len(d.en) for d in sample)
    meta.update(
        seed=args.seed,
        sample_articles=len(sample),
        sample_chars=chars,
        sample_ids=[d.id for d in sample],
        est_deepl_usd=estimate_deepl_usd(chars),
    )
    print(
        f"Sample: {len(sample)} articles {meta['sample_ids']}, {chars} EN chars, "
        f"est. DeepL ${meta['est_deepl_usd']:.2f}"
    )
    if args.dry_run:
        print("Dry run: no DeepL call.")
        return 0

    from app.core.config import get_settings
    from app.services.translation import TranslationService

    settings = get_settings()
    if not settings.deepl_api or not settings.deepl_glossary_id:
        print("DEEPL_API and DEEPL_GLOSSARY_ID must both be set for --mode after.")
        return 2
    factory = service_factory or (
        lambda: TranslationService(
            settings.deepl_api, glossary_enabled=True, glossary_id=settings.deepl_glossary_id
        )
    )
    after_docs = retranslate(sample, factory())
    report = {
        "meta": meta,
        "before": measure(entries, sample),
        "after": measure(entries, after_docs),
    }
    print(render_markdown(report))
    out = Path(args.out) if args.out else DEFAULT_OUT_DIR / f"{date}-glossary.md"
    md, js = write_report(report, out)
    print(f"Wrote {md} and {js}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
