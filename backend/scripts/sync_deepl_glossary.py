"""Create (or replace) the EN→ES DeepL glossary from the committed TSV (#633).

Reads ``app/data/deepl_glossary_en_es.tsv`` (see ``scripts.glossary_tsv``),
validates it and, without ``--dry-run``, creates a DeepL glossary from its
``en`` / ``es`` columns and prints the new id. The script never edits ``.env``:
the operator sets ``DEEPL_GLOSSARY_ID=<id>`` (and ``DEEPL_GLOSSARY_ENABLED=true``)
on the droplet. DeepL v2 glossaries are immutable, so "replace" means create a
new one, then — only with ``--delete-previous`` — delete the old id (default:
the configured ``DEEPL_GLOSSARY_ID``) after the new one exists.

ES-419: glossaries are language-level, not regional. The SDK strips a regional
variant at creation (``ES-419`` -> ``ES``), and ``translate_text`` accepts an
``ES`` glossary with an ``ES-419`` target (it compares the target with its
regional variant removed). So the glossary is created EN→ES and the service
keeps translating to ``ES-419``.

Glossary management calls are not billed per character. ``--dry-run`` makes at
most one call — ``get_glossary_languages()`` (read-only, free), and only when
``DEEPL_API`` is set — to print the supported pairs and confirm EN→ES is one.

Usage (from ``backend/``; on the droplet ``docker exec pic-backend python -m ...``):
    python -m scripts.sync_deepl_glossary --dry-run
    python -m scripts.sync_deepl_glossary                      # create, print id
    python -m scripts.sync_deepl_glossary --delete-previous    # create, then delete the old id
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import deepl

from scripts.glossary_tsv import GLOSSARY_TSV, GlossaryFormatError, load_glossary_tsv

SOURCE_LANG = "EN"
TARGET_LANG = "ES"  # glossaries are language-level; applies to the ES-419 target


def supported_pairs(translator) -> list[tuple[str, str]]:
    """The free ``get_glossary_languages()`` call, as upper-cased pairs."""
    return [
        (p.source_lang.upper(), p.target_lang.upper()) for p in translator.get_glossary_languages()
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tsv", default=str(GLOSSARY_TSV), help="Glossary TSV path")
    parser.add_argument("--name", default=None, help="Glossary name (default pic-en-es-<date>)")
    parser.add_argument(
        "--delete-previous",
        nargs="?",
        const="",
        default=None,
        metavar="ID",
        help="After creating, delete this glossary id (default: DEEPL_GLOSSARY_ID)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and print only")
    return parser


def main(argv: list[str] | None = None, *, translator_factory=deepl.Translator) -> int:
    args = build_parser().parse_args(argv)
    from app.core.config import get_settings

    settings = get_settings()
    try:
        entries = load_glossary_tsv(Path(args.tsv))
    except (OSError, GlossaryFormatError) as e:
        print(f"Cannot load {args.tsv}: {e}")
        return 2
    dnt = sum(1 for e in entries if e.do_not_translate)
    print(f"{args.tsv}: {len(entries)} entries ({dnt} do-not-translate)")
    for e in entries[:20]:
        print(f"  {e.en} -> {e.es}")
    if len(entries) > 20:
        print(f"  ... {len(entries) - 20} more")

    translator = translator_factory(settings.deepl_api) if settings.deepl_api else None
    if translator is None:
        print("DEEPL_API is empty: cannot list glossary language pairs.")
    else:
        try:
            pairs = supported_pairs(translator)
        except deepl.DeepLException as e:
            print(f"get_glossary_languages failed: {e}")
            pairs = None
        if pairs is not None:
            print("Supported glossary pairs: " + ", ".join(f"{s}->{t}" for s, t in sorted(pairs)))
            ok = (SOURCE_LANG, TARGET_LANG) in pairs
            print(f"{SOURCE_LANG}->{TARGET_LANG} supported: {'yes' if ok else 'NO'}")

    if args.dry_run:
        print("Dry run: no glossary created or deleted.")
        return 0
    if translator is None:
        return 2
    if not entries:
        print("The TSV has no entries; nothing to create.")
        return 2

    name = args.name or f"pic-en-es-{datetime.now(UTC):%Y-%m-%d}"
    info = translator.create_glossary(name, SOURCE_LANG, TARGET_LANG, {e.en: e.es for e in entries})
    print(f"Created glossary {info.glossary_id} ({name}, {len(entries)} entries).")
    print(f"Operator: set DEEPL_GLOSSARY_ID={info.glossary_id} and DEEPL_GLOSSARY_ENABLED=true")

    if args.delete_previous is not None:
        old = args.delete_previous or settings.deepl_glossary_id
        if old and old != info.glossary_id:
            translator.delete_glossary(old)
            print(f"Deleted previous glossary {old}.")
        else:
            print("No previous glossary id to delete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
