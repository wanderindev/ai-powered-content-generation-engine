#!/usr/bin/env python3
"""
Fix spaced bold/italic markdown patterns in article content.

CommonMark requires no space between ** and text for bold formatting.
This script fixes patterns like `** text **` → `**text**`.

Usage:
    python scripts/fix_spaced_markdown.py --preview
    python scripts/fix_spaced_markdown.py --execute
"""
import argparse
import re
import sys
from pathlib import Path

# Add backend to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from app.core.config import get_settings


def fix_spaced_markdown(content: str) -> tuple[str, list[tuple[str, str]]]:
    """
    Fix spaced bold/italic patterns in markdown.

    Returns:
        Tuple of (fixed_content, list of (original, replacement) changes)
    """
    changes = []

    # Pattern for spaced bold: ** text ** → **text**
    # Match ** followed by space, then content, then space and **
    bold_pattern = r'\*\*\s+(.+?)\s+\*\*'

    def fix_bold(match):
        original = match.group(0)
        inner_text = match.group(1).strip()
        replacement = f'**{inner_text}**'
        changes.append((original, replacement))
        return replacement

    content = re.sub(bold_pattern, fix_bold, content)

    # Pattern for spaced italic: * text * → *text*
    # Be careful not to match list items or horizontal rules
    # Only match when there's content between single asterisks with spaces
    italic_pattern = r'(?<!\*)\*\s+([^*\n]+?)\s+\*(?!\*)'

    def fix_italic(match):
        original = match.group(0)
        inner_text = match.group(1).strip()
        # Skip if it looks like a list item
        if inner_text.startswith('-') or inner_text.startswith('*'):
            return original
        replacement = f'*{inner_text}*'
        changes.append((original, replacement))
        return replacement

    content = re.sub(italic_pattern, fix_italic, content)

    return content, changes


def get_db_connection():
    """Get database connection."""
    settings = get_settings()
    return psycopg2.connect(settings.database_url)


def preview_changes():
    """Preview what would be fixed."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, content
        FROM articles
        WHERE content IS NOT NULL
        ORDER BY id
    """)

    articles = cursor.fetchall()
    total_changes = 0
    articles_with_changes = 0

    for article_id, title, content in articles:
        _, changes = fix_spaced_markdown(content)

        if changes:
            articles_with_changes += 1
            total_changes += len(changes)
            print(f"\n{'='*60}")
            print(f"Article {article_id}: {title[:50]}...")
            print(f"{'='*60}")

            for original, replacement in changes:
                print(f"  {original!r}")
                print(f"  → {replacement!r}")
                print()

    cursor.close()
    conn.close()

    print(f"\n{'='*60}")
    print(f"Summary: {total_changes} fixes in {articles_with_changes} articles")
    print(f"{'='*60}")


def execute_fixes():
    """Execute the fixes on the database."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, content
        FROM articles
        WHERE content IS NOT NULL
        ORDER BY id
    """)

    articles = cursor.fetchall()
    total_changes = 0
    articles_updated = 0

    for article_id, title, content in articles:
        fixed_content, changes = fix_spaced_markdown(content)

        if changes:
            cursor.execute(
                "UPDATE articles SET content = %s WHERE id = %s",
                (fixed_content, article_id)
            )
            articles_updated += 1
            total_changes += len(changes)
            print(f"Fixed article {article_id}: {title[:50]}... ({len(changes)} changes)")

    conn.commit()
    cursor.close()
    conn.close()

    print(f"\nDone! Updated {articles_updated} articles with {total_changes} total fixes.")


def main():
    parser = argparse.ArgumentParser(
        description="Fix spaced bold/italic markdown patterns in articles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/fix_spaced_markdown.py --preview
    python scripts/fix_spaced_markdown.py --execute
        """,
    )
    parser.add_argument(
        "--preview", action="store_true", help="Preview changes without modifying"
    )
    parser.add_argument(
        "--execute", action="store_true", help="Execute fixes on database"
    )
    args = parser.parse_args()

    if not any([args.preview, args.execute]):
        parser.print_help()
        return

    if args.preview:
        preview_changes()
    elif args.execute:
        print("This will modify article content in the database.")
        confirm = input("Type 'yes' to confirm: ")
        if confirm.lower() == "yes":
            execute_fixes()
        else:
            print("Aborted.")


if __name__ == "__main__":
    main()
