"""Draft EN→ES glossary candidates from the bilingual article corpus (#633, epic #618).

Writes a **draft for operator curation** — never the committed glossary
(``app/data/deepl_glossary_en_es.tsv``, see ``scripts.glossary_tsv``). Three
stages:

1. **Extract (no model).** Every APPROVED article with both an English and a
   Spanish body is read (title + body, markdown stripped). Candidate terms are
   maximal runs of capitalised words, allowing ``of`` / ``the`` / ``de`` /
   ``del`` … inside a run (``Bay of Panama``, ``Casco Antiguo``), minus a
   stoplist of sentence-initial function words, kept when they recur across
   ``--min-docs`` articles; a short list of known Panama terms (``KNOWN_TERMS``)
   is always considered.
2. **Align evidence (no model).** For each term, the Spanish capitalised runs
   that co-occur with it at article level are ranked by Jaccard overlap of
   their article sets, plus the count of the term verbatim in Spanish (the
   do-not-translate signal).
3. **Propose (Sonnet, ``feature="glossary_candidates"``).** Batches of terms
   with that evidence go to Sonnet through ``create_message``; it returns one
   canonical Spanish rendering per term (or do-not-translate), the variants it
   saw, and a drop suggestion for non-terms. Malformed entries — unknown term,
   missing ``es``, a batch that is not JSON — are dropped and counted, never
   guessed at.

The draft TSV then counts every proposed rendering over the Spanish versions of
the articles that use the term and attaches example snippets. Its ``status``
column is blank for the curator (``keep`` / ``edit`` / ``drop``); curated
``keep``/``edit`` rows become ``en<TAB>es<TAB>variants`` lines of the committed
TSV (#664).

Read-only by construction: a ``postgresql_readonly`` session, materialised to
plain tuples, rolled back and closed before any model call; nothing is added or
committed.

Usage (from ``backend/``):
    python -m scripts.glossary_candidates --dry-run          # extract + cost estimate, no model
    python -m scripts.glossary_candidates --out /tmp/glossary-candidates.tsv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.models.article import Article
from app.models.enums import ContentStatus
from app.services.llm import SONNET_MODEL, create_message, parse_llm_json
from sqlalchemy.orm import Session

from scripts.glossary_tsv import count_term, term_pattern

logger = logging.getLogger(__name__)

FEATURE = "glossary_candidates"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "ai_generated_content" / "glossary" / "deepl_glossary_candidates.tsv"
DEFAULT_MIN_DOCS = 2
DEFAULT_MAX_TERMS = 400
DEFAULT_BATCH_SIZE = 40
ES_CANDIDATES_PER_TERM = 8
MAX_TOKENS = 16000
SONNET_INPUT_PRICE_PER_MTOK = 3.0
SONNET_OUTPUT_PRICE_PER_MTOK = 15.0
_CHARS_PER_TOKEN = 3
SNIPPET_RADIUS = 60

# Always considered (when they occur at all), whatever their frequency.
KNOWN_TERMS = (
    "Canal Zone",
    "Casco Antiguo",
    "Casco Viejo",
    "Panama Canal",
    "Panama Railroad",
    "Panama Viejo",
    "Isthmus of Panama",
    "Guna",
    "Guna Yala",
    "Kuna",
    "Ngäbe",
    "Ngäbe-Buglé",
    "Emberá",
    "Wounaan",
    "Naso",
    "Bribri",
    "Darién",
    "Camino Real",
    "Camino de Cruces",
    "Zonian",
)

# Allowed *inside* a run only. "and" / "y" are deliberately absent: they join
# two names ("Spain and France"), not the words of one.
_CONNECTORS = frozenset("of the de del la las los el da do".split())
_STOPWORDS = frozenset(
    """a an the this that these those it its in on at by for from to of and or but as
    with after before during when while where there here then however although though
    yet so if because since until unlike like despite many most some several such
    each every both all any one two three four five six seven eight nine ten first
    second third his her their our we they he she i you what which who whom whose
    how why not no yes also even only still today now later early late chapter part
    january february march april may june july august september october november
    december monday tuesday wednesday thursday friday saturday sunday
    el la los las un una unos unas este esta estos estas ese esa en con por para
    desde hasta pero sin sobre entre cuando donde como también sus su lo al del
    durante tras aunque mientras parte capítulo""".split()
)
_WORD = r"[A-ZÁÉÍÓÚÑÜ][\w'’\-]*"
_CONNECTOR_RE = "|".join(sorted(_CONNECTORS, key=len, reverse=True))
_RUN_RE = re.compile(rf"(?<![\w'’\-]){_WORD}(?:[ \t]+(?:(?:{_CONNECTOR_RE})[ \t]+){{0,2}}{_WORD})*")


@dataclass(frozen=True)
class CorpusDoc:
    id: int
    en: str
    es: str


@dataclass
class Candidate:
    term: str
    en_docs: set[int] = field(default_factory=set)
    en_count: int = 0
    es_verbatim: int = 0
    es_candidates: list[dict] = field(default_factory=list)
    en_example: str = ""


@dataclass(frozen=True)
class Proposal:
    term: str
    es: str
    do_not_translate: bool
    variants: tuple[str, ...]
    drop: bool
    note: str


# --- corpus ---------------------------------------------------------------


def strip_markdown(text: str | None) -> str:
    """Plain prose from stored markdown: no code, URLs, images or markup."""
    if not text:
        return ""
    text = re.sub(r"```.*?```|~~~.*?~~~", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_#>|]", " ", text)
    return text


def load_corpus(db: Session) -> list[CorpusDoc]:
    """APPROVED articles with both language bodies, as detached plain rows."""
    rows = (
        db.query(Article.id, Article.title, Article.content, Article.title_es, Article.content_es)
        .filter(
            Article.status == ContentStatus.APPROVED.value,
            Article.content.isnot(None),
            Article.content_es.isnot(None),
        )
        .order_by(Article.id)
        .all()
    )
    return [
        CorpusDoc(
            r.id,
            strip_markdown(f"{r.title or ''}.\n{r.content}"),
            strip_markdown(f"{r.title_es or ''}.\n{r.content_es}"),
        )
        for r in rows
    ]


# --- extraction -----------------------------------------------------------


def _trim_run(run: str) -> str | None:
    words = run.split()
    while words and (words[0].lower() in _STOPWORDS or words[0].lower() in _CONNECTORS):
        words.pop(0)
    while words and words[-1].lower() in _CONNECTORS:
        words.pop()
    if not words:
        return None
    term = " ".join(words)
    if len(words) == 1 and (len(term) < 3 or term.lower() in _STOPWORDS):
        return None
    if any(ch.isdigit() for ch in term):
        return None
    return term


def capitalised_runs(text: str) -> Counter:
    """Counter of trimmed capitalised runs in ``text``."""
    out: Counter = Counter()
    for m in _RUN_RE.finditer(text):
        term = _trim_run(m.group(0))
        if term:
            out[term] += 1
    return out


def snippet(text: str, term: str, radius: int = SNIPPET_RADIUS) -> str:
    m = term_pattern(term).search(text)
    if not m:
        return ""
    start, end = max(0, m.start() - radius), min(len(text), m.end() + radius)
    return " ".join(text[start:end].split())


def extract_candidates(
    docs: list[CorpusDoc],
    *,
    min_docs: int = DEFAULT_MIN_DOCS,
    max_terms: int = DEFAULT_MAX_TERMS,
) -> list[Candidate]:
    """Recurring EN capitalised runs (+ KNOWN_TERMS) with their ES alignment evidence."""
    en_runs = {d.id: capitalised_runs(d.en) for d in docs}
    es_runs = {d.id: capitalised_runs(d.es) for d in docs}
    by_id = {d.id: d for d in docs}

    term_docs: dict[str, set[int]] = defaultdict(set)
    term_count: Counter = Counter()
    # Case-variants of one run ("Canal Zone" / "Canal zone" at a line start) merge.
    display: dict[str, str] = {}
    for doc_id, runs in en_runs.items():
        for term, n in runs.items():
            key = term.casefold()
            display.setdefault(key, term)
            term_docs[key].add(doc_id)
            term_count[key] += n
    for known in KNOWN_TERMS:
        key = known.casefold()
        hits = {d.id for d in docs if count_term(known, d.en)}
        if hits:
            display[key] = known
            term_docs[key] |= hits
            term_count[key] = max(term_count[key], sum(count_term(known, d.en) for d in docs))

    known_keys = {k.casefold() for k in KNOWN_TERMS}
    keys = [k for k in term_docs if len(term_docs[k]) >= min_docs or k in known_keys]
    keys.sort(key=lambda k: (k not in known_keys, -len(term_docs[k]), -term_count[k], k))
    keys = keys[:max_terms]

    es_df: Counter = Counter()
    for runs in es_runs.values():
        es_df.update(runs.keys())

    out: list[Candidate] = []
    for key in keys:
        term = display[key]
        dset = term_docs[key]
        cooc: Counter = Counter()
        mentions: Counter = Counter()
        for doc_id in dset:
            cooc.update(es_runs[doc_id].keys())
            mentions.update(es_runs[doc_id])
        scored = []
        for g, inter in cooc.most_common(50):
            union = len(dset) + es_df[g] - inter
            scored.append((inter / union if union else 0.0, g, inter))
        scored.sort(key=lambda s: (-s[0], -s[2], s[1]))
        example_doc = next((by_id[i] for i in sorted(dset) if snippet(by_id[i].en, term)), None)
        out.append(
            Candidate(
                term=term,
                en_docs=set(dset),
                en_count=term_count[key],
                es_verbatim=sum(count_term(term, by_id[i].es) for i in dset),
                es_candidates=[
                    {"text": g, "docs": inter, "mentions": mentions[g], "jaccard": round(j, 2)}
                    for j, g, inter in scored[:ES_CANDIDATES_PER_TERM]
                ],
                en_example=snippet(example_doc.en, term) if example_doc else "",
            )
        )
    return out


# --- model ----------------------------------------------------------------

SYSTEM_PROMPT = """You help build an English->Spanish (Latin American, Panama) DeepL glossary \
for a Panama history and tourism site. For each English term you get the number of articles \
using it, an English example, how often the term appears verbatim in the Spanish versions, and \
the Spanish capitalised phrases that co-occur with it across the same articles (ranked by \
article-set overlap; the Spanish versions were machine-translated, so they may be inconsistent).

For each term decide the canonical Spanish rendering a Panamanian editor would use:
- proper names, places, institutions, historical names and domain terms get one canonical form;
- names already Spanish or indigenous (e.g. Casco Antiguo, Guna Yala, Ngäbe) are \
"do_not_translate": the Spanish equals the English term exactly;
- set "drop": true for things that are not glossary material (sentence fragments, generic words, \
people's names that never change, terms whose correct rendering depends on context).
Use the corpus evidence; when the evidence shows several renderings, pick the best one and list \
the others under "variants" (exact strings as they appear in Spanish).

Answer with ONLY a JSON array, one object per input term, in this shape:
[{"term": "<the English term exactly as given>", "es": "<canonical Spanish>", \
"do_not_translate": false, "variants": ["<other rendering>"], "drop": false, "note": "<short>"}]"""


def batch_payload(batch: list[Candidate]) -> str:
    return json.dumps(
        [
            {
                "term": c.term,
                "articles": len(c.en_docs),
                "en_example": c.en_example,
                "verbatim_in_spanish": c.es_verbatim,
                "spanish_cooccurring": c.es_candidates,
            }
            for c in batch
        ],
        ensure_ascii=False,
    )


def batches(cands: list[Candidate], size: int) -> list[list[Candidate]]:
    return [cands[i : i + size] for i in range(0, len(cands), size)]


def estimate_cost(cands: list[Candidate], batch_size: int) -> dict:
    groups = batches(cands, batch_size)
    chars = sum(len(SYSTEM_PROMPT) + len(batch_payload(b)) for b in groups)
    input_tokens = chars // _CHARS_PER_TOKEN
    return {
        "calls": len(groups),
        "input_tokens_est": input_tokens,
        "cost_usd_min": round(input_tokens * SONNET_INPUT_PRICE_PER_MTOK / 1e6, 4),
        "cost_usd_max": round(
            input_tokens * SONNET_INPUT_PRICE_PER_MTOK / 1e6
            + len(groups) * MAX_TOKENS * SONNET_OUTPUT_PRICE_PER_MTOK / 1e6,
            4,
        ),
    }


def _as_str(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_proposals(data, batch_terms: list[str]) -> tuple[list[Proposal], int]:
    """Validate the model's array; returns (proposals, dropped_count).

    An entry is dropped when it is not an object, names a term outside the
    batch (or one already answered), or has no ``es`` (unless do-not-translate).
    """
    if isinstance(data, dict) and isinstance(data.get("terms"), list):
        data = data["terms"]
    if not isinstance(data, list):
        return [], len(batch_terms)
    wanted = {t.casefold(): t for t in batch_terms}
    seen: set[str] = set()
    out: list[Proposal] = []
    dropped = 0
    for item in data:
        if not isinstance(item, dict):
            dropped += 1
            continue
        key = _as_str(item.get("term")).casefold()
        if key not in wanted or key in seen:
            dropped += 1
            continue
        term = wanted[key]
        dnt = item.get("do_not_translate") is True
        es = term if dnt else _as_str(item.get("es"))
        if not es or "\t" in es or "\n" in es:
            dropped += 1
            continue
        raw_variants = item.get("variants")
        variants = tuple(
            dict.fromkeys(
                v.strip()
                for v in (raw_variants if isinstance(raw_variants, list) else [])
                if isinstance(v, str) and v.strip() and v.strip() != es and "\t" not in v
            )
        )
        seen.add(key)
        out.append(
            Proposal(
                term=term,
                es=es,
                do_not_translate=dnt or es == term,
                variants=variants,
                drop=item.get("drop") is True,
                note=" ".join(_as_str(item.get("note")).split()),
            )
        )
    return out, dropped


def propose(batch: list[Candidate]) -> tuple[list[Proposal], int]:
    """One Sonnet call for one batch; a call or parse failure drops the batch."""
    try:
        message = create_message(
            feature=FEATURE,
            model=SONNET_MODEL,
            max_tokens=MAX_TOKENS,
            output_config={"effort": "medium"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": batch_payload(batch)}],
        )
        data = parse_llm_json(message)
    except ValueError as e:
        logger.warning("glossary candidates: batch dropped (%s)", e)
        return [], len(batch)
    return parse_proposals(data, [c.term for c in batch])


# --- draft ----------------------------------------------------------------

DRAFT_COLUMNS = (
    "status",
    "term",
    "proposed_es",
    "do_not_translate",
    "variants",
    "en_articles",
    "en_mentions",
    "es_renderings",
    "es_examples",
    "en_example",
    "note",
)


def _clean(text: str) -> str:
    return " ".join((text or "").replace("\t", " ").split())


def draft_rows(
    cands: list[Candidate], proposals: list[Proposal], docs: list[CorpusDoc]
) -> list[dict]:
    """One row per proposal, with per-rendering counts over the term's ES articles."""
    by_term = {c.term: c for c in cands}
    by_id = {d.id: d for d in docs}
    rows = []
    for p in proposals:
        cand = by_term[p.term]
        es_texts = [by_id[i].es for i in sorted(cand.en_docs) if i in by_id]
        forms = list(dict.fromkeys((p.es, *p.variants)))
        counts = {f: sum(count_term(f, t) for t in es_texts) for f in forms}
        examples = []
        for f in forms:
            snip = next((s for t in es_texts if (s := snippet(t, f))), "")
            if snip:
                examples.append(f"[{f}] {snip}")
            if len(examples) == 2:
                break
        rows.append(
            {
                "status": "",
                "term": p.term,
                "proposed_es": p.es,
                "do_not_translate": "yes" if p.do_not_translate else "",
                "variants": "; ".join(p.variants),
                "en_articles": len(cand.en_docs),
                "en_mentions": cand.en_count,
                "es_renderings": "; ".join(f"{f}: {n}" for f, n in counts.items()),
                "es_examples": _clean(" | ".join(examples)),
                "en_example": _clean(cand.en_example),
                "note": _clean(("model suggests drop. " if p.drop else "") + p.note),
            }
        )
    return rows


def write_draft(rows: list[dict], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=DRAFT_COLUMNS, delimiter="\t", quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        writer.writerows(rows)
    return out


# --- cli ------------------------------------------------------------------


def _readonly_session() -> Session:
    from app.core.database import engine

    return Session(bind=engine.execution_options(postgresql_readonly=True), autoflush=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-docs", type=int, default=DEFAULT_MIN_DOCS)
    parser.add_argument("--max-terms", type=int, default=DEFAULT_MAX_TERMS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Draft TSV path")
    parser.add_argument("--dry-run", action="store_true", help="Extract and estimate only")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    db = _readonly_session()
    try:
        docs = load_corpus(db)
    finally:
        db.rollback()  # read-only; release the connection before any model call
        db.close()
    cands = extract_candidates(docs, min_docs=args.min_docs, max_terms=args.max_terms)
    est = estimate_cost(cands, args.batch_size)
    print(f"{len(docs)} bilingual APPROVED articles; {len(cands)} candidate terms.")
    print(", ".join(c.term for c in cands[:40]) + (" ..." if len(cands) > 40 else ""))
    print(
        f"Sonnet: {est['calls']} calls, ~{est['input_tokens_est']} input tokens, "
        f"est. ${est['cost_usd_min']:.2f}-${est['cost_usd_max']:.2f}"
    )
    if args.dry_run:
        print("Dry run: no model call, no draft written.")
        return 0
    proposals: list[Proposal] = []
    dropped = 0
    for batch in batches(cands, args.batch_size):
        got, bad = propose(batch)
        proposals.extend(got)
        dropped += bad
    rows = draft_rows(cands, proposals, docs)
    out = write_draft(rows, Path(args.out))
    print(
        f"{len(rows)} proposals ({dropped} malformed/missing dropped) -> {out} "
        f"({datetime.now(UTC):%Y-%m-%d %H:%M} UTC)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
