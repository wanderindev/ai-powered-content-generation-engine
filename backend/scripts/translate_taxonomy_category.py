#!/usr/bin/env python3
"""
Translate taxonomy and category names from English to Spanish.

Usage:
    python scripts/translate_taxonomy_category.py --preview
    python scripts/translate_taxonomy_category.py --execute

Requires DEEPL_API and DATABASE_URL environment variables to be set.
To target production, source `.env.prod-readonly` before running:

    set -a; source .env.prod-readonly; set +a
"""
import argparse
import os
import sys

import deepl
import psycopg2


DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    sys.stderr.write(
        "DATABASE_URL is not set. Source .env.prod-readonly (for prod) or "
        "your local .env first.\n"
    )
    sys.exit(2)


def get_connection():
    """Get database connection."""
    return psycopg2.connect(DATABASE_URL)


def get_untranslated_taxonomies(conn):
    """Get taxonomies missing Spanish translations."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, description FROM taxonomies WHERE name_es IS NULL"
        )
        return cur.fetchall()


def get_untranslated_categories(conn):
    """Get categories missing Spanish translations."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, description FROM categories WHERE name_es IS NULL"
        )
        return cur.fetchall()


def translate_text(translator: deepl.Translator, text: str) -> str:
    """Translate text from English to Spanish."""
    if not text:
        return None
    result = translator.translate_text(
        text,
        target_lang="ES-419",  # Latin American Spanish
        formality="default",
    )
    return result.text


def preview_changes(conn):
    """Show what would be translated."""
    taxonomies = get_untranslated_taxonomies(conn)
    categories = get_untranslated_categories(conn)

    print(f"Found {len(taxonomies)} taxonomies to translate:")
    for tid, name, desc in taxonomies:
        print(f"  [{tid}] {name}")

    print(f"\nFound {len(categories)} categories to translate:")
    for cid, name, desc in categories[:20]:
        print(f"  [{cid}] {name}")
    if len(categories) > 20:
        print(f"  ... and {len(categories) - 20} more")


def execute_translation(conn, dry_run: bool = False):
    """Translate and update database."""
    deepl_api = os.getenv("DEEPL_API")
    if not deepl_api:
        print("ERROR: DEEPL_API environment variable not set.")
        sys.exit(1)

    translator = deepl.Translator(deepl_api)

    # Translate taxonomies
    taxonomies = get_untranslated_taxonomies(conn)
    print(f"\nTranslating {len(taxonomies)} taxonomies...")

    with conn.cursor() as cur:
        for tid, name, desc in taxonomies:
            name_es = translate_text(translator, name)
            desc_es = translate_text(translator, desc) if desc else None

            print(f"  [{tid}] {name} -> {name_es}")

            if not dry_run:
                cur.execute(
                    "UPDATE taxonomies SET name_es = %s, description_es = %s WHERE id = %s",
                    (name_es, desc_es, tid)
                )

    # Translate categories
    categories = get_untranslated_categories(conn)
    print(f"\nTranslating {len(categories)} categories...")

    with conn.cursor() as cur:
        for cid, name, desc in categories:
            name_es = translate_text(translator, name)
            desc_es = translate_text(translator, desc) if desc else None

            print(f"  [{cid}] {name} -> {name_es}")

            if not dry_run:
                cur.execute(
                    "UPDATE categories SET name_es = %s, description_es = %s WHERE id = %s",
                    (name_es, desc_es, cid)
                )

    if not dry_run:
        conn.commit()
        print("\nChanges saved to database.")
    else:
        print("\n[DRY RUN] No changes made.")


def main():
    parser = argparse.ArgumentParser(
        description="Translate taxonomy and category names to Spanish"
    )
    parser.add_argument(
        "--preview", action="store_true", help="Show what would be translated"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Translate but don't save changes"
    )
    parser.add_argument(
        "--execute", action="store_true", help="Translate and save changes"
    )
    args = parser.parse_args()

    if not any([args.preview, args.dry_run, args.execute]):
        parser.print_help()
        return

    conn = get_connection()

    try:
        if args.preview:
            preview_changes(conn)
        elif args.dry_run:
            execute_translation(conn, dry_run=True)
        elif args.execute:
            execute_translation(conn, dry_run=False)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
