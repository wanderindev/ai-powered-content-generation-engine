"""The EN→ES DeepL glossary file and the term-matching helpers (#633, epic #618).

``app/data/deepl_glossary_en_es.tsv`` is the curated source of truth: a header
row ``en<TAB>es<TAB>variants`` then one entry per line. ``en`` / ``es`` become
the DeepL glossary entry (``es == en`` is a do-not-translate entry, e.g. Casco
Antiguo); the optional ``variants`` column (``;``-separated) lists the
non-canonical ES renderings the corpus uses, and is read only by
``scripts.eval_glossary`` — it is never sent to DeepL. Blank lines and lines
starting with ``#`` are ignored.

Shared by ``scripts.sync_deepl_glossary`` and ``scripts.eval_glossary``; the
draft that ``scripts.glossary_candidates`` writes for curation is a different
(wider) file. ``--from-draft`` turns a curated draft into this file (#664):
rows whose ``status`` is ``keep`` or ``edit`` (case-insensitive) become entries
from their ``term`` / ``proposed_es`` / ``variants`` cells, ``drop`` rows are
left out, and a blank or unknown status is refused, so an uncurated draft
cannot be committed by accident. The draft keeps no original proposal, so an
in-place edit marked ``keep`` is counted as kept.

Usage (from ``backend/``):
    python -m scripts.glossary_tsv --from-draft <draft.tsv> --dry-run   # counts only
    python -m scripts.glossary_tsv --from-draft <draft.tsv>             # write app/data TSV
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

GLOSSARY_TSV = Path(__file__).resolve().parents[1] / "app" / "data" / "deepl_glossary_en_es.tsv"
HEADER = ("en", "es", "variants")


class GlossaryFormatError(ValueError):
    """The glossary TSV is malformed (reported with its line number)."""


@dataclass(frozen=True)
class GlossaryEntry:
    en: str
    es: str
    variants: tuple[str, ...] = field(default=())

    @property
    def do_not_translate(self) -> bool:
        return self.en == self.es


def parse_glossary_tsv(text: str) -> list[GlossaryEntry]:
    """Parse the glossary TSV; raises ``GlossaryFormatError`` on a bad line.

    Rejects an empty term, a duplicate ``en`` (DeepL keys entries by source
    term, so a duplicate would silently keep one of them) and leading/trailing
    whitespace inside a cell, which DeepL also rejects.
    """
    entries: list[GlossaryEntry] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = line.split("\t")
        if tuple(c.strip().lower() for c in cells[:2]) == HEADER[:2]:
            continue
        if len(cells) < 2 or len(cells) > 3:
            raise GlossaryFormatError(f"line {lineno}: expected 2 or 3 tab-separated columns")
        en, es = cells[0], cells[1]
        if not en.strip() or not es.strip():
            raise GlossaryFormatError(f"line {lineno}: empty term")
        if en != en.strip() or es != es.strip():
            raise GlossaryFormatError(f"line {lineno}: leading/trailing whitespace in a term")
        key = en.casefold()
        if key in seen:
            raise GlossaryFormatError(f"line {lineno}: duplicate term {en!r}")
        seen.add(key)
        variants = tuple(v.strip() for v in (cells[2] if len(cells) == 3 else "").split(";"))
        entries.append(GlossaryEntry(en, es, tuple(v for v in variants if v)))
    return entries


def load_glossary_tsv(path: Path = GLOSSARY_TSV) -> list[GlossaryEntry]:
    return parse_glossary_tsv(Path(path).read_text(encoding="utf-8"))


def term_pattern(term: str) -> re.Pattern:
    """Case-insensitive, word-bounded match for a (possibly multi-word) term."""
    body = r"\s+".join(re.escape(part) for part in term.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def count_term(term: str, text: str | None) -> int:
    if not text or not term.strip():
        return 0
    return len(term_pattern(term).findall(text))


# --- curated draft -> committed TSV (#664) ---------------------------------

CURATED_STATUSES = ("keep", "edit", "drop")


def entries_from_draft(rows: list[dict]) -> tuple[list[GlossaryEntry], Counter]:
    """Curated draft rows -> glossary entries plus a per-status count.

    Raises ``GlossaryFormatError`` on a blank/unknown status. The result is
    re-validated through ``parse_glossary_tsv`` so the draft obeys the same
    rules as the committed file (no empty term, no duplicate, no stray
    whitespace). A variant equal to the canonical form is dropped.
    """
    counts: Counter = Counter()
    lines = ["\t".join(HEADER)]
    for n, row in enumerate(rows, start=2):
        status = (row.get("status") or "").strip().casefold()
        if status not in CURATED_STATUSES:
            raise GlossaryFormatError(f"draft row {n}: status {row.get('status')!r} not curated")
        counts[status] += 1
        if status == "drop":
            continue
        en, es = row.get("term") or "", row.get("proposed_es") or ""
        variants = [v.strip() for v in (row.get("variants") or "").split(";")]
        variants = [v for v in dict.fromkeys(variants) if v and v != es]
        lines.append("\t".join((en, es, "; ".join(variants))))
    return parse_glossary_tsv("\n".join(lines) + "\n"), counts


def format_glossary_tsv(entries: list[GlossaryEntry]) -> str:
    out = ["\t".join(HEADER)]
    for e in entries:
        out.append("\t".join((e.en, e.es, "; ".join(e.variants)) if e.variants else (e.en, e.es)))
    return "\n".join(out) + "\n"


def read_draft(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the glossary TSV from a curated draft")
    parser.add_argument("--from-draft", required=True, help="Curated glossary_candidates draft")
    parser.add_argument("--out", default=str(GLOSSARY_TSV), help="Glossary TSV to write")
    parser.add_argument("--dry-run", action="store_true", help="Validate and count only")
    args = parser.parse_args(argv)
    try:
        entries, counts = entries_from_draft(read_draft(Path(args.from_draft)))
    except (OSError, GlossaryFormatError) as e:
        print(f"Cannot build from {args.from_draft}: {e}")
        return 2
    dnt = sum(1 for e in entries if e.do_not_translate)
    print(
        f"{sum(counts.values())} draft rows: keep {counts['keep']}, edit {counts['edit']}, "
        f"drop {counts['drop']} -> {len(entries)} entries ({dnt} do-not-translate)"
    )
    if args.dry_run:
        print("Dry run: nothing written.")
        return 0
    Path(args.out).write_text(format_glossary_tsv(entries), encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
